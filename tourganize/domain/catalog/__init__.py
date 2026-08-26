"""The Component Catalog's domain half: Component Kinds and the catalog invariants.

The loading — a file, a database, a remote service — belongs to an adapter behind the
``ComponentCatalog`` port. What a valid catalog *is*, and how one is read, belongs here.
"""

from __future__ import annotations

from tourganize.domain.catalog.kinds import (
    KIND_KEY_PATTERN,
    ComponentKind,
    catalog_problems,
    find_kind,
    only_enabled,
)

__all__ = [
    "KIND_KEY_PATTERN",
    "ComponentKind",
    "catalog_problems",
    "find_kind",
    "only_enabled",
]
