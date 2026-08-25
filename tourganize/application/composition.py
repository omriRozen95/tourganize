"""The Composition Root — the only place in the codebase that constructs adapters.

Every later feature adds its port slot to :class:`Container` and its adapter selection to
:func:`build_container`, reading the choice from :class:`Settings`. Nothing else may import
``tourganize.adapters``; the import-linter contract in ``pyproject.toml`` and
``tests/architecture/test_import_boundaries.py`` both enforce that.

Slots the roadmap will add here, with the feature that owns each:

===========================  =========================================
``component_catalog``        F02  ``ComponentCatalog``
``priority_policy``          F04  ``PriorityPolicy``
``turn_interpreter``         F05  ``TurnInterpreter``
``option_slate_planner``     F05  ``OptionSlatePlanner``
``option_sources``           F06  ``OptionSource`` (one per Component Kind profile)
``presentation_surface``     F07  ``PresentationSurface``
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

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from tourganize.adapters.clock.system import SystemClock
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.platform.settings import TELEMETRY_FILENAME, Settings
from tourganize.ports.platform import Clock, TelemetrySink

__all__ = ["Container", "build_container"]

#: Ports the roadmap introduces later, and the feature that wires each one. ``doctor``
#: prints this so the surface of what is not yet built stays visible.
PENDING_PORTS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "ComponentCatalog": "F02",
        "PriorityPolicy": "F04",
        "TurnInterpreter": "F05",
        "OptionSlatePlanner": "F05",
        "OptionSource": "F06",
        "PresentationSurface": "F07",
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

    def adapters(self) -> MappingProxyType[str, str]:
        """Return ``port name -> adapter class name``, for ``doctor`` and telemetry."""
        return MappingProxyType(
            {
                "Clock": type(self.clock).__name__,
                "TelemetrySink": type(self.telemetry_sink).__name__,
            }
        )


def build_container(settings: Settings) -> Container:
    """Select and construct every adapter named by ``settings``."""
    return Container(
        settings=settings,
        clock=SystemClock(),
        telemetry_sink=_build_telemetry_sink(settings),
    )


def _build_telemetry_sink(settings: Settings) -> TelemetrySink:
    if settings.telemetry_sink == "null":
        return NullTelemetrySink()
    return JsonlTelemetrySink(_telemetry_path(settings))


def _telemetry_path(settings: Settings) -> Path:
    if settings.telemetry_path is not None:
        return settings.telemetry_path
    return settings.data_dir / TELEMETRY_FILENAME
