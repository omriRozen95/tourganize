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

Slots the roadmap will add here, with the feature that owns each:

===========================  =========================================
``option_slate_planner``     F06  ``OptionSlatePlanner`` (F05 ships the fake behind it)
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
from types import MappingProxyType
from typing import Final

from tourganize.adapters.catalog.priority import FixedOrderPolicy, WeightedCatalogPolicy
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.system import SystemClock
from tourganize.adapters.interpretation.keyword import KeywordTurnInterpreter
from tourganize.adapters.telemetry.jsonl import JsonlTelemetrySink
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.dialogue import DialogueSettings
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.settings import Settings, default_telemetry_path
from tourganize.ports.catalog import ComponentCatalog, PriorityPolicy
from tourganize.ports.interpretation import TurnInterpreter
from tourganize.ports.platform import Clock, TelemetrySink

__all__ = ["Container", "build_container", "build_dialogue_settings"]

#: Ports the roadmap introduces later, and the feature that wires each one. ``doctor``
#: prints this so the surface of what is not yet built stays visible.
PENDING_PORTS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "OptionSlatePlanner": "F06",
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
    component_catalog: ComponentCatalog
    priority_policy: PriorityPolicy
    turn_interpreter: TurnInterpreter

    def adapters(self) -> MappingProxyType[str, str]:
        """Return ``port name -> adapter class name``, for ``doctor`` and telemetry."""
        return MappingProxyType(
            {
                "Clock": type(self.clock).__name__,
                "TelemetrySink": type(self.telemetry_sink).__name__,
                "ComponentCatalog": type(self.component_catalog).__name__,
                "PriorityPolicy": type(self.priority_policy).__name__,
                "TurnInterpreter": type(self.turn_interpreter).__name__,
            }
        )


def build_container(settings: Settings) -> Container:
    """Select and construct every adapter named by ``settings``."""
    return Container(
        settings=settings,
        clock=SystemClock(),
        telemetry_sink=_build_telemetry_sink(settings),
        # Constructing the catalog does not read the file: a broken catalog has to be a
        # failing `doctor` check, not an exception thrown while the container is being wired.
        # The same laziness covers the Requirement Schemas it resolves ``schema_key`` against.
        component_catalog=YamlComponentCatalog(settings.catalog_path, settings.schema_dir),
        priority_policy=_build_priority_policy(settings),
        # Lazily too: the phrase tables are read on the first turn, so a missing
        # `config/interpretation/` is a failing `doctor` check rather than an unwireable app.
        turn_interpreter=_build_turn_interpreter(settings),
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
