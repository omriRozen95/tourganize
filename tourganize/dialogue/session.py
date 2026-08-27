"""The Planning Session: the conversation aggregate, and the only thing the Director mutates.

One session is one traveller conversation: its identity, the language it is being held in, the
Dialogue State it has reached, the Trip Plan under construction, and the Transcript of what was
said. It is the unit F12 persists and resumes, which is why :attr:`PlanningSession.schema_version`
exists from the first day — a stored session has to be able to say which shape it was written in.

Mutation happens **only** through the Dialogue Director. This type is deliberately a plain
mutable dataclass rather than a fortress of methods: the invariants worth enforcing here are
about *values* (a session id is text, a turn index counts up), and the invariants about
*behaviour* — when a state may change, when a question may be asked — belong to the state
machine, which is one object and one file away. Two guardians of the same rule is how the two
drift apart.

``pending_question`` is the one piece of elicitation bookkeeping the session carries. It exists
because "one blocking question per Act" needs a memory of what was asked: without it a
re-answered question is indistinguishable from a fresh one, and the re-ask limit could never be
counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from tourganize.dialogue.states import DialogueState
from tourganize.dialogue.turns import DEFAULT_LOCALE, AssistantAct, UserTurn
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_aware, require_text
from tourganize.domain.trip import TripPlan

__all__ = [
    "SESSION_SCHEMA_VERSION",
    "PendingQuestion",
    "PlanningSession",
    "TranscriptEntry",
    "new_session",
]

#: The shape a stored Planning Session is written in. F12 reads it to decide whether a session
#: it loaded can be understood; it is bumped by whichever feature changes the field set.
SESSION_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class PendingQuestion:
    """The blocking question that is currently outstanding, and how often it has been asked.

    It names a **Blocking Rule** rather than a field, because that is what an obligation is:
    ``when`` may be satisfied by a range *or* by a start and an end, and a traveller who
    answers with the pair has answered the question even though the field the Act named is
    still empty. ``field_names`` is the flattened set of every field that would help, kept so
    that an answer can be recognised without re-deriving it from the schema.

    ``attempts`` counts the asks, not the failures: the first ask is attempt 1. That is what
    makes ``TOURGANIZE_DIALOGUE_MAX_REASKS`` readable as "how many times we ask before trying
    something else".
    """

    kind_key: str
    rule_name: str
    field_names: tuple[str, ...]
    asked_on_turn: int
    attempts: int = 1

    def __post_init__(self) -> None:
        require_text(self.kind_key, "PendingQuestion.kind_key")
        require_text(self.rule_name, "PendingQuestion.rule_name")
        if type(self.field_names) is not tuple or not self.field_names:
            raise InvariantViolationError(
                f"{self.kind_key}.{self.rule_name}: field_names must be a non-empty tuple, "
                f"got {self.field_names!r}"
            )
        for name in self.field_names:
            require_text(name, f"{self.kind_key}.{self.rule_name}.field_names")
        if type(self.asked_on_turn) is not int or self.asked_on_turn < 0:
            raise InvariantViolationError(
                f"PendingQuestion.asked_on_turn must be a whole number, got {self.asked_on_turn!r}"
            )
        if type(self.attempts) is not int or self.attempts < 1:
            raise InvariantViolationError(
                f"PendingQuestion.attempts counts from one, got {self.attempts!r}"
            )

    def asked_again(self, turn_index: int) -> PendingQuestion:
        """The same question, asked once more. Immutable, like everything it is made of."""
        return PendingQuestion(
            kind_key=self.kind_key,
            rule_name=self.rule_name,
            field_names=self.field_names,
            asked_on_turn=turn_index,
            attempts=self.attempts + 1,
        )

    def is_about(self, kind_key: str, rule_name: str) -> bool:
        """True when this is the question ``rule_name`` of ``kind_key`` would ask."""
        return self.kind_key == kind_key and self.rule_name == rule_name


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One exchange: the turn that arrived, and the Assistant Acts it produced.

    ``turn`` is ``None`` for the opening greeting, which is the one thing the assistant says
    without having been spoken to first. Keeping it in the Transcript rather than treating it
    as a special case means the record of a session is the whole of what was said.
    """

    turn: UserTurn | None
    acts: tuple[AssistantAct, ...] = ()

    def __post_init__(self) -> None:
        if self.turn is not None and type(self.turn) is not UserTurn:
            raise InvariantViolationError(
                f"TranscriptEntry.turn must be a UserTurn or None, got {self.turn!r}"
            )
        if type(self.acts) is not tuple:
            raise InvariantViolationError(
                f"TranscriptEntry.acts must be a tuple, got {self.acts!r}"
            )
        for act in self.acts:
            if type(act) is not AssistantAct:
                raise InvariantViolationError(
                    f"TranscriptEntry.acts holds AssistantAct, got {act!r}"
                )


@dataclass
class PlanningSession:
    """One traveller conversation. Mutated only by the Dialogue Director."""

    session_id: str
    created_at: datetime
    plan: TripPlan
    locale: str = DEFAULT_LOCALE
    state: DialogueState = DialogueState.GREETING
    transcript: tuple[TranscriptEntry, ...] = ()
    focus_kind: str | None = None
    turn_index: int = -1
    pending_question: PendingQuestion | None = None
    offer_queue: tuple[str, ...] = ()
    schema_version: int = SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_text(self.session_id, "PlanningSession.session_id")
        require_aware(self.created_at, "PlanningSession.created_at")
        require_text(self.locale, "PlanningSession.locale")
        if type(self.plan) is not TripPlan:
            raise InvariantViolationError(
                f"PlanningSession.plan must be a TripPlan, got {self.plan!r}"
            )
        if not isinstance(self.state, DialogueState):
            raise InvariantViolationError(
                f"PlanningSession.state must be a DialogueState, got {self.state!r}"
            )
        # -1 is "no turn has arrived yet", so the first turn is index 0 and a turn index in a
        # log line means the same thing as a Requirement Value's turn index.
        if type(self.turn_index) is not int or self.turn_index < -1:
            raise InvariantViolationError(
                f"PlanningSession.turn_index counts from zero, got {self.turn_index!r}"
            )

    @property
    def is_closed(self) -> bool:
        return self.state is DialogueState.CLOSED

    @property
    def next_turn_index(self) -> int:
        """The index the next inbound User Turn should carry."""
        return self.turn_index + 1

    def record(self, turn: UserTurn | None, acts: tuple[AssistantAct, ...]) -> None:
        """Append one exchange to the Transcript."""
        self.transcript = (*self.transcript, TranscriptEntry(turn=turn, acts=acts))

    def acts(self) -> tuple[AssistantAct, ...]:
        """Every Assistant Act of the session so far, in the order they were emitted."""
        return tuple(act for entry in self.transcript for act in entry.acts)


def new_session(
    session_id: str, created_at: datetime, *, locale: str = DEFAULT_LOCALE
) -> PlanningSession:
    """Start a session whose Trip Plan shares its identity and its moment of creation.

    A Planning Session and the Trip Plan it is building are one conversation, so they are
    created together and given the same id: a stored plan that could not be traced back to the
    session that produced it would be a plan nobody could resume (F12).
    """
    return PlanningSession(
        session_id=session_id,
        created_at=created_at,
        plan=TripPlan(plan_id=session_id, created_at=created_at),
        locale=locale,
    )
