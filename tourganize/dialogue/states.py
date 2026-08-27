"""The Dialogue State machine, as data.

:data:`TRANSITIONS` is the whole machine. Every move the Dialogue Director makes goes through
:func:`require_transition`, so an edge that is not in this table cannot be walked even by a
caller that means well — the same bargain
:data:`~tourganize.domain.trip.component.LEGAL_TRANSITIONS` makes for a Plan Component's
lifecycle, and for the same reason: a conversation that reached an impossible state would be
one nobody could replay or explain.

Two things about the shape are worth stating, because they are what make the table small.

**:data:`~DialogueState.INTERPRETING` is the hub.** Every turn enters it — that is what "a
turn arrived" *is* — and leaves it for whatever the interpretation implies. So the resting
states each need exactly one outgoing edge, and the fan-out lives in one row rather than in
five. A turn that changes nothing returns to the state it came from, which is why
``INTERPRETING`` can also lead back to each resting state — and why ``SOURCING`` and
``REFINING`` do too: a turn that got as far as asking a planner and came back empty-handed has
nothing to ask and nothing to offer, and ``ELICITING_BLOCKING`` is entered by *asking*.

**The states between two turns are few.** :data:`RESTING_STATES` names them: those are the
states a session is ever *observed* in, and everything else — ``INTERPRETING``, ``SOURCING``,
``PRESENTING_SLATE``, ``ELICITING_OPTIONAL``, ``REFINING``, ``SUMMARISING`` — is passed
through inside one ``handle()`` call. They are still real states rather than a comment,
because the table is what the telemetry and the Golden Conversations (F11) read to explain how
one turn got from where it started to where it ended.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import IllegalDialogueTransitionError

__all__ = [
    "RESTING_STATES",
    "TRANSITIONS",
    "DialogueState",
    "reachable_states",
    "require_transition",
]


class DialogueState(Enum):
    """Where one Planning Session has got to."""

    #: Nothing has been said yet. The session has greeted and is listening.
    GREETING = "greeting"
    #: A turn has arrived and is being read. Transient, and the hub of the table.
    INTERPRETING = "interpreting"
    #: One Blocking Rule is unsatisfied and has been asked about.
    ELICITING_BLOCKING = "eliciting_blocking"
    #: Optional filters were asked for alongside a slate. Nothing waits on the answer.
    ELICITING_OPTIONAL = "eliciting_optional"
    #: A component is Plannable and the Option Slate Planner has been called.
    SOURCING = "sourcing"
    #: A slate came back and is being handed to the surface.
    PRESENTING_SLATE = "presenting_slate"
    #: A slate is on the table; the traveller may choose or refine.
    AWAITING_CHOICE = "awaiting_choice"
    #: A refinement is being merged, and will either re-source or re-block.
    REFINING = "refining"
    #: A Proactive Offer is on the table.
    OFFERING_UNMENTIONED = "offering_unmentioned"
    #: The closing summary is being produced.
    SUMMARISING = "summarising"
    #: The session is over. A turn arriving here raises rather than reopening it.
    CLOSED = "closed"


_S: Final = DialogueState

#: The states a session is ever observed in between two turns. Everything else is walked
#: through inside one ``handle()``.
RESTING_STATES: Final = frozenset(
    {
        _S.GREETING,
        _S.ELICITING_BLOCKING,
        _S.AWAITING_CHOICE,
        _S.OFFERING_UNMENTIONED,
        _S.CLOSED,
    }
)

_TRANSITIONS: Final[dict[DialogueState, frozenset[DialogueState]]] = {
    _S.GREETING: frozenset({_S.INTERPRETING}),
    # The hub. Reading a turn leads either onward — elicit, source, refine, offer, summarise —
    # or straight back to where the session was resting, which is what a turn that changed
    # nothing deserves.
    _S.INTERPRETING: frozenset(
        {
            _S.GREETING,
            _S.ELICITING_BLOCKING,
            _S.SOURCING,
            _S.REFINING,
            _S.AWAITING_CHOICE,
            _S.OFFERING_UNMENTIONED,
            _S.SUMMARISING,
        }
    ),
    _S.ELICITING_BLOCKING: frozenset({_S.INTERPRETING}),
    # Optional filters never block, so this state has exactly one way out and it is forward.
    _S.ELICITING_OPTIONAL: frozenset({_S.AWAITING_CHOICE}),
    # SOURCING to itself: one turn may step past a Component Kind that would not source and
    # try the next one. ELICITING_BLOCKING because the *next* Kind the turn tries may have a
    # question outstanding. GREETING and AWAITING_CHOICE are the unwind edges: a turn whose
    # sourcing failed has nothing to ask and nothing to offer, so the session goes back to the
    # Resting State it arrived in rather than claiming to be eliciting something nobody asked.
    _S.SOURCING: frozenset(
        {
            _S.PRESENTING_SLATE,
            _S.SOURCING,
            _S.GREETING,
            _S.ELICITING_BLOCKING,
            _S.AWAITING_CHOICE,
            _S.OFFERING_UNMENTIONED,
            _S.SUMMARISING,
        }
    ),
    _S.PRESENTING_SLATE: frozenset({_S.ELICITING_OPTIONAL, _S.AWAITING_CHOICE}),
    _S.AWAITING_CHOICE: frozenset({_S.INTERPRETING}),
    # A refinement may *introduce* a blocking gap by invalidating a value, which is the whole
    # reason this is a state and not a straight line back into SOURCING. AWAITING_CHOICE is
    # the unwind edge: a refinement the Director could not act on leaves the slate on the table.
    _S.REFINING: frozenset({_S.SOURCING, _S.ELICITING_BLOCKING, _S.AWAITING_CHOICE}),
    _S.OFFERING_UNMENTIONED: frozenset({_S.INTERPRETING}),
    _S.SUMMARISING: frozenset({_S.CLOSED}),
    _S.CLOSED: frozenset(),
}

#: The legal Dialogue State transitions. Read-only: a feature that needs a new edge changes
#: this table, where the whole machine is visible at once.
TRANSITIONS: Final[Mapping[DialogueState, frozenset[DialogueState]]] = MappingProxyType(
    _TRANSITIONS
)


def require_transition(current: DialogueState, target: DialogueState) -> DialogueState:
    """Return ``target`` when ``current -> target`` is a declared edge, or raise.

    A function rather than a method so that the guard can be driven directly by a test: the
    thing worth proving is that the *table* refuses an illegal move, not that one particular
    Director asked it to.
    """
    if target not in TRANSITIONS:
        raise IllegalDialogueTransitionError(f"{target!r} is not a DialogueState")
    if target not in TRANSITIONS[current]:
        legal = ", ".join(sorted(state.name for state in TRANSITIONS[current]))
        raise IllegalDialogueTransitionError(
            f"{current.name} -> {target.name} is not a legal Dialogue State transition; "
            f"legal from {current.name}: {legal or 'nothing, it is terminal'}"
        )
    return target


def reachable_states(start: DialogueState = DialogueState.GREETING) -> frozenset[DialogueState]:
    """Every state reachable from ``start`` by walking :data:`TRANSITIONS`.

    Used to assert that the table has no unreachable state: a state nothing can get to is
    either a missing edge or a state that should not exist, and both are worth failing over.
    """
    seen: set[DialogueState] = set()
    frontier = [start]
    while frontier:
        state = frontier.pop()
        if state in seen:
            continue
        seen.add(state)
        frontier.extend(TRANSITIONS[state])
    return frozenset(seen)
