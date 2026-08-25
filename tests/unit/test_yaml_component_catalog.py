"""Loading a Component Catalog from a file: what is accepted, and how it fails."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SAMPLE_CATALOG, write_catalog

from tourganize.adapters.catalog.yaml import CATALOG_VERSION, YamlComponentCatalog
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.platform.errors import CatalogError, ConfigurationError

VALID_KIND = (
    "  - kind_key: alpha\n"
    "    message_key: component.alpha\n"
    "    priority_weight: 300\n"
    "    schema_key: alpha.v1\n"
)


def catalog_from(text: str, tmp_path: Path) -> YamlComponentCatalog:
    return YamlComponentCatalog(write_catalog(tmp_path / "config", text))


def test_the_shipped_catalog_loads() -> None:
    """The file in this repository is the one the application actually ships with."""
    catalog = YamlComponentCatalog(Path("config") / "catalog" / "components.yaml")

    kinds = catalog.kinds()

    assert len(kinds) == 3
    assert all(kind.enabled for kind in kinds)
    assert [kind.priority_weight for kind in kinds] == sorted(
        (kind.priority_weight for kind in kinds), reverse=True
    )


def test_kinds_come_back_in_declaration_order(tmp_path: Path) -> None:
    """Declaration order is contract: F04 breaks priority ties with it."""
    catalog = catalog_from(SAMPLE_CATALOG, tmp_path)

    assert [kind.kind_key for kind in catalog.kinds()] == ["alpha", "beta", "gamma"]


def test_declared_properties_are_read_verbatim(tmp_path: Path) -> None:
    catalog = catalog_from(SAMPLE_CATALOG, tmp_path)

    beta = catalog.kind("beta")

    assert beta.message_key == "component.beta"
    assert beta.priority_weight == 200
    assert beta.schema_key == "beta.v1"
    assert beta.requires_outcome_of == ("alpha",)
    assert beta.enabled is True


def test_a_disabled_kind_is_listed_but_not_plannable(tmp_path: Path) -> None:
    catalog = catalog_from(SAMPLE_CATALOG, tmp_path)

    assert "gamma" in [kind.kind_key for kind in catalog.kinds()]
    assert "gamma" not in [kind.kind_key for kind in catalog.enabled_kinds()]
    with pytest.raises(UnknownComponentKindError) as raised:
        catalog.kind("gamma")
    assert "disabled" in str(raised.value)


def test_a_fourth_kind_needs_no_python_change(tmp_path: Path) -> None:
    """The whole point of the catalog: adding a topic is an entry in a file."""
    catalog = catalog_from(
        SAMPLE_CATALOG
        + "  - kind_key: delta\n"
        "    message_key: component.delta\n"
        "    priority_weight: 50\n"
        "    schema_key: delta.v1\n"
        "    requires_outcome_of: [beta]\n",
        tmp_path,
    )

    keys = [kind.kind_key for kind in catalog.kinds()]

    assert keys == ["alpha", "beta", "gamma", "delta"]
    assert catalog.kind("delta").requires_outcome_of == ("beta",)


def test_the_file_is_read_once_and_cached(tmp_path: Path) -> None:
    catalog = catalog_from(SAMPLE_CATALOG, tmp_path)
    first = catalog.kinds()

    catalog.path.unlink()

    assert catalog.kinds() is first


def test_the_file_is_not_read_before_it_is_asked_for(tmp_path: Path) -> None:
    """`doctor` has to be able to report a broken catalog rather than die building it."""
    catalog = YamlComponentCatalog(tmp_path / "never-written.yaml")

    assert catalog.origin.endswith("never-written.yaml")
    with pytest.raises(CatalogError):
        catalog.kinds()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("kinds: []\n", "version None is not supported"),
        (f"version: 2\nkinds:\n{VALID_KIND}", "is not supported"),
        (f"version: {CATALOG_VERSION}\n", "declares no `kinds` list"),
        (f"version: {CATALOG_VERSION}\nkinds: alpha\n", "`kinds` must be a list"),
        (f"version: {CATALOG_VERSION}\nkinds:\n  - alpha\n", "is not a mapping"),
        ("- alpha\n- beta\n", "must be a mapping"),
        (f"version: {CATALOG_VERSION}\nkinds: []\nextra: 1\n", "unknown top-level key"),
    ],
)
def test_a_malformed_document_is_refused(body: str, reason: str, tmp_path: Path) -> None:
    with pytest.raises(CatalogError) as raised:
        catalog_from(body, tmp_path).kinds()

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ("  - message_key: component.alpha\n", "missing required key(s) kind_key"),
        (
            "  - kind_key: alpha\n    message_key: component.alpha\n    schema_key: alpha.v1\n",
            "missing required key(s) priority_weight",
        ),
        (VALID_KIND + "    priorty_weight: 1\n", "unknown key(s) priorty_weight"),
        (
            "  - kind_key: alpha\n"
            "    message_key: component.alpha\n"
            "    priority_weight: heavy\n"
            "    schema_key: alpha.v1\n",
            "priority_weight must be a whole number",
        ),
        (VALID_KIND + "    enabled: yes\n", "enabled must be true or false"),
        (VALID_KIND + "    requires_outcome_of: beta\n", "must be a list of kind_keys"),
        (VALID_KIND + "    requires_outcome_of: [1]\n", "must contain kind_keys"),
        (
            "  - kind_key: Alpha\n"
            "    message_key: component.alpha\n"
            "    priority_weight: 1\n"
            "    schema_key: alpha.v1\n",
            "kind_key must match",
        ),
    ],
)
def test_a_malformed_kind_names_its_position_and_its_problem(
    entry: str, reason: str, tmp_path: Path
) -> None:
    with pytest.raises(CatalogError) as raised:
        catalog_from(f"version: {CATALOG_VERSION}\nkinds:\n{entry}", tmp_path).kinds()

    message = str(raised.value)
    assert reason in message
    assert "kind 1" in message


def test_every_broken_kind_is_reported_not_just_the_first(tmp_path: Path) -> None:
    body = (
        f"version: {CATALOG_VERSION}\n"
        "kinds:\n"
        "  - kind_key: alpha\n"
        "    message_key: component.alpha\n"
        "    priority_weight: heavy\n"
        "    schema_key: alpha.v1\n"
        "  - kind_key: Beta\n"
        "    message_key: component.beta\n"
        "    priority_weight: 1\n"
        "    schema_key: beta.v1\n"
    )

    with pytest.raises(CatalogError) as raised:
        catalog_from(body, tmp_path).kinds()

    message = str(raised.value)
    assert "kind 1" in message
    assert "kind 2" in message


def test_the_catalog_invariants_are_enforced_at_load(tmp_path: Path) -> None:
    body = (
        f"version: {CATALOG_VERSION}\n"
        "kinds:\n"
        f"{VALID_KIND}"
        "  - kind_key: alpha\n"
        "    message_key: component.alpha\n"
        "    priority_weight: 1\n"
        "    schema_key: alpha.v1\n"
        "    requires_outcome_of: [nowhere]\n"
    )

    with pytest.raises(CatalogError) as raised:
        catalog_from(body, tmp_path).kinds()

    message = str(raised.value)
    assert "duplicate kind_key 'alpha'" in message
    assert "which no kind declares" in message


def test_a_catalog_error_is_a_configuration_error_so_the_cli_exits_3(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        YamlComponentCatalog(tmp_path / "absent.yaml").kinds()


def test_a_syntax_error_in_the_file_names_the_line(tmp_path: Path) -> None:
    with pytest.raises(CatalogError) as raised:
        catalog_from(f"version: {CATALOG_VERSION}\nkinds:\n\t- kind_key: alpha\n", tmp_path).kinds()

    assert "line 3" in str(raised.value)


def test_schema_for_is_declared_but_belongs_to_f03(tmp_path: Path) -> None:
    catalog = catalog_from(SAMPLE_CATALOG, tmp_path)

    with pytest.raises(NotImplementedError) as raised:
        catalog.schema_for("alpha")

    assert "F03" in str(raised.value)
