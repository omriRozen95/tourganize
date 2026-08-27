"""The Source Registry and the shipped ranking: which sources answer, and in what order."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.adapters.options import CheapestFirstRanking, SourceRegistry
from tourganize.adapters.options.fake import (
    FAILING_SOURCE_ID,
    RECORDED_SOURCE_ID,
    FailingOptionSource,
    RecordedOptionSource,
)
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.domain.options import Money, PlanOption
from tourganize.domain.options.query import OptionQuery
from tourganize.domain.requirements import RequirementSet
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.settings import OptionSourceProfile
from tourganize.ports.options import OptionRanking, OptionSource, OptionSourceRegistry

OptionFactory = Callable[..., PlanOption]


def a_query(kind_key: str = "alpha") -> OptionQuery:
    return OptionQuery(kind_key=kind_key, requirements=RequirementSet.empty(kind_key), slate_size=3)


def a_recorded_source(source_id: str = RECORDED_SOURCE_ID) -> RecordedOptionSource:
    return RecordedOptionSource({}, DEFAULT_MOMENT, source_id=source_id)


# -- the registry ----------------------------------------------------------------------------


def test_one_profile_serves_every_component_kind() -> None:
    source = a_recorded_source()
    registry = SourceRegistry(OptionSourceProfile(), {"fixture": (source,)})

    assert isinstance(registry, OptionSourceRegistry)
    assert registry.sources_for("alpha") == (source,)
    assert registry.sources_for("anything_at_all") == (source,)
    assert registry.profile_for("alpha") == "fixture"


def test_a_per_kind_override_sends_one_kind_somewhere_else() -> None:
    """A client with an account for one topic and none for another has to be able to mix."""
    fixture, live = a_recorded_source("fixture"), a_recorded_source("live")
    registry = SourceRegistry(
        OptionSourceProfile(per_kind={"beta": "live"}),
        {"fixture": (fixture,), "live": (live,)},
    )

    assert registry.sources_for("alpha") == (fixture,)
    assert registry.sources_for("beta") == (live,)
    assert registry.profile_for("beta") == "live"


def test_two_sources_for_one_kind_are_returned_in_call_order() -> None:
    """F17's shape: a world source with the Fixture Provider behind it as the fallback."""
    first, second = a_recorded_source("world"), a_recorded_source("fixture")
    registry = SourceRegistry(OptionSourceProfile(), {"fixture": (first, second)})

    assert registry.sources_for("alpha") == (first, second)


def test_a_profile_with_nothing_wired_behind_it_is_a_configuration_bug() -> None:
    registry = SourceRegistry(
        OptionSourceProfile(per_kind={"beta": "live"}), {"fixture": (a_recorded_source(),)}
    )

    assert registry.sources_for("alpha")
    with pytest.raises(UnknownComponentKindError, match="no Option Source is registered"):
        registry.sources_for("beta")


def test_registering_the_same_source_twice_is_refused() -> None:
    """De-duplication would hide it, and every option would have been counted twice.

    A ``ConfigurationError`` and not an ``UnknownComponentKindError``: no ``kind_key`` is
    involved, and the glossary reserves that error for a Kind the catalog does not declare.
    """
    with pytest.raises(ConfigurationError, match="repeats a source_id"):
        SourceRegistry(
            OptionSourceProfile(), {"fixture": (a_recorded_source(), a_recorded_source())}
        )


def test_the_registry_describes_itself_for_doctor() -> None:
    registry = SourceRegistry(
        OptionSourceProfile(),
        {"fixture": (a_recorded_source("world"), FailingOptionSource())},
    )

    assert registry.describe("alpha") == f"fixture: world, {FAILING_SOURCE_ID}"
    assert "nothing wired" in SourceRegistry(
        OptionSourceProfile(per_kind={"alpha": "live"}), {}
    ).describe("alpha")


# -- the ranking -----------------------------------------------------------------------------


def test_the_ranking_satisfies_the_port_and_names_itself() -> None:
    ranking = CheapestFirstRanking()

    assert isinstance(ranking, OptionRanking)
    assert ranking.ranking_id == "cheapest_first"


def test_options_that_fail_a_filter_are_demoted_below_ones_that_do_not(
    option_factory: OptionFactory,
) -> None:
    """The whole meaning of "demoted": a cheap option that fails still sorts after one that
    does not, which is what ranking by price first could never say."""
    cheap_but_failing = option_factory("a1", price=Money(1000, "EUR")).with_filter_notes(
        ["min_score"]
    )
    dear_but_passing = option_factory("a2", price=Money(90000, "EUR"))

    ordered = CheapestFirstRanking().order([cheap_but_failing, dear_but_passing], a_query())

    assert [option.option_id for option in ordered] == ["a2", "a1"]


def test_more_failed_filters_sort_lower(option_factory: OptionFactory) -> None:
    one = option_factory("a1", price=Money(90000, "EUR")).with_filter_notes(["min_score"])
    two = option_factory("a2", price=Money(1000, "EUR")).with_filter_notes(["min_score", "budget"])

    ordered = CheapestFirstRanking().order([two, one], a_query())

    assert [option.option_id for option in ordered] == ["a1", "a2"]


def test_within_one_currency_the_cheaper_option_leads(option_factory: OptionFactory) -> None:
    dear = option_factory("a1", price=Money(90000, "EUR"))
    cheap = option_factory("a2", price=Money(11000, "EUR"))

    ordered = CheapestFirstRanking().order([dear, cheap], a_query())

    assert [option.option_id for option in ordered] == ["a2", "a1"]


def test_two_currencies_are_grouped_rather_than_converted(option_factory: OptionFactory) -> None:
    """There is no exchange rate in the domain; ordering the *groups* is stable and honest."""
    shekels = option_factory("a1", price=Money(1000, "ILS"))
    euros = option_factory("a2", price=Money(90000, "EUR"))

    ordered = CheapestFirstRanking().order([shekels, euros], a_query())

    assert [option.price.currency for option in ordered if option.price] == ["EUR", "ILS"]


def test_an_unpriced_option_sorts_last(option_factory: OptionFactory) -> None:
    """Nothing is known about what it costs, and leading with it would read as a recommendation."""
    unpriced = option_factory("a1")
    priced = option_factory("a2", price=Money(148000, "EUR"))

    ordered = CheapestFirstRanking().order([unpriced, priced], a_query())

    assert [option.option_id for option in ordered] == ["a2", "a1"]


def test_a_declared_source_order_breaks_a_tie(option_factory: OptionFactory) -> None:
    preferred = PlanOption(
        option_id="a1",
        kind_key="alpha",
        facts={},
        price=Money(50000, "EUR"),
        provenance=option_factory("x").provenance,
    )
    fallback = PlanOption(
        option_id="a2",
        kind_key="alpha",
        facts={},
        price=Money(50000, "EUR"),
        provenance=option_factory("y").provenance,
    )

    ordered = CheapestFirstRanking(["fixture:alpha"]).order([fallback, preferred], a_query())

    assert [option.option_id for option in ordered] == ["a1", "a2"]


def test_the_ranking_returns_exactly_what_it_was_given(option_factory: OptionFactory) -> None:
    options = [option_factory(f"a{position}", price=Money(position, "EUR")) for position in (3, 1)]

    ordered = CheapestFirstRanking().order(options, a_query())

    assert sorted(option.option_id for option in ordered) == ["a1", "a3"]
    assert len(ordered) == len(options)


# -- the two ``OptionSource`` fakes ------------------------------------------------------------


def test_the_failing_source_records_what_it_was_asked_before_it_raises() -> None:
    """Proving it was *asked* and failed, rather than skipped, is the point of the record."""
    source = FailingOptionSource(kind_keys=["alpha"])

    with pytest.raises(Exception, match="cannot answer"):
        source.search(a_query())

    assert [query.kind_key for query in source.queries] == ["alpha"]
    assert isinstance(source, OptionSource)


def test_the_recorded_source_answers_with_what_it_was_handed(
    option_factory: OptionFactory,
) -> None:
    options = [option_factory("a1"), option_factory("a2")]
    source = RecordedOptionSource({"alpha": options}, DEFAULT_MOMENT, diagnostics=["replayed"])

    result = source.search(a_query())

    assert [option.option_id for option in result.options] == ["a1", "a2"]
    assert result.diagnostics == ("replayed",)
    assert source.kind_keys == frozenset({"alpha"})


def test_the_recorded_source_honours_the_slate_size_like_any_other_source(
    option_factory: OptionFactory,
) -> None:
    """A fake whose shape differs from the contract is the one thing D9 forbids."""
    options = [option_factory(f"a{position}") for position in range(5)]
    source = RecordedOptionSource({"alpha": options}, DEFAULT_MOMENT)

    result = source.search(
        OptionQuery(kind_key="alpha", requirements=RequirementSet.empty("alpha"), slate_size=2)
    )

    assert len(result.options) == 2
    assert result.partial is True


def test_the_recorded_source_answers_emptily_for_a_kind_it_holds_nothing_for() -> None:
    source = RecordedOptionSource({}, DEFAULT_MOMENT)

    assert source.search(a_query("omega")).options == ()
