"""The Scripted Surface: the turns it yields, the Acts it keeps, the transcript it writes.

What is asserted here is this adapter's own behaviour. The promises it shares with every other
surface live in ``tests/contracts/test_presentation_surface_contract.py`` and are not repeated:
a rule stated twice is a rule that will eventually be stated two different ways.

Three of these tests are about a *file* rather than about the class, and belong here for the
same reason ``read_script`` does — "what counts as a turn in a transcript" is one answer, and
the surface that replays them owns it. The last of them reads the transcripts that ship in
``fixtures/conversations/``, so a comment written into one of those files can never quietly
become a turn somebody says out loud.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Final

import pytest
from conftest import write_catalog, write_messages, write_schemas

from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.presentation.scripted import (
    SCRIPTED_SURFACE_ID,
    ScriptedSurface,
    read_script,
)
from tourganize.dialogue import (
    ASK_BLOCKING,
    CLOSE,
    GREET,
    PRESENT_SLATE,
    AssistantAct,
    UserTurn,
)
from tourganize.language.act_renderer import ActRenderer, RenderedAct
from tourganize.platform.errors import ConfigurationError
from tourganize.ports.presentation import NOTICE_READY, NOTICE_WORKING, SurfaceNotice

#: The Component Kind these Acts are about — the sample catalog's, so that no travel topic has
#: to be named to test a surface.
KIND: Final = "alpha"

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHIPPED_TRANSCRIPTS: Final = REPO_ROOT / "fixtures" / "conversations"


@pytest.fixture
def renderer(tmp_path: Path) -> ActRenderer:
    """An Act Renderer over this test's own Message Catalogue and sample catalog."""
    config = tmp_path / "config"
    catalog = YamlComponentCatalog(write_catalog(config), write_schemas(config))
    return ActRenderer(
        write_messages(config), catalog, supported_locales=("en", "he"), default_locale="en"
    )


def a_slate_act(*, filter_notes: tuple[str, ...] = ()) -> AssistantAct:
    """A ``present_slate`` with two options, the second failing an optional filter."""
    return AssistantAct(
        PRESENT_SLATE,
        {
            "round_index": 0,
            "option_ids": ("alpha-1", "alpha-2"),
            "options": (
                {
                    "option_id": "alpha-1",
                    "price": {"amount_minor": 39000, "currency": "EUR"},
                    "facts": {"name": "Cheaper", "review_score": 6.9},
                    "source_id": "fixture",
                    "filter_notes": (),
                },
                {
                    "option_id": "alpha-2",
                    "price": {"amount_minor": 74000, "currency": "EUR"},
                    "facts": {"name": "Dearer", "review_score": 8.4},
                    "source_id": "fixture",
                    "filter_notes": filter_notes,
                },
            ),
            "requirements_digest": "0" * 16,
        },
        kind_key=KIND,
    )


# -- the script ------------------------------------------------------------------------------


def test_the_script_is_replayed_in_order_and_then_the_surface_closes() -> None:
    surface = ScriptedSurface(("one", "two"), FrozenClock(DEFAULT_MOMENT))

    spoken = [surface.next_turn(), surface.next_turn(), surface.next_turn()]

    assert [turn.text for turn in spoken if turn is not None] == ["one", "two"]
    assert spoken[2] is None
    assert surface.next_turn() is None, "a finished script started answering again"


def test_a_turn_is_numbered_from_zero_so_it_matches_the_session_the_runner_drives() -> None:
    """The counter is the surface's own, and stays in step because every turn is handled."""
    surface = ScriptedSurface(("one", "two", "three"), FrozenClock(DEFAULT_MOMENT))

    numbers = [turn.index for _ in range(3) if (turn := surface.next_turn()) is not None]

    assert numbers == [0, 1, 2]


def test_a_turn_is_stamped_from_the_clock_and_not_from_the_wall() -> None:
    """A recorded conversation replays with the timestamps it was captured with."""
    clock = FrozenClock(DEFAULT_MOMENT, step=timedelta(seconds=30))
    surface = ScriptedSurface(("one", "two"), clock)

    first, second = surface.next_turn(), surface.next_turn()

    assert first is not None and second is not None
    assert first.received_at == DEFAULT_MOMENT
    assert second.received_at == DEFAULT_MOMENT + timedelta(seconds=30)


def test_the_locale_the_surface_was_started_in_becomes_the_turns_locale_hint() -> None:
    """A hint and no more: the interpreter may disagree, and F10's detector settles it."""
    surface = ScriptedSurface(("one",), FrozenClock(DEFAULT_MOMENT), locale="he")

    turn = surface.next_turn()

    assert isinstance(turn, UserTurn)
    assert turn.locale_hint == "he"


def test_a_surface_started_without_a_locale_hints_at_nothing() -> None:
    surface = ScriptedSurface(("one",), FrozenClock(DEFAULT_MOMENT))

    turn = surface.next_turn()

    assert isinstance(turn, UserTurn)
    assert turn.locale_hint is None


def test_closing_stops_the_script_even_with_turns_left_in_it() -> None:
    surface = ScriptedSurface(("one", "two", "three"), FrozenClock(DEFAULT_MOMENT))
    surface.next_turn()

    surface.close()

    assert surface.next_turn() is None


def test_an_empty_script_closes_immediately() -> None:
    """A greeting and nothing else is a whole session, and the shortest one there is."""
    assert ScriptedSurface((), FrozenClock(DEFAULT_MOMENT)).next_turn() is None


# -- reading a transcript file ---------------------------------------------------------------


def test_a_transcript_file_is_one_turn_per_line(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    path.write_text("first\nsecond\nthird\n", encoding="utf-8")

    assert read_script(path) == ("first", "second", "third")


def test_a_blank_line_is_not_a_turn_and_neither_is_a_line_of_only_whitespace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "script.txt"
    path.write_text("first\n\n   \n\t\nsecond\n", encoding="utf-8")

    assert read_script(path) == ("first", "second")


def test_a_comment_is_not_a_turn_however_it_is_indented(tmp_path: Path) -> None:
    path = tmp_path / "script.txt"
    path.write_text("# what this proves\nfirst\n    # and a note\nsecond\n", encoding="utf-8")

    assert read_script(path) == ("first", "second")


def test_a_turn_is_stripped_so_invisible_spacing_cannot_change_a_session(
    tmp_path: Path,
) -> None:
    path = tmp_path / "script.txt"
    path.write_text("  first  \nsecond\t\n", encoding="utf-8")

    assert read_script(path) == ("first", "second")


def test_a_hash_inside_a_turn_is_not_a_comment(tmp_path: Path) -> None:
    """Only the *first* non-space character starts a comment; a turn may still contain one."""
    path = tmp_path / "script.txt"
    path.write_text("room #12 please\n", encoding="utf-8")

    assert read_script(path) == ("room #12 please",)


def test_a_transcript_file_that_is_not_there_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="transcript file"):
        read_script(tmp_path / "nothing.txt")


def test_the_shipped_transcripts_read_as_scripts_with_turns_in_them() -> None:
    """The files ``fixtures/conversations/`` ships are read by the rules above, not by eye."""
    shipped = sorted(SHIPPED_TRANSCRIPTS.glob("*.txt"))

    assert shipped, "fixtures/conversations/ ships no transcript at all"
    for path in shipped:
        turns = read_script(path)
        assert turns, f"{path.name} holds no turns"
        assert not any(turn.startswith("#") for turn in turns), f"{path.name} yielded a comment"


# -- what the surface records ------------------------------------------------------------------


def test_every_act_is_captured_in_the_order_it_was_shown() -> None:
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT))
    shown = (AssistantAct(GREET), a_slate_act(), AssistantAct(CLOSE))

    for act in shown:
        surface.show(act)

    assert surface.captured == shown


def test_nothing_is_rendered_when_no_act_renderer_was_supplied() -> None:
    """F11 replays structure; a harness that needed a Message Catalogue would be coupled."""
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT))

    surface.show(AssistantAct(GREET))

    assert surface.captured == (AssistantAct(GREET),)
    assert surface.rendered == ()
    assert surface.transcript == f"< {GREET}\n"


def test_every_act_is_rendered_when_a_renderer_was_supplied(renderer: ActRenderer) -> None:
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT), renderer, locale="en")

    surface.show(AssistantAct(GREET))
    surface.show(a_slate_act())

    assert [drawn.act for drawn in surface.rendered] == [GREET, PRESENT_SLATE]
    assert all(isinstance(drawn, RenderedAct) for drawn in surface.rendered)


def test_notices_are_recorded_and_kept_out_of_the_transcript() -> None:
    """A notice is what the program was doing, never part of what was said."""
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT))

    surface.notify(SurfaceNotice(NOTICE_WORKING, session_id="s"))
    surface.notify(SurfaceNotice(NOTICE_READY, session_id="s"))

    assert [notice.code for notice in surface.notices] == [NOTICE_WORKING, NOTICE_READY]
    assert surface.transcript == ""


# -- the transcript ----------------------------------------------------------------------------


def test_the_transcript_records_what_was_shown_and_what_was_typed(
    renderer: ActRenderer,
) -> None:
    surface = ScriptedSurface(("hello",), FrozenClock(DEFAULT_MOMENT), renderer, locale="en")

    surface.show(AssistantAct(GREET))
    surface.next_turn()
    surface.show(
        AssistantAct(
            ASK_BLOCKING,
            {"rule_name": "when", "prompt_message_keys": ("ask.alpha.date_range",)},
            kind_key=KIND,
        )
    )

    assert surface.transcript == (
        "< greet\n"
        "    greet\n"
        "> hello\n"
        "< ask_blocking (alpha)\n"
        "    one thing first:\n"
        "    which dates?\n"
    )


def test_an_option_table_numbers_its_rows_and_shows_a_filter_note(
    renderer: ActRenderer,
) -> None:
    """Soft filtering that nobody can see is indistinguishable from no filtering at all."""
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT), renderer, locale="en")

    surface.show(a_slate_act(filter_notes=("budget_ceiling",)))

    rows = [line for line in surface.transcript.splitlines() if line.strip()[:2] in {"1.", "2."}]
    assert len(rows) == 2
    assert "1. alpha-1" in rows[0]
    assert "390.00 EUR" in rows[0]
    assert "!" in rows[1], f"the failed optional filter is invisible in {rows[1]!r}"
    assert "!" not in rows[0], "an option that fails nothing was marked anyway"


def test_the_same_session_produces_a_byte_identical_transcript(tmp_path: Path) -> None:
    """The property a stored expectation rests on: same script, same text, any process."""
    config = tmp_path / "config"
    catalog = YamlComponentCatalog(write_catalog(config), write_schemas(config))
    message_dir = write_messages(config)

    def replay() -> str:
        surface = ScriptedSurface(
            ("hello",),
            FrozenClock(DEFAULT_MOMENT),
            ActRenderer(message_dir, catalog, supported_locales=("en", "he")),
            locale="en",
        )
        surface.show(AssistantAct(GREET))
        surface.next_turn()
        surface.show(a_slate_act(filter_notes=("budget_ceiling",)))
        surface.show(AssistantAct(CLOSE))
        return surface.transcript

    assert replay() == replay()


def test_the_transcript_names_the_component_an_act_is_about(renderer: ActRenderer) -> None:
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT), renderer, locale="en")

    surface.show(AssistantAct(GREET))
    surface.show(a_slate_act())

    lines = surface.transcript.splitlines()
    assert lines[0] == "< greet"
    assert any(line == f"< {PRESENT_SLATE} ({KIND})" for line in lines)


# -- degrading rather than dying ---------------------------------------------------------------


class _RaisingRenderer(ActRenderer):
    """An Act Renderer that breaks its own contract. Not a thing that ships — a counterexample.

    The port promises ``render`` never raises for an Act in the closed vocabulary. This asserts
    what happens when a future renderer breaks that promise anyway: a marker and a log line,
    and a conversation that carries on, rather than a demonstration that ends in a traceback.
    """

    def render(self, act: AssistantAct, locale: str | None = None) -> RenderedAct:
        raise RuntimeError(f"I refuse to draw {act.act}")


def test_a_renderer_that_raises_becomes_a_visible_marker_and_a_log_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = tmp_path / "config"
    catalog = YamlComponentCatalog(write_catalog(config), write_schemas(config))
    surface = ScriptedSurface(
        (),
        FrozenClock(DEFAULT_MOMENT),
        _RaisingRenderer(write_messages(config), catalog),
        locale="en",
    )

    with caplog.at_level(logging.ERROR):
        surface.show(AssistantAct(GREET))

    assert surface.captured == (AssistantAct(GREET),)
    assert f"⟪missing:{GREET}⟫" in surface.transcript
    assert any("Act Renderer raised" in record.message for record in caplog.records)


def test_the_surface_names_itself() -> None:
    assert ScriptedSurface((), FrozenClock(DEFAULT_MOMENT)).surface_id == SCRIPTED_SURFACE_ID


def test_run_session_hands_back_what_the_conversation_answered() -> None:
    """The Scripted Surface owns no thread, so this is the identity — and has to stay one."""
    surface = ScriptedSurface((), FrozenClock(DEFAULT_MOMENT))

    assert surface.run_session(lambda: "an outcome") == "an outcome"
