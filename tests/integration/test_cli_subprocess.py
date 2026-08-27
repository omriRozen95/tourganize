"""The CLI as an installed program: real process, real exit codes, real files."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from tourganize import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The catalog that ships with the repository. Pointing the subprocess at it — rather than at
#: a copy written into ``tmp_path`` — is what makes these tests prove that the *shipped* file
#: loads, which no unit test can.
SHIPPED_CATALOG = REPO_ROOT / "config" / "catalog" / "components.yaml"
#: The Requirement Schemas those kinds name. Pointed at for the same reason as the catalog:
#: only a subprocess reading the *shipped* files can prove the shipped files are sound.
SHIPPED_SCHEMAS = REPO_ROOT / "config" / "catalog" / "schemas"


def _run(*arguments: str, tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT),
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
        "TOURGANIZE_CATALOG_PATH": str(SHIPPED_CATALOG),
        "TOURGANIZE_SCHEMA_DIR": str(SHIPPED_SCHEMAS),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
    }
    environment.update(extra)
    return subprocess.run(
        [sys.executable, "-m", "tourganize", *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _first_shipped_kind() -> str:
    """The first enabled ``kind_key`` in the shipped catalog, read from the file itself.

    Read rather than written down, so this suite never becomes the place a travel topic is
    hardcoded — the same rule the package itself lives under.
    """
    for line in SHIPPED_CATALOG.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("- kind_key:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"{SHIPPED_CATALOG} declares no Component Kinds")


def _blocking_values_of(kind_key: str) -> dict[str, object]:
    """A plausible value for each blocking field of ``kind_key``'s shipped schema."""
    schema = next(
        path
        for path in SHIPPED_SCHEMAS.glob("*.yaml")
        if f"component_kind: {kind_key}\n" in path.read_text(encoding="utf-8")
    ).read_text(encoding="utf-8")
    blocking = re.findall(
        r"- name: (\w+)[^\n]*\n\s+field_kind: (\w+)\n\s+obligation: blocking", schema
    )
    sample: dict[str, object] = {"place": "Paris", "date_range": "2026-10-23/2026-10-28"}
    return {name: sample.get(kind, sample["place"]) for name, kind in blocking}


def test_version(tmp_path: Path) -> None:
    result = _run("--version", tmp_path=tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == __version__


def test_doctor_succeeds_and_writes_its_probe_event(tmp_path: Path) -> None:
    result = _run("doctor", tmp_path=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "doctor: ok" in result.stdout

    telemetry = tmp_path / "var" / "telemetry.jsonl"
    records = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert [record["kind"] for record in records] == ["doctor_probe"]


def test_doctor_redacts_secrets_from_a_secrets_file(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("TOURGANIZE_PROVIDER_API_KEY=leak-me-not\n", encoding="utf-8")

    result = _run("doctor", tmp_path=tmp_path, TOURGANIZE_SECRETS_FILE=str(secrets_file))

    assert result.returncode == 0
    assert "leak-me-not" not in result.stdout + result.stderr
    assert "TOURGANIZE_PROVIDER_API_KEY=***" in result.stdout


def test_a_stub_command_exits_2(tmp_path: Path) -> None:
    result = _run("chat", tmp_path=tmp_path)

    assert result.returncode == 2
    assert "F07" in result.stderr


def test_an_invalid_setting_exits_3(tmp_path: Path) -> None:
    result = _run("doctor", tmp_path=tmp_path, TOURGANIZE_TELEMETRY_SINK="carrier-pigeon")

    assert result.returncode == 3
    assert "TOURGANIZE_TELEMETRY_SINK" in result.stderr


def test_json_logs_are_machine_readable(tmp_path: Path) -> None:
    result = _run(
        "doctor",
        tmp_path=tmp_path,
        TOURGANIZE_LOG_FORMAT="json",
        TOURGANIZE_LOG_LEVEL="DEBUG",
    )

    assert result.returncode == 0
    lines = result.stderr.strip().splitlines()
    assert lines
    records = [json.loads(line) for line in lines]
    assert any(record["kind"] == "startup" for record in records)
    assert all(record["logger"].startswith("tourganize") for record in records)


def test_the_shipped_catalog_loads_and_lists_three_kinds(tmp_path: Path) -> None:
    result = _run("catalog", "show", tmp_path=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    separator = next(index for index, line in enumerate(lines) if set(line) == {"-", " "})
    kinds = [line.split()[0] for line in lines[separator + 1 :] if line.strip()]
    assert len(kinds) == 3
    assert result.stdout.count("component.") == 3


def test_the_shipped_catalog_and_its_schemas_validate(tmp_path: Path) -> None:
    result = _run("catalog", "validate", tmp_path=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no problems found" in result.stdout
    assert "3 Requirement Schemas" in result.stdout


def test_the_shipped_schemas_produce_a_gap_report(tmp_path: Path) -> None:
    """The Definition of Done, run against the files the application actually ships with."""
    kind = _first_shipped_kind()

    empty = _run("catalog", "gaps", "--kind", kind, tmp_path=tmp_path)

    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert "is_plannable: false" in empty.stdout
    assert "ask." in empty.stdout


def test_a_gap_report_turns_plannable_once_the_blocking_values_arrive(tmp_path: Path) -> None:
    kind = _first_shipped_kind()
    supplied = json.dumps(_blocking_values_of(kind))

    result = _run("catalog", "gaps", "--kind", kind, "--set", supplied, tmp_path=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "is_plannable: true" in result.stdout
    assert "blocking (0):" in result.stdout


def test_an_unknown_requirement_field_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    kind = _first_shipped_kind()

    result = _run("catalog", "gaps", "--kind", kind, "--set", '{"nowhere": 1}', tmp_path=tmp_path)

    assert result.returncode == 2
    assert "nowhere" in result.stderr


def test_a_schema_that_contradicts_the_catalog_exits_3(tmp_path: Path) -> None:
    kind = _first_shipped_kind()
    elsewhere = tmp_path / "schemas"
    elsewhere.mkdir()
    for path in SHIPPED_SCHEMAS.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        (elsewhere / path.name).write_text(
            text.replace(f"component_kind: {kind}", "component_kind: elsewhere"), encoding="utf-8"
        )

    result = _run("catalog", "validate", tmp_path=tmp_path, TOURGANIZE_SCHEMA_DIR=str(elsewhere))

    assert result.returncode == 3
    assert "elsewhere" in result.stderr
    assert result.stdout == ""


def test_a_catalog_with_a_cycle_exits_3(tmp_path: Path) -> None:
    broken = tmp_path / "cyclic.yaml"
    broken.write_text(
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: one, message_key: component.one, priority_weight: 1,"
        " schema_key: one.v1, requires_outcome_of: [two]}\n"
        "  - {kind_key: two, message_key: component.two, priority_weight: 2,"
        " schema_key: two.v1, requires_outcome_of: [one]}\n",
        encoding="utf-8",
    )

    result = _run("catalog", "validate", tmp_path=tmp_path, TOURGANIZE_CATALOG_PATH=str(broken))

    assert result.returncode == 3
    assert "dependency cycle" in result.stderr
