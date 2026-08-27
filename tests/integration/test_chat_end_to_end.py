"""F07's Definition of done, made observable: the walking skeleton, end to end.

Everything here runs the application as it is wired — the shipped `config/` (catalog, schemas,
phrase tables, Message Catalogue, Display Profiles) and the shipped `fixtures/`, with only the
writable paths redirected into ``tmp_path``. Nothing is stubbed except where a test needs a value
the keyword stand-in cannot read, and even then the stand-in stays underneath it.

Two conventions are inherited from ``test_dialogue_real_sourcing.py`` and are deliberate. The
Component Kind under test is found by **reading** the shipped catalog for the one whose schema
asks for a place and a date range, rather than by naming a travel topic; and the impossible budget
ceiling that makes every option fail an optional filter is read off that Kind's schema the same
way. ``_AlsoOffering`` and ``_impossible_ceiling`` below are copies of that module's private
helpers rather than imports of them — a test module reaching into another test module's private
names is the coupling that makes both of them hard to change.

Assertions prefer *structure* — the sequence of Act names, the number of Option Rows, the writing
direction — over exact English wording, so that F08's composed phrasing and F10's real Hebrew can
improve every sentence in this repository without touching this file. The one place wording is
asserted is the missing-key marker, which is a contract rather than a phrasing.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, final

import pytest

from tourganize.adapters.presentation.scripted import ScriptedSurface, read_script
from tourganize.application.composition import (
    Container,
    build_container,
    build_dialogue_settings,
    build_surface,
)
from tourganize.application.session_runner import SessionOutcome, run
from tourganize.cli import EXIT_OK, main
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
    DialogueContext,
    DialogueDirector,
    DialogueState,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.requirements import FieldKind, RequirementSchema, RequirementUpdate
from tourganize.language.act_renderer import RenderedAct, missing_marker
from tourganize.platform.settings import Settings
from tourganize.ports.catalog import ComponentCatalog
from tourganize.ports.interpretation import TurnInterpreter

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHIPPED_CONFIG: Final = REPO_ROOT / "config"
SHIPPED_FIXTURES: Final = REPO_ROOT / "fixtures" / "options"
SHIPPED_MESSAGES: Final = SHIPPED_CONFIG / "messages"
CONVERSATIONS: Final = REPO_ROOT / "fixtures" / "conversations"
PARIS: Final = CONVERSATIONS / "paris.txt"
PARIS_HEBREW: Final = CONVERSATIONS / "paris.he.txt"

#: **The first Golden Conversation.** ``fixtures/conversations/paris.txt`` is the Phase 1 demo
#: written down, and this is the sequence of Assistant Acts replaying it must produce. Act *names*
#: rather than payloads on purpose: this is the shape of the conversation, which F08's real
#: interpreter and F10's real wording must both leave alone, and F11 inherits it as-is.
GOLDEN_PARIS_ACTS: Final = (
    GREET,
    ASK_BLOCKING,
    PRESENT_SLATE,
    ASK_OPTIONAL,
    CONFIRM_SELECTION,
    OFFER_UNMENTIONED,
    DELIVER_SUMMARY,
    CLOSE,
)

#: A refinement in the middle of the demo: the same turns, with "cheaper" between the first slate
#: and the choice. The choose-or-refine loop is unbounded, so this is one round of many possible.
REFINING_SCRIPT: Final = (
    "find me a hotel in Paris",
    "23-28 October 2026",
    "cheaper",
    "2",
    "no thanks",
)

#: How a test replaces one of the container's adapters, given the container it was wired into.
InterpreterFactory = Callable[[Container], TurnInterpreter]


def environ_for(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """The shipped configuration and fixture tree, with only the writable paths redirected."""
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(SHIPPED_CONFIG),
        "TOURGANIZE_FIXTURE_DIR": str(SHIPPED_FIXTURES),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        "TOURGANIZE_TELEMETRY_SINK": "null",
    }
    environ.update(overrides)
    return environ


def run_cli(argv: list[str], environ: Mapping[str, str]) -> tuple[int, str, str]:
    """Drive ``tourganize`` in-process with an explicit environment, as ``test_cli`` does."""
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@dataclass(frozen=True, slots=True)
class Replay:
    """One scripted session, and everything a test might want to look at afterwards."""

    container: Container
    director: DialogueDirector
    surface: ScriptedSurface
    outcome: SessionOutcome

    @property
    def act_names(self) -> tuple[str, ...]:
        return tuple(act.act for act in self.surface.captured)

    def only(self, act: str) -> AssistantAct:
        """The single Act of that name, asserting there is exactly one."""
        found = [captured for captured in self.surface.captured if captured.act == act]
        assert len(found) == 1, f"expected one {act}, got {len(found)}"
        return found[0]

    def rendered(self, act: str) -> tuple[RenderedAct, ...]:
        return tuple(drawn for drawn in self.surface.rendered if drawn.act == act)


def replay(
    tmp_path: Path,
    script: Sequence[str],
    *,
    locale: str | None = None,
    interpreter: InterpreterFactory | None = None,
    **overrides: str,
) -> Replay:
    """Wire the application from the shipped config and run ``script`` through it headlessly."""
    container = build_container(Settings.from_env(environ_for(tmp_path, **overrides)))
    director = DialogueDirector(
        container.component_catalog,
        container.priority_policy,
        (interpreter(container) if interpreter is not None else container.turn_interpreter),
        container.option_slate_planner,
        container.clock,
        container.telemetry_sink,
        build_dialogue_settings(container.settings),
        session_id="chat-end-to-end",
    )
    surface = ScriptedSurface(tuple(script), container.clock, container.act_renderer, locale=locale)
    outcome = run(director, surface, locale=locale)
    return Replay(container, director, surface, outcome)


def the_place_and_dates_kind(catalog: ComponentCatalog) -> str:
    """The shipped Kind whose schema asks for a place *and* a date range — read, not named."""
    for kind in catalog.enabled_kinds():
        rules = {rule.name for rule in catalog.schema_for(kind.kind_key).blocking_rules}
        if {"where", "when"} <= rules and kind.requires_outcome_of:
            return kind.kind_key
    raise AssertionError("the shipped schemas declare no kind with a where-and-when rule")


# -- the scripted transcript, headless ---------------------------------------------------------


def test_the_shipped_transcripts_exist_and_are_one_turn_per_line() -> None:
    """The demo is a file in the repository, not a list in a test: F11 replays the file."""
    for transcript in (PARIS, PARIS_HEBREW):
        assert transcript.is_file(), transcript
        assert read_script(transcript), f"{transcript.name} has no turns"


def test_chat_with_a_script_runs_headlessly_and_exits_zero(tmp_path: Path) -> None:
    """CI has no TTY, so this is the path the Phase 1 demo is actually *gated* on."""
    code, _out, err = run_cli(["chat", "--script", str(PARIS)], environ_for(tmp_path))

    assert code == EXIT_OK, err
    assert "not implemented" not in err


def test_the_paris_transcript_produces_the_first_golden_conversation(tmp_path: Path) -> None:
    """The stored expectation F11 inherits. Act names, so wording may still improve."""
    session = replay(tmp_path, read_script(PARIS))

    assert session.act_names == GOLDEN_PARIS_ACTS
    assert session.outcome.ok
    assert session.outcome.closed_by_director
    assert session.director.session.state is DialogueState.CLOSED


def test_the_transcript_is_byte_identical_between_two_processes_worth_of_runs(
    tmp_path: Path,
) -> None:
    """Determinism is what makes a Golden Conversation an expectation rather than a sample."""
    first = replay(tmp_path / "a", read_script(PARIS))
    second = replay(tmp_path / "b", read_script(PARIS))

    assert first.surface.transcript == second.surface.transcript


# -- what the traveller actually sees ------------------------------------------------------------


def test_the_slate_is_numbered_and_carries_prices_and_review_scores(tmp_path: Path) -> None:
    """The DoD's own words: "a numbered slate of 3 options with prices and review scores"."""
    session = replay(tmp_path, read_script(PARIS))
    lodging = the_place_and_dates_kind(session.container.component_catalog)
    (slate,) = session.rendered(PRESENT_SLATE)

    assert slate.kind_key == lodging
    assert len(slate.option_rows) == session.container.settings.slate_size
    assert [row.number for row in slate.option_rows] == [1, 2, 3]
    assert all(row.price for row in slate.option_rows), "every fixture option has a price"
    # The Display Profile declares one column with a unit; that is the review score. Read the
    # profile rather than naming the fact, for the same reason the Kind itself is read.
    profile = session.container.act_renderer.profiles("en").for_kind(lodging)
    units = [column.unit for column in profile.columns if column.unit]
    assert units, "the shipped Display Profile declares no column with a unit"
    for row in slate.option_rows:
        labels = [label for label, _value in row.cells]
        assert len(labels) == len(set(labels)), "a column may not appear twice"
        values = [value for _label, value in row.cells]
        assert any(value.endswith(unit) for value in values for unit in units)

    transcript = session.surface.transcript
    assert transcript
    for row in slate.option_rows:
        assert row.price is not None and row.price in transcript
        assert str(row.number) in transcript


def test_typing_two_confirms_the_second_option_then_the_offer_and_the_close_follow(
    tmp_path: Path,
) -> None:
    """The demo's tail, in order: choose, be offered the rest, decline, be summarised, close."""
    session = replay(tmp_path, read_script(PARIS))
    lodging = the_place_and_dates_kind(session.container.component_catalog)
    offered = session.only(PRESENT_SLATE).payload["option_ids"]
    assert isinstance(offered, tuple)

    confirmed = session.only(CONFIRM_SELECTION)
    assert confirmed.payload["option_id"] == offered[1], "'2' means the second row shown"
    assert confirmed.kind_key == lodging

    order = session.act_names
    assert order.index(CONFIRM_SELECTION) < order.index(OFFER_UNMENTIONED)
    assert order.index(OFFER_UNMENTIONED) < order.index(DELIVER_SUMMARY)
    assert order[-1] == CLOSE

    summary = session.only(DELIVER_SUMMARY)
    selections = summary.payload["selections"]
    assert isinstance(selections, tuple) and len(selections) == 1
    (drawn,) = session.rendered(DELIVER_SUMMARY)
    assert drawn.lines, "a summary with a Selection in it has something to say"


def test_a_refinement_produces_a_second_numbered_slate_for_the_same_component(
    tmp_path: Path,
) -> None:
    """The choose-or-refine loop, seen from the surface: two slates, one Plan Component."""
    session = replay(tmp_path, REFINING_SCRIPT)
    lodging = the_place_and_dates_kind(session.container.component_catalog)
    slates = session.rendered(PRESENT_SLATE)

    assert len(slates) == 2
    assert all(drawn.kind_key == lodging for drawn in slates)
    assert all([row.number for row in drawn.option_rows] == [1, 2, 3] for drawn in slates)

    component = session.director.session.plan.component(lodging)
    assert [slate.round_index for slate in component.slates] == [0, 1]
    assert session.act_names[-1] == CLOSE


# -- Hebrew ---------------------------------------------------------------------------------------


def test_hebrew_completes_a_whole_session_with_every_act_drawn_right_to_left(
    tmp_path: Path,
) -> None:
    """F07 promises only that Hebrew does not crash and that `direction` is plumbed. Both."""
    session = replay(tmp_path, read_script(PARIS_HEBREW), locale="he")

    assert session.outcome.ok
    assert session.act_names[0] == GREET
    assert session.act_names[-1] == CLOSE
    assert len(session.surface.rendered) == len(session.surface.captured)
    assert all(drawn.direction == "rtl" for drawn in session.surface.rendered)
    assert all(drawn.heading for drawn in session.surface.rendered)
    assert "⟪missing:" not in session.surface.transcript, "he.yaml declares every key it needs"


def test_chat_locale_he_with_the_hebrew_transcript_exits_zero(tmp_path: Path) -> None:
    code, _out, err = run_cli(
        ["chat", "--locale", "he", "--script", str(PARIS_HEBREW)], environ_for(tmp_path)
    )

    assert code == EXIT_OK, err


def test_the_same_transcript_in_two_locales_is_the_same_conversation(tmp_path: Path) -> None:
    """Wording is a locale's business; control flow is not. The Acts must not diverge."""
    english = replay(tmp_path / "en", read_script(PARIS))
    hebrew = replay(tmp_path / "he", read_script(PARIS_HEBREW), locale="he")

    assert hebrew.act_names == english.act_names == GOLDEN_PARIS_ACTS


# -- a Message Catalogue with a hole in it --------------------------------------------------------


def _catalogue_without(tmp_path: Path, locale: str, key: str) -> Path:
    """A copy of the shipped message directory with one key deleted from one locale."""
    message_dir = tmp_path / "messages"
    message_dir.mkdir(parents=True, exist_ok=True)
    for source in SHIPPED_MESSAGES.iterdir():
        (message_dir / source.name).write_text(source.read_text("utf-8"), encoding="utf-8")
    target = message_dir / f"{locale}.yaml"
    kept = [
        line for line in target.read_text("utf-8").splitlines() if not line.startswith(f"  {key}:")
    ]
    assert len(kept) < len(target.read_text("utf-8").splitlines()), f"{key} was not there"
    target.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return message_dir


def test_a_deleted_message_key_is_a_marker_and_a_warning_and_the_session_still_completes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one failure mode a demonstration would not notice is a blank line. So: never blank."""
    message_dir = _catalogue_without(tmp_path, "en", GREET)

    with caplog.at_level(logging.WARNING, logger="tourganize.language.act_renderer"):
        session = replay(tmp_path, read_script(PARIS), TOURGANIZE_MESSAGE_DIR=str(message_dir))

    (greeting,) = session.rendered(GREET)
    assert greeting.heading == missing_marker(GREET)
    assert missing_marker(GREET) in session.surface.transcript
    assert any(
        record.levelno == logging.WARNING and GREET in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]

    assert session.act_names == GOLDEN_PARIS_ACTS, "one lost sentence, whole conversation"
    assert session.outcome.ok


def test_chat_with_a_holed_catalogue_still_exits_zero(tmp_path: Path) -> None:
    message_dir = _catalogue_without(tmp_path, "en", GREET)

    code, _out, err = run_cli(
        ["chat", "--script", str(PARIS)],
        environ_for(tmp_path, TOURGANIZE_MESSAGE_DIR=str(message_dir)),
    )

    assert code == EXIT_OK, err


# -- soft filtering, made visible ------------------------------------------------------------------


@final
class _AlsoOffering:
    """The real interpreter, with one Requirement Update added to whatever it read.

    Copied from ``tests/integration/test_dialogue_real_sourcing.py`` rather than imported: the
    keyword interpreter is F05's deliberate stand-in and reads no money at all, so "under €1 a
    night" is not something a traveller can say to it until F08 arrives. This offers that value
    the way an extracting interpreter would have, on any turn whose focused schema declares the
    field, and leaves everything else to the adapter the Composition Root wired.
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

    Copied, with the same reasoning, from ``test_dialogue_real_sourcing.py``: read off the schema
    rather than written down, so that no travel topic — and no field name only one topic happens
    to use — is hardcoded in a test.
    """
    money_fields = [
        spec.name for spec in schema.optional_fields() if spec.field_kind is FieldKind.MONEY
    ]
    assert money_fields, "the shipped schema declares no money filter to test with"
    return RequirementUpdate(field_name=money_fields[0], value="100 EUR")


def _under_an_impossible_ceiling(container: Container) -> TurnInterpreter:
    catalog = container.component_catalog
    schema = catalog.schema_for(the_place_and_dates_kind(catalog))
    return _AlsoOffering(container.turn_interpreter, _impossible_ceiling(schema))


def test_filter_notes_are_visible_in_the_option_table(tmp_path: Path) -> None:
    """Soft filtering that nobody can see is silent filtering, which F06 forbids.

    Every option exceeds the ceiling, so every row must carry a note — and the note must be the
    *phrased* field label from the Message Catalogue, not the raw field name, because a Filter
    Note is a field name in the payload and a word on the screen.
    """
    session = replay(tmp_path, read_script(PARIS), interpreter=_under_an_impossible_ceiling)
    lodging = the_place_and_dates_kind(session.container.component_catalog)
    schema = session.container.component_catalog.schema_for(lodging)
    field_name = _impossible_ceiling(schema).field_name
    phrased = session.container.act_renderer.catalogue("en").get(
        f"field.{lodging}.{field_name}", f"field.{field_name}"
    )
    (slate,) = session.rendered(PRESENT_SLATE)

    assert slate.option_rows, "a demoted option is still shown"
    for row in slate.option_rows:
        assert row.filter_notes == (phrased,)
        assert phrased in session.surface.transcript

    shown = session.only(PRESENT_SLATE).payload["options"]
    assert isinstance(shown, tuple)
    assert all(option["filter_notes"] == (field_name,) for option in shown)


# -- everything that worked before still works ------------------------------------------------------


def test_doctor_reports_the_surface_the_locale_and_the_message_directory(tmp_path: Path) -> None:
    code, out, err = run_cli(["doctor"], environ_for(tmp_path, TOURGANIZE_SURFACE="scripted"))

    assert code == EXIT_OK, err
    assert "surface: scripted" in out
    assert f"message_dir: {SHIPPED_MESSAGES}" in out
    assert "default_locale: en" in out
    assert "supported_locales: en,he" in out
    assert "PresentationSurface" in out, "doctor names the adapter behind every wired port"


def test_doctor_passes_with_the_default_terminal_surface(tmp_path: Path) -> None:
    """The demo's own installation: the `terminal` extra is present and `doctor` says so."""
    code, out, err = run_cli(["doctor"], environ_for(tmp_path))

    assert code == EXIT_OK, err
    assert "surface: terminal" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["catalog", "show"],
        ["catalog", "validate"],
        ["catalog", "agenda"],
    ],
)
def test_the_catalog_commands_are_unaffected(tmp_path: Path, argv: list[str]) -> None:
    code, _out, err = run_cli(argv, environ_for(tmp_path))

    assert code == EXIT_OK, err


def test_options_search_is_unaffected(tmp_path: Path) -> None:
    environ = environ_for(tmp_path)
    kind = the_place_and_dates_kind(build_container(Settings.from_env(environ)).component_catalog)

    code, out, err = run_cli(
        [
            "options",
            "search",
            "--kind",
            kind,
            "--set",
            '{"place": "Paris", "date_range": "2026-10-23/2026-10-28"}',
        ],
        environ,
    )

    assert code == EXIT_OK, err
    assert out


# -- the seam itself ---------------------------------------------------------------------------------


def test_the_composition_root_builds_the_scripted_surface_when_a_script_is_given(
    tmp_path: Path,
) -> None:
    """``--script`` is more plainly a request for the headless surface than a setting is."""
    container = build_container(
        Settings.from_env(environ_for(tmp_path, TOURGANIZE_SURFACE="terminal"))
    )

    surface = build_surface(container, script=read_script(PARIS))

    assert surface.surface_id == "scripted"


#: The turns `docs/architecture/overview.md` §6 ("Phase 1 in one line") says the client types.
#: Quoted from the document rather than paraphrased, because the DoD asks for that paragraph to
#: be *verified by running it* and a paraphrase would verify something else.
PHASE_ONE_TURNS: Final = (
    "find me a hotel in Paris between the 23rd and 28th of October 2026",
    "23-28 October 2026",
    "2",
    "no thanks",
)


def test_the_phase_one_paragraph_in_the_overview_is_true_when_run(tmp_path: Path) -> None:
    """§6 is a promise to a client, so it is a test rather than a paragraph nobody replays.

    The first turn names a place and a Component Kind and *not* a readable date — resolving
    "between the 23rd and 28th of October" is F08's, not a transcript's — so the one blocking
    detail is asked, answered, and the slate follows. Same shape as the script, longer opening.
    """
    session = replay(tmp_path, PHASE_ONE_TURNS)

    assert session.act_names == GOLDEN_PARIS_ACTS
    assert session.outcome.ok
    (slate,) = session.rendered(PRESENT_SLATE)
    assert len(slate.option_rows) == 3
    assert all(row.price for row in slate.option_rows)
