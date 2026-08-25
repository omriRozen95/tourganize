"""The ``TelemetrySink`` fake: records nothing, never fails."""

from __future__ import annotations

from tourganize.adapters.telemetry.null.null_sink import NullTelemetrySink

__all__ = ["NullTelemetrySink"]
