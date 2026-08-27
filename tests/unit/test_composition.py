"""The Composition Root selects adapters from Settings — and is the only place that does."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tourganize.adapters.catalog.priority import FixedOrderPolicy, WeightedCatalogPolicy
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.system import SystemClock
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.application.composition import PENDING_PORTS, build_container
from tourganize.platform.settings import Settings
from tourganize.ports.catalog import ComponentCatalog, PriorityPolicy
from tourganize.ports.platform import Clock, TelemetrySink

SettingsFactory = Callable[..., Settings]


def test_the_default_container_wires_every_port_that_has_an_adapter(
    settings_factory: SettingsFactory,
) -> None:
    container = build_container(settings_factory())

    assert isinstance(container.clock, SystemClock)
    assert isinstance(container.telemetry_sink, JsonlTelemetrySink)
    assert isinstance(container.component_catalog, YamlComponentCatalog)
    assert isinstance(container.priority_policy, WeightedCatalogPolicy)
    assert isinstance(container.clock, Clock)
    assert isinstance(container.telemetry_sink, TelemetrySink)
    assert isinstance(container.component_catalog, ComponentCatalog)
    assert isinstance(container.priority_policy, PriorityPolicy)


def test_the_catalog_is_wired_where_settings_point_and_read_no_earlier(
    settings_factory: SettingsFactory, tmp_path: Path
) -> None:
    """Wiring must not read the file: a broken catalog is a `doctor` finding, not a crash."""
    missing = tmp_path / "elsewhere" / "components.yaml"
    container = build_container(settings_factory(TOURGANIZE_CATALOG_PATH=str(missing)))

    catalog = container.component_catalog
    assert isinstance(catalog, YamlComponentCatalog)
    assert catalog.path == missing


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


def test_the_priority_policy_is_chosen_by_configuration(
    settings_factory: SettingsFactory,
) -> None:
    """The whole of ``TOURGANIZE_PRIORITY_POLICY``: one value, two policies, no other change."""
    weighted = build_container(settings_factory(TOURGANIZE_PRIORITY_POLICY="weighted"))
    fixed = build_container(settings_factory(TOURGANIZE_PRIORITY_POLICY="fixed"))

    assert isinstance(weighted.priority_policy, WeightedCatalogPolicy)
    assert isinstance(fixed.priority_policy, FixedOrderPolicy)


def test_a_policy_names_itself_with_the_value_that_selected_it(
    settings_factory: SettingsFactory,
) -> None:
    """``TOURGANIZE_PRIORITY_POLICY`` and ``policy_id`` are one vocabulary, not two."""
    for name in ("weighted", "fixed"):
        container = build_container(settings_factory(TOURGANIZE_PRIORITY_POLICY=name))

        assert container.settings.priority_policy == name
        assert container.priority_policy.policy_id == name


def test_the_fixed_policy_is_built_with_no_order_of_its_own(
    settings_factory: SettingsFactory,
) -> None:
    """Planning them in the order the file lists them needs no second configuration key."""
    policy = build_container(settings_factory(TOURGANIZE_PRIORITY_POLICY="fixed")).priority_policy

    assert isinstance(policy, FixedOrderPolicy)
    assert policy.kind_keys == ()


def test_the_container_reports_its_adapters_by_name(settings_factory: SettingsFactory) -> None:
    container = build_container(settings_factory())

    assert dict(container.adapters()) == {
        "Clock": "SystemClock",
        "TelemetrySink": "JsonlTelemetrySink",
        "ComponentCatalog": "YamlComponentCatalog",
        "PriorityPolicy": "WeightedCatalogPolicy",
    }


def test_ports_awaiting_a_feature_are_declared_not_forgotten() -> None:
    assert PENDING_PORTS["TurnInterpreter"] == "F05"
    assert PENDING_PORTS["LlmGateway"] == "F08"
    assert "PresentationSurface" in PENDING_PORTS


def test_a_wired_port_is_removed_from_the_pending_list() -> None:
    """F02 wires the Component Catalog and F04 the Priority Policy; neither is missing now."""
    assert "ComponentCatalog" not in PENDING_PORTS
    assert "PriorityPolicy" not in PENDING_PORTS


def test_the_container_is_frozen(settings_factory: SettingsFactory) -> None:
    container = build_container(settings_factory())
    try:
        container.clock = SystemClock()  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Container must be immutable")
