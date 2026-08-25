"""``ComponentKind`` and the catalog invariants — the rules, without a file in sight."""

from __future__ import annotations

import pytest

from tourganize.domain.catalog import ComponentKind, catalog_problems
from tourganize.domain.errors import InvariantViolationError, TourganizeError


def kind(key: str, *, weight: int = 100, awaits: tuple[str, ...] = ()) -> ComponentKind:
    return ComponentKind(
        kind_key=key,
        message_key=f"component.{key}",
        priority_weight=weight,
        schema_key=f"{key}.v1",
        requires_outcome_of=awaits,
    )


def test_a_kind_is_data_with_declared_defaults() -> None:
    declared = kind("alpha")

    assert declared.requires_outcome_of == ()
    assert declared.enabled is True
    assert declared.priority_weight == 100


def test_a_kind_is_frozen() -> None:
    declared = kind("alpha")

    with pytest.raises(AttributeError):
        declared.priority_weight = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "key",
    ["", "   ", "Alpha", "air travel", "9lives", "air-travel", "AIR_TRAVEL"],
)
def test_a_kind_key_must_be_lower_snake_case(key: str) -> None:
    with pytest.raises(InvariantViolationError):
        kind(key)


def test_a_weight_must_be_a_whole_number_and_a_bool_is_not_one() -> None:
    with pytest.raises(InvariantViolationError):
        ComponentKind("alpha", "component.alpha", True, "alpha.v1")  # type: ignore[arg-type]
    with pytest.raises(InvariantViolationError):
        ComponentKind("alpha", "component.alpha", 1.5, "alpha.v1")  # type: ignore[arg-type]


def test_a_kind_may_not_await_its_own_outcome() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        kind("alpha", awaits=("alpha",))

    assert "own outcome" in str(raised.value)


def test_every_domain_error_is_catchable_as_the_root() -> None:
    with pytest.raises(TourganizeError):
        kind("Alpha")


def test_a_sound_catalog_has_no_problems() -> None:
    assert catalog_problems([kind("alpha"), kind("beta", awaits=("alpha",))]) == ()


def test_an_empty_catalog_is_not_itself_a_problem() -> None:
    """`doctor` decides an installation needs kinds; the invariants judge only the ones given."""
    assert catalog_problems([]) == ()


def test_a_duplicate_kind_key_is_reported_once() -> None:
    problems = catalog_problems([kind("alpha"), kind("alpha", weight=200), kind("alpha")])

    assert problems == ("duplicate kind_key 'alpha'",)


def test_a_dangling_outcome_dependency_names_both_ends() -> None:
    problems = catalog_problems([kind("alpha", awaits=("nowhere",))])

    assert len(problems) == 1
    assert "alpha" in problems[0]
    assert "'nowhere'" in problems[0]


def test_a_two_kind_cycle_is_reported() -> None:
    problems = catalog_problems([kind("alpha", awaits=("beta",)), kind("beta", awaits=("alpha",))])

    assert len(problems) == 1
    assert problems[0].startswith("dependency cycle:")


def test_a_longer_cycle_is_reported_once_not_once_per_member() -> None:
    problems = catalog_problems(
        [
            kind("alpha", awaits=("beta",)),
            kind("beta", awaits=("gamma",)),
            kind("gamma", awaits=("alpha",)),
        ]
    )

    assert len(problems) == 1
    assert problems[0].count("->") == 3


def test_a_diamond_of_dependencies_is_not_a_cycle() -> None:
    problems = catalog_problems(
        [
            kind("alpha"),
            kind("beta", awaits=("alpha",)),
            kind("gamma", awaits=("alpha",)),
            kind("delta", awaits=("beta", "gamma")),
        ]
    )

    assert problems == ()


def test_every_problem_is_reported_not_only_the_first() -> None:
    problems = catalog_problems(
        [
            kind("alpha", awaits=("nowhere",)),
            kind("alpha"),
            kind("beta", awaits=("gamma",)),
            kind("gamma", awaits=("beta",)),
        ]
    )

    assert len(problems) == 3
    assert any("duplicate" in problem for problem in problems)
    assert any("no kind declares" in problem for problem in problems)
    assert any("cycle" in problem for problem in problems)
