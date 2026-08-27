"""Requirement Schemas: what has to be known before a Plan Component can be planned.

A Requirement Schema is the per-Component-Kind declaration of the fields that describe the
traveller's wish. Like the Component Catalog it is **data** — a file under
``${TOURGANIZE_SCHEMA_DIR}`` — and like the catalog, what a *valid* schema is lives here while
the reading of one lives in an adapter.

Two shapes carry the obligation, and it is worth being clear about why there are two:

* :class:`Obligation` is a property of a single Field Spec. ``BLOCKING`` means "planning may
  not start without knowing this"; ``OPTIONAL`` means "a filter, asked opportunistically,
  never blocking". Exactly two values, forever.
* :class:`BlockingRule` is how a blocking obligation is actually *satisfied*, and it is a set
  of alternatives rather than a flag. The client's own example is the reason: "there should be
  some time range, if not a specific start and end date". One rule named ``when``, satisfied by
  ``date_range`` **or** by ``starts_on`` **and** ``ends_on``. A per-field boolean cannot say
  that; ``any_of: (("date_range",), ("starts_on", "ends_on"))`` says exactly that.

A group in ``any_of`` may name a field whose own Obligation is ``OPTIONAL`` — ``starts_on`` is
one — because the field is not required *by itself*; it is one of the ways a rule can be met.
The reverse does not hold: a field declared ``BLOCKING`` that no rule references would be an
obligation nothing enforces, so :func:`schema_problems` reports it.

:func:`schema_problems` follows the precedent set by
:func:`~tourganize.domain.catalog.kinds.catalog_problems`: it *returns* findings rather than
raising, because the rules are the domain's and the exception — with its file and line
context — belongs to whichever adapter read the file.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import MESSAGE_KEY_PATTERN, require_key, require_text

__all__ = [
    "CONSTRAINT_KEYS",
    "FIELD_NAME_PATTERN",
    "BlockingRule",
    "FieldKind",
    "FieldSpec",
    "Obligation",
    "RequirementSchema",
    "schema_problems",
]

#: A field name is lower snake case, for the same reason a ``kind_key`` is: it appears in
#: message keys, telemetry fields, extraction schemas (F08) and JSON on the command line.
FIELD_NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")

_SCHEMA_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*\.v[0-9]+")
_RULE_NAME_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")

#: The constraint keys this release *understands*. ``constraints`` is an open bag —
#: ``Mapping[str, object]`` — so that a new Field Kind can read a bound nobody has invented
#: yet without every older schema having to be re-validated; these two are simply the ones
#: whose values are checked here, because they are the ones a validator acts on.
CONSTRAINT_KEYS: Final = frozenset({"min", "max"})


class FieldKind(Enum):
    """The type of one requirement field, and therefore which validator reads it."""

    DATE_RANGE = "date_range"
    DATE = "date"
    PLACE = "place"
    INTEGER = "integer"
    MONEY = "money"
    SCORE = "score"
    TEXT = "text"
    ENUM = "enum"
    BOOLEAN = "boolean"
    DURATION = "duration"


class Obligation(Enum):
    """Whether a field blocks planning, or merely filters it.

    Exactly two values. A third — "sometimes blocking" — is what :class:`BlockingRule` is for.
    """

    BLOCKING = "blocking"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One declared field of a Requirement Schema. Data, validated at construction."""

    name: str
    field_kind: FieldKind
    obligation: Obligation
    prompt_message_key: str
    example_message_key: str | None = None
    enum_values: tuple[str, ...] = ()
    constraints: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_key(self.name, "FieldSpec.name", FIELD_NAME_PATTERN)
        if not isinstance(self.field_kind, FieldKind):
            raise InvariantViolationError(
                f"{self.name}: field_kind must be a FieldKind, got {self.field_kind!r}"
            )
        if not isinstance(self.obligation, Obligation):
            raise InvariantViolationError(
                f"{self.name}: obligation must be an Obligation, got {self.obligation!r}"
            )
        # Every field carries the message key of the question that asks for it, because a gap
        # the dialogue cannot phrase a question for is a gap that can never be closed.
        require_key(self.prompt_message_key, f"{self.name}.prompt_message_key", MESSAGE_KEY_PATTERN)
        if self.example_message_key is not None:
            require_key(
                self.example_message_key, f"{self.name}.example_message_key", MESSAGE_KEY_PATTERN
            )
        _require_enum_values(self)
        object.__setattr__(self, "constraints", _checked_constraints(self))

    @property
    def is_blocking(self) -> bool:
        return self.obligation is Obligation.BLOCKING

    def constraint(self, key: str) -> object | None:
        """One declared bound, or ``None`` when the field does not declare it."""
        return self.constraints.get(key)


@dataclass(frozen=True, slots=True)
class BlockingRule:
    """One obligation, and every combination of fields that would satisfy it.

    ``any_of`` is read as a disjunction of conjunctions: the rule is met when **all** the
    fields of **any one** group hold a value. Groups are kept in declaration order, which is
    the order the dialogue prefers them in.
    """

    name: str
    any_of: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        require_key(self.name, "BlockingRule.name", _RULE_NAME_PATTERN)
        if type(self.any_of) is not tuple or not self.any_of:
            raise InvariantViolationError(
                f"blocking rule {self.name!r}: any_of must be a non-empty tuple of field "
                f"groups, got {self.any_of!r}"
            )
        for group in self.any_of:
            if type(group) is not tuple or not group:
                raise InvariantViolationError(
                    f"blocking rule {self.name!r}: every any_of group must be a non-empty "
                    f"tuple of field names, got {group!r}"
                )
            for name in group:
                require_key(name, f"blocking rule {self.name!r}", FIELD_NAME_PATTERN)
            if len(set(group)) != len(group):
                raise InvariantViolationError(
                    f"blocking rule {self.name!r}: group {group!r} repeats a field name"
                )

    @property
    def referenced_fields(self) -> frozenset[str]:
        """Every field name any group of this rule names."""
        return frozenset(name for group in self.any_of for name in group)


@dataclass(frozen=True, slots=True)
class RequirementSchema:
    """The declared requirements of one Component Kind."""

    schema_key: str
    component_kind: str
    fields: tuple[FieldSpec, ...] = ()
    blocking_rules: tuple[BlockingRule, ...] = ()

    def __post_init__(self) -> None:
        require_key(self.schema_key, "RequirementSchema.schema_key", _SCHEMA_KEY_PATTERN)
        require_key(self.component_kind, "RequirementSchema.component_kind", FIELD_NAME_PATTERN)
        if type(self.fields) is not tuple:
            raise InvariantViolationError(
                f"{self.schema_key}: fields must be a tuple of FieldSpec, got {self.fields!r}"
            )
        if type(self.blocking_rules) is not tuple:
            raise InvariantViolationError(
                f"{self.schema_key}: blocking_rules must be a tuple of BlockingRule, "
                f"got {self.blocking_rules!r}"
            )
        if not self.blocking_rules:
            object.__setattr__(self, "blocking_rules", _derived_rules(self.fields))

    def field(self, name: str) -> FieldSpec | None:
        """The Field Spec called ``name``, or ``None`` when the schema does not declare it."""
        for spec in self.fields:
            if spec.name == name:
                return spec
        return None

    def declares(self, name: str) -> bool:
        return self.field(name) is not None

    def blocking_fields(self) -> tuple[FieldSpec, ...]:
        """The fields declared ``blocking``, in declaration order."""
        return tuple(spec for spec in self.fields if spec.obligation is Obligation.BLOCKING)

    def optional_fields(self) -> tuple[FieldSpec, ...]:
        """The fields declared ``optional``, in declaration order."""
        return tuple(spec for spec in self.fields if spec.obligation is Obligation.OPTIONAL)

    def rule(self, name: str) -> BlockingRule | None:
        """The Blocking Rule called ``name``, or ``None``."""
        for rule in self.blocking_rules:
            if rule.name == name:
                return rule
        return None

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.fields)


def schema_problems(schema: RequirementSchema) -> tuple[str, ...]:
    """Return one message per broken schema invariant — empty when the schema is sound.

    Four things make a schema unusable, and all four are reported at once rather than one per
    round trip, because schemas are edited by hand:

    * two fields claiming the same name, or two rules the same name;
    * a Blocking Rule group naming a field nobody declares — the rule could then never be
      satisfied, and the traveller would be asked for something the schema cannot describe;
    * a field declared ``blocking`` that no rule references — an obligation nothing enforces;
    * a schema that declares fields but no Blocking Rule at all. Nothing gates planning, so
      the component is Plannable from the first turn and would be sourced against an empty
      Requirement Set — options for a wish nobody has stated yet. A schema that genuinely
      needs nothing known declares no fields either, and is left alone.

    "Every field has a ``prompt_message_key``" and "an enum field declares values" are
    enforced by :class:`FieldSpec` itself, at construction, because a Field Spec without them
    is not a thing that should exist even for a moment.
    """
    problems = [*_duplicate_field_names(schema), *_duplicate_rule_names(schema)]
    declared = set(schema.field_names)
    for rule in schema.blocking_rules:
        problems += [
            f"blocking rule {rule.name!r} names {name!r}, which the schema does not declare"
            for name in sorted(rule.referenced_fields - declared)
        ]
    referenced = {name for rule in schema.blocking_rules for name in rule.referenced_fields}
    problems += [
        f"field {spec.name!r} is blocking but no blocking rule references it"
        for spec in schema.blocking_fields()
        if spec.name not in referenced
    ]
    if schema.fields and not schema.blocking_rules:
        problems.append(
            "the schema declares fields but no blocking rule, so nothing has to be known "
            "before planning starts"
        )
    return tuple(problems)


def _duplicate_field_names(schema: RequirementSchema) -> list[str]:
    return [f"duplicate field name {name!r}" for name in _repeated(schema.field_names)]


def _duplicate_rule_names(schema: RequirementSchema) -> list[str]:
    names = tuple(rule.name for rule in schema.blocking_rules)
    return [f"duplicate blocking rule name {name!r}" for name in _repeated(names)]


def _repeated(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for name in names:
        if name in seen and name not in repeated:
            repeated.append(name)
        seen.add(name)
    return repeated


def _derived_rules(fields: Sequence[FieldSpec]) -> tuple[BlockingRule, ...]:
    """One single-field rule per blocking field, for a schema that declares no rules.

    The ``any_of`` model is the only model — this is not a second one. It is the answer to
    "what does a schema mean when every obligation is satisfied one field at a time?", which
    is the common case and should not have to be written out twice.
    """
    return tuple(
        BlockingRule(spec.name, ((spec.name,),))
        for spec in fields
        if spec.obligation is Obligation.BLOCKING
    )


def _require_enum_values(spec: FieldSpec) -> None:
    if type(spec.enum_values) is not tuple:
        raise InvariantViolationError(
            f"{spec.name}: enum_values must be a tuple of strings, got {spec.enum_values!r}"
        )
    for value in spec.enum_values:
        require_text(value, f"{spec.name}.enum_values")
    if spec.field_kind is FieldKind.ENUM and not spec.enum_values:
        raise InvariantViolationError(
            f"{spec.name}: an enum field must declare its enum_values, or nothing can ever "
            f"satisfy it"
        )
    if spec.field_kind is not FieldKind.ENUM and spec.enum_values:
        raise InvariantViolationError(
            f"{spec.name}: enum_values are only meaningful on an enum field, "
            f"not on {spec.field_kind.value}"
        )
    if len(set(spec.enum_values)) != len(spec.enum_values):
        raise InvariantViolationError(f"{spec.name}: enum_values repeats a value")


def _checked_constraints(spec: FieldSpec) -> Mapping[str, object]:
    """Return the declared constraints as a read-only mapping, or raise.

    The constraints this release understands — :data:`CONSTRAINT_KEYS` — are checked; anything
    else is carried through untouched. That asymmetry is deliberate. ``min: "one"`` is a
    mistake nobody meant, and refusing it costs a round trip; a key this release has never
    heard of is what a *newer* Field Kind looks like from here, and refusing that would make
    "adding a Field Kind is additive" untrue for every schema file already written. A
    constraint on a kind that does not read it is inert, not wrong, for the same reason.
    """
    if not isinstance(spec.constraints, Mapping):
        raise InvariantViolationError(
            f"{spec.name}: constraints must be a mapping, got {spec.constraints!r}"
        )
    declared: dict[str, object] = {}
    for key in sorted(spec.constraints, key=str):
        value = spec.constraints[key]
        if key in CONSTRAINT_KEYS and (
            isinstance(value, bool) or not isinstance(value, int | float)
        ):
            raise InvariantViolationError(
                f"{spec.name}: constraint {key} must be a number, got {value!r}"
            )
        declared[str(key)] = value
    low, high = declared.get("min"), declared.get("max")
    if isinstance(low, int | float) and isinstance(high, int | float) and low > high:
        raise InvariantViolationError(f"{spec.name}: constraint min {low} is above max {high}")
    return MappingProxyType(declared)
