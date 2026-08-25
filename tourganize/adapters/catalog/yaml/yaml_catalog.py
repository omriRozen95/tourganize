"""``YamlComponentCatalog`` — the Component Catalog as a file on disk.

The file is the only place a travel topic exists at all. Nothing in the Python tree names
one — a test asserts it — which is what makes adding a new kind a five-line edit to
``config/catalog/components.yaml`` and no code change whatsoever.

Two deliberate choices:

* **Loading is lazy and cached.** The catalog is read on first use, not in the Composition
  Root, so ``tourganize doctor`` can *report* a broken catalog as a failing check instead of
  dying while the container is being built. Once read, it is held: a conversation cannot see
  the catalog change underneath it mid-turn.
* **Unknown keys are refused.** A misspelled ``priorty_weight`` would otherwise be silently
  ignored and the kind would quietly take a default weight. The file is edited by hand, so it
  is read strictly and every problem is reported at once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, final

from tourganize.domain.catalog import ComponentKind, catalog_problems
from tourganize.domain.errors import InvariantViolationError, UnknownComponentKindError
from tourganize.platform.errors import CatalogError, ConfigurationError
from tourganize.platform.yaml_subset import read_config_file

__all__ = ["CATALOG_VERSION", "YamlComponentCatalog"]

#: The only catalog schema version this release understands. A file declaring anything else
#: is refused rather than read hopefully: the shape may change, and a silent misreading of a
#: future file is worse than a clear refusal.
CATALOG_VERSION: Final = 1

_DOCUMENT_KEYS: Final = frozenset({"version", "kinds"})
_KIND_KEYS: Final = frozenset(
    {
        "kind_key",
        "message_key",
        "priority_weight",
        "schema_key",
        "requires_outcome_of",
        "enabled",
    }
)
_REQUIRED_KIND_KEYS: Final = ("kind_key", "message_key", "priority_weight", "schema_key")


@final
class YamlComponentCatalog:
    """The Component Catalog declared in ``${TOURGANIZE_CATALOG_PATH}``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._kinds: tuple[ComponentKind, ...] | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def origin(self) -> str:
        """Where this catalog came from, for ``doctor`` and error messages."""
        return str(self._path)

    def kinds(self) -> tuple[ComponentKind, ...]:
        cached = self._kinds
        if cached is None:
            cached = _load(self._path)
            self._kinds = cached
        return cached

    def enabled_kinds(self) -> tuple[ComponentKind, ...]:
        return tuple(kind for kind in self.kinds() if kind.enabled)

    def kind(self, kind_key: str) -> ComponentKind:
        for kind in self.enabled_kinds():
            if kind.kind_key == kind_key:
                return kind
        disabled = {kind.kind_key for kind in self.kinds() if not kind.enabled}
        if kind_key in disabled:
            raise UnknownComponentKindError(
                f"Component Kind {kind_key!r} is declared in {self.origin} but disabled"
            )
        declared = ", ".join(kind.kind_key for kind in self.enabled_kinds()) or "none"
        raise UnknownComponentKindError(
            f"unknown Component Kind {kind_key!r}; {self.origin} declares {declared}"
        )

    def schema_for(self, kind_key: str) -> object:
        """Declared by the port, implemented by F03."""
        raise NotImplementedError(
            "Requirement Schemas arrive with F03; ComponentKind.schema_key names the schema "
            f"{kind_key!r} will resolve to."
        )


def _load(path: Path) -> tuple[ComponentKind, ...]:
    """Read, validate and freeze the catalog file, or raise ``CatalogError``."""
    try:
        document = read_config_file(path)
    except ConfigurationError as exc:
        raise CatalogError(f"the Component Catalog could not be read: {exc}") from exc

    entries = _entries(document, path)
    kinds: list[ComponentKind] = []
    problems: list[str] = []
    for position, entry in enumerate(entries, start=1):
        try:
            kinds.append(_kind_of(entry, position))
        except CatalogError as exc:
            problems.append(str(exc))
    problems += catalog_problems(kinds)
    if problems:
        raise CatalogError(f"invalid Component Catalog {path}: " + "; ".join(problems))
    return tuple(kinds)


def _entries(document: object, path: Path) -> tuple[Mapping[str, object], ...]:
    if not isinstance(document, Mapping):
        raise CatalogError(
            f"invalid Component Catalog {path}: the file must be a mapping with `version` "
            f"and `kinds` keys"
        )
    unknown = sorted(key for key in document if key not in _DOCUMENT_KEYS)
    if unknown:
        raise CatalogError(
            f"invalid Component Catalog {path}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected {', '.join(sorted(_DOCUMENT_KEYS))}"
        )
    version = document.get("version")
    if version != CATALOG_VERSION:
        raise CatalogError(
            f"invalid Component Catalog {path}: version {version!r} is not supported, "
            f"this release reads version {CATALOG_VERSION}"
        )
    declared = document.get("kinds")
    if declared is None:
        raise CatalogError(f"invalid Component Catalog {path}: it declares no `kinds` list")
    if not isinstance(declared, Sequence) or isinstance(declared, str):
        raise CatalogError(
            f"invalid Component Catalog {path}: `kinds` must be a list, got {declared!r}"
        )
    entries: list[Mapping[str, object]] = []
    for position, entry in enumerate(declared, start=1):
        if not isinstance(entry, Mapping):
            raise CatalogError(
                f"invalid Component Catalog {path}: kind {position} is not a mapping "
                f"({entry!r})"
            )
        entries.append(entry)
    return tuple(entries)


def _kind_of(entry: Mapping[str, object], position: int) -> ComponentKind:
    where = f"kind {position}"
    unknown = sorted(str(key) for key in entry if key not in _KIND_KEYS)
    if unknown:
        raise CatalogError(
            f"{where}: unknown key(s) {', '.join(unknown)}; a Component Kind declares "
            f"{', '.join(sorted(_KIND_KEYS))}"
        )
    missing = [key for key in _REQUIRED_KIND_KEYS if entry.get(key) is None]
    if missing:
        raise CatalogError(f"{where}: missing required key(s) {', '.join(missing)}")
    named = f"kind {position} ({entry['kind_key']!r})"
    try:
        return ComponentKind(
            kind_key=_text(entry, "kind_key", named),
            message_key=_text(entry, "message_key", named),
            priority_weight=_integer(entry, "priority_weight", named),
            schema_key=_text(entry, "schema_key", named),
            requires_outcome_of=_key_tuple(entry, "requires_outcome_of", named),
            enabled=_boolean(entry, "enabled", named, default=True),
        )
    except InvariantViolationError as exc:
        # The domain owns what a valid Component Kind is; this adapter owns saying which
        # entry of which file failed to be one.
        raise CatalogError(f"{named}: {exc}") from exc


def _text(entry: Mapping[str, object], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise CatalogError(f"{where}: {key} must be text, got {value!r}")
    return value


def _integer(entry: Mapping[str, object], key: str, where: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogError(f"{where}: {key} must be a whole number, got {value!r}")
    return value


def _boolean(entry: Mapping[str, object], key: str, where: str, *, default: bool) -> bool:
    value = entry.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CatalogError(f"{where}: {key} must be true or false, got {value!r}")
    return value


def _key_tuple(entry: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise CatalogError(f"{where}: {key} must be a list of kind_keys, got {value!r}")
    referenced: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise CatalogError(f"{where}: {key} must contain kind_keys, got {item!r}")
        referenced.append(item)
    return tuple(referenced)
