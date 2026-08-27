"""What a valid value of each :class:`~tourganize.domain.requirements.schema.FieldKind` is.

One pure function per Field Kind, registered in :data:`VALIDATORS`, each of which either
returns the value in its **normalised** form or raises
:class:`~tourganize.domain.errors.RequirementValueError` naming the field and a
locale-neutral reason key. Normalising here is what makes everything downstream simple: a
``date`` is always a :class:`datetime.date` by the time the Gap Report sees it, a ``money`` is
always a :class:`~tourganize.domain.options.money.Money`, and a ``place`` is always a trimmed
string — so ``digest()`` is stable and F06's matching has one shape to match on.

Four boundaries this module deliberately does not cross:

* **Free text is not parsed here.** Each Field Kind accepts one canonical spelling — and the
  already-parsed Python type where there is one, because a ``date`` that arrived as a
  :class:`datetime.date` needs no spelling at all. ``"2h"`` for a duration, ``"yes"`` for a
  boolean and ``"EUR 74000"`` for an amount are all *extraction*, which is F08's job against a
  prompt and a model; accepting them here would put a second, weaker parser in the domain and
  make the two disagree about what a traveller meant.
* **Relative expressions are not interpreted here.** "this year", "next month" and "next
  Tuesday" are resolved against the ``Clock`` in the interpretation layer (F05/F08) *before*
  a value reaches a Requirement Set. Reading them here would pull a clock, and then a locale
  calendar, into a package whose whole value is that it has neither.
* **A place is not resolved.** It is trimmed and its case is preserved; turning "Paris" into
  an IATA code or a coordinate is F16/F17's work behind a port.
* **Money is never converted.** ``Money`` refuses a float and there is no exchange rate in the
  domain; a value that names no currency is invalid rather than assumed.

Every reason key this module can produce is listed in :data:`REASON_MESSAGE_KEYS`, so F10's
Message Catalogue can be checked against it instead of against a grep.

Gap analysis needs the *finding* rather than the exception — a present-but-invalid value has
to be reported so the dialogue can re-ask for it, and an exception would abandon the whole
report over one field — so :mod:`~tourganize.domain.requirements.gaps` catches
:class:`~tourganize.domain.errors.RequirementValueError`, which already names the field, the
reason key and the detail.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Final, NoReturn

from tourganize.domain.errors import InvariantViolationError, RequirementValueError
from tourganize.domain.options.money import Money
from tourganize.domain.requirements.schema import FieldKind, FieldSpec

__all__ = [
    "REASON_ABOVE_MAXIMUM",
    "REASON_BELOW_MINIMUM",
    "REASON_BLANK",
    "REASON_DATE_RANGE_REVERSED",
    "REASON_MESSAGE_KEYS",
    "REASON_MONEY_WITHOUT_CURRENCY",
    "REASON_NOT_AN_INTEGER",
    "REASON_NOT_A_BOOLEAN",
    "REASON_NOT_A_DATE",
    "REASON_NOT_A_DATE_RANGE",
    "REASON_NOT_A_DURATION",
    "REASON_NOT_A_NUMBER",
    "REASON_NOT_IN_ENUM",
    "REASON_NOT_MONEY",
    "REASON_NOT_TEXT",
    "VALIDATORS",
    "DateRange",
    "normalise",
]

#: The reason keys, as the Message Catalogue will see them. They are locale-neutral: the
#: dialogue re-asks in the traveller's language by looking the key up, never by echoing the
#: English detail that accompanies it.
REASON_BLANK: Final = "requirement.invalid.blank"
REASON_NOT_TEXT: Final = "requirement.invalid.not_text"
REASON_NOT_A_DATE: Final = "requirement.invalid.not_a_date"
REASON_NOT_A_DATE_RANGE: Final = "requirement.invalid.not_a_date_range"
REASON_DATE_RANGE_REVERSED: Final = "requirement.invalid.date_range_reversed"
REASON_NOT_AN_INTEGER: Final = "requirement.invalid.not_an_integer"
REASON_NOT_A_NUMBER: Final = "requirement.invalid.not_a_number"
REASON_NOT_MONEY: Final = "requirement.invalid.not_money"
REASON_MONEY_WITHOUT_CURRENCY: Final = "requirement.invalid.money_without_currency"
REASON_NOT_A_BOOLEAN: Final = "requirement.invalid.not_a_boolean"
REASON_NOT_A_DURATION: Final = "requirement.invalid.not_a_duration"
REASON_NOT_IN_ENUM: Final = "requirement.invalid.not_in_enum"
REASON_BELOW_MINIMUM: Final = "requirement.invalid.below_minimum"
REASON_ABOVE_MAXIMUM: Final = "requirement.invalid.above_maximum"

#: Every reason a value can be refused for. F10 phrases each of these; a validator that
#: invents a sixteenth key without adding it here is caught by a unit test.
REASON_MESSAGE_KEYS: Final = (
    REASON_BLANK,
    REASON_NOT_TEXT,
    REASON_NOT_A_DATE,
    REASON_NOT_A_DATE_RANGE,
    REASON_DATE_RANGE_REVERSED,
    REASON_NOT_AN_INTEGER,
    REASON_NOT_A_NUMBER,
    REASON_NOT_MONEY,
    REASON_MONEY_WITHOUT_CURRENCY,
    REASON_NOT_A_BOOLEAN,
    REASON_NOT_A_DURATION,
    REASON_NOT_IN_ENUM,
    REASON_BELOW_MINIMUM,
    REASON_ABOVE_MAXIMUM,
)

_CURRENCY_CODE: Final = re.compile(r"[A-Za-z]{3}")


@dataclass(frozen=True, slots=True)
class DateRange:
    """The normalised form of a ``date_range`` value: two resolved, ordered dates.

    Both ends are inclusive and both are already resolved — this type never holds "next
    month". ``end >= start`` is an invariant rather than a validation result, because the
    validator checks the ordering *before* constructing one, so it can name the field in the
    error the dialogue re-asks with.
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        for label, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, date) or isinstance(value, datetime):
                raise InvariantViolationError(
                    f"DateRange.{label} must be a datetime.date, got {value!r}"
                )
        if self.end < self.start:
            raise InvariantViolationError(
                f"DateRange must not end before it starts, got {self.start} to {self.end}"
            )

    @property
    def nights(self) -> int:
        """Whole days between the two ends — what a per-night or per-day price counts in."""
        return (self.end - self.start).days

    def __str__(self) -> str:
        return f"{self.start.isoformat()}/{self.end.isoformat()}"


def normalise(spec: FieldSpec, value: object) -> object:
    """Return ``value`` in the normalised form of ``spec``'s Field Kind, or raise.

    Idempotent: normalising an already-normalised value returns an equal value, which is what
    lets a Requirement Set be re-analysed on every turn without drifting.
    """
    validator = VALIDATORS.get(spec.field_kind)
    if validator is None:  # pragma: no cover - a test asserts the registry is exhaustive
        raise InvariantViolationError(
            f"{spec.name}: no validator is registered for field kind {spec.field_kind.value}"
        )
    return validator(spec, value)


def _text_value(spec: FieldSpec, value: object) -> str:
    """Trimmed, case-preserved text — a ``text`` value, and a ``place`` before F16/F17."""
    if not isinstance(value, str):
        _refuse(spec, REASON_NOT_TEXT, f"expected text, got {_shown(value)}")
    trimmed = value.strip()
    if not trimmed:
        _refuse(spec, REASON_BLANK, "expected a non-empty value")
    return trimmed


def _date_value(spec: FieldSpec, value: object) -> date:
    """One resolved calendar date: a ``date``, a ``datetime``, or an ISO-8601 spelling."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            _refuse(
                spec,
                REASON_NOT_A_DATE,
                f"expected a resolved ISO-8601 date such as 2026-10-23, got {_shown(value)}",
            )
    _refuse(spec, REASON_NOT_A_DATE, f"expected a date, got {_shown(value)}")


def _date_range(spec: FieldSpec, value: object) -> object:
    start, end = _date_range_ends(spec, value)
    if end < start:
        _refuse(
            spec,
            REASON_DATE_RANGE_REVERSED,
            f"the range ends before it starts: {start.isoformat()} to {end.isoformat()}",
        )
    return DateRange(start, end)


def _date_range_ends(spec: FieldSpec, value: object) -> tuple[date, date]:
    """Read the two ends out of any accepted spelling of a range."""
    if isinstance(value, DateRange):
        return value.start, value.end
    if isinstance(value, str):
        parts = value.split("/")
        if len(parts) != 2:
            _refuse(
                spec,
                REASON_NOT_A_DATE_RANGE,
                f"expected two ISO-8601 dates separated by '/', got {_shown(value)}",
            )
        return _date_value(spec, parts[0]), _date_value(spec, parts[1])
    if isinstance(value, Mapping):
        if "start" not in value or "end" not in value:
            _refuse(
                spec,
                REASON_NOT_A_DATE_RANGE,
                f"expected `start` and `end` keys, got {_shown(value)}",
            )
        return _date_value(spec, value["start"]), _date_value(spec, value["end"])
    if isinstance(value, Sequence):
        if len(value) != 2:
            _refuse(
                spec,
                REASON_NOT_A_DATE_RANGE,
                f"expected exactly two dates, got {len(value)}",
            )
        return _date_value(spec, value[0]), _date_value(spec, value[1])
    _refuse(spec, REASON_NOT_A_DATE_RANGE, f"expected a date range, got {_shown(value)}")


def _integer(spec: FieldSpec, value: object) -> object:
    whole = _whole_number(spec, value)
    _within_bounds(spec, whole)
    return whole


def _whole_number(spec: FieldSpec, value: object) -> int:
    # `isinstance(True, int)` is True, and `True` is not a count of guests.
    if isinstance(value, bool):
        _refuse(spec, REASON_NOT_AN_INTEGER, f"expected a whole number, got {_shown(value)}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            _refuse(spec, REASON_NOT_AN_INTEGER, f"expected a whole number, got {_shown(value)}")
    _refuse(spec, REASON_NOT_AN_INTEGER, f"expected a whole number, got {_shown(value)}")


def _score(spec: FieldSpec, value: object) -> object:
    """A rating on the scale its constraints declare — 0 to 10 for a review score, say."""
    number = _number(spec, value)
    _within_bounds(spec, number)
    return number


def _number(spec: FieldSpec, value: object) -> float:
    if isinstance(value, bool):
        _refuse(spec, REASON_NOT_A_NUMBER, f"expected a number, got {_shown(value)}")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            _refuse(spec, REASON_NOT_A_NUMBER, f"expected a number, got {_shown(value)}")
    _refuse(spec, REASON_NOT_A_NUMBER, f"expected a number, got {_shown(value)}")


def _money(spec: FieldSpec, value: object) -> object:
    """An exact amount **and** its currency, spelled ``<minor units> <ISO 4217 code>``.

    There is no default currency anywhere, and no second spelling: ``EUR 74000`` is refused
    for naming no amount where an amount belongs, not silently read backwards.
    """
    if isinstance(value, Money):
        return value
    if isinstance(value, Mapping):
        return _money_of(spec, value.get("amount_minor"), value.get("currency"))
    if isinstance(value, str):
        parts = value.split()
        if len(parts) == 1:
            _refuse(
                spec,
                REASON_MONEY_WITHOUT_CURRENCY,
                f"an amount must name its currency, got {_shown(value)}",
            )
        if len(parts) != 2:
            _refuse(
                spec,
                REASON_NOT_MONEY,
                f"expected `<minor units> <ISO code>`, got {_shown(value)}",
            )
        return _money_of(spec, parts[0], parts[1])
    if isinstance(value, int | float):
        _refuse(
            spec,
            REASON_MONEY_WITHOUT_CURRENCY,
            f"an amount must name its currency, got {_shown(value)}",
        )
    _refuse(spec, REASON_NOT_MONEY, f"expected an amount and a currency, got {_shown(value)}")


def _money_of(spec: FieldSpec, amount: object, code: object) -> Money:
    if code is None:
        _refuse(
            spec,
            REASON_MONEY_WITHOUT_CURRENCY,
            "an amount must name its currency, got no `currency`",
        )
    if not isinstance(code, str) or not _CURRENCY_CODE.fullmatch(code):
        _refuse(
            spec, REASON_NOT_MONEY, f"expected a three-letter ISO 4217 code, got {_shown(code)}"
        )
    minor = _whole_number(spec, amount)
    try:
        return Money(minor, code.upper())
    except InvariantViolationError as exc:  # pragma: no cover - the checks above precede it
        _refuse(spec, REASON_NOT_MONEY, str(exc))


def _boolean(spec: FieldSpec, value: object) -> object:
    """A real ``bool``, or the words ``true``/``false``. ``yes``, ``1`` and ``on`` are prose."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        spelled = value.strip().lower()
        if spelled == "true":
            return True
        if spelled == "false":
            return False
    _refuse(spec, REASON_NOT_A_BOOLEAN, f"expected true or false, got {_shown(value)}")


def _enum(spec: FieldSpec, value: object) -> object:
    chosen = _text_value(spec, value)
    if chosen not in spec.enum_values:
        _refuse(
            spec,
            REASON_NOT_IN_ENUM,
            f"expected one of {', '.join(spec.enum_values)}, got {_shown(value)}",
        )
    return chosen


def _duration(spec: FieldSpec, value: object) -> object:
    """A length of time in **minutes**, or a :class:`datetime.timedelta`.

    Minutes are the unit the declared bounds are read in too, so one number means one thing
    everywhere. ``2h`` is a spelling, and spellings are F08's to resolve.
    """
    minutes = _duration_minutes(spec, value)
    _within_bounds(spec, minutes)
    return timedelta(minutes=minutes)


def _duration_minutes(spec: FieldSpec, value: object) -> float:
    if isinstance(value, timedelta):
        return value.total_seconds() / 60
    if isinstance(value, bool):
        _refuse(spec, REASON_NOT_A_DURATION, f"expected a duration, got {_shown(value)}")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            _refuse(
                spec,
                REASON_NOT_A_DURATION,
                f"expected a number of minutes, got {_shown(value)}",
            )
    _refuse(spec, REASON_NOT_A_DURATION, f"expected a duration, got {_shown(value)}")


def _within_bounds(spec: FieldSpec, number: float) -> None:
    """Refuse a number outside the bounds its Field Spec declares."""
    low, high = spec.constraint("min"), spec.constraint("max")
    if isinstance(low, int | float) and number < low:
        _refuse(spec, REASON_BELOW_MINIMUM, f"{_plain(number)} is below the minimum {_plain(low)}")
    if isinstance(high, int | float) and number > high:
        _refuse(spec, REASON_ABOVE_MAXIMUM, f"{_plain(number)} is above the maximum {_plain(high)}")


def _plain(number: float) -> str:
    """Render a bound the way it was written: ``10`` rather than ``10.0``."""
    return str(int(number)) if float(number).is_integer() else str(number)


def _shown(value: object) -> str:
    """The offending value, truncated — a detail line is for a log, not for a payload."""
    shown = repr(value)
    return shown if len(shown) <= 60 else shown[:57] + "..."


def _refuse(spec: FieldSpec, reason_message_key: str, detail: str) -> NoReturn:
    raise RequirementValueError(spec.name, reason_message_key, detail)


#: One validator per Field Kind. Registration is a table rather than a chain of ``if``s for
#: the same reason the Component Catalog is: a new Field Kind is an entry here plus a
#: function, and no consumer changes.
VALIDATORS: Final[Mapping[FieldKind, Callable[[FieldSpec, object], object]]] = MappingProxyType(
    {
        FieldKind.DATE_RANGE: _date_range,
        FieldKind.DATE: _date_value,
        FieldKind.PLACE: _text_value,
        FieldKind.INTEGER: _integer,
        FieldKind.MONEY: _money,
        FieldKind.SCORE: _score,
        FieldKind.TEXT: _text_value,
        FieldKind.ENUM: _enum,
        FieldKind.BOOLEAN: _boolean,
        FieldKind.DURATION: _duration,
    }
)
