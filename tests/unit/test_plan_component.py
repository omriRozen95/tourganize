"""The Component Status machine: every legal edge, and the ones that must be refused."""

from __future__ import annotations

import pytest

from tourganize.domain.errors import IllegalTransitionError, InvariantViolationError
from tourganize.domain.requirements import (
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import LEGAL_TRANSITIONS, ComponentStatus, PlanComponent

S = ComponentStatus

#: The path a component walks in an ordinary turn-by-turn conversation.
HAPPY_PATH = (S.ELICITING, S.READY, S.SOURCING, S.AWAITING_CHOICE, S.SELECTED)

ILLEGAL = [
    (S.PENDING, S.SELECTED),
    (S.PENDING, S.AWAITING_CHOICE),
    (S.PENDING, S.SOURCING),
    (S.READY, S.SELECTED),
    (S.READY, S.AWAITING_CHOICE),
    (S.SELECTED, S.AWAITING_CHOICE),
    (S.SELECTED, S.DECLINED),
    (S.DECLINED, S.ELICITING),
    (S.DECLINED, S.SOURCING),
    (S.DECLINED, S.SELECTED),
]


def component(status: ComponentStatus = S.PENDING) -> PlanComponent:
    return PlanComponent(kind_key="alpha", status=status)


def test_a_new_component_is_pending_and_knows_nothing() -> None:
    fresh = component()

    assert fresh.status is S.PENDING
    assert fresh.requirements is None
    assert fresh.slates == ()
    assert fresh.selection is None
    assert fresh.mentioned_on_turn is None
    assert fresh.latest_slate() is None
    assert not fresh.is_settled
    assert not fresh.is_mentioned


def test_the_typed_hole_f02_left_now_holds_a_requirement_set() -> None:
    """F03 filled it. Nothing in the component looks inside — it just carries it."""
    schema = RequirementSchema(
        "alpha.v1",
        "alpha",
        (FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),),
    )
    held = RequirementSet.empty("alpha").with_updates(
        [RequirementUpdate("place", "Paris")], schema=schema
    )

    item = PlanComponent(kind_key="alpha", requirements=held)

    assert item.requirements is held
    assert item.requirements.value_of("place") == "Paris"


def test_the_happy_path_is_walkable_end_to_end() -> None:
    item = component()

    for status in HAPPY_PATH:
        item.advance_to(status)

    assert item.status is S.SELECTED
    assert item.is_settled


@pytest.mark.parametrize(("start", "target"), ILLEGAL)
def test_an_illegal_transition_raises(start: ComponentStatus, target: ComponentStatus) -> None:
    item = component(start)

    with pytest.raises(IllegalTransitionError) as raised:
        item.advance_to(target)

    assert start.name in str(raised.value)
    assert target.name in str(raised.value)
    assert item.status is start


def test_every_legal_transition_in_the_table_is_walkable() -> None:
    """The table is the machine; nothing in it may be unreachable in practice."""
    walked = 0
    for start, targets in LEGAL_TRANSITIONS.items():
        for target in targets:
            item = component(start)

            item.advance_to(target)

            assert item.status is target
            walked += 1

    assert walked == sum(len(targets) for targets in LEGAL_TRANSITIONS.values())
    assert walked > 20


def test_declined_is_terminal_in_every_direction() -> None:
    """A declined kind is never offered again in that session — the machine enforces it."""
    assert LEGAL_TRANSITIONS[S.DECLINED] == frozenset()

    item = component(S.DECLINED)
    for status in ComponentStatus:
        assert not item.can_advance_to(status)


def test_the_choose_or_refine_loop_is_unbounded() -> None:
    item = component(S.AWAITING_CHOICE)

    for _ in range(25):
        item.advance_to(S.SOURCING)
        item.advance_to(S.AWAITING_CHOICE)

    assert item.status is S.AWAITING_CHOICE


def test_eliciting_may_repeat_because_one_question_is_asked_per_act() -> None:
    item = component(S.ELICITING)

    item.advance_to(S.ELICITING)

    assert item.status is S.ELICITING


def test_a_failed_component_may_be_sourced_again() -> None:
    """Sourcing failures are usually transient; F04 is what eventually skips a broken kind."""
    item = component(S.FAILED)

    item.advance_to(S.SOURCING)

    assert item.status is S.SOURCING


def test_a_settled_choice_may_be_reopened_by_refinement() -> None:
    item = component(S.SELECTED)

    item.advance_to(S.SOURCING)

    assert item.status is S.SOURCING


def test_the_transition_table_is_read_only_data() -> None:
    with pytest.raises(TypeError):
        LEGAL_TRANSITIONS[S.DECLINED] = frozenset({S.PENDING})  # type: ignore[index]


def test_every_status_appears_in_the_table_so_no_state_is_a_dead_end_by_accident() -> None:
    assert set(LEGAL_TRANSITIONS) == set(ComponentStatus)


def test_advance_to_refuses_something_that_is_not_a_status() -> None:
    with pytest.raises(InvariantViolationError):
        component().advance_to("selected")  # type: ignore[arg-type]


def test_a_component_needs_a_kind_key() -> None:
    with pytest.raises(InvariantViolationError):
        PlanComponent(kind_key="   ")
