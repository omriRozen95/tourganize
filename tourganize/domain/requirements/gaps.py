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

``is_plannable`` is ``not blocking and not invalid``, and it is *the* gate: sourcing starts
when it is true and elicitation continues while it is false.

Ask ordering lives here rather than in the dialogue because it is a fact about the schema, not
about the conversation: gaps come back in the schema's Blocking Rule declaration order, so
:meth:`GapReport.next_blocking` is simply the first of them. What the dialogue does with one
gap per turn is F05's business.
"""

from __future__ import annotations

from dataclasses import dataclass

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_text
from tourganize.domain.requirements.schema import FieldSpec, RequirementSchema
from tourganize.domain.requirements.validation import invalid_reason
from tourganize.domain.requirements.values import RequirementSet

__all__ = ["BlockingGap", "GapReport", "InvalidValue", "analyse"]


@dataclass(frozen=True, slots=True)
class InvalidValue:
    """A value that is present but cannot be used, and why.

    ``reason_message_key`` is what the traveller is told, in their own language. ``detail`` is
    English and diagnostic — it belongs in a log line or a CLI report, never in an Assistant
    Act payload.
    """

    field_name: str
    reason_message_key: str
    detail: str

    def __post_init__(self) -> None:
        require_text(self.field_name, "InvalidValue.field_name")
        require_text(self.reason_message_key, "InvalidValue.reason_message_key")


@dataclass(frozen=True, slots=True)
class BlockingGap:
    """One unsatisfied Blocking Rule, and every combination that would satisfy it.

    ``candidates`` and ``missing`` are parallel: ``candidates[i]`` is the ``i``-th group of the
    rule as Field Specs, and ``missing[i]`` names the fields of that group that still hold no
    value. Every group has at least one missing field — that is what makes the rule a gap.
    """

    rule_name: str
    candidates: tuple[tuple[FieldSpec, ...], ...]
    missing: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        require_text(self.rule_name, "BlockingGap.rule_name")
        if not self.candidates or len(self.candidates) != len(self.missing):
            raise InvariantViolationError(
                f"blocking gap {self.rule_name!r}: candidates and missing must be non-empty "
                f"and the same length, got {len(self.candidates)} and {len(self.missing)}"
            )
        if any(not names for names in self.missing):
            raise InvariantViolationError(
                f"blocking gap {self.rule_name!r}: a group with nothing missing satisfies the "
                f"rule, so it is not a gap"
            )

    @property
    def field_names(self) -> tuple[tuple[str, ...], ...]:
        """The candidate field groups by name — what F05 stores on its pending question."""
        return tuple(tuple(spec.name for spec in group) for group in self.candidates)

    @property
    def prompt_message_keys(self) -> tuple[str, ...]:
        """The question key of every field still missing, nearest group first, deduplicated."""
        keys: list[str] = []
        for index in self._by_distance():
            for spec in self.candidates[index]:
                if spec.name in self.missing[index] and spec.prompt_message_key not in keys:
                    keys.append(spec.prompt_message_key)
        return tuple(keys)

    def nearest_candidate(self) -> tuple[FieldSpec, ...]:
        """The group closest to being satisfied.

        Nearest rather than simply first, because a traveller who has already answered half of
        one group should be asked for the other half, not sent back to an alternative they
        chose not to give. Ordered by: fewest fields still missing, then most fields already
        answered, then the order the schema declares the groups in.
        """
        return self.candidates[self._by_distance()[0]]

    def next_field(self) -> FieldSpec:
        """The single field to ask about: the first missing one of the nearest group."""
        index = self._by_distance()[0]
        missing = self.missing[index]
        return next(spec for spec in self.candidates[index] if spec.name in missing)

    def _by_distance(self) -> tuple[int, ...]:
        """Candidate indices, nearest first. See :meth:`nearest_candidate` for the ordering."""

        def distance(index: int) -> tuple[int, int, int]:
            answered = len(self.candidates[index]) - len(self.missing[index])
            return len(self.missing[index]), -answered, index

        return tuple(sorted(range(len(self.candidates)), key=distance))


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
        """True when sourcing may start: nothing blocking is missing and nothing present is bad."""
        return not self.blocking and not self.invalid

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
        missing = tuple(tuple(name for name in group if name not in held) for group in rule.any_of)
        if any(not names for names in missing):
            continue  # one group is fully present: the rule is satisfied, however badly
        gaps.append(
            BlockingGap(
                rule_name=rule.name,
                candidates=tuple(_specs_of(schema, group) for group in rule.any_of),
                missing=missing,
            )
        )
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
    findings: list[InvalidValue] = []
    for spec in schema.fields:
        value = held.provenance_of(spec.name)
        if value is None:
            continue
        reason = invalid_reason(spec, value.value)
        if reason is not None:
            findings.append(InvalidValue(spec.name, reason[0], reason[1]))
    return tuple(findings)
