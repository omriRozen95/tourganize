"""Reading the keyword interpreter's phrase tables from ``keywords.<locale>.yaml``.

The tables are **configuration**, not code, for two reasons. The first is the rule the whole
system is built on: no travel topic may be named in ``tourganize/``, and "this word means that
``kind_key``" is exactly such a naming — so the per-kind keyword lists live in a file next to
the Component Catalog that declares those kinds. The second is bilingualism: Hebrew phrasings,
Hebrew month names and Hebrew keywords are content, and content belongs in a locale file.

One file per Locale Tag, named for it. Unknown keys are refused rather than ignored, exactly as
in the catalog and schema readers: a misspelled ``intnets`` block would otherwise silently
produce an interpreter that understands nothing, and a conversation that answers ``clarify`` to
everything is a much harder thing to diagnose than a file that would not load.

Deliberately small. This interpreter is scaffolding — F08's Definition of Done includes swapping
it out by config — and a phrase table that grew into a grammar would be scaffolding nobody ever
replaced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final

from tourganize.dialogue import TurnIntent
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.yaml_subset import read_config_file

__all__ = [
    "KEYWORD_FILE_PATTERN",
    "SHAPES",
    "SHAPE_DATE_RANGE",
    "SHAPE_PLACE",
    "PhraseTable",
    "load_phrase_tables",
    "read_phrase_table",
]

#: Phrase-table files are named ``keywords.<locale>.yaml``; the locale is read from the name and
#: has to agree with the ``locale`` the file declares.
KEYWORD_FILE_PATTERN: Final = re.compile(r"keywords\.(?P<locale>[A-Za-z0-9_-]+)\.yaml")

#: The value shapes this interpreter can recognise, and therefore the keys its ``fields`` block
#: may map to a Requirement Schema field name. Two, on purpose: a lone date is ambiguous — a
#: check-in or a check-out? — and guessing is F08's job with a schema and a prompt, not a regex's.
SHAPE_PLACE: Final = "place"
SHAPE_DATE_RANGE: Final = "date_range"
SHAPES: Final = (SHAPE_PLACE, SHAPE_DATE_RANGE)

_DOCUMENT_KEYS: Final = frozenset(
    {"locale", "intents", "kinds", "fields", "place_markers", "range_separators", "months"}
)
_INTENT_VALUES: Final = frozenset(intent.value for intent in TurnIntent)


@dataclass(frozen=True, slots=True)
class PhraseTable:
    """One locale's phrases: what an utterance means, and what it may be about."""

    locale: str
    intents: Mapping[TurnIntent, tuple[str, ...]] = field(default_factory=dict)
    kinds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    fields: Mapping[str, str] = field(default_factory=dict)
    place_markers: tuple[str, ...] = ()
    range_separators: tuple[str, ...] = ()
    months: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Read-only views, so a table handed to an interpreter cannot be edited underneath it.
        for name in ("intents", "kinds", "fields", "months"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def field_for(self, shape: str) -> str | None:
        """The Requirement Schema field name this locale files ``shape`` under."""
        return self.fields.get(shape)


def load_phrase_tables(directory: Path) -> Mapping[str, PhraseTable]:
    """Read every ``keywords.<locale>.yaml`` in ``directory``, keyed by Locale Tag.

    Raises :class:`~tourganize.platform.errors.ConfigurationError` when the directory holds no
    table at all: an interpreter with no phrases is one that answers ``clarify`` to every turn,
    and that is a misconfigured installation rather than a quiet degradation.
    """
    if not directory.is_dir():
        raise ConfigurationError(
            f"{directory} is not a directory; TOURGANIZE_KEYWORD_CONFIG_DIR must point at the "
            f"keyword interpreter's phrase tables (keywords.<locale>.yaml)"
        )
    tables: dict[str, PhraseTable] = {}
    for path in sorted(directory.iterdir()):
        matched = KEYWORD_FILE_PATTERN.fullmatch(path.name)
        if matched is None or not path.is_file():
            continue
        table = read_phrase_table(path, expected_locale=matched.group("locale"))
        tables[table.locale] = table
    if not tables:
        raise ConfigurationError(
            f"{directory} declares no phrase table; expected at least one file named "
            f"keywords.<locale>.yaml"
        )
    return tables


def read_phrase_table(path: Path, *, expected_locale: str | None = None) -> PhraseTable:
    """Read, validate and freeze one phrase table, or raise ``ConfigurationError``."""
    document = read_config_file(path)
    if not isinstance(document, Mapping):
        raise ConfigurationError(f"invalid phrase table {path}: the file must be a mapping")
    unknown = sorted(str(key) for key in document if key not in _DOCUMENT_KEYS)
    if unknown:
        raise ConfigurationError(
            f"invalid phrase table {path}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected {', '.join(sorted(_DOCUMENT_KEYS))}"
        )
    locale = _text(document, "locale", path)
    if expected_locale is not None and locale != expected_locale:
        raise ConfigurationError(
            f"invalid phrase table {path}: it declares locale {locale!r}, but its file name "
            f"says {expected_locale!r}"
        )
    return PhraseTable(
        locale=locale,
        intents=_intents_of(document, path),
        kinds=_phrase_lists(document, "kinds", path),
        fields=_fields_of(document, path),
        place_markers=_phrases(document, "place_markers", path),
        range_separators=_phrases(document, "range_separators", path),
        months=_months_of(document, path),
    )


def _intents_of(document: Mapping[str, object], path: Path) -> Mapping[TurnIntent, tuple[str, ...]]:
    """Read the ``intents`` block, refusing any key that is not a Turn Intent."""
    declared = _phrase_lists(document, "intents", path)
    unknown = sorted(name for name in declared if name not in _INTENT_VALUES)
    if unknown:
        raise ConfigurationError(
            f"invalid phrase table {path}: intents names {', '.join(unknown)}, which is not a "
            f"Turn Intent; the intents are {', '.join(sorted(_INTENT_VALUES))}"
        )
    return {TurnIntent(name): phrases for name, phrases in declared.items()}


def _fields_of(document: Mapping[str, object], path: Path) -> Mapping[str, str]:
    """Read the ``fields`` block: value shape -> the schema field name it is filed under."""
    block = document.get("fields")
    if block is None:
        return {}
    if not isinstance(block, Mapping):
        raise ConfigurationError(f"invalid phrase table {path}: fields must be a mapping")
    mapping: dict[str, str] = {}
    for shape, name in block.items():
        if shape not in SHAPES:
            raise ConfigurationError(
                f"invalid phrase table {path}: fields names the shape {shape!r}, which this "
                f"interpreter cannot recognise; it knows {', '.join(SHAPES)}"
            )
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(
                f"invalid phrase table {path}: fields.{shape} must be a field name, got {name!r}"
            )
        mapping[str(shape)] = name.strip()
    return mapping


def _months_of(document: Mapping[str, object], path: Path) -> Mapping[str, int]:
    """Read the ``months`` block: a month name in this locale -> its number."""
    block = document.get("months")
    if block is None:
        return {}
    if not isinstance(block, Mapping):
        raise ConfigurationError(f"invalid phrase table {path}: months must be a mapping")
    mapping: dict[str, int] = {}
    for name, number in block.items():
        if type(number) is not int or not 1 <= number <= 12:
            raise ConfigurationError(
                f"invalid phrase table {path}: months.{name} must be a month number 1-12, "
                f"got {number!r}"
            )
        mapping[str(name).strip().lower()] = number
    return mapping


def _phrase_lists(
    document: Mapping[str, object], key: str, path: Path
) -> Mapping[str, tuple[str, ...]]:
    block = document.get(key)
    if block is None:
        return {}
    if not isinstance(block, Mapping):
        raise ConfigurationError(
            f"invalid phrase table {path}: {key} must be a mapping of name to phrase list"
        )
    return {
        str(name): _phrase_tuple(phrases, f"{key}.{name}", path) for name, phrases in block.items()
    }


def _phrases(document: Mapping[str, object], key: str, path: Path) -> tuple[str, ...]:
    if document.get(key) is None:
        return ()
    return _phrase_tuple(document[key], key, path)


def _phrase_tuple(value: object, where: str, path: Path) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ConfigurationError(f"invalid phrase table {path}: {where} must be a list")
    phrases: list[str] = []
    for item in value:
        if isinstance(item, int | float) and not isinstance(item, bool):
            # `- 1` in a phrase list is a number to the YAML reader; a phrase is text.
            phrases.append(str(item))
            continue
        if not isinstance(item, str) or not item.strip():
            raise ConfigurationError(
                f"invalid phrase table {path}: {where} must hold non-empty phrases, got {item!r}"
            )
        phrases.append(item.strip().lower())
    return tuple(phrases)


def _text(document: Mapping[str, object], key: str, path: Path) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"invalid phrase table {path}: {key} must be text, got {value!r}")
    return value.strip()
