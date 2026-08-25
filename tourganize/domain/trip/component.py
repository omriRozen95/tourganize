"""The Plan Component and its Component Status machine.

One Plan Component is one plannable topic instance inside a Trip Plan — *the* generic
abstraction the client asked for. It is never subclassed per topic: whatever differs between
one topic and the next is declared in the Component Catalog and in the Requirement Schema,
never here.

The lifecycle is a table, not a pile of ``if``s. :data:`LEGAL_TRANSITIONS` is the whole
machine, and :meth:`PlanComponent.advance_to` is the only way to move, so an impossible
history — a component selected before anything was ever offered — cannot be recorded even by
a caller that means well.

Two edges are worth explaining, because they are what makes the client's rules work:

* ``AWAITING_CHOICE -> SOURCING`` is the choose-or-refine loop. Refinement re-sources the
  *same* component with the next ``round_index``; there is no bound on how often.
* ``FAILED -> SOURCING`` exists because a failure to source is usually transient. ``DECLINED``
  has no way out at all: a kind the traveller turned down is never offered again in that
  session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import IllegalTransitionError, InvariantViolationError
from tourganize.domain.options import OptionSlate
from tourganize.domain.trip.selection import Selection

__all__ = ["LEGAL_TRANSITIONS", "ComponentStatus", "PlanComponent"]


class ComponentStatus(Enum):
    """Where one Plan Component has got to."""

    PENDING = "pending"
    ELICITING = "eliciting"
    READY = "ready"
    SOURCING = "sourcing"
    AWAITING_CHOICE = "awaiting_choice"
    SELECTED = "selected"
    DECLINED = "declined"
    FAILED = "failed"


_TRANSITIONS: Final[dict[ComponentStatus, frozenset[ComponentStatus]]] = {
    ComponentStatus.PENDING: frozenset(
        {
            ComponentStatus.ELICITING,
            ComponentStatus.READY,
            ComponentStatus.DECLINED,
            ComponentStatus.FAILED,
        }
    ),
    # Eliciting to itself: one blocking question per Act, so several turns may be spent here.
    ComponentStatus.ELICITING: frozenset(
        {
            ComponentStatus.ELICITING,
            ComponentStatus.READY,
            ComponentStatus.DECLINED,
            ComponentStatus.FAILED,
        }
    ),
    ComponentStatus.READY: frozenset(
        {
            ComponentStatus.SOURCING,
            ComponentStatus.ELICITING,
            ComponentStatus.DECLINED,
            ComponentStatus.FAILED,
        }
    ),
    ComponentStatus.SOURCING: frozenset(
        {
            ComponentStatus.AWAITING_CHOICE,
            ComponentStatus.SOURCING,
            ComponentStatus.ELICITING,
            ComponentStatus.DECLINED,
            ComponentStatus.FAILED,
        }
    ),
    ComponentStatus.AWAITING_CHOICE: frozenset(
        {
            ComponentStatus.SELECTED,
            ComponentStatus.SOURCING,
            ComponentStatus.ELICITING,
            ComponentStatus.DECLINED,
            ComponentStatus.FAILED,
        }
    ),
    # A settled choice may be reopened: the traveller changes their mind, the component is
    # sourced again, and the earlier rounds stay in the history.
    ComponentStatus.SELECTED: frozenset(
        {ComponentStatus.SOURCING, ComponentStatus.ELICITING, ComponentStatus.FAILED}
    ),
    ComponentStatus.DECLINED: frozenset(),
    ComponentStatus.FAILED: frozenset({ComponentStatus.SOURCING, ComponentStatus.ELICITING}),
}

#: The legal Component Status transitions, as data. Read-only on purpose: a feature that
#: needs a new edge changes this table, where the whole machine is visible at once.
LEGAL_TRANSITIONS: Final[Mapping[ComponentStatus, frozenset[ComponentStatus]]] = MappingProxyType(
    _TRANSITIONS
)

#: A component in one of these has been dealt with: it is neither planned nor asked about
#: again unless something reopens it.
SETTLED_STATUSES: Final = frozenset({ComponentStatus.SELECTED, ComponentStatus.DECLINED})


@dataclass
class PlanComponent:
    """One plannable topic instance: its requirements, its slate history, its choice.

    ``requirements`` is deliberately untyped here. F03 introduces the Requirement Set that
    fills it; until then it is a hole, and nothing in this module looks inside it.
    """

    kind_key: str
    status: ComponentStatus = ComponentStatus.PENDING
    requirements: object | None = None
    slates: tuple[OptionSlate, ...] = ()
    selection: Selection | None = None
    mentioned_on_turn: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind_key) is not str or not self.kind_key.strip():
            raise InvariantViolationError(
                f"PlanComponent.kind_key must be a non-empty string, got {self.kind_key!r}"
            )

    @property
    def is_settled(self) -> bool:
        """True once this component is selected or declined."""
        return self.status in SETTLED_STATUSES

    @property
    def is_mentioned(self) -> bool:
        """True when the traveller raised this Component Kind themselves."""
        return self.mentioned_on_turn is not None

    @property
    def round_count(self) -> int:
        """How many Option Slates have been offered for this component."""
        return len(self.slates)

    def latest_slate(self) -> OptionSlate | None:
        """The most recent Option Slate, or ``None`` before anything was offered."""
        return self.slates[-1] if self.slates else None

    def can_advance_to(self, status: ComponentStatus) -> bool:
        return status in LEGAL_TRANSITIONS[self.status]

    def advance_to(self, status: ComponentStatus) -> None:
        """Move to ``status``, or raise :class:`IllegalTransitionError`."""
        if status not in LEGAL_TRANSITIONS:
            raise InvariantViolationError(f"{status!r} is not a ComponentStatus")
        if not self.can_advance_to(status):
            legal = ", ".join(sorted(item.name for item in LEGAL_TRANSITIONS[self.status]))
            raise IllegalTransitionError(
                f"{self.kind_key}: {self.status.name} -> {status.name} is not a legal "
                f"transition; legal from {self.status.name}: {legal or 'nothing, it is terminal'}"
            )
        self.status = status
