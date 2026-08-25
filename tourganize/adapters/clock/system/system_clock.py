"""``SystemClock`` — the wall clock, in UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import final

__all__ = ["SystemClock"]


@final
class SystemClock:
    """Reads the system clock. Always returns an aware UTC datetime.

    UTC is not a preference: stored sessions (F12) and exported itineraries (F13/F14) are
    localised at the edge, so everything inside the application stays on one timeline.
    """

    def now(self) -> datetime:
        return datetime.now(tz=UTC)
