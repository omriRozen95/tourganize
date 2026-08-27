"""``doctor``'s report: real probes, redacted settings, an honest verdict."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import write_catalog

from tourganize.adapters.catalog.priority import FixedOrderPolicy
from tourganize.application.composition import build_container
from tourganize.application.diagnostics import run_diagnostics
from tourganize.platform.settings import Settings

SettingsFactory = Callable[..., Settings]


def test_a_healthy_installation_passes_every_check(
    settings_factory: SettingsFactory, catalog_file: Path, keyword_files: Path
) -> None:
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    assert report.ok
    assert {check.name for check in report.checks} == {
        "config_dir",
        "data_dir",
        "clock",
        "telemetry_sink",
        "component_catalog",
        "priority_policy",
        "turn_interpreter",
        "option_sources",
    }
    assert "doctor: ok" in report.render()
    assert catalog_file.exists()
    assert keyword_files.is_dir()


def test_the_data_dir_is_probed_by_writing(settings_factory: SettingsFactory) -> None:
    settings = settings_factory()
    run_diagnostics(build_container(settings), version="9.9.9")

    assert settings.data_dir.is_dir()
    assert not (settings.data_dir / ".tourganize-doctor").exists()


def test_the_probe_event_reaches_the_sink(settings_factory: SettingsFactory) -> None:
    settings = settings_factory()
    run_diagnostics(build_container(settings), version="9.9.9")

    assert settings.telemetry_path is not None
    assert "doctor_probe" in settings.telemetry_path.read_text(encoding="utf-8")


def test_an_unwritable_data_dir_fails_doctor_rather_than_a_later_write(
    settings_factory: SettingsFactory, tmp_path: Path
) -> None:
    if os.geteuid() == 0:  # pragma: no cover - root ignores the mode bits
        pytest.skip("root can write to a read-only directory")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        settings = settings_factory(TOURGANIZE_DATA_DIR=str(blocked / "state"))
        report = run_diagnostics(build_container(settings), version="9.9.9")

        assert not report.ok
        failures = [check for check in report.checks if not check.ok]
        assert "data_dir" in [check.name for check in failures]
        assert "telemetry_sink" in [check.name for check in failures]
        assert "doctor: FAILED" in report.render()
    finally:
        blocked.chmod(stat.S_IRWXU)


def test_a_config_dir_that_is_not_a_directory_fails(
    settings_factory: SettingsFactory, tmp_path: Path
) -> None:
    settings = settings_factory()
    stray = tmp_path / "config"
    stray.write_text("", encoding="utf-8")

    report = run_diagnostics(build_container(settings), version="9.9.9")

    assert not report.ok
    assert any(check.name == "config_dir" and not check.ok for check in report.checks)


def test_a_catalog_that_cannot_be_loaded_fails_doctor(
    settings_factory: SettingsFactory,
) -> None:
    """From F02 on, an installation with no Component Catalog cannot plan anything."""
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    catalog = next(check for check in report.checks if check.name == "component_catalog")
    assert not catalog.ok
    assert "does not exist" in catalog.detail
    assert not report.ok


def test_an_invalid_catalog_fails_doctor_with_every_problem_named(
    settings_factory: SettingsFactory, tmp_path: Path
) -> None:
    write_catalog(
        tmp_path / "config",
        """\
version: 1
kinds:
  - kind_key: alpha
    message_key: component.alpha
    priority_weight: 1
    schema_key: alpha.v1
    requires_outcome_of: [beta]
  - kind_key: alpha
    message_key: component.alpha
    priority_weight: 2
    schema_key: alpha.v1
""",
    )

    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    catalog = next(check for check in report.checks if check.name == "component_catalog")
    assert not catalog.ok
    assert "duplicate kind_key 'alpha'" in catalog.detail
    assert "which no kind declares" in catalog.detail


def test_an_empty_catalog_fails_doctor(settings_factory: SettingsFactory, tmp_path: Path) -> None:
    write_catalog(tmp_path / "config", "version: 1\nkinds: []\n")

    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    catalog = next(check for check in report.checks if check.name == "component_catalog")
    assert not catalog.ok
    assert "declares no Component Kinds" in catalog.detail


def test_the_catalog_check_counts_declared_and_enabled_kinds(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    catalog = next(check for check in report.checks if check.name == "component_catalog")
    assert catalog.ok
    assert catalog.detail.startswith("3 Component Kinds (2 enabled)")
    assert str(catalog_file) in catalog.detail


def test_the_priority_policy_check_reports_the_order_it_would_plan_in(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    """A real probe: the Agenda is built, so a policy that misbehaves fails here, not mid-turn."""
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    policy = next(check for check in report.checks if check.name == "priority_policy")
    assert policy.ok
    assert "WeightedCatalogPolicy (weighted)" in policy.detail
    # The catalog fixture declares alpha (300) and beta (200); gamma is disabled.
    assert policy.detail.endswith("would plan alpha, beta")
    assert catalog_file.exists()


def test_the_configured_policy_is_the_one_doctor_probes(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    settings = settings_factory(TOURGANIZE_PRIORITY_POLICY="fixed")

    report = run_diagnostics(build_container(settings), version="9.9.9")

    policy = next(check for check in report.checks if check.name == "priority_policy")
    assert "FixedOrderPolicy (fixed)" in policy.detail
    assert report.adapters["PriorityPolicy"] == "FixedOrderPolicy"
    assert catalog_file.exists()


def test_the_probe_uses_the_resolved_agenda_failure_skip(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    """The probe is built the way a turn will be, ``failure_skip`` included, so an impossible
    value is a failing check rather than an exception out of a health report. ``Settings`` will
    not resolve one, which is why the test has to write it in by hand."""
    container = build_container(settings_factory())
    impossible = replace(container, settings=replace(container.settings, agenda_failure_skip=0))

    report = run_diagnostics(impossible, version="9.9.9")

    policy = next(check for check in report.checks if check.name == "priority_policy")
    assert not policy.ok
    assert "failure_skip must be at least 1" in policy.detail
    assert catalog_file.exists()


def test_a_policy_that_breaks_its_contract_fails_doctor(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    """The reason the check is a real probe: a replacement policy is found out here, before a
    conversation starts, rather than halfway through one."""
    container = build_container(settings_factory())
    broken = replace(container, priority_policy=FixedOrderPolicy(("nowhere",), verbatim=True))

    report = run_diagnostics(broken, version="9.9.9")

    policy = next(check for check in report.checks if check.name == "priority_policy")
    assert not policy.ok
    assert "nowhere" in policy.detail
    assert not report.ok
    assert catalog_file.exists()


def test_a_catalog_that_cannot_be_loaded_is_not_reported_twice(
    settings_factory: SettingsFactory,
) -> None:
    """The catalog check already says what is wrong; the policy has nothing to order, not a
    second opinion about the file."""
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    policy = next(check for check in report.checks if check.name == "priority_policy")
    assert policy.ok
    assert policy.detail.endswith("nothing to order yet")
    assert not report.ok  # the catalog check still fails, so doctor still fails


def test_no_secret_value_appears_in_the_rendered_report(
    settings_factory: SettingsFactory,
) -> None:
    leak = "leak-me-not"
    settings = settings_factory(TOURGANIZE_PROVIDER_API_KEY=leak)

    rendered = run_diagnostics(build_container(settings), version="9.9.9").render()

    assert leak not in rendered
    assert "TOURGANIZE_PROVIDER_API_KEY=***" in rendered


def test_the_report_lists_unrecognised_keys_and_pending_ports(
    settings_factory: SettingsFactory,
) -> None:
    report = run_diagnostics(
        build_container(settings_factory()),
        version="9.9.9",
        unrecognised=("TOURGANIZE_LGO_LEVEL",),
    )
    rendered = report.render()

    assert "TOURGANIZE_LGO_LEVEL" in rendered
    assert "LlmGateway (F08)" in rendered
    assert "ComponentCatalog" not in report.pending_ports
    assert rendered.startswith("tourganize 9.9.9")


def test_the_turn_interpreter_is_probed_by_reading_a_turn(
    settings_factory: SettingsFactory, catalog_file: Path, keyword_files: Path
) -> None:
    """A real probe: the keyword interpreter reads its phrase tables lazily, on the first turn."""
    del catalog_file, keyword_files
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    check = next(item for item in report.checks if item.name == "turn_interpreter")
    assert check.ok
    assert "KeywordTurnInterpreter" in check.detail


def test_missing_phrase_tables_fail_doctor_rather_than_the_first_turn(
    settings_factory: SettingsFactory, catalog_file: Path
) -> None:
    """No `config/interpretation/` is a misconfigured install, and `doctor` is where it shows."""
    del catalog_file
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    check = next(item for item in report.checks if item.name == "turn_interpreter")
    assert not check.ok
    assert "interpretation" in check.detail
    assert not report.ok
