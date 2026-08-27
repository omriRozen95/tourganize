"""The ``ComponentCatalog`` port: where the declared Component Kinds come from.

The catalog is *data*, and this port is the seam that keeps it that way. Behind it F02 ships a
YAML file reader and an in-memory fake; a database, a remote configuration service or a
generated catalog would all satisfy the same four methods, and nothing above the port would
notice.

``schema_for`` completes the port. A Component Kind declares a ``schema_key`` and this is
where that key becomes a Requirement Schema — a file for the YAML adapter, an object handed in
for the fake, a row somewhere else. What the *catalog* is and where the *schemas* are are the
same question as far as a caller is concerned, which is why they are one port and not two.

``PriorityPolicy`` (F04) belongs to this module too, and is re-exported from it rather than
declared here. It orders the Component Kinds a catalog declares, so this is where a reader
looks for it — but the Mentioned-First Rule that consumes its output is a domain function, and
the domain may import nothing but the standard library and itself. The protocol is therefore
defined in :mod:`tourganize.domain.catalog.prioritization`, exactly as
:class:`~tourganize.domain.errors.TourganizeError` is defined in the domain and read from
``tourganize.platform.errors``. Its adapters are ``WeightedCatalogPolicy`` (the shipped
default) and ``FixedOrderPolicy`` (the fake), both in ``tourganize.adapters.catalog.priority``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tourganize.domain.catalog import ComponentKind, PriorityPolicy
from tourganize.domain.requirements import RequirementSchema

__all__ = ["ComponentCatalog", "PriorityPolicy"]


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

    def schema_for(self, kind_key: str) -> RequirementSchema:
        """The Requirement Schema declared by ``kind_key``.

        Raises :class:`~tourganize.domain.errors.UnknownComponentKindError` for a kind the
        catalog does not declare or has disabled, and
        :class:`~tourganize.platform.errors.SchemaError` when the schema it names is missing,
        unreadable, invalid, or describes a different Component Kind. Every adapter caches:
        a conversation must not see its requirements change underneath it mid-turn.
        """
        ...
