"""The dependency rules of the architecture, checked against the source itself."""

from __future__ import annotations

import sys

from boundaries import (
    ALLOWED_IMPORTS,
    ImportEdge,
    allowance_for,
    find_violations,
    imports_of,
    iter_modules,
    judge,
    planted_violation,
)


def test_the_tree_satisfies_every_import_contract() -> None:
    assert find_violations() == ()


def test_the_pure_packages_import_only_the_standard_library_and_what_they_are_allowed() -> None:
    """Exhaustive, by AST: every import of the domain, the dialogue and the ports."""
    offenders = [
        f"{edge} (allowed: {', '.join(allowance_for(name) or ())})"
        for name, path in iter_modules()
        if allowance_for(name) is not None
        for edge in imports_of(name, path)
        if edge.imported.split(".")[0] not in sys.stdlib_module_names
        and not edge.imported.startswith(allowance_for(name) or ())
    ]
    assert offenders == []


def test_the_domain_may_not_import_the_ports_but_the_dialogue_may() -> None:
    """D17 in one assertion: the dialogue's extra permission, and its exact width."""
    from_domain = ImportEdge("tourganize.domain.trip.plan", "tourganize.ports.platform", 1)
    from_dialogue = ImportEdge("tourganize.dialogue.director", "tourganize.ports.platform", 1)

    assert judge(from_domain) == [
        "domain must import only the standard library and tourganize.domain, "
        f"tourganize.dialogue: {from_domain}"
    ]
    assert judge(from_dialogue) == []


def test_the_dialogue_may_not_reach_the_platform() -> None:
    """The one permission D17 grants is `tourganize.ports` and nothing beyond it."""
    edge = ImportEdge("tourganize.dialogue.director", "tourganize.platform.settings", 1)

    assert judge(edge) == [
        "dialogue must import only the standard library and tourganize.domain, "
        f"tourganize.dialogue, tourganize.ports: {edge}"
    ]


def test_every_constrained_package_declares_what_it_may_import() -> None:
    """A new pure package has to be added to the table, not left silently unconstrained."""
    assert set(ALLOWED_IMPORTS) == {
        "tourganize.domain",
        "tourganize.dialogue",
        "tourganize.ports",
    }
    assert allowance_for("tourganize.application.composition") is None


def test_the_checker_catches_a_planted_violation() -> None:
    with planted_violation() as probe:
        violations = find_violations()

    assert not probe.exists()
    assert any("_boundary_probe" in violation for violation in violations)
    assert any("must import only the standard library" in violation for violation in violations)
    assert any("only the Composition Root" in violation for violation in violations)
    assert find_violations() == ()


def test_the_cli_may_not_import_an_adapter() -> None:
    """The CLI holds no exemption: it receives adapters from the Container like everything else.

    CLAUDE.md — "Adapters are selected from Settings in exactly one place:
    tourganize/application/composition.py."
    """
    edge = ImportEdge("tourganize.cli", "tourganize.adapters.telemetry.null", 1)

    assert judge(edge) == [f"only the Composition Root may import adapters: {edge}"]


def test_the_composition_root_may() -> None:
    edge = ImportEdge("tourganize.application.composition", "tourganize.adapters.clock.system", 1)

    assert judge(edge) == []


def test_every_module_is_reachable_by_name() -> None:
    names = {name for name, _ in iter_modules()}

    assert "tourganize" in names
    assert "tourganize.cli" in names
    assert "tourganize.ports.platform" in names
    assert "tourganize.application.composition" in names
