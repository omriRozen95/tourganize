"""The CLI as an installed program: real process, real exit codes, real files."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tourganize import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(*arguments: str, tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT),
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
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
