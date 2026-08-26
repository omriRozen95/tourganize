"""The CLI surface: the commands that work, the stubs that name their feature, four exit codes."""

from __future__ import annotations

import io
from collections.abc import Mapping
from pathlib import Path

import pytest
from conftest import write_catalog

from tourganize import __version__
from tourganize.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_DOCTOR_FAILED,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    PLANNED_CATALOG_COMMANDS,
    PLANNED_COMMANDS,
    main,
)


def _run(argv: list[str], environ: Mapping[str, str] | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ or {}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _environ(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A healthy installation in ``tmp_path``, Component Catalog included."""
    write_catalog(tmp_path / "config")
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


@pytest.mark.parametrize("action", sorted(PLANNED_CATALOG_COMMANDS))
def test_every_planned_catalog_action_exits_2_naming_its_feature(
    action: str, tmp_path: Path
) -> None:
    feature, _summary = PLANNED_CATALOG_COMMANDS[action]

    code, out, err = _run(["catalog", action], _environ(tmp_path))

    assert code == EXIT_NOT_IMPLEMENTED
    assert feature in err
    assert action in err
    assert out == ""


def test_catalog_show_lists_the_declared_kinds(tmp_path: Path) -> None:
    code, out, _ = _run(["catalog", "show"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "kind_key" in out
    assert "alpha" in out and "beta" in out and "gamma" in out
    assert "300" in out
    # beta awaits alpha's outcome, and gamma is declared but disabled.
    beta = next(line for line in out.splitlines() if line.startswith("beta"))
    gamma = next(line for line in out.splitlines() if line.startswith("gamma"))
    assert "alpha" in beta
    assert gamma.endswith("no")


def test_catalog_validate_accepts_a_sound_catalog(tmp_path: Path) -> None:
    code, out, err = _run(["catalog", "validate"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "no problems found" in out
    assert err == ""


#: A broken catalog per validation rule, with the phrase the CLI must print for it. Keyed by
#: a short name so the parametrised test ids read as the rules they exercise.
BROKEN_CATALOGS = {
    "duplicate_key": (
        "duplicate kind_key 'alpha'",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1}\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 2,"
        " schema_key: alpha.v1}\n",
    ),
    "dangling_dependency": (
        "which no kind declares",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1, requires_outcome_of: [nowhere]}\n",
    ),
    "dependency_cycle": (
        "dependency cycle",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1, requires_outcome_of: [beta]}\n"
        "  - {kind_key: beta, message_key: component.beta, priority_weight: 2,"
        " schema_key: beta.v1, requires_outcome_of: [alpha]}\n",
    ),
}


@pytest.mark.parametrize("rule", sorted(BROKEN_CATALOGS))
def test_catalog_validate_exits_3_and_names_the_problem(rule: str, tmp_path: Path) -> None:
    expected, body = BROKEN_CATALOGS[rule]
    environ = _environ(tmp_path)
    write_catalog(tmp_path / "config", body)

    code, out, err = _run(["catalog", "validate"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert expected in err
    assert out == ""


def test_catalog_show_exits_3_when_there_is_no_catalog(tmp_path: Path) -> None:
    environ = _environ(tmp_path, TOURGANIZE_CATALOG_PATH=str(tmp_path / "absent.yaml"))

    code, out, err = _run(["catalog", "show"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "does not exist" in err
    assert out == ""


def test_catalog_without_an_action_says_what_it_offers(tmp_path: Path) -> None:
    """And says it before reading the file, so a missing action is not reported as a bad catalog."""
    environ = _environ(tmp_path, TOURGANIZE_CATALOG_PATH=str(tmp_path / "absent.yaml"))

    code, out, err = _run(["catalog"], environ)

    assert code == EXIT_NOT_IMPLEMENTED
    assert "show" in err and "validate" in err
    assert "does not exist" not in err
    assert out == ""


def test_doctor_reports_settings_adapters_and_ports(tmp_path: Path) -> None:
    code, out, _ = _run(["doctor"], _environ(tmp_path))

    assert code == EXIT_OK
    assert f"tourganize {__version__}" in out
    assert "telemetry_sink: jsonl" in out
    assert "TelemetrySink: JsonlTelemetrySink" in out
    assert "ComponentCatalog: YamlComponentCatalog" in out
    assert "[ok  ] clock" in out
    assert "[ok  ] component_catalog" in out
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
