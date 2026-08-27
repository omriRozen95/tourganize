"""``CheapestFirstRanking`` — the order a slate is presented in, and why it is that order.

Small, explicit and replaceable, exactly as the ``PriorityPolicy`` is. Four keys, applied in
this order:

1. **The filters the traveller stated, first.** An option that fails one is demoted below every
   option that fails none. This is what "soft filtering" *means*: a €160 room is still shown to
   someone who said "under €150", but it is shown below the ones that are under €150, marked.
   Ranking by price first would make demotion an empty word — a cheap option failing a review
   score filter would still lead the slate — so filter satisfaction outranks price here even
   though the feature file lists price first. That is the one place this implementation departs
   from the spec's sketch, and the feature file records it.
2. **Price ascending**, within a currency. Two currencies are ordered by their code and never
   converted: there is no exchange rate in the domain, and a ranking is not the place to invent
   one. In practice a query is answered in one currency, so this only decides how two
   *groups* sit relative to each other.
3. **Source order**, so that a preferred provider's option wins a tie with a fallback's.
4. **The option's own id**, so that the answer is total and therefore identical on two machines.

An unpriced option sorts after every priced one: nothing is known about what it costs, and
leading a slate with it would read as a recommendation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, final

from tourganize.domain.options import PlanOption
from tourganize.domain.options.query import OptionQuery

__all__ = ["CHEAPEST_FIRST_RANKING_ID", "CheapestFirstRanking"]

#: What this ranking calls itself in telemetry, so a recorded slate can be explained later.
CHEAPEST_FIRST_RANKING_ID: Final = "cheapest_first"

#: Sorts after every currency code, so an unpriced option lands at the end of the slate.
_NO_CURRENCY: Final = "￿"


@final
class CheapestFirstRanking:
    """Filters satisfied first, then price ascending, then source order, then the id."""

    def __init__(self, source_order: Sequence[str] = ()) -> None:
        self._source_rank: Mapping[str, int] = {
            source_id: rank for rank, source_id in enumerate(source_order)
        }

    @property
    def ranking_id(self) -> str:
        return CHEAPEST_FIRST_RANKING_ID

    def order(self, options: Sequence[PlanOption], query: OptionQuery) -> Sequence[PlanOption]:
        """Return exactly ``options``, cheapest-and-unfiltered first."""
        del query  # this ranking reads the options alone; a replacement may read the query.
        return sorted(options, key=self._key)

    def _key(self, option: PlanOption) -> tuple[int, str, int, int, str]:
        price = option.price
        return (
            len(option.filter_notes),
            _NO_CURRENCY if price is None else price.currency,
            0 if price is None else price.amount_minor,
            self._source_rank.get(option.provenance.source_id, len(self._source_rank)),
            option.option_id,
        )
