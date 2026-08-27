"""Gap analysis: exactly what is still missing before a Plan Component can be planned.

:func:`analyse` is a pure function of a Requirement Schema and a Requirement Set. It is
deliberately **not** a port: there is nothing to swap out and nothing outside to reach, and
making it a port would invite an adapter to decide what "plannable" means — which is the one
decision the dialogue must be able to trust ([D2](../../docs/architecture/decisions.md)).

Three lists come out, and the difference between two of them is the subtle part:

* ``blocking`` — Blocking Rules that **no** candidate field group satisfies. A rule counts as
  satisfied as soon as one group is fully *present*, whether or not the values are any good.
* ``invalid`` — values that are present and fail their Field Kind's validation. A reversed date
  range is not a *missing* date range, and reporting it as one would have the assistant ask a
  question the traveller has already answered. It asks a different question instead: "that
  range ends before it starts".
* ``optional`` — declared optional fields with no value. These are filters. They are never
  blocking, they are asked at most once, and they are bundled alongside the first slate (F05).

``is_plannable`` is *the* gate: sourcing starts when it is true and elicitation continues
while it is false. It is false while any Blocking Rule is unsatisfied, and false while a value
that a Blocking Rule *reads* is invalid — a rule satisfied by a date nobody can parse would
send an empty search out. An invalid **optional filter** does not hold planning up, because
"optional filters never block" is a hard rule of this system: it is still reported, and still
re-asked, alongside the first slate rather than instead of it.

Ask ordering lives here rather than in the dialogue because it is a fact about the schema, not
about the conversation: gaps come back in the schema's Blocking Rule declaration order, so
:meth:`GapReport.next_blocking` is simply the first of them. *Which* of a rule's candidate
groups to pursue, and which field of it to ask for, is asking policy and belongs to F05 — this
module hands it every group with its own missing fields and stops there.
"""

from __future__ import annotations

from dataclasses import dataclass

from tourganize.domain.errors import InvariantViolationError, RequirementValueError
from tourganize.domain.invariants import require_text
from tourganize.domain.requirements.schema import FieldSpec, RequirementSchema
from tourganize.domain.requirements.validation import normalise
from tourganize.domain.requirements.values import RequirementSet

__all__ = ["BlockingGap", "CandidateGroup", "GapReport", "InvalidValue", "analyse"]


@dataclass(frozen=True, slots=True)
class InvalidValue:
    """A value that is present but cannot be used, and why.

    ``reason_message_key`` is what the traveller is told, in their own language. ``detail`` is
    English and diagnostic — it belongs in a log line or a CLI report, never in an Assistant
    Act payload. ``blocks`` says whether planning waits for it: true when a Blocking Rule
    reads this field, false when it is only a filter.
    """

    field_name: str
    reason_message_key: str
    detail: str
    blocks: bool

    def __post_init__(self) -> None:
        require_text(self.field_name, "InvalidValue.field_name")
        require_text(self.reason_message_key, "InvalidValue.reason_message_key")
        if not isinstance(self.blocks, bool):
            raise InvariantViolationError(
                f"{self.field_name}: blocks must be true or false, got {self.blocks!r}"
            )


@dataclass(frozen=True, slots=True)
class CandidateGroup:
    """One way a Blocking Rule could be satisfied, and what is still missing from it.

    The fields and their missing names travel together rather than as two lists the caller
    has to keep in step: a group whose ``missing`` belonged to a different group is a bug that
    cannot be written here.
    """

    fields: tuple[FieldSpec, ...]
    missing: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.fields) is not tuple or not self.fields:
            raise InvariantViolationError(
                f"a candidate group must name at least one field, got {self.fields!r}"
            )
        if type(self.missing) is not tuple or not self.missing:
            raise InvariantViolationError(
                "a candidate group with nothing missing satisfies its rule, so it is not a gap"
            )
        declared = {spec.name for spec in self.fields}
        unknown = sorted(name for name in self.missing if name not in declared)
        if unknown:
            raise InvariantViolationError(
                f"candidate group {sorted(declared)}: missing names {', '.join(unknown)}, "
                f"which the group does not contain"
            )

    @property
    def field_names(self) -> tuple[str, ...]:
        """Every field of this group, in the order the rule declares them."""
        return tuple(spec.name for spec in self.fields)

    @property
    def missing_fields(self) -> tuple[FieldSpec, ...]:
        """The Field Specs of this group that still hold no value, in declaration order."""
        return tuple(spec for spec in self.fields if spec.name in self.missing)


@dataclass(frozen=True, slots=True)
class BlockingGap:
    """One unsatisfied Blocking Rule, and every combination that would satisfy it.

    Every group has at least one missing field — that is what makes the rule a gap. The groups
    are in the schema's declaration order, and stay in it: preferring the group a traveller has
    half-answered is an asking policy, and asking policy is F05's.
    """

    rule_name: str
    candidates: tuple[CandidateGroup, ...]

    def __post_init__(self) -> None:
        require_text(self.rule_name, "BlockingGap.rule_name")
        if type(self.candidates) is not tuple or not self.candidates:
            raise InvariantViolationError(
                f"blocking gap {self.rule_name!r}: a rule with no candidate group could never "
                f"be satisfied, got {self.candidates!r}"
            )

    @property
    def field_names(self) -> tuple[tuple[str, ...], ...]:
        """The candidate field groups by name — what F05 stores on its pending question."""
        return tuple(group.field_names for group in self.candidates)

    @property
    def prompt_message_keys(self) -> tuple[str, ...]:
        """The question key of every field still missing, in declaration order, deduplicated."""
        keys: list[str] = []
        for group in self.candidates:
            keys += [
                spec.prompt_message_key
                for spec in group.missing_fields
                if spec.prompt_message_key not in keys
            ]
        return tuple(keys)


@dataclass(frozen=True, slots=True)
class GapReport:
    """What one Plan Component still needs, split the way the dialogue asks for it."""

    component_kind: str
    blocking: tuple[BlockingGap, ...] = ()
    optional: tuple[FieldSpec, ...] = ()
    invalid: tuple[InvalidValue, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.component_kind, "GapReport.component_kind")

    @property
    def is_plannable(self) -> bool:
        """True when sourcing may start: nothing blocking is missing, nothing it reads is bad."""
        return not self.blocking and not self.blocking_invalid

    @property
    def blocking_invalid(self) -> tuple[InvalidValue, ...]:
        """The invalid values that hold planning up — the ones a Blocking Rule reads."""
        return tuple(bad for bad in self.invalid if bad.blocks)

    def next_blocking(self) -> BlockingGap | None:
        """The single most useful blocking gap to ask about, or ``None`` when there is none."""
        return self.blocking[0] if self.blocking else None

    @property
    def blocking_rule_names(self) -> tuple[str, ...]:
        return tuple(gap.rule_name for gap in self.blocking)

    @property
    def optional_field_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.optional)

    @property
    def invalid_field_names(self) -> tuple[str, ...]:
        return tuple(bad.field_name for bad in self.invalid)


def analyse(schema: RequirementSchema, requirement_set: RequirementSet) -> GapReport:
    """Return the Gap Report of ``requirement_set`` against ``schema``.

    Pure and total: it never raises over a traveller's data, only over a caller's — handing it
    a Requirement Set that belongs to another Component Kind is a programming error, not a
    conversation that went wrong.
    """
    if schema.component_kind != requirement_set.component_kind:
        raise InvariantViolationError(
            f"schema {schema.schema_key} describes {schema.component_kind!r}, but the "
            f"Requirement Set belongs to {requirement_set.component_kind!r}"
        )
    return GapReport(
        component_kind=schema.component_kind,
        blocking=_blocking_gaps(schema, requirement_set),
        optional=tuple(
            spec for spec in schema.optional_fields() if spec.name not in requirement_set
        ),
        invalid=_invalid_values(schema, requirement_set),
    )


def _blocking_gaps(schema: RequirementSchema, held: RequirementSet) -> tuple[BlockingGap, ...]:
    gaps: list[BlockingGap] = []
    for rule in schema.blocking_rules:
        groups = [
            CandidateGroup(
                fields=_specs_of(schema, group),
                missing=tuple(name for name in group if name not in held),
            )
            for group in rule.any_of
            if any(name not in held for name in group)
        ]
        if len(groups) == len(rule.any_of):  # every group is short of something
            gaps.append(BlockingGap(rule_name=rule.name, candidates=tuple(groups)))
    return tuple(gaps)


def _specs_of(schema: RequirementSchema, group: tuple[str, ...]) -> tuple[FieldSpec, ...]:
    specs: list[FieldSpec] = []
    for name in group:
        spec = schema.field(name)
        if spec is None:
            # schema_problems() reports this at load; an adapter never hands one over.
            raise InvariantViolationError(
                f"schema {schema.schema_key}: a blocking rule names {name!r}, which the schema "
                f"does not declare"
            )
        specs.append(spec)
    return tuple(specs)


def _invalid_values(schema: RequirementSchema, held: RequirementSet) -> tuple[InvalidValue, ...]:
    """Findings in schema declaration order, so a report reads the way the schema is written."""
    gating = _gating_field_names(schema)
    findings: list[InvalidValue] = []
    for spec in schema.fields:
        value = held.provenance_of(spec.name)
        if value is None:
            continue
        try:
            normalise(spec, value.value)
        except RequirementValueError as refusal:
            findings.append(
                InvalidValue(
                    field_name=refusal.field_name,
                    reason_message_key=refusal.reason_message_key,
                    detail=refusal.detail,
                    blocks=spec.name in gating,
                )
            )
    return tuple(findings)


def _gating_field_names(schema: RequirementSchema) -> frozenset[str]:
    """The fields planning waits on: every one a Blocking Rule reads, plus every blocking field.

    The two overlap in any schema that loads — ``schema_problems`` refuses a blocking field no
    rule references — but a schema built in code need not have been through that check, and a
    field the schema itself calls blocking must gate planning whatever the rules say.
    """
    referenced = {name for rule in schema.blocking_rules for name in rule.referenced_fields}
    return frozenset(referenced | {spec.name for spec in schema.blocking_fields()})
