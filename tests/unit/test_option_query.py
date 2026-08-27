"""``OptionQuery`` and ``OptionSourceResult``: what crosses the ``OptionSource`` seam."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options import PlanOption
from tourganize.domain.options.query import (
    DEFAULT_QUERY_LOCALE,
    OptionQuery,
    OptionSourceResult,
)
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import Selection

OptionFactory = Callable[..., PlanOption]

SCHEMA = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)


def requirements(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty("alpha").with_updates(updates, schema=SCHEMA)


def test_a_query_carries_what_a_source_may_know_and_nothing_else() -> None:
    query = OptionQuery(kind_key="alpha", requirements=requirements(place="Paris"), slate_size=3)

    assert query.kind_key == "alpha"
    assert query.value_of("place") == "Paris"
    assert query.slate_size == 3
    assert query.locale == DEFAULT_QUERY_LOCALE
    assert query.context_selections == {}
    assert query.request_id == ""


def test_a_query_digest_is_the_requirement_set_s() -> None:
    """One fingerprint of "what was asked for", so a source and a slate agree about it."""
    held = requirements(place="Paris")
    query = OptionQuery(kind_key="alpha", requirements=held, slate_size=3)

    assert query.digest() == held.digest()
    assert (
        query.digest()
        != OptionQuery(
            kind_key="alpha", requirements=requirements(place="Lisbon"), slate_size=3
        ).digest()
    )


def test_a_query_refuses_requirements_of_another_component_kind() -> None:
    with pytest.raises(InvariantViolationError, match="carries the requirements of"):
        OptionQuery(kind_key="beta", requirements=requirements(place="Paris"), slate_size=3)


@pytest.mark.parametrize("slate_size", [0, -1, 2.5, True])
def test_a_query_refuses_a_slate_size_that_is_not_a_count(slate_size: object) -> None:
    with pytest.raises(InvariantViolationError, match="slate_size"):
        OptionQuery(
            kind_key="alpha",
            requirements=requirements(place="Paris"),
            slate_size=slate_size,  # type: ignore[arg-type]
        )


def test_context_selections_are_a_read_only_view_of_the_plan(
    option_factory: OptionFactory,
) -> None:
    """A source reads a Selection an Outcome Dependency entitles it to; it never writes one."""
    selection = Selection("beta", option_factory("b1", "beta"), chosen_at_turn=2)
    query = OptionQuery(
        kind_key="alpha",
        requirements=requirements(place="Paris"),
        slate_size=3,
        context_selections={"beta": selection},
    )

    assert query.selection_of("beta") is selection
    assert query.selection_of("gamma") is None
    with pytest.raises(TypeError):
        query.context_selections["gamma"] = selection  # type: ignore[index]


def test_context_selections_refuse_anything_that_is_not_a_selection() -> None:
    with pytest.raises(InvariantViolationError, match="must be a Selection"):
        OptionQuery(
            kind_key="alpha",
            requirements=requirements(place="Paris"),
            slate_size=3,
            context_selections={"beta": "the cheap one"},  # type: ignore[dict-item]
        )


def test_a_result_names_its_source_and_when_it_was_retrieved(
    option_factory: OptionFactory,
) -> None:
    result = OptionSourceResult(
        options=(option_factory("a1"),), source_id="fixture", retrieved_at=DEFAULT_MOMENT
    )

    assert result.source_id == "fixture"
    assert len(result) == 1
    assert result.partial is False
    assert result.diagnostics == ()


def test_a_result_refuses_a_naive_timestamp(option_factory: OptionFactory) -> None:
    with pytest.raises(InvariantViolationError, match="timezone-aware"):
        OptionSourceResult(
            options=(option_factory("a1"),),
            source_id="fixture",
            retrieved_at=datetime(2026, 1, 1, 12, 0),
        )


def test_a_result_refuses_anything_that_is_not_a_plan_option() -> None:
    with pytest.raises(InvariantViolationError, match="not a PlanOption"):
        OptionSourceResult(
            options=("a cheap room",),  # type: ignore[arg-type]
            source_id="fixture",
            retrieved_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        )


def test_a_result_may_be_empty_and_say_why(option_factory: OptionFactory) -> None:
    """Nothing found is an answer, not an exception — F05 already has an Act for it."""
    del option_factory
    result = OptionSourceResult(
        options=(),
        source_id="fixture",
        retrieved_at=DEFAULT_MOMENT,
        partial=True,
        diagnostics=("no_match",),
    )

    assert len(result) == 0
    assert result.diagnostics == ("no_match",)


def test_a_diagnostic_is_an_opaque_code_rather_than_a_sentence() -> None:
    with pytest.raises(InvariantViolationError, match="diagnostics"):
        OptionSourceResult(
            options=(),
            source_id="fixture",
            retrieved_at=DEFAULT_MOMENT,
            diagnostics="no_match",  # type: ignore[arg-type]
        )
