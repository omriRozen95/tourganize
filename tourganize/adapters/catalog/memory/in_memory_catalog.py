"""``InMemoryComponentCatalog`` — the ``ComponentCatalog`` fake.

A catalog built from Component Kinds in code, validated exactly as a file-backed one is. It
exists so that no test needs a file on disk to have a catalog, and so a test can declare the
two neutral kinds its case is about instead of reasoning about the shipped three.

Requirement Schemas are supplied the same way: as objects, validated on construction against
the kinds that name them, so the fake answers ``schema_for`` with the same guarantees the
file-backed catalog does — right down to refusing a schema whose ``component_kind`` disagrees
with the kind that declared it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import final

from tourganize.domain.catalog import ComponentKind, catalog_problems, find_kind, only_enabled
from tourganize.domain.requirements import RequirementSchema
from tourganize.platform.errors import CatalogError, SchemaError

__all__ = ["InMemoryComponentCatalog"]


@final
class InMemoryComponentCatalog:
    """A catalog held in memory. Validates on construction, like every other adapter."""

    def __init__(
        self,
        kinds: Iterable[ComponentKind],
        schemas: Iterable[RequirementSchema] = (),
        *,
        origin: str = "in memory",
    ) -> None:
        declared = tuple(kinds)
        problems = catalog_problems(declared)
        if problems:
            raise CatalogError(f"invalid Component Catalog ({origin}): " + "; ".join(problems))
        self._kinds = declared
        self._origin = origin
        self._schemas = _schemas_by_key(declared, tuple(schemas), origin)

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

    def schema_for(self, kind_key: str) -> RequirementSchema:
        """The Requirement Schema the Component Kind ``kind_key`` declares."""
        kind = self.kind(kind_key)
        schema = self._schemas.get(kind.schema_key)
        if schema is None:
            raise SchemaError(
                f"{self._origin}: Component Kind {kind.kind_key!r} declares schema "
                f"{kind.schema_key!r}, which was not supplied"
            )
        return schema


def _schemas_by_key(
    kinds: tuple[ComponentKind, ...], schemas: tuple[RequirementSchema, ...], origin: str
) -> dict[str, RequirementSchema]:
    """Index the schemas by key, refusing any that contradicts the kind that names it."""
    declared = {kind.schema_key: kind.kind_key for kind in kinds}
    by_key: dict[str, RequirementSchema] = {}
    for schema in schemas:
        if schema.schema_key in by_key:
            raise SchemaError(f"({origin}): two schemas declare the key {schema.schema_key!r}")
        expected = declared.get(schema.schema_key)
        if expected is None:
            raise SchemaError(
                f"({origin}): schema {schema.schema_key!r} is not declared by any Component Kind"
            )
        if schema.component_kind != expected:
            raise SchemaError(
                f"({origin}): Component Kind {expected!r} declares schema "
                f"{schema.schema_key!r}, but that schema describes {schema.component_kind!r}"
            )
        by_key[schema.schema_key] = schema
    return by_key
