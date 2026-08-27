"""The Planning Service: the real ``OptionSlatePlanner`` over the ``OptionSource`` port."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Final

import pytest
from conftest import write_option_fixtures

from tourganize.adapters.catalog.memory import InMemoryComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.options import CheapestFirstRanking, SourceRegistry
from tourganize.adapters.options.fake import FailingOptionSource, RecordedOptionSource
from tourganize.adapters.options.fixture import FixtureOptionSource
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.application.planning_service import (
    CANDIDATE_FACTOR,
    SOURCING_EVENT_KIND,
    PlanningService,
)
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.domain.options import Money, OptionSlate, PlanOption
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import ComponentStatus, Selection, TripPlan
from tourganize.platform.errors import ContractViolationError, OptionSourcingError
from tourganize.platform.settings import OptionSourceProfile
from tourganize.ports.interpretation import OptionSlatePlanner
from tourganize.ports.options import OptionQuery, OptionSource, OptionSourceResult
from tourganize.ports.platform import TelemetryEvent

OptionFactory = Callable[..., PlanOption]

KIND: Final = "alpha"

ALPHA_SCHEMA: Final = RequirementSchema(
    schema_key="alpha.v1",
    component_kind=KIND,
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        FieldSpec("date_range", FieldKind.DATE_RANGE, Obligation.BLOCKING, "ask.alpha.when"),
        FieldSpec(
            name="budget_ceiling",
            field_kind=FieldKind.MONEY,
            obligation=Obligation.OPTIONAL,
            prompt_message_key="ask.alpha.budget_ceiling",
            constraints={"filters": "price", "comparison": "at_most"},
        ),
        FieldSpec(
            name="min_score",
            field_kind=FieldKind.SCORE,
            obligation=Obligation.OPTIONAL,
            prompt_message_key="ask.alpha.min_score",
            constraints={"min": 0, "max": 10, "filters": "review_score", "comparison": "at_least"},
        ),
    ),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",),)),
    ),
)

BETA_SCHEMA: Final = RequirementSchema(
    schema_key="beta.v1",
    component_kind="beta",
    fields=(FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.beta.place"),),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)

KINDS: Final = (
    ComponentKind(
        kind_key=KIND,
        message_key="component.alpha",
        priority_weight=200,
        schema_key="alpha.v1",
        requires_outcome_of=("beta",),
    ),
    ComponentKind(
        kind_key="beta",
        message_key="component.beta",
        priority_weight=300,
        schema_key="beta.v1",
    ),
)


class RecordingSink:
    """A ``TelemetrySink`` that keeps what it was handed. F01's convention, one file over."""

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def record(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    @property
    def degraded(self) -> bool:
        return False


def catalog() -> InMemoryComponentCatalog:
    return InMemoryComponentCatalog(KINDS, (ALPHA_SCHEMA, BETA_SCHEMA))


def requirements(kind_key: str = KIND, **values: object) -> RequirementSet:
    schema = ALPHA_SCHEMA if kind_key == KIND else BETA_SCHEMA
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty(kind_key).with_updates(updates, schema=schema)


def a_plan() -> TripPlan:
    return TripPlan(plan_id="plan-1", created_at=DEFAULT_MOMENT)


def service(
    sources: Sequence[OptionSource],
    *,
    slate_size: int = 3,
    filter_strict: bool = False,
    timeout_seconds: float = 10.0,
    clock: FrozenClock | None = None,
    telemetry: RecordingSink | None = None,
    profile: OptionSourceProfile | None = None,
    registry_sources: Mapping[str, Sequence[OptionSource]] | None = None,
) -> PlanningService:
    registry = SourceRegistry(
        profile or OptionSourceProfile(),
        registry_sources if registry_sources is not None else {"fixture": tuple(sources)},
    )
    return PlanningService(
        catalog(),
        registry,
        CheapestFirstRanking(),
        clock or FrozenClock(DEFAULT_MOMENT),
        telemetry or NullTelemetrySink(),
        slate_size=slate_size,
        filter_strict=filter_strict,
        timeout_seconds=timeout_seconds,
    )


def fixture_source(tmp_path: Path, clock: FrozenClock | None = None) -> FixtureOptionSource:
    return FixtureOptionSource(
        write_option_fixtures(tmp_path / "fixtures"), clock or FrozenClock(DEFAULT_MOMENT)
    )


# -- the port ---------------------------------------------------------------------------------


def test_the_service_is_an_option_slate_planner(tmp_path: Path) -> None:
    assert isinstance(service([fixture_source(tmp_path)]), OptionSlatePlanner)


def test_a_slate_answers_the_round_it_was_asked_for(tmp_path: Path) -> None:
    slate = service([fixture_source(tmp_path)]).plan(KIND, requirements(place="Paris"), a_plan(), 2)

    assert isinstance(slate, OptionSlate)
    assert (slate.kind_key, slate.round_index) == (KIND, 2)
    assert slate.requirements_digest == requirements(place="Paris").digest()


# -- the query it builds ------------------------------------------------------------------------


def test_the_query_carries_the_slate_size_times_the_candidate_factor(tmp_path: Path) -> None:
    """A source is asked for a pool: a source asked for exactly three chooses the slate itself."""
    source = RecordedOptionSource({}, DEFAULT_MOMENT)
    service([source], slate_size=3).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert source.queries[0].slate_size == 3 * CANDIDATE_FACTOR


def test_the_request_id_is_derived_so_two_identical_rounds_are_identical(
    tmp_path: Path,
) -> None:
    source = RecordedOptionSource({}, DEFAULT_MOMENT)
    planner = service([source])

    planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)
    planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert source.queries[0].request_id == source.queries[1].request_id
    assert requirements(place="Paris").digest() in source.queries[0].request_id


def test_the_query_carries_the_selections_an_outcome_dependency_entitles_it_to(
    option_factory: OptionFactory,
) -> None:
    """``alpha`` declares ``requires_outcome_of: [beta]``, so it may read beta's choice."""
    plan = a_plan()
    chosen = option_factory("b1", "beta")
    component = plan.ensure_component("beta")
    component.advance_to(ComponentStatus.ELICITING)
    component.advance_to(ComponentStatus.READY)
    component.advance_to(ComponentStatus.SOURCING)
    plan.record_slate(OptionSlate(kind_key="beta", round_index=0, options=(chosen,)))
    plan.record_selection(Selection("beta", chosen, chosen_at_turn=1))
    source = RecordedOptionSource({}, DEFAULT_MOMENT)

    service([source]).plan(KIND, requirements(place="Paris"), plan, 0)

    assert source.queries[0].selection_of("beta") is not None
    assert source.queries[0].selection_of("beta").option_id == "b1"  # type: ignore[union-attr]


def test_a_kind_reads_no_selection_it_was_not_entitled_to(option_factory: OptionFactory) -> None:
    """``beta`` declares no Outcome Dependency, so it is handed nothing about the trip."""
    plan = a_plan()
    plan.mark_selected(KIND)
    source = RecordedOptionSource({}, DEFAULT_MOMENT)

    service([source]).plan("beta", requirements("beta", place="Paris"), plan, 0)

    assert dict(source.queries[0].context_selections) == {}
    assert option_factory("x")


# -- merging and de-duplication -----------------------------------------------------------------


def test_two_sources_are_called_in_order_and_their_options_merged(
    option_factory: OptionFactory,
) -> None:
    first = RecordedOptionSource(
        {KIND: [option_factory("a1", price=Money(10000, "EUR"))]},
        DEFAULT_MOMENT,
        source_id="world",
    )
    second = RecordedOptionSource(
        {KIND: [option_factory("a2", price=Money(20000, "EUR"))]},
        DEFAULT_MOMENT,
        source_id="fixture",
    )

    slate = service([first, second]).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert [option.option_id for option in slate.options] == ["a1", "a2"]
    assert first.queries and second.queries


def test_a_source_that_publishes_no_reference_still_offers_every_option(
    option_factory: OptionFactory,
) -> None:
    """De-duplication keys on ``option_id`` when there is no ``external_ref`` to key on.

    Keying every unreferenced option by ``None`` would collapse a whole slate into its first
    row, and a provider without stable references is an ordinary provider, not a broken one.
    """
    source = RecordedOptionSource(
        {KIND: [option_factory("a1"), option_factory("a2"), option_factory("a3")]},
        DEFAULT_MOMENT,
    )

    slate = service([source]).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert [option.option_id for option in slate.options] == ["a1", "a2", "a3"]
    assert all(option.provenance.external_ref is None for option in slate.options)


def test_the_same_option_twice_from_one_source_is_counted_once(
    option_factory: OptionFactory,
) -> None:
    """De-duplication is by ``(source_id, external_ref)``, and ``OptionSlate`` would refuse a
    repeated ``option_id`` outright — so the merge has to catch it first."""
    repeated = option_factory("a1", price=Money(10000, "EUR"))
    source = RecordedOptionSource({KIND: [repeated, repeated]}, DEFAULT_MOMENT)

    slate = service([source]).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert [option.option_id for option in slate.options] == ["a1"]


# -- soft filters -------------------------------------------------------------------------------


def test_an_option_failing_an_optional_filter_is_demoted_and_marked(tmp_path: Path) -> None:
    """The client's own case: "under €150" still shows the €160 room, below and marked."""
    held = requirements(place="Paris", budget_ceiling="60000 EUR")

    slate = service([fixture_source(tmp_path)]).plan(KIND, held, a_plan(), 0)

    assert slate.options
    passing = [option for option in slate.options if option.satisfies_every_filter]
    failing = [option for option in slate.options if not option.satisfies_every_filter]
    assert [option.option_id for option in slate.options] == [
        *(option.option_id for option in passing),
        *(option.option_id for option in failing),
    ]


def test_a_ceiling_below_every_price_still_answers_and_marks_every_option(
    tmp_path: Path,
) -> None:
    held = requirements(place="Paris", budget_ceiling="1000 EUR")

    slate = service([fixture_source(tmp_path)]).plan(KIND, held, a_plan(), 0)

    assert len(slate.options) == 3
    assert all(option.filter_notes == ("budget_ceiling",) for option in slate.options)


def test_strict_filtering_discards_instead_of_demoting(tmp_path: Path) -> None:
    held = requirements(place="Paris", budget_ceiling="1000 EUR")

    slate = service([fixture_source(tmp_path)], filter_strict=True).plan(KIND, held, a_plan(), 0)

    assert slate.options == ()
    assert "filtered_out" in slate.diagnostics


def test_strict_filtering_keeps_whatever_passes(tmp_path: Path) -> None:
    held = requirements(place="Paris", budget_ceiling="60000 EUR")

    slate = service([fixture_source(tmp_path)], filter_strict=True).plan(KIND, held, a_plan(), 0)

    assert slate.options
    assert all(option.satisfies_every_filter for option in slate.options)


def test_a_kind_the_catalog_does_not_declare_has_no_filters_and_is_still_sourced(
    tmp_path: Path,
) -> None:
    """What may be planned is the catalog's question, and it was asked before this point."""
    slate = service([fixture_source(tmp_path)]).plan(
        "omega", RequirementSet.empty("omega"), a_plan(), 0
    )

    assert slate.kind_key == "omega"
    assert all(option.kind_key == "omega" for option in slate.options)


# -- determinism ---------------------------------------------------------------------------------


def test_the_same_query_twice_yields_identical_slates(tmp_path: Path) -> None:
    planner = service([fixture_source(tmp_path)])
    held = requirements(place="Paris")

    first = planner.plan(KIND, held, a_plan(), 0)
    second = planner.plan(KIND, held, a_plan(), 0)

    assert first == second


def test_a_refinement_changing_a_filter_yields_a_different_slate(tmp_path: Path) -> None:
    planner = service([fixture_source(tmp_path)])

    before = planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)
    after = planner.plan(KIND, requirements(place="Paris", min_score=8.5), a_plan(), 1)

    assert before.requirements_digest != after.requirements_digest
    assert [option.option_id for option in before.options] != [
        option.option_id for option in after.options
    ]


@pytest.mark.parametrize(("asked", "expected"), [(1, 1), (3, 3), (10, 5)])
def test_the_slate_size_is_honoured_even_when_fewer_options_exist(
    tmp_path: Path, asked: int, expected: int
) -> None:
    slate = service([fixture_source(tmp_path)], slate_size=asked).plan(
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert len(slate.options) == expected


# -- failure -------------------------------------------------------------------------------------


def test_one_source_failing_of_two_yields_a_slate_from_the_survivor_and_a_diagnostic(
    option_factory: OptionFactory,
) -> None:
    broken = FailingOptionSource(source_id="world")
    working = RecordedOptionSource(
        {KIND: [option_factory("a1", price=Money(10000, "EUR"))]},
        DEFAULT_MOMENT,
        source_id="fixture",
    )

    slate = service([broken, working]).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert [option.option_id for option in slate.options] == ["a1"]
    assert slate.diagnostics == ("source_failed:world",)
    assert broken.queries, "the failing source was asked, not skipped"


def test_every_source_failing_raises_an_option_sourcing_error() -> None:
    sources = [FailingOptionSource(source_id="world"), FailingOptionSource(source_id="live")]

    with pytest.raises(OptionSourcingError, match="every Option Source"):
        service(sources).plan(KIND, requirements(place="Paris"), a_plan(), 0)


def test_no_source_registered_for_a_kind_is_a_configuration_bug() -> None:
    planner = service(
        [],
        profile=OptionSourceProfile(per_kind={KIND: "live"}),
        registry_sources={"fixture": (RecordedOptionSource({}, DEFAULT_MOMENT),)},
    )

    with pytest.raises(UnknownComponentKindError, match="no Option Source is registered"):
        planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)


def test_an_empty_answer_is_a_slate_and_not_an_exception() -> None:
    """F05 already turns an empty slate into ``report_sourcing_failure``."""
    slate = service([RecordedOptionSource({}, DEFAULT_MOMENT)]).plan(
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert slate.options == ()
    assert slate.diagnostics == ("nothing_found",)


class _SlowSource:
    """A source that takes time: it moves the very Clock the service measures it with."""

    def __init__(self, clock: FrozenClock, seconds: float, options: Sequence[PlanOption]) -> None:
        self._clock = clock
        self._seconds = seconds
        self._options = tuple(options)

    @property
    def source_id(self) -> str:
        return "fake:slow"

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset({KIND})

    def search(self, query: OptionQuery) -> OptionSourceResult:
        del query
        self._clock.advance(timedelta(seconds=self._seconds))
        return OptionSourceResult(
            options=self._options, source_id=self.source_id, retrieved_at=self._clock.now()
        )


def test_a_source_that_overruns_its_budget_is_skipped_and_recorded(
    option_factory: OptionFactory,
) -> None:
    """The backstop: a source that ignores its own budget cannot hold a conversation open."""
    clock = FrozenClock(DEFAULT_MOMENT)
    slow = _SlowSource(clock, 30.0, [option_factory("a1", price=Money(10000, "EUR"))])
    quick = RecordedOptionSource(
        {KIND: [option_factory("a2", price=Money(20000, "EUR"))]}, DEFAULT_MOMENT
    )

    slate = service([slow, quick], clock=clock, timeout_seconds=1.0).plan(
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert [option.option_id for option in slate.options] == ["a2"]
    assert "source_timed_out:fake:slow" in slate.diagnostics


def test_every_source_overrunning_is_every_source_failing(
    option_factory: OptionFactory,
) -> None:
    """A budget nobody met is the same answer as a provider nobody could reach."""
    clock = FrozenClock(DEFAULT_MOMENT)
    slow = _SlowSource(clock, 30.0, [option_factory("a1")])

    with pytest.raises(OptionSourcingError, match="every Option Source"):
        service([slow], clock=clock, timeout_seconds=1.0).plan(
            KIND, requirements(place="Paris"), a_plan(), 0
        )


def test_a_source_inside_its_budget_is_used(option_factory: OptionFactory) -> None:
    clock = FrozenClock(DEFAULT_MOMENT)
    prompt = _SlowSource(clock, 0.25, [option_factory("a1", price=Money(10000, "EUR"))])

    slate = service([prompt], clock=clock, timeout_seconds=10.0).plan(
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert [option.option_id for option in slate.options] == ["a1"]
    assert slate.diagnostics == ()


class _HangingSource:
    """A source that does not answer at all until the test lets it go.

    ``_SlowSource`` simulates latency by moving the Clock, which is what a recorded
    conversation does; this one really blocks the thread it is called on, which is what a
    provider whose socket never closes does. Only the second kind proves the budget *bounds*
    the call rather than describing it afterwards.
    """

    def __init__(self) -> None:
        self.released = Event()

    @property
    def source_id(self) -> str:
        return "fake:hanging"

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset({KIND})

    def search(self, query: OptionQuery) -> OptionSourceResult:
        del query
        self.released.wait(30)  # bounded, so a broken test cannot wedge the suite forever
        return OptionSourceResult(options=(), source_id=self.source_id, retrieved_at=DEFAULT_MOMENT)


class _ExitingSource:
    """A source that leaves without answering and without raising anything catchable."""

    @property
    def source_id(self) -> str:
        return "fake:exiting"

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset({KIND})

    def search(self, query: OptionQuery) -> OptionSourceResult:
        del query
        raise SystemExit("a source that calls it a day")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_source_that_ends_without_answering_is_a_source_that_failed(
    option_factory: OptionFactory,
) -> None:
    """``SystemExit`` is not an ``Exception`` and is not caught. It is still a failed source.

    The warning being ignored is pytest's own report that a thread ended on an exception —
    which is exactly what this test arranges, and what the service is being asked to survive.
    """
    quick = RecordedOptionSource(
        {KIND: [option_factory("a2", price=Money(20000, "EUR"))]}, DEFAULT_MOMENT
    )

    slate = service([_ExitingSource(), quick]).plan(KIND, requirements(place="Paris"), a_plan(), 0)

    assert [option.option_id for option in slate.options] == ["a2"]
    assert "source_failed:fake:exiting" in slate.diagnostics


def test_a_source_that_never_answers_is_abandoned_at_its_deadline(
    option_factory: OptionFactory,
) -> None:
    """The budget stops a hung source. Without this, one provider holds the turn open forever."""
    hanging = _HangingSource()
    quick = RecordedOptionSource(
        {KIND: [option_factory("a2", price=Money(20000, "EUR"))]}, DEFAULT_MOMENT
    )
    planner = service([hanging, quick], timeout_seconds=0.25)

    started = monotonic()
    try:
        slate = planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)
        elapsed = monotonic() - started

        assert not hanging.released.is_set(), "the source is still hanging, by construction"
        assert elapsed < 5.0, f"the turn waited {elapsed:.1f}s on a source it should have left"
        assert [option.option_id for option in slate.options] == ["a2"]
        assert "source_timed_out:fake:hanging" in slate.diagnostics
    finally:
        hanging.released.set()


# -- the seams that are checked rather than trusted ------------------------------------------------


class _WrongKindSource:
    """A source that answers about a Component Kind nobody asked about."""

    def __init__(self, options: Sequence[PlanOption]) -> None:
        self._options = tuple(options)

    @property
    def source_id(self) -> str:
        return "fake:wrong_kind"

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset({KIND})

    def search(self, query: OptionQuery) -> OptionSourceResult:
        del query
        return OptionSourceResult(
            options=self._options, source_id=self.source_id, retrieved_at=DEFAULT_MOMENT
        )


def test_a_source_answering_about_another_component_kind_is_skipped(
    option_factory: OptionFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A source that breaks its contract is a source that failed: logged, recorded, skipped."""
    broken = _WrongKindSource([option_factory("b1", "beta")])
    working = RecordedOptionSource(
        {KIND: [option_factory("a1", price=Money(10000, "EUR"))]}, DEFAULT_MOMENT
    )

    with caplog.at_level(logging.WARNING):
        slate = service([broken, working]).plan(KIND, requirements(place="Paris"), a_plan(), 0)  # type: ignore[list-item]

    assert [option.option_id for option in slate.options] == ["a1"]
    assert "source_failed:fake:wrong_kind" in slate.diagnostics
    assert "another Component Kind" in caplog.text


def test_a_source_returning_more_than_it_was_asked_for_is_skipped(
    option_factory: OptionFactory,
) -> None:
    broken = _WrongKindSource([option_factory(f"a{position}") for position in range(20)])
    working = RecordedOptionSource(
        {KIND: [option_factory("kept", price=Money(10000, "EUR"))]}, DEFAULT_MOMENT
    )

    slate = service([broken, working], slate_size=1).plan(  # type: ignore[list-item]
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert [option.option_id for option in slate.options] == ["kept"]
    assert "source_failed:fake:wrong_kind" in slate.diagnostics


def test_every_source_breaking_its_contract_is_every_source_failing(
    option_factory: OptionFactory,
) -> None:
    """Degrading needs a survivor. With none, this is the case that raises, like any other."""
    broken = _WrongKindSource([option_factory("b1", "beta")])

    with pytest.raises(OptionSourcingError, match="every Option Source"):
        service([broken]).plan(KIND, requirements(place="Paris"), a_plan(), 0)  # type: ignore[list-item]


class _InventingRanking:
    """A replaceable ranking that drops an option. Replaceable means checked, not trusted."""

    @property
    def ranking_id(self) -> str:
        return "inventing"

    def order(self, options: Sequence[PlanOption], query: OptionQuery) -> Sequence[PlanOption]:
        del query
        return list(options)[:1]


def test_a_ranking_that_drops_an_option_is_refused(option_factory: OptionFactory) -> None:
    source = RecordedOptionSource(
        {KIND: [option_factory("a1"), option_factory("a2")]}, DEFAULT_MOMENT
    )
    planner = PlanningService(
        catalog(),
        SourceRegistry(OptionSourceProfile(), {"fixture": (source,)}),
        _InventingRanking(),
        FrozenClock(DEFAULT_MOMENT),
        NullTelemetrySink(),
        slate_size=3,
    )

    with pytest.raises(ContractViolationError, match="exactly the options"):
        planner.plan(KIND, requirements(place="Paris"), a_plan(), 0)


# -- telemetry -------------------------------------------------------------------------------------


def test_one_event_per_sourcing_call_with_everything_f06_asks_for(tmp_path: Path) -> None:
    sink = RecordingSink()
    clock = FrozenClock(DEFAULT_MOMENT, step=timedelta(milliseconds=250))

    service([fixture_source(tmp_path, clock)], clock=clock, telemetry=sink).plan(
        KIND, requirements(place="Paris"), a_plan(), 1
    )

    assert [event.kind for event in sink.events] == [SOURCING_EVENT_KIND]
    fields = sink.events[0].fields
    assert fields["kind_key"] == KIND
    assert fields["round_index"] == 1
    assert fields["source_ids"] == ("fixture",)
    assert fields["options_found"] == 5
    assert fields["options_presented"] == 3
    assert fields["sources_failed"] == 0
    assert fields["synthesised"] is False
    assert isinstance(fields["latency_ms"], float)
    assert fields["latency_ms"] > 0
    assert fields["profile"] == "fixture"
    assert fields["ranking_id"] == "cheapest_first"


def test_the_synthesised_flag_says_when_nothing_was_recorded(tmp_path: Path) -> None:
    sink = RecordingSink()

    service([fixture_source(tmp_path)], telemetry=sink).plan(
        KIND, requirements(place="Reykjavik"), a_plan(), 0
    )

    assert sink.events[0].fields["synthesised"] is True


def test_a_failing_source_is_counted_in_the_event(option_factory: OptionFactory) -> None:
    sink = RecordingSink()
    working = RecordedOptionSource({KIND: [option_factory("a1")]}, DEFAULT_MOMENT)

    service([FailingOptionSource(source_id="world"), working], telemetry=sink).plan(
        KIND, requirements(place="Paris"), a_plan(), 0
    )

    assert sink.events[0].fields["sources_failed"] == 1
    assert sink.events[0].fields["diagnostics"] == ("source_failed:world",)


def test_nothing_is_recorded_when_every_source_fails() -> None:
    """The call did not produce a slate, so there is no slate to record — the log says why."""
    sink = RecordingSink()

    with pytest.raises(OptionSourcingError):
        service([FailingOptionSource()], telemetry=sink).plan(
            KIND, requirements(place="Paris"), a_plan(), 0
        )

    assert sink.events == []
