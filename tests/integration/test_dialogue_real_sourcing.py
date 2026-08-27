"""What real sourcing adds to the conversation, beyond F05's scenarios passing unchanged.

That claim itself is tested next door: ``test_dialogue_walkthrough.py`` runs every F05 scenario
twice, once against the fake planner and once against the real one from the Composition Root,
with the same assertions both times — which is F06's Definition of done, and it is the
walkthrough's job rather than this file's.

What is here is the behaviour that only exists once options are real: the slate carries the
digest of what was asked for, a Selection points at an option that came off disk, a refinement
re-sources it, a place nobody recorded still answers, and strict filtering that leaves nothing
becomes a reported failure rather than a dead session. The slates are built by
:class:`~tourganize.application.planning_service.PlanningService` from the fixture tree that
ships with the repository, through the ``OptionSource`` port, and the Director is wired by
``build_container`` rather than by hand. A couple of F05's shapes are re-asserted here too,
where what is being checked is the data rather than the state machine.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Final, final

import pytest

from tourganize.application.composition import (
    Container,
    build_container,
    build_dialogue_settings,
)
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
    DialogueContext,
    DialogueDirector,
    DialogueState,
    TurnInterpretation,
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
from tourganize.ports.catalog import ComponentCatalog
from tourganize.ports.interpretation import OptionSlatePlanner, TurnInterpreter

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


#: How a test replaces one of the container's adapters, given the container it was wired into.
InterpreterFactory = Callable[[Container], TurnInterpreter]


class Conversation:
    """A greeted Director built by the Composition Root, and one line per traveller turn."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        planner: OptionSlatePlanner | None = None,
        interpreter: InterpreterFactory | None = None,
        **overrides: str,
    ) -> None:
        self.container = build_container(settings_for(tmp_path, **overrides))
        self.director = DialogueDirector(
            self.container.component_catalog,
            self.container.priority_policy,
            (
                interpreter(self.container)
                if interpreter is not None
                else self.container.turn_interpreter
            ),
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
    return lodging_kind_of(conversation.container.component_catalog)


def lodging_kind_of(catalog: ComponentCatalog) -> str:
    """The same question, asked of a catalog that has no conversation around it yet."""
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
    """F06's DoD, end to end: strict plus an impossible ceiling is an Act, not a dead session.

    Driven through the Director, because the DoD is about what the *conversation* does — "the
    slate is empty **and** the Director emits ``report_sourcing_failure``" — and a planner
    called directly can only show the first half of that.

    The soft conversation is the control: the same turns and the same impossible ceiling, and a
    slate comes back marked. Without it, an empty fixture tree would pass this test just as
    happily as strict filtering does.
    """
    soft, marked = _the_dates_turn(tmp_path)
    strict, refused = _the_dates_turn(tmp_path, TOURGANIZE_OPTION_FILTER_STRICT="true")
    lodging = the_lodging_kind(strict)

    assert names(marked)[0] == PRESENT_SLATE, "the ceiling alone must not empty the slate"
    assert soft.director.session.plan.component(lodging).latest_slate() is not None
    shown = marked[0].payload["options"]
    assert isinstance(shown, tuple)
    assert shown and all(option["filter_notes"] for option in shown), "demoted, and marked"

    assert names(refused)[0] == REPORT_SOURCING_FAILURE
    assert strict.director.session.plan.component(lodging).latest_slate() is None
    assert strict.director.session.state is not DialogueState.CLOSED


def _the_dates_turn(
    tmp_path: Path, **overrides: str
) -> tuple[Conversation, tuple[AssistantAct, ...]]:
    """Open the Paris conversation with an impossible budget, and answer the blocking question."""
    conversation = Conversation(
        tmp_path, interpreter=_also_offering_an_impossible_ceiling, **overrides
    )
    conversation.say("find me a hotel in Paris")
    return conversation, conversation.say("23-28 October 2026")


def _also_offering_an_impossible_ceiling(container: Container) -> TurnInterpreter:
    """The wired interpreter, plus the one value its phrase table cannot read."""
    catalog = container.component_catalog
    schema = catalog.schema_for(lodging_kind_of(catalog))
    return _AlsoOffering(container.turn_interpreter, _impossible_ceiling(schema))


@final
class _AlsoOffering:
    """The real interpreter, with one Requirement Update added to whatever it read.

    The keyword interpreter is F05's deliberate stand-in and reads no money at all, so "under
    €1 a night" is not something a traveller can say to it until F08 arrives. This offers that
    value the way an extracting interpreter would have, on any turn whose focused schema
    declares the field, and leaves everything else — which Kind was raised, which option was
    chosen, which locale is in force — to the adapter the Composition Root wired.
    """

    def __init__(self, inner: TurnInterpreter, update: RequirementUpdate) -> None:
        self._inner = inner
        self._update = update

    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation:
        read = self._inner.interpret(turn, context)
        if self._update.field_name not in context.focus_field_names:
            return read
        return replace(read, requirement_updates=(*read.requirement_updates, self._update))


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
