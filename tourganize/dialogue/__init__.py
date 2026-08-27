"""The Dialogue context: Planning Session, Dialogue State, Dialogue Director, Assistant Acts.

Pure, like ``tourganize.domain``: modules here may import the standard library, the domain and
``tourganize.ports``, and nothing else — no HTTP client, no LLM SDK, no terminal library, no
database driver. The rule is enforced by import-linter (see ``pyproject.toml``) and by
``tests/architecture/test_import_boundaries.py``; the one extra permission over the domain's is
``tourganize.ports``, which is [D17](../../docs/architecture/decisions.md).

Six modules, in dependency order: :mod:`~tourganize.dialogue.turns` is the vocabulary that
crosses the Director's edges, :mod:`~tourganize.dialogue.states` is the state machine as data,
:mod:`~tourganize.dialogue.session` is the conversation aggregate,
:mod:`~tourganize.dialogue.settings` is the four limits the machine reads,
:mod:`~tourganize.dialogue.ports` declares the two protocols the Director consumes, and
:mod:`~tourganize.dialogue.director` is the machine itself.

Filled by F05.
"""

from __future__ import annotations

from tourganize.dialogue.director import TURN_EVENT_KIND, DialogueDirector
from tourganize.dialogue.ports import DialogueContext, OptionSlatePlanner, TurnInterpreter
from tourganize.dialogue.session import (
    SESSION_SCHEMA_VERSION,
    PendingQuestion,
    PlanningSession,
    TranscriptEntry,
    new_session,
)
from tourganize.dialogue.settings import (
    DEFAULT_MAX_REASKS,
    DEFAULT_OFFER_BATCH,
    DEFAULT_OPTIONAL_ASK_LIMIT,
    DialogueSettings,
)
from tourganize.dialogue.states import (
    RESTING_STATES,
    TRANSITIONS,
    DialogueState,
    reachable_states,
    require_transition,
)
from tourganize.dialogue.turns import (
    ACT_VOCABULARY,
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CLARIFY_CODES,
    CLARIFY_INTERPRETER_FAILED,
    CLARIFY_NOT_UNDERSTOOD,
    CLARIFY_STILL_MISSING,
    CLARIFY_UNDECLARED_FIELD,
    CLARIFY_UNRESOLVED_CHOICE,
    CLOSE,
    CONFIRM_SELECTION,
    DEFAULT_LOCALE,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    REPORT_SOURCING_FAILURE,
    SOURCING_FAILED,
    AssistantAct,
    TurnIntent,
    TurnInterpretation,
    UserTurn,
)

__all__ = [
    "ACT_VOCABULARY",
    "ASK_BLOCKING",
    "ASK_OPTIONAL",
    "CLARIFY",
    "CLARIFY_CODES",
    "CLARIFY_INTERPRETER_FAILED",
    "CLARIFY_NOT_UNDERSTOOD",
    "CLARIFY_STILL_MISSING",
    "CLARIFY_UNDECLARED_FIELD",
    "CLARIFY_UNRESOLVED_CHOICE",
    "CLOSE",
    "CONFIRM_SELECTION",
    "DEFAULT_LOCALE",
    "DEFAULT_MAX_REASKS",
    "DEFAULT_OFFER_BATCH",
    "DEFAULT_OPTIONAL_ASK_LIMIT",
    "DELIVER_SUMMARY",
    "GREET",
    "OFFER_UNMENTIONED",
    "PRESENT_SLATE",
    "REPORT_INVALID_VALUE",
    "REPORT_SOURCING_FAILURE",
    "RESTING_STATES",
    "SESSION_SCHEMA_VERSION",
    "SOURCING_FAILED",
    "TRANSITIONS",
    "TURN_EVENT_KIND",
    "AssistantAct",
    "DialogueContext",
    "DialogueDirector",
    "DialogueSettings",
    "DialogueState",
    "OptionSlatePlanner",
    "PendingQuestion",
    "PlanningSession",
    "TranscriptEntry",
    "TurnIntent",
    "TurnInterpretation",
    "TurnInterpreter",
    "UserTurn",
    "new_session",
    "reachable_states",
    "require_transition",
]
