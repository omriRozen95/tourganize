"""The ``ComponentCatalog`` contract, run against every adapter of the port — fakes included.

A new ``ComponentCatalog`` adapter is done when this file passes **unmodified**. Everything
asserted here is something the port promises, never something one adapter happens to do: the
file-backed catalog and the in-memory fake are handed the *same* declarations and must answer
identically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import SAMPLE_CATALOG, write_catalog

from tourganize.adapters.catalog.memory import InMemoryComponentCatalog
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.platform.errors import CatalogError
from tourganize.ports.catalog import ComponentCatalog

CatalogBuilder = Callable[[Path], ComponentCatalog]

#: The declarations every adapter is built from: three kinds, one Outcome Dependency, one
#: disabled. Keeping them in one place is what makes the two adapters comparable at all.
DECLARED = (
    ComponentKind("alpha", "component.alpha", 300, "alpha.v1"),
    ComponentKind("beta", "component.beta", 200, "beta.v1", ("alpha",)),
    ComponentKind("gamma", "component.gamma", 100, "gamma.v1", (), False),
)


def _yaml_catalog(tmp_path: Path) -> ComponentCatalog:
    return YamlComponentCatalog(write_catalog(tmp_path / "config", SAMPLE_CATALOG))


def _memory_catalog(_tmp_path: Path) -> ComponentCatalog:
    return InMemoryComponentCatalog(DECLARED)


#: Every adapter of the port, keyed by the name the test ids use.
CATALOGS: dict[str, CatalogBuilder] = {
    "YamlComponentCatalog": _yaml_catalog,
    "InMemoryComponentCatalog": _memory_catalog,
}


def catalogs(tmp_path: Path) -> Iterator[tuple[str, ComponentCatalog]]:
    for name, build in CATALOGS.items():
        yield name, build(tmp_path)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_the_port_is_satisfied_structurally(build: CatalogBuilder, tmp_path: Path) -> None:
    assert isinstance(build(tmp_path), ComponentCatalog)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_kinds_are_returned_in_declaration_order(build: CatalogBuilder, tmp_path: Path) -> None:
    catalog = build(tmp_path)

    assert [kind.kind_key for kind in catalog.kinds()] == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_reading_twice_gives_the_same_answer(build: CatalogBuilder, tmp_path: Path) -> None:
    """A conversation must not see the catalog change underneath it mid-turn."""
    catalog = build(tmp_path)

    assert catalog.kinds() == catalog.kinds()


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_every_declared_property_survives_the_adapter(
    build: CatalogBuilder, tmp_path: Path
) -> None:
    catalog = build(tmp_path)

    assert catalog.kinds() == DECLARED


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_enabled_kinds_filters_and_keeps_order(build: CatalogBuilder, tmp_path: Path) -> None:
    catalog = build(tmp_path)

    assert [kind.kind_key for kind in catalog.enabled_kinds()] == ["alpha", "beta"]


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_one_kind_is_reachable_by_key(build: CatalogBuilder, tmp_path: Path) -> None:
    catalog = build(tmp_path)

    assert catalog.kind("beta").requires_outcome_of == ("alpha",)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_an_unknown_key_raises_and_says_what_is_declared(
    build: CatalogBuilder, tmp_path: Path
) -> None:
    catalog = build(tmp_path)

    with pytest.raises(UnknownComponentKindError) as raised:
        catalog.kind("nowhere")

    assert "nowhere" in str(raised.value)
    assert "alpha" in str(raised.value)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_a_disabled_kind_is_not_reachable_by_key(build: CatalogBuilder, tmp_path: Path) -> None:
    catalog = build(tmp_path)

    with pytest.raises(UnknownComponentKindError) as raised:
        catalog.kind("gamma")

    assert "disabled" in str(raised.value)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_an_invalid_catalog_is_refused_by_every_adapter(
    build: CatalogBuilder, tmp_path: Path
) -> None:
    """Whatever the storage, the same declarations are invalid for the same reason."""
    del build
    cyclic = (
        ComponentKind("alpha", "component.alpha", 1, "alpha.v1", ("beta",)),
        ComponentKind("beta", "component.beta", 2, "beta.v1", ("alpha",)),
    )
    body = (
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1, requires_outcome_of: [beta]}\n"
        "  - {kind_key: beta, message_key: component.beta, priority_weight: 2,"
        " schema_key: beta.v1, requires_outcome_of: [alpha]}\n"
    )

    with pytest.raises(CatalogError) as from_memory:
        InMemoryComponentCatalog(cyclic)
    with pytest.raises(CatalogError) as from_file:
        YamlComponentCatalog(write_catalog(tmp_path / "config", body)).kinds()

    assert "dependency cycle" in str(from_memory.value)
    assert "dependency cycle" in str(from_file.value)


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_schema_for_is_declared_and_awaits_f03(build: CatalogBuilder, tmp_path: Path) -> None:
    """F03's Starting state requires this method declared but unimplemented on every adapter.

    F03 implements it and replaces this assertion with a real one.
    """
    catalog = build(tmp_path)

    with pytest.raises(NotImplementedError):
        catalog.schema_for("alpha")


@pytest.mark.parametrize("build", CATALOGS.values(), ids=CATALOGS)
def test_every_adapter_says_where_its_catalog_came_from(
    build: CatalogBuilder, tmp_path: Path
) -> None:
    """``doctor`` and every error message need an origin, so the port's adapters all have one."""
    catalog = build(tmp_path)

    assert isinstance(getattr(catalog, "origin", None), str)


def test_the_two_adapters_agree_on_everything(tmp_path: Path) -> None:
    """The fake's shape may never differ from the real adapter's."""
    answers = {
        name: (catalog.kinds(), catalog.enabled_kinds(), catalog.kind("alpha"))
        for name, catalog in catalogs(tmp_path)
    }

    assert len(answers) == len(CATALOGS)
    assert len(set(answers.values())) == 1
