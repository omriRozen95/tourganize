"""``InMemoryComponentCatalog`` — the ``ComponentCatalog`` fake.

A catalog built from Component Kinds in code, validated exactly as a file-backed one is. It
exists so that no test needs a file on disk to have a catalog, and so a test can declare the
two neutral kinds its case is about instead of reasoning about the shipped three.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import final

from tourganize.domain.catalog import ComponentKind, catalog_problems
from tourganize.domain.errors import UnknownComponentKindError
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
        return tuple(kind for kind in self._kinds if kind.enabled)

    def kind(self, kind_key: str) -> ComponentKind:
        for kind in self.enabled_kinds():
            if kind.kind_key == kind_key:
                return kind
        raise UnknownComponentKindError(_unknown_message(kind_key, self._kinds, self._origin))

    def schema_for(self, kind_key: str) -> object:
        """Declared by the port, implemented by F03."""
        raise NotImplementedError(
            "Requirement Schemas arrive with F03; ComponentKind.schema_key names the schema "
            f"{kind_key!r} will resolve to."
        )


def _unknown_message(kind_key: str, kinds: tuple[ComponentKind, ...], origin: str) -> str:
    disabled = {kind.kind_key for kind in kinds if not kind.enabled}
    if kind_key in disabled:
        return f"Component Kind {kind_key!r} is declared in {origin} but disabled"
    declared = ", ".join(kind.kind_key for kind in kinds if kind.enabled) or "none"
    return f"unknown Component Kind {kind_key!r}; {origin} declares {declared}"
