"""The Trip Plan aggregate: slate rounds, selections, completeness, and kept history."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from tourganize.adapters.clock.fake import FrozenClock
from tourganize.domain.errors import (
    InvariantViolationError,
    UnknownComponentKindError,
    UnknownOptionError,
)
from tourganize.domain.options import Money, OptionSlate, PlanOption
from tourganize.domain.trip import ComponentStatus, Selection, TripPlan

OptionFactory = Callable[..., PlanOption]
S = ComponentStatus


def plan_at(moment: datetime) -> TripPlan:
    return TripPlan(plan_id="plan-1", created_at=moment)


def source(plan: TripPlan, kind_key: str) -> None:
    """Walk a component to ``SOURCING``, the state a slate may be recorded in."""
    component = plan.ensure_component(kind_key)
    if component.status is S.PENDING:
        component.advance_to(S.READY)
    component.advance_to(S.SOURCING)


def test_a_plan_takes_its_creation_time_from_the_clock(frozen_clock: FrozenClock) -> None:
    moment = frozen_clock.now()

    plan = plan_at(moment)

    assert plan.created_at == moment
    assert plan.components == {}
    assert plan.completeness().is_empty


def test_a_plan_refuses_a_naive_creation_time() -> None:
    with pytest.raises(InvariantViolationError):
        TripPlan(plan_id="plan-1", created_at=datetime(2026, 1, 1, 12, 0))


def test_ensure_component_is_idempotent(frozen_clock: FrozenClock) -> None:
    plan = plan_at(frozen_clock.now())

    first = plan.ensure_component("alpha")
    again = plan.ensure_component("alpha")

    assert first is again
    assert plan.open_kinds() == ("alpha",)


def test_asking_for_a_component_the_plan_does_not_hold_raises(
    frozen_clock: FrozenClock,
) -> None:
    plan = plan_at(frozen_clock.now())

    with pytest.raises(UnknownComponentKindError):
        plan.component("alpha")


def test_the_earliest_mention_is_the_one_kept(frozen_clock: FrozenClock) -> None:
    """Repeating yourself must not move a Component Kind in the agenda's tie-breaking."""
    plan = plan_at(frozen_clock.now())

    plan.mark_mentioned("alpha", 4)
    plan.mark_mentioned("alpha", 1)
    plan.mark_mentioned("alpha", 9)

    assert plan.component("alpha").mentioned_on_turn == 1


def test_mentioned_kinds_come_back_in_the_order_they_were_raised(
    frozen_clock: FrozenClock,
) -> None:
    plan = plan_at(frozen_clock.now())

    plan.mark_mentioned("beta", 3)
    plan.mark_mentioned("alpha", 1)
    plan.ensure_component("gamma")

    assert plan.mentioned_kinds() == ("alpha", "beta")


@pytest.mark.parametrize("turn_index", [-1, -5])
def test_a_mention_needs_a_real_turn_index(turn_index: int, frozen_clock: FrozenClock) -> None:
    plan = plan_at(frozen_clock.now())

    with pytest.raises(InvariantViolationError):
        plan.mark_mentioned("alpha", turn_index)


def test_slate_rounds_increment_and_history_is_never_discarded(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())
    source(plan, "alpha")
    plan.record_slate(OptionSlate("alpha", 0, (option_factory("a1"), option_factory("a2"))))

    plan.component("alpha").advance_to(S.SOURCING)
    plan.record_slate(OptionSlate("alpha", 1, (option_factory("a3"),)))

    component = plan.component("alpha")
    assert component.round_count == 2
    assert [slate.round_index for slate in component.slates] == [0, 1]
    assert component.slates[0].contains("a1")
    assert component.latest_slate() is component.slates[1]


def test_a_slate_may_only_be_recorded_as_the_next_round(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())
    source(plan, "alpha")

    with pytest.raises(InvariantViolationError) as raised:
        plan.record_slate(OptionSlate("alpha", 3, (option_factory("a1"),)))

    assert "next round" in str(raised.value)


def test_a_slate_cannot_be_recorded_for_a_component_nobody_sourced(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())

    with pytest.raises(UnknownComponentKindError):
        plan.record_slate(OptionSlate("alpha", 0, (option_factory("a1"),)))


def test_the_definition_of_done_walkthrough(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    """Two components, two rounds on one, a choice from the second — round zero survives."""
    plan = plan_at(frozen_clock.now())
    plan.mark_mentioned("alpha", 1)
    plan.mark_mentioned("beta", 1)

    source(plan, "alpha")
    plan.record_slate(OptionSlate("alpha", 0, (option_factory("a1"), option_factory("a2"))))
    plan.component("alpha").advance_to(S.SOURCING)
    second_round = OptionSlate("alpha", 1, (option_factory("a3", price=Money(9900, "EUR")),))
    plan.record_slate(second_round)
    plan.record_selection(Selection("alpha", second_round.options[0], 5))

    component = plan.component("alpha")
    assert component.status is S.SELECTED
    assert component.selection is not None
    assert component.selection.option_id == "a3"
    assert component.slates[0].contains("a1")
    assert plan.settled_kinds() == ("alpha",)
    assert plan.open_kinds() == ("beta",)


def test_selecting_an_option_that_was_never_offered_raises(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())
    source(plan, "alpha")
    plan.record_slate(OptionSlate("alpha", 0, (option_factory("a1"),)))

    with pytest.raises(UnknownOptionError) as raised:
        plan.record_selection(Selection("alpha", option_factory("a9"), 2))

    assert "a9" in str(raised.value)
    assert plan.component("alpha").selection is None


def test_selecting_from_a_superseded_round_raises(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    """ "The second one" from a refined-away slate is exactly the mix-up this refuses."""
    plan = plan_at(frozen_clock.now())
    source(plan, "alpha")
    plan.record_slate(OptionSlate("alpha", 0, (option_factory("a1"),)))
    plan.component("alpha").advance_to(S.SOURCING)
    plan.record_slate(OptionSlate("alpha", 1, (option_factory("a2"),)))

    with pytest.raises(UnknownOptionError):
        plan.record_selection(Selection("alpha", option_factory("a1"), 4))


def test_selecting_before_anything_was_offered_raises(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())
    plan.ensure_component("alpha")

    with pytest.raises(UnknownOptionError) as raised:
        plan.record_selection(Selection("alpha", option_factory("a1"), 1))

    assert "nothing has been offered" in str(raised.value)


def test_a_selection_must_match_its_component_kind(option_factory: OptionFactory) -> None:
    with pytest.raises(InvariantViolationError):
        Selection("beta", option_factory("a1"), 1)


def test_declining_settles_a_kind_for_good(frozen_clock: FrozenClock) -> None:
    plan = plan_at(frozen_clock.now())

    plan.decline("gamma")

    assert plan.component("gamma").status is S.DECLINED
    assert plan.settled_kinds() == ("gamma",)
    assert plan.open_kinds() == ()


def test_marking_a_kind_selected_needs_no_slate_and_settles_it(
    frozen_clock: FrozenClock,
) -> None:
    """The state ``catalog agenda`` describes, produced by the aggregate rather than assembled
    from outside it: SELECTED, legal history, and no Selection to invent."""
    plan = plan_at(frozen_clock.now())

    plan.mark_selected("alpha")

    assert plan.component("alpha").status is S.SELECTED
    assert plan.component("alpha").selection is None
    assert plan.settled_kinds() == ("alpha",)
    assert plan.completeness().selected == ("alpha",)


def test_marking_a_kind_selected_is_refused_once_something_has_been_offered(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    """Once a slate exists there is a Plan Option to name, so ``record_selection`` is the only
    honest way to record the choice."""
    plan = plan_at(frozen_clock.now())
    source(plan, "alpha")
    plan.record_slate(OptionSlate(kind_key="alpha", round_index=0, options=(option_factory("a1"),)))

    with pytest.raises(InvariantViolationError) as raised:
        plan.mark_selected("alpha")

    assert "record_selection" in str(raised.value)
    assert plan.component("alpha").status is S.AWAITING_CHOICE


def test_completeness_across_selected_declined_and_open(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    plan = plan_at(frozen_clock.now())
    plan.mark_mentioned("alpha", 1)
    source(plan, "alpha")
    slate = OptionSlate("alpha", 0, (option_factory("a1"),))
    plan.record_slate(slate)
    plan.record_selection(Selection("alpha", slate.options[0], 2))
    plan.decline("beta")
    plan.mark_mentioned("gamma", 3)

    completeness = plan.completeness()

    assert completeness.selected == ("alpha",)
    assert completeness.declined == ("beta",)
    assert completeness.open == ("gamma",)
    assert completeness.open_mentioned == ("gamma",)
    assert not completeness.is_closeable


def test_an_unmentioned_open_kind_does_not_block_closing(
    frozen_clock: FrozenClock, option_factory: OptionFactory
) -> None:
    """A traveller who asked only for lodging is finished when lodging is chosen."""
    plan = plan_at(frozen_clock.now())
    plan.mark_mentioned("alpha", 1)
    source(plan, "alpha")
    slate = OptionSlate("alpha", 0, (option_factory("a1"),))
    plan.record_slate(slate)
    plan.record_selection(Selection("alpha", slate.options[0], 2))
    plan.ensure_component("beta")  # proactively offered, never mentioned

    completeness = plan.completeness()

    assert completeness.open == ("beta",)
    assert completeness.open_mentioned == ()
    assert completeness.is_closeable


def test_a_failed_mentioned_component_keeps_the_plan_open(frozen_clock: FrozenClock) -> None:
    plan = plan_at(frozen_clock.now())
    plan.mark_mentioned("alpha", 1)
    plan.component("alpha").advance_to(ComponentStatus.FAILED)

    completeness = plan.completeness()

    assert completeness.open == ("alpha",)
    assert not completeness.is_closeable


def test_a_failed_component_can_still_be_declined(frozen_clock: FrozenClock) -> None:
    """The other way out of a component that will not source: the traveller drops it.

    Completeness counts a failed component as open, so without this edge a plan with one
    unsourceable kind in it could never be closed at all.
    """
    plan = plan_at(frozen_clock.now())
    plan.mark_mentioned("alpha", 1)
    plan.component("alpha").advance_to(ComponentStatus.FAILED)

    plan.decline("alpha")

    assert plan.component("alpha").status is ComponentStatus.DECLINED
    assert plan.completeness().declined == ("alpha",)
    assert plan.completeness().is_closeable


def test_components_keep_the_order_they_entered_the_conversation(
    frozen_clock: FrozenClock,
) -> None:
    plan = plan_at(frozen_clock.now())

    for key in ("gamma", "alpha", "beta"):
        plan.ensure_component(key)

    assert plan.open_kinds() == ("gamma", "alpha", "beta")


def test_the_mutators_refuse_something_that_is_not_the_type_they_take(
    frozen_clock: FrozenClock,
) -> None:
    plan = plan_at(frozen_clock.now())

    with pytest.raises(InvariantViolationError):
        plan.record_slate("alpha")  # type: ignore[arg-type]
    with pytest.raises(InvariantViolationError):
        plan.record_selection("alpha")  # type: ignore[arg-type]
