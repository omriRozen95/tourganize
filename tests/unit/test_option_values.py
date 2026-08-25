"""The option value objects: exact money, traceable provenance, and no prose anywhere."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options import Money, OptionSlate, PlanOption, Provenance

OptionFactory = Callable[..., PlanOption]

#: Names that would turn a Plan Option into a rendered sentence. The bilingual requirement
#: breaks the moment one of these appears: wording has to be composed per locale at
#: presentation time, from message keys and structured facts.
PROSE_FIELD_NAMES = frozenset(
    {"title", "description", "summary", "label", "text", "name", "heading", "caption"}
)


def test_money_is_exact_and_rejects_a_float() -> None:
    assert Money(74000, "EUR").amount_minor == 74000

    with pytest.raises(InvariantViolationError) as raised:
        Money(740.00, "EUR")  # type: ignore[arg-type]

    assert "minor units" in str(raised.value)


def test_money_rejects_a_bool_disguised_as_an_integer() -> None:
    with pytest.raises(InvariantViolationError):
        Money(True, "EUR")  # type: ignore[arg-type]


@pytest.mark.parametrize("currency", ["eur", "EURO", "E", "", "12", "€"])
def test_money_requires_an_iso_currency_code(currency: str) -> None:
    with pytest.raises(InvariantViolationError):
        Money(100, currency)


def test_money_allows_zero_and_negative_amounts() -> None:
    """A refund and a free option are both real; only inexactness is forbidden."""
    assert Money(0, "ILS").amount_minor == 0
    assert Money(-2500, "ILS").amount_minor == -2500


def test_money_of_the_same_currency_compares_and_of_different_currencies_says_so() -> None:
    assert Money(100, "EUR") < Money(200, "EUR")
    assert Money(100, "EUR").same_currency_as(Money(900, "EUR"))
    assert not Money(100, "EUR").same_currency_as(Money(100, "ILS"))


def test_provenance_requires_a_source_and_an_aware_timestamp() -> None:
    moment = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)

    assert Provenance("fixture:alpha", moment).external_ref is None

    with pytest.raises(InvariantViolationError):
        Provenance("  ", moment)
    with pytest.raises(InvariantViolationError):
        Provenance("fixture:alpha", datetime(2026, 5, 1, 9, 30))


def test_a_plan_option_carries_no_prose_field() -> None:
    declared = {field.name for field in fields(PlanOption)}

    assert declared & PROSE_FIELD_NAMES == set()
    assert declared == {"option_id", "kind_key", "facts", "price", "provenance"}


def test_a_plan_option_needs_provenance_because_an_untraceable_option_cannot_be_shown(
    option_factory: OptionFactory,
) -> None:
    option = option_factory("a1", nights=5)

    assert option.provenance.source_id == "fixture:alpha"
    with pytest.raises(InvariantViolationError):
        PlanOption("a1", "alpha", {}, None, None)  # type: ignore[arg-type]


def test_option_facts_are_a_read_only_view(option_factory: OptionFactory) -> None:
    option = option_factory("a1", nights=5, refundable=True)

    assert option.fact("nights") == 5
    assert option.fact("absent") is None
    with pytest.raises(TypeError):
        option.facts["nights"] = 6  # type: ignore[index]


def test_a_slate_holds_a_round_and_finds_its_options(option_factory: OptionFactory) -> None:
    slate = OptionSlate("alpha", 0, (option_factory("a1"), option_factory("a2")), "digest-1")

    assert len(slate) == 2
    assert slate.contains("a1")
    assert not slate.contains("a3")
    assert slate.option("a2") is not None
    assert slate.option("a3") is None


def test_a_slate_may_be_empty_because_a_source_may_find_nothing() -> None:
    assert len(OptionSlate("alpha", 0)) == 0


def test_a_slate_refuses_options_of_another_component_kind(
    option_factory: OptionFactory,
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        OptionSlate("alpha", 0, (option_factory("b1", "beta"),))

    assert "another Component Kind" in str(raised.value)


def test_a_slate_refuses_a_repeated_option_id(option_factory: OptionFactory) -> None:
    with pytest.raises(InvariantViolationError):
        OptionSlate("alpha", 0, (option_factory("a1"), option_factory("a1")))


@pytest.mark.parametrize("round_index", [-1, -10])
def test_a_slate_round_is_never_negative(round_index: int) -> None:
    with pytest.raises(InvariantViolationError):
        OptionSlate("alpha", round_index)


def test_provenance_keeps_citations_for_f19_to_hang_grounding_off() -> None:
    moment = datetime(2026, 5, 1, tzinfo=UTC) + timedelta(hours=3)
    provenance = Provenance("corpus:visa-rules", moment, "page-4", ("doc-1#p12", "doc-1#p13"))

    assert provenance.citations == ("doc-1#p12", "doc-1#p13")
