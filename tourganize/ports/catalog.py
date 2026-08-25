"""The ``ComponentCatalog`` port: where the declared Component Kinds come from.

The catalog is *data*, and this port is the seam that keeps it that way. Behind it F02 ships a
YAML file reader and an in-memory fake; a database, a remote configuration service or a
generated catalog would all satisfy the same four methods, and nothing above the port would
notice.

``schema_for`` is declared here but not yet implemented by any adapter: F03 introduces the
Requirement Schema it returns and narrows the return type from ``object``. It is declared now
so that the port's shape is the one later features were promised, rather than something that
grows a method the moment it is needed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tourganize.domain.catalog import ComponentKind

__all__ = ["ComponentCatalog"]


@runtime_checkable
class ComponentCatalog(Protocol):
    """The declarative registry of Component Kinds.

    Every method is a read: a catalog is loaded, never edited through the port. Adapters
    validate at load and raise :class:`~tourganize.platform.errors.CatalogError`, so a caller
    that gets a catalog back can trust that the keys are unique, the Outcome Dependencies
    resolve and there are no cycles.
    """

    def kinds(self) -> tuple[ComponentKind, ...]:
        """Every declared Component Kind, enabled or not, in declaration order.

        Declaration order is part of the contract: F04 breaks priority ties with it, so the
        Planning Agenda cannot flicker between turns.
        """
        ...

    def enabled_kinds(self) -> tuple[ComponentKind, ...]:
        """The declared Component Kinds with ``enabled: true``, in declaration order."""
        ...

    def kind(self, kind_key: str) -> ComponentKind:
        """One Component Kind by key.

        Raises :class:`~tourganize.domain.errors.UnknownComponentKindError` when the catalog
        does not declare it — including when it is declared but disabled, because a disabled
        kind must not be plannable by accident.
        """
        ...

    def schema_for(self, kind_key: str) -> object:
        """The Requirement Schema declared by ``kind_key``. Implemented by F03."""
        ...
