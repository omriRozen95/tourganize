"""``TripPlan`` — the aggregate root, and the only place a plan may be changed.

Every mutation goes through one of six named methods, and nothing outside this module walks a
Component Status edge. That is the whole point: the dialogue (F05) decides *when* to plan
something, and this type decides what a plan is allowed to become. A slate cannot be recorded
for a component nobody sourced, a Selection cannot name an option that was never offered, and
refinement history is never thrown away.

``created_at`` is passed in rather than read here — the domain has no clock, so the caller
hands it ``clock.now()`` and a replayed conversation keeps the timestamps it was recorded
with.

Component order is insertion order: the order in which Component Kinds first entered the
conversation. It is stable, meaningful, and what the agenda, the summary and the export all
read, so nothing has to sort by a field that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from tourganize.domain.errors import (
    InvariantViolationError,
    UnknownComponentKindError,
    UnknownOptionError,
)
from tourganize.domain.invariants import require_aware, require_text
from tourganize.domain.options import OptionSlate
from tourganize.domain.trip.completeness import PlanCompleteness
from tourganize.domain.trip.component import ComponentStatus, PlanComponent
from tourganize.domain.trip.selection import Selection

__all__ = ["TripPlan"]

#: The Component Status edges a component walks to reach ``SELECTED``. Spelled out rather than
#: assigned, because :data:`~tourganize.domain.trip.component.LEGAL_TRANSITIONS` is what says
#: they are legal and :meth:`TripPlan.mark_selected` is the only thing that walks them all.
_TO_SELECTED: Final = (
    ComponentStatus.READY,
    ComponentStatus.SOURCING,
    ComponentStatus.AWAITING_CHOICE,
    ComponentStatus.SELECTED,
)


@dataclass
class TripPlan:
    """The trip being assembled for one conversation."""

    plan_id: str
    created_at: datetime
    components: dict[str, PlanComponent] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.plan_id, "TripPlan.plan_id")
        require_aware(self.created_at, "TripPlan.created_at")

    # -- reads ------------------------------------------------------------------------

    def component(self, kind_key: str) -> PlanComponent:
        """Return the Plan Component for ``kind_key``, or raise."""
        try:
            return self.components[kind_key]
        except KeyError as exc:
            raise UnknownComponentKindError(
                f"this plan holds no component for {kind_key!r}; "
                f"it has {', '.join(self.components) or 'none'}"
            ) from exc

    def has_component(self, kind_key: str) -> bool:
        return kind_key in self.components

    def settled_kinds(self) -> tuple[str, ...]:
        """The Component Kinds that are selected or declined, in insertion order."""
        return tuple(key for key, item in self.components.items() if item.is_settled)

    def open_kinds(self) -> tuple[str, ...]:
        """The Component Kinds still to be dealt with, in insertion order."""
        return tuple(key for key, item in self.components.items() if not item.is_settled)

    def mentioned_kinds(self) -> tuple[str, ...]:
        """The Component Kinds the traveller raised, earliest mention first.

        The Mentioned-First Rule is applied in F04's ``build_agenda``; this is the fact it
        reads.
        """
        mentioned = [item for item in self.components.values() if item.is_mentioned]
        mentioned.sort(key=lambda item: item.mentioned_on_turn or 0)
        return tuple(item.kind_key for item in mentioned)

    def completeness(self) -> PlanCompleteness:
        """Derive the plan's completeness. Computed, never stored."""
        selected: list[str] = []
        declined: list[str] = []
        still_open: list[str] = []
        open_mentioned: list[str] = []
        for key, item in self.components.items():
            if item.status is ComponentStatus.SELECTED:
                selected.append(key)
            elif item.status is ComponentStatus.DECLINED:
                declined.append(key)
            else:
                still_open.append(key)
                if item.is_mentioned:
                    open_mentioned.append(key)
        return PlanCompleteness(
            selected=tuple(selected),
            declined=tuple(declined),
            open=tuple(still_open),
            open_mentioned=tuple(open_mentioned),
        )

    # -- the only mutators ------------------------------------------------------------

    def ensure_component(self, kind_key: str) -> PlanComponent:
        """Return the component for ``kind_key``, creating a ``PENDING`` one if needed."""
        existing = self.components.get(kind_key)
        if existing is not None:
            return existing
        created = PlanComponent(kind_key=kind_key)
        self.components[kind_key] = created
        return created

    def mark_mentioned(self, kind_key: str, turn_index: int) -> None:
        """Record that the traveller raised this Component Kind on ``turn_index``.

        The earliest mention is the one kept: a kind asked for on turn 1 and again on turn 6
        was still raised on turn 1, and the Agenda's tie-breaking should not move because the
        traveller repeated themselves.
        """
        if type(turn_index) is not int:
            raise InvariantViolationError(f"turn_index must be an integer, got {turn_index!r}")
        if turn_index < 0:
            raise InvariantViolationError(f"turn_index must not be negative, got {turn_index}")
        component = self.ensure_component(kind_key)
        if component.mentioned_on_turn is None or turn_index < component.mentioned_on_turn:
            component.mentioned_on_turn = turn_index

    def record_slate(self, slate: OptionSlate) -> None:
        """Append one Option Slate as the next round for its component.

        The component must already be ``SOURCING`` — a slate is the *result* of sourcing, and
        recording one for a component nobody sourced would invent a history that never
        happened. The round index must be the next one: rounds are appended, never replaced.
        """
        if type(slate) is not OptionSlate:
            raise InvariantViolationError(f"record_slate expects an OptionSlate, got {slate!r}")
        component = self.component(slate.kind_key)
        expected = len(component.slates)
        if slate.round_index != expected:
            raise InvariantViolationError(
                f"{slate.kind_key}: slate round_index {slate.round_index} is not the next "
                f"round ({expected}); refinement appends a round and never replaces one"
            )
        component.advance_to(ComponentStatus.AWAITING_CHOICE)
        component.slates = (*component.slates, slate)

    def record_selection(self, selection: Selection) -> None:
        """Record the traveller's choice for one component.

        The option has to be on the component's *latest* slate. Choosing "the second one" from
        a slate that has since been refined away is exactly the mix-up this refuses.
        """
        if type(selection) is not Selection:
            raise InvariantViolationError(
                f"record_selection expects a Selection, got {selection!r}"
            )
        component = self.component(selection.kind_key)
        latest = component.latest_slate()
        if latest is None:
            raise UnknownOptionError(
                f"{selection.kind_key}: nothing has been offered yet, so "
                f"{selection.option_id!r} cannot be selected"
            )
        if not latest.contains(selection.option_id):
            offered = ", ".join(option.option_id for option in latest.options) or "nothing"
            raise UnknownOptionError(
                f"{selection.kind_key}: {selection.option_id!r} is not on round "
                f"{latest.round_index} of the slate, which offered {offered}"
            )
        component.advance_to(ComponentStatus.SELECTED)
        component.selection = selection

    def mark_selected(self, kind_key: str) -> None:
        """Record that ``kind_key`` is already chosen, with no Selection to record.

        For *describing* a plan this conversation did not build. ``tourganize catalog agenda``
        names on the command line what is already settled, and there it has no Option Source,
        no Option Slate and therefore no Plan Option that a Selection could honestly name. It
        walks the same Component Status edges sourcing and a choice would, so the recorded
        history stays legal, and it lives here rather than in the caller so that "``SELECTED``
        with no Selection" is a state this aggregate produces deliberately, in one place,
        instead of one that anything outside the domain can assemble for itself.

        A component that has been offered something is refused: once a slate exists there is a
        Plan Option to name, and :meth:`record_selection` is the method that names it. That is
        also why the dialogue never wants this one.
        """
        component = self.ensure_component(kind_key)
        if component.slates:
            raise InvariantViolationError(
                f"{kind_key}: {component.round_count} Option Slate(s) have been offered, so "
                f"record_selection is what records the choice — it names the Plan Option"
            )
        for status in _TO_SELECTED:
            component.advance_to(status)

    def decline(self, kind_key: str) -> None:
        """Mark a Component Kind as declined. It is never offered again in this session."""
        self.ensure_component(kind_key).advance_to(ComponentStatus.DECLINED)
