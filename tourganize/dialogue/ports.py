"""The two ports the Dialogue Director consumes that are typed with Dialogue value objects.

Read them from :mod:`tourganize.ports.interpretation`, which re-exports both — that module is
where the application's ports are listed, and it is the documented import path. They are
*defined* here for a mechanical reason, and it is the same one that put
:class:`~tourganize.domain.catalog.prioritization.PriorityPolicy` in the domain (D15): a port's
contract has to name the types it carries. ``TurnInterpreter`` speaks in
:class:`~tourganize.dialogue.turns.UserTurn`, :class:`~tourganize.dialogue.states.DialogueState`
and :class:`~tourganize.dialogue.session.PendingQuestion`, so ``tourganize.ports`` must be able
to import the dialogue to declare them — and the Director, which consumes the protocols, is
itself in the dialogue. Defining them where the value objects live is what keeps that one
direction, rather than two packages importing each other's contents.

:class:`DialogueContext` is the whole of what an interpreter is allowed to know. No session
object leaks out: an interpreter cannot see the Trip Plan, the Transcript or the Requirement
Sets, so a replacement interpreter cannot quietly start making planning decisions. What it
*can* see is what it needs to read a turn well — which state the conversation is in, which
component is in focus, which question is outstanding, which options are on the table, and which
field names the focused Requirement Schema declares.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from tourganize.dialogue.session import PendingQuestion
from tourganize.dialogue.states import DialogueState
from tourganize.dialogue.turns import DEFAULT_LOCALE, TurnInterpretation, UserTurn
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_text
from tourganize.domain.options import OptionSlate
from tourganize.domain.requirements import RequirementSet
from tourganize.domain.trip import TripPlan

__all__ = ["DialogueContext", "OptionSlatePlanner", "TurnInterpreter"]


@dataclass(frozen=True, slots=True)
class DialogueContext:
    """Everything an interpreter may know about the conversation so far.

    Assembled by the Director once per turn and thrown away. ``slate_option_refs`` are the
    ``option_id``s of the latest Option Slate, so an interpreter can resolve "the second one"
    without being handed the slate itself, and ``focus_field_names`` are the fields the focused
    Requirement Schema declares, so an interpreter offers values only for fields that exist.
    """

    state: DialogueState
    locale: str = DEFAULT_LOCALE
    focus_kind: str | None = None
    pending_question: PendingQuestion | None = None
    slate_option_refs: tuple[str, ...] = ()
    known_kind_keys: tuple[str, ...] = ()
    focus_field_names: tuple[str, ...] = ()
    turn_index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.state, DialogueState):
            raise InvariantViolationError(
                f"DialogueContext.state must be a DialogueState, got {self.state!r}"
            )
        require_text(self.locale, "DialogueContext.locale")


@runtime_checkable
class TurnInterpreter(Protocol):
    """Turns free text into a :class:`~tourganize.dialogue.turns.TurnInterpretation`.

    The first adapter is keyword-based and deterministic; F08 replaces it with one that calls
    the LLM Gateway, and the Director does not change. An interpreter is a *language* component
    and nothing else: it never decides what happens next, never touches the Trip Plan, and
    never resolves a relative date without the ``Clock`` it was built with — "next month" is
    resolved here, at the boundary, because the domain accepts only resolved values.

    Raising is allowed. The Director catches it once and asks for clarification; an interpreter
    that raises twice in a row is broken rather than confused, and the exception propagates.
    """

    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation:
        """Read one turn in the light of ``context``."""
        ...


@runtime_checkable
class OptionSlatePlanner(Protocol):
    """Produces one Option Slate for one Plan Component in one round.

    This is the seam that keeps the Director free of I/O. F06's planning service implements it
    over the ``OptionSource`` port; until then a fake answers with fixed slates, and the state
    machine is fully testable without a provider, a network or a fixture file.

    ``round_index`` is the round being *asked for*, counting from zero, and the planner is not
    expected to remember anything: the Trip Plan holds the history, and a refinement is simply
    the same ``kind_key`` with the next index and a richer Requirement Set. Raising is the way
    to say "nothing could be sourced"; the Director turns that into a
    ``report_sourcing_failure`` Act, because the conversation must not die because a provider
    did.
    """

    def plan(
        self,
        kind_key: str,
        requirements: RequirementSet,
        plan: TripPlan,
        round_index: int,
    ) -> OptionSlate:
        """Return the Option Slate for ``kind_key``'s round ``round_index``."""
        ...
