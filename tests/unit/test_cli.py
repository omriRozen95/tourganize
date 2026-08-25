"""The CLI surface: two working commands, five honest stubs, four exit codes."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest

from tourganize import __version__
from tourganize.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_DOCTOR_FAILED,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    PLANNED_COMMANDS,
    main,
)


def _run(argv: list[str], environ: Mapping[str, str] | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ or {}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _environ(tmp_path: Path, **extra: str) -> dict[str, str]:
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
    }
    environ.update(extra)
    return environ


def test_version_prints_the_package_version() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"], environ={})
    assert raised.value.code == EXIT_OK


def test_no_command_prints_help() -> None:
    code, out, _ = _run([])
    assert code == EXIT_OK
    assert "doctor" in out
    assert "chat" in out


@pytest.mark.parametrize("command", sorted(PLANNED_COMMANDS))
def test_every_planned_command_exits_2_naming_its_feature(command: str) -> None:
    feature, _summary = PLANNED_COMMANDS[command]

    code, out, err = _run([command])

    assert code == EXIT_NOT_IMPLEMENTED
    assert feature in err
    assert command in err
    assert out == ""


def test_chat_names_f07() -> None:
    _code, _out, err = _run(["chat"])
    assert "F07" in err


def test_doctor_reports_settings_adapters_and_ports(tmp_path: Path) -> None:
    code, out, _ = _run(["doctor"], _environ(tmp_path))

    assert code == EXIT_OK
    assert f"tourganize {__version__}" in out
    assert "telemetry_sink: jsonl" in out
    assert "TelemetrySink: JsonlTelemetrySink" in out
    assert "[ok  ] clock" in out
    assert "doctor: ok" in out


def test_doctor_never_prints_a_secret(tmp_path: Path) -> None:
    leak = "cli-must-not-print-this"
    environ = _environ(tmp_path, TOURGANIZE_PROVIDER_API_KEY=leak)

    code, out, err = _run(["doctor"], environ)

    assert code == EXIT_OK
    assert leak not in out
    assert leak not in err
    assert "TOURGANIZE_PROVIDER_API_KEY=***" in out


def test_doctor_fails_when_a_port_is_unhealthy(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    environ = _environ(tmp_path, TOURGANIZE_DATA_DIR=str(blocker / "state"))

    code, out, _ = _run(["doctor"], environ)

    assert code == EXIT_DOCTOR_FAILED
    assert "doctor: FAILED" in out


def test_an_invalid_setting_exits_3_before_anything_is_built(tmp_path: Path) -> None:
    environ = _environ(tmp_path, TOURGANIZE_LOG_FORMAT="xml")

    code, out, err = _run(["doctor"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "configuration error" in err
    assert "TOURGANIZE_LOG_FORMAT" in err
    assert out == ""


def test_a_stub_command_reports_a_broken_configuration_rather_than_its_own_stubbing() -> None:
    """Settings are resolved before dispatch: "fail fast, never half-configured"."""
    code, out, err = _run(["chat"], {"TOURGANIZE_LOG_FORMAT": "xml"})

    assert code == EXIT_CONFIGURATION_ERROR
    assert "TOURGANIZE_LOG_FORMAT" in err
    assert out == ""


def test_an_unknown_command_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["teleport"], environ={})
    assert raised.value.code == 2
