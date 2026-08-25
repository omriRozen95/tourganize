"""What ``tourganize doctor`` reports: resolved settings, selected adapters, port health.

The checks are deliberately real rather than declarative — the data directory is probed by
writing a file, the telemetry sink by recording an event — because the failures worth
catching before a conversation starts are exactly the ones a config dump cannot see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tourganize.application.composition import PENDING_PORTS, Container
from tourganize.ports.platform import TelemetryEvent

__all__ = ["CheckResult", "DoctorReport", "run_diagnostics"]

_PROBE_FILENAME: Final = ".tourganize-doctor"
_PROBE_EVENT_KIND: Final = "doctor_probe"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one health check."""

    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        marker = "ok  " if self.ok else "FAIL"
        return f"  [{marker}] {self.name}: {self.detail}"


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Everything ``doctor`` prints, as data, so tests can assert on it directly."""

    version: str
    settings: Mapping[str, str]
    adapters: Mapping[str, str]
    checks: Sequence[CheckResult]
    pending_ports: Mapping[str, str]
    unrecognised_keys: Sequence[str]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self) -> str:
        lines = [f"tourganize {self.version}", "", "settings:"]
        lines += [f"  {key}: {value}" for key, value in self.settings.items()]
        lines += ["", "adapters:"]
        lines += [f"  {port}: {adapter}" for port, adapter in self.adapters.items()]
        lines += ["", "ports:"]
        lines += [check.render() for check in self.checks]
        if self.pending_ports:
            pending = ", ".join(
                f"{port} ({feature})" for port, feature in self.pending_ports.items()
            )
            lines += ["", f"awaiting a feature: {pending}"]
        if self.unrecognised_keys:
            lines += ["", f"unrecognised TOURGANIZE_* keys: {', '.join(self.unrecognised_keys)}"]
        lines += ["", "doctor: ok" if self.ok else "doctor: FAILED"]
        return "\n".join(lines)


def run_diagnostics(
    container: Container,
    *,
    version: str,
    unrecognised: Sequence[str] = (),
) -> DoctorReport:
    """Probe every wired port and return the report."""
    settings = container.settings
    checks = [
        _check_config_dir(settings.config_dir),
        _check_data_dir(settings.data_dir),
        _check_clock(container),
        _check_telemetry_sink(container),
    ]
    return DoctorReport(
        version=version,
        settings=settings.describe(),
        adapters=container.adapters(),
        checks=checks,
        pending_ports=PENDING_PORTS,
        unrecognised_keys=tuple(unrecognised),
    )


def _check_config_dir(config_dir: Path) -> CheckResult:
    if not config_dir.exists():
        # No configuration file is required yet; F02 is the first feature that reads one.
        return CheckResult("config_dir", True, f"{config_dir} does not exist yet")
    if not config_dir.is_dir():
        return CheckResult("config_dir", False, f"{config_dir} is not a directory")
    return CheckResult("config_dir", True, f"{config_dir} readable")


def _check_data_dir(data_dir: Path) -> CheckResult:
    probe = data_dir / _PROBE_FILENAME
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("data_dir", False, f"{data_dir} is not writable: {exc.strerror or exc}")
    return CheckResult("data_dir", True, f"{data_dir} writable")


def _check_clock(container: Container) -> CheckResult:
    moment = container.clock.now()
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        return CheckResult("clock", False, "now() returned a naive datetime")
    return CheckResult("clock", True, f"now() = {moment.isoformat()}")


def _check_telemetry_sink(container: Container) -> CheckResult:
    sink = container.telemetry_sink
    name = type(sink).__name__
    sink.record(
        TelemetryEvent(
            kind=_PROBE_EVENT_KIND,
            session_id=None,
            occurred_at=container.clock.now(),
            fields={"source": "doctor"},
        )
    )
    if sink.degraded:
        return CheckResult(
            "telemetry_sink", False, f"{name} stopped recording after a write failure"
        )
    return CheckResult("telemetry_sink", True, f"{name} accepted a probe event")
