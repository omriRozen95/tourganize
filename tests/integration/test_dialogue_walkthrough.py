"""The conversation the client described, driven through the files that actually ship.

Every other F05 test uses neutral ``kind_key``s, because a test about the state machine should
not have to name a travel topic. This one is the exception on purpose: it reads
``config/catalog/components.yaml``, ``config/catalog/schemas/`` and
``config/interpretation/keywords.en.yaml`` from the repository and drives the Paris-hotel
opening from F05's Definition of done end to end. It is what proves the shipped configuration —
weights, dependencies, blocking rules, phrase tables — actually hangs together, which no unit
test with a hand-built catalog can.

The travel topics are read *out of the shipped catalog* rather than written down here, for the
same reason ``test_cli_subprocess.py`` reads them: this suite must not become the second place a
``kind_key`` is hardcoded.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest

from tourganize.adapters.catalog.priority import WeightedCatalogPolicy
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.interpretation.keyword import KeywordTurnInterpreter
from tourganize.adapters.options.fake import FixedSlatePlanner
from tourganize.adapters.telemetry.null import NullTelemetrySink
from tourganize.dialogue import (
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLOSE,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    AssistantAct,
    DialogueDirector,
    DialogueSettings,
    DialogueState,
    UserTurn,
)
from tourganize.domain.trip import ComponentStatus

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHIPPED_CATALOG: Final = REPO_ROOT / "config" / "catalog" / "components.yaml"
SHIPPED_SCHEMAS: Final = REPO_ROOT / "config" / "catalog" / "schemas"
SHIPPED_KEYWORDS: Final = REPO_ROOT / "config" / "interpretation"


def shipped_kinds_by_weight() -> list[str]:
    """The enabled shipped ``kind_key``s, heaviest first, read from the catalog file itself."""
    catalog = YamlComponentCatalog(SHIPPED_CATALOG, SHIPPED_SCHEMAS)
    ordered = sorted(catalog.enabled_kinds(), key=lambda kind: -kind.priority_weight)
    return [kind.kind_key for kind in ordered]


def the_lodging_kind() -> str:
    """The shipped Component Kind whose Requirement Schema asks for a place *and* a date range.

    Read out of the schemas rather than named, so this test knows which Kind the utterance
    "a hotel in Paris" is about without a topic string appearing in it.
    """
    catalog = YamlComponentCatalog(SHIPPED_CATALOG, SHIPPED_SCHEMAS)
    for kind in catalog.enabled_kinds():
        schema = catalog.schema_for(kind.kind_key)
        rules = {rule.name for rule in schema.blocking_rules}
        if {"where", "when"} <= rules and kind.requires_outcome_of:
            return kind.kind_key
    raise AssertionError(f"{SHIPPED_SCHEMAS} declares no kind with a where-and-when rule")


class Walkthrough:
    """A greeted Director over the shipped configuration, and one line per traveller turn."""

    def __init__(self, *, offer_batch: int = 2) -> None:
        self.clock = FrozenClock(DEFAULT_MOMENT)
        self.director = DialogueDirector(
            YamlComponentCatalog(SHIPPED_CATALOG, SHIPPED_SCHEMAS),
            WeightedCatalogPolicy(),
            KeywordTurnInterpreter(SHIPPED_KEYWORDS),
            FixedSlatePlanner(self.clock),
            self.clock,
            NullTelemetrySink(),
            DialogueSettings(offer_batch=offer_batch),
            session_id="walkthrough",
        )
        self.acts: list[AssistantAct] = list(self.director.begin())

    def say(self, text: str) -> tuple[AssistantAct, ...]:
        produced = self.director.handle(
            UserTurn(
                index=self.director.session.next_turn_index,
                text=text,
                received_at=self.clock.now(),
            )
        )
        self.acts += produced
        return produced


def names(acts: Sequence[AssistantAct]) -> list[str]:
    return [act.act for act in acts]


def test_the_shipped_phrase_tables_load() -> None:
    """The other half of ``catalog validate``: the interpretation config parses too."""
    tables = KeywordTurnInterpreter(SHIPPED_KEYWORDS).tables()

    assert {"en", "he"} <= set(tables)
    assert tables["en"].field_for("place")
    assert tables["he"].kinds


def test_the_paris_hotel_opening_asks_for_the_dates_and_then_presents_a_slate() -> None:
    """F05's first scenario, on the shipped files: a question, no slate; then a slate."""
    lodging = the_lodging_kind()
    walkthrough = Walkthrough()

    first = walkthrough.say("find me a hotel in Paris")

    assert names(first) == [ASK_BLOCKING]
    assert first[0].kind_key == lodging
    assert first[0].payload["rule_name"] == "when"

    second = walkthrough.say("23-28 October 2026")

    assert names(second) == [PRESENT_SLATE, ASK_OPTIONAL]
    assert second[0].kind_key == lodging
    held = walkthrough.director.session.plan.component(lodging).requirements
    assert held is not None
    assert held.value_of("place") == "Paris"
    assert str(held.value_of("date_range")) == "2026-10-23/2026-10-28"


def test_mentioned_first_end_to_end_on_the_act_sequence() -> None:
    """The Kind the traveller raised is planned before any other Kind is even offered."""
    lodging = the_lodging_kind()
    walkthrough = Walkthrough()
    walkthrough.say("find me a hotel in Paris")
    walkthrough.say("23-28 October 2026")
    walkthrough.say("2")

    sequence = names(walkthrough.acts)
    assert sequence.index(CONFIRM_SELECTION) < sequence.index(OFFER_UNMENTIONED)
    for act in walkthrough.acts[: sequence.index(CONFIRM_SELECTION)]:
        assert act.kind_key in (None, lodging), act
    assert walkthrough.director.session.plan.component(lodging).status is ComponentStatus.SELECTED


def test_the_first_offer_names_the_top_ranked_unmentioned_kind() -> None:
    """The Priority Policy's answer, seen through the dialogue: heaviest unmentioned first."""
    heaviest_first = shipped_kinds_by_weight()
    lodging = the_lodging_kind()
    expected = next(key for key in heaviest_first if key != lodging)

    walkthrough = Walkthrough(offer_batch=1)
    walkthrough.say("find me a hotel in Paris")
    walkthrough.say("23-28 October 2026")
    walkthrough.say("2")

    offered = walkthrough.acts[-1]
    assert offered.act == OFFER_UNMENTIONED
    assert offered.payload["kind_keys"] == (expected,)


def test_declining_every_offer_leads_to_the_summary_then_close() -> None:
    remaining = len(shipped_kinds_by_weight()) - 1
    walkthrough = Walkthrough(offer_batch=1)
    walkthrough.say("find me a hotel in Paris")
    walkthrough.say("23-28 October 2026")
    walkthrough.say("2")

    for _ in range(remaining - 1):
        assert names(walkthrough.say("no thanks")) == [OFFER_UNMENTIONED]

    closing = walkthrough.say("no thanks")

    assert names(closing) == [DELIVER_SUMMARY, CLOSE]
    assert walkthrough.director.session.state is DialogueState.CLOSED
    assert len(closing[0].payload["declined"]) == remaining


def test_the_whole_walkthrough_starts_with_a_greeting_and_says_nothing_twice() -> None:
    walkthrough = Walkthrough()
    walkthrough.say("find me a hotel in Paris")
    walkthrough.say("23-28 October 2026")
    walkthrough.say("cheaper")
    walkthrough.say("1")
    walkthrough.say("no thanks")

    sequence = names(walkthrough.acts)
    assert sequence[0] == GREET
    assert sequence.count(GREET) == 1
    assert sequence.count(ASK_OPTIONAL) == 1
    assert sequence[-2:] == [DELIVER_SUMMARY, CLOSE]


def test_a_hebrew_turn_switches_the_locale_of_every_act() -> None:
    """Hebrew is a first-class content language from the first turn, and Acts carry the locale."""
    walkthrough = Walkthrough()
    acts = walkthrough.say("מלון")

    assert walkthrough.director.session.locale == "he"
    assert acts
    assert all(act.locale == "he" for act in acts)


@pytest.mark.parametrize("utterance", ["", "?!", "tell me a joke"])
def test_a_turn_nobody_can_read_never_ends_the_conversation(utterance: str) -> None:
    walkthrough = Walkthrough()
    walkthrough.say(utterance)

    assert walkthrough.director.session.state is not DialogueState.CLOSED
