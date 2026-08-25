"""Component Kinds: the *types* of Plan Component, declared as data.

A Component Kind is identified by its ``kind_key`` and carries only declarations — a message
key for wording, a priority weight for the Planning Agenda (F04), the key of its Requirement
Schema (F03), and the Kinds whose outcome it reads. There is no behaviour here and there are
no subclasses: adding ``dining`` to the catalog file is the whole change.

The catalog invariants live here too, as :func:`catalog_problems`, which *returns* what is
wrong instead of raising. The rules are the domain's; the exception is not — every adapter
that loads a catalog turns the same findings into
:class:`~tourganize.platform.errors.CatalogError` with its own file and line context.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tourganize.domain.errors import InvariantViolationError

__all__ = ["KIND_KEY_PATTERN", "ComponentKind", "catalog_problems"]

#: A ``kind_key`` is lower snake case. It is an identifier used in config keys, message keys
#: and telemetry fields, so it is kept to a shape that is safe in all three.
KIND_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")

_MESSAGE_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_.]*")


@dataclass(frozen=True, slots=True)
class ComponentKind:
    """One declared kind of Plan Component. Data, validated at construction."""

    kind_key: str
    message_key: str
    priority_weight: int
    schema_key: str
    requires_outcome_of: tuple[str, ...] = ()
    enabled: bool = True

    def __post_init__(self) -> None:
        _require_key(self.kind_key, "kind_key", KIND_KEY_PATTERN)
        _require_key(self.message_key, "message_key", _MESSAGE_KEY_PATTERN)
        _require_text(self.schema_key, "schema_key")
        if type(self.priority_weight) is not int:
            raise InvariantViolationError(
                f"{self.kind_key}: priority_weight must be an integer, got {self.priority_weight!r}"
            )
        if type(self.requires_outcome_of) is not tuple:
            raise InvariantViolationError(
                f"{self.kind_key}: requires_outcome_of must be a tuple of kind_keys, "
                f"got {self.requires_outcome_of!r}"
            )
        for referenced in self.requires_outcome_of:
            _require_key(referenced, f"{self.kind_key}.requires_outcome_of", KIND_KEY_PATTERN)
        if self.kind_key in self.requires_outcome_of:
            raise InvariantViolationError(f"{self.kind_key}: a kind cannot await its own outcome")


def catalog_problems(kinds: Sequence[ComponentKind]) -> tuple[str, ...]:
    """Return one message per broken catalog invariant — empty when the catalog is sound.

    Three things make a set of Component Kinds unusable: two kinds claiming the same
    ``kind_key``, an Outcome Dependency on a kind nobody declares, and a cycle in those
    dependencies. Every problem found is reported, not just the first, because a catalog is
    edited by hand and one round trip per mistake is a poor way to spend an afternoon.
    """
    problems = [*_duplicate_keys(kinds), *_dangling_dependencies(kinds)]
    problems += _dependency_cycles(kinds)
    return tuple(problems)


def _duplicate_keys(kinds: Sequence[ComponentKind]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for kind in kinds:
        if kind.kind_key in seen and kind.kind_key not in duplicates:
            duplicates.append(kind.kind_key)
        seen.add(kind.kind_key)
    return [f"duplicate kind_key {key!r}" for key in duplicates]


def _dangling_dependencies(kinds: Sequence[ComponentKind]) -> list[str]:
    declared = {kind.kind_key for kind in kinds}
    return [
        f"{kind.kind_key}: requires_outcome_of names {referenced!r}, which no kind declares"
        for kind in kinds
        for referenced in kind.requires_outcome_of
        if referenced not in declared
    ]


def _dependency_cycles(kinds: Sequence[ComponentKind]) -> list[str]:
    """Report every dependency cycle once, as the path that closes it."""
    edges = {kind.kind_key: kind.requires_outcome_of for kind in kinds}
    settled: set[str] = set()
    reported: set[frozenset[str]] = set()
    problems: list[str] = []

    def walk(key: str, path: tuple[str, ...]) -> None:
        if key in path:
            cycle = path[path.index(key) :]
            if frozenset(cycle) not in reported:
                reported.add(frozenset(cycle))
                problems.append("dependency cycle: " + " -> ".join((*cycle, key)))
            return
        if key in settled:
            return
        for referenced in edges.get(key, ()):
            walk(referenced, (*path, key))
        settled.add(key)

    for kind in kinds:
        walk(kind.kind_key, ())
    return problems


def _require_text(value: str, field: str) -> None:
    if type(value) is not str or not value.strip():
        raise InvariantViolationError(f"{field} must be a non-empty string, got {value!r}")


def _require_key(value: str, field: str, pattern: re.Pattern[str]) -> None:
    _require_text(value, field)
    if not pattern.fullmatch(value):
        raise InvariantViolationError(f"{field} must match {pattern.pattern!r}, got {value!r}")
