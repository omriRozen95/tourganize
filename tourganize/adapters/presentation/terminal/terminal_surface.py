"""The Terminal Surface — a Textual application a traveller talks to.

The Director runs on the calling thread and Textual runs the interface on its own, so the two
meet at a queue: :meth:`TerminalSurface.next_turn` blocks until the input line produces a
line, and :meth:`show` posts a rendered Act to the transcript pane. That is the whole of the
concurrency here, and it is why the input line is disabled between "a turn was submitted" and
"the Director came back": a turn typed into a working session would otherwise be silently
dropped, which is the one thing a demonstration must never do.

``Ctrl+C`` is a clean close, not a traceback: it stops the app and makes :meth:`next_turn`
answer ``None``, which the session runner reads as "the traveller left" — the same signal a
script that ran out of lines gives.

**Which thread is whose, and why it is that way round.** Textual's POSIX driver installs
signal handlers for ``SIGTSTP``, ``SIGCONT`` and ``SIGWINCH`` while it starts, and
:func:`signal.signal` may only be called from the main thread — so a real, interactive Textual
app started on a worker thread does not merely misbehave, it silently declines to start. The
interface therefore owns the *process's* main thread and the Director's loop is the one that
moves: :meth:`run_session` is the entry point, it runs the app where it was called and the
conversation on a thread it spawns. Everything above still reads as the docstring's first
paragraph describes it, because the *Director's* thread is the one that calls this port, and
from there ``next_turn`` blocks on a queue exactly as promised.

The headless driver installs no handlers, so ``headless=True`` runs anywhere — which is how
the contract suite exercises this surface on a machine with no TTY, and how a container
without one fails loudly rather than half-starting.

**Telling the surface what the Director is doing.** The status line shows the session id, the
Component Kind in focus and — with ``debug_status`` — the Dialogue State. The first two the
surface can work out on its own: the id is constructor configuration, and every Assistant Act
that concerns a component names it, so :meth:`show` keeps the focus up to date without being
told. The Dialogue State is *not* in an Act, and reading it off the Director would be a
surface touching the Director's internals, which the port forbids. So it is pushed in:
:meth:`status` takes ``kind_key`` and ``state`` keyword arguments, either of which may be
omitted to leave that half alone, and the Session Runner calls it when it wants the line to
say more. Nothing breaks when nobody ever calls it; the line simply shows less.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from queue import SimpleQueue
from typing import ClassVar, Final, TypeVar, final

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Input, RichLog, Static

from tourganize.adapters.presentation import rendered_lines
from tourganize.dialogue import AssistantAct, UserTurn
from tourganize.language.act_renderer import ActRenderer, RenderedAct, missing_marker
from tourganize.platform.errors import ContractViolationError
from tourganize.ports.platform import Clock
from tourganize.ports.presentation import (
    NOTICE_CLOSING,
    NOTICE_READY,
    NOTICE_WORKING,
    SurfaceNotice,
)

__all__ = ["TERMINAL_SURFACE_ID", "TerminalSurface"]

LOGGER: Final = logging.getLogger("tourganize.adapters.presentation.terminal")

TERMINAL_SURFACE_ID: Final = "terminal"

#: How the transcript pane marks a line the traveller typed. It is an echo, not a prompt: the
#: input line has already been cleared by the time it appears, and a session read back off the
#: screen should tell the two directions apart at a glance.
ECHO_PREFIX: Final = "> "

#: What separates the segments of the status line. The segments themselves are identifiers —
#: a session id, a ``kind_key``, a Dialogue State, a notice code — and never phrases, so the
#: line needs no Message Catalogue and says the same thing in every locale.
STATUS_SEPARATOR: Final = " | "

#: How long :meth:`TerminalSurface.run_session` waits for the conversation thread after the
#: interface has stopped. It is a backstop, not a schedule: the thread has already been told
#: to finish by the ``None`` :meth:`TerminalSurface.close` puts on the queue, and a thread
#: still running after this was stuck somewhere the surface cannot reach.
WORKER_JOIN_TIMEOUT: Final = 5.0

_Result = TypeVar("_Result")


@final
class _PasteInput(Input):
    """An ``Input`` that keeps the whole of a multi-line paste instead of its first line.

    Textual's own handler takes ``event.text.splitlines()[0]`` and drops the rest, which for a
    traveller pasting an itinerary out of an email means most of it disappears with no warning
    at all. One turn is still one line, so the lines are joined by spaces rather than kept: the
    surface's job is not to lose what was pasted, and deciding what a paragraph *means* is the
    Turn Interpreter's.
    """

    def _on_paste(self, event: events.Paste) -> None:
        joined = " ".join(part.strip() for part in event.text.splitlines() if part.strip())
        if joined:
            self.insert_text_at_cursor(joined)
        # Both, and both are load-bearing. `stop` keeps the paste from bubbling to the app;
        # `prevent_default` is what stops Textual walking on down the MRO to ``Input``'s own
        # handler, which would insert the first line a second time.
        event.prevent_default()
        event.stop()


class _ChatApp(App[None]):
    """The widgets: a scrolling transcript, a status line, an input line.

    It holds no conversation state and makes no decisions. Everything it knows arrives through
    a method call from :class:`TerminalSurface`, and everything it learns leaves through one of
    the three callbacks it was constructed with — which is what keeps the state machine in the
    Dialogue Director, where the system's only state machine belongs.
    """

    CSS: ClassVar[str] = """
    Screen { layout: vertical; }
    #transcript { height: 1fr; padding: 0 1; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #entry { height: 3; }
    """

    #: ``Ctrl+C`` is Textual's own binding by default, and it quits the application out from
    #: under the conversation. Here it is a *close*: the surface is told, the Director's thread
    #: is woken with the same ``None`` a finished script gives, and the interface stays up
    #: until the session has said its goodbye.
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "leave", show=False, priority=True, system=True),
    ]

    def __init__(
        self,
        *,
        on_line: Callable[[str], None],
        on_leave: Callable[[], None],
        on_started: Callable[[], None],
        lines: tuple[str, ...] = (),
        status: str = "",
    ) -> None:
        super().__init__()
        self._on_line = on_line
        self._on_leave = on_leave
        self._on_started = on_started
        self._backlog = lines
        self._status = status

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False)
        # Markup off, here and in the log: a status segment is an identifier the machine
        # chose — a session id, a ``kind_key`` — and one containing a bracket would be
        # read as a style tag and disappear.
        yield Static(self._status, id="status", markup=False)
        yield _PasteInput(id="entry")

    def on_mount(self) -> None:
        """Draw whatever was shown before the interface existed, then start the session."""
        self.append(self._backlog)
        self.query_one("#entry", Input).focus()
        self._on_started()

    def append(self, lines: tuple[str, ...]) -> None:
        """Add lines to the transcript pane. Called on this application's own thread."""
        log = self.query_one("#transcript", RichLog)
        for line in lines:
            log.write(line)

    def set_status(self, text: str) -> None:
        """Replace the status line."""
        self.query_one("#status", Static).update(text)

    def set_accepting(self, accepting: bool) -> None:
        """Enable or disable the input line."""
        entry = self.query_one("#entry", Input)
        entry.disabled = not accepting
        if accepting:
            entry.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.value = ""
        self._on_line(event.value)

    def action_leave(self) -> None:
        self._on_leave()


@final
class TerminalSurface:
    """A scrolling transcript, an input line and a status line."""

    def __init__(
        self,
        renderer: ActRenderer,
        clock: Clock,
        *,
        locale: str,
        session_id: str = "",
        debug_status: bool = False,
        headless: bool = False,
    ) -> None:
        self._renderer = renderer
        self._clock = clock
        self._locale = locale
        self._session_id = session_id
        self._debug_status = debug_status
        self._headless = headless
        self._typed: SimpleQueue[str | None] = SimpleQueue()
        self._closed = threading.Event()
        self._index = 0
        self._lines: list[str] = []
        self._notices: list[SurfaceNotice] = []
        self._accepting = True
        self._kind_key = ""
        self._state = ""
        self._notice_code = ""
        self._app: _ChatApp | None = None
        self._ui_thread: int | None = None

    @property
    def surface_id(self) -> str:
        return TERMINAL_SURFACE_ID

    @property
    def transcript(self) -> str:
        """Everything the transcript pane has been given, in order.

        The pane itself is a widget and a widget is not observable from a test, so the lines
        are kept here as well. It is also what a session started after the interface was
        already running would be caught up with.
        """
        return "".join(f"{line}\n" for line in self._lines)

    @property
    def notices(self) -> tuple[SurfaceNotice, ...]:
        """Every out-of-band notice, in order."""
        return tuple(self._notices)

    @property
    def accepting_input(self) -> bool:
        """Whether the input line is taking turns at the moment."""
        return self._accepting

    @property
    def status_line(self) -> str:
        """What the status line says: session id, Component Kind, Dialogue State, notice.

        Every segment is an identifier rather than a phrase — a ``kind_key``, a state name, a
        notice code — so the line needs no Message Catalogue and reads the same in every
        locale. Empty segments are left out, and the Dialogue State only appears at all when
        the surface was built with ``debug_status``: it is priceless while developing and
        noise in front of a client.
        """
        segments = [self._session_id, self._kind_key]
        if self._debug_status:
            segments.append(self._state)
        segments.append(self._notice_code)
        return STATUS_SEPARATOR.join(segment for segment in segments if segment)

    def run_session(self, pump: Callable[[], _Result]) -> _Result:
        """Run the interface on this thread and ``pump`` — the conversation — on another.

        Answers with whatever ``pump`` answered, and re-raises whatever it raised, so that a
        caller can write ``surface.run_session(lambda: run(director, surface))`` and treat the
        result as if the runner had been called directly. The interface stops when ``pump``
        returns, which is *after* the closing Act has been shown rather than the moment
        ``Ctrl+C`` was pressed.

        Must be called from the main thread unless ``headless`` was set: see this module's
        docstring for why, and note that the failure it prevents is a silent one.
        """
        if not self._headless and threading.current_thread() is not threading.main_thread():
            raise ContractViolationError(
                "the Terminal Surface must be run from the main thread: Textual's driver "
                "installs POSIX signal handlers, which no other thread may do, and an "
                "interface started anywhere else stops without saying so"
            )
        results: list[_Result] = []
        failure: list[BaseException] = []

        def conversation() -> None:
            try:
                results.append(pump())
            except BaseException as error:
                failure.append(error)
            finally:
                self.close()
                self._post(lambda app: app.exit())

        worker = threading.Thread(target=conversation, name="tourganize-session", daemon=True)
        app = _ChatApp(
            on_line=self._submitted,
            on_leave=self.close,
            on_started=worker.start,
            lines=tuple(self._lines),
            status=self.status_line,
        )
        self._app = app
        self._ui_thread = threading.get_ident()
        try:
            app.run(headless=self._headless)
        finally:
            self._app = None
            self._ui_thread = None
            # An interface that stopped for its own reasons leaves the conversation blocked in
            # next_turn. Closing here is what unblocks it, and is why the join below is a
            # backstop rather than the mechanism.
            self.close()
            worker.join(WORKER_JOIN_TIMEOUT)
        if failure:
            raise failure[0]
        if not results:
            raise ContractViolationError(
                "the terminal session never ran: the interface stopped before it started"
            )
        return results[0]

    def status(self, *, kind_key: str | None = None, state: str | None = None) -> None:
        """Tell the status line what the Director is doing.

        Both arguments are optional and ``None`` means "leave that half as it was", so a caller
        that only knows one of them says only that. Pass an empty string to clear a segment.
        ``state`` is only ever shown when the surface was built with ``debug_status``.
        """
        if kind_key is not None:
            self._kind_key = kind_key
        if state is not None:
            self._state = state
        self._refresh_status()

    def show(self, act: AssistantAct) -> None:
        if act.kind_key is not None:
            self._kind_key = act.kind_key
        self._write(rendered_lines(self._draw(act)))
        self._refresh_status()

    def next_turn(self) -> UserTurn | None:
        if self._closed.is_set():
            return None
        # Belt and braces against a lost turn: the Director asking for the next one is proof
        # it has finished with the last, whether or not anybody sent NOTICE_READY.
        self._accept_input(True)
        text = self._typed.get()
        if text is None or self._closed.is_set():
            return None
        turn = UserTurn(
            index=self._index,
            text=text,
            received_at=self._clock.now(),
            locale_hint=self._locale,
        )
        self._index += 1
        return turn

    def notify(self, notice: SurfaceNotice) -> None:
        self._notices.append(notice)
        self._notice_code = notice.code
        if notice.code == NOTICE_WORKING:
            self._accept_input(False)
        elif notice.code == NOTICE_READY:
            self._accept_input(True)
        elif notice.code == NOTICE_CLOSING:
            self._accept_input(False)
        self._refresh_status()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        # Wakes a conversation blocked in next_turn. The interface is deliberately *not*
        # stopped here: run_session stops it when the conversation returns, so a farewell Act
        # shown after the traveller pressed Ctrl+C still reaches the screen.
        self._typed.put(None)
        self._accept_input(False)

    def _submitted(self, text: str) -> None:
        """One line arrived from the input widget, on the application's own thread."""
        self._accept_input(False)
        self._write((f"{ECHO_PREFIX}{text}",))
        self._typed.put(text)

    def _draw(self, act: AssistantAct) -> RenderedAct:
        """Render one Act, degrading to a visible marker rather than ending the session."""
        try:
            return self._renderer.render(act, self._locale)
        except Exception:
            LOGGER.exception("the Act Renderer raised on %s; showing a marker instead", act.act)
            return RenderedAct(act=act.act, heading=missing_marker(act.act), kind_key=act.kind_key)

    def _write(self, lines: tuple[str, ...]) -> None:
        if not lines:
            return
        self._lines += lines
        self._post(lambda app: app.append(lines))

    def _accept_input(self, accepting: bool) -> None:
        if self._accepting == accepting:
            return
        self._accepting = accepting
        self._post(lambda app: app.set_accepting(accepting))

    def _refresh_status(self) -> None:
        text = self.status_line
        self._post(lambda app: app.set_status(text))

    def _post(self, action: Callable[[_ChatApp], None]) -> None:
        """Apply ``action`` to the running interface, from whichever thread we are on.

        No interface, or one that has stopped, is not an error: the surface keeps its own copy
        of everything it was told, so an update with nowhere to go is a line already recorded.
        """
        app = self._app
        if app is None or not app.is_running:
            return
        if threading.get_ident() == self._ui_thread:
            action(app)
            return
        try:
            app.call_from_thread(action, app)
        except RuntimeError:
            LOGGER.debug("the interface stopped before an update reached it")
