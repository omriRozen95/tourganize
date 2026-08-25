"""The two smallest ports: ``Clock`` and ``TelemetrySink``.

Both exist so that nothing in the application reads the wall clock or writes an
observability record directly. ``TelemetryEvent`` is deliberately generic — ``kind`` plus a
free ``fields`` mapping — so F08 can define the Turn Ledger, and F20/F21 can extend it with
server-side numbers, without changing this port.

Adapters: ``tourganize.adapters.clock.system`` / ``.fake`` and
``tourganize.adapters.telemetry.jsonl`` / ``.null``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "TelemetryEvent", "TelemetrySink"]


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One structured observability record.

    ``occurred_at`` is timezone-aware and comes from a :class:`Clock` — it is a required
    argument on purpose, so that a recorded conversation replays with the timestamps it was
    captured with instead of the ones it is replayed at.
    """

    kind: str
    session_id: str | None
    occurred_at: datetime
    fields: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class Clock(Protocol):
    """The only source of "now" in the application."""

    def now(self) -> datetime:
        """Return the current moment as a timezone-aware datetime."""
        ...


@runtime_checkable
class TelemetrySink(Protocol):
    """Receives Turn Ledger entries and other structured events.

    A sink never raises: telemetry must not be able to end a planning session. An adapter
    that cannot write degrades to a no-op after warning once.
    """

    def record(self, event: TelemetryEvent) -> None:
        """Record one event. Must not raise."""
        ...
