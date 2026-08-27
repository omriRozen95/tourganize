"""The dialogue's value objects, and the two things they refuse: a stray Act, and prose.

The Act vocabulary being *closed* is the whole of why a surface can be exhaustive about what it
draws, so the refusal is tested rather than assumed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.dialogue import (
    ACT_VOCABULARY,
    ASK_BLOCKING,
    CLARIFY_CODES,
    GREET,
    AssistantAct,
    TurnIntent,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.requirements import RequirementUpdate


def test_a_user_turn_carries_the_moment_it_arrived() -> None:
    turn = UserTurn(index=0, text="anything", received_at=DEFAULT_MOMENT)

    assert turn.index == 0
    assert turn.received_at == DEFAULT_MOMENT
    assert turn.locale_hint is None


def test_a_naive_arrival_time_is_refused() -> None:
    """The domain's rule, and the reason a session can be replayed with its own timestamps."""
    with pytest.raises(InvariantViolationError, match="timezone-aware"):
        UserTurn(index=0, text="anything", received_at=datetime(2026, 10, 23))


def test_a_negative_turn_index_is_refused() -> None:
    with pytest.raises(InvariantViolationError, match="whole number"):
        UserTurn(index=-1, text="anything", received_at=DEFAULT_MOMENT)


def test_an_empty_turn_is_allowed_because_a_traveller_may_send_one() -> None:
    """Blank text is a turn the interpreter will not place, not an invalid object."""
    assert not UserTurn(index=0, text="", received_at=DEFAULT_MOMENT).text


def test_an_act_outside_the_vocabulary_is_refused() -> None:
    with pytest.raises(InvariantViolationError, match="the vocabulary is closed"):
        AssistantAct(act="apologise")


def test_the_vocabulary_is_the_eleven_acts_the_spec_names() -> None:
    assert set(ACT_VOCABULARY) == {
        "greet",
        "ask_blocking",
        "ask_optional",
        "report_invalid_value",
        "present_slate",
        "confirm_selection",
        "offer_unmentioned",
        "deliver_summary",
        "clarify",
        "report_sourcing_failure",
        "close",
    }


def test_the_clarify_codes_are_distinct_and_key_shaped() -> None:
    """They reach telemetry fields and message keys, like an Agenda Reason Code."""
    assert len(set(CLARIFY_CODES)) == len(CLARIFY_CODES)
    assert all(code.islower() and " " not in code for code in CLARIFY_CODES)


def test_an_act_payload_cannot_be_edited_underneath_a_surface() -> None:
    payload = {"rule_name": "when"}
    act = AssistantAct(act=ASK_BLOCKING, payload=payload, kind_key="alpha")
    payload["rule_name"] = "where"

    assert act.payload["rule_name"] == "when"
    with pytest.raises(TypeError):
        act.payload["rule_name"] = "where"  # type: ignore[index]


def test_an_act_defaults_to_the_default_locale_and_no_component() -> None:
    act = AssistantAct(act=GREET)

    assert act.locale == "en"
    assert act.kind_key is None
    assert dict(act.payload) == {}


def test_an_interpretation_needs_a_real_intent() -> None:
    with pytest.raises(InvariantViolationError, match="TurnIntent"):
        TurnInterpretation(intent="choose")  # type: ignore[arg-type]


def test_an_interpretation_holds_requirement_updates_not_values() -> None:
    with pytest.raises(InvariantViolationError, match="RequirementUpdate"):
        TurnInterpretation(
            intent=TurnIntent.ANSWER_QUESTION,
            requirement_updates=("place=Paris",),  # type: ignore[arg-type]
        )


def test_confidence_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(InvariantViolationError, match="between 0 and 1"):
        TurnInterpretation(intent=TurnIntent.UNKNOWN, confidence=1.5)


def test_an_interpretation_defaults_to_saying_nothing_but_its_intent() -> None:
    reading = TurnInterpretation(intent=TurnIntent.SMALL_TALK)

    assert reading.mentioned_kinds == ()
    assert reading.requirement_updates == ()
    assert reading.chosen_option_ref is None
    assert reading.detected_locale is None


def test_an_interpretation_carries_the_travellers_own_words_on_an_update() -> None:
    """``raw_text`` is what lets a re-ask quote what was actually said."""
    reading = TurnInterpretation(
        intent=TurnIntent.ANSWER_QUESTION,
        requirement_updates=(
            RequirementUpdate(field_name="place", value="Paris", raw_text="in Paris"),
        ),
    )

    assert reading.requirement_updates[0].raw_text == "in Paris"


def test_the_intent_vocabulary_is_the_nine_the_spec_names() -> None:
    assert {intent.value for intent in TurnIntent} == {
        "state_request",
        "answer_question",
        "choose_option",
        "refine",
        "accept_offer",
        "decline_offer",
        "end_session",
        "small_talk",
        "unknown",
    }


def test_turns_are_ordinary_values_and_compare_by_content() -> None:
    later = DEFAULT_MOMENT + timedelta(minutes=1)
    assert UserTurn(0, "a", DEFAULT_MOMENT) == UserTurn(0, "a", DEFAULT_MOMENT)
    assert UserTurn(0, "a", DEFAULT_MOMENT) != UserTurn(0, "a", later)
    assert DEFAULT_MOMENT.tzinfo is UTC
