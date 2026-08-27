"""The Session Runner: greet, pump, show, close — and never decide anything else.

Both sides are fakes on purpose. The runner's whole job is the *shape* of a session, and a
test that wired a real Director and a real surface to check it would be testing them instead:
what has to be pinned here is that the greeting is shown before anything is asked, that every
Act reaches the surface, that either end of the conversation may close it, and that a failure
becomes an outcome rather than a traceback.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tourganize.adapters.clock.fake import FrozenClock
from tourganize.application.session_runner import SessionOutcome, run
from tourganize.dialogue import (
    CLOSE,
    DEFAULT_LOCALE,
    GREET,
    PRESENT_SLATE,
    AssistantAct,
    UserTurn,
)
from tourganize.domain.errors import SessionClosedError
from tourganize.ports.presentation import (
    NOTICE_CLOSING,
    NOTICE_FAILED,
    NOTICE_READY,
    NOTICE_WORKING,
    PresentationSurface,
    SurfaceNotice,
)

SESSION_ID = "session-under-test"


class _Session:
    """As much Planning Session as the runner is allowed to look at.

    Two scalars, which is the whole of it: an id to label the run with and the locale the
    farewell is said in. A fake that offered more would let a regression in the runner —
    reaching into the plan, or the Dialogue State — pass unnoticed.
    """

    def __init__(self, session_id: str = SESSION_ID, locale: str = DEFAULT_LOCALE) -> None:
        self.session_id = session_id
        self.locale = locale


class _FakeDirector:
    """Answers each turn with the Acts it was handed, in order, then repeats the last batch.

    Structural, not a subclass: the runner is typed against ``DialogueDirector`` but touches
    three members of it, and a fake that named the other forty would be pinning the Director's
    shape in a test about the runner.
    """

    def __init__(
        self,
        replies: Sequence[Sequence[AssistantAct]] = (),
        locale: str = DEFAULT_LOCALE,
    ) -> None:
        self.session = _Session(locale=locale)
        self.begun_with: list[str | None] = []
        self.handled: list[UserTurn] = []
        self._replies = [tuple(reply) for reply in replies]

    def begin(self, locale: str = "en") -> tuple[AssistantAct, ...]:
        self.begun_with.append(locale)
        return (AssistantAct(GREET),)

    def handle(self, turn: UserTurn) -> tuple[AssistantAct, ...]:
        self.handled.append(turn)
        index = min(len(self.handled) - 1, len(self._replies) - 1)
        return self._replies[index] if self._replies else ()


class _RaisingDirector(_FakeDirector):
    """Fails on the first turn, whatever it was asked."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    def handle(self, turn: UserTurn) -> tuple[AssistantAct, ...]:
        self.handled.append(turn)
        raise self._error


class _FakeSurface:
    """A list of lines in, everything the runner did to it recorded."""

    def __init__(self, script: Sequence[str] = (), clock: FrozenClock | None = None) -> None:
        self.shown: list[AssistantAct] = []
        self.notices: list[SurfaceNotice] = []
        self.closes = 0
        self._script = list(script)
        self._clock = clock if clock is not None else FrozenClock()
        self._index = 0

    @property
    def surface_id(self) -> str:
        return "fake"

    @property
    def notice_codes(self) -> tuple[str, ...]:
        return tuple(notice.code for notice in self.notices)

    def show(self, act: AssistantAct) -> None:
        self.shown.append(act)

    def next_turn(self) -> UserTurn | None:
        if self._index >= len(self._script):
            return None
        text = self._script[self._index]
        self._index += 1
        return UserTurn(index=self._index, text=text, received_at=self._clock.now())

    def notify(self, notice: SurfaceNotice) -> None:
        self.notices.append(notice)

    def close(self) -> None:
        self.closes += 1


class _RefusingSurface(_FakeSurface):
    """Fails on the two notices the runner sends *because* the session is already ending.

    Not on ``working`` and ``ready``: those are sent mid-conversation, where the port's "must
    not raise" holds and a breach of it is a broken surface that ends the session like any
    other failure. These two are different, and so is ``close`` — they are the goodbye, and a
    surface that cannot hear it must not become the reason the session is reported to have
    ended.
    """

    def notify(self, notice: SurfaceNotice) -> None:
        super().notify(notice)
        if notice.code in (NOTICE_FAILED, NOTICE_CLOSING):
            raise RuntimeError(f"cannot notify {notice.code}")

    def close(self) -> None:
        super().close()
        raise RuntimeError("cannot close")


def test_the_fake_surface_satisfies_the_port() -> None:
    """The fakes below are only worth what the port says they are."""
    assert isinstance(_FakeSurface(), PresentationSurface)


def test_the_greeting_is_shown_before_a_turn_is_ever_asked_for() -> None:
    director = _FakeDirector()
    surface = _FakeSurface()

    outcome = run(director, surface)

    # A greeting, then the traveller closed the surface without saying anything — so the
    # farewell is the second and last Act, and nothing was ever handed to the Director.
    assert [act.act for act in surface.shown] == [GREET, CLOSE]
    assert director.handled == []
    assert outcome == SessionOutcome(
        session_id=SESSION_ID, turns=0, acts=2, closed_by_director=False, error=None
    )
    assert outcome.ok


def test_every_turn_is_handled_and_every_act_is_shown() -> None:
    director = _FakeDirector([(AssistantAct(PRESENT_SLATE), AssistantAct(PRESENT_SLATE))])
    surface = _FakeSurface(["a hotel in Paris", "the second one"])

    outcome = run(director, surface)

    assert [turn.text for turn in director.handled] == ["a hotel in Paris", "the second one"]
    assert [act.act for act in surface.shown] == [GREET, *[PRESENT_SLATE] * 4, CLOSE]
    assert outcome.turns == 2
    assert outcome.acts == 6
    assert outcome.ok


def test_the_surface_answering_none_ends_the_loop_without_an_error() -> None:
    """A traveller who walks away ended the session the way a session is meant to end."""
    director = _FakeDirector()
    surface = _FakeSurface(["one turn, then the surface is done"])

    outcome = run(director, surface)

    assert outcome.turns == 1
    assert not outcome.closed_by_director
    assert outcome.ok


def test_a_close_act_ends_the_loop_and_no_further_turn_is_read() -> None:
    director = _FakeDirector([(AssistantAct(CLOSE),)])
    surface = _FakeSurface(["that's all", "this line is never read"])

    outcome = run(director, surface)

    assert [turn.text for turn in director.handled] == ["that's all"]
    assert outcome.closed_by_director
    assert outcome.turns == 1
    assert outcome.ok


def test_each_turn_is_bracketed_by_working_and_ready_and_the_session_by_closing() -> None:
    """The input line is disabled while the Director works, so no typed turn is lost."""
    director = _FakeDirector()
    surface = _FakeSurface(["one"])

    run(director, surface)

    assert surface.notice_codes == (NOTICE_WORKING, NOTICE_READY, NOTICE_CLOSING)
    assert {notice.session_id for notice in surface.notices} == {SESSION_ID}


def test_the_surface_is_closed_exactly_once() -> None:
    director = _FakeDirector()
    surface = _FakeSurface(["one", "two"])

    run(director, surface)

    assert surface.closes == 1


def test_a_raising_director_becomes_an_error_naming_the_session_and_still_closes() -> None:
    director = _RaisingDirector(ValueError("the planner fell over"))
    surface = _FakeSurface(["one"])

    outcome = run(director, surface)

    assert not outcome.ok
    assert outcome.error == "ValueError: the planner fell over"
    assert outcome.session_id == SESSION_ID
    assert surface.closes == 1
    assert surface.notice_codes == (NOTICE_WORKING, NOTICE_FAILED, NOTICE_CLOSING)
    assert surface.notices[1].detail == outcome.error


def test_a_surface_that_cannot_be_told_does_not_replace_the_error_it_could_not_be_told() -> None:
    director = _RaisingDirector(ValueError("the planner fell over"))
    surface = _RefusingSurface(["one"])

    outcome = run(director, surface)

    assert outcome.error == "ValueError: the planner fell over"
    assert surface.notice_codes == (NOTICE_WORKING, NOTICE_FAILED, NOTICE_CLOSING)
    assert surface.closes == 1


def test_a_session_closed_error_ends_the_loop_normally() -> None:
    """A turn that arrives one after the close is not a failure: reopening is F12's `resume`."""
    director = _RaisingDirector(SessionClosedError("session closed on turn 4"))
    surface = _FakeSurface(["one more, after the end"])

    outcome = run(director, surface)

    assert outcome.ok
    assert outcome.error is None
    assert outcome.closed_by_director
    assert surface.notice_codes == (NOTICE_WORKING, NOTICE_CLOSING)
    assert surface.closes == 1


@pytest.mark.parametrize("locale", [None, "he"])
def test_the_locale_is_the_runners_only_word_in_the_conversation(locale: str | None) -> None:
    """`--locale he` reaches the Director; nothing else about the dialogue does."""
    director = _FakeDirector()

    run(director, _FakeSurface(), locale=locale)

    assert director.begun_with == ["en" if locale is None else locale]


def test_a_surface_that_closes_first_is_said_goodbye_to_in_the_language_it_was_speaking() -> None:
    """Ctrl+C is a farewell, not a session that stops mid-screen.

    The Director is never asked: the traveller has left, so there is no turn to interpret and
    no state to move to. The runner shows ``close`` — the vocabulary's own word for what just
    happened, carrying no payload and no wording — in the locale the *session* had reached,
    which is not necessarily the one it was started in.
    """
    director = _FakeDirector(locale="he")
    surface = _FakeSurface()

    outcome = run(director, surface, locale="en")

    farewell = surface.shown[-1]
    assert farewell.act == CLOSE
    assert farewell.payload == {}
    assert farewell.locale == "he"
    assert outcome.closed_by_director is False
    assert outcome.ok


def test_a_director_that_closes_the_session_is_not_said_goodbye_to_twice() -> None:
    """One ``close`` per session. The runner's farewell is for the case nobody else covered."""
    director = _FakeDirector([(AssistantAct(CLOSE),)])
    surface = _FakeSurface(["that is all"])

    outcome = run(director, surface)

    assert [act.act for act in surface.shown] == [GREET, CLOSE]
    assert outcome.closed_by_director is True
    assert outcome.ok
