"""``analyse()``: what still blocks planning, what only filters it, and what cannot be used."""

from __future__ import annotations

from datetime import date

import pytest

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.requirements import (
    BlockingGap,
    BlockingRule,
    CandidateGroup,
    FieldKind,
    FieldSpec,
    GapReport,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
    analyse,
    schema_problems,
)
from tourganize.domain.requirements.validation import (
    REASON_ABOVE_MAXIMUM,
    REASON_DATE_RANGE_REVERSED,
)

PLACE = FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place")
RANGE = FieldSpec("date_range", FieldKind.DATE_RANGE, Obligation.BLOCKING, "ask.alpha.date_range")
STARTS = FieldSpec("starts_on", FieldKind.DATE, Obligation.OPTIONAL, "ask.alpha.starts_on")
ENDS = FieldSpec("ends_on", FieldKind.DATE, Obligation.OPTIONAL, "ask.alpha.ends_on")
PARTY = FieldSpec(
    "party_size",
    FieldKind.INTEGER,
    Obligation.OPTIONAL,
    "ask.alpha.party_size",
    constraints={"min": 1, "max": 12},
)
RATING = FieldSpec(
    "min_rating",
    FieldKind.SCORE,
    Obligation.OPTIONAL,
    "ask.alpha.min_rating",
    constraints={"min": 0, "max": 10},
)

#: The client's own rule: "there should be some time range, if not a specific start and end
#: date". One obligation, two ways to satisfy it.
SCHEMA = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(PLACE, RANGE, STARTS, ENDS, PARTY, RATING),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",), ("starts_on", "ends_on"))),
    ),
)


def given(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(name, value) for name, value in values.items()]
    return RequirementSet.empty("alpha").with_updates(updates, schema=SCHEMA)


def test_an_empty_set_blocks_on_every_rule_and_lists_every_optional_field() -> None:
    report = analyse(SCHEMA, given())

    assert report.component_kind == "alpha"
    assert report.blocking_rule_names == ("where", "when")
    assert report.optional_field_names == ("starts_on", "ends_on", "party_size", "min_rating")
    assert report.invalid == ()
    assert not report.is_plannable


def test_a_satisfied_schema_is_plannable_and_still_lists_its_filters() -> None:
    report = analyse(SCHEMA, given(place="Paris", date_range="2026-10-23/2026-10-28"))

    assert report.blocking == ()
    assert report.is_plannable
    assert report.optional_field_names == ("starts_on", "ends_on", "party_size", "min_rating")


def test_an_explicit_pair_satisfies_the_same_rule_as_a_range() -> None:
    """The client's rule, stated as a test: `starts_on` + `ends_on` closes `when`."""
    report = analyse(SCHEMA, given(place="Paris", starts_on="2026-10-23", ends_on="2026-10-28"))

    assert "when" not in report.blocking_rule_names
    assert report.is_plannable


def test_half_of_the_pair_does_not_satisfy_the_rule() -> None:
    report = analyse(SCHEMA, given(place="Paris", starts_on="2026-10-23"))

    assert report.blocking_rule_names == ("when",)
    assert not report.is_plannable


def test_a_partly_filled_group_reports_only_what_is_still_missing_from_it() -> None:
    """*Which* group to pursue is F05's asking policy; the gap hands over both, in file order."""
    gap = analyse(SCHEMA, given(place="Paris", starts_on="2026-10-23")).next_blocking()

    assert gap is not None
    assert gap.field_names == (("date_range",), ("starts_on", "ends_on"))
    assert [group.missing for group in gap.candidates] == [("date_range",), ("ends_on",)]
    assert gap.prompt_message_keys == ("ask.alpha.date_range", "ask.alpha.ends_on")


def test_a_blocking_gap_carries_every_candidate_group() -> None:
    gap = analyse(SCHEMA, given(place="Paris")).next_blocking()

    assert gap is not None
    assert gap.rule_name == "when"
    assert gap.field_names == (("date_range",), ("starts_on", "ends_on"))
    assert [group.missing for group in gap.candidates] == [
        ("date_range",),
        ("starts_on", "ends_on"),
    ]
    assert gap.candidates[0].missing_fields == (RANGE,)


def test_next_blocking_follows_the_schemas_declaration_order() -> None:
    report = analyse(SCHEMA, given())

    first = report.next_blocking()
    assert first is not None
    assert first.rule_name == "where"


def test_next_blocking_is_none_once_nothing_blocks() -> None:
    assert (
        analyse(SCHEMA, given(place="Paris", date_range="2026-10-23/2026-10-28")).next_blocking()
        is None
    )


def test_a_present_but_invalid_blocking_value_is_invalid_not_blocking() -> None:
    """Asking for dates the traveller already gave is worse than telling them what is wrong."""
    report = analyse(SCHEMA, given(place="Paris", date_range="2026-10-28/2026-10-23"))

    assert report.blocking == ()
    assert report.invalid_field_names == ("date_range",)
    assert report.invalid[0].reason_message_key == REASON_DATE_RANGE_REVERSED
    assert not report.is_plannable


def test_an_invalid_optional_value_is_reported_but_never_blocks_planning() -> None:
    """Optional filters never block — the bad one is re-asked *alongside* the first slate."""
    report = analyse(
        SCHEMA, given(place="Paris", date_range="2026-10-23/2026-10-28", min_rating=99)
    )

    assert report.blocking == ()
    assert report.invalid_field_names == ("min_rating",)
    assert report.invalid[0].reason_message_key == REASON_ABOVE_MAXIMUM
    assert not report.invalid[0].blocks
    assert report.blocking_invalid == ()
    assert report.is_plannable


def test_an_invalid_value_a_blocking_rule_reads_does_block_planning() -> None:
    """`starts_on` is an optional *field*, but the `when` rule reads it: a bad one gates."""
    report = analyse(SCHEMA, given(place="Paris", starts_on="the 23rd", ends_on="2026-10-28"))

    assert report.blocking == ()
    assert report.invalid_field_names == ("starts_on",)
    assert report.invalid[0].blocks
    assert not report.is_plannable


def test_invalid_findings_come_back_in_schema_declaration_order() -> None:
    report = analyse(
        SCHEMA, given(date_range="2026-10-28/2026-10-23", party_size=99, min_rating=99)
    )

    assert report.invalid_field_names == ("date_range", "party_size", "min_rating")


def test_an_optional_field_with_a_value_is_no_longer_a_gap() -> None:
    report = analyse(SCHEMA, given(place="Paris", date_range="2026-10-23/2026-10-28", party_size=2))

    assert "party_size" not in report.optional_field_names


def test_a_schema_with_no_rules_blocks_on_each_blocking_field() -> None:
    derived = RequirementSchema("alpha.v1", "alpha", (PLACE, RANGE, PARTY))

    report = analyse(derived, RequirementSet.empty("alpha"))

    assert report.blocking_rule_names == ("place", "date_range")
    assert report.optional_field_names == ("party_size",)


def test_a_schema_of_filters_alone_is_refused_at_load_though_analyse_still_answers() -> None:
    """Nothing would gate planning, so it would be sourced against an empty Requirement Set."""
    filters_only = RequirementSchema("alpha.v1", "alpha", (PARTY, RATING))

    report = analyse(filters_only, RequirementSet.empty("alpha"))

    assert report.is_plannable  # pure and total: it answers for any schema handed to it
    assert report.optional_field_names == ("party_size", "min_rating")
    assert schema_problems(filters_only)  # ... but no adapter would ever hand it one


def test_adding_an_optional_field_to_a_schema_needs_no_python_change() -> None:
    """The whole point of a declarative schema: a new filter is a line in a file."""
    extended = RequirementSchema(
        schema_key=SCHEMA.schema_key,
        component_kind=SCHEMA.component_kind,
        fields=(
            *SCHEMA.fields,
            FieldSpec("breakfast", FieldKind.BOOLEAN, Obligation.OPTIONAL, "ask.alpha.breakfast"),
        ),
        blocking_rules=SCHEMA.blocking_rules,
    )

    report = analyse(extended, RequirementSet.empty("alpha"))

    assert report.optional_field_names[-1] == "breakfast"
    assert report.blocking_rule_names == ("where", "when")


def test_analysing_a_set_from_another_component_kind_is_a_programming_error() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        analyse(SCHEMA, RequirementSet.empty("beta"))

    assert "beta" in str(raised.value)


def test_a_rule_naming_an_undeclared_field_is_refused_rather_than_skipped() -> None:
    """The adapter never hands one over — but a hand-built schema must not analyse silently."""
    broken = RequirementSchema(
        "alpha.v1", "alpha", (PLACE,), (BlockingRule("where", (("nowhere",),)),)
    )

    with pytest.raises(InvariantViolationError) as raised:
        analyse(broken, RequirementSet.empty("alpha"))

    assert "nowhere" in str(raised.value)


def test_a_gap_with_a_fully_present_group_is_not_a_gap() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        CandidateGroup((STARTS, ENDS), ())

    assert "not a gap" in str(raised.value)


def test_a_gap_report_needs_a_component_kind() -> None:
    with pytest.raises(InvariantViolationError):
        GapReport("")


def test_a_group_cannot_be_missing_a_field_it_does_not_contain() -> None:
    """Fields and their missing names travel together, so they cannot drift out of step."""
    with pytest.raises(InvariantViolationError) as raised:
        CandidateGroup((RANGE,), ("starts_on",))

    assert "which the group does not contain" in str(raised.value)


def test_a_rule_with_no_candidate_group_could_never_be_satisfied() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        BlockingGap("when", ())

    assert "never" in str(raised.value)


def test_a_normalised_value_reaches_the_report_normalised() -> None:
    held = given(place="  Paris  ", date_range="2026-10-23/2026-10-28")

    assert held.value_of("place") == "Paris"
    range_value = held.value_of("date_range")
    assert getattr(range_value, "start", None) == date(2026, 10, 23)
    assert analyse(SCHEMA, held).is_plannable
