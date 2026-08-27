"""``build_agenda``: the Mentioned-First Rule, soft dependencies, and the policy seam.

The rules pinned here are the client's own, so each one is a named test rather than an
assertion inside a longer scenario. Neutral ``kind_key``s throughout — a test about *ordering*
should not have to name a travel topic, and the shipped weights it would then depend on are
configuration.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

import pytest

from tourganize.adapters.catalog.priority import FixedOrderPolicy, WeightedCatalogPolicy
from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.domain.catalog import (
    AWAITS_OUTCOME,
    DEFAULT_AGENDA_FAILURE_SKIP,
    FAILED_SKIPPED,
    NOT_PLANNABLE,
    READY,
    ComponentKind,
    PlanningAgenda,
    awaited_within,
    build_agenda,
)
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.trip import ComponentStatus, TripPlan
from tourganize.platform.errors import ContractViolationError, TourganizeError
from tourganize.ports.catalog import PriorityPolicy

S = ComponentStatus
WEIGHTED = WeightedCatalogPolicy()


def kind(
    key: str, *, weight: int = 100, awaits: tuple[str, ...] = (), enabled: bool = True
) -> ComponentKind:
    return ComponentKind(
        kind_key=key,
        message_key=f"component.{key}",
        priority_weight=weight,
        schema_key=f"{key}.v1",
        requires_outcome_of=awaits,
        enabled=enabled,
    )


def a_plan(*mentioned: str) -> TripPlan:
    """A plan in which ``mentioned`` was raised, in that order, from turn zero."""
    plan = TripPlan(plan_id="plan-1", created_at=DEFAULT_MOMENT)
    for turn_index, kind_key in enumerate(mentioned):
        plan.mark_mentioned(kind_key, turn_index)
    return plan


def select(plan: TripPlan, kind_key: str) -> None:
    """Walk a component all the way to SELECTED, the way sourcing and a choice would."""
    component = plan.ensure_component(kind_key)
    for status in (S.READY, S.SOURCING, S.AWAITING_CHOICE, S.SELECTED):
        component.advance_to(status)


def fail(plan: TripPlan, kind_key: str, times: int) -> None:
    """Fail to source ``kind_key`` ``times`` in a row, leaving it FAILED."""
    component = plan.ensure_component(kind_key)
    for _ in range(times):
        component.advance_to(S.FAILED)
        if component.consecutive_failures < times:
            component.advance_to(S.SOURCING)


def keys(
    plan: TripPlan, kinds: Sequence[ComponentKind], policy: PriorityPolicy = WEIGHTED
) -> tuple[str, ...]:
    """The Agenda's Component Kinds in order — the shorthand most of these tests read by."""
    return tuple(entry.kind_key for entry in build_agenda(plan, kinds, policy).entries)


def labelled_before_its_blocker(agenda: PlanningAgenda) -> list[str]:
    """Every entry that names a blocker it ranks *ahead* of. Empty is the invariant."""
    position = {entry.kind_key: index for index, entry in enumerate(agenda.entries)}
    return [
        f"{entry.kind_key} at rank {entry.rank} awaits {blocker}, which is ranked after it"
        for entry in agenda.entries
        for blocker in entry.blocked_by
        if position[blocker] > position[entry.kind_key]
    ]


class AgainstTheDependencies:
    """A policy built to break the rule: it answers in the reverse of what it was handed.

    Handed a band whose Kinds are declared dependencies-first, it therefore proposes every
    dependent *before* the Kind it awaits. Nothing about the port forbids that — a policy is
    free to order a band however it likes — which is exactly why ``build_agenda`` has to be
    the thing that settles the dependencies.
    """

    @property
    def policy_id(self) -> str:
        return "against_the_dependencies"

    def order(self, candidates: Sequence[ComponentKind], plan: TripPlan) -> tuple[str, ...]:
        del plan
        return tuple(kind.kind_key for kind in reversed(candidates))


@contextmanager
def collected_warnings() -> Iterator[list[logging.LogRecord]]:
    """Read what ``build_agenda`` logged, and put the logger back as it was.

    The domain takes no constructor arguments and has nothing to inject, so it logs through
    its own module logger, obtained by name — hence ``build_agenda.__module__`` rather than a
    string this test would have to keep in step. Reading that logger is the honest way to
    assert on the one message it emits.
    """
    logger = logging.getLogger(build_agenda.__module__)
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    collector = Collector()
    previous_level = logger.level
    logger.addHandler(collector)
    logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        logger.removeHandler(collector)
        logger.setLevel(previous_level)


# -- the hard rule ---------------------------------------------------------------------


def test_mentioned_first_is_not_overridable_by_weight() -> None:
    """The client's rule, and the one this whole module exists to make unbreakable."""
    kinds = (kind("alpha", weight=1), kind("beta", weight=1000))

    agenda = build_agenda(a_plan("alpha"), kinds, WEIGHTED)

    assert agenda.explain() == (
        ("alpha", "MENTIONED", 0, READY),
        ("beta", "UNMENTIONED", 0, READY),
    )


def test_no_policy_can_reorder_across_the_bands() -> None:
    """Even a policy that names the unmentioned Kind first only ever sees one band at a time."""
    kinds = (kind("alpha", weight=1), kind("beta", weight=1000))

    assert keys(a_plan("alpha"), kinds, FixedOrderPolicy(("beta", "alpha"))) == ("alpha", "beta")


def test_a_band_is_ordered_by_the_injected_policy_and_nothing_else() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200), kind("gamma", weight=100))

    weighted = keys(a_plan(), kinds)
    fixed = keys(a_plan(), kinds, FixedOrderPolicy(("gamma", "beta")))

    assert weighted == ("alpha", "beta", "gamma")
    assert fixed == ("gamma", "beta", "alpha")


# -- soft outcome dependencies --------------------------------------------------------


def test_outcome_dependency_is_soft() -> None:
    """A traveller who wants only ``beta`` is never held waiting on the ``alpha`` they
    never mentioned: the dependency is in the other band, so it does not apply."""
    kinds = (kind("alpha", weight=300), kind("beta", weight=200, awaits=("alpha",)))

    agenda = build_agenda(a_plan("beta"), kinds, WEIGHTED)

    actionable = agenda.next_actionable()
    assert actionable is not None
    assert actionable.kind_key == "beta"
    assert actionable.blocked_by == ()
    assert actionable.reason_code != AWAITS_OUTCOME


def test_outcome_dependency_orders_within_band() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200, awaits=("alpha",)))

    agenda = build_agenda(a_plan("beta", "alpha"), kinds, WEIGHTED)

    assert agenda.explain() == (
        ("alpha", "MENTIONED", 0, READY),
        ("beta", "MENTIONED", 1, AWAITS_OUTCOME),
    )
    assert agenda.entries[1].blocked_by == ("alpha",)


def test_a_dependency_that_is_settled_constrains_nothing() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200, awaits=("alpha",)))
    plan = a_plan("beta", "alpha")
    select(plan, "alpha")

    agenda = build_agenda(plan, kinds, WEIGHTED)

    assert agenda.explain() == (("beta", "MENTIONED", 0, READY),)


def test_a_dependency_that_was_declined_constrains_nothing() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200, awaits=("alpha",)))
    plan = a_plan("beta", "alpha")
    plan.decline("alpha")

    agenda = build_agenda(plan, kinds, WEIGHTED)

    assert agenda.entries[0].blocked_by == ()
    assert agenda.entries[0].reason_code == READY


def test_a_dependency_that_is_disabled_constrains_nothing() -> None:
    kinds = (kind("alpha", enabled=False), kind("beta", awaits=("alpha",)))

    agenda = build_agenda(a_plan("beta"), kinds, WEIGHTED)

    assert agenda.explain() == (("beta", "MENTIONED", 0, READY),)


def test_a_dependency_ranks_ahead_of_a_heavier_kind_that_awaits_it() -> None:
    """Weight loses to a declared dependency, but only inside the band."""
    kinds = (kind("alpha", weight=100), kind("beta", weight=900, awaits=("alpha",)))

    assert keys(a_plan("alpha", "beta"), kinds) == ("alpha", "beta")


def test_a_policy_that_ignores_dependencies_cannot_rank_a_dependent_first() -> None:
    """The bug D16 closed, in the shape it was reachable in: ``TOURGANIZE_PRIORITY_POLICY=fixed``
    reads no ``requires_outcome_of`` at all, and here the catalog declares the dependent first.
    Before D16 the Agenda answered ``beta`` at rank 0, labelled ``awaits_outcome`` for a Kind it
    had put *after* it."""
    kinds = (kind("beta", weight=200, awaits=("alpha",)), kind("alpha", weight=300))

    agenda = build_agenda(a_plan("beta", "alpha"), kinds, FixedOrderPolicy())

    assert agenda.explain() == (
        ("alpha", "MENTIONED", 0, READY),
        ("beta", "MENTIONED", 1, AWAITS_OUTCOME),
    )
    assert agenda.entries[1].blocked_by == ("alpha",)
    assert labelled_before_its_blocker(agenda) == []


def test_no_policy_can_make_the_order_and_the_labels_disagree() -> None:
    """The structural claim, against a policy written to violate it: ``build_agenda`` computes
    the position and the ``blocked_by`` label from one answer, so they cannot disagree."""
    kinds = (kind("alpha"), kind("beta", awaits=("alpha",)), kind("gamma", awaits=("beta",)))

    agenda = build_agenda(a_plan("alpha", "beta", "gamma"), kinds, AgainstTheDependencies())

    assert tuple(entry.kind_key for entry in agenda.entries) == ("alpha", "beta", "gamma")
    assert labelled_before_its_blocker(agenda) == []


def test_only_the_kinds_a_dependency_constrains_are_moved() -> None:
    """The adjustment is the minimum that makes the labels true: everywhere the declarations do
    not contradict the policy, the policy's order survives verbatim."""
    kinds = (kind("alpha"), kind("beta"), kind("gamma", awaits=("delta",)), kind("delta"))

    ordered = keys(a_plan(), kinds, FixedOrderPolicy(("alpha", "gamma", "beta", "delta")))

    assert ordered == ("alpha", "beta", "delta", "gamma")


def test_a_dependency_cycle_is_broken_by_declaration_order_and_reported_once() -> None:
    """Unreachable through a loaded catalog — ``catalog_problems`` refuses a cycle and every
    adapter raises first — but an Agenda that looped for ever would be a worse failure than one
    that orders a cycle arbitrarily and says so. The fixed policy is used because the weighted
    one reports its own cycle too, and this is about what ``build_agenda`` does."""
    kinds = (kind("beta", awaits=("alpha",)), kind("alpha", awaits=("beta",)))

    with collected_warnings() as records:
        first = keys(a_plan(), kinds, FixedOrderPolicy())
        again = keys(a_plan(), kinds, FixedOrderPolicy())

    assert first == ("beta", "alpha") == again  # declaration order, deterministically
    assert [record.levelno for record in records] == [logging.WARNING] * 2  # one per build
    assert "cycle" in records[0].getMessage()
    assert "UNMENTIONED" in records[0].getMessage()


def test_awaited_within_is_the_one_place_the_soft_rule_lives() -> None:
    dependent = kind("beta", awaits=("alpha", "gamma"))

    assert awaited_within(dependent, {"alpha", "beta", "gamma"}) == ("alpha", "gamma")
    assert awaited_within(dependent, {"gamma"}) == ("gamma",)
    assert awaited_within(dependent, set()) == ()


# -- what belongs on the agenda at all ------------------------------------------------


def test_a_disabled_kind_is_never_planned() -> None:
    kinds = (kind("alpha"), kind("gamma", enabled=False))

    assert keys(a_plan(), kinds) == ("alpha",)


def test_a_selected_kind_leaves_the_agenda() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan("alpha", "beta")
    select(plan, "alpha")

    assert keys(plan, kinds) == ("beta",)


def test_a_declined_kind_leaves_the_agenda_and_is_never_offered_again() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan()
    plan.decline("beta")

    assert keys(plan, kinds) == ("alpha",)


def test_an_agenda_with_nothing_left_is_empty_rather_than_an_error() -> None:
    kinds = (kind("alpha"),)
    plan = a_plan("alpha")
    select(plan, "alpha")

    agenda = build_agenda(plan, kinds, WEIGHTED)

    assert agenda.entries == ()
    assert agenda.next_actionable() is None
    assert agenda.is_mentioned_band_empty()


def test_a_kind_the_plan_has_never_heard_of_is_unmentioned_and_open() -> None:
    agenda = build_agenda(a_plan(), (kind("alpha"),), WEIGHTED)

    assert agenda.unmentioned_open() == ("alpha",)
    assert agenda.is_mentioned_band_empty()


# -- the Proactive Offer gate ---------------------------------------------------------


def test_the_mentioned_band_is_empty_only_when_no_mentioned_kind_is_open() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan("alpha")

    assert not build_agenda(plan, kinds, WEIGHTED).is_mentioned_band_empty()

    select(plan, "alpha")

    assert build_agenda(plan, kinds, WEIGHTED).is_mentioned_band_empty()


def test_declining_a_mentioned_kind_empties_the_band_too() -> None:
    """Declined is settled: the traveller said no, so there is nothing left to plan for it."""
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan("alpha")
    plan.decline("alpha")

    agenda = build_agenda(plan, kinds, WEIGHTED)

    assert agenda.is_mentioned_band_empty()
    assert agenda.unmentioned_open() == ("beta",)


# -- stability -------------------------------------------------------------------------


def test_the_agenda_is_stable_between_turns() -> None:
    kinds = (kind("alpha", weight=200), kind("beta", weight=200), kind("gamma", weight=200))
    plan = a_plan("alpha")

    first = build_agenda(plan, kinds, WEIGHTED)
    second = build_agenda(plan, kinds, WEIGHTED)

    assert first == second
    assert first.entries == second.entries


def test_settling_one_component_does_not_reorder_the_rest() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200), kind("gamma", weight=100))
    plan = a_plan()

    before = keys(plan, kinds)
    select(plan, "beta")
    after = keys(plan, kinds)

    assert before == ("alpha", "beta", "gamma")
    assert after == ("alpha", "gamma")


def test_equal_weights_break_by_declaration_order_so_nothing_flickers() -> None:
    kinds = (kind("gamma", weight=200), kind("alpha", weight=200), kind("beta", weight=200))

    assert keys(a_plan(), kinds) == ("gamma", "alpha", "beta")


# -- actionability ---------------------------------------------------------------------


def test_plannability_is_reported_as_a_reason_code_without_changing_the_order() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))

    agenda = build_agenda(a_plan(), kinds, WEIGHTED, plannable={"alpha": True, "beta": False})

    assert agenda.explain() == (
        ("alpha", "UNMENTIONED", 0, READY),
        ("beta", "UNMENTIONED", 1, NOT_PLANNABLE),
    )
    assert all(entry.is_actionable for entry in agenda.entries)


def test_a_kind_the_plannability_map_omits_is_treated_as_not_plannable() -> None:
    """On no information the dialogue elicits; sourcing on a guess would search for nothing."""
    agenda = build_agenda(a_plan(), (kind("alpha"),), WEIGHTED, plannable={})

    assert agenda.entries[0].reason_code == NOT_PLANNABLE


def test_without_a_plannability_map_no_entry_claims_to_know() -> None:
    agenda = build_agenda(a_plan(), (kind("alpha"),), WEIGHTED)

    assert agenda.entries[0].reason_code == READY


def test_a_failed_kind_is_still_planned_until_it_has_failed_often_enough() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan("alpha", "beta")
    fail(plan, "alpha", times=1)

    agenda = build_agenda(plan, kinds, WEIGHTED, failure_skip=2)

    actionable = agenda.next_actionable()
    assert actionable is not None
    assert actionable.kind_key == "alpha"
    assert agenda.entries[0].reason_code == READY


def test_a_kind_that_keeps_failing_is_skipped_so_it_cannot_deadlock_the_conversation() -> None:
    kinds = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan("alpha", "beta")
    fail(plan, "alpha", times=2)

    agenda = build_agenda(plan, kinds, WEIGHTED, failure_skip=2)

    assert agenda.entries[0].reason_code == FAILED_SKIPPED
    actionable = agenda.next_actionable()
    assert actionable is not None
    assert actionable.kind_key == "beta"
    # Skipped, never dropped: it is still open, so the plan is not closed over the hole.
    assert agenda.mentioned_open() == ("alpha", "beta")


def test_the_skip_threshold_is_configurable_and_defaults_to_the_documented_two() -> None:
    kinds = (kind("alpha"),)
    plan = a_plan("alpha")
    fail(plan, "alpha", times=1)

    assert DEFAULT_AGENDA_FAILURE_SKIP == 2
    assert build_agenda(plan, kinds, WEIGHTED).entries[0].reason_code == READY
    assert build_agenda(plan, kinds, WEIGHTED, failure_skip=1).entries[0].reason_code == (
        FAILED_SKIPPED
    )


def test_a_slate_that_finally_arrives_clears_the_run_of_failures() -> None:
    plan = a_plan("alpha")
    fail(plan, "alpha", times=2)
    plan.component("alpha").advance_to(S.SOURCING)
    plan.component("alpha").advance_to(S.AWAITING_CHOICE)

    agenda = build_agenda(plan, (kind("alpha"),), WEIGHTED, failure_skip=2)

    assert plan.component("alpha").consecutive_failures == 0
    assert agenda.entries[0].reason_code == READY


@pytest.mark.parametrize("skip", [0, -1, 1.5, True])
def test_a_skip_threshold_below_one_is_refused(skip: object) -> None:
    with pytest.raises(InvariantViolationError):
        build_agenda(a_plan(), (kind("alpha"),), WEIGHTED, failure_skip=skip)  # type: ignore[arg-type]


# -- the policy seam -------------------------------------------------------------------


def test_a_policy_that_invents_a_kind_key_is_refused_at_the_seam() -> None:
    inventing = FixedOrderPolicy(("alpha", "nowhere"), verbatim=True)

    with pytest.raises(ContractViolationError) as raised:
        build_agenda(a_plan(), (kind("alpha"),), inventing)

    assert "invented ['nowhere']" in str(raised.value)
    assert "fixed" in str(raised.value)
    assert isinstance(raised.value, TourganizeError)


def test_a_policy_that_drops_a_kind_key_is_refused_at_the_seam() -> None:
    """A dropped Kind would simply vanish from the conversation, which nobody would see."""
    dropping = FixedOrderPolicy(("alpha",), verbatim=True)

    with pytest.raises(ContractViolationError) as raised:
        build_agenda(a_plan(), (kind("alpha"), kind("beta")), dropping)

    assert "dropped ['beta']" in str(raised.value)


def test_a_policy_that_repeats_a_kind_key_is_refused_at_the_seam() -> None:
    repeating = FixedOrderPolicy(("alpha", "alpha"), verbatim=True)

    with pytest.raises(ContractViolationError) as raised:
        build_agenda(a_plan(), (kind("alpha"), kind("beta")), repeating)

    assert "repeated ['alpha']" in str(raised.value)


def test_the_seam_is_checked_per_band_not_once_for_the_whole_agenda() -> None:
    """The mentioned band alone is handed over first, so an order for *both* is a violation."""
    both = FixedOrderPolicy(("alpha", "beta"), verbatim=True)
    plan = a_plan("alpha")

    with pytest.raises(ContractViolationError) as raised:
        build_agenda(plan, (kind("alpha"), kind("beta")), both)

    assert "invented ['beta']" in str(raised.value)
