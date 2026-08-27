"""The ``OptionSlatePlanner`` contract, run against every adapter of the port.

F06's real planning service is the second adapter, and it was done when this file passed
**unmodified** — only :data:`PLANNERS` grew, which is what that dict is for. Nothing here asserts anything about *which* options come back — that is the
whole of what a planner is free to decide — only the shape of the answer, which the Dialogue
Director and the Trip Plan both depend on: the slate is for the Component Kind that was asked
about, in the round that was asked for, with options of that Kind, distinct ids, and Provenance
on every one of them.

The last of those is not a formality. ``TripPlan.record_slate`` refuses a slate whose round is
not the next one, and ``PlanOption`` refuses an option nobody can trace back to a source, so an
adapter that gets either wrong is one whose slates cannot be recorded at all.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tourganize.adapters.catalog.memory import InMemoryComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.options import CheapestFirstRanking, SourceRegistry
from tourganize.adapters.options.fake import FixedSlatePlanner
from tourganize.adapters.options.fixture import FixtureOptionSource
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.application.planning_service import PlanningService
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.options import OptionSlate
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import ComponentStatus, TripPlan
from tourganize.platform.settings import OptionSourceProfile
from tourganize.ports.interpretation import OptionSlatePlanner

PlannerBuilder = Callable[[], OptionSlatePlanner]

#: Where F06's real planner reads its recorded options from: the tree that ships with the
#: repository. Pointed at rather than copied, for the reason the CLI subprocess suite points at
#: the shipped catalog — only the shipped files can prove the shipped files work.
SHIPPED_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "options"


def _planning_service(slate_size: int = 3) -> OptionSlatePlanner:
    """F06's real planner, wired the way the Composition Root wires it.

    Its Component Catalog declares ``alpha`` alone: this suite also asks about ``omega``, and a
    planner that could only answer for the Kinds its catalog names would be a planner with a
    fixed set of topics in it — which is the one thing the last test in this file forbids.
    """
    clock = FrozenClock(DEFAULT_MOMENT)
    kinds = (
        ComponentKind(
            kind_key="alpha",
            message_key="component.alpha",
            priority_weight=100,
            schema_key="alpha.v1",
        ),
    )
    return PlanningService(
        InMemoryComponentCatalog(kinds, (SCHEMA,)),
        SourceRegistry(
            OptionSourceProfile(), {"fixture": (FixtureOptionSource(SHIPPED_FIXTURES, clock),)}
        ),
        CheapestFirstRanking(),
        clock,
        NullTelemetrySink(),
        slate_size=slate_size,
    )


#: Every adapter of the port, keyed by the name the test ids use. F06 appended its own.
PLANNERS: dict[str, PlannerBuilder] = {
    "FixedSlatePlanner": lambda: FixedSlatePlanner(FrozenClock(DEFAULT_MOMENT)),
    "FixedSlatePlanner(one option)": lambda: FixedSlatePlanner(
        FrozenClock(DEFAULT_MOMENT), slate_size=1
    ),
    "PlanningService": _planning_service,
    "PlanningService(one option)": lambda: _planning_service(slate_size=1),
}

SCHEMA = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        FieldSpec("party_size", FieldKind.INTEGER, Obligation.OPTIONAL, "ask.alpha.party_size"),
    ),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)


@pytest.fixture(params=sorted(PLANNERS), ids=sorted(PLANNERS))
def planner(request: pytest.FixtureRequest) -> OptionSlatePlanner:
    return PLANNERS[request.param]()


def a_plan() -> TripPlan:
    return TripPlan(plan_id="plan-1", created_at=DEFAULT_MOMENT)


def requirements(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty("alpha").with_updates(updates, schema=SCHEMA)


def test_the_adapter_satisfies_the_protocol(planner: OptionSlatePlanner) -> None:
    assert isinstance(planner, OptionSlatePlanner)


def test_the_slate_answers_the_question_it_was_asked(planner: OptionSlatePlanner) -> None:
    slate = planner.plan("alpha", requirements(place="Paris"), a_plan(), 0)

    assert isinstance(slate, OptionSlate)
    assert slate.kind_key == "alpha"
    assert slate.round_index == 0


def test_every_option_belongs_to_that_component_kind(planner: OptionSlatePlanner) -> None:
    slate = planner.plan("alpha", requirements(place="Paris"), a_plan(), 0)

    assert slate.options
    assert all(option.kind_key == "alpha" for option in slate.options)


def test_option_ids_are_distinct_within_a_round(planner: OptionSlatePlanner) -> None:
    """``OptionSlate`` refuses a repeat, so an adapter that repeats one cannot build a slate."""
    slate = planner.plan("alpha", requirements(place="Paris"), a_plan(), 0)
    identifiers = [option.option_id for option in slate.options]

    assert len(set(identifiers)) == len(identifiers)


def test_every_option_can_be_traced_back_to_a_source(planner: OptionSlatePlanner) -> None:
    slate = planner.plan("alpha", requirements(place="Paris"), a_plan(), 0)

    for option in slate.options:
        assert option.provenance.source_id
        assert option.provenance.retrieved_at.tzinfo is not None


def test_a_later_round_is_a_new_slate_the_plan_will_accept(
    planner: OptionSlatePlanner,
) -> None:
    """The choose-or-refine loop, from the planner's side: rounds append, never replace."""
    plan = a_plan()
    component = plan.ensure_component("alpha")
    component.advance_to(ComponentStatus.READY)
    held = requirements(place="Paris")

    for round_index in range(3):
        component.advance_to(ComponentStatus.SOURCING)
        plan.record_slate(planner.plan("alpha", held, plan, round_index))

    assert [slate.round_index for slate in component.slates] == [0, 1, 2]


def test_the_requirements_digest_follows_what_was_asked_for(
    planner: OptionSlatePlanner,
) -> None:
    """Two different requirements must not share a digest; the same ones must."""
    plan = a_plan()
    paris = planner.plan("alpha", requirements(place="Paris"), plan, 0)
    again = planner.plan("alpha", requirements(place="Paris"), plan, 0)
    lisbon = planner.plan("alpha", requirements(place="Lisbon"), plan, 0)

    assert paris.requirements_digest == again.requirements_digest
    assert paris.requirements_digest != lisbon.requirements_digest


def test_planning_the_same_round_twice_answers_the_same_way(
    planner: OptionSlatePlanner,
) -> None:
    plan = a_plan()
    held = requirements(place="Paris")

    first = planner.plan("alpha", held, plan, 1)
    second = planner.plan("alpha", held, plan, 1)

    assert [option.option_id for option in first.options] == [
        option.option_id for option in second.options
    ]


def test_a_planner_answers_for_whichever_component_kind_it_is_asked_about(
    planner: OptionSlatePlanner,
) -> None:
    """Component Kinds are data: a planner may not have a fixed set of them in it."""
    plan = a_plan()
    slate = planner.plan("omega", RequirementSet.empty("omega"), plan, 0)

    assert slate.kind_key == "omega"
    assert all(option.kind_key == "omega" for option in slate.options)
