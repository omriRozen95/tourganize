"""What a Requirement Schema is, and every way one can be malformed."""

from __future__ import annotations

import pytest

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    schema_problems,
)


def field(
    name: str,
    kind: FieldKind = FieldKind.TEXT,
    obligation: Obligation = Obligation.OPTIONAL,
    **extra: object,
) -> FieldSpec:
    return FieldSpec(
        name=name,
        field_kind=kind,
        obligation=obligation,
        prompt_message_key=f"ask.alpha.{name}",
        **extra,  # type: ignore[arg-type]
    )


PLACE = field("place", FieldKind.PLACE, Obligation.BLOCKING)
RANGE = field("date_range", FieldKind.DATE_RANGE, Obligation.BLOCKING)
STARTS = field("starts_on", FieldKind.DATE)
ENDS = field("ends_on", FieldKind.DATE)

#: The client's own rule, in one schema: a range, *or* an explicit pair, satisfies "when".
SAMPLE = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(PLACE, RANGE, STARTS, ENDS),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",), ("starts_on", "ends_on"))),
    ),
)


def test_fields_are_split_by_obligation_in_declaration_order() -> None:
    assert [spec.name for spec in SAMPLE.blocking_fields()] == ["place", "date_range"]
    assert [spec.name for spec in SAMPLE.optional_fields()] == ["starts_on", "ends_on"]


def test_one_field_is_reachable_by_name() -> None:
    assert SAMPLE.field("place") is PLACE
    assert SAMPLE.field("nowhere") is None
    assert SAMPLE.declares("date_range")
    assert not SAMPLE.declares("nowhere")


def test_one_rule_is_reachable_by_name() -> None:
    rule = SAMPLE.rule("when")

    assert rule is not None
    assert rule.any_of == (("date_range",), ("starts_on", "ends_on"))
    assert rule.referenced_fields == frozenset({"date_range", "starts_on", "ends_on"})
    assert SAMPLE.rule("nowhere") is None


def test_a_sound_schema_has_no_problems() -> None:
    assert schema_problems(SAMPLE) == ()


def test_an_optional_field_may_satisfy_a_blocking_rule() -> None:
    """The client's example: `starts_on`/`ends_on` are filters *and* a way to satisfy `when`."""
    assert STARTS.obligation is Obligation.OPTIONAL
    assert "starts_on" in SAMPLE.rule("when").any_of[1]  # type: ignore[union-attr]
    assert schema_problems(SAMPLE) == ()


def test_a_schema_with_no_rules_derives_one_per_blocking_field() -> None:
    """The common case is one field per obligation, and it should not have to be written twice."""
    derived = RequirementSchema("alpha.v1", "alpha", (PLACE, RANGE, STARTS))

    assert [rule.name for rule in derived.blocking_rules] == ["place", "date_range"]
    assert derived.blocking_rules[0].any_of == (("place",),)
    assert schema_problems(derived) == ()


def test_a_rule_naming_an_undeclared_field_is_a_problem() -> None:
    broken = RequirementSchema(
        "alpha.v1", "alpha", (PLACE,), (BlockingRule("where", (("place", "nowhere"),)),)
    )

    assert schema_problems(broken) == (
        "blocking rule 'where' names 'nowhere', which the schema does not declare",
    )


def test_a_blocking_field_no_rule_references_is_a_problem() -> None:
    """An obligation nothing enforces would never be asked for and never block anything."""
    broken = RequirementSchema(
        "alpha.v1", "alpha", (PLACE, RANGE), (BlockingRule("where", (("place",),)),)
    )

    assert schema_problems(broken) == (
        "field 'date_range' is blocking but no blocking rule references it",
    )


def test_a_schema_of_filters_alone_has_nothing_to_gate_planning() -> None:
    """Every field optional and no rule: Plannable on turn one, sourced against nothing."""
    filters_only = RequirementSchema("alpha.v1", "alpha", (STARTS, ENDS))

    assert schema_problems(filters_only) == (
        "the schema declares fields but no blocking rule, so nothing has to be known "
        "before planning starts",
    )


def test_a_schema_that_declares_no_fields_at_all_is_left_alone() -> None:
    """Nothing to know is only a problem when the schema says there *is* something to know."""
    assert schema_problems(RequirementSchema("alpha.v1", "alpha")) == ()


def test_duplicate_names_are_reported_once_each() -> None:
    broken = RequirementSchema(
        "alpha.v1",
        "alpha",
        (PLACE, PLACE),
        (BlockingRule("where", (("place",),)), BlockingRule("where", (("place",),))),
    )

    assert schema_problems(broken) == (
        "duplicate field name 'place'",
        "duplicate blocking rule name 'where'",
    )


def test_every_problem_is_reported_not_just_the_first() -> None:
    broken = RequirementSchema(
        "alpha.v1", "alpha", (PLACE, RANGE), (BlockingRule("where", (("nowhere",),)),)
    )

    assert len(schema_problems(broken)) == 3


@pytest.mark.parametrize(
    ("keyword", "reason"),
    [
        ({"name": "Place"}, "FieldSpec.name must match"),
        ({"name": ""}, "must be a non-empty string"),
        ({"field_kind": "place"}, "field_kind must be a FieldKind"),
        ({"obligation": "blocking"}, "obligation must be an Obligation"),
        ({"prompt_message_key": ""}, "must be a non-empty string"),
        ({"prompt_message_key": "Ask.Place"}, "prompt_message_key must match"),
        ({"example_message_key": "Nope!"}, "example_message_key must match"),
    ],
)
def test_a_malformed_field_spec_is_refused(keyword: dict[str, object], reason: str) -> None:
    declared: dict[str, object] = {
        "name": "place",
        "field_kind": FieldKind.PLACE,
        "obligation": Obligation.BLOCKING,
        "prompt_message_key": "ask.alpha.place",
    }
    declared.update(keyword)

    with pytest.raises(InvariantViolationError) as raised:
        FieldSpec(**declared)  # type: ignore[arg-type]

    assert reason in str(raised.value)


def test_an_enum_field_must_declare_its_values() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        field("comfort", FieldKind.ENUM)

    assert "must declare its enum_values" in str(raised.value)


def test_enum_values_on_a_non_enum_field_are_refused() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        field("comfort", FieldKind.TEXT, enum_values=("a", "b"))

    assert "only meaningful on an enum field" in str(raised.value)


def test_enum_values_may_not_repeat() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        field("comfort", FieldKind.ENUM, enum_values=("a", "a"))

    assert "repeats a value" in str(raised.value)


@pytest.mark.parametrize(
    ("constraints", "kind", "reason"),
    [
        ({"min": "one"}, FieldKind.INTEGER, "constraint min must be a number"),
        ({"min": True}, FieldKind.INTEGER, "constraint min must be a number"),
        ({"min": 5, "max": 1}, FieldKind.INTEGER, "constraint min 5 is above max 1"),
    ],
)
def test_a_malformed_constraint_is_refused(
    constraints: dict[str, object], kind: FieldKind, reason: str
) -> None:
    """The constraints this release *understands* are still checked."""
    with pytest.raises(InvariantViolationError) as raised:
        field("bounded", kind, constraints=constraints)

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("constraints", "kind"),
    [
        ({"step": 15}, FieldKind.INTEGER),
        ({"pattern": "[A-Z]{3}"}, FieldKind.TEXT),
        ({"min": 1}, FieldKind.PLACE),
    ],
)
def test_a_constraint_this_release_does_not_read_is_carried_not_refused(
    constraints: dict[str, object], kind: FieldKind
) -> None:
    """Adding a Field Kind is additive: a bound it reads must not break older loaders.

    `constraints` is `Mapping[str, object]` by contract — an open bag. A key nobody here reads
    is inert, and so is a bound on a kind that does not consume it; refusing either would make
    every schema file already written a hostage to the next Field Kind.
    """
    spec = field("bounded", kind, constraints=constraints)

    assert dict(spec.constraints) == constraints


def test_constraints_are_read_only_and_reachable_by_key() -> None:
    bounded = field("party_size", FieldKind.INTEGER, constraints={"min": 1, "max": 12})

    assert bounded.constraint("min") == 1
    assert bounded.constraint("max") == 12
    assert bounded.constraint("nowhere") is None
    with pytest.raises(TypeError):
        bounded.constraints["min"] = 3  # type: ignore[index]


@pytest.mark.parametrize(
    ("any_of", "reason"),
    [
        ((), "any_of must be a non-empty tuple"),
        ((("place",), ()), "must be a non-empty tuple of field names"),
        ((("Place",),), "must match"),
        ((("place", "place"),), "repeats a field name"),
    ],
)
def test_a_malformed_blocking_rule_is_refused(
    any_of: tuple[tuple[str, ...], ...], reason: str
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        BlockingRule("where", any_of)

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("schema_key", "component_kind", "reason"),
    [
        ("alpha", "alpha", "schema_key must match"),
        ("alpha.v1", "Alpha", "component_kind must match"),
        ("alpha.v1", "", "must be a non-empty string"),
    ],
)
def test_a_malformed_schema_header_is_refused(
    schema_key: str, component_kind: str, reason: str
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        RequirementSchema(schema_key, component_kind, (PLACE,))

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("fields", "rules", "reason"),
    [
        ([PLACE], (), "fields must be a tuple"),
        ((PLACE,), [BlockingRule("where", (("place",),))], "blocking_rules must be a tuple"),
    ],
)
def test_a_schema_built_from_lists_is_refused(fields: object, rules: object, reason: str) -> None:
    """A list would let a schema be edited after it was validated."""
    with pytest.raises(InvariantViolationError) as raised:
        RequirementSchema("alpha.v1", "alpha", fields, rules)  # type: ignore[arg-type]

    assert reason in str(raised.value)


def test_enum_values_must_be_a_tuple_of_text() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        field("comfort", FieldKind.ENUM, enum_values=["basic"])

    assert "enum_values must be a tuple" in str(raised.value)


def test_constraints_must_be_a_mapping() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        field("party_size", FieldKind.INTEGER, constraints=[("min", 1)])

    assert "constraints must be a mapping" in str(raised.value)


def test_a_schema_is_frozen() -> None:
    with pytest.raises(AttributeError):
        SAMPLE.schema_key = "beta.v1"  # type: ignore[misc]


def test_field_names_are_reported_in_declaration_order() -> None:
    assert SAMPLE.field_names == ("place", "date_range", "starts_on", "ends_on")


def test_is_blocking_is_the_readable_half_of_the_obligation() -> None:
    assert PLACE.is_blocking
    assert not STARTS.is_blocking
