"""``JsonlTelemetrySink`` — one JSON object per line, appended to a file.

JSON Lines is chosen because it is append-only (no read-modify-write, so a crash costs at
most the last line), greppable, and directly loadable by the parity report in F21.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import final

from tourganize.platform.logging import get_logger
from tourganize.ports.platform import TelemetryEvent

__all__ = ["JsonlTelemetrySink"]


@final
class JsonlTelemetrySink:
    """Append events to ``path``, degrading to a no-op if the file cannot be written.

    Telemetry may never end a planning session, so the first failure is logged once at
    WARNING and the sink then stays quiet for the rest of the process.
    """

    def __init__(self, path: Path, *, logger: logging.Logger | None = None) -> None:
        self._path = path
        self._logger = logger if logger is not None else get_logger("telemetry")
        self._degraded = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def degraded(self) -> bool:
        """True once a write has failed and the sink has stopped trying."""
        return self._degraded

    def record(self, event: TelemetryEvent) -> None:
        if self._degraded:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(_payload(event), ensure_ascii=False, default=str)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except (OSError, TypeError, ValueError) as exc:
            self._degrade(exc)

    def _degrade(self, exc: Exception) -> None:
        self._degraded = True
        self._logger.warning(
            "telemetry sink disabled after a write failure: %s", exc, extra={"kind": "telemetry"}
        )


def _payload(event: TelemetryEvent) -> dict[str, object]:
    return {
        "kind": event.kind,
        "session_id": event.session_id,
        "occurred_at": event.occurred_at.isoformat(),
        "fields": dict(event.fields),
    }
