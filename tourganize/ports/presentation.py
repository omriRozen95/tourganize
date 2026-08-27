"""The ``PresentationSurface`` port: where a traveller and the Director meet.

One protocol and one value object, and both are deliberately small. A surface **renders
Assistant Acts and yields User Turns** — it never reads the Planning Session, never looks at
a Trip Plan and never decides what happens next, because the Dialogue Director owns all of
the control flow and a surface that started making decisions would be a second state machine
nobody could replay. In the other direction the Director never learns which surface is
attached: :mod:`tourganize.application.session_runner` is the only place the two ports meet.

``next_turn`` returning ``None`` is **the close signal**, and it is part of the contract
rather than an implementation detail: a terminal where somebody pressed ``Ctrl+C``, a script
that ran out of lines and a socket that hung up are the same event, and a surface reports it
the same way. Nothing else in the protocol may raise to say "we are finished".

:class:`SurfaceNotice` is out-of-band status — *sourcing is taking a moment*, *that failed* —
and is not part of the transcript. It carries an opaque ``code`` for a surface to render, and
a ``detail`` that is English and diagnostic. That is not the "no prose" rule being bent: the
rule governs Assistant Acts, which are what the traveller is *told*; a notice is what the
program is *doing*, it is never phrased into the conversation, and F11's harness ignores it.

Adapters: ``tourganize.adapters.presentation.terminal`` (a person types into it) and
``tourganize.adapters.presentation.scripted`` (a list of strings, headless — the harness
backbone of F11 and the cheapest integration test there is).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from tourganize.dialogue import AssistantAct, UserTurn

__all__ = [
    "NOTICE_CLOSING",
    "NOTICE_CODES",
    "NOTICE_FAILED",
    "NOTICE_READY",
    "NOTICE_WORKING",
    "PresentationSurface",
    "SurfaceNotice",
]

#: The Director has been handed a turn and has not answered yet. A surface disables its
#: input line on this one, so that a turn typed while the Director is working is not lost.
NOTICE_WORKING: Final = "working"
#: The Director has answered and the surface may accept input again.
NOTICE_READY: Final = "ready"
#: Something went wrong that is not an Assistant Act — the session runner caught an exception.
NOTICE_FAILED: Final = "failed"
#: The session is ending. The last thing a surface is told before :meth:`close`.
NOTICE_CLOSING: Final = "closing"

#: The notice codes this release emits, for tests and for a surface that wants to be
#: exhaustive. Opaque like an Agenda Reason Code: a surface that does not recognise one shows
#: it as-is rather than failing, and the vocabulary is free to grow.
NOTICE_CODES: Final = (NOTICE_WORKING, NOTICE_READY, NOTICE_FAILED, NOTICE_CLOSING)


@dataclass(frozen=True, slots=True)
class SurfaceNotice:
    """One out-of-band status message. Never part of the transcript."""

    code: str
    detail: str = ""
    session_id: str | None = None


@runtime_checkable
class PresentationSurface(Protocol):
    """The traveller-facing edge of the application."""

    @property
    def surface_id(self) -> str:
        """A stable identity for logs and telemetry, e.g. ``terminal`` or ``scripted``."""
        ...

    def show(self, act: AssistantAct) -> None:
        """Render one Assistant Act. Must not raise for any Act in the closed vocabulary."""
        ...

    def next_turn(self) -> UserTurn | None:
        """Return the next User Turn, or ``None`` when the traveller closed the surface."""
        ...

    def notify(self, notice: SurfaceNotice) -> None:
        """Report out-of-band status. Must not raise, and must never end the session."""
        ...

    def close(self) -> None:
        """Release whatever the surface holds. Called exactly once, and must be idempotent."""
        ...
