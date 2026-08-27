"""The dialogue's vocabulary: User Turns in, Assistant Acts out.

Three shapes cross the Director's edges and all of them are frozen value objects.

A **User Turn** is one traveller utterance with the moment it arrived. A **Turn
Interpretation** is the structured reading of that utterance — produced by the
``TurnInterpreter`` port, never here — and an **Assistant Act** is a structured *intent to
communicate*, which the Presentation Surface renders and the Language Services phrase.

The rule that shapes every payload in this module: **no prose**. An Act carries message keys,
field names, ``kind_key``s, opaque codes and structured option data, and never a composed
sentence — because the traveller may be reading in Hebrew, and a sentence assembled here
would be a sentence in one language. That is not a stylistic preference; it is the whole
mechanism by which the bilingual requirement survives (F10 phrases, F08 composes). The Act
vocabulary is a **closed set** for the same reason: a surface can be exhaustive about what it
knows how to draw, and a new Act is a deliberate change to this list rather than a string
somebody invented at a call site.

``TurnIntent`` is likewise closed. An interpreter that cannot place an utterance answers
``UNKNOWN`` and the Director asks for clarification — which is the honest failure mode, and
the one D2 accepted the cost of.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_aware, require_text
from tourganize.domain.requirements import RequirementUpdate

__all__ = [
    "ACT_VOCABULARY",
    "ASK_BLOCKING",
    "ASK_OPTIONAL",
    "CLARIFY",
    "CLARIFY_CODES",
    "CLARIFY_INTERPRETER_FAILED",
    "CLARIFY_NOT_UNDERSTOOD",
    "CLARIFY_STILL_MISSING",
    "CLARIFY_UNDECLARED_FIELD",
    "CLARIFY_UNRESOLVED_CHOICE",
    "CLOSE",
    "CONFIRM_SELECTION",
    "DEFAULT_LOCALE",
    "DELIVER_SUMMARY",
    "GREET",
    "OFFER_UNMENTIONED",
    "PRESENT_SLATE",
    "REPORT_INVALID_VALUE",
    "REPORT_SOURCING_FAILURE",
    "SOURCING_FAILED",
    "AssistantAct",
    "TurnIntent",
    "TurnInterpretation",
    "UserTurn",
]

#: The locale a session speaks unless a surface says otherwise. One definition, because the
#: Director, the interpreter and ``Settings`` would otherwise each spell it themselves.
DEFAULT_LOCALE: Final = "en"

# -- the Act vocabulary, closed -------------------------------------------------------------

#: Open the conversation. Payload is empty: even "hello" is wording, and wording is F10's.
GREET: Final = "greet"
#: Ask the one blocking question that stands between a component and being planned.
ASK_BLOCKING: Final = "ask_blocking"
#: Ask, once and without blocking anything, for optional filters.
ASK_OPTIONAL: Final = "ask_optional"
#: Say that a value the traveller already gave cannot be used, and why — by message key.
REPORT_INVALID_VALUE: Final = "report_invalid_value"
#: Present one round of Plan Options for one component.
PRESENT_SLATE: Final = "present_slate"
#: Acknowledge a Selection.
CONFIRM_SELECTION: Final = "confirm_selection"
#: Offer to plan Component Kinds the traveller never raised.
OFFER_UNMENTIONED: Final = "offer_unmentioned"
#: Report the Plan Completeness and the Selections at the end of the session.
DELIVER_SUMMARY: Final = "deliver_summary"
#: Say that the turn could not be placed, carrying an opaque code for *why*.
CLARIFY: Final = "clarify"
#: Say that sourcing failed for one component. The conversation continues regardless.
REPORT_SOURCING_FAILURE: Final = "report_sourcing_failure"
#: End the session.
CLOSE: Final = "close"

#: Every Act this system may emit. Closed on purpose: a surface renders all of these or fails
#: loudly, and adding one is a change here plus a change in every surface.
ACT_VOCABULARY: Final = frozenset(
    {
        GREET,
        ASK_BLOCKING,
        ASK_OPTIONAL,
        REPORT_INVALID_VALUE,
        PRESENT_SLATE,
        CONFIRM_SELECTION,
        OFFER_UNMENTIONED,
        DELIVER_SUMMARY,
        CLARIFY,
        REPORT_SOURCING_FAILURE,
        CLOSE,
    }
)

# -- the opaque codes an Act payload carries ------------------------------------------------

#: The turn could not be placed at all.
CLARIFY_NOT_UNDERSTOOD: Final = "not_understood"
#: A choice reference named nothing on the latest Option Slate.
CLARIFY_UNRESOLVED_CHOICE: Final = "unresolved_choice"
#: One Blocking Rule has been asked about too many times; the example is offered instead.
CLARIFY_STILL_MISSING: Final = "still_missing"
#: The Turn Interpreter raised. Its fault, not the traveller's, so the turn is not lost.
CLARIFY_INTERPRETER_FAILED: Final = "interpreter_failed"
#: The interpreter offered a value for a field the Requirement Schema does not declare —
#: prompt/schema drift, reported rather than silently dropped.
CLARIFY_UNDECLARED_FIELD: Final = "undeclared_field"

#: The codes a ``clarify`` payload may carry, for tests and telemetry. Like an Agenda Reason
#: Code these are opaque: a consumer that does not recognise one still knows it means "ask
#: again", and the vocabulary is free to grow.
CLARIFY_CODES: Final = (
    CLARIFY_NOT_UNDERSTOOD,
    CLARIFY_UNRESOLVED_CHOICE,
    CLARIFY_STILL_MISSING,
    CLARIFY_INTERPRETER_FAILED,
    CLARIFY_UNDECLARED_FIELD,
)

#: Why a ``report_sourcing_failure`` was emitted. One code so far; the payload names it rather
#: than the exception's message, which is English and belongs in the log.
SOURCING_FAILED: Final = "sourcing_failed"


class TurnIntent(Enum):
    """What one User Turn is *for*, as far as the state machine is concerned."""

    #: "where are we?" — report the plan without ending the session.
    STATE_REQUEST = "state_request"
    #: An answer to a question that was asked, or unprompted detail about a component.
    ANSWER_QUESTION = "answer_question"
    #: One Plan Option of the latest Option Slate was accepted.
    CHOOSE_OPTION = "choose_option"
    #: Not a choice: corrections or extra detail, which re-source the same component.
    REFINE = "refine"
    #: Yes to a Proactive Offer.
    ACCEPT_OFFER = "accept_offer"
    #: No to a Proactive Offer. The Kind is declined and never offered again.
    DECLINE_OFFER = "decline_offer"
    #: The traveller is leaving. Honoured from any state.
    END_SESSION = "end_session"
    #: Pleasantries. Acknowledged, and the conversation carries on where it was.
    SMALL_TALK = "small_talk"
    #: The interpreter could not place this turn.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UserTurn:
    """One inbound traveller utterance.

    ``received_at`` comes from a :class:`~tourganize.ports.platform.Clock` rather than from
    the wall clock, so a recorded conversation replays with the timestamps it was captured
    with. ``locale_hint`` is what the *surface* believes — a terminal that was started with
    ``--locale he``, say — and it is a hint: the interpreter may disagree, and F10's Language
    Detector eventually settles it.
    """

    index: int
    text: str
    received_at: datetime
    locale_hint: str | None = None

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise InvariantViolationError(
                f"UserTurn.index must be a whole number, got {self.index!r}"
            )
        if type(self.text) is not str:
            raise InvariantViolationError(f"UserTurn.text must be text, got {self.text!r}")
        require_aware(self.received_at, "UserTurn.received_at")
        if self.locale_hint is not None:
            require_text(self.locale_hint, "UserTurn.locale_hint")


@dataclass(frozen=True, slots=True)
class TurnInterpretation:
    """The structured reading of one User Turn, as produced by the ``TurnInterpreter`` port.

    Everything but ``intent`` is optional, and an interpreter that fills nothing else is still
    a working interpreter — that is what makes the keyword adapter and an LLM-backed one
    interchangeable. ``requirement_updates`` are *offers* of values: the merge decides which
    of them win (F03), and this type never pre-judges that.

    ``detected_locale`` is the interpreter's own reading of the language, which the Director
    adopts as the session locale. ``notes`` is diagnostic, English, and for telemetry: it is
    never put into an Assistant Act payload, because an Act payload holds no prose.
    """

    intent: TurnIntent
    mentioned_kinds: tuple[str, ...] = ()
    requirement_updates: tuple[RequirementUpdate, ...] = ()
    chosen_option_ref: str | None = None
    detected_locale: str | None = None
    confidence: float | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, TurnIntent):
            raise InvariantViolationError(
                f"TurnInterpretation.intent must be a TurnIntent, got {self.intent!r}"
            )
        _require_key_tuple(self.mentioned_kinds, "TurnInterpretation.mentioned_kinds")
        if type(self.requirement_updates) is not tuple:
            raise InvariantViolationError(
                f"TurnInterpretation.requirement_updates must be a tuple, "
                f"got {self.requirement_updates!r}"
            )
        for update in self.requirement_updates:
            if type(update) is not RequirementUpdate:
                raise InvariantViolationError(
                    f"TurnInterpretation.requirement_updates holds RequirementUpdate, "
                    f"got {update!r}"
                )
        if self.chosen_option_ref is not None:
            require_text(self.chosen_option_ref, "TurnInterpretation.chosen_option_ref")
        if self.detected_locale is not None:
            require_text(self.detected_locale, "TurnInterpretation.detected_locale")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolationError(
                f"TurnInterpretation.confidence must be between 0 and 1, got {self.confidence!r}"
            )


@dataclass(frozen=True, slots=True)
class AssistantAct:
    """One structured, locale-neutral intent to communicate.

    ``act`` is one of :data:`ACT_VOCABULARY` and nothing else. ``payload`` holds message keys,
    field names, ``kind_key``s, opaque codes, numbers and structured Plan Option data —
    **never** a composed sentence. ``kind_key`` names the Plan Component the Act is about
    where there is one, so a surface can group Acts without parsing the payload.
    """

    act: str
    payload: Mapping[str, object] = field(default_factory=dict)
    locale: str = DEFAULT_LOCALE
    kind_key: str | None = None

    def __post_init__(self) -> None:
        require_text(self.act, "AssistantAct.act")
        if self.act not in ACT_VOCABULARY:
            known = ", ".join(sorted(ACT_VOCABULARY))
            raise InvariantViolationError(
                f"{self.act!r} is not an Assistant Act; the vocabulary is closed: {known}"
            )
        if not isinstance(self.payload, Mapping):
            raise InvariantViolationError(
                f"{self.act}: payload must be a mapping, got {self.payload!r}"
            )
        require_text(self.locale, "AssistantAct.locale")
        if self.kind_key is not None:
            require_text(self.kind_key, "AssistantAct.kind_key")
        # A read-only view, so an Act handed to a surface cannot be edited underneath it.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def _require_key_tuple(values: Sequence[str], field_name: str) -> None:
    if type(values) is not tuple:
        raise InvariantViolationError(f"{field_name} must be a tuple, got {values!r}")
    for value in values:
        require_text(value, field_name)
