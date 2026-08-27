"""The Dialogue Director: every behavioural rule the client stated, as a scenario.

Each of F05's Definition-of-done scenarios is one named test here, driven through the real
keyword Turn Interpreter and a fake ``OptionSlatePlanner`` — no model, no network, no fixture
files. Neutral ``kind_key``s throughout: a test about the *state machine* should not have to
name a travel topic, and the one scenario that is genuinely about the shipped catalog lives in
``tests/integration/test_dialogue_walkthrough.py``.

The harness is deliberately thin. ``harness.say("...")`` is one traveller turn, and everything
asserted afterwards is read off the Acts, the session or the plan — never off a private
attribute of the Director.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest
from conftest import keyword_table, write_keywords

from tourganize.adapters.catalog.memory import InMemoryComponentCatalog
from tourganize.adapters.catalog.priority import WeightedCatalogPolicy
from tourganize.adapters.clock.fake import FrozenClock
from tourganize.adapters.interpretation.keyword import KeywordTurnInterpreter
from tourganize.adapters.options.fake import FixedSlatePlanner
from tourganize.dialogue import (
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CLARIFY_INTERPRETER_FAILED,
    CLARIFY_NOT_UNDERSTOOD,
    CLARIFY_STILL_MISSING,
    CLARIFY_UNRESOLVED_CHOICE,
    CLOSE,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    REPORT_SOURCING_FAILURE,
    TURN_EVENT_KIND,
    AssistantAct,
    DialogueContext,
    DialogueDirector,
    DialogueSettings,
    DialogueState,
    PlanningSession,
    TurnIntent,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.errors import (
    ContractViolationError,
    InvariantViolationError,
    SessionClosedError,
)
from tourganize.domain.options import OptionSlate
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.domain.trip import ComponentStatus, PlanComponent, TripPlan
from tourganize.ports.interpretation import OptionSlatePlanner, TurnInterpreter
from tourganize.ports.platform import TelemetryEvent

S = DialogueState
C = ComponentStatus

#: A phrase table for the three neutral Component Kinds these scenarios drive. The shared one
#: names ``gamma``, which :data:`KINDS` does not declare; everything else about it is the same,
#: so it is derived rather than copied.
DIRECTOR_KEYWORDS: Final = keyword_table("alpha", "beta", "delta")

#: Three enabled Kinds with distinct weights and one Outcome Dependency, so that the Agenda has
#: something to order and the offer queue something to work through.
KINDS: Final = (
    ComponentKind("alpha", "component.alpha", 300, "alpha.v1"),
    ComponentKind("beta", "component.beta", 200, "beta.v1", ("alpha",)),
    ComponentKind("delta", "component.delta", 100, "delta.v1"),
)

#: The payload keys whose string values are allowed to read like prose. Message keys never do
#: — they have no spaces — so in practice this is about a Plan Option's declared ``facts``,
#: which come from a provider and are not the dialogue's to police.
PROSE_ALLOWLIST: Final = frozenset(
    {
        "facts",
        "message_keys",
        "prompt_message_keys",
        "example_message_keys",
        "reason_message_key",
    }
)


def _place(name: str, prompt: str, *, blocking: bool = True) -> FieldSpec:
    return FieldSpec(
        name=name,
        field_kind=FieldKind.PLACE,
        obligation=Obligation.BLOCKING if blocking else Obligation.OPTIONAL,
        prompt_message_key=prompt,
        example_message_key=f"example.{prompt}",
    )


ALPHA_SCHEMA: Final = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(
        _place("place", "ask.alpha.place"),
        FieldSpec(
            name="date_range",
            field_kind=FieldKind.DATE_RANGE,
            obligation=Obligation.BLOCKING,
            prompt_message_key="ask.alpha.date_range",
            example_message_key="example.alpha.date_range",
        ),
        FieldSpec("starts_on", FieldKind.DATE, Obligation.OPTIONAL, "ask.alpha.starts_on"),
        FieldSpec("ends_on", FieldKind.DATE, Obligation.OPTIONAL, "ask.alpha.ends_on"),
        FieldSpec("party_size", FieldKind.INTEGER, Obligation.OPTIONAL, "ask.alpha.party_size"),
    ),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",), ("starts_on", "ends_on"))),
    ),
)

BETA_SCHEMA: Final = RequirementSchema(
    schema_key="beta.v1",
    component_kind="beta",
    fields=(_place("place", "ask.beta.place"),),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)

DELTA_SCHEMA: Final = RequirementSchema(
    schema_key="delta.v1",
    component_kind="delta",
    fields=(_place("place", "ask.delta.place"),),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)

#: Keyed by ``schema_key``, because a scenario that narrows the catalog to one Kind has to
#: narrow the schemas with it — the fake catalog refuses a schema no Kind declares, exactly as
#: the file-backed one does.
SCHEMAS: Final[Mapping[str, RequirementSchema]] = {
    schema.schema_key: schema for schema in (ALPHA_SCHEMA, BETA_SCHEMA, DELTA_SCHEMA)
}


class RecordingSink:
    """A ``TelemetrySink`` that keeps what it was given, so a test can read the Turn Ledger."""

    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def record(self, event: TelemetryEvent) -> None:
        self.events.append(event)

    @property
    def degraded(self) -> bool:
        return False


class RaisingPlanner:
    """An ``OptionSlatePlanner`` that always raises. A provider having a bad day."""

    def plan(
        self, kind_key: str, requirements: RequirementSet, plan: TripPlan, round_index: int
    ) -> OptionSlate:
        raise RuntimeError(f"no {kind_key} anywhere")


class EmptyPlanner:
    """An ``OptionSlatePlanner`` that answers with a slate holding nothing."""

    def plan(
        self, kind_key: str, requirements: RequirementSet, plan: TripPlan, round_index: int
    ) -> OptionSlate:
        return OptionSlate(kind_key=kind_key, round_index=round_index)


class WrongRoundPlanner:
    """An ``OptionSlatePlanner`` that answers a question nobody asked."""

    def plan(
        self, kind_key: str, requirements: RequirementSet, plan: TripPlan, round_index: int
    ) -> OptionSlate:
        return OptionSlate(kind_key=kind_key, round_index=round_index + 7)


class RaisingInterpreter:
    """A ``TurnInterpreter`` that always raises. Caught once, then propagated."""

    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation:
        raise RuntimeError("the model is on fire")


class ScriptedInterpreter:
    """A ``TurnInterpreter`` that answers with whatever it was built with.

    For the readings the keyword interpreter cannot produce — an undeclared field name, a
    Component Kind the catalog does not declare — which are exactly the ones an *extraction*
    prompt drifting away from a schema (F08) would produce.
    """

    def __init__(self, *readings: object) -> None:
        self._readings = list(readings)

    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation:
        return self._readings.pop(0)  # type: ignore[return-value]


@dataclass
class Harness:
    """One Director, the sink it writes to, and a one-line way to take a turn."""

    director: DialogueDirector
    sink: RecordingSink
    clock: FrozenClock
    said: list[str] = field(default_factory=list)

    def say(self, text: str) -> tuple[AssistantAct, ...]:
        """One traveller turn, indexed the way a surface would index it."""
        self.said.append(text)
        return self.director.handle(
            UserTurn(
                index=self.session.next_turn_index,
                text=text,
                received_at=self.clock.now(),
            )
        )

    @property
    def session(self) -> PlanningSession:
        return self.director.session

    @property
    def state(self) -> DialogueState:
        return self.director.session.state

    @property
    def plan(self) -> TripPlan:
        return self.director.session.plan

    def component(self, kind_key: str) -> PlanComponent:
        return self.plan.component(kind_key)

    def every_act(self) -> tuple[AssistantAct, ...]:
        return self.director.session.acts()


HarnessFactory = Callable[..., Harness]


@pytest.fixture
def harness_factory(tmp_path: Path, frozen_clock: FrozenClock) -> HarnessFactory:
    """Build a greeted Director over the neutral catalog, with whatever planner a test needs."""
    directory = write_keywords(tmp_path, {"en": DIRECTOR_KEYWORDS})

    def factory(
        *,
        planner: OptionSlatePlanner | None = None,
        settings: DialogueSettings | None = None,
        interpreter: TurnInterpreter | None = None,
        kinds: Iterable[ComponentKind] = KINDS,
        greet: bool = True,
    ) -> Harness:
        sink = RecordingSink()
        declared = tuple(kinds)
        director = DialogueDirector(
            InMemoryComponentCatalog(declared, tuple(SCHEMAS[k.schema_key] for k in declared)),
            WeightedCatalogPolicy(),
            interpreter if interpreter is not None else KeywordTurnInterpreter(directory),
            planner if planner is not None else FixedSlatePlanner(frozen_clock),
            frozen_clock,
            sink,
            settings if settings is not None else DialogueSettings(),
            session_id="session-1",
        )
        if greet:
            director.begin()
        return Harness(director=director, sink=sink, clock=frozen_clock)

    return factory


def acts_of(acts: Sequence[AssistantAct]) -> list[str]:
    return [act.act for act in acts]


def prose_in(acts: Iterable[AssistantAct]) -> list[str]:
    """Every payload string that reads like a sentence, as readable counterexamples.

    The heuristic F05's Definition of done names: more than three space-separated words, outside
    the allowlisted keys. Empty is the invariant — the domain holds no prose, and an Act payload
    is the last place it could have crept in.
    """
    found: list[str] = []

    def walk(key: str, value: object) -> None:
        if key in PROSE_ALLOWLIST:
            return
        if isinstance(value, str):
            if len(value.split()) > 3:
                found.append(f"{key}={value!r}")
            return
        if isinstance(value, Mapping):
            for inner, item in value.items():
                walk(str(inner), item)
            return
        if isinstance(value, tuple | list):
            for item in value:
                walk(key, item)

    for act in acts:
        for key, value in act.payload.items():
            walk(key, value)
    return found


# -- greeting and the session's edges -------------------------------------------------------


def test_begin_greets_once_and_records_it(harness_factory: HarnessFactory) -> None:
    harness = harness_factory(greet=False)
    acts = harness.director.begin()

    assert acts_of(acts) == [GREET]
    assert harness.state is S.GREETING
    assert harness.director.session.transcript[0].turn is None

    with pytest.raises(InvariantViolationError, match="already begun"):
        harness.director.begin()


def test_the_greeting_carries_the_locale_it_was_begun_in(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(greet=False)
    acts = harness.director.begin("he")

    assert acts[0].locale == "he"
    assert harness.director.session.locale == "he"


def test_turns_must_arrive_in_order(harness_factory: HarnessFactory) -> None:
    """A surface asks the session for ``next_turn_index``; a repeat is a surface bug."""
    harness = harness_factory()
    harness.say("hello")

    with pytest.raises(InvariantViolationError, match="turns arrive in order"):
        harness.director.handle(UserTurn(index=0, text="hello", received_at=harness.clock.now()))


def test_handle_wants_a_user_turn(harness_factory: HarnessFactory) -> None:
    with pytest.raises(InvariantViolationError, match="UserTurn"):
        harness_factory().director.handle("hello")  # type: ignore[arg-type]


# -- blocking gaps before sourcing -----------------------------------------------------------


def test_blocking_before_planning(harness_factory: HarnessFactory) -> None:
    """The first scenario of the DoD: no dates, so a question and *no* slate — then a slate."""
    harness = harness_factory()

    first = harness.say("an alpha in Paris")

    assert acts_of(first) == [ASK_BLOCKING]
    assert first[0].kind_key == "alpha"
    assert first[0].payload["rule_name"] == "when"
    assert harness.state is S.ELICITING_BLOCKING
    assert harness.component("alpha").status is C.ELICITING

    second = harness.say("23-28 October 2026")

    assert PRESENT_SLATE in acts_of(second)
    assert harness.state is S.AWAITING_CHOICE
    assert harness.component("alpha").round_count == 1


def test_one_blocking_question_per_act(harness_factory: HarnessFactory) -> None:
    """Both ``where`` and ``when`` are missing; exactly one question comes out per turn."""
    harness = harness_factory()

    first = harness.say("an alpha")
    assert acts_of(first) == [ASK_BLOCKING]
    assert first[0].payload["rule_name"] == "where"

    second = harness.say("in Paris")
    assert acts_of(second) == [ASK_BLOCKING]
    assert second[0].payload["rule_name"] == "when"


def test_the_question_names_every_way_the_rule_could_be_satisfied(
    harness_factory: HarnessFactory,
) -> None:
    """A Blocking Rule is a rule over field *groups*, and the Act says so."""
    harness = harness_factory()
    act = harness.say("an alpha in Paris")[0]

    assert act.payload["field_groups"] == (("date_range",), ("starts_on", "ends_on"))
    assert act.payload["preferred_fields"] == ("date_range",)
    assert act.payload["prompt_message_keys"] == ("ask.alpha.date_range",)
    assert act.payload["attempt"] == 1


def test_a_pending_question_remembers_the_rule_and_counts_the_asks(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris")
    harness.say("in Paris")

    question = harness.director.session.pending_question
    assert question is not None
    assert (question.kind_key, question.rule_name) == ("alpha", "when")
    assert question.attempts == 2
    assert question.field_names == ("date_range", "starts_on", "ends_on")


def test_after_the_reask_limit_the_example_is_offered_then_the_component_fails(
    harness_factory: HarnessFactory,
) -> None:
    """A conversation that asks the same thing for ever is worse than one that gives up."""
    harness = harness_factory(settings=DialogueSettings(max_reasks=2))
    harness.say("an alpha in Paris")

    assert acts_of(harness.say("in Paris")) == [ASK_BLOCKING]

    clarified = harness.say("in Paris")
    assert acts_of(clarified) == [CLARIFY]
    assert clarified[0].payload["reason_code"] == CLARIFY_STILL_MISSING
    assert clarified[0].payload["example_message_keys"] == ("example.alpha.date_range",)

    given_up = harness.say("in Paris")
    assert harness.component("alpha").status is C.FAILED
    assert acts_of(given_up) == [DELIVER_SUMMARY]
    assert given_up[0].payload["open_mentioned"] == ("alpha",)


def test_giving_up_does_not_reset_the_count_so_the_cycle_cannot_restart(
    harness_factory: HarnessFactory,
) -> None:
    """Ask, ask, clarify, fail — and then nothing, however long the traveller keeps going.

    The Pending Question is kept when the Director gives up. Clearing it would make the next
    turn's attempt look like the first one, and the same component would be asked about,
    clarified and failed again for as many turns as the traveller had patience for.
    """
    harness = harness_factory(settings=DialogueSettings(max_reasks=2, failure_skip=2))
    harness.say("an alpha in Paris")
    for _ in range(9):
        harness.say("in Paris")

    emitted = acts_of(harness.every_act())

    assert emitted.count(ASK_BLOCKING) == 2
    assert emitted.count(CLARIFY) == 1
    assert harness.component("alpha").status is C.FAILED
    assert harness.component("alpha").consecutive_failures == 2
    standing = harness.director.session.pending_question
    assert standing is not None and standing.rule_name == "when"
    # And the Agenda has stepped it over for good, so no later turn can pick it up again.
    assert acts_of(harness.say("in Paris")) == [DELIVER_SUMMARY]


def test_an_invalid_value_is_reported_not_asked_for_again(
    harness_factory: HarnessFactory,
) -> None:
    """A reversed range is not a *missing* range, and the component does not reach SOURCING."""
    harness = harness_factory()
    acts = harness.say("an alpha in Paris 2026-10-28/2026-10-23")

    assert acts_of(acts) == [REPORT_INVALID_VALUE]
    assert acts[0].payload["field_name"] == "date_range"
    assert acts[0].payload["reason_message_key"] == "requirement.invalid.date_range_reversed"
    assert harness.state is S.ELICITING_BLOCKING
    assert harness.component("alpha").status is C.ELICITING
    assert harness.component("alpha").round_count == 0


def test_the_same_invalid_value_is_not_reported_for_ever(
    harness_factory: HarnessFactory,
) -> None:
    """``report_invalid_value`` is "a re-ask, not a rejection of the turn" — so it is counted.

    Six identical reversed ranges used to produce six identical reports, because the invalid
    path kept no Pending Question and so had no count to reach a limit with. It escalates the
    same way an unanswered question does: report, report, example, then give up.
    """
    harness = harness_factory(settings=DialogueSettings(max_reasks=2, failure_skip=2))
    reversed_range = "an alpha in Paris 2026-10-28/2026-10-23"
    for _ in range(6):
        harness.say(reversed_range)

    emitted = acts_of(harness.every_act())

    assert emitted.count(REPORT_INVALID_VALUE) == 2
    assert emitted.count(CLARIFY) == 1
    assert harness.component("alpha").status is C.FAILED
    assert harness.component("alpha").round_count == 0


def test_an_invalid_value_and_a_missing_one_count_against_the_same_obligation(
    harness_factory: HarnessFactory,
) -> None:
    """A Pending Question is about a Blocking Rule, and both ways of failing it are attempts."""
    harness = harness_factory(settings=DialogueSettings(max_reasks=3))
    harness.say("an alpha in Paris")

    harness.say("2026-10-28/2026-10-23")
    standing = harness.director.session.pending_question

    assert standing is not None
    assert (standing.rule_name, standing.attempts) == ("when", 2)


def test_the_invalid_report_carries_no_english_detail(
    harness_factory: HarnessFactory,
) -> None:
    """``InvalidValue.detail`` is diagnostic English and belongs in a log, not in an Act."""
    harness = harness_factory()
    payload = harness.say("an alpha in Paris 2026-10-28/2026-10-23")[0].payload

    assert set(payload) == {"field_name", "reason_message_key", "attempt"}
    assert payload["attempt"] == 1


# -- optional filters ------------------------------------------------------------------------


def test_optional_filters_never_block_and_are_asked_once(
    harness_factory: HarnessFactory,
) -> None:
    """A slate on the same turn, at most two optional fields beside it, and never again."""
    harness = harness_factory()
    acts = harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    assert acts_of(acts) == [PRESENT_SLATE, ASK_OPTIONAL]
    assert acts[1].payload["field_names"] == ("starts_on", "ends_on")
    assert len(acts[1].payload["prompt_message_keys"]) == 2

    refined = harness.say("cheaper")
    assert acts_of(refined) == [PRESENT_SLATE]
    assert ASK_OPTIONAL not in acts_of(harness.say("cheaper"))


def test_the_optional_bundle_honours_its_configured_limit(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(settings=DialogueSettings(optional_ask_limit=1))
    acts = harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    assert acts[1].payload["field_names"] == ("starts_on",)


def test_an_optional_value_may_still_be_supplied_by_any_later_turn(
    harness_factory: HarnessFactory,
) -> None:
    """Ignoring the question is fine; answering it later is fine too."""
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")
    harness.say("cheaper")

    held = harness.component("alpha").requirements
    assert held is not None
    assert held.value_of("place") == "Paris"


def test_an_optional_field_ignored_at_the_slate_may_be_supplied_later(
    harness_factory: HarnessFactory,
) -> None:
    """Any turn may still supply them: the optional bundle is a question, not a deadline.

    Driven through a scripted interpreter rather than the keyword one, because the phrase table
    reads a place and a date range and nothing else — the shapes an optional filter arrives in
    are F08's problem, not this rule's.
    """
    harness = harness_factory(
        interpreter=ScriptedInterpreter(
            TurnInterpretation(
                intent=TurnIntent.ANSWER_QUESTION,
                mentioned_kinds=("alpha",),
                requirement_updates=(
                    RequirementUpdate(field_name="place", value="Paris"),
                    RequirementUpdate(field_name="date_range", value="2026-10-23/2026-10-28"),
                ),
            ),
            TurnInterpretation(
                intent=TurnIntent.ANSWER_QUESTION,
                requirement_updates=(RequirementUpdate(field_name="party_size", value=3),),
            ),
        )
    )
    first = harness.say("an alpha in Paris 2026-10-23/2026-10-28")
    assert acts_of(first) == [PRESENT_SLATE, ASK_OPTIONAL]
    assert "party_size" not in first[1].payload["field_names"]

    later = harness.say("three of us")

    held = harness.component("alpha").requirements
    assert held is not None
    assert held.value_of("party_size") == 3
    # And it is not asked about again: the bundle rides with round zero and no other.
    assert ASK_OPTIONAL not in acts_of(later)


# -- the choose-or-refine loop ---------------------------------------------------------------


def test_choosing_records_a_selection_and_moves_the_focus_on(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha and a beta in Paris")
    harness.say("23-28 October 2026")

    acts = harness.say("2")

    assert acts_of(acts) == [CONFIRM_SELECTION, ASK_BLOCKING]
    assert acts[0].payload["option_id"] == "alpha-r0-2"
    selection = harness.component("alpha").selection
    assert selection is not None
    assert selection.option_id == "alpha-r0-2"
    assert selection.chosen_at_turn == 2
    assert harness.director.session.focus_kind == "beta"
    assert acts[1].kind_key == "beta"


def test_a_choice_may_name_the_option_id_itself(harness_factory: HarnessFactory) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    harness.say("'alpha-r0-3'")

    selection = harness.component("alpha").selection
    assert selection is not None
    assert selection.option_id == "alpha-r0-3"


def test_an_unresolvable_choice_asks_again_and_changes_nothing(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    acts = harness.say("9")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_UNRESOLVED_CHOICE
    assert harness.state is S.AWAITING_CHOICE
    assert harness.component("alpha").selection is None


def test_three_refinements_produce_three_new_rounds_and_no_selection(
    harness_factory: HarnessFactory,
) -> None:
    """The loop is unbounded, and slate history is never discarded."""
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    rounds = []
    for _ in range(3):
        acts = harness.say("cheaper")
        assert acts_of(acts) == [PRESENT_SLATE]
        rounds.append(acts[0].payload["round_index"])

    component = harness.component("alpha")
    assert rounds == [1, 2, 3]
    assert component.round_count == 4
    assert [slate.round_index for slate in component.slates] == [0, 1, 2, 3]
    assert component.selection is None
    assert harness.state is S.AWAITING_CHOICE


def test_a_refinement_that_invalidates_a_value_goes_back_to_eliciting(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    acts = harness.say("make it 2026-11-05/2026-11-01")

    assert acts_of(acts) == [REPORT_INVALID_VALUE]
    assert harness.state is S.ELICITING_BLOCKING
    assert harness.component("alpha").round_count == 1


def test_a_refinement_carries_the_new_values_into_the_next_round(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")
    harness.say("make it 2026-11-01/2026-11-05")

    held = harness.component("alpha").requirements
    assert held is not None
    assert str(held.value_of("date_range")) == "2026-11-01/2026-11-05"
    assert harness.component("alpha").round_count == 2


def test_a_mid_slate_mention_of_another_kind_is_noted_not_obeyed(
    harness_factory: HarnessFactory,
) -> None:
    """Mentioned-First is a rule about the *Agenda*, not a licence to interrupt a slate."""
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    acts = harness.say("2 and also a beta")

    assert acts_of(acts) == [CONFIRM_SELECTION, ASK_BLOCKING]
    assert acts[0].kind_key == "alpha"
    assert acts[0].payload["noted_kinds"] == ("beta",)
    assert harness.plan.component("beta").is_mentioned


# -- proactive offers ------------------------------------------------------------------------


def a_settled_alpha(harness: Harness) -> None:
    """Get one component chosen, which is what empties the mentioned band."""
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")
    harness.say("2")


def test_the_offer_waits_for_the_mentioned_band_to_empty(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    harness.say("an alpha and a beta in Paris")
    harness.say("23-28 October 2026")

    assert OFFER_UNMENTIONED not in acts_of(harness.say("1"))
    assert harness.director.session.focus_kind == "beta"


def test_offer_then_decline_then_the_next_offer_then_the_summary(
    harness_factory: HarnessFactory,
) -> None:
    """The DoD's offer scenario: the top-ranked Kind first, then the next, then closing."""
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    a_settled_alpha(harness)

    assert harness.state is S.OFFERING_UNMENTIONED
    offered = harness.every_act()[-1]
    assert offered.act == OFFER_UNMENTIONED
    assert offered.payload["kind_keys"] == ("beta",)
    assert offered.payload["message_keys"] == ("component.beta",)

    second = harness.say("no thanks")
    assert acts_of(second) == [OFFER_UNMENTIONED]
    assert second[0].payload["kind_keys"] == ("delta",)
    assert harness.plan.component("beta").status is C.DECLINED

    closing = harness.say("no thanks")
    assert acts_of(closing) == [DELIVER_SUMMARY, CLOSE]
    assert harness.state is S.CLOSED
    assert closing[0].payload["declined"] == ("beta", "delta")


def test_the_offer_batch_may_name_more_than_one_kind(
    harness_factory: HarnessFactory,
) -> None:
    """The documented default is 2, and one Act names both — never two Acts."""
    harness = harness_factory()
    a_settled_alpha(harness)

    offered = harness.every_act()[-1]
    assert offered.payload["kind_keys"] == ("beta", "delta")
    assert offered.payload["remaining"] == 0

    closing = harness.say("no thanks")
    assert acts_of(closing) == [DELIVER_SUMMARY, CLOSE]


def test_accepting_an_offer_re_enters_planning_for_that_kind(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    a_settled_alpha(harness)

    accepted = harness.say("yes please")

    assert acts_of(accepted) == [ASK_BLOCKING]
    assert accepted[0].kind_key == "beta"
    assert harness.state is S.ELICITING_BLOCKING
    assert harness.plan.component("beta").is_mentioned


def test_an_accepted_kind_is_never_offered_again(harness_factory: HarnessFactory) -> None:
    """Accepting is a mention, and a mentioned Kind is planned rather than offered."""
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    a_settled_alpha(harness)
    harness.say("yes please")
    harness.say("in Lisbon")
    harness.say("1")

    offers = [act for act in harness.every_act() if act.act == OFFER_UNMENTIONED]
    named = [key for act in offers for key in act.payload["kind_keys"]]
    assert named.count("beta") == 1
    assert harness.plan.component("beta").status is C.SELECTED


def test_a_declined_kind_is_never_offered_again(harness_factory: HarnessFactory) -> None:
    """The client's hard rule: declining answers an *offer*, so nothing offers it again."""
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    a_settled_alpha(harness)
    harness.say("no thanks")

    harness.say("a beta in Lisbon")
    harness.say("1")

    offers = [act for act in harness.every_act() if act.act == OFFER_UNMENTIONED]
    named = [key for act in offers for key in act.payload["kind_keys"]]
    assert named.count("beta") == 1


def test_a_declined_kind_the_traveller_raises_again_is_planned(
    harness_factory: HarnessFactory,
) -> None:
    """The other half of "decline is about offers, not prohibition" (D18).

    ``DECLINED -> ELICITING`` is the one edge out of ``DECLINED``, and it is walked only here:
    the traveller raised the Kind themselves, which also marks it mentioned, which is what
    keeps the never-offered-again rule true without a second rule enforcing it.
    """
    harness = harness_factory(settings=DialogueSettings(offer_batch=1))
    a_settled_alpha(harness)
    harness.say("no thanks")
    assert harness.plan.component("beta").status is C.DECLINED

    acts = harness.say("a beta in Lisbon")

    assert acts_of(acts) == [PRESENT_SLATE]
    assert acts[0].kind_key == "beta"
    assert harness.plan.component("beta").is_mentioned
    assert harness.state is S.AWAITING_CHOICE

    chosen = harness.say("1")

    assert CONFIRM_SELECTION in acts_of(chosen)
    assert harness.plan.component("beta").status is C.SELECTED


def test_a_yes_that_names_one_kind_answers_only_for_that_one(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    a_settled_alpha(harness)

    harness.say("yes please, a beta")

    assert harness.plan.component("beta").is_mentioned
    assert not harness.plan.has_component("delta")
    assert harness.plan.mentioned_kinds() == ("alpha", "beta")


def test_an_answer_to_an_offer_nobody_made_asks_for_clarification(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    acts = harness.say("yes please")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_NOT_UNDERSTOOD


# -- closing ---------------------------------------------------------------------------------


def test_a_plan_with_nothing_left_to_offer_closes_itself(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(kinds=(KINDS[0],))
    a_settled_alpha(harness)

    assert harness.state is S.CLOSED
    assert acts_of(harness.every_act())[-2:] == [DELIVER_SUMMARY, CLOSE]


def test_ending_mid_slate_reports_the_open_component_then_closes(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    acts = harness.say("goodbye")

    assert acts_of(acts) == [DELIVER_SUMMARY, CLOSE]
    assert acts[0].payload["open_mentioned"] == ("alpha",)
    assert acts[0].payload["is_closeable"] is False
    assert harness.state is S.CLOSED

    with pytest.raises(SessionClosedError, match="resume"):
        harness.say("actually, wait")


def test_ending_from_the_greeting_is_honoured_too(harness_factory: HarnessFactory) -> None:
    """``END_SESSION`` from any state: a traveller who says goodbye means it."""
    harness = harness_factory()
    acts = harness.say("goodbye")

    assert acts_of(acts) == [DELIVER_SUMMARY, CLOSE]
    assert acts[0].payload["selected"] == ()


def test_the_summary_names_the_selections_it_reports(harness_factory: HarnessFactory) -> None:
    harness = harness_factory(kinds=(KINDS[0],))
    a_settled_alpha(harness)

    summary = next(act for act in harness.every_act() if act.act == DELIVER_SUMMARY)
    assert summary.payload["selected"] == ("alpha",)
    assert summary.payload["is_closeable"] is True
    assert summary.payload["selections"] == (
        {"kind_key": "alpha", "option_id": "alpha-r0-2", "round_index": 0},
    )


def test_asking_where_we_are_reports_without_closing(harness_factory: HarnessFactory) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    acts = harness.say("where are we")

    assert acts_of(acts) == [DELIVER_SUMMARY]
    assert harness.state is S.AWAITING_CHOICE
    assert not harness.director.session.is_closed


# -- failure containment ---------------------------------------------------------------------


def test_a_planner_that_raises_becomes_an_act_and_the_next_agenda_entry(
    harness_factory: HarnessFactory,
) -> None:
    """The conversation never dies because a provider did."""
    harness = harness_factory(planner=RaisingPlanner())
    acts = harness.say("an alpha and a beta in Paris 2026-10-23/2026-10-28")

    assert acts_of(acts) == [REPORT_SOURCING_FAILURE, ASK_BLOCKING]
    assert acts[0].kind_key == "alpha"
    assert acts[0].payload["reason_code"] == "sourcing_failed"
    assert acts[1].kind_key == "beta"
    # One failure is not a failed component: `FAILED` is where F02 counts the run, and the
    # component is only left there once the run reaches `failure_skip` (default 2).
    assert harness.component("alpha").status is C.ELICITING
    assert harness.component("alpha").consecutive_failures == 1


def test_a_kind_that_keeps_failing_is_stepped_over(
    harness_factory: HarnessFactory, frozen_clock: FrozenClock
) -> None:
    """After the configured run of failures the Agenda skips it, and the rest carries on.

    Only ``alpha`` refuses to source, so what is being watched is whether one broken Component
    Kind can deadlock a conversation that has a perfectly plannable second one.
    """
    harness = harness_factory(
        planner=FixedSlatePlanner(frozen_clock, fails_for=("alpha",)),
        settings=DialogueSettings(failure_skip=2),
    )

    first = harness.say("an alpha and a beta in Paris 2026-10-23/2026-10-28")
    assert acts_of(first) == [REPORT_SOURCING_FAILURE, ASK_BLOCKING]

    second = harness.say("in Lisbon")
    # No `ask_optional`: `beta.v1` declares no optional field, so there is nothing to bundle.
    assert acts_of(second) == [REPORT_SOURCING_FAILURE, PRESENT_SLATE]
    assert harness.component("alpha").consecutive_failures == 2

    third = harness.say("cheaper")
    assert acts_of(third) == [PRESENT_SLATE]
    assert third[0].kind_key == "beta"
    assert REPORT_SOURCING_FAILURE not in acts_of(third)


def test_a_turn_with_nothing_to_ask_rests_where_it_arrived(
    harness_factory: HarnessFactory,
) -> None:
    """A sourcing failure asks nothing, so it must not leave the session claiming to elicit.

    ``ELICITING_BLOCKING`` is a state the Director enters by *asking*, and F11 and F12 read it.
    A session sitting there with no Pending Question and no question emitted is a session that
    says it is waiting for an answer to a question nobody asked.
    """
    harness = harness_factory(kinds=(KINDS[0],), planner=RaisingPlanner())

    acts = harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    assert acts_of(acts) == [REPORT_SOURCING_FAILURE, DELIVER_SUMMARY]
    assert harness.state is S.GREETING
    assert harness.director.session.pending_question is None


def test_eliciting_blocking_is_only_ever_entered_by_asking(
    harness_factory: HarnessFactory,
) -> None:
    """The invariant behind the test above, over a spread of scenarios rather than one."""
    scripts = (
        ("an alpha in Paris", "in Paris", "in Paris", "in Paris", "in Paris"),
        ("an alpha in Paris 2026-10-28/2026-10-23", "still that range"),
        ("an alpha and a beta in Paris 2026-10-23/2026-10-28", "1", "in Lisbon", "1"),
        ("an alpha in Paris 2026-10-23/2026-10-28", "cheaper", "goodbye"),
    )
    eliciting_acts = {ASK_BLOCKING, REPORT_INVALID_VALUE, CLARIFY}
    for script in scripts:
        harness = harness_factory(settings=DialogueSettings(max_reasks=2))
        for text in script:
            if harness.session.is_closed:
                break
            before = harness.state
            acts = harness.say(text)
            entered = harness.state is S.ELICITING_BLOCKING and before is not S.ELICITING_BLOCKING
            if entered:
                assert eliciting_acts & set(acts_of(acts)), (script, text, acts_of(acts))


def test_an_empty_slate_is_a_sourcing_failure_not_a_choice_of_nothing(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(planner=EmptyPlanner())
    acts = harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    assert acts_of(acts) == [REPORT_SOURCING_FAILURE, DELIVER_SUMMARY]
    assert acts[0].payload["reason_code"] == "sourcing_failed"
    assert harness.component("alpha").round_count == 0


def test_a_planner_answering_the_wrong_round_is_refused_at_the_seam(
    harness_factory: HarnessFactory,
) -> None:
    """A planner is replaceable, so its output is checked rather than trusted."""
    harness = harness_factory(planner=WrongRoundPlanner())

    with pytest.raises(ContractViolationError, match="was asked for 'alpha' round 0"):
        harness.say("an alpha in Paris 2026-10-23/2026-10-28")


def test_an_interpreter_that_raises_is_caught_once_then_propagates(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(interpreter=RaisingInterpreter())

    acts = harness.say("an alpha in Paris")
    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_INTERPRETER_FAILED
    assert harness.state is S.GREETING

    with pytest.raises(RuntimeError, match="on fire"):
        harness.say("an alpha in Paris")


def test_a_turn_nobody_can_place_asks_for_clarification(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    acts = harness.say("???")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_NOT_UNDERSTOOD
    assert harness.state is S.GREETING


def test_small_talk_leaves_the_conversation_where_it_was(
    harness_factory: HarnessFactory,
) -> None:
    """No Act in the vocabulary means "acknowledged", and inventing one would invent wording."""
    harness = harness_factory()
    harness.say("an alpha in Paris 2026-10-23/2026-10-28")

    assert harness.say("thanks") == ()
    assert harness.state is S.AWAITING_CHOICE
    # Pinned for F07: a turn may legitimately produce no Act at all, and the exchange is still
    # recorded, so a surface that assumes "at least one Act per turn" is the thing that breaks.
    assert harness.session.transcript[-1].acts == ()
    assert harness.session.transcript[-1].turn is not None


def test_small_talk_in_the_greeting_starts_the_conversation(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    acts = harness.say("hello")

    assert acts_of(acts) == [OFFER_UNMENTIONED]


# -- telemetry, locale and prose -------------------------------------------------------------


def test_one_telemetry_event_per_handle_with_the_states_and_the_agenda(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    harness.say("an alpha in Paris")
    harness.say("23-28 October 2026")

    assert [event.kind for event in harness.sink.events] == [TURN_EVENT_KIND] * 2
    first, second = harness.sink.events
    assert first.session_id == "session-1"
    assert first.fields["state_before"] == "GREETING"
    assert first.fields["state_after"] == "ELICITING_BLOCKING"
    assert first.fields["intent"] == "answer_question"
    assert first.fields["focus_kind"] == "alpha"
    assert first.fields["acts"] == (ASK_BLOCKING,)
    assert first.fields["turn_index"] == 0
    assert second.fields["state_before"] == "ELICITING_BLOCKING"
    assert second.fields["state_after"] == "AWAITING_CHOICE"
    assert ("alpha", "MENTIONED", 0, "ready") in second.fields["agenda"]
    assert isinstance(second.fields["latency_ms"], float)


def test_a_turn_the_interpreter_could_not_read_still_records_an_event(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(interpreter=RaisingInterpreter())
    harness.say("an alpha in Paris")

    assert len(harness.sink.events) == 1
    assert harness.sink.events[0].fields["intent"] is None


def test_the_session_locale_follows_what_the_interpreter_detected(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory()
    acts = harness.say("אלפא")

    assert harness.director.session.locale == "he"
    assert all(act.locale == "he" for act in acts)


def test_no_act_payload_holds_a_sentence(harness_factory: HarnessFactory) -> None:
    """The DoD's automated prose check, over a walk of the whole scenario space."""
    collected: list[AssistantAct] = []

    scripts = (
        ("an alpha in Paris", "23-28 October 2026", "cheaper", "2", "no thanks", "no thanks"),
        ("an alpha in Paris 2026-10-28/2026-10-23", "2026-10-23/2026-10-28", "1"),
        ("an alpha", "in Paris", "where are we", "goodbye"),
        ("???", "hello", "yes please"),
    )
    for script in scripts:
        harness = harness_factory(settings=DialogueSettings(offer_batch=1))
        for text in script:
            if harness.director.session.is_closed:
                break
            harness.say(text)
        collected += list(harness.every_act())

    assert len(collected) > 10
    assert prose_in(collected) == []


def test_the_prose_check_would_notice_a_sentence(harness_factory: HarnessFactory) -> None:
    """A planted violation, so the check above is known to be able to fail."""
    planted = AssistantAct(act=CLARIFY, payload={"reason_code": "I did not understand that"})
    allowed = AssistantAct(act=CLARIFY, payload={"facts": "a room with a view of the sea"})

    assert prose_in([planted]) == ["reason_code='I did not understand that'"]
    assert prose_in([allowed]) == []


# -- prompt and schema drift -----------------------------------------------------------------


def test_a_value_for_a_field_no_schema_declares_is_reported_not_merged(
    harness_factory: HarnessFactory,
) -> None:
    """An update naming an undeclared field is what surfaces prompt/schema drift (F03)."""
    harness = harness_factory(
        interpreter=ScriptedInterpreter(
            TurnInterpretation(
                intent=TurnIntent.ANSWER_QUESTION,
                mentioned_kinds=("alpha",),
                requirement_updates=(RequirementUpdate(field_name="star_rating", value=4),),
            )
        )
    )

    acts = harness.say("an alpha with four stars")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == "undeclared_field"
    assert acts[0].payload["field_name"] == "star_rating"
    assert harness.component("alpha").requirements is None


def test_a_mid_slate_value_for_an_undeclared_field_is_reported_too(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(
        interpreter=ScriptedInterpreter(
            TurnInterpretation(
                intent=TurnIntent.ANSWER_QUESTION,
                mentioned_kinds=("alpha",),
                requirement_updates=(
                    RequirementUpdate(field_name="place", value="Paris"),
                    RequirementUpdate(field_name="date_range", value="2026-10-23/2026-10-28"),
                ),
            ),
            TurnInterpretation(
                intent=TurnIntent.REFINE,
                requirement_updates=(RequirementUpdate(field_name="star_rating", value=4),),
            ),
        )
    )
    assert PRESENT_SLATE in acts_of(harness.say("an alpha in Paris 2026-10-23/2026-10-28"))

    acts = harness.say("four stars please")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == "undeclared_field"
    assert harness.state is S.AWAITING_CHOICE
    assert harness.component("alpha").round_count == 1


def test_a_kind_the_catalog_does_not_declare_is_ignored(
    harness_factory: HarnessFactory,
) -> None:
    """A stale prompt or phrase table is not a licence to plan something nobody declared."""
    harness = harness_factory(
        interpreter=ScriptedInterpreter(
            TurnInterpretation(intent=TurnIntent.ANSWER_QUESTION, mentioned_kinds=("omega",))
        )
    )

    acts = harness.say("an omega")

    assert not harness.plan.has_component("omega")
    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_NOT_UNDERSTOOD


def test_an_interpreter_that_answers_with_something_else_is_refused_at_the_seam(
    harness_factory: HarnessFactory,
) -> None:
    """An interpreter is replaceable, so its output is checked rather than trusted."""
    harness = harness_factory(interpreter=ScriptedInterpreter("choose the second one"))

    with pytest.raises(ContractViolationError, match="must return a TurnInterpretation"):
        harness.say("2")


def test_a_choice_arriving_when_no_slate_is_up_asks_for_clarification(
    harness_factory: HarnessFactory,
) -> None:
    harness = harness_factory(
        interpreter=ScriptedInterpreter(
            TurnInterpretation(intent=TurnIntent.CHOOSE_OPTION, chosen_option_ref="2")
        )
    )

    acts = harness.say("2")

    assert acts_of(acts) == [CLARIFY]
    assert acts[0].payload["reason_code"] == CLARIFY_NOT_UNDERSTOOD
