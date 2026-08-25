"""``TripPlan`` — the aggregate root, and the only place a plan may be changed.

Every mutation goes through one of five named methods. That is the whole point: the dialogue
(F05) decides *when* to plan something, and this type decides what a plan is allowed to
become. A slate cannot be recorded for a component nobody sourced, a Selection cannot name an
option that was never offered, and refinement history is never thrown away.

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

from tourganize.domain.errors import (
    InvariantViolationError,
    UnknownComponentKindError,
    UnknownOptionError,
)
from tourganize.domain.options import OptionSlate
from tourganize.domain.trip.completeness import PlanCompleteness
from tourganize.domain.trip.component import ComponentStatus, PlanComponent
from tourganize.domain.trip.selection import Selection

__all__ = ["TripPlan"]


@dataclass
class TripPlan:
    """The trip being assembled for one conversation."""

    plan_id: str
    created_at: datetime
    components: dict[str, PlanComponent] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.plan_id) is not str or not self.plan_id.strip():
            raise InvariantViolationError(
                f"TripPlan.plan_id must be a non-empty string, got {self.plan_id!r}"
            )
        moment = self.created_at
        if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
            raise InvariantViolationError(
                f"TripPlan.created_at must be timezone-aware, got {moment!r}"
            )

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

    def decline(self, kind_key: str) -> None:
        """Mark a Component Kind as declined. It is never offered again in this session."""
        self.ensure_component(kind_key).advance_to(ComponentStatus.DECLINED)
