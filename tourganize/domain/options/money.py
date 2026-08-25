"""``Money`` — an amount in minor units and an ISO 4217 currency. Never a float.

Prices arrive from providers, are compared, ranked and summed, and end up in an exported
document. A binary float cannot do that without eventually being a cent out, so the domain
holds minor units (agorot, cents, pence) as an ``int`` and refuses anything else at
construction. Formatting for a locale is presentation's job, not this type's.

Currencies are never mixed implicitly: F13 sums per currency, and there is no exchange rate
anywhere in the domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from tourganize.domain.errors import InvariantViolationError

__all__ = ["Money"]

_CURRENCY_PATTERN: Final = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True, slots=True, order=True)
class Money:
    """An exact amount of one currency, in that currency's minor units."""

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        # `type(...) is not int` rather than `isinstance`: a bool *is* an int to isinstance,
        # and `True` is not a price.
        if type(self.amount_minor) is not int:
            raise InvariantViolationError(
                "Money.amount_minor must be an integer number of minor units "
                f"(cents, agorot), got {self.amount_minor!r}"
            )
        if type(self.currency) is not str or not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise InvariantViolationError(
                f"Money.currency must be a three-letter upper-case ISO 4217 code, "
                f"got {self.currency!r}"
            )

    def same_currency_as(self, other: Money) -> bool:
        """True when two amounts may be compared or summed at all."""
        return self.currency == other.currency
