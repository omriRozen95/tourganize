"""The keyword Turn Interpreter, and its phrase tables.

What is worth pinning here is that everything locale-specific is *configuration*: the intents,
the per-kind keywords, the place markers, the month names and the field names each value shape
is filed under. The interpreter itself is scaffolding F08 replaces, so these tests deliberately
assert what it reads out of a table rather than how clever it is about English.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import SAMPLE_KEYWORDS, write_keywords

from tourganize.adapters.clock.fake import DEFAULT_MOMENT
from tourganize.adapters.interpretation.keyword import (
    HEBREW_LOCALE,
    SHAPES,
    KeywordTurnInterpreter,
    PhraseTable,
    load_phrase_tables,
    read_phrase_table,
)
from tourganize.dialogue import (
    DialogueContext,
    DialogueState,
    PendingQuestion,
    TurnIntent,
    UserTurn,
)
from tourganize.platform.errors import ConfigurationError

KNOWN = ("alpha", "beta", "gamma")


@pytest.fixture
def interpreter(tmp_path: Path) -> KeywordTurnInterpreter:
    return KeywordTurnInterpreter(write_keywords(tmp_path))


def a_turn(text: str, *, index: int = 0, locale_hint: str | None = None) -> UserTurn:
    return UserTurn(index=index, text=text, received_at=DEFAULT_MOMENT, locale_hint=locale_hint)


def a_context(
    state: DialogueState = DialogueState.GREETING,
    *,
    focus_kind: str | None = None,
    slate_option_refs: tuple[str, ...] = (),
    focus_field_names: tuple[str, ...] = (),
    pending_question: PendingQuestion | None = None,
) -> DialogueContext:
    return DialogueContext(
        state=state,
        focus_kind=focus_kind,
        pending_question=pending_question,
        slate_option_refs=slate_option_refs,
        known_kind_keys=KNOWN,
        focus_field_names=focus_field_names,
    )


# -- reading the tables ----------------------------------------------------------------------


def test_a_table_is_read_per_locale_and_keyed_by_it(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"en": SAMPLE_KEYWORDS})
    tables = load_phrase_tables(directory)

    assert set(tables) == {"en"}
    assert tables["en"].field_for("place") == "place"
    assert tables["en"].months["october"] == 10


def test_a_directory_with_no_table_is_a_misconfigured_installation(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()

    with pytest.raises(ConfigurationError, match=r"keywords\.<locale>\.yaml"):
        load_phrase_tables(tmp_path / "empty")


def test_a_missing_directory_names_the_key_that_points_at_it(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="TOURGANIZE_KEYWORD_CONFIG_DIR"):
        load_phrase_tables(tmp_path / "nowhere")


def test_a_file_whose_name_disagrees_with_its_locale_is_refused(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"fr": SAMPLE_KEYWORDS})

    with pytest.raises(ConfigurationError, match="its file name says 'fr'"):
        load_phrase_tables(directory)


def test_a_file_that_is_not_a_phrase_table_is_refused(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"en": "intnets:\n  end_session: [bye]\n"})

    with pytest.raises(ConfigurationError, match="unknown top-level key"):
        load_phrase_tables(directory)


def test_an_intent_the_dialogue_does_not_have_is_refused(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"en": "locale: en\nintents:\n  book_it: [book]\n"})

    with pytest.raises(ConfigurationError, match="not a Turn Intent"):
        load_phrase_tables(directory)


def test_a_value_shape_the_interpreter_cannot_read_is_refused(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"en": "locale: en\nfields:\n  budget: budget_ceiling\n"})

    with pytest.raises(ConfigurationError, match="cannot recognise"):
        load_phrase_tables(directory)


def test_a_month_number_outside_the_year_is_refused(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path, {"en": "locale: en\nmonths:\n  smarch: 13\n"})

    with pytest.raises(ConfigurationError, match="month number 1-12"):
        load_phrase_tables(directory)


def test_files_that_are_not_phrase_tables_are_ignored(tmp_path: Path) -> None:
    directory = write_keywords(tmp_path)
    (directory / "README.md").write_text("notes", encoding="utf-8")

    assert set(load_phrase_tables(directory)) == {"en"}


def test_a_table_cannot_be_edited_underneath_an_interpreter(tmp_path: Path) -> None:
    table = read_phrase_table(write_keywords(tmp_path) / "keywords.en.yaml")

    assert table.locale == "en"
    with pytest.raises(TypeError):
        table.months["december"] = 12  # type: ignore[index]


def test_an_empty_table_is_a_valid_one_that_understands_nothing() -> None:
    """A locale may declare only what it has; the shapes it maps nothing to are simply unread."""
    table = PhraseTable(locale="en")

    assert table.field_for("place") is None
    assert set(SHAPES) == {"place", "date_range"}


# -- locale ----------------------------------------------------------------------------------


def test_hebrew_script_is_decisive(interpreter: KeywordTurnInterpreter) -> None:
    reading = interpreter.interpret(a_turn("אלפא"), a_context())

    assert reading.detected_locale == HEBREW_LOCALE


def test_a_surface_hint_stands_when_the_script_says_nothing(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("alpha", locale_hint="he"), a_context())

    assert reading.detected_locale == "he"


def test_a_latin_turn_with_no_hint_leaves_the_session_locale_alone(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """An English-looking turn is not evidence that a Hebrew conversation switched language."""
    reading = interpreter.interpret(a_turn("alpha"), a_context())

    assert reading.detected_locale is None


def test_a_locale_with_no_table_falls_back_to_the_default_one(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("alpha", locale_hint="fr"), a_context())

    assert reading.intent is TurnIntent.ANSWER_QUESTION


# -- intent ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("goodbye", TurnIntent.END_SESSION),
        ("that is all", TurnIntent.END_SESSION),
        ("where are we", TurnIntent.STATE_REQUEST),
        ("hello", TurnIntent.SMALL_TALK),
        ("alpha", TurnIntent.ANSWER_QUESTION),
        ("in Paris", TurnIntent.ANSWER_QUESTION),
        ("???", TurnIntent.UNKNOWN),
    ],
)
def test_intent_comes_from_the_phrase_table(
    interpreter: KeywordTurnInterpreter, text: str, expected: TurnIntent
) -> None:
    assert interpreter.interpret(a_turn(text), a_context()).intent is expected


def test_leaving_is_honoured_from_any_state(interpreter: KeywordTurnInterpreter) -> None:
    for state in (DialogueState.AWAITING_CHOICE, DialogueState.OFFERING_UNMENTIONED):
        reading = interpreter.interpret(a_turn("goodbye"), a_context(state))
        assert reading.intent is TurnIntent.END_SESSION


def test_yes_and_no_only_mean_an_offer_while_one_is_on_the_table(
    interpreter: KeywordTurnInterpreter,
) -> None:
    offering = a_context(DialogueState.OFFERING_UNMENTIONED)

    assert interpreter.interpret(a_turn("yes please"), offering).intent is TurnIntent.ACCEPT_OFFER
    assert interpreter.interpret(a_turn("no thanks"), offering).intent is TurnIntent.DECLINE_OFFER
    assert interpreter.interpret(a_turn("yes please"), a_context()).intent is TurnIntent.UNKNOWN


def test_a_slate_on_the_table_makes_a_number_a_choice(
    interpreter: KeywordTurnInterpreter,
) -> None:
    context = a_context(DialogueState.AWAITING_CHOICE, slate_option_refs=("a-1", "a-2", "a-3"))
    reading = interpreter.interpret(a_turn("2"), context)

    assert reading.intent is TurnIntent.CHOOSE_OPTION
    assert reading.chosen_option_ref == "2"


def test_an_out_of_range_ordinal_is_still_an_attempt_to_choose(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """The Director resolves references, because it is the thing that holds the slate."""
    context = a_context(DialogueState.AWAITING_CHOICE, slate_option_refs=("a-1", "a-2"))
    reading = interpreter.interpret(a_turn("9"), context)

    assert reading.intent is TurnIntent.CHOOSE_OPTION
    assert reading.chosen_option_ref == "9"


def test_a_quoted_id_is_a_choice_reference(interpreter: KeywordTurnInterpreter) -> None:
    context = a_context(DialogueState.AWAITING_CHOICE, slate_option_refs=("a-1", "a-2"))

    assert interpreter.interpret(a_turn("'a-2'"), context).chosen_option_ref == "a-2"


def test_a_bare_option_id_is_a_choice_reference(interpreter: KeywordTurnInterpreter) -> None:
    context = a_context(DialogueState.AWAITING_CHOICE, slate_option_refs=("a-1", "a-2"))

    assert interpreter.interpret(a_turn("I will take a-2"), context).chosen_option_ref == "a-2"


def test_a_refinement_outranks_a_choice_when_the_turn_carries_values(
    interpreter: KeywordTurnInterpreter,
) -> None:
    context = a_context(
        DialogueState.AWAITING_CHOICE,
        focus_kind="alpha",
        slate_option_refs=("a-1", "a-2"),
        focus_field_names=("place", "date_range"),
    )
    reading = interpreter.interpret(a_turn("make it 2026-11-01/2026-11-05"), context)

    assert reading.intent is TurnIntent.REFINE
    assert reading.chosen_option_ref is None


def test_a_refine_phrase_is_a_refinement_only_while_a_slate_is_up(
    interpreter: KeywordTurnInterpreter,
) -> None:
    awaiting = a_context(DialogueState.AWAITING_CHOICE, slate_option_refs=("a-1",))

    assert interpreter.interpret(a_turn("cheaper"), awaiting).intent is TurnIntent.REFINE
    assert interpreter.interpret(a_turn("cheaper"), a_context()).intent is TurnIntent.UNKNOWN


# -- mentioned kinds and values --------------------------------------------------------------


def test_mentioned_kinds_come_from_the_per_kind_keyword_lists(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("a beta and an alpha"), a_context())

    assert reading.mentioned_kinds == ("alpha", "beta")


def test_a_kind_the_catalog_does_not_declare_is_not_mentioned(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """A stale phrase table is not a licence to plan something nobody declared."""
    context = DialogueContext(state=DialogueState.GREETING, known_kind_keys=("alpha",))
    reading = interpreter.interpret(a_turn("a beta and an alpha"), context)

    assert reading.mentioned_kinds == ("alpha",)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-23/2026-10-28", "2026-10-23/2026-10-28"),
        ("2026-10-23 to 2026-10-28", "2026-10-23/2026-10-28"),
        ("2026-10-23 - 2026-10-28", "2026-10-23/2026-10-28"),
        ("23-28 October 2026", "2026-10-23/2026-10-28"),
        ("23–28 october 2026", "2026-10-23/2026-10-28"),
    ],
)
def test_a_date_range_is_offered_in_the_canonical_spelling(
    interpreter: KeywordTurnInterpreter, text: str, expected: str
) -> None:
    reading = interpreter.interpret(a_turn(text), a_context())

    assert [(u.field_name, u.value) for u in reading.requirement_updates] == [
        ("date_range", expected)
    ]


def test_a_day_the_month_does_not_have_is_offered_as_nothing(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """A date nobody meant is worse than a question asked again."""
    reading = interpreter.interpret(a_turn("31-32 november 2026"), a_context())

    assert reading.requirement_updates == ()


def test_a_relative_expression_produces_no_value_at_all(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """ "Next month" needs a Clock and a calendar; resolving it is F08's, not a regex's."""
    reading = interpreter.interpret(a_turn("some time next month"), a_context())

    assert reading.requirement_updates == ()


def test_a_place_follows_a_marker_and_keeps_its_case(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("an alpha in Tel Aviv"), a_context())

    assert [(u.field_name, u.value) for u in reading.requirement_updates] == [("place", "Tel Aviv")]


def test_a_place_stops_at_the_first_word_that_is_not_a_name(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("an alpha in Paris between the 23rd"), a_context())

    assert [u.value for u in reading.requirement_updates] == ["Paris"]


def test_a_month_name_is_never_read_as_a_place(interpreter: KeywordTurnInterpreter) -> None:
    reading = interpreter.interpret(a_turn("an alpha in October"), a_context())

    assert reading.requirement_updates == ()


def test_an_update_quotes_what_the_traveller_actually_said(
    interpreter: KeywordTurnInterpreter,
) -> None:
    reading = interpreter.interpret(a_turn("an alpha in Paris"), a_context())

    assert reading.requirement_updates[0].raw_text == "Paris"


def test_a_value_is_offered_only_for_a_field_the_focused_schema_declares(
    interpreter: KeywordTurnInterpreter,
) -> None:
    context = a_context(focus_kind="beta", focus_field_names=("place",))
    reading = interpreter.interpret(a_turn("in Paris 2026-10-23/2026-10-28"), context)

    assert [u.field_name for u in reading.requirement_updates] == ["place"]


def test_with_nothing_in_focus_the_tables_own_field_names_are_offered(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """On the first turn there is no schema to check against, and the Director reports drift."""
    reading = interpreter.interpret(a_turn("in Paris 2026-10-23/2026-10-28"), a_context())

    assert {u.field_name for u in reading.requirement_updates} == {"place", "date_range"}


def test_the_same_turn_always_reads_the_same_way(
    interpreter: KeywordTurnInterpreter,
) -> None:
    """Deterministic is the whole point: it is what makes a Golden Conversation possible."""
    first = interpreter.interpret(a_turn("an alpha in Paris 2026-10-23/2026-10-28"), a_context())
    second = interpreter.interpret(a_turn("an alpha in Paris 2026-10-23/2026-10-28"), a_context())

    assert first == second


def test_the_tables_are_read_once_and_cached(interpreter: KeywordTurnInterpreter) -> None:
    assert interpreter.tables() is interpreter.tables()
