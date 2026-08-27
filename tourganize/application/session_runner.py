"""The Session Runner: the only place the two ports meet.

It owns neither the dialogue nor the rendering. It greets, pumps turns until the surface
closes or the Director does, and hands every Act to the surface. That is deliberately about
twenty lines: a runner that grew a decision would be a second state machine, and the Dialogue
Director is the only one this system has.

Three rules keep it that size. It never reads the Planning Session for anything but the
session id it labels notices and logs with; it never branches on an Act beyond noticing the
one that says the conversation is over; and it never phrases anything, because a runner that
composed a sentence would be a second Message Catalogue.

What it *does* own is failure. A surface or a Director that raises ends the session with an
error rather than a traceback: the error is logged, the surface is told once, and the caller
turns :attr:`SessionOutcome.ok` into an exit code. Telling a broken surface that something
broke may itself fail, so the closing notices are best-effort — a surface that cannot say
goodbye must not replace the reason the session ended.

The session id is bound as log context for the whole run, so every line anything logs while
a conversation is in flight — the Director's, the Planning Service's, a source's — carries
the id of the transcript it belongs to. That is the one piece of correlation the runner is
the only place able to install, because it is the only place that knows a session is running.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from tourganize.dialogue import CLOSE, AssistantAct, DialogueDirector
from tourganize.domain.errors import SessionClosedError
from tourganize.platform.logging import log_context
from tourganize.ports.presentation import (
    NOTICE_CLOSING,
    NOTICE_FAILED,
    NOTICE_READY,
    NOTICE_WORKING,
    PresentationSurface,
    SurfaceNotice,
)

__all__ = ["SessionOutcome", "run"]

_LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    """What one session did, for the CLI's exit code and for the log."""

    session_id: str
    turns: int
    acts: int
    closed_by_director: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the session ended the way a session is meant to end.

        A traveller who walks away mid-conversation ended it the way a session is meant to
        end: ``next_turn`` answering ``None`` is the close signal, not a failure. Only an
        exception nobody expected is one.
        """
        return self.error is None


def run(
    director: DialogueDirector,
    surface: PresentationSurface,
    *,
    locale: str | None = None,
) -> SessionOutcome:
    """Greet, then pump turns until the surface or the Director closes the session."""
    # Two scalars are read off the Planning Session and nothing else is, because everything
    # else in it is the Director's business. The session id is the thread that ties a
    # transcript in telemetry to the run that produced it; the locale is the language the
    # farewell below has to be in, and it is read *here* rather than taken from the argument
    # because an interpreter may have detected a different one halfway through — saying
    # goodbye in the language the traveller started in would be the one sentence of the
    # session that ignored them.
    session_id = director.session.session_id
    turns = 0
    acts = 0
    closed = False
    error: str | None = None
    with log_context(session_id=session_id):
        try:
            emitted = director.begin(locale) if locale is not None else director.begin()
            acts += len(emitted)
            closed = _show_all(surface, emitted)
            while not closed and (turn := surface.next_turn()) is not None:
                turns += 1
                surface.notify(SurfaceNotice(NOTICE_WORKING, session_id=session_id))
                emitted = director.handle(turn)
                acts += len(emitted)
                closed = _show_all(surface, emitted)
                surface.notify(SurfaceNotice(NOTICE_READY, session_id=session_id))
            if not closed:
                # The surface answered ``None``: somebody pressed Ctrl+C, or a script ran
                # out. Saying goodbye is the one Act this loop constructs itself, and it is
                # worth being precise about why that is not the runner making a decision.
                # The traveller has *left* — there is no turn to interpret and no state to
                # move to, so there is nothing for the Director to be asked. ``close`` is the
                # vocabulary's own word for the event that just happened, it carries no
                # payload and no wording (the Message Catalogue still owns every word of it),
                # and a session that simply stopped mid-screen reads as a crash even when the
                # exit code says otherwise.
                acts += 1
                _show_all(surface, (AssistantAct(act=CLOSE, locale=director.session.locale),))
        except SessionClosedError:
            # The Director had already closed. Ending the loop is the whole response:
            # reopening a closed session is F12's `resume`, nothing was lost, and a surface
            # that asked one turn too many is not a failure of this session.
            closed = True
        except Exception as exc:
            # The boundary: every failure the Director or the surface can raise becomes an
            # outcome here, because above this line sits a command that owes the shell an
            # exit code and below it a conversation that may have been half told.
            error = f"{type(exc).__name__}: {exc}"
            _LOGGER.exception("session failed", extra={"kind": "session"})
            _quietly(surface, SurfaceNotice(NOTICE_FAILED, detail=error, session_id=session_id))
        finally:
            _quietly(surface, SurfaceNotice(NOTICE_CLOSING, session_id=session_id))
            _close(surface, session_id)
    return SessionOutcome(
        session_id=session_id, turns=turns, acts=acts, closed_by_director=closed, error=error
    )


def _show_all(surface: PresentationSurface, acts: Sequence[AssistantAct]) -> bool:
    """Show every Act in order, and report whether one of them ended the conversation.

    The only thing the runner reads out of an Act. Which Acts follow which is the Director's
    business, and a runner that learned a second Act name would be starting to have opinions.
    """
    closed = False
    for act in acts:
        surface.show(act)
        closed = closed or act.act == CLOSE
    return closed


def _quietly(surface: PresentationSurface, notice: SurfaceNotice) -> None:
    """Deliver one closing notice, swallowing a surface that fails while being told bad news.

    The port says ``notify`` must not raise, and a surface that breaks that during the
    conversation is a broken surface and ends the session like any other failure. These two
    notices are different: they are sent *because* the session is already ending, and the
    reason it is ending must survive a surface that cannot hear it.
    """
    try:
        surface.notify(notice)
    except Exception:
        _LOGGER.warning("surface refused notice %s", notice.code, exc_info=True)


def _close(surface: PresentationSurface, session_id: str) -> None:
    """Release the surface, exactly once, whatever happened before it."""
    try:
        surface.close()
    except Exception:
        _LOGGER.warning("surface %s failed to close", session_id, exc_info=True)
