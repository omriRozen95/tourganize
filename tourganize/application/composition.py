"""The Composition Root — the only place in the codebase that constructs adapters.

Every later feature adds its port slot to :class:`Container` and its adapter selection to
:func:`build_container`, reading the choice from :class:`Settings`. Nothing else may import
``tourganize.adapters``; the import-linter contract in ``pyproject.toml`` and
``tests/architecture/test_import_boundaries.py`` both enforce that.

``build_dialogue_settings`` sits here for the same reason the adapters do: it is the one place
that reads ``TOURGANIZE_DIALOGUE_*`` and hands the dialogue the four numbers it needs.
``tourganize.dialogue`` cannot import ``Settings`` — it may import the standard library, the
domain and ``tourganize.ports``, and nothing else — so the conversion belongs on this side of
the boundary rather than in a constructor argument nobody could type.

The Presentation Surface is the one adapter that is deliberately *not* a Container slot.
:func:`build_surface` builds it per invocation, because what it needs — the locale, and a
script when there is one — is an argument of the command rather than a setting, and because
``doctor``, ``catalog`` and ``options`` have to run in an installation that never installed
the ``terminal`` extra. The Container still reports which adapter is selected: it resolves
the class *name* from a table, so nothing imports a terminal library, and nothing starts a
Textual application, in order to print a line of a health report.

Slots the roadmap will add here, with the feature that owns each:

===========================  =========================================
``llm_gateway``              F08  ``LlmGateway``
``language_detector``        F10  ``LanguageDetector``
``session_repository``       F12  ``SessionRepository``
``itinerary_renderer``       F13  ``ItineraryRenderer``
``tool_broker``              F15  ``ToolBroker``
``knowledge_corpus``         F18  ``KnowledgeCorpus``
``knowledge_retriever``      F19  ``KnowledgeRetriever``
===========================  =========================================
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol, TypeVar, runtime_checkable

from tourganize.adapters.catalog.priority import FixedOrderPolicy, WeightedCatalogPolicy
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.system import SystemClock
from tourganize.adapters.interpretation.keyword import KeywordTurnInterpreter
from tourganize.adapters.options import CheapestFirstRanking, SourceRegistry
from tourganize.adapters.options.fixture import FixtureOptionSource
from tourganize.adapters.presentation.scripted import ScriptedSurface, read_script
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.application.planning_service import PlanningService
from tourganize.dialogue import DialogueSettings
from tourganize.language.act_renderer import ActRenderer
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.settings import (
    Settings,
    SourceProfileName,
    SurfaceName,
    default_telemetry_path,
)
from tourganize.ports.catalog import ComponentCatalog, PriorityPolicy
from tourganize.ports.interpretation import OptionSlatePlanner, TurnInterpreter
from tourganize.ports.options import OptionRanking, OptionSource, OptionSourceRegistry
from tourganize.ports.platform import Clock, TelemetrySink
from tourganize.ports.presentation import PresentationSurface

__all__ = [
    "Container",
    "build_container",
    "build_dialogue_settings",
    "build_surface",
    "read_script_file",
    "run_on_surface",
    "surface_dependency_problem",
    "surface_transcript",
]

#: Which feature delivers each Source Profile this release cannot build. ``fixture`` is absent
#: because it is the one that is wired; the rest are refused by name, naming the feature.
_SOURCE_PROFILE_FEATURES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"world": "F17", "live": "F24"}
)

#: Which adapter class each value of ``TOURGANIZE_SURFACE`` selects, as a *name*. ``doctor``
#: prints the selected adapter of every port, and it has to be able to do that in an
#: installation without the ``terminal`` extra — and without starting a Textual application
#: to ask an instance what it is called. A table of names is the only answer that costs
#: neither; ``tests/unit/test_composition.py`` is where it is pinned to the real classes.
_SURFACE_ADAPTERS: Final[MappingProxyType[SurfaceName, str]] = MappingProxyType(
    {"terminal": "TerminalSurface", "scripted": "ScriptedSurface"}
)

#: What each surface needs installed, and the extra that installs it. ``scripted`` is absent
#: because it needs nothing at all, which is exactly why it is the surface CI drives: a
#: headless conversation must not depend on a terminal library being present.
_SURFACE_EXTRAS: Final[MappingProxyType[SurfaceName, tuple[str, str]]] = MappingProxyType(
    {"terminal": ("textual", "terminal")}
)

#: Ports with no adapter in the Container yet, and the feature that wires one. ``doctor``
#: prints this so the surface of what is not yet built stays visible.
PENDING_PORTS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "LlmGateway": "F08",
        "LanguageDetector": "F10",
        "SessionRepository": "F12",
        "ItineraryRenderer": "F13",
        "ToolBroker": "F15",
        "KnowledgeCorpus": "F18",
        "KnowledgeRetriever": "F19",
    }
)


@dataclass(frozen=True, slots=True)
class Container:
    """The wired application: settings plus one slot per port that has an adapter."""

    settings: Settings
    clock: Clock
    telemetry_sink: TelemetrySink
    component_catalog: ComponentCatalog
    priority_policy: PriorityPolicy
    turn_interpreter: TurnInterpreter
    option_sources: OptionSourceRegistry
    option_ranking: OptionRanking
    option_slate_planner: OptionSlatePlanner
    act_renderer: ActRenderer

    def adapters(self) -> MappingProxyType[str, str]:
        """Return ``port name -> adapter class name``, for ``doctor`` and telemetry.

        The Presentation Surface is named rather than inspected: it is built per invocation
        by :func:`build_surface`, and reporting it must not cost an import of a terminal
        library nor the construction of one.
        """
        return MappingProxyType(
            {
                "Clock": type(self.clock).__name__,
                "TelemetrySink": type(self.telemetry_sink).__name__,
                "ComponentCatalog": type(self.component_catalog).__name__,
                "PriorityPolicy": type(self.priority_policy).__name__,
                "TurnInterpreter": type(self.turn_interpreter).__name__,
                "OptionSourceRegistry": type(self.option_sources).__name__,
                "OptionRanking": type(self.option_ranking).__name__,
                "OptionSlatePlanner": type(self.option_slate_planner).__name__,
                "PresentationSurface": _SURFACE_ADAPTERS[self.settings.surface],
            }
        )


def build_container(settings: Settings) -> Container:
    """Select and construct every adapter named by ``settings``."""
    clock = SystemClock()
    telemetry_sink = _build_telemetry_sink(settings)
    # Constructing the catalog does not read the file: a broken catalog has to be a failing
    # `doctor` check, not an exception thrown while the container is being wired. The same
    # laziness covers the Requirement Schemas it resolves ``schema_key`` against, the phrase
    # tables the interpreter reads on the first turn, and the fixture tree the Option Sources
    # read on the first query.
    component_catalog = YamlComponentCatalog(settings.catalog_path, settings.schema_dir)
    option_sources = _build_option_sources(settings, clock)
    option_ranking = CheapestFirstRanking()
    return Container(
        settings=settings,
        clock=clock,
        telemetry_sink=telemetry_sink,
        component_catalog=component_catalog,
        priority_policy=_build_priority_policy(settings),
        turn_interpreter=_build_turn_interpreter(settings),
        option_sources=option_sources,
        option_ranking=option_ranking,
        option_slate_planner=PlanningService(
            component_catalog,
            option_sources,
            option_ranking,
            clock,
            telemetry_sink,
            slate_size=settings.slate_size,
            filter_strict=settings.option_filter_strict,
            timeout_seconds=settings.option_source_timeout_seconds,
        ),
        # Lazy for the same reason the catalog is: constructing the renderer reads no message
        # file, so a missing or malformed one is a failing `doctor` check and a visible
        # ⟪missing:…⟫ marker rather than an exception thrown while the application is wired.
        act_renderer=ActRenderer(
            settings.message_dir,
            component_catalog,
            supported_locales=settings.supported_locales,
            default_locale=settings.default_locale,
        ),
    )


def build_dialogue_settings(settings: Settings) -> DialogueSettings:
    """The dialogue's slice of the resolved configuration, in the type the Director takes.

    F07 wires the Director and needs this; it lives here rather than there so that the mapping
    from ``TOURGANIZE_DIALOGUE_*`` to the state machine's limits is written once.
    """
    return DialogueSettings(
        max_reasks=settings.dialogue_max_reasks,
        optional_ask_limit=settings.dialogue_optional_ask_limit,
        offer_batch=settings.dialogue_offer_batch,
        failure_skip=settings.agenda_failure_skip,
    )


def build_surface(
    container: Container,
    *,
    locale: str | None = None,
    script: Sequence[str] | None = None,
    session_id: str = "",
    debug_status: bool = False,
) -> PresentationSurface:
    """Build the Presentation Surface ``TOURGANIZE_SURFACE`` names, in ``locale``.

    ``script`` forces the Scripted Surface whatever the setting says, because a caller that
    already has the turns has said what it wants more plainly than an environment variable
    could: ``tourganize chat --script FILE`` is headless even on a machine with a terminal.

    The terminal adapter is imported *inside* this function, and that is the whole reason the
    surface is built here rather than in :func:`build_container`. ``doctor``, ``catalog`` and
    ``options`` must run in an installation that never installed the ``terminal`` extra, and a
    module-level import of it would make every one of them fail at start-up over a dependency
    none of them uses. When the selected surface really is the terminal and the extra really
    is missing, the failure names the command that fixes it.

    ``session_id`` and ``debug_status`` are the Terminal Surface's status line: the id so that
    a transcript in telemetry can be found from what is on screen, and the flag because the
    Dialogue State beside it is priceless while developing and noise in front of a client.
    The Scripted Surface takes neither, having no status line to put anything in.
    """
    settings = container.settings
    chosen: SurfaceName = "scripted" if script is not None else settings.surface
    resolved = locale if locale is not None else settings.default_locale
    if chosen == "scripted":
        return ScriptedSurface(
            () if script is None else tuple(script),
            container.clock,
            container.act_renderer,
            locale=resolved,
        )
    try:
        from tourganize.adapters.presentation.terminal import TerminalSurface
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigurationError(_missing_extra(chosen, str(exc))) from exc
    return TerminalSurface(
        container.act_renderer,
        container.clock,
        locale=resolved,
        session_id=session_id,
        debug_status=debug_status,
    )


def surface_dependency_problem(settings: Settings) -> str | None:
    """Why the *selected* surface cannot be built, or ``None`` when it can.

    A ``find_spec`` rather than an import, and deliberately so: ``doctor`` reports on a
    surface it is not about to run, and importing a terminal library into a process whose
    next act is to print a report and exit would be paying for the answer twice. Only the
    selected surface is judged — an installation that drives the Scripted Surface is not
    unhealthy for lacking the ``terminal`` extra it never asked for.
    """
    required = _SURFACE_EXTRAS.get(settings.surface)
    if required is None:
        return None
    module, _extra = required
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover - a half-installed distribution
        found = False
    return None if found else _missing_extra(settings.surface, f"no module named {module!r}")


@runtime_checkable
class _Recording(Protocol):
    """A surface that kept the session as text. Adapter knowledge, not a port promise."""

    @property
    def transcript(self) -> str: ...


def surface_transcript(surface: PresentationSurface) -> str | None:
    """The whole session as text when the surface kept one, else ``None``.

    Both shipped surfaces keep one, and printing it is what makes ``chat`` a command rather
    than a spectacle. For the Scripted Surface that is obvious — it has been talking to itself
    and the transcript is the only output a headless run has. For the Terminal Surface it is
    the less obvious half of the same argument: a terminal application draws on the *alternate
    screen*, which the terminal throws away when the application exits, so a plan summary that
    was only ever drawn is a plan summary the traveller watched disappear. Writing it to real
    stdout on the way out leaves the conversation in the scrollback where a shell user expects
    to find it — and it is what makes "declining prints a plan summary" literally true.

    A capability, not a type test: a surface that records nothing simply does not answer, and
    this function needs no list of the ones that do.
    """
    if isinstance(surface, _Recording):
        return surface.transcript or None
    return None


def read_script_file(path: Path) -> tuple[str, ...]:
    """Read a transcript file as the turns it holds, by the Scripted Surface's own rules.

    Here rather than at the command line because ``tourganize.cli`` may not import an
    adapter, and because "what counts as a turn in a transcript file" is one answer belonging
    to the surface that replays them, not a second one written next to an argument parser.
    """
    return read_script(path)


def _missing_extra(surface: SurfaceName, detail: str) -> str:
    """The message an installation missing a surface's dependency is told, naming the fix."""
    _module, extra = _SURFACE_EXTRAS[surface]
    return (
        f'TOURGANIZE_SURFACE={surface} needs the `{extra}` extra: pip install -e ".[{extra}]" '
        f"({detail}); TOURGANIZE_SURFACE=scripted needs nothing and runs headless"
    )


def _build_turn_interpreter(settings: Settings) -> TurnInterpreter:
    """Select the Turn Interpreter named by ``TOURGANIZE_INTERPRETER``.

    ``model`` is a documented value of that key and F08 is the feature that builds it. Refusing
    it here, by name, is the whole reason the key accepts it now: an installation that asks for
    the model-backed interpreter is told which feature delivers it, instead of being told that
    ``model`` is not a value — which would be true and useless.
    """
    if settings.interpreter == "model":
        raise ConfigurationError(
            "TOURGANIZE_INTERPRETER=model is delivered by F08 (the LLM Gateway and Prompt "
            "Library); set TOURGANIZE_INTERPRETER=keyword until then"
        )
    return KeywordTurnInterpreter(settings.keyword_config_dir)


def _build_option_sources(settings: Settings, clock: Clock) -> SourceRegistry:
    """Wire the Option Sources of every Source Profile ``TOURGANIZE_OPTION_SOURCE_PROFILE`` names.

    ``world`` and ``live`` are documented values of that key and F17 and F24 are the features
    that build them, so asking for one is refused *by name* here — the same bargain
    ``TOURGANIZE_INTERPRETER=model`` makes. Refusing at wiring time rather than at the first
    query is deliberate: a demonstration that greets a traveller and then cannot source anything
    is worse than one that will not start.

    Only the profiles actually named are built, so a fixture-only installation never constructs
    a client for a provider it has no account with.
    """
    profile = settings.option_source_profile
    unavailable = {
        name: _SOURCE_PROFILE_FEATURES[name] for name in profile.names if name != "fixture"
    }
    if unavailable:
        named = ", ".join(f"{name} ({feature})" for name, feature in sorted(unavailable.items()))
        raise ConfigurationError(
            f"TOURGANIZE_OPTION_SOURCE_PROFILE={profile.describe()} asks for {named}; set every "
            f"Component Kind to the fixture profile until then"
        )
    sources: dict[SourceProfileName, tuple[OptionSource, ...]] = {
        "fixture": (FixtureOptionSource(settings.fixture_dir, clock),)
    }
    return SourceRegistry(profile, sources)


def _build_priority_policy(settings: Settings) -> PriorityPolicy:
    """Select the Priority Policy named by ``TOURGANIZE_PRIORITY_POLICY``.

    ``fixed`` is built with no configured order on purpose: with none, ``FixedOrderPolicy``
    keeps the order it is handed, which is the order the Component Catalog declares its Kinds
    in. "Plan them in the order the file lists them, ignoring the weights" is the whole meaning
    of the setting, and it needs no second key to say it in.
    """
    if settings.priority_policy == "fixed":
        return FixedOrderPolicy()
    return WeightedCatalogPolicy()


def _build_telemetry_sink(settings: Settings) -> TelemetrySink:
    if settings.telemetry_sink == "null":
        return NullTelemetrySink()
    # `Settings.from_env` always resolves the path; a Settings built by hand may not, so the
    # documented default is asked for by name rather than spelled out a second time.
    path = settings.telemetry_path or default_telemetry_path(settings.data_dir)
    return JsonlTelemetrySink(path)


_Result = TypeVar("_Result")


@runtime_checkable
class _SessionHost(Protocol):
    """A surface that has to own the thread the process started on.

    Not a widening of :class:`~tourganize.ports.presentation.PresentationSurface`, and
    deliberately private: it is a fact about *some* adapters, not a promise the port makes,
    and a surface that does not declare it is run exactly as before.
    """

    def run_session(self, pump: Callable[[], _Result]) -> _Result: ...


def run_on_surface(surface: PresentationSurface, pump: Callable[[], _Result]) -> _Result:
    """Run ``pump`` — the session loop — on whichever thread ``surface`` needs it on.

    A terminal application is not a library the Director can call into: its driver installs
    signal handlers, and ``signal.signal`` only works on the main thread, so an interface
    started on a worker returns without ever drawing and the Director then blocks forever on
    a queue nobody is feeding. The interface therefore keeps the main thread and the *session*
    moves — which is the opposite of how it reads, and exactly why it is written down here
    rather than discovered again.

    The check is a capability, not a type test: ``ScriptedSurface`` answers ``run_session`` by
    calling ``pump`` on the spot, so this function has no branch on which surface is attached
    and neither does its caller. That is the same rule the Session Runner lives under — a
    surface is never asked what it is.
    """
    if isinstance(surface, _SessionHost):
        return surface.run_session(pump)
    return pump()  # pragma: no cover - both shipped adapters host their own session
