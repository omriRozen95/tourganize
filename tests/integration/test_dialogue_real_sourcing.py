"""The F05 conversation, driven by F06's **real** Planning Service via the Composition Root.

``test_dialogue_walkthrough.py`` drives the same opening against the fake planner and is left
untouched — that is the point: F05's scenarios pass unchanged when the fake is swapped for the
real thing, because the Director never learned anything about where options come from.

What is different here is everything below the ``OptionSlatePlanner`` seam. The slates are
built by :class:`~tourganize.application.planning_service.PlanningService` from the fixture
tree that ships with the repository, through the ``OptionSource`` port, ranked and filtered,
and the Director is wired by ``build_container`` rather than by hand. If the wiring is wrong,
these fail; if the *Director* had to change to accommodate real sourcing, the walkthrough next
door would fail too, and it does not.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest

from tourganize.application.composition import build_container, build_dialogue_settings
from tourganize.dialogue import (
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLOSE,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_SOURCING_FAILURE,
    AssistantAct,
    DialogueDirector,
    DialogueState,
    UserTurn,
)
from tourganize.domain.options import OptionSlate
from tourganize.domain.requirements import (
    FieldKind,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import ComponentStatus, TripPlan
from tourganize.platform.errors import ConfigurationError, OptionSourcingError
from tourganize.platform.settings import Settings
from tourganize.ports.interpretation import OptionSlatePlanner

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHIPPED_CONFIG: Final = REPO_ROOT / "config"
SHIPPED_FIXTURES: Final = REPO_ROOT / "fixtures" / "options"


def settings_for(tmp_path: Path, **overrides: str) -> Settings:
    """The shipped configuration and fixture tree, with only the writable paths redirected."""
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(SHIPPED_CONFIG),
        "TOURGANIZE_FIXTURE_DIR": str(SHIPPED_FIXTURES),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        "TOURGANIZE_TELEMETRY_SINK": "null",
    }
    environ.update(overrides)
    return Settings.from_env(environ)


class Conversation:
    """A greeted Director built by the Composition Root, and one line per traveller turn."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        planner: OptionSlatePlanner | None = None,
        **overrides: str,
    ) -> None:
        self.container = build_container(settings_for(tmp_path, **overrides))
        self.director = DialogueDirector(
            self.container.component_catalog,
            self.container.priority_policy,
            self.container.turn_interpreter,
            planner if planner is not None else self.container.option_slate_planner,
            self.container.clock,
            self.container.telemetry_sink,
            build_dialogue_settings(self.container.settings),
            session_id="real-sourcing",
        )
        self.acts: list[AssistantAct] = list(self.director.begin())

    def say(self, text: str) -> tuple[AssistantAct, ...]:
        produced = self.director.handle(
            UserTurn(
                index=self.director.session.next_turn_index,
                text=text,
                received_at=self.container.clock.now(),
            )
        )
        self.acts += produced
        return produced


def names(acts: Sequence[AssistantAct]) -> list[str]:
    return [act.act for act in acts]


def the_lodging_kind(conversation: Conversation) -> str:
    """The shipped Kind whose schema asks for a place *and* a date range — read, not named."""
    catalog = conversation.container.component_catalog
    for kind in catalog.enabled_kinds():
        rules = {rule.name for rule in catalog.schema_for(kind.kind_key).blocking_rules}
        if {"where", "when"} <= rules and kind.requires_outcome_of:
            return kind.kind_key
    raise AssertionError("the shipped schemas declare no kind with a where-and-when rule")


def test_the_container_wires_the_real_planner_behind_the_seam(tmp_path: Path) -> None:
    container = build_container(settings_for(tmp_path))

    assert container.adapters()["OptionSlatePlanner"] == "PlanningService"
    assert container.adapters()["OptionSourceRegistry"] == "SourceRegistry"


def test_the_paris_opening_asks_for_the_dates_then_presents_real_options(
    tmp_path: Path,
) -> None:
    """F05's first scenario, with the options coming off disk through the OptionSource port."""
    conversation = Conversation(tmp_path)
    lodging = the_lodging_kind(conversation)

    first = conversation.say("find me a hotel in Paris")

    assert names(first) == [ASK_BLOCKING]
    assert first[0].payload["rule_name"] == "when"

    second = conversation.say("23-28 October 2026")

    assert names(second) == [PRESENT_SLATE, ASK_OPTIONAL]
    options = second[0].payload["options"]
    assert isinstance(options, tuple)
    assert len(options) == conversation.container.settings.slate_size
    assert all(option["price"] is not None for option in options)
    assert all(option["source_id"] == "fixture" for option in options)
    assert all(option["facts"]["review_score"] for option in options)
    assert second[0].kind_key == lodging


def test_the_slate_carries_the_requirements_digest_of_what_was_asked_for(
    tmp_path: Path,
) -> None:
    conversation = Conversation(tmp_path)
    lodging = the_lodging_kind(conversation)
    conversation.say("find me a hotel in Paris")
    conversation.say("23-28 October 2026")

    component = conversation.director.session.plan.component(lodging)
    held = component.requirements
    slate = component.latest_slate()

    assert held is not None
    assert slate is not None
    assert slate.requirements_digest == held.digest()


def test_choosing_records_a_selection_of_a_real_option(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    lodging = the_lodging_kind(conversation)
    conversation.say("find me a hotel in Paris")
    slate_acts = conversation.say("23-28 October 2026")
    offered = slate_acts[0].payload["option_ids"]
    assert isinstance(offered, tuple)

    chosen = conversation.say("2")

    assert names(chosen)[0] == CONFIRM_SELECTION
    component = conversation.director.session.plan.component(lodging)
    assert component.status is ComponentStatus.SELECTED
    assert component.selection is not None
    assert component.selection.option_id == offered[1]
    assert component.selection.option.provenance.source_id == "fixture"


def test_a_refinement_re_sources_the_same_component_with_a_new_round(tmp_path: Path) -> None:
    """The choose-or-refine loop against real data: three rounds, none of them discarded."""
    conversation = Conversation(tmp_path)
    lodging = the_lodging_kind(conversation)
    conversation.say("find me a hotel in Paris")
    conversation.say("23-28 October 2026")

    for _ in range(3):
        assert names(conversation.say("cheaper"))[0] == PRESENT_SLATE

    component = conversation.director.session.plan.component(lodging)
    assert [slate.round_index for slate in component.slates] == [0, 1, 2, 3]
    assert component.selection is None


def test_mentioned_first_still_holds_end_to_end(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path)
    lodging = the_lodging_kind(conversation)
    conversation.say("find me a hotel in Paris")
    conversation.say("23-28 October 2026")
    conversation.say("2")

    sequence = names(conversation.acts)
    assert sequence.index(CONFIRM_SELECTION) < sequence.index(OFFER_UNMENTIONED)
    for act in conversation.acts[: sequence.index(CONFIRM_SELECTION)]:
        assert act.kind_key in (None, lodging), act


def test_the_whole_conversation_reaches_a_summary_and_closes(tmp_path: Path) -> None:
    conversation = Conversation(tmp_path, TOURGANIZE_DIALOGUE_OFFER_BATCH="2")
    conversation.say("find me a hotel in Paris")
    conversation.say("23-28 October 2026")
    conversation.say("1")
    closing = conversation.say("no thanks")

    assert names(closing) == [DELIVER_SUMMARY, CLOSE]
    assert conversation.director.session.state is DialogueState.CLOSED


def test_a_place_nobody_recorded_still_produces_a_slate(tmp_path: Path) -> None:
    """The synthetic fallback, seen from the conversation: a demonstration never dead-ends."""
    conversation = Conversation(tmp_path)
    conversation.say("find me a hotel in Reykjavik")
    acts = conversation.say("23-28 October 2026")

    assert names(acts)[0] == PRESENT_SLATE


def test_strict_filtering_that_empties_the_slate_becomes_a_reported_failure(
    tmp_path: Path,
) -> None:
    """F06's DoD, end to end: strict plus an impossible ceiling is an Act, not a dead session."""
    conversation = Conversation(tmp_path, TOURGANIZE_OPTION_FILTER_STRICT="true")
    lodging = the_lodging_kind(conversation)
    schema = conversation.container.component_catalog.schema_for(lodging)
    conversation.say("find me a hotel in Paris")
    conversation.say("23-28 October 2026")

    component = conversation.director.session.plan.component(lodging)
    held = component.requirements
    assert held is not None
    impossible = held.with_updates([_impossible_ceiling(schema)], schema=schema)

    slate = conversation.container.option_slate_planner.plan(
        lodging, impossible, conversation.director.session.plan, 1
    )

    assert slate.options == ()
    assert "filtered_out" in slate.diagnostics


def _impossible_ceiling(schema: RequirementSchema) -> RequirementUpdate:
    """A budget below every recorded price, on whichever money filter the schema declares.

    Read off the schema rather than written down, for the reason every helper in this suite
    reads rather than names: a travel topic — or a field name that only one topic happens to
    use — must not be hardcoded here.
    """
    money_fields = [
        spec.name for spec in schema.optional_fields() if spec.field_kind is FieldKind.MONEY
    ]
    assert money_fields, "the shipped schema declares no money filter to test with"
    return RequirementUpdate(field_name=money_fields[0], value="100 EUR")


class _AlwaysFailingPlanner:
    """A planner behind which every Option Source has failed."""

    def plan(
        self,
        kind_key: str,
        requirements: RequirementSet,
        plan: TripPlan,
        round_index: int,
    ) -> OptionSlate:
        del requirements, plan, round_index
        raise OptionSourcingError(f"every Option Source for {kind_key!r} failed")


def test_a_sourcing_failure_is_reported_without_ending_the_session(tmp_path: Path) -> None:
    """``OptionSourcingError`` becomes ``report_sourcing_failure``; the conversation goes on.

    The Director is built with the failing planner rather than having one swapped in, so what
    is exercised is the seam as it is wired, not a private attribute reached into mid-test.
    """
    conversation = Conversation(tmp_path, planner=_AlwaysFailingPlanner())
    lodging = the_lodging_kind(conversation)

    conversation.say("find me a hotel in Paris")
    acts = conversation.say("23-28 October 2026")

    assert REPORT_SOURCING_FAILURE in names(acts)
    assert conversation.director.session.state is not DialogueState.CLOSED
    assert conversation.director.session.plan.component(lodging).status in {
        ComponentStatus.FAILED,
        ComponentStatus.ELICITING,
    }


@pytest.mark.parametrize("profile", ["world", "live"])
def test_a_profile_this_release_cannot_build_names_the_feature_that_will(
    tmp_path: Path, profile: str
) -> None:
    """``world`` and ``live`` resolve in Settings and are refused here, naming their feature."""
    with pytest.raises(ConfigurationError, match=r"F17|F24"):
        build_container(settings_for(tmp_path, TOURGANIZE_OPTION_SOURCE_PROFILE=profile))
