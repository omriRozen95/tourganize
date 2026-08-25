"""``doctor``'s report: real probes, redacted settings, an honest verdict."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

from tourganize.application.composition import build_container
from tourganize.application.diagnostics import run_diagnostics
from tourganize.platform.settings import Settings

SettingsFactory = Callable[..., Settings]


def test_a_healthy_installation_passes_every_check(settings_factory: SettingsFactory) -> None:
    report = run_diagnostics(build_container(settings_factory()), version="9.9.9")

    assert report.ok
    assert {check.name for check in report.checks} == {
        "config_dir",
        "data_dir",
        "clock",
        "telemetry_sink",
    }
    assert "doctor: ok" in report.render()


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
        assert [check.name for check in failures] == ["data_dir", "telemetry_sink"]
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
    assert rendered.startswith("tourganize 9.9.9")
