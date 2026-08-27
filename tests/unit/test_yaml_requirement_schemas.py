"""Loading a Requirement Schema from a file: what is accepted, and how it fails."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SAMPLE_CATALOG, schemas_dir, write_catalog, write_schemas

from tourganize.adapters.catalog.yaml import YamlComponentCatalog, load_schema, schema_path
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.domain.requirements import (
    FieldKind,
    Obligation,
    RequirementSet,
    RequirementUpdate,
    analyse,
)
from tourganize.platform.errors import CatalogError, ConfigurationError, SchemaError

VALID_FIELD = (
    "  - name: place\n"
    "    field_kind: place\n"
    "    obligation: blocking\n"
    "    prompt_message_key: ask.alpha.place\n"
)
HEADER = "schema_key: alpha.v1\ncomponent_kind: alpha\n"


def write_schema(tmp_path: Path, text: str, schema_key: str = "alpha.v1") -> Path:
    directory = schemas_dir(tmp_path / "config")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{schema_key}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def catalog_of(tmp_path: Path, *, catalog: str = SAMPLE_CATALOG) -> YamlComponentCatalog:
    config = tmp_path / "config"
    write_schemas(config)
    return YamlComponentCatalog(write_catalog(config, catalog), schemas_dir(config))


def shipped_catalog() -> YamlComponentCatalog:
    """The catalog and schemas this repository ships, read the way ``Settings`` resolves them."""
    config = Path("config")
    return YamlComponentCatalog(config / "catalog" / "components.yaml", schemas_dir(config))


def test_the_shipped_schemas_load_and_agree_with_the_shipped_catalog() -> None:
    """The files in this repository are the ones the application actually ships with."""
    catalog = shipped_catalog()

    for kind in catalog.enabled_kinds():
        schema = catalog.schema_for(kind.kind_key)
        assert schema.schema_key == kind.schema_key
        assert schema.component_kind == kind.kind_key
        assert schema.fields
        assert schema.blocking_rules


#: The client's own rule, in the client's own words: "there should be some time range, if not
#: a specific start and end date". This is the one place a shipped topic and its field names
#: are written down in a test on purpose — the Definition of Done names them, and a fixture
#: schema shaped the same way would prove the machinery works without proving the file that
#: ships is shaped the way the client asked for.
CLIENT_RULE_KIND = "lodging"
CLIENT_RULE = "when"
CLIENT_RULE_PAIR = ("check_in", "check_out")


def test_the_shipped_pair_satisfies_the_shipped_rule_without_a_range() -> None:
    """`check_in` + `check_out` closes `when`; `check_in` alone does not."""
    schema = shipped_catalog().schema_for(CLIENT_RULE_KIND)
    empty = RequirementSet.empty(CLIENT_RULE_KIND)

    both = empty.with_updates(
        [
            RequirementUpdate(CLIENT_RULE_PAIR[0], "2026-10-23"),
            RequirementUpdate(CLIENT_RULE_PAIR[1], "2026-10-28"),
        ],
        schema=schema,
    )
    one = empty.with_updates([RequirementUpdate(CLIENT_RULE_PAIR[0], "2026-10-23")], schema=schema)

    assert "date_range" not in both
    assert CLIENT_RULE not in analyse(schema, both).blocking_rule_names
    assert CLIENT_RULE in analyse(schema, one).blocking_rule_names


def test_the_shipped_pair_is_the_only_way_the_rule_bends() -> None:
    """The pair satisfies `when`; the fields either side of it still block on their own."""
    schema = shipped_catalog().schema_for(CLIENT_RULE_KIND)
    rule = schema.rule(CLIENT_RULE)

    assert rule is not None
    assert rule.any_of == (("date_range",), CLIENT_RULE_PAIR)


def test_a_schema_is_read_verbatim(tmp_path: Path) -> None:
    schema = catalog_of(tmp_path).schema_for("alpha")

    place = schema.field("place")
    assert place is not None
    assert place.field_kind is FieldKind.PLACE
    assert place.obligation is Obligation.BLOCKING
    assert place.prompt_message_key == "ask.alpha.place"
    assert place.example_message_key == "example.alpha.place"
    party = schema.field("party_size")
    assert party is not None
    assert dict(party.constraints) == {"max": 12, "min": 1}


def test_a_blocking_rule_with_two_candidate_groups_survives_the_file(tmp_path: Path) -> None:
    """The `any_of` model, read off disk exactly as the client's example states it."""
    rule = catalog_of(tmp_path).schema_for("alpha").rule("when")

    assert rule is not None
    assert rule.any_of == (("date_range",), ("starts_on", "ends_on"))


def test_enum_values_survive_the_file(tmp_path: Path) -> None:
    comfort = catalog_of(tmp_path).schema_for("beta").field("comfort")

    assert comfort is not None
    assert comfort.enum_values == ("basic", "standard", "premium")


def test_a_schema_is_read_once_and_cached(tmp_path: Path) -> None:
    catalog = catalog_of(tmp_path)
    first = catalog.schema_for("alpha")

    schema_path(catalog.schema_dir, "alpha.v1").unlink()

    assert catalog.schema_for("alpha") is first


def test_the_schema_directory_is_the_one_it_was_handed(tmp_path: Path) -> None:
    """There is no second default here: ``TOURGANIZE_SCHEMA_DIR`` is resolved in Settings."""
    elsewhere = tmp_path / "elsewhere"

    catalog = YamlComponentCatalog(write_catalog(tmp_path / "config"), elsewhere)

    assert catalog.schema_dir == elsewhere


def test_a_disabled_kind_has_no_reachable_schema(tmp_path: Path) -> None:
    with pytest.raises(UnknownComponentKindError):
        catalog_of(tmp_path).schema_for("gamma")


def test_a_missing_schema_file_names_the_path_it_looked_for(tmp_path: Path) -> None:
    catalog = YamlComponentCatalog(
        write_catalog(tmp_path / "config"), schemas_dir(tmp_path / "config")
    )

    with pytest.raises(SchemaError) as raised:
        catalog.schema_for("alpha")

    assert "alpha.v1.yaml" in str(raised.value)
    assert "does not exist" in str(raised.value)


def test_a_schema_error_is_a_catalog_error_so_the_cli_exits_3(tmp_path: Path) -> None:
    """A kind whose schema resolves to nothing is as broken as a dangling dependency."""
    catalog = YamlComponentCatalog(
        write_catalog(tmp_path / "config"), schemas_dir(tmp_path / "config")
    )

    with pytest.raises(CatalogError):
        catalog.schema_for("alpha")
    with pytest.raises(ConfigurationError):
        catalog.schema_for("alpha")


def test_a_schema_whose_component_kind_disagrees_with_the_catalog_is_refused(
    tmp_path: Path,
) -> None:
    write_schemas(tmp_path / "config")
    write_schema(
        tmp_path, "schema_key: alpha.v1\ncomponent_kind: elsewhere\nfields:\n" + VALID_FIELD
    )
    catalog = YamlComponentCatalog(
        write_catalog(tmp_path / "config"), schemas_dir(tmp_path / "config")
    )

    with pytest.raises(SchemaError) as raised:
        catalog.schema_for("alpha")

    assert "describes 'elsewhere'" in str(raised.value)


def test_a_schema_whose_key_disagrees_with_its_file_name_is_refused(tmp_path: Path) -> None:
    path = write_schema(
        tmp_path, "schema_key: beta.v1\ncomponent_kind: alpha\nfields:\n" + VALID_FIELD
    )

    with pytest.raises(SchemaError) as raised:
        load_schema(path, expected_key="alpha.v1")

    assert "its file name says 'alpha.v1'" in str(raised.value)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("- alpha\n- beta\n", "must be a mapping"),
        ("component_kind: alpha\nfields: []\n", "missing required key(s) schema_key"),
        ("schema_key: alpha.v1\nfields: []\n", "missing required key(s) component_kind"),
        (HEADER, "missing required key(s) fields"),
        (HEADER + "fields: nope\n", "`fields` must be a list"),
        (HEADER + "fields:\n  - alpha\n", "field 1 is not a mapping"),
        (HEADER + "fields:\n" + VALID_FIELD + "extra: 1\n", "unknown top-level key(s) extra"),
        (HEADER + "fields:\n" + VALID_FIELD + "blocking_rules: nope\n", "must be a list"),
        (HEADER + "fields:\n" + VALID_FIELD + "blocking_rules:\n  - where\n", "is not a mapping"),
    ],
)
def test_a_malformed_document_is_refused(body: str, reason: str, tmp_path: Path) -> None:
    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("  - field_kind: place\n", "missing required key(s) name"),
        (
            "  - name: place\n    field_kind: place\n    obligation: blocking\n",
            "missing required key(s) prompt_message_key",
        ),
        (VALID_FIELD + "    prompt_msg_key: x\n", "unknown key(s) prompt_msg_key"),
        (
            "  - name: place\n"
            "    field_kind: somewhere\n"
            "    obligation: blocking\n"
            "    prompt_message_key: ask.alpha.place\n",
            "field_kind must be one of",
        ),
        (
            "  - name: place\n"
            "    field_kind: place\n"
            "    obligation: sometimes\n"
            "    prompt_message_key: ask.alpha.place\n",
            "obligation must be one of",
        ),
        (
            "  - name: Place\n"
            "    field_kind: place\n"
            "    obligation: blocking\n"
            "    prompt_message_key: ask.alpha.place\n",
            "FieldSpec.name must match",
        ),
        (VALID_FIELD + "    enum_values: basic\n", "enum_values must be a list"),
        (VALID_FIELD + "    enum_values: [1]\n", "enum_values must contain text"),
        (VALID_FIELD + "    constraints: [1]\n", "constraints must be a mapping"),
        (VALID_FIELD + "    example_message_key: 1\n", "example_message_key must be text"),
    ],
)
def test_a_malformed_field_names_its_position_and_its_problem(
    entry: str, reason: str, tmp_path: Path
) -> None:
    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, HEADER + "fields:\n" + entry))

    message = str(raised.value)
    assert reason in message
    assert "field 1" in message


def test_a_field_with_no_prompt_message_key_is_refused(tmp_path: Path) -> None:
    """DoD: a gap the dialogue cannot phrase a question for can never be closed."""
    body = (
        HEADER + "fields:\n" + "  - name: place\n    field_kind: place\n    obligation: blocking\n"
    )

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    assert "prompt_message_key" in str(raised.value)


def test_an_enum_field_with_no_values_is_refused(tmp_path: Path) -> None:
    body = (
        HEADER + "fields:\n" + "  - name: comfort\n"
        "    field_kind: enum\n"
        "    obligation: optional\n"
        "    prompt_message_key: ask.alpha.comfort\n" + VALID_FIELD
    )

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    assert "must declare its enum_values" in str(raised.value)


def test_a_blocking_rule_naming_an_undeclared_field_is_refused(tmp_path: Path) -> None:
    body = (
        HEADER
        + "fields:\n"
        + VALID_FIELD
        + "blocking_rules:\n  - name: where\n    any_of: [[nowhere]]\n"
    )

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    assert "names 'nowhere', which the schema does not declare" in str(raised.value)


@pytest.mark.parametrize(
    ("rule", "reason"),
    [
        ("  - any_of: [[place]]\n", "missing required key(s) name"),
        ("  - name: where\n", "missing required key(s) any_of"),
        ("  - name: where\n    any_of: [[place]]\n    extra: 1\n", "unknown key(s) extra"),
        ("  - name: where\n    any_of: nope\n", "any_of must be a list"),
        ("  - name: where\n    any_of: [[1]]\n", "must contain field names"),
        ("  - name: Where\n    any_of: [[place]]\n", "BlockingRule.name must match"),
    ],
)
def test_a_malformed_blocking_rule_names_its_position(
    rule: str, reason: str, tmp_path: Path
) -> None:
    body = HEADER + "fields:\n" + VALID_FIELD + "blocking_rules:\n" + rule

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    message = str(raised.value)
    assert reason in message
    assert "blocking rule 1" in message


def test_a_bare_name_is_read_as_a_group_of_one(tmp_path: Path) -> None:
    """`any_of: [place]` reads better than `any_of: [[place]]` when there is one way in."""
    body = (
        HEADER
        + "fields:\n"
        + VALID_FIELD
        + "blocking_rules:\n  - name: where\n    any_of: [place]\n"
    )

    schema = load_schema(write_schema(tmp_path, body))

    assert schema.blocking_rules[0].any_of == (("place",),)


def test_every_broken_field_is_reported_not_just_the_first(tmp_path: Path) -> None:
    body = (
        HEADER
        + "fields:\n"
        + "  - name: place\n    field_kind: nowhere\n    obligation: blocking\n"
        "    prompt_message_key: ask.alpha.place\n"
        "  - name: Second\n    field_kind: place\n    obligation: optional\n"
        "    prompt_message_key: ask.alpha.second\n"
    )

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    message = str(raised.value)
    assert "field 1" in message
    assert "field 2" in message


def test_a_malformed_schema_header_names_the_file(tmp_path: Path) -> None:
    body = "schema_key: alpha.v1\ncomponent_kind: Alpha\nfields:\n" + VALID_FIELD

    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, body))

    assert "component_kind must match" in str(raised.value)
    assert "alpha.v1.yaml" in str(raised.value)


def test_a_syntax_error_in_the_file_names_the_line(tmp_path: Path) -> None:
    with pytest.raises(SchemaError) as raised:
        load_schema(write_schema(tmp_path, HEADER + "fields:\n\t- name: place\n"))

    assert "line 4" in str(raised.value)


def test_a_schema_with_no_blocking_rules_derives_them_from_the_obligations(
    tmp_path: Path,
) -> None:
    schema = load_schema(write_schema(tmp_path, HEADER + "fields:\n" + VALID_FIELD))

    assert [rule.name for rule in schema.blocking_rules] == ["place"]
    assert schema.blocking_rules[0].any_of == (("place",),)


def test_adding_an_optional_field_to_a_shipped_schema_needs_no_python_change(
    tmp_path: Path,
) -> None:
    """A new filter is a line in a file — proven against the loader, not just the domain."""
    catalog = catalog_of(tmp_path)
    extra = (
        "  - name: breakfast\n"
        "    field_kind: boolean\n"
        "    obligation: optional\n"
        "    prompt_message_key: ask.alpha.breakfast\n"
    )
    existing = schema_path(catalog.schema_dir, "alpha.v1").read_text(encoding="utf-8")
    head, _, rules = existing.partition("blocking_rules:")
    write_schema(tmp_path, f"{head}{extra}blocking_rules:{rules}")

    schema = YamlComponentCatalog(catalog.path, catalog.schema_dir).schema_for("alpha")

    assert "breakfast" in schema.field_names
    assert [spec.name for spec in schema.optional_fields()][-1] == "breakfast"
