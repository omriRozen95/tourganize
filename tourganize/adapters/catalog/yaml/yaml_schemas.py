"""Reading Requirement Schemas from ``${TOURGANIZE_SCHEMA_DIR}/<schema_key>.yaml``.

The same bargain as the Component Catalog, for the same reason: what a valid schema *is*
belongs to the domain, and reading one — a path, a file, a YAML dialect, a line number —
belongs here. The domain half is
:func:`~tourganize.domain.requirements.schema.schema_problems` and the ``__post_init__`` of
:class:`~tourganize.domain.requirements.schema.FieldSpec`; this module turns their findings
into a :class:`~tourganize.platform.errors.SchemaError` that names the file.

Unknown keys are refused rather than ignored, exactly as in the catalog reader: a misspelled
``obligaton`` would otherwise quietly default a blocking field to optional, and the traveller
would be sourced options for a component nobody has the dates for.

The file name is part of the contract: a file named ``<key>.yaml`` must declare
``schema_key: <key>``, so that ``ComponentKind.schema_key`` resolves to a path with no lookup
table in between, and a renamed file cannot silently shadow the schema it replaced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, TypeVar

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    schema_problems,
)
from tourganize.platform.errors import ConfigurationError, SchemaError
from tourganize.platform.yaml_subset import read_config_file

__all__ = ["SCHEMA_FILE_SUFFIX", "load_schema", "schema_path"]

#: Requirement Schemas are files, one per ``schema_key``, with this suffix.
SCHEMA_FILE_SUFFIX: Final = ".yaml"

_DOCUMENT_KEYS: Final = frozenset({"schema_key", "component_kind", "fields", "blocking_rules"})
_FIELD_KEYS: Final = frozenset(
    {
        "name",
        "field_kind",
        "obligation",
        "prompt_message_key",
        "example_message_key",
        "enum_values",
        "constraints",
    }
)
_REQUIRED_FIELD_KEYS: Final = ("name", "field_kind", "obligation", "prompt_message_key")
_RULE_KEYS: Final = frozenset({"name", "any_of"})

_ChoiceT = TypeVar("_ChoiceT")

_FIELD_KINDS: Final[Mapping[str, FieldKind]] = {kind.value: kind for kind in FieldKind}
_OBLIGATIONS: Final[Mapping[str, Obligation]] = {item.value: item for item in Obligation}


def schema_path(schema_dir: Path, schema_key: str) -> Path:
    """Where the schema called ``schema_key`` is expected to be."""
    return schema_dir / f"{schema_key}{SCHEMA_FILE_SUFFIX}"


def load_schema(path: Path, *, expected_key: str | None = None) -> RequirementSchema:
    """Read, validate and freeze one Requirement Schema file, or raise ``SchemaError``."""
    try:
        document = read_config_file(path)
    except ConfigurationError as exc:
        raise SchemaError(f"the Requirement Schema could not be read: {exc}") from exc

    entries = _document_of(document, path)
    declared_key = _text(entries, "schema_key", str(path))
    if expected_key is not None and declared_key != expected_key:
        raise SchemaError(
            f"invalid Requirement Schema {path}: it declares schema_key {declared_key!r}, but "
            f"its file name says {expected_key!r}"
        )

    problems: list[str] = []
    fields = _fields_of(entries, path, problems)
    rules = _rules_of(entries, path, problems)
    if problems:
        raise SchemaError(f"invalid Requirement Schema {path}: " + "; ".join(problems))

    try:
        schema = RequirementSchema(
            schema_key=declared_key,
            component_kind=_text(entries, "component_kind", str(path)),
            fields=fields,
            blocking_rules=rules,
        )
    except InvariantViolationError as exc:
        raise SchemaError(f"invalid Requirement Schema {path}: {exc}") from exc

    found = schema_problems(schema)
    if found:
        raise SchemaError(f"invalid Requirement Schema {path}: " + "; ".join(found))
    return schema


def _document_of(document: object, path: Path) -> Mapping[str, object]:
    if not isinstance(document, Mapping):
        raise SchemaError(
            f"invalid Requirement Schema {path}: the file must be a mapping with "
            f"`schema_key`, `component_kind` and `fields` keys"
        )
    unknown = sorted(str(key) for key in document if key not in _DOCUMENT_KEYS)
    if unknown:
        raise SchemaError(
            f"invalid Requirement Schema {path}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected {', '.join(sorted(_DOCUMENT_KEYS))}"
        )
    required = ("schema_key", "component_kind", "fields")
    missing = [key for key in required if document.get(key) is None]
    if missing:
        raise SchemaError(
            f"invalid Requirement Schema {path}: missing required key(s) {', '.join(missing)}"
        )
    return document


def _fields_of(
    document: Mapping[str, object], path: Path, problems: list[str]
) -> tuple[FieldSpec, ...]:
    declared = _sequence(document, "fields", path)
    fields: list[FieldSpec] = []
    for position, entry in enumerate(declared, start=1):
        where = f"field {position}"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} is not a mapping ({entry!r})")
            continue
        try:
            fields.append(_field_of(entry, where))
        except SchemaError as exc:
            problems.append(str(exc))
    return tuple(fields)


def _field_of(entry: Mapping[str, object], where: str) -> FieldSpec:
    unknown = sorted(str(key) for key in entry if key not in _FIELD_KEYS)
    if unknown:
        raise SchemaError(
            f"{where}: unknown key(s) {', '.join(unknown)}; a field declares "
            f"{', '.join(sorted(_FIELD_KEYS))}"
        )
    missing = [key for key in _REQUIRED_FIELD_KEYS if entry.get(key) is None]
    if missing:
        raise SchemaError(f"{where}: missing required key(s) {', '.join(missing)}")
    named = f"{where} ({entry['name']!r})"
    try:
        return FieldSpec(
            name=_text(entry, "name", named),
            field_kind=_choice(entry, "field_kind", _FIELD_KINDS, named),
            obligation=_choice(entry, "obligation", _OBLIGATIONS, named),
            prompt_message_key=_text(entry, "prompt_message_key", named),
            example_message_key=_optional_text(entry, "example_message_key", named),
            enum_values=_text_tuple(entry, "enum_values", named),
            constraints=_constraints_of(entry, named),
        )
    except InvariantViolationError as exc:
        # The domain owns what a valid Field Spec is; this adapter owns saying which entry of
        # which file failed to be one.
        raise SchemaError(f"{named}: {exc}") from exc


def _rules_of(
    document: Mapping[str, object], path: Path, problems: list[str]
) -> tuple[BlockingRule, ...]:
    if document.get("blocking_rules") is None:
        return ()
    declared = _sequence(document, "blocking_rules", path)
    rules: list[BlockingRule] = []
    for position, entry in enumerate(declared, start=1):
        where = f"blocking rule {position}"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} is not a mapping ({entry!r})")
            continue
        try:
            rules.append(_rule_of(entry, where))
        except SchemaError as exc:
            problems.append(str(exc))
    return tuple(rules)


def _rule_of(entry: Mapping[str, object], where: str) -> BlockingRule:
    unknown = sorted(str(key) for key in entry if key not in _RULE_KEYS)
    if unknown:
        raise SchemaError(
            f"{where}: unknown key(s) {', '.join(unknown)}; a blocking rule declares "
            f"{', '.join(sorted(_RULE_KEYS))}"
        )
    missing = [key for key in sorted(_RULE_KEYS) if entry.get(key) is None]
    if missing:
        raise SchemaError(f"{where}: missing required key(s) {', '.join(missing)}")
    named = f"{where} ({entry['name']!r})"
    groups = entry["any_of"]
    if isinstance(groups, str) or not isinstance(groups, Sequence):
        raise SchemaError(f"{named}: any_of must be a list of field-name groups, got {groups!r}")
    try:
        return BlockingRule(
            name=_text(entry, "name", named),
            any_of=tuple(_group_of(group, named) for group in groups),
        )
    except InvariantViolationError as exc:
        raise SchemaError(f"{named}: {exc}") from exc


def _group_of(group: object, where: str) -> tuple[str, ...]:
    """One ``any_of`` group. A bare name is read as a group of one, because it reads better."""
    if isinstance(group, str):
        return (group,)
    if not isinstance(group, Sequence):
        raise SchemaError(
            f"{where}: each any_of group must be a list of field names, got {group!r}"
        )
    names: list[str] = []
    for name in group:
        if not isinstance(name, str):
            raise SchemaError(f"{where}: an any_of group must contain field names, got {name!r}")
        names.append(name)
    return tuple(names)


def _sequence(document: Mapping[str, object], key: str, path: Path) -> Sequence[object]:
    value = document.get(key)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError(
            f"invalid Requirement Schema {path}: `{key}` must be a list, got {value!r}"
        )
    return value


def _text(entry: Mapping[str, object], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise SchemaError(f"{where}: {key} must be text, got {value!r}")
    return value


def _optional_text(entry: Mapping[str, object], key: str, where: str) -> str | None:
    if entry.get(key) is None:
        return None
    return _text(entry, key, where)


def _text_tuple(entry: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SchemaError(f"{where}: {key} must be a list, got {value!r}")
    for item in value:
        if not isinstance(item, str):
            raise SchemaError(f"{where}: {key} must contain text, got {item!r}")
    return tuple(str(item) for item in value)


def _constraints_of(entry: Mapping[str, object], where: str) -> Mapping[str, object]:
    value = entry.get("constraints")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SchemaError(f"{where}: constraints must be a mapping, got {value!r}")
    return value


def _choice(
    entry: Mapping[str, object], key: str, allowed: Mapping[str, _ChoiceT], where: str
) -> _ChoiceT:
    spelled = _text(entry, key, where)
    chosen = allowed.get(spelled)
    if chosen is None:
        raise SchemaError(
            f"{where}: {key} must be one of {', '.join(sorted(allowed))}, got {spelled!r}"
        )
    return chosen
