"""The dependency rules of the architecture, checked against the source itself."""

from __future__ import annotations

import sys

from boundaries import (
    PURE_PACKAGES,
    find_violations,
    imports_of,
    iter_modules,
    planted_violation,
)


def test_the_tree_satisfies_every_import_contract() -> None:
    assert find_violations() == ()


def test_the_pure_packages_import_only_the_standard_library() -> None:
    offenders = [
        str(edge)
        for name, path in iter_modules()
        if name.startswith(PURE_PACKAGES)
        for edge in imports_of(name, path)
        if edge.imported.split(".")[0] not in sys.stdlib_module_names
        and not edge.imported.startswith(PURE_PACKAGES)
    ]
    assert offenders == []


def test_the_checker_catches_a_planted_violation() -> None:
    with planted_violation() as probe:
        violations = find_violations()

    assert not probe.exists()
    assert any("_boundary_probe" in violation for violation in violations)
    assert any("must import only the standard library" in violation for violation in violations)
    assert any("only the Composition Root and the CLI" in violation for violation in violations)
    assert find_violations() == ()


def test_every_module_is_reachable_by_name() -> None:
    names = {name for name, _ in iter_modules()}

    assert "tourganize" in names
    assert "tourganize.cli" in names
    assert "tourganize.ports.platform" in names
    assert "tourganize.application.composition" in names
