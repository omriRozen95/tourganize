"""The ``PriorityPolicy`` contract, run against every adapter of the port — the fake included.

A new ``PriorityPolicy`` adapter is done when this file passes **unmodified**. Everything
asserted here is something the port promises, never something one policy happens to do: the two
shipped policies order the same candidates differently on purpose, so nothing about *which*
order comes out can be asserted — only that whatever comes out is an order of exactly those
Component Kinds, that it comes out the same way twice, and that ``build_agenda`` can be trusted
to concatenate the bands, and to settle the declared Outcome Dependencies inside each of them,
whatever a policy answers.

``FixedOrderPolicy(verbatim=True)`` is deliberately absent from :data:`POLICIES`: its whole
purpose is to *break* this contract so that the seam refusing it can be tested (see
``tests/unit/test_prioritization.py``). A fake that can be configured to misbehave is still
held to the contract in every configuration a caller would use.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tourganize.adapters.catalog.priority import FixedOrderPolicy, WeightedCatalogPolicy
from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.domain.catalog import ComponentKind, PlanningAgenda, build_agenda
from tourganize.domain.trip import TripPlan
from tourganize.ports.catalog import PriorityPolicy

PolicyBuilder = Callable[[], PriorityPolicy]

#: The declarations every policy is asked about: three Kinds, distinct weights, one Outcome
#: Dependency that contradicts the weights. Keeping them in one place is what makes the
#: policies comparable at all.
DECLARED = (
    ComponentKind("alpha", "component.alpha", 300, "alpha.v1"),
    ComponentKind("beta", "component.beta", 200, "beta.v1", ("gamma",)),
    ComponentKind("gamma", "component.gamma", 100, "gamma.v1"),
)

#: Every adapter of the port, keyed by the name the test ids use.
POLICIES: dict[str, PolicyBuilder] = {
    "WeightedCatalogPolicy": WeightedCatalogPolicy,
    "FixedOrderPolicy": FixedOrderPolicy,
    "FixedOrderPolicy(configured)": lambda: FixedOrderPolicy(("gamma", "alpha")),
}


def a_plan(*mentioned: str) -> TripPlan:
    plan = TripPlan(plan_id="plan-1", created_at=DEFAULT_MOMENT)
    for turn_index, kind_key in enumerate(mentioned):
        plan.mark_mentioned(kind_key, turn_index)
    return plan


def labelled_before_its_blocker(agenda: PlanningAgenda) -> list[str]:
    """Every entry that names a blocker it ranks *ahead* of, as readable counterexamples.

    An empty list is the invariant: an Agenda's order and its ``blocked_by`` labels say the
    same thing. Asserted as a property rather than as an expected order, because *which* order
    a policy answers with is precisely what this port does not promise.
    """
    position = {entry.kind_key: index for index, entry in enumerate(agenda.entries)}
    return [
        f"{entry.kind_key} (rank {entry.rank} of {entry.band.name}) awaits {blocker}, "
        f"which the Agenda ranks after it"
        for entry in agenda.entries
        for blocker in entry.blocked_by
        if position[blocker] > position[entry.kind_key]
    ]


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_the_port_is_satisfied_structurally(build: PolicyBuilder) -> None:
    assert isinstance(build(), PriorityPolicy)


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_every_policy_names_itself(build: PolicyBuilder) -> None:
    """``doctor`` prints it and telemetry records it, so every adapter must answer it."""
    policy_id = build().policy_id

    assert isinstance(policy_id, str)
    assert policy_id.strip() == policy_id != ""


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_the_answer_is_exactly_the_candidates_in_some_order(build: PolicyBuilder) -> None:
    ordered = build().order(DECLARED, a_plan())

    assert sorted(ordered) == sorted(kind.kind_key for kind in DECLARED)


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_ordering_twice_gives_the_same_answer(build: PolicyBuilder) -> None:
    """The Agenda is rebuilt every turn; it must not flicker between two of them."""
    policy = build()

    assert tuple(policy.order(DECLARED, a_plan())) == tuple(policy.order(DECLARED, a_plan()))


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_no_candidates_is_an_empty_answer_rather_than_a_failure(build: PolicyBuilder) -> None:
    assert tuple(build().order((), a_plan())) == ()


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_one_candidate_comes_back_alone(build: PolicyBuilder) -> None:
    assert tuple(build().order((DECLARED[1],), a_plan())) == ("beta",)


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_a_subset_is_answered_with_that_subset(build: PolicyBuilder) -> None:
    """A band is a subset of the catalog, so this is the case every real call is."""
    ordered = build().order((DECLARED[0], DECLARED[2]), a_plan())

    assert sorted(ordered) == ["alpha", "gamma"]


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_the_policy_never_mutates_the_candidates_it_is_given(build: PolicyBuilder) -> None:
    before = tuple(DECLARED)

    build().order(DECLARED, a_plan())

    assert before == DECLARED


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_mentioned_first_holds_whatever_the_policy_answers(build: PolicyBuilder) -> None:
    """The rule lives in ``build_agenda``, so no adapter of this port can weaken it."""
    agenda = build_agenda(a_plan("gamma"), DECLARED, build())

    assert agenda.mentioned_open() == ("gamma",)
    assert sorted(agenda.unmentioned_open()) == ["alpha", "beta"]
    assert next(entry.kind_key for entry in agenda.entries) == "gamma"


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
def test_an_outcome_dependency_is_never_left_behind_by_the_agenda(build: PolicyBuilder) -> None:
    """Whatever the order, a Kind that awaits an open Kind in its band says so."""
    agenda = build_agenda(a_plan(), DECLARED, build())
    entries = {entry.kind_key: entry for entry in agenda.entries}

    assert entries["beta"].blocked_by == ("gamma",)
    assert entries["alpha"].blocked_by == ()


@pytest.mark.parametrize("build", POLICIES.values(), ids=POLICIES)
@pytest.mark.parametrize(
    "mentioned", [(), ("beta",), ("beta", "gamma"), ("alpha", "beta", "gamma")]
)
def test_the_order_and_the_blocked_by_labels_never_disagree(
    build: PolicyBuilder, mentioned: tuple[str, ...]
) -> None:
    """``blocked_by`` says "this ranks after that", so the Agenda must rank it after that.

    ``build_agenda`` owns the ordering (D16), which is why this holds for a policy that has
    never read ``requires_outcome_of`` — the fixed policy has not — and for one configured to
    put ``beta`` first. Every partition of :data:`DECLARED` into the two bands is covered,
    because the rule applies inside a band and nowhere else.
    """
    agenda = build_agenda(a_plan(*mentioned), DECLARED, build())

    assert labelled_before_its_blocker(agenda) == []


def test_the_policies_do_not_all_answer_the_same_thing() -> None:
    """Otherwise the suite above would pass with one policy shipped three times over.

    The weighted policy reads the declared weights; the fixed one reads the order it is
    configured with, which is what makes ``TOURGANIZE_PRIORITY_POLICY`` worth having.
    """
    answers = {name: tuple(policy().order(DECLARED, a_plan())) for name, policy in POLICIES.items()}

    assert len(set(answers.values())) > 1
    assert answers["WeightedCatalogPolicy"] == ("alpha", "gamma", "beta")
    assert answers["FixedOrderPolicy(configured)"] == ("gamma", "alpha", "beta")
