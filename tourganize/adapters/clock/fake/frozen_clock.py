"""``FrozenClock`` — a controllable ``Clock`` for tests and replay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final, final

from tourganize.platform.errors import ContractViolationError

__all__ = ["DEFAULT_MOMENT", "FrozenClock"]

#: An arbitrary but fixed moment, so that a test that does not care about time still gets a
#: stable value in snapshots and telemetry.
DEFAULT_MOMENT: Final = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@final
class FrozenClock:
    """A clock that only moves when told to.

    With ``step`` set, each :meth:`now` advances by that amount afterwards, which is enough
    to give successive turns distinct, predictable timestamps without any test arithmetic.
    """

    def __init__(self, moment: datetime | None = None, *, step: timedelta = timedelta(0)) -> None:
        self._moment = _require_aware(moment if moment is not None else DEFAULT_MOMENT)
        self._step = step

    def now(self) -> datetime:
        current = self._moment
        self._moment = current + self._step
        return current

    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward (or back, with a negative delta) and return the new time."""
        self._moment = self._moment + delta
        return self._moment

    def set_to(self, moment: datetime) -> None:
        """Pin the clock to ``moment``."""
        self._moment = _require_aware(moment)


def _require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ContractViolationError(f"a Clock moment must be timezone-aware, got {moment!r}")
    return moment
