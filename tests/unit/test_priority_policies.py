"""The two shipped Priority Policies, each about what only it promises.

What *both* promise — a permutation of the candidates, a stable answer, a ``policy_id`` — is
the port's, and is asserted over every adapter in
``tests/contracts/test_priority_policy_contract.py``. This file is for the rest: the weighted
policy's reading of the catalog's declarations, and the fake's two modes.
"""

from __future__ import annotations

import logging

from tourganize.adapters.catalog.priority import (
    FIXED_POLICY_ID,
    WEIGHTED_POLICY_ID,
    FixedOrderPolicy,
    WeightedCatalogPolicy,
)
from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.trip import TripPlan


def kind(key: str, *, weight: int = 100, awaits: tuple[str, ...] = ()) -> ComponentKind:
    return ComponentKind(
        kind_key=key,
        message_key=f"component.{key}",
        priority_weight=weight,
        schema_key=f"{key}.v1",
        requires_outcome_of=awaits,
    )


def a_plan() -> TripPlan:
    return TripPlan(plan_id="plan-1", created_at=DEFAULT_MOMENT)


def collecting_logger(name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
    """A logger whose records a test can read, without touching the application's handlers."""
    logger = logging.getLogger(name)
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.handlers = [Collector()]
    logger.setLevel(logging.WARNING)
    return logger, records


# -- WeightedCatalogPolicy -------------------------------------------------------------


def test_the_weighted_policy_names_itself_after_the_setting_that_selects_it() -> None:
    assert WeightedCatalogPolicy().policy_id == WEIGHTED_POLICY_ID == "weighted"


def test_heavier_kinds_are_planned_first() -> None:
    candidates = (kind("alpha", weight=100), kind("beta", weight=300), kind("gamma", weight=200))

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("beta", "gamma", "alpha")


def test_equal_weights_keep_the_order_the_catalog_declares() -> None:
    """The tie-break the port promises declaration order for: the Agenda must not flicker."""
    candidates = (kind("gamma", weight=200), kind("alpha", weight=200), kind("beta", weight=200))

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("gamma", "alpha", "beta")


def test_a_declared_outcome_dependency_wins_over_weight() -> None:
    candidates = (kind("alpha", weight=100), kind("beta", weight=900, awaits=("alpha",)))

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("alpha", "beta")


def test_a_chain_of_dependencies_comes_out_in_order() -> None:
    candidates = (
        kind("alpha", weight=100, awaits=("beta",)),
        kind("beta", weight=200, awaits=("gamma",)),
        kind("gamma", weight=300),
    )

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("gamma", "beta", "alpha")


def test_weight_still_decides_everywhere_a_dependency_does_not() -> None:
    """One declared edge must not rewrite the order of the Kinds it says nothing about."""
    candidates = (
        kind("alpha", weight=400),
        kind("beta", weight=300, awaits=("gamma",)),
        kind("gamma", weight=100),
    )

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("alpha", "gamma", "beta")


def test_a_dependency_on_a_kind_that_is_not_a_candidate_constrains_nothing() -> None:
    """Which is what makes Outcome Dependencies soft: the other band is simply not here."""
    candidates = (kind("beta", weight=200, awaits=("alpha",)),)

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("beta",)


def test_a_dependency_cycle_is_broken_by_declaration_order_and_warned_about_once() -> None:
    """Unreachable through a loaded catalog — ``catalog_problems`` refuses a cycle — but a
    policy that looped for ever over a bad edge would hang the conversation."""
    logger, records = collecting_logger("test.prioritization.cycle")
    candidates = (
        kind("alpha", weight=100, awaits=("beta",)),
        kind("beta", weight=200, awaits=("alpha",)),
    )

    ordered = WeightedCatalogPolicy(logger=logger).order(candidates, a_plan())

    assert sorted(ordered) == ["alpha", "beta"]
    assert ordered[0] == "alpha"  # first declared, not heaviest: the deadlock is broken, not sorted
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "cycle" in records[0].getMessage()


def test_the_plan_does_not_change_the_shipped_policy_s_mind() -> None:
    """Context-free by decision (D3). The plan is in the signature for the *replacement*."""
    candidates = (kind("alpha", weight=300), kind("beta", weight=200))
    plan = a_plan()
    plan.mark_mentioned("beta", 0)

    assert WeightedCatalogPolicy().order(candidates, plan) == ("alpha", "beta")


def test_a_negative_weight_is_simply_the_lightest() -> None:
    candidates = (kind("alpha", weight=-10), kind("beta", weight=0))

    assert WeightedCatalogPolicy().order(candidates, a_plan()) == ("beta", "alpha")


# -- FixedOrderPolicy ------------------------------------------------------------------


def test_the_fixed_policy_names_itself_after_the_setting_that_selects_it() -> None:
    assert FixedOrderPolicy().policy_id == FIXED_POLICY_ID == "fixed"


def test_configured_kinds_come_first_in_the_configured_order() -> None:
    candidates = (kind("alpha"), kind("beta"), kind("gamma"))

    assert FixedOrderPolicy(("gamma", "alpha")).order(candidates, a_plan()) == (
        "gamma",
        "alpha",
        "beta",
    )


def test_candidates_the_configuration_does_not_name_keep_the_order_they_arrived_in() -> None:
    candidates = (kind("gamma"), kind("beta"), kind("alpha"))

    assert FixedOrderPolicy().order(candidates, a_plan()) == ("gamma", "beta", "alpha")


def test_configured_kinds_that_are_not_candidates_are_dropped_rather_than_invented() -> None:
    """A band holds two Kinds out of five, and the answer still has to be about those two."""
    policy = FixedOrderPolicy(("nowhere", "beta", "alpha"))

    assert policy.order((kind("alpha"), kind("beta")), a_plan()) == ("beta", "alpha")


def test_the_configured_order_is_readable_for_doctor_and_for_a_test() -> None:
    assert FixedOrderPolicy(("beta", "alpha")).kind_keys == ("beta", "alpha")
    assert FixedOrderPolicy().kind_keys == ()


def test_weights_are_ignored_which_is_the_whole_point_of_the_fixed_policy() -> None:
    candidates = (kind("alpha", weight=1), kind("beta", weight=1000))

    assert FixedOrderPolicy().order(candidates, a_plan()) == ("alpha", "beta")


def test_verbatim_returns_the_configured_list_exactly_so_the_seam_can_be_driven_wrong() -> None:
    """The only reason the mode exists: ``build_agenda`` must refuse a policy that misbehaves,
    and a seam nobody ever drives wrong is a seam nobody knows is there."""
    policy = FixedOrderPolicy(("nowhere",), verbatim=True)

    assert policy.order((kind("alpha"),), a_plan()) == ("nowhere",)
