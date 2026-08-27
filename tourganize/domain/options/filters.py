"""Optional filters: what a traveller's optional answers mean about a Plan Option.

An optional Field Spec is *a filter* — F03 says so in one word, ``optional``: "a filter, asked
opportunistically, never blocking". This module is the other half of that sentence. It turns
the optional values a Requirement Set holds into :class:`OptionFilter`s, and an
:class:`OptionFilter` into a **note** on a Plan Option that fails it.

Two rules shape the whole module.

**Soft, not exclusive.** A filter never removes an option here; it *annotates* one. A traveller
who says "under €150" is still shown the €160 room, marked as exceeding the ceiling, rather
than an empty slate — and the decision to discard instead is configuration
(``TOURGANIZE_OPTION_FILTER_STRICT``) applied by the Planning Service, not a rule this module
knows about. It is the same shape F16's Feasibility findings take: advisory, and visible.

**Declared, not coded.** *Which* fact a filter reads, and in which direction, is data — two
entries in the Field Spec's ``constraints`` bag, which F03 left open for exactly this ("a key
this release has never heard of is what a *newer* Field Kind looks like from here")::

    - name: budget_ceiling
      field_kind: money
      obligation: optional
      prompt_message_key: ask.alpha.budget_ceiling
      constraints: {filters: price, comparison: at_most}

That is what keeps a fourth Component Kind free of Python. A schema that declares no
``filters`` on an optional field simply has a field nothing demotes by — which is the honest
default, because most optional fields are preferences a provider cannot be measured against.

A note is a **field name**, never a sentence: ``("budget_ceiling",)`` says which declared
filter the option failed, and the Message Catalogue phrases it from that field's own message
key. The domain holds no prose, and a slate the traveller reads in Hebrew could not hold one
anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_text
from tourganize.domain.options.money import Money
from tourganize.domain.options.option import PlanOption
from tourganize.domain.requirements.schema import Obligation, RequirementSchema
from tourganize.domain.requirements.validation import DateRange
from tourganize.domain.requirements.values import RequirementSet

__all__ = [
    "COMPARISON_KEY",
    "FILTERS_KEY",
    "PRICE_FACT",
    "Comparison",
    "OptionFilter",
    "filter_notes_for",
    "filters_of",
]

#: The constraint key naming the Plan Option fact a filter reads.
FILTERS_KEY: Final = "filters"

#: The constraint key naming the direction of the comparison; see :class:`Comparison`.
COMPARISON_KEY: Final = "comparison"

#: The reserved value of :data:`FILTERS_KEY` that means the option's ``price`` rather than one
#: of its declared facts. A price is the one thing every Plan Option carries in its own right,
#: so it is the one fact name that cannot simply be looked up in ``facts``.
PRICE_FACT: Final = "price"


class Comparison(Enum):
    """What a filter asks of an option's fact. Three values, and no fourth is planned.

    ``at_most`` and ``at_least`` are bounds — a ceiling on a price, a floor on a review score.
    ``equals`` is an exact match, which is what an ``enum`` or a ``boolean`` preference means.
    Anything richer belongs to the source that holds the data, not to a declared filter.
    """

    AT_MOST = "at_most"
    AT_LEAST = "at_least"
    EQUALS = "equals"


@dataclass(frozen=True, slots=True)
class OptionFilter:
    """One optional requirement, read as a demand on a Plan Option.

    ``field_name`` is what a failing option is noted with and what the Message Catalogue
    phrases; ``fact_name`` is what is read off the option, and it is the *provider's* name for
    the same quantity — a traveller's ``budget_ceiling`` is an option's ``price``.
    """

    field_name: str
    fact_name: str
    comparison: Comparison
    value: object

    def __post_init__(self) -> None:
        require_text(self.field_name, "OptionFilter.field_name")
        require_text(self.fact_name, "OptionFilter.fact_name")
        if not isinstance(self.comparison, Comparison):
            raise InvariantViolationError(
                f"{self.field_name}: comparison must be a Comparison, got {self.comparison!r}"
            )

    def is_satisfied_by(self, option: PlanOption) -> bool:
        """Whether ``option`` meets this filter.

        An option that does not carry the fact at all **passes**. Real providers return sparse,
        inconsistent records (D9's own stated risk), and demoting every option that happens not
        to publish a review score would turn a missing field into a judgement about the option.
        A filter this release cannot compare — two currencies, a shape that is not ordered —
        passes for the same reason: silence is not a failure.
        """
        held = _fact_of(option, self.fact_name)
        if held is None:
            return True
        return _compare(self.comparison, held, self.value)


def filters_of(schema: RequirementSchema, requirements: RequirementSet) -> tuple[OptionFilter, ...]:
    """Every declared filter the traveller has actually answered, in declaration order.

    An optional field with no value is not a filter — it is a question F05 may still ask. An
    optional field that declares no ``filters`` constraint is not one either: it is a
    preference nothing on an option can be measured against, and inventing a comparison for it
    would be inventing a rule the schema never stated.
    """
    found: list[OptionFilter] = []
    for spec in schema.fields:
        if spec.obligation is not Obligation.OPTIONAL:
            continue
        fact_name = spec.constraint(FILTERS_KEY)
        if not isinstance(fact_name, str) or not fact_name:
            continue
        value = requirements.value_of(spec.name)
        if value is None:
            continue
        comparison = _comparison_of(spec.constraint(COMPARISON_KEY))
        if comparison is None:
            continue
        found.append(OptionFilter(spec.name, fact_name, comparison, value))
    return tuple(found)


def filter_notes_for(option: PlanOption, filters: Sequence[OptionFilter]) -> tuple[str, ...]:
    """The field names of the filters ``option`` fails, in the order they were declared."""
    return tuple(item.field_name for item in filters if not item.is_satisfied_by(option))


def _comparison_of(declared: object) -> Comparison | None:
    """Read the declared comparison, defaulting to ``at_most``.

    A missing ``comparison`` is read as a ceiling because that is what a stated preference
    almost always is — a budget, a number of stops, a walking distance. An *unrecognised* one
    is dropped rather than guessed at: it is what a schema written for a later release looks
    like from here, and the same reasoning F03 applies to an unknown constraint key.
    """
    if declared is None:
        return Comparison.AT_MOST
    for candidate in Comparison:
        if declared == candidate.value:
            return candidate
    return None


def _fact_of(option: PlanOption, fact_name: str) -> object | None:
    """The quantity ``fact_name`` names on ``option``: its price, or one of its facts."""
    if fact_name == PRICE_FACT:
        return option.price
    return option.facts.get(fact_name)


def _compare(comparison: Comparison, held: object, wanted: object) -> bool:
    """Apply one comparison, answering ``True`` for anything that cannot be compared."""
    if comparison is Comparison.EQUALS:
        return bool(held == wanted)
    ordered = _ordering_key(held, wanted)
    if ordered is None:
        return True
    left, right = ordered
    return left <= right if comparison is Comparison.AT_MOST else left >= right


def _ordering_key(held: object, wanted: object) -> tuple[float, float] | None:
    """Reduce two comparable values to two numbers, or answer ``None`` for a pair that is not.

    Written out per type rather than left to ``<``: two ``Money`` amounts in different
    currencies raise rather than compare, because there is no exchange rate in the domain, and
    a filter is not the place to invent one.
    """
    if isinstance(held, Money) and isinstance(wanted, Money):
        if not held.same_currency_as(wanted):
            return None
        return float(held.amount_minor), float(wanted.amount_minor)
    if isinstance(held, bool) or isinstance(wanted, bool):
        return None
    if isinstance(held, int | float) and isinstance(wanted, int | float):
        return float(held), float(wanted)
    if isinstance(held, timedelta) and isinstance(wanted, timedelta):
        return held.total_seconds(), wanted.total_seconds()
    if isinstance(held, DateRange) or isinstance(wanted, DateRange):
        return None
    if isinstance(held, date) and isinstance(wanted, date):
        return float(held.toordinal()), float(wanted.toordinal())
    return None
