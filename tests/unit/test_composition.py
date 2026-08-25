"""The Composition Root selects adapters from Settings — and is the only place that does."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tourganize.adapters.clock.system import SystemClock
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.application.composition import PENDING_PORTS, build_container
from tourganize.platform.settings import Settings
from tourganize.ports.platform import Clock, TelemetrySink

SettingsFactory = Callable[..., Settings]


def test_the_default_container_wires_the_two_platform_ports(
    settings_factory: SettingsFactory,
) -> None:
    container = build_container(settings_factory())

    assert isinstance(container.clock, SystemClock)
    assert isinstance(container.telemetry_sink, JsonlTelemetrySink)
    assert isinstance(container.clock, Clock)
    assert isinstance(container.telemetry_sink, TelemetrySink)


def test_the_telemetry_sink_is_chosen_by_configuration(
    settings_factory: SettingsFactory,
) -> None:
    null_container = build_container(settings_factory(TOURGANIZE_TELEMETRY_SINK="null"))
    jsonl_container = build_container(settings_factory(TOURGANIZE_TELEMETRY_SINK="jsonl"))

    assert isinstance(null_container.telemetry_sink, NullTelemetrySink)
    assert isinstance(jsonl_container.telemetry_sink, JsonlTelemetrySink)


def test_the_jsonl_sink_writes_where_settings_say(
    settings_factory: SettingsFactory, tmp_path: Path
) -> None:
    target = tmp_path / "elsewhere" / "events.jsonl"
    container = build_container(settings_factory(TOURGANIZE_TELEMETRY_PATH=str(target)))

    sink = container.telemetry_sink
    assert isinstance(sink, JsonlTelemetrySink)
    assert sink.path == target


def test_the_container_reports_its_adapters_by_name(settings_factory: SettingsFactory) -> None:
    container = build_container(settings_factory())

    assert dict(container.adapters()) == {
        "Clock": "SystemClock",
        "TelemetrySink": "JsonlTelemetrySink",
    }


def test_ports_awaiting_a_feature_are_declared_not_forgotten() -> None:
    assert PENDING_PORTS["ComponentCatalog"] == "F02"
    assert PENDING_PORTS["LlmGateway"] == "F08"
    assert "PresentationSurface" in PENDING_PORTS


def test_the_container_is_frozen(settings_factory: SettingsFactory) -> None:
    container = build_container(settings_factory())
    try:
        container.clock = SystemClock()  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Container must be immutable")
