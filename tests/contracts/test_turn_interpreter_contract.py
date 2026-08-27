"""The ``TurnInterpreter`` contract, run against every adapter of the port.

A new ``TurnInterpreter`` adapter — F08's model-backed one is the next — is done when this file
passes **unmodified**. Everything asserted here is something the *port* promises, never
something the keyword adapter happens to do: nothing about which intent a particular sentence
maps to can be asserted, because that is precisely what a replacement interpreter is allowed to
be better at.

What the port does promise is narrow and mechanical, and all of it matters to the Director:
a ``TurnInterpretation`` comes back for any turn at all, its ``mentioned_kinds`` name only
Component Kinds the context declared, its ``requirement_updates`` are Requirement Updates, and
reading a turn changes nothing — the same turn read twice reads the same way, and the context it
was given is not modified.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import write_keywords

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.adapters.interpretation.keyword import KeywordTurnInterpreter
from tourganize.dialogue import (
    DialogueContext,
    DialogueState,
    PendingQuestion,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.requirements import RequirementUpdate
from tourganize.ports.interpretation import TurnInterpreter

InterpreterBuilder = Callable[[Path], TurnInterpreter]

#: Every adapter of the port, keyed by the name the test ids use. F08 appends its own.
INTERPRETERS: dict[str, InterpreterBuilder] = {
    "KeywordTurnInterpreter": KeywordTurnInterpreter,
}

KNOWN = ("alpha", "beta", "gamma")


def turns() -> Iterator[UserTurn]:
    """Turns every interpreter must survive, including the ones nobody could read."""
    for index, text in enumerate(
        (
            "",
            "   ",
            "???",
            "an alpha in Paris 2026-10-23/2026-10-28",
            "goodbye",
            "2",
            "אלפא בפריז",
            "a" * 500,
        )
    ):
        yield UserTurn(index=index, text=text, received_at=DEFAULT_MOMENT)


def contexts() -> Iterator[DialogueContext]:
    """One context per Dialogue State a turn can actually arrive in, plus the awkward ones."""
    yield DialogueContext(state=DialogueState.GREETING, known_kind_keys=KNOWN)
    yield DialogueContext(
        state=DialogueState.ELICITING_BLOCKING,
        focus_kind="alpha",
        pending_question=PendingQuestion("alpha", "when", ("date_range",), 0),
        known_kind_keys=KNOWN,
        focus_field_names=("place", "date_range"),
    )
    yield DialogueContext(
        state=DialogueState.AWAITING_CHOICE,
        focus_kind="alpha",
        slate_option_refs=("alpha-r0-1", "alpha-r0-2"),
        known_kind_keys=KNOWN,
        focus_field_names=("place", "date_range"),
    )
    yield DialogueContext(state=DialogueState.OFFERING_UNMENTIONED, known_kind_keys=KNOWN)
    yield DialogueContext(state=DialogueState.GREETING)  # a catalog that declares nothing


@pytest.fixture(params=sorted(INTERPRETERS), ids=sorted(INTERPRETERS))
def interpreter(request: pytest.FixtureRequest, tmp_path: Path) -> TurnInterpreter:
    """One interpreter per adapter, configured against this test's own config directory."""
    return INTERPRETERS[request.param](write_keywords(tmp_path))


def test_the_adapter_satisfies_the_protocol(interpreter: TurnInterpreter) -> None:
    assert isinstance(interpreter, TurnInterpreter)


def test_every_turn_in_every_state_yields_an_interpretation(
    interpreter: TurnInterpreter,
) -> None:
    """There is no turn an interpreter may refuse: ``UNKNOWN`` is the answer for the rest."""
    for context in contexts():
        for turn in turns():
            reading = interpreter.interpret(turn, context)
            assert isinstance(reading, TurnInterpretation)


def test_mentioned_kinds_are_only_ever_kinds_the_context_declared(
    interpreter: TurnInterpreter,
) -> None:
    for context in contexts():
        for turn in turns():
            reading = interpreter.interpret(turn, context)
            assert set(reading.mentioned_kinds) <= set(context.known_kind_keys)


def test_requirement_updates_are_requirement_updates(interpreter: TurnInterpreter) -> None:
    for context in contexts():
        for turn in turns():
            for update in interpreter.interpret(turn, context).requirement_updates:
                assert isinstance(update, RequirementUpdate)


def test_a_choice_reference_is_only_offered_with_a_choice(
    interpreter: TurnInterpreter,
) -> None:
    """The Director resolves the reference, so the port only promises where one may appear."""
    for context in contexts():
        for turn in turns():
            reading = interpreter.interpret(turn, context)
            if reading.chosen_option_ref is not None:
                assert reading.intent.value == "choose_option"


def test_reading_a_turn_twice_reads_it_the_same_way(interpreter: TurnInterpreter) -> None:
    """Not a performance claim: it is what makes a Golden Conversation (F11) possible."""
    for context in contexts():
        for turn in turns():
            assert interpreter.interpret(turn, context) == interpreter.interpret(turn, context)


def test_reading_a_turn_does_not_touch_the_context_it_was_given(
    interpreter: TurnInterpreter,
) -> None:
    for context in contexts():
        before = (
            context.state,
            context.locale,
            context.focus_kind,
            context.slate_option_refs,
            context.known_kind_keys,
            context.focus_field_names,
        )
        for turn in turns():
            interpreter.interpret(turn, context)
        assert (
            context.state,
            context.locale,
            context.focus_kind,
            context.slate_option_refs,
            context.known_kind_keys,
            context.focus_field_names,
        ) == before


def test_a_detected_locale_is_a_locale_tag_or_nothing(interpreter: TurnInterpreter) -> None:
    for context in contexts():
        for turn in turns():
            detected = interpreter.interpret(turn, context).detected_locale
            assert detected is None or (detected.strip() and detected.islower())


def test_confidence_stays_within_its_scale(interpreter: TurnInterpreter) -> None:
    for context in contexts():
        for turn in turns():
            confidence = interpreter.interpret(turn, context).confidence
            assert confidence is None or 0.0 <= confidence <= 1.0
