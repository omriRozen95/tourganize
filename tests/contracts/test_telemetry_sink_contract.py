"""The ``TelemetrySink`` contract, run against every adapter of the port.

The promise a sink makes is narrow and absolute: it accepts any event, and it never raises.
Telemetry must not be able to end a planning session.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.ports.platform import TelemetryEvent, TelemetrySink

SinkBuilder = Callable[[Path], TelemetrySink]

SINKS: list[tuple[str, SinkBuilder]] = [
    ("NullTelemetrySink", lambda _path: NullTelemetrySink()),
    ("JsonlTelemetrySink", lambda path: JsonlTelemetrySink(path / "telemetry.jsonl")),
]
SINK_IDS = [name for name, _ in SINKS]


def _events() -> Iterator[TelemetryEvent]:
    clock = FrozenClock(step=timedelta(seconds=1))
    yield TelemetryEvent(kind="turn", session_id="session-1", occurred_at=clock.now())
    yield TelemetryEvent(
        kind="llm_call",
        session_id=None,
        occurred_at=clock.now(),
        fields={"tokens": 412, "locale": "he", "city": "פריז"},
    )
    yield TelemetryEvent(
        kind="odd_payload",
        session_id="session-1",
        occurred_at=clock.now(),
        fields={"unserialisable": object()},
    )


@pytest.mark.parametrize(("name", "build"), SINKS, ids=SINK_IDS)
def test_a_sink_accepts_every_event_and_never_raises(
    name: str, build: SinkBuilder, tmp_path: Path
) -> None:
    sink = build(tmp_path)

    for event in _events():
        sink.record(event)


@pytest.mark.parametrize(("name", "build"), SINKS, ids=SINK_IDS)
def test_the_port_is_satisfied_structurally(name: str, build: SinkBuilder, tmp_path: Path) -> None:
    assert isinstance(build(tmp_path), TelemetrySink)


@pytest.mark.parametrize(("name", "build"), SINKS, ids=SINK_IDS)
def test_a_sink_survives_a_destination_it_cannot_write(
    name: str, build: SinkBuilder, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    sink = build(blocker / "nested")

    sink.record(TelemetryEvent(kind="turn", session_id=None, occurred_at=DEFAULT_MOMENT))


def test_the_jsonl_sink_writes_one_parseable_object_per_event(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "telemetry.jsonl"
    sink = JsonlTelemetrySink(path)

    for event in _events():
        sink.record(event)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]

    assert len(records) == 3
    assert [record["kind"] for record in records] == ["turn", "llm_call", "odd_payload"]
    assert records[0]["session_id"] == "session-1"
    assert records[0]["occurred_at"] == DEFAULT_MOMENT.isoformat()
    assert records[1]["session_id"] is None
    assert records[1]["fields"]["tokens"] == 412
    assert records[1]["fields"]["city"] == "פריז"
    assert isinstance(records[2]["fields"]["unserialisable"], str)
    assert not sink.degraded


def test_the_jsonl_sink_appends_rather_than_truncating(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    event = TelemetryEvent(kind="turn", session_id=None, occurred_at=DEFAULT_MOMENT)

    JsonlTelemetrySink(path).record(event)
    JsonlTelemetrySink(path).record(event)

    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_a_write_failure_degrades_to_a_warning_once(tmp_path: Path) -> None:
    import logging

    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    logger = logging.getLogger("test.telemetry.degrade")
    records: list[logging.LogRecord] = []

    class Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.addHandler(Collector())
    logger.setLevel(logging.WARNING)
    sink = JsonlTelemetrySink(blocker / "nested" / "telemetry.jsonl", logger=logger)

    for event in _events():
        sink.record(event)

    assert sink.degraded
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
