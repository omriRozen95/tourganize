"""Optional filters: declared in data, soft by construction, and a note rather than a sentence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest

from tourganize.domain.options import Money, PlanOption
from tourganize.domain.options.filters import (
    Comparison,
    OptionFilter,
    filter_notes_for,
    filters_of,
)
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)

OptionFactory = Callable[..., PlanOption]


def _optional(
    name: str,
    kind: FieldKind,
    *,
    enum_values: tuple[str, ...] = (),
    **constraints: object,
) -> FieldSpec:
    return FieldSpec(
        name=name,
        field_kind=kind,
        obligation=Obligation.OPTIONAL,
        prompt_message_key=f"ask.alpha.{name}",
        enum_values=enum_values,
        constraints=constraints,
    )


SCHEMA = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        _optional("budget_ceiling", FieldKind.MONEY, filters="price", comparison="at_most"),
        _optional(
            "min_score",
            FieldKind.SCORE,
            min=0,
            max=10,
            filters="review_score",
            comparison="at_least",
        ),
        _optional(
            "comfort",
            FieldKind.ENUM,
            enum_values=("basic", "standard", "premium"),
            filters="comfort",
            comparison="equals",
        ),
        # Declared optional and *not* a filter: a preference no option can be measured against.
        _optional("notes_to_supplier", FieldKind.TEXT),
    ),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)


def requirements(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty("alpha").with_updates(updates, schema=SCHEMA)


def test_only_answered_optional_fields_become_filters() -> None:
    """An unanswered optional field is a question F05 may still ask, not a demand."""
    assert filters_of(SCHEMA, requirements()) == ()

    filters = filters_of(SCHEMA, requirements(budget_ceiling="15000 EUR"))

    assert [item.field_name for item in filters] == ["budget_ceiling"]
    assert filters[0].fact_name == "price"
    assert filters[0].comparison is Comparison.AT_MOST


def test_an_optional_field_that_declares_no_filter_never_demotes_anything() -> None:
    """Inventing a comparison the schema never stated is inventing a rule."""
    filters = filters_of(SCHEMA, requirements(notes_to_supplier="quiet room"))

    assert filters == ()


def test_filters_come_back_in_the_schema_s_declaration_order() -> None:
    filters = filters_of(
        SCHEMA, requirements(min_score=8.0, budget_ceiling="15000 EUR", comfort="premium")
    )

    assert [item.field_name for item in filters] == [
        "budget_ceiling",
        "min_score",
        "comfort",
    ]


def test_a_price_ceiling_demotes_a_dearer_option(option_factory: OptionFactory) -> None:
    filters = filters_of(SCHEMA, requirements(budget_ceiling="15000 EUR"))
    cheap = option_factory("a1", price=Money(9000, "EUR"))
    dear = option_factory("a2", price=Money(74000, "EUR"))

    assert filter_notes_for(cheap, filters) == ()
    assert filter_notes_for(dear, filters) == ("budget_ceiling",)


def test_a_score_floor_reads_a_fact_by_the_name_the_schema_declares(
    option_factory: OptionFactory,
) -> None:
    filters = filters_of(SCHEMA, requirements(min_score=8.0))

    assert filter_notes_for(option_factory("a1", review_score=9.1), filters) == ()
    assert filter_notes_for(option_factory("a2", review_score=6.4), filters) == ("min_score",)


def test_an_exact_match_filter_compares_by_equality(option_factory: OptionFactory) -> None:
    filters = filters_of(SCHEMA, requirements(comfort="premium"))

    assert filter_notes_for(option_factory("a1", comfort="premium"), filters) == ()
    assert filter_notes_for(option_factory("a2", comfort="basic"), filters) == ("comfort",)


def test_an_option_that_does_not_publish_the_fact_passes(option_factory: OptionFactory) -> None:
    """Real providers return sparse records; a missing field is silence, not a failure."""
    filters = filters_of(SCHEMA, requirements(min_score=8.0))

    assert filter_notes_for(option_factory("a1", nights=5), filters) == ()


def test_two_currencies_are_never_compared(option_factory: OptionFactory) -> None:
    """There is no exchange rate in the domain, and a filter is not the place to invent one."""
    filters = filters_of(SCHEMA, requirements(budget_ceiling="15000 EUR"))

    assert filter_notes_for(option_factory("a1", price=Money(120000, "ILS")), filters) == ()


def test_every_failed_filter_is_noted_not_only_the_first(option_factory: OptionFactory) -> None:
    filters = filters_of(SCHEMA, requirements(budget_ceiling="1000 EUR", min_score=9.5))
    option = option_factory("a1", price=Money(74000, "EUR"), review_score=6.9)

    assert filter_notes_for(option, filters) == ("budget_ceiling", "min_score")


def test_a_note_is_a_field_name_and_never_a_sentence(option_factory: OptionFactory) -> None:
    """The Message Catalogue phrases it; the domain holds no prose to phrase it with."""
    filters = filters_of(SCHEMA, requirements(budget_ceiling="1000 EUR"))

    for note in filter_notes_for(option_factory("a1", price=Money(74000, "EUR")), filters):
        assert " " not in note
        assert SCHEMA.declares(note)


@pytest.mark.parametrize(
    ("comparison", "held", "wanted", "satisfied"),
    [
        (Comparison.AT_MOST, 2, 3, True),
        (Comparison.AT_MOST, 4, 3, False),
        (Comparison.AT_LEAST, 4, 3, True),
        (Comparison.AT_LEAST, 2, 3, False),
        (Comparison.EQUALS, "basic", "basic", True),
        (Comparison.EQUALS, "basic", "premium", False),
        (Comparison.AT_MOST, timedelta(hours=4), timedelta(hours=6), True),
        (Comparison.AT_MOST, date(2026, 10, 23), date(2026, 10, 28), True),
        (Comparison.AT_MOST, date(2026, 11, 2), date(2026, 10, 28), False),
        # A pair this release cannot order passes: silence is not a failure.
        (Comparison.AT_MOST, "soon", 3, True),
        (Comparison.AT_LEAST, True, 3, True),
    ],
)
def test_each_comparison_on_each_shape_it_can_read(
    option_factory: OptionFactory,
    comparison: Comparison,
    held: object,
    wanted: object,
    satisfied: bool,
) -> None:
    item = OptionFilter("stops", "stops", comparison, wanted)

    assert item.is_satisfied_by(option_factory("a1", stops=held)) is satisfied


def test_a_missing_comparison_is_read_as_a_ceiling(option_factory: OptionFactory) -> None:
    """A stated preference is almost always an upper bound: a budget, a number of stops."""
    schema = RequirementSchema(
        schema_key="beta.v1",
        component_kind="beta",
        fields=(
            FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.beta.place"),
            _optional("max_stops", FieldKind.INTEGER, filters="stops"),
        ),
        blocking_rules=(BlockingRule("where", (("place",),)),),
    )
    held = RequirementSet.empty("beta").with_updates(
        [RequirementUpdate(field_name="max_stops", value=1)], schema=schema
    )

    filters = filters_of(schema, held)

    assert filters[0].comparison is Comparison.AT_MOST
    assert filter_notes_for(option_factory("a1", "beta", stops=2), filters) == ("max_stops",)


def test_a_comparison_this_release_does_not_know_is_dropped_rather_than_guessed_at() -> None:
    """What a schema written for a later release looks like from here — F03's own reasoning."""
    schema = RequirementSchema(
        schema_key="beta.v1",
        component_kind="beta",
        fields=(
            FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.beta.place"),
            _optional("area", FieldKind.TEXT, filters="area", comparison="within_walking_distance"),
        ),
        blocking_rules=(BlockingRule("where", (("place",),)),),
    )
    held = RequirementSet.empty("beta").with_updates(
        [RequirementUpdate(field_name="area", value="6e")], schema=schema
    )

    assert filters_of(schema, held) == ()


def test_a_filter_refuses_a_comparison_that_is_not_one() -> None:
    with pytest.raises(Exception, match="comparison"):
        OptionFilter("budget_ceiling", "price", "at_most", 1)  # type: ignore[arg-type]
