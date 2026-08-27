"""Every Field Kind validator: what it accepts, what it normalises to, and how it refuses."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tourganize.domain.errors import InvariantViolationError, RequirementValueError
from tourganize.domain.options import Money
from tourganize.domain.requirements import (
    REASON_MESSAGE_KEYS,
    VALIDATORS,
    DateRange,
    FieldKind,
    FieldSpec,
    Obligation,
    normalise,
)
from tourganize.domain.requirements.validation import (
    REASON_ABOVE_MAXIMUM,
    REASON_BELOW_MINIMUM,
    REASON_BLANK,
    REASON_DATE_RANGE_REVERSED,
    REASON_MONEY_WITHOUT_CURRENCY,
    REASON_NOT_A_BOOLEAN,
    REASON_NOT_A_DATE,
    REASON_NOT_A_DATE_RANGE,
    REASON_NOT_A_DURATION,
    REASON_NOT_A_NUMBER,
    REASON_NOT_AN_INTEGER,
    REASON_NOT_IN_ENUM,
    REASON_NOT_MONEY,
    REASON_NOT_TEXT,
)


def spec(kind: FieldKind, name: str = "value", **extra: object) -> FieldSpec:
    return FieldSpec(
        name=name,
        field_kind=kind,
        obligation=Obligation.OPTIONAL,
        prompt_message_key=f"ask.alpha.{name}",
        **extra,  # type: ignore[arg-type]
    )


def test_every_field_kind_has_a_validator() -> None:
    """A new Field Kind is additive — but only if the registry grows with it."""
    assert set(VALIDATORS) == set(FieldKind)


def test_every_reason_key_is_declared() -> None:
    """F10 phrases this list; a key that is not on it is a key nobody can translate."""
    assert len(set(REASON_MESSAGE_KEYS)) == len(REASON_MESSAGE_KEYS)
    assert all(key.startswith("requirement.invalid.") for key in REASON_MESSAGE_KEYS)


@pytest.mark.parametrize(
    ("kind", "value", "normalised"),
    [
        (FieldKind.PLACE, "  Paris  ", "Paris"),
        (FieldKind.PLACE, "Tel Aviv", "Tel Aviv"),
        (FieldKind.TEXT, " a note ", "a note"),
        (FieldKind.DATE, "2026-10-23", date(2026, 10, 23)),
        (FieldKind.DATE, date(2026, 10, 23), date(2026, 10, 23)),
        (FieldKind.DATE, datetime(2026, 10, 23, 9, 30, tzinfo=UTC), date(2026, 10, 23)),
        (
            FieldKind.DATE_RANGE,
            "2026-10-23/2026-10-28",
            DateRange(date(2026, 10, 23), date(2026, 10, 28)),
        ),
        (
            FieldKind.DATE_RANGE,
            ["2026-10-23", "2026-10-28"],
            DateRange(date(2026, 10, 23), date(2026, 10, 28)),
        ),
        (
            FieldKind.DATE_RANGE,
            {"start": "2026-10-23", "end": "2026-10-28"},
            DateRange(date(2026, 10, 23), date(2026, 10, 28)),
        ),
        (FieldKind.INTEGER, 4, 4),
        (FieldKind.INTEGER, "4", 4),
        (FieldKind.INTEGER, 4.0, 4),
        (FieldKind.SCORE, 8, 8.0),
        (FieldKind.SCORE, "8.7", 8.7),
        (FieldKind.MONEY, Money(74000, "EUR"), Money(74000, "EUR")),
        (FieldKind.MONEY, {"amount_minor": 74000, "currency": "EUR"}, Money(74000, "EUR")),
        (FieldKind.MONEY, "74000 EUR", Money(74000, "EUR")),
        (FieldKind.MONEY, "25000 ils", Money(25000, "ILS")),
        (FieldKind.BOOLEAN, True, True),
        (FieldKind.BOOLEAN, False, False),
        (FieldKind.BOOLEAN, "true", True),
        (FieldKind.BOOLEAN, " False ", False),
        (FieldKind.DURATION, 90, timedelta(minutes=90)),
        (FieldKind.DURATION, "90", timedelta(minutes=90)),
        (FieldKind.DURATION, timedelta(minutes=45), timedelta(minutes=45)),
    ],
)
def test_an_accepted_value_is_normalised(
    kind: FieldKind, value: object, normalised: object
) -> None:
    assert normalise(spec(kind), value) == normalised


def test_an_enum_value_must_be_one_of_the_declared_ones() -> None:
    comfort = spec(FieldKind.ENUM, "comfort", enum_values=("basic", "standard"))

    assert normalise(comfort, " standard ") == "standard"
    with pytest.raises(RequirementValueError) as raised:
        normalise(comfort, "luxury")
    assert raised.value.reason_message_key == REASON_NOT_IN_ENUM


def test_normalising_is_idempotent() -> None:
    """A set is re-analysed on every turn; the values must not drift as it happens."""
    for kind, value in (
        (FieldKind.PLACE, "  Paris "),
        (FieldKind.DATE, "2026-10-23"),
        (FieldKind.DATE_RANGE, "2026-10-23/2026-10-28"),
        (FieldKind.MONEY, "74000 EUR"),
        (FieldKind.DURATION, "90"),
    ):
        once = normalise(spec(kind), value)
        assert normalise(spec(kind), once) == once


@pytest.mark.parametrize(
    ("kind", "value", "reason", "extra"),
    [
        (FieldKind.PLACE, "   ", REASON_BLANK, {}),
        (FieldKind.PLACE, 12, REASON_NOT_TEXT, {}),
        (FieldKind.TEXT, None, REASON_NOT_TEXT, {}),
        (FieldKind.DATE, "the 23rd", REASON_NOT_A_DATE, {}),
        (FieldKind.DATE, "next month", REASON_NOT_A_DATE, {}),
        (FieldKind.DATE, 20261023, REASON_NOT_A_DATE, {}),
        (FieldKind.DATE_RANGE, "2026-10-28/2026-10-23", REASON_DATE_RANGE_REVERSED, {}),
        (FieldKind.DATE_RANGE, "2026-10-23", REASON_NOT_A_DATE_RANGE, {}),
        (FieldKind.DATE_RANGE, ["2026-10-23"], REASON_NOT_A_DATE_RANGE, {}),
        (FieldKind.DATE_RANGE, {"from": "2026-10-23"}, REASON_NOT_A_DATE_RANGE, {}),
        (FieldKind.DATE_RANGE, 7, REASON_NOT_A_DATE_RANGE, {}),
        (FieldKind.INTEGER, "a few", REASON_NOT_AN_INTEGER, {}),
        (FieldKind.INTEGER, 4.5, REASON_NOT_AN_INTEGER, {}),
        (FieldKind.INTEGER, True, REASON_NOT_AN_INTEGER, {}),
        (FieldKind.INTEGER, 0, REASON_BELOW_MINIMUM, {"constraints": {"min": 1, "max": 12}}),
        (FieldKind.INTEGER, 13, REASON_ABOVE_MAXIMUM, {"constraints": {"min": 1, "max": 12}}),
        (FieldKind.SCORE, "excellent", REASON_NOT_A_NUMBER, {}),
        (FieldKind.SCORE, None, REASON_NOT_A_NUMBER, {}),
        (FieldKind.SCORE, True, REASON_NOT_A_NUMBER, {}),
        (FieldKind.SCORE, -1, REASON_BELOW_MINIMUM, {"constraints": {"min": 0, "max": 10}}),
        (FieldKind.SCORE, 11, REASON_ABOVE_MAXIMUM, {"constraints": {"min": 0, "max": 10}}),
        (FieldKind.MONEY, 74000, REASON_MONEY_WITHOUT_CURRENCY, {}),
        (FieldKind.MONEY, "74000", REASON_MONEY_WITHOUT_CURRENCY, {}),
        (FieldKind.MONEY, {"amount_minor": 74000}, REASON_MONEY_WITHOUT_CURRENCY, {}),
        (FieldKind.MONEY, {"amount_minor": 74000, "currency": "euro"}, REASON_NOT_MONEY, {}),
        (FieldKind.MONEY, "74000 EUR extra", REASON_NOT_MONEY, {}),
        (FieldKind.MONEY, "EUR 74000", REASON_NOT_MONEY, {}),
        (FieldKind.MONEY, "74000,EUR", REASON_MONEY_WITHOUT_CURRENCY, {}),
        (FieldKind.MONEY, None, REASON_NOT_MONEY, {}),
        (FieldKind.BOOLEAN, "maybe", REASON_NOT_A_BOOLEAN, {}),
        (FieldKind.BOOLEAN, "yes", REASON_NOT_A_BOOLEAN, {}),
        (FieldKind.BOOLEAN, "1", REASON_NOT_A_BOOLEAN, {}),
        (FieldKind.BOOLEAN, 1, REASON_NOT_A_BOOLEAN, {}),
        (FieldKind.DURATION, "a while", REASON_NOT_A_DURATION, {}),
        (FieldKind.DURATION, "2h", REASON_NOT_A_DURATION, {}),
        (FieldKind.DURATION, "1d", REASON_NOT_A_DURATION, {}),
        (FieldKind.DURATION, True, REASON_NOT_A_DURATION, {}),
        (FieldKind.DURATION, None, REASON_NOT_A_DURATION, {}),
        (FieldKind.DURATION, 500, REASON_ABOVE_MAXIMUM, {"constraints": {"max": 240}}),
    ],
)
def test_a_refused_value_names_its_field_and_its_reason(
    kind: FieldKind, value: object, reason: str, extra: dict[str, object]
) -> None:
    field_spec = spec(kind, "party_size", **extra)

    with pytest.raises(RequirementValueError) as raised:
        normalise(field_spec, value)

    assert raised.value.field_name == "party_size"
    assert raised.value.reason_message_key == reason
    assert reason in REASON_MESSAGE_KEYS
    assert "party_size" in str(raised.value)
    assert raised.value.detail


def test_money_never_assumes_a_currency() -> None:
    """The one invariant `Money` exists for: there is no exchange rate in the domain."""
    with pytest.raises(RequirementValueError) as raised:
        normalise(spec(FieldKind.MONEY), 74000)

    assert raised.value.reason_message_key == REASON_MONEY_WITHOUT_CURRENCY
    assert "currency" in raised.value.detail


def test_a_reversed_date_range_is_named_as_reversed_not_as_unreadable() -> None:
    with pytest.raises(RequirementValueError) as raised:
        normalise(spec(FieldKind.DATE_RANGE, "date_range"), "2026-10-28/2026-10-23")

    assert raised.value.reason_message_key == REASON_DATE_RANGE_REVERSED
    assert "2026-10-28" in raised.value.detail


def test_a_bound_is_rendered_the_way_it_was_written() -> None:
    """`10`, not `10.0`: the detail line ends up in a log a human reads."""
    with pytest.raises(RequirementValueError) as raised:
        normalise(spec(FieldKind.SCORE, "min_rating", constraints={"max": 10}), 11)

    assert "above the maximum 10" in raised.value.detail


def test_a_refusal_carries_everything_a_gap_report_finding_needs() -> None:
    """`analyse()` builds an Invalid Value straight from this exception, field name included."""
    bounded = spec(FieldKind.SCORE, "min_rating", constraints={"min": 0, "max": 10})

    assert normalise(bounded, 8.7) == 8.7
    with pytest.raises(RequirementValueError) as raised:
        normalise(bounded, 99)

    assert raised.value.field_name == "min_rating"
    assert raised.value.reason_message_key == REASON_ABOVE_MAXIMUM
    assert raised.value.detail


def test_a_long_offending_value_is_truncated_in_the_detail() -> None:
    with pytest.raises(RequirementValueError) as raised:
        normalise(spec(FieldKind.DATE), "x" * 500)

    assert len(raised.value.detail) < 120
    assert "..." in raised.value.detail


def test_a_date_range_knows_how_many_nights_it_spans() -> None:
    assert DateRange(date(2026, 10, 23), date(2026, 10, 28)).nights == 5


def test_a_date_range_renders_the_way_it_is_written_on_the_command_line() -> None:
    assert str(DateRange(date(2026, 10, 23), date(2026, 10, 28))) == "2026-10-23/2026-10-28"


@pytest.mark.parametrize(
    ("start", "end", "reason"),
    [
        (date(2026, 10, 28), date(2026, 10, 23), "must not end before it starts"),
        ("2026-10-23", date(2026, 10, 28), "must be a datetime.date"),
        (
            datetime(2026, 10, 23, tzinfo=UTC),
            date(2026, 10, 28),
            "must be a datetime.date",
        ),
    ],
)
def test_an_impossible_date_range_cannot_be_constructed(
    start: object, end: object, reason: str
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        DateRange(start, end)  # type: ignore[arg-type]

    assert reason in str(raised.value)
