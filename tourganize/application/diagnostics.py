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

from tourganize.application.composition import (
    PENDING_PORTS,
    Container,
    surface_dependency_problem,
)
from tourganize.dialogue import DEFAULT_LOCALE, DialogueContext, DialogueState, UserTurn
from tourganize.domain.catalog import build_agenda
from tourganize.domain.errors import InvariantViolationError, UnknownComponentKindError
from tourganize.domain.invariants import is_aware
from tourganize.domain.trip import TripPlan
from tourganize.platform.errors import ConfigurationError, ContractViolationError
from tourganize.ports.platform import TelemetryEvent

__all__ = ["CheckResult", "DoctorReport", "run_diagnostics"]

_PROBE_FILENAME: Final = ".tourganize-doctor"
_PROBE_EVENT_KIND: Final = "doctor_probe"
_PROBE_PLAN_ID: Final = "doctor"
#: What the Turn Interpreter probe hands the interpreter. Deliberately meaningless in every
#: language: the check is that the interpreter *answers*, having read its configuration, not
#: that it understood anything.
_PROBE_UTTERANCE: Final = "?"


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
        _check_component_catalog(container),
        _check_priority_policy(container),
        _check_turn_interpreter(container),
        _check_option_sources(container),
        _check_message_catalogue(container),
        _check_presentation_surface(container),
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
        # Absence is reported by the check that needs a file, naming the file: `config_dir`
        # itself only has to exist by the time something reads from it.
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
    if not is_aware(moment):
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


def _check_component_catalog(container: Container) -> CheckResult:
    """Load the Component Catalog for real, and count what it declares.

    This is the check that makes ``doctor`` worth running after an edit to
    ``components.yaml``: a duplicate key, a dangling Outcome Dependency or a cycle fails here,
    with the same message ``tourganize catalog validate`` prints, instead of surfacing halfway
    through a conversation.
    """
    catalog = container.component_catalog
    origin = container.settings.catalog_path
    try:
        kinds = catalog.kinds()
    except ConfigurationError as exc:
        return CheckResult("component_catalog", False, str(exc))
    if not kinds:
        return CheckResult("component_catalog", False, f"{origin} declares no Component Kinds")
    enabled = sum(1 for kind in kinds if kind.enabled)
    return CheckResult(
        "component_catalog",
        True,
        f"{len(kinds)} Component Kinds ({enabled} enabled) from {origin}",
    )


def _check_priority_policy(container: Container) -> CheckResult:
    """Build the Planning Agenda of an empty plan, and report the order it comes back in.

    A real probe rather than a name: the policy is replaceable, and a replacement that drops or
    invents a ``kind_key`` is refused at the Agenda's seam. Better to find that here than
    halfway through a conversation. A catalog that will not load is *not* reported again — the
    check above it already says why — because two lines about one broken file is one too many.

    ``failure_skip`` is passed rather than defaulted, so this probe exercises the resolved value
    of ``TOURGANIZE_AGENDA_FAILURE_SKIP`` and not the constant behind it. ``plannable`` is not:
    computing it means loading every Requirement Schema, which is what ``catalog validate`` is
    for, and the probe plan has no components, so no answer it could give would change the order
    this check reports.
    """
    policy = container.priority_policy
    named = f"{type(policy).__name__} ({policy.policy_id})"
    try:
        kinds = container.component_catalog.kinds()
    except ConfigurationError:
        return CheckResult("priority_policy", True, f"{named}: nothing to order yet")
    plan = TripPlan(plan_id=_PROBE_PLAN_ID, created_at=container.clock.now())
    try:
        agenda = build_agenda(
            plan, kinds, policy, failure_skip=container.settings.agenda_failure_skip
        )
    except (ContractViolationError, InvariantViolationError) as exc:
        return CheckResult("priority_policy", False, str(exc))
    order = ", ".join(entry.kind_key for entry in agenda.entries) or "nothing"
    return CheckResult("priority_policy", True, f"{named} would plan {order}")


def _check_turn_interpreter(container: Container) -> CheckResult:
    """Read one meaningless turn, which is what makes the interpreter load its configuration.

    A real probe rather than a name, for the reason the catalog check is one: the keyword
    interpreter reads its phrase tables lazily, so a missing or malformed
    ``keywords.<locale>.yaml`` would otherwise first be noticed on a traveller's first turn.
    """
    interpreter = container.turn_interpreter
    name = type(interpreter).__name__
    turn = UserTurn(index=0, text=_PROBE_UTTERANCE, received_at=container.clock.now())
    context = DialogueContext(state=DialogueState.GREETING, locale=DEFAULT_LOCALE)
    try:
        interpretation = interpreter.interpret(turn, context)
    except ConfigurationError as exc:
        return CheckResult("turn_interpreter", False, str(exc))
    return CheckResult(
        "turn_interpreter", True, f"{name} read a probe turn as {interpretation.intent.value}"
    )


def _check_option_sources(container: Container) -> CheckResult:
    """Resolve the Option Sources of every declared Component Kind, and say what they are.

    A real probe rather than a name, for the reason the catalog check is one: this is where a
    per-kind Source Profile that names a Kind nobody declares, or a profile with nothing wired
    behind it, becomes visible — before a traveller is greeted rather than after they have
    answered three questions.

    A fixture tree that holds no data for a Kind is reported and is **not** a failure. The
    Fixture Provider answers such a query with a deterministic synthetic set on purpose, so that
    a demonstration never dead-ends; what would be dishonest is not saying so, which is why the
    count of Kinds with recorded data is in the line.
    """
    registry = container.option_sources
    try:
        kinds = container.component_catalog.enabled_kinds()
    except ConfigurationError:
        # The catalog check above already says why. One line about one broken file is enough.
        return CheckResult("option_sources", True, "no Component Kinds to source for yet")
    try:
        resolved = {kind.kind_key: registry.sources_for(kind.kind_key) for kind in kinds}
    except UnknownComponentKindError as exc:
        return CheckResult("option_sources", False, str(exc))
    profiles = ", ".join(
        f"{kind_key} -> {registry.profile_for(kind_key)}: "
        f"{', '.join(source.source_id for source in sources)}"
        for kind_key, sources in resolved.items()
    )
    recorded = sum(
        1
        for kind_key, sources in resolved.items()
        if any(kind_key in source.kind_keys for source in sources)
    )
    return CheckResult(
        "option_sources",
        True,
        f"{profiles or 'nothing to source'} ({recorded} of {len(resolved)} with recorded data)",
    )


def _check_message_catalogue(container: Container) -> CheckResult:
    """Load the Message Catalogue and Display Profiles of every supported locale.

    A real probe rather than a name, for the reason the catalog check is one: the renderer
    reads its files lazily, so a locale listed in ``TOURGANIZE_SUPPORTED_LOCALES`` with no
    ``<locale>.yaml`` behind it would otherwise first be noticed as a screen full of
    ⟪missing:…⟫ markers in front of whoever the demonstration was for.

    Every supported locale is loaded, not only the default: the point of shipping a second
    one from day one is that the second one is exercised. A locale with no Display Profile
    file is *not* a failure — the renderer falls back to "every declared fact, in declaration
    order", which is what an unconfigured fourth Component Kind gets too — so the count of
    profiles is reported and a zero is left to speak for itself.
    """
    renderer = container.act_renderer
    described: list[str] = []
    for locale in container.settings.supported_locales:
        try:
            catalogue = renderer.catalogue(locale)
            profiles = renderer.profiles(locale)
        except ConfigurationError as exc:
            return CheckResult("message_catalogue", False, str(exc))
        described.append(
            f"{locale} ({len(catalogue.messages)} messages, {catalogue.direction}, "
            f"{len(profiles.kinds)} display profiles)"
        )
    return CheckResult(
        "message_catalogue", True, f"{renderer.message_dir}: {', '.join(described) or 'nothing'}"
    )


def _check_presentation_surface(container: Container) -> CheckResult:
    """Report the selected surface, and whether the installation could actually run it.

    Only the *selected* one is judged. An installation that drives the Scripted Surface in a
    container with no terminal library is healthy, and telling it otherwise would train
    everybody to ignore a failing check — which is the failure mode a health report cannot
    afford. When the selected surface is the terminal and its extra is missing, the line names
    the pip command that fixes it.
    """
    settings = container.settings
    adapter = container.adapters()["PresentationSurface"]
    problem = surface_dependency_problem(settings)
    if problem is not None:
        return CheckResult("presentation_surface", False, problem)
    return CheckResult(
        "presentation_surface",
        True,
        f"{adapter} ({settings.surface}) in {settings.default_locale}, "
        f"of {', '.join(settings.supported_locales)}",
    )
