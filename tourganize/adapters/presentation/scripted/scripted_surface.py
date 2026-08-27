"""A headless Presentation Surface: a list of lines in, every Act captured.

This is the cheapest possible integration test and the backbone of F11's Golden
Conversations. It has no terminal, no dependency and no I/O of its own beyond an optional
transcript file, and it records **exactly** what a traveller would have been shown:
:attr:`ScriptedSurface.captured` holds the Assistant Acts as the Director emitted them, and
:attr:`ScriptedSurface.rendered` holds what the Act Renderer made of them, so an expectation
can be pinned against either the structure or the words.

Three decisions are worth the paragraph they cost.

**The turn index is this surface's own counter, not the session's.** A surface that asked the
Director what number the next turn is would be reading the Director's state, which the port
forbids. It does not need to: the Session Runner hands every turn this surface yields to
``handle()`` exactly once, so a counter starting at zero stays in lockstep with
``PlanningSession.next_turn_index`` by construction. If they ever disagree, a turn was dropped
between the two — which is a bug worth the loud failure the Director raises for it.

**Rendering is optional and never fatal.** With no Act Renderer the surface still captures
Acts and still writes a transcript, of the Act names alone: F11 replays structure, and a
harness that could not run without a Message Catalogue would be a harness coupled to wording.
When a renderer *is* supplied and it raises anyway — which its own contract says it will not —
the Act is recorded with a visible missing marker instead, because a surface that crashed on a
rendering fault would take the conversation with it.

**The transcript is a byte-stable artefact.** Same script, same Acts, same text, in any
process: that is the property a stored expectation rests on, so nothing here formats a
timestamp, an address or anything else the machine chooses.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, TypeVar, final

from tourganize.adapters.presentation import rendered_lines
from tourganize.dialogue import AssistantAct, UserTurn
from tourganize.language.act_renderer import ActRenderer, RenderedAct, missing_marker
from tourganize.platform.errors import ConfigurationError
from tourganize.ports.platform import Clock
from tourganize.ports.presentation import SurfaceNotice

__all__ = ["SCRIPTED_SURFACE_ID", "ScriptedSurface", "read_script"]

LOGGER: Final = logging.getLogger("tourganize.adapters.presentation.scripted")

SCRIPTED_SURFACE_ID: Final = "scripted"

#: A line whose first non-space character is this is a note to the reader, not a turn. Scripts
#: are read by people — a Golden Conversation that cannot say what it demonstrates is a wall of
#: sentences nobody dares change.
COMMENT_PREFIX: Final = "#"

#: How the transcript marks what the assistant showed, what the traveller typed, and the text
#: of a rendered Act. Deliberately unlike anything a traveller would type, so that a transcript
#: can be read back line by line without an escaping rule.
SHOWN_PREFIX: Final = "< "
TYPED_PREFIX: Final = "> "
RENDERED_PREFIX: Final = "    "

_Result = TypeVar("_Result")


def read_script(path: Path) -> tuple[str, ...]:
    """Read a transcript file as one turn per line, dropping blanks and ``#`` comments.

    A blank line is not a turn and neither is a line of only whitespace: an empty utterance is
    a thing a traveller can do, but it is not a thing a *file* can express unambiguously, and a
    script that gained a turn because somebody left a trailing newline would be unreadable.
    Each turn is stripped, so invisible trailing spaces cannot change what a session does.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"cannot read the transcript file {path}: {error}") from error
    turns = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(COMMENT_PREFIX):
            turns.append(stripped)
    return tuple(turns)


@final
class ScriptedSurface:
    """Replays ``script`` in order, then closes by answering ``None``."""

    def __init__(
        self,
        script: Sequence[str],
        clock: Clock,
        renderer: ActRenderer | None = None,
        *,
        locale: str | None = None,
    ) -> None:
        self._script = tuple(script)
        self._clock = clock
        self._renderer = renderer
        self._locale = locale
        self._position = 0
        self._captured: list[AssistantAct] = []
        self._rendered: list[RenderedAct] = []
        self._notices: list[SurfaceNotice] = []
        self._lines: list[str] = []
        self._closed = False

    @property
    def surface_id(self) -> str:
        return SCRIPTED_SURFACE_ID

    @property
    def captured(self) -> tuple[AssistantAct, ...]:
        """Every Act this surface was shown, in order."""
        return tuple(self._captured)

    @property
    def rendered(self) -> tuple[RenderedAct, ...]:
        """Every Act as the Act Renderer drew it. Empty when no renderer was supplied."""
        return tuple(self._rendered)

    @property
    def notices(self) -> tuple[SurfaceNotice, ...]:
        """Every out-of-band notice, in order."""
        return tuple(self._notices)

    @property
    def transcript(self) -> str:
        """The whole session as text: what was shown, and what was typed back.

        Notices are deliberately absent. A notice is what the *program* was doing, never part
        of what was said (see :mod:`tourganize.ports.presentation`), and a transcript that
        moved when telemetry did would be a fragile thing to pin an expectation to.
        """
        return "".join(f"{line}\n" for line in self._lines)

    def run_session(self, pump: Callable[[], _Result]) -> _Result:
        """Run ``pump`` and answer with its result.

        There is nothing to own here — no event loop, no terminal — so this is ``pump()`` and
        a docstring. It exists because the Terminal Surface's version is *not* trivial, and a
        caller that had to know which kind of surface it was holding would be a caller with a
        branch in it. See :class:`~tourganize.adapters.presentation.terminal.TerminalSurface`.
        """
        return pump()

    def show(self, act: AssistantAct) -> None:
        self._captured.append(act)
        named = f"{SHOWN_PREFIX}{act.act}"
        self._lines.append(named if act.kind_key is None else f"{named} ({act.kind_key})")
        renderer = self._renderer
        if renderer is None:
            return
        rendered = self._draw(renderer, act)
        self._rendered.append(rendered)
        self._lines += [f"{RENDERED_PREFIX}{line}" for line in rendered_lines(rendered)]

    def next_turn(self) -> UserTurn | None:
        if self._closed or self._position >= len(self._script):
            return None
        text = self._script[self._position]
        turn = UserTurn(
            index=self._position,
            text=text,
            received_at=self._clock.now(),
            locale_hint=self._locale,
        )
        self._position += 1
        self._lines.append(f"{TYPED_PREFIX}{text}")
        return turn

    def notify(self, notice: SurfaceNotice) -> None:
        self._notices.append(notice)

    def close(self) -> None:
        self._closed = True

    def _draw(self, renderer: ActRenderer, act: AssistantAct) -> RenderedAct:
        """Render one Act, degrading to a visible marker rather than ending the session."""
        try:
            return renderer.render(act, self._locale)
        except Exception:
            # The Act Renderer promises never to raise for an Act in the closed vocabulary, so
            # arriving here is a bug in the renderer. It is still not this surface's business
            # to end the conversation over one: the marker is the same one a missing message
            # key produces, and the log entry is where the bug gets found.
            LOGGER.exception("the Act Renderer raised on %s; showing a marker instead", act.act)
            return RenderedAct(act=act.act, heading=missing_marker(act.act), kind_key=act.kind_key)
