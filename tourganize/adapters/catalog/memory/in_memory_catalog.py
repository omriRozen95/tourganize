"""``InMemoryComponentCatalog`` — the ``ComponentCatalog`` fake.

A catalog built from Component Kinds in code, validated exactly as a file-backed one is. It
exists so that no test needs a file on disk to have a catalog, and so a test can declare the
two neutral kinds its case is about instead of reasoning about the shipped three.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import final

from tourganize.domain.catalog import ComponentKind, catalog_problems, find_kind, only_enabled
from tourganize.platform.errors import CatalogError

__all__ = ["InMemoryComponentCatalog"]


@final
class InMemoryComponentCatalog:
    """A catalog held in memory. Validates on construction, like every other adapter."""

    def __init__(self, kinds: Iterable[ComponentKind], *, origin: str = "in memory") -> None:
        declared = tuple(kinds)
        problems = catalog_problems(declared)
        if problems:
            raise CatalogError(f"invalid Component Catalog ({origin}): " + "; ".join(problems))
        self._kinds = declared
        self._origin = origin

    @property
    def origin(self) -> str:
        """Where this catalog came from, for ``doctor`` and error messages."""
        return self._origin

    def kinds(self) -> tuple[ComponentKind, ...]:
        return self._kinds

    def enabled_kinds(self) -> tuple[ComponentKind, ...]:
        return only_enabled(self._kinds)

    def kind(self, kind_key: str) -> ComponentKind:
        return find_kind(self._kinds, kind_key, self._origin)

    def schema_for(self, kind_key: str) -> object:
        """Declared by the port, implemented by F03 — see the port's module docstring."""
        raise NotImplementedError(
            "Requirement Schemas arrive with F03; ComponentKind.schema_key names the schema "
            f"{kind_key!r} will resolve to."
        )
