"""The Terminal Surface, driven headlessly: the queue, the status line and the widgets.

Textual's headless driver is the same application with the display and the keyboard taken
away — the widgets are built, the CSS is parsed, and messages are dispatched on the
application's own thread exactly as they are in front of a person. That is what makes this
adapter testable in CI, which has no TTY at all, and it is why nothing here is skipped.

The file has two halves. The first drives the *surface*: :meth:`TerminalSurface.run_session`
really runs a Textual application, and everything asserted about the queue, the input line and
the status line happens inside one. Proof that the interface actually started is structural
rather than visual — ``run_session`` only returns a result at all if the application mounted,
because the conversation thread is started from ``on_mount`` and nowhere else.

The second half drives the *widgets*, through Textual's own pilot: a key pressed, a line
submitted, a multi-line paste, ``Ctrl+C``. Those are the four things a person does that the
surface has to survive, and they cannot be reached by calling methods on the surface.

Visual correctness in Hebrew is deliberately not asserted anywhere. Bidi shaping is F10's, and
what F07 promises is narrower and testable: Hebrew goes through every Act in the vocabulary
without raising, and comes out in logical order.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
from conftest import write_catalog, write_messages, write_schemas
from textual import events
from textual.widgets import Input, RichLog, Static

from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.presentation.terminal import TERMINAL_SURFACE_ID, TerminalSurface
from tourganize.adapters.presentation.terminal.terminal_surface import _ChatApp
from tourganize.dialogue import (
    ACT_VOCABULARY,
    CLOSE,
    GREET,
    PRESENT_SLATE,
    AssistantAct,
    UserTurn,
)
from tourganize.language.act_renderer import ActRenderer
from tourganize.platform.errors import ContractViolationError
from tourganize.ports.presentation import (
    NOTICE_CLOSING,
    NOTICE_READY,
    NOTICE_WORKING,
    PresentationSurface,
    SurfaceNotice,
)

#: The Component Kind these Acts are about — the sample catalog's, so that no travel topic has
#: to be named to test a surface.
KIND: Final = "alpha"


def build_renderer(root: Path) -> ActRenderer:
    """An Act Renderer over this test's own Message Catalogue and sample catalog."""
    config = root / "config"
    catalog = YamlComponentCatalog(write_catalog(config), write_schemas(config))
    return ActRenderer(
        write_messages(config), catalog, supported_locales=("en", "he"), default_locale="en"
    )


@pytest.fixture
def renderer(tmp_path: Path) -> ActRenderer:
    return build_renderer(tmp_path)


def a_surface(
    renderer: ActRenderer,
    *,
    locale: str = "en",
    session_id: str = "unit",
    debug_status: bool = False,
) -> TerminalSurface:
    """A headless Terminal Surface, since no test in this repository owns a terminal."""
    return TerminalSurface(
        renderer,
        FrozenClock(DEFAULT_MOMENT),
        locale=locale,
        session_id=session_id,
        debug_status=debug_status,
        headless=True,
    )


# -- the surface -------------------------------------------------------------------------------


def test_the_surface_names_itself_and_satisfies_the_port(renderer: ActRenderer) -> None:
    surface = a_surface(renderer)

    assert surface.surface_id == TERMINAL_SURFACE_ID
    assert isinstance(surface, PresentationSurface)


def test_the_conversation_runs_on_a_thread_of_its_own_and_answers_the_caller(
    renderer: ActRenderer,
) -> None:
    """Also the proof the interface started: the thread is spawned from ``on_mount`` only."""
    surface = a_surface(renderer)
    caller = threading.get_ident()
    ran_on: list[int] = []

    answer = surface.run_session(lambda: ran_on.append(threading.get_ident()) or "done")

    assert answer == "done"
    assert ran_on and ran_on[0] != caller


def test_what_the_conversation_raised_is_raised_at_the_caller(renderer: ActRenderer) -> None:
    """A failure on the conversation thread must not be swallowed by the interface."""
    surface = a_surface(renderer)

    def explode() -> None:
        raise RuntimeError("the Director gave up")

    with pytest.raises(RuntimeError, match="the Director gave up"):
        surface.run_session(explode)


def test_showing_an_act_draws_its_lines(renderer: ActRenderer) -> None:
    surface = a_surface(renderer)

    surface.run_session(lambda: surface.show(AssistantAct(GREET)))

    assert surface.transcript == "greet\n"


def test_every_act_in_the_vocabulary_renders_in_hebrew_without_raising(
    renderer: ActRenderer,
) -> None:
    """F07 promises Hebrew does not crash. Making it *look* right is F10's, and only F10's."""
    surface = a_surface(renderer, locale="he")

    def body() -> None:
        for name in sorted(ACT_VOCABULARY):
            surface.show(AssistantAct(name, locale="he", kind_key=KIND))

    surface.run_session(body)

    drawn = surface.transcript
    assert drawn.count("he:") >= len(ACT_VOCABULARY), f"the Hebrew catalogue was not used: {drawn}"


def test_a_notice_that_the_director_is_working_closes_the_input_line(
    renderer: ActRenderer,
) -> None:
    surface = a_surface(renderer)
    seen: list[bool] = []

    def body() -> None:
        surface.notify(SurfaceNotice(NOTICE_WORKING, session_id="unit"))
        seen.append(surface.accepting_input)
        surface.notify(SurfaceNotice(NOTICE_READY, session_id="unit"))
        seen.append(surface.accepting_input)

    surface.run_session(body)

    assert seen == [False, True]


def test_a_closing_notice_closes_the_input_line_too(renderer: ActRenderer) -> None:
    surface = a_surface(renderer)

    surface.run_session(lambda: surface.notify(SurfaceNotice(NOTICE_CLOSING)))

    assert surface.accepting_input is False


def test_a_submitted_turn_shuts_the_input_until_the_director_asks_for_the_next_one(
    renderer: ActRenderer,
) -> None:
    """The one thing a demonstration must never do is lose a turn somebody typed."""
    surface = a_surface(renderer)
    states: list[bool] = []
    turns: list[UserTurn | None] = []

    def body() -> None:
        surface._submitted("a hotel in Paris")
        states.append(surface.accepting_input)
        turns.append(surface.next_turn())
        states.append(surface.accepting_input)

    surface.run_session(body)

    assert states == [False, True], "the input line did not wait for the Director"
    typed = turns[0]
    assert isinstance(typed, UserTurn)
    assert typed.text == "a hotel in Paris"
    assert typed.index == 0
    assert typed.received_at == DEFAULT_MOMENT
    assert typed.locale_hint == "en"


def test_a_submitted_turn_is_echoed_into_the_transcript(renderer: ActRenderer) -> None:
    surface = a_surface(renderer)

    surface.run_session(lambda: surface._submitted("hello"))

    assert surface.transcript == "> hello\n"


def test_turns_are_numbered_from_zero_so_they_match_the_session_the_runner_drives(
    renderer: ActRenderer,
) -> None:
    surface = a_surface(renderer)
    numbers: list[int] = []

    def body() -> None:
        for text in ("one", "two", "three"):
            surface._submitted(text)
            turn = surface.next_turn()
            assert turn is not None
            numbers.append(turn.index)

    surface.run_session(body)

    assert numbers == [0, 1, 2]


def test_closing_the_surface_makes_the_next_turn_none_and_keeps_it_none(
    renderer: ActRenderer,
) -> None:
    """``Ctrl+C`` arrives here: the binding calls ``close``, and this is what that means."""
    surface = a_surface(renderer)
    answers: list[UserTurn | None] = []

    def body() -> None:
        surface.close()
        answers.append(surface.next_turn())
        answers.append(surface.next_turn())

    surface.run_session(body)

    assert answers == [None, None]


def test_a_turn_queued_before_the_surface_closed_is_not_handed_over_afterwards(
    renderer: ActRenderer,
) -> None:
    """Closing is not a pause. A turn typed and then abandoned belongs to no session."""
    surface = a_surface(renderer)
    answers: list[UserTurn | None] = []

    def body() -> None:
        surface._submitted("too late")
        surface.close()
        answers.append(surface.next_turn())

    surface.run_session(body)

    assert answers == [None]


# -- the status line ---------------------------------------------------------------------------


def test_the_status_line_shows_the_session_and_the_component_but_not_the_state(
    renderer: ActRenderer,
) -> None:
    """The Dialogue State is priceless while developing and noise in front of a client."""
    surface = a_surface(renderer, session_id="demo-1")

    surface.status(kind_key=KIND, state="AWAITING_CHOICE")

    assert surface.status_line == f"demo-1 | {KIND}"


def test_the_status_line_shows_the_dialogue_state_when_it_was_asked_for(
    renderer: ActRenderer,
) -> None:
    surface = a_surface(renderer, session_id="demo-1", debug_status=True)

    surface.status(kind_key=KIND, state="AWAITING_CHOICE")

    assert surface.status_line == f"demo-1 | {KIND} | AWAITING_CHOICE"


def test_status_leaves_alone_the_half_it_was_not_told_about(renderer: ActRenderer) -> None:
    surface = a_surface(renderer, session_id="demo-1", debug_status=True)
    surface.status(kind_key=KIND, state="GREETING")

    surface.status(state="AWAITING_CHOICE")

    assert surface.status_line == f"demo-1 | {KIND} | AWAITING_CHOICE"
    surface.status(kind_key="")
    assert surface.status_line == "demo-1 | AWAITING_CHOICE"


def test_the_status_line_follows_the_component_the_acts_are_about(
    renderer: ActRenderer,
) -> None:
    """Nobody has to tell the surface this: every Act about a component names it."""
    surface = a_surface(renderer, session_id="demo-1")

    surface.show(AssistantAct(PRESENT_SLATE, {"options": ()}, kind_key=KIND))

    assert surface.status_line == f"demo-1 | {KIND}"
    surface.show(AssistantAct(CLOSE))
    assert surface.status_line == f"demo-1 | {KIND}", "an Act about nothing cleared the focus"


def test_the_status_line_carries_the_latest_notice_code(renderer: ActRenderer) -> None:
    surface = a_surface(renderer, session_id="demo-1")

    surface.notify(SurfaceNotice(NOTICE_WORKING))

    assert surface.status_line == f"demo-1 | {NOTICE_WORKING}"


# -- running the interface where it can actually run ---------------------------------------------


def test_an_interactive_interface_refuses_to_start_off_the_main_thread(
    renderer: ActRenderer,
) -> None:
    """The failure this prevents is silent: Textual's driver simply does not come up.

    ``signal.signal`` may only be called from the main thread, and the POSIX driver calls it
    while starting. A surface that let that happen would print nothing, accept nothing and
    exit 0, which is the worst way for a demonstration to fail.
    """
    surface = TerminalSurface(renderer, FrozenClock(DEFAULT_MOMENT), locale="en")
    refusals: list[BaseException] = []

    def go() -> None:
        try:
            surface.run_session(lambda: None)
        except ContractViolationError as error:
            refusals.append(error)

    worker = threading.Thread(target=go)
    worker.start()
    worker.join(10)

    assert refusals and "main thread" in str(refusals[0])


# -- the widgets, through Textual's own pilot ------------------------------------------------


def an_app(
    *,
    on_line: Callable[[str], None] = lambda _line: None,
    on_leave: Callable[[], None] = lambda: None,
) -> _ChatApp:
    """A bare chat application, with the surface's callbacks replaced by recorders."""
    return _ChatApp(on_line=on_line, on_leave=on_leave, on_started=lambda: None)


def test_typing_a_line_and_pressing_enter_yields_it_and_clears_the_input() -> None:
    lines: list[str] = []

    async def scenario() -> None:
        app = an_app(on_line=lines.append)
        async with app.run_test() as pilot:
            await pilot.press("h", "i", "enter")
            assert app.query_one("#entry", Input).value == ""

    asyncio.run(scenario())

    assert lines == ["hi"]


def test_ctrl_c_asks_the_surface_to_close_rather_than_killing_the_application() -> None:
    """Exit 0 with a farewell, not a traceback: the app stays up until the session is done."""
    left: list[bool] = []

    async def scenario() -> None:
        app = an_app(on_leave=lambda: left.append(True))
        async with app.run_test() as pilot:
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.is_running, "Ctrl+C stopped the application instead of closing the surface"

    asyncio.run(scenario())

    assert left == [True]


def test_a_multi_line_paste_arrives_as_one_turn_rather_than_its_first_line() -> None:
    """Textual's own ``Input`` keeps ``splitlines()[0]`` and drops the rest, silently."""
    pasted: list[str] = []

    async def scenario() -> None:
        app = an_app(on_line=pasted.append)
        async with app.run_test() as pilot:
            entry = app.query_one("#entry", Input)
            entry.post_message(events.Paste("a hotel in Paris\n23-28 October 2026\n"))
            await pilot.pause()
            await pilot.press("enter")

    asyncio.run(scenario())

    assert pasted == ["a hotel in Paris 23-28 October 2026"]


def test_the_application_draws_what_it_is_given_including_hebrew() -> None:
    """The pane is a widget, so the assertion is that nothing raised and the lines are there."""

    async def scenario() -> None:
        app = an_app()
        async with app.run_test() as pilot:
            app.append(("שלום", "hello"))
            app.set_status("demo-1 | alpha | AWAITING_CHOICE")
            await pilot.pause()
            assert app.query_one("#transcript", RichLog).lines
            assert "AWAITING_CHOICE" in str(app.query_one("#status", Static).content)

    asyncio.run(scenario())


def test_the_input_line_can_be_shut_and_opened_again() -> None:
    async def scenario() -> None:
        app = an_app()
        async with app.run_test() as pilot:
            app.set_accepting(False)
            await pilot.pause()
            assert app.query_one("#entry", Input).disabled is True
            app.set_accepting(True)
            await pilot.pause()
            assert app.query_one("#entry", Input).disabled is False

    asyncio.run(scenario())
