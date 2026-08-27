"""The ``OptionSource`` contract, run against every adapter of the port.

This file is what makes [D9](../../docs/architecture/decisions.md)'s central promise
enforceable rather than aspirational: *a fixture's shape may never differ from the port
contract*. F17's world-backed source and F24's live providers are the next adapters, and each
of them is finished when this suite passes over it **unmodified** — a row in :data:`SOURCES` and
nothing else.

Nothing here asserts *which* options come back. That is the whole of what a source is free to
decide. What it asserts is the shape every consumer above the port relies on:

* the result is for the Component Kind that was asked about, and holds options of no other;
* every option carries Provenance, because an option nobody can trace back is not presentable;
* a priced option names a currency — the domain has no bare amounts and no exchange rate;
* ``option_id``s are unique within a result and **stable** across identical queries, which is
  what makes a Selection resolvable and a Golden Conversation replayable;
* no option carries prose, because wording is composed per locale at presentation time;
* ``slate_size`` is honoured, including when fewer options exist than were asked for.

Each of those is a module-level ``check_*`` function rather than an assertion inside a test, for
one reason: a suite that cannot fail is worth nothing, and the second half of this file
**proves these checks bite** by running four deliberately broken adapters through the very same
functions and asserting each is rejected. The suite itself stays green, because the broken
adapters are never parametrised into it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from conftest import write_option_fixtures

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.options.fake import RecordedOptionSource
from tourganize.adapters.options.fixture import FixtureOptionSource
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options import Money, PlanOption, Provenance
from tourganize.domain.options.query import OptionQuery, OptionSourceResult
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.ports.options import OptionSource

SourceBuilder = Callable[[Path], OptionSource]

#: The Component Kind every query in this suite asks about. Neutral, like the sample catalog's:
#: a test about the *port* should not have to name a travel topic.
KIND: Final = "alpha"

#: More than three space-separated words in a fact reads as a sentence. The same heuristic F05
#: applies to Act payloads, applied here to the one place F05 deliberately does not police —
#: a Plan Option's declared facts, which come from a provider and are checked at *this* seam.
PROSE_WORD_LIMIT: Final = 3

SCHEMA: Final = RequirementSchema(
    schema_key="alpha.v1",
    component_kind=KIND,
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        FieldSpec("date_range", FieldKind.DATE_RANGE, Obligation.BLOCKING, "ask.alpha.when"),
        FieldSpec("party_size", FieldKind.INTEGER, Obligation.OPTIONAL, "ask.alpha.party_size"),
    ),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",),)),
    ),
)


def requirements(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty(KIND).with_updates(updates, schema=SCHEMA)


def a_query(slate_size: int = 3, **values: object) -> OptionQuery:
    supplied = values or {"place": "Paris", "date_range": "2026-10-23/2026-10-28"}
    return OptionQuery(
        kind_key=KIND,
        requirements=requirements(**supplied),
        slate_size=slate_size,
        request_id=f"{KIND}:contract",
    )


class _Default:
    """ "No price was named", told apart from "this option has no price"."""


_DEFAULT: Final = _Default()


def an_option(
    option_id: str,
    *,
    kind_key: str = KIND,
    price: Money | _Default | None = _DEFAULT,
    facts: Mapping[str, object] | None = None,
) -> PlanOption:
    return PlanOption(
        option_id=option_id,
        kind_key=kind_key,
        facts={"review_score": 8.4} if facts is None else facts,
        price=Money(74000, "EUR") if isinstance(price, _Default) else price,
        provenance=Provenance(
            source_id="fake:recorded", retrieved_at=DEFAULT_MOMENT, external_ref=option_id
        ),
    )


def _fixture_source(root: Path) -> OptionSource:
    return FixtureOptionSource(write_option_fixtures(root), FrozenClock(DEFAULT_MOMENT))


def _empty_fixture_source(root: Path) -> OptionSource:
    """A Fixture Provider with nothing recorded: every query is answered synthetically.

    Parametrised on purpose. The synthetic fallback is a *code path that produces options a
    traveller will be shown*, so it has to satisfy the same contract as a recording — an
    invented option with no Provenance would be exactly as unpresentable.
    """
    return FixtureOptionSource(root / "empty", FrozenClock(DEFAULT_MOMENT))


def _recorded_source(root: Path) -> OptionSource:
    del root
    return RecordedOptionSource(
        {KIND: [an_option(f"rec-{position}") for position in range(1, 6)]},
        DEFAULT_MOMENT,
    )


#: Every adapter of the port, keyed by the name the test ids use. F17 and F24 append their own.
SOURCES: dict[str, SourceBuilder] = {
    "FixtureOptionSource": _fixture_source,
    "FixtureOptionSource(synthesising)": _empty_fixture_source,
    "RecordedOptionSource": _recorded_source,
}


@pytest.fixture(params=sorted(SOURCES), ids=sorted(SOURCES))
def source(request: pytest.FixtureRequest, tmp_path: Path) -> OptionSource:
    return SOURCES[request.param](tmp_path)


# -- the checks, as functions, so the second half of the file can prove they bite -------------


def check_answers_the_kind_it_was_asked_about(
    result: OptionSourceResult, query: OptionQuery
) -> None:
    foreign = [option.option_id for option in result.options if option.kind_key != query.kind_key]
    assert foreign == [], f"options of another Component Kind came back: {foreign}"


def check_every_option_is_traceable(result: OptionSourceResult) -> None:
    for option in result.options:
        assert option.provenance.source_id, f"{option.option_id} names no source"
        assert option.provenance.retrieved_at.tzinfo is not None, (
            f"{option.option_id} was retrieved at a naive moment"
        )


def check_every_price_names_a_currency(result: OptionSourceResult) -> None:
    """A price is a ``Money`` or nothing at all: there is no bare number in the domain."""
    for option in result.options:
        if option.price is None:
            continue
        assert isinstance(option.price, Money), f"{option.option_id} has a price that is not Money"
        assert option.price.currency, f"{option.option_id} has an amount with no currency"


def check_option_ids_are_unique(result: OptionSourceResult) -> None:
    identifiers = [option.option_id for option in result.options]
    assert len(set(identifiers)) == len(identifiers), f"a repeated option_id in {identifiers}"


def check_slate_size_is_honoured(result: OptionSourceResult, query: OptionQuery) -> None:
    assert len(result.options) <= query.slate_size, (
        f"{len(result.options)} options came back for a slate of {query.slate_size}"
    )


def check_no_option_holds_prose(result: OptionSourceResult) -> None:
    found = [
        f"{option.option_id}.{name}={value!r}"
        for option in result.options
        for name, value in option.facts.items()
        if isinstance(value, str) and len(value.split()) > PROSE_WORD_LIMIT
    ]
    assert found == [], f"a fact reads like a sentence: {found}"


def check_everything(result: OptionSourceResult, query: OptionQuery) -> None:
    """Every shape rule at once — what a broken adapter is run through below."""
    check_answers_the_kind_it_was_asked_about(result, query)
    check_every_option_is_traceable(result)
    check_every_price_names_a_currency(result)
    check_option_ids_are_unique(result)
    check_slate_size_is_honoured(result, query)
    check_no_option_holds_prose(result)


# -- the contract ----------------------------------------------------------------------------


def test_the_adapter_satisfies_the_protocol(source: OptionSource) -> None:
    assert isinstance(source, OptionSource)
    assert source.source_id
    assert isinstance(source.kind_keys, frozenset)


def test_a_search_answers_with_a_result_for_that_component_kind(source: OptionSource) -> None:
    query = a_query()

    result = source.search(query)

    assert isinstance(result, OptionSourceResult)
    assert result.source_id == source.source_id
    check_answers_the_kind_it_was_asked_about(result, query)


def test_every_option_can_be_traced_back_to_a_source(source: OptionSource) -> None:
    check_every_option_is_traceable(source.search(a_query()))


def test_every_price_names_its_currency(source: OptionSource) -> None:
    check_every_price_names_a_currency(source.search(a_query()))


def test_option_ids_are_unique_within_one_result(source: OptionSource) -> None:
    check_option_ids_are_unique(source.search(a_query()))


def test_no_option_holds_prose(source: OptionSource) -> None:
    """A provider does not know the traveller's language, so it may not compose a sentence."""
    check_no_option_holds_prose(source.search(a_query()))


@pytest.mark.parametrize("slate_size", [1, 3, 10])
def test_slate_size_is_honoured_even_when_fewer_options_exist(
    source: OptionSource, slate_size: int
) -> None:
    query = a_query(slate_size)

    check_slate_size_is_honoured(source.search(query), query)


def test_the_same_query_twice_answers_identically(source: OptionSource) -> None:
    """Stable ``option_id``s across identical queries: what makes F11's replay possible."""
    query = a_query()

    first = source.search(query)
    second = source.search(a_query())

    assert [option.option_id for option in first.options] == [
        option.option_id for option in second.options
    ]


def test_a_result_carries_the_moment_it_was_retrieved(source: OptionSource) -> None:
    result = source.search(a_query())

    assert result.retrieved_at.tzinfo is not None


def test_a_result_satisfies_every_rule_at_once(source: OptionSource) -> None:
    query = a_query()

    check_everything(source.search(query), query)


# -- the proof that the suite bites ----------------------------------------------------------


class _BrokenSource:
    """An adapter that answers with whatever it was handed, however wrong.

    Deliberately **not** in :data:`SOURCES`: its whole purpose is to fail the checks, and a
    suite that parametrised it would be red for ever. The tests below run it through the same
    ``check_*`` functions the contract uses and assert each one rejects it — which is what makes
    the green half of this file mean something.
    """

    def __init__(self, options: Iterable[PlanOption]) -> None:
        self._options = tuple(options)

    @property
    def source_id(self) -> str:
        return "fake:broken"

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset({KIND})

    def search(self, query: OptionQuery) -> OptionSourceResult:
        del query
        return OptionSourceResult(
            options=self._options,
            source_id=self.source_id,
            retrieved_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )


def _broken_result(options: Iterable[PlanOption]) -> tuple[OptionSourceResult, OptionQuery]:
    query = a_query()
    return _BrokenSource(options).search(query), query


def test_the_suite_rejects_prose_in_an_option() -> None:
    result, query = _broken_result(
        [an_option("p-1", facts={"summary": "a charming room overlooking the courtyard"})]
    )

    with pytest.raises(AssertionError, match="reads like a sentence"):
        check_everything(result, query)


def test_the_suite_rejects_an_amount_with_no_currency() -> None:
    """Two lines of defence against a bare amount, and this asserts both.

    The first is the domain type: ``PlanOption`` refuses a price that is not ``Money``, so an
    adapter that passes a provider's raw integer straight through never builds an option at
    all. That guard is a convention rather than an interpreter rule — a frozen dataclass can be
    written through with ``object.__setattr__``, and a provider adapter deserialising into
    ``__dict__`` is a real way to arrive there — so the contract check is written to reject a
    bare amount too, and the second half of this test is what proves it does.
    """
    with pytest.raises(InvariantViolationError, match="must be Money"):
        an_option("p-2", price=74000)  # type: ignore[arg-type]

    smuggled = an_option("p-2")
    object.__setattr__(smuggled, "price", 74000)
    result, query = _broken_result([smuggled])

    with pytest.raises(AssertionError, match="not Money"):
        check_everything(result, query)


def test_the_suite_rejects_a_duplicate_option_id() -> None:
    result, query = _broken_result([an_option("p-3"), an_option("p-3")])

    with pytest.raises(AssertionError, match="repeated option_id"):
        check_everything(result, query)


def test_the_suite_rejects_an_undeclared_component_kind() -> None:
    result, query = _broken_result([an_option("p-4", kind_key="omega")])

    with pytest.raises(AssertionError, match="another Component Kind"):
        check_everything(result, query)


def test_the_suite_rejects_a_slate_bigger_than_the_one_asked_for() -> None:
    result, query = _broken_result(an_option(f"p-{position}") for position in range(10))

    with pytest.raises(AssertionError, match="options came back for a slate"):
        check_everything(result, query)


def test_the_broken_source_would_otherwise_look_like_a_real_adapter() -> None:
    """The counterexamples are only worth something because the shell around them is valid."""
    assert isinstance(_BrokenSource([]), OptionSource)
