"""``NullTelemetrySink`` — the no-op sink."""

from __future__ import annotations

from typing import final

from tourganize.ports.platform import TelemetryEvent

__all__ = ["NullTelemetrySink"]


@final
class NullTelemetrySink:
    """Accepts and discards every event.

    Selected by ``TOURGANIZE_TELEMETRY_SINK=null`` and used by tests that do not assert on
    telemetry, so no test has to own a temporary file to stay quiet.
    """

    def record(self, event: TelemetryEvent) -> None:
        return None

    @property
    def degraded(self) -> bool:
        """A sink that writes nowhere has nothing that can fail."""
        return False
