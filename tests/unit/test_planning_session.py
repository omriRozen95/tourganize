"""The Planning Session aggregate: identity, the Transcript, and the outstanding question."""

from __future__ import annotations

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.dialogue import (
    GREET,
    SESSION_SCHEMA_VERSION,
    AssistantAct,
    DialogueState,
    PendingQuestion,
    PlanningSession,
    TranscriptEntry,
    UserTurn,
    new_session,
)
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.trip import TripPlan


def a_session() -> PlanningSession:
    return new_session("session-1", DEFAULT_MOMENT)


def test_a_new_session_shares_its_identity_with_the_plan_it_is_building() -> None:
    """A stored plan nobody could trace back to its session is a plan nobody could resume."""
    session = a_session()

    assert session.plan.plan_id == session.session_id
    assert session.plan.created_at == session.created_at


def test_a_new_session_is_greeting_with_nothing_said() -> None:
    session = a_session()

    assert session.state is DialogueState.GREETING
    assert session.transcript == ()
    assert session.focus_kind is None
    assert session.pending_question is None
    assert session.offer_queue == ()
    assert not session.is_closed


def test_the_first_turn_is_index_zero() -> None:
    """``turn_index`` starts at -1 — "no turn has arrived" — so turn indices count from zero."""
    session = a_session()

    assert session.turn_index == -1
    assert session.next_turn_index == 0


def test_the_schema_version_is_stamped_for_the_feature_that_persists_it() -> None:
    assert a_session().schema_version == SESSION_SCHEMA_VERSION == 1


def test_a_session_needs_an_identity_and_an_aware_moment() -> None:
    with pytest.raises(InvariantViolationError, match="session_id"):
        PlanningSession(session_id=" ", created_at=DEFAULT_MOMENT, plan=a_session().plan)


def test_a_session_needs_a_trip_plan() -> None:
    with pytest.raises(InvariantViolationError, match="TripPlan"):
        PlanningSession(session_id="s", created_at=DEFAULT_MOMENT, plan="a plan")  # type: ignore[arg-type]


def test_a_session_needs_a_dialogue_state() -> None:
    with pytest.raises(InvariantViolationError, match="DialogueState"):
        PlanningSession(
            session_id="s",
            created_at=DEFAULT_MOMENT,
            plan=TripPlan("s", DEFAULT_MOMENT),
            state="greeting",  # type: ignore[arg-type]
        )


def test_the_transcript_records_the_greeting_that_nobody_prompted() -> None:
    session = a_session()
    session.record(None, (AssistantAct(act=GREET),))

    assert session.transcript[0].turn is None
    assert session.turns() == ()
    assert [act.act for act in session.acts()] == [GREET]


def test_the_transcript_pairs_each_turn_with_the_acts_it_produced() -> None:
    session = a_session()
    turn = UserTurn(index=0, text="anything", received_at=DEFAULT_MOMENT)
    session.record(turn, (AssistantAct(act=GREET),))

    assert session.turns() == (turn,)
    assert len(session.transcript) == 1


def test_a_transcript_entry_holds_acts_and_a_turn_or_nothing() -> None:
    with pytest.raises(InvariantViolationError, match="UserTurn or None"):
        TranscriptEntry(turn="hello")  # type: ignore[arg-type]
    with pytest.raises(InvariantViolationError, match="AssistantAct"):
        TranscriptEntry(turn=None, acts=("greet",))  # type: ignore[arg-type]


def test_a_pending_question_names_a_rule_and_counts_from_one() -> None:
    """It names the *rule*, because an obligation may be satisfied in more than one way."""
    question = PendingQuestion(
        kind_key="alpha", rule_name="when", field_names=("date_range",), asked_on_turn=0
    )

    assert question.attempts == 1
    assert question.is_about("alpha", "when")
    assert not question.is_about("alpha", "where")
    assert not question.is_about("beta", "when")


def test_asking_again_is_a_new_question_with_the_next_attempt() -> None:
    question = PendingQuestion("alpha", "when", ("date_range",), 0)
    again = question.asked_again(3)

    assert (again.attempts, again.asked_on_turn) == (2, 3)
    assert question.attempts == 1


def test_a_question_about_no_field_could_never_be_answered() -> None:
    with pytest.raises(InvariantViolationError, match="non-empty tuple"):
        PendingQuestion("alpha", "when", (), 0)


def test_a_question_asked_zero_times_is_not_a_question() -> None:
    with pytest.raises(InvariantViolationError, match="counts from one"):
        PendingQuestion("alpha", "when", ("date_range",), 0, attempts=0)
