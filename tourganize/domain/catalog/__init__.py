"""The Component Catalog's domain half: Component Kinds, the invariants, the Agenda.

The loading — a file, a database, a remote service — belongs to an adapter behind the
``ComponentCatalog`` port. What a valid catalog *is*, how one is read, and what order the Kinds
it declares are planned in all belong here.

Two of the three modules are F02's; :mod:`~tourganize.domain.catalog.agenda` and
:mod:`~tourganize.domain.catalog.prioritization` are F04's answer to "what do we plan next?".
The ``PriorityPolicy`` port is declared in the second of them and re-exported by
``tourganize.ports.catalog``, because the rule that concatenates its output — Mentioned-First —
is a domain function, and the domain may import nothing but the standard library and itself.
"""

from __future__ import annotations

from tourganize.domain.catalog.agenda import (
    AWAITS_OUTCOME,
    DEFAULT_AGENDA_FAILURE_SKIP,
    FAILED_SKIPPED,
    NOT_PLANNABLE,
    READY,
    REASON_CODE_PATTERN,
    REASON_CODES,
    AgendaBand,
    AgendaEntry,
    PlanningAgenda,
)
from tourganize.domain.catalog.kinds import (
    KIND_KEY_PATTERN,
    ComponentKind,
    catalog_problems,
    find_kind,
    only_enabled,
)
from tourganize.domain.catalog.prioritization import (
    PriorityPolicy,
    awaited_within,
    build_agenda,
)

__all__ = [
    "AWAITS_OUTCOME",
    "DEFAULT_AGENDA_FAILURE_SKIP",
    "FAILED_SKIPPED",
    "KIND_KEY_PATTERN",
    "NOT_PLANNABLE",
    "READY",
    "REASON_CODES",
    "REASON_CODE_PATTERN",
    "AgendaBand",
    "AgendaEntry",
    "ComponentKind",
    "PlanningAgenda",
    "PriorityPolicy",
    "awaited_within",
    "build_agenda",
    "catalog_problems",
    "find_kind",
    "only_enabled",
]
