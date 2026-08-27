"""The ``PresentationSurface`` contract, run against every adapter of the port.

F25's web surface is the next adapter, and it will be finished when this suite passes over it
**unmodified** — a row in :data:`SURFACES` and nothing else. Nothing here asserts what a
particular surface *looks* like: a transcript pane, a numbered table and a status line are the
terminal's decisions, and a surface that drew every Act as a single line would still be a
correct surface. What it asserts is the handful of promises the Session Runner and the Dialogue
Director are written against:

* ``surface_id`` is non-empty and does not change while a session is running, because it is
  what a telemetry record is filed under;
* ``show`` survives **every** Act in the closed vocabulary — including one whose payload is
  empty, because an Act is a structured intent and not a template a surface may rely on;
* ``notify`` survives every notice code and never ends the session, so out-of-band status can
  never be the thing that breaks a conversation;
* ``next_turn`` answers ``None`` once the surface has closed, and keeps answering ``None``,
  because that is the *only* close signal the port has;
* ``close`` is idempotent, since the runner calls it in a ``finally`` that may already have
  been reached.

The surfaces do not all run the same way — the terminal one owns an event loop and has to be
handed the thread to run it on — so every check goes through :attr:`Harness.drive` rather than
being called directly. That is the one adapter-shaped thing in this file, and it is deliberate:
a port that could only be satisfied by an adapter with no event loop would not survive F25.

Each promise is a module-level ``check_*`` function rather than an assertion inside a test, for
the reason :mod:`tests.contracts.test_option_source_contract` gives: a suite that cannot fail is
worth nothing. The second half of this file **proves these checks bite** by running five
deliberately broken surfaces through the very same functions and asserting each one is
rejected. The suite stays green, because the broken surfaces are never parametrised into it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest
from conftest import write_catalog, write_messages, write_schemas

from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.presentation.scripted import ScriptedSurface
from tourganize.adapters.presentation.terminal import TerminalSurface
from tourganize.dialogue import (
    ACT_VOCABULARY,
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CLARIFY_CODES,
    CLOSE,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    REPORT_SOURCING_FAILURE,
    SOURCING_FAILED,
    AssistantAct,
    UserTurn,
)
from tourganize.language.act_renderer import ActRenderer
from tourganize.ports.presentation import NOTICE_CODES, PresentationSurface, SurfaceNotice

#: The Component Kind every Act in this suite is about. Neutral, like the sample catalog's:
#: a test about the *port* should not have to name a travel topic.
KIND: Final = "alpha"

#: The turns a scripted surface is built with. Two, because one is not enough to tell "the
#: script is finished" from "the surface never yielded anything".
SCRIPT: Final = ("first", "second")


@dataclass(frozen=True, slots=True)
class Harness:
    """One surface, plus whatever it takes to exercise it.

    ``drive`` runs a callable in whatever context that surface needs — straight through for a
    headless one, inside a running application for the terminal. Every check goes through it,
    so a surface that owns an event loop is testable without the suite knowing that it does.

    ``yields_turns`` is the one thing a headless test genuinely cannot arrange: a terminal
    blocks in ``next_turn`` until somebody types, and nobody is typing. The test that needs a
    turn skips with that reason rather than being quietly dropped from the suite.
    """

    surface: PresentationSurface
    drive: Callable[[Callable[[], None]], None]
    yields_turns: bool = True


SurfaceBuilder = Callable[[Path], Harness]


def renderer_for(root: Path) -> ActRenderer:
    """An Act Renderer over this test's own Message Catalogue and sample catalog."""
    config = root / "config"
    catalog = YamlComponentCatalog(write_catalog(config), write_schemas(config))
    return ActRenderer(
        write_messages(config), catalog, supported_locales=("en", "he"), default_locale="en"
    )


def an_option(option_id: str, *, filter_notes: tuple[str, ...] = ()) -> dict[str, object]:
    """One Plan Option as the Director puts it into a ``present_slate`` payload."""
    return {
        "option_id": option_id,
        "price": {"amount_minor": 74000, "currency": "EUR"},
        "facts": {"name": "Somewhere", "review_score": 8.4},
        "source_id": "fixture",
        "filter_notes": filter_notes,
    }


def acts() -> Iterator[AssistantAct]:
    """One Act of every name, then the same names again with nothing in them.

    The second pass is the interesting one. A payload is structured data the Director chose,
    not a template a surface may lean on, so a surface that indexed into one would break the
    first time an Act was emitted from a state nobody had drawn for. Rendering an empty
    payload produces markers, and markers are a perfectly good thing to show.
    """
    yield AssistantAct(GREET)
    yield AssistantAct(
        ASK_BLOCKING,
        {
            "rule_name": "when",
            "field_groups": (("date_range",),),
            "preferred_fields": ("date_range",),
            "prompt_message_keys": ("ask.alpha.date_range",),
            "schema_key": "alpha.v1",
        },
        kind_key=KIND,
    )
    yield AssistantAct(
        ASK_OPTIONAL,
        {
            "field_names": ("party_size", "budget_ceiling"),
            "prompt_message_keys": ("ask.alpha.party_size", "ask.alpha.budget_ceiling"),
        },
        kind_key=KIND,
    )
    yield AssistantAct(
        REPORT_INVALID_VALUE,
        {"field_name": "date_range", "reason_message_key": "requirement.invalid.not_a_date"},
        kind_key=KIND,
    )
    yield AssistantAct(
        PRESENT_SLATE,
        {
            "round_index": 0,
            "option_ids": ("alpha-1", "alpha-2"),
            "options": (an_option("alpha-1"), an_option("alpha-2", filter_notes=("budget",))),
            "requirements_digest": "0" * 16,
        },
        kind_key=KIND,
    )
    yield AssistantAct(
        CONFIRM_SELECTION,
        {"option_id": "alpha-1", "round_index": 0, "noted_kinds": ("beta",)},
        kind_key=KIND,
    )
    yield AssistantAct(
        OFFER_UNMENTIONED,
        {"kind_keys": ("beta",), "message_keys": ("component.beta",), "remaining": 1},
    )
    yield AssistantAct(
        DELIVER_SUMMARY,
        {
            "selected": (KIND,),
            "declined": ("beta",),
            "open": (),
            "open_mentioned": (),
            "selections": ({"kind_key": KIND, "option_id": "alpha-1", "round_index": 0},),
        },
    )
    for code in CLARIFY_CODES:
        yield AssistantAct(CLARIFY, {"reason_code": code, "given": "that one"})
    yield AssistantAct(
        REPORT_SOURCING_FAILURE,
        {"reason_code": SOURCING_FAILED, "round_index": 0, "consecutive_failures": 1},
        kind_key=KIND,
    )
    yield AssistantAct(CLOSE)
    for name in sorted(ACT_VOCABULARY):
        yield AssistantAct(name, locale="he", kind_key=KIND)


def notices() -> Iterator[SurfaceNotice]:
    """Every declared notice code, and one nobody declared.

    The vocabulary is opaque and free to grow (F05's convention for a Reason Code, applied to
    a notice), so a surface that only survived the four it was written against would break the
    first time somebody added a fifth.
    """
    for code in NOTICE_CODES:
        yield SurfaceNotice(code, detail=f"detail for {code}", session_id="contract")
    yield SurfaceNotice("a_code_from_the_future")


def _scripted(root: Path) -> Harness:
    surface = ScriptedSurface(SCRIPT, FrozenClock(DEFAULT_MOMENT), renderer_for(root), locale="en")
    return Harness(surface, lambda body: body())


def _scripted_unrendered(root: Path) -> Harness:
    """A Scripted Surface with no Act Renderer at all.

    Parametrised on purpose. F11's harness replays *structure*, so it may well run without a
    Message Catalogue, and "the surface still works with no renderer" is a promise of this
    adapter rather than an accident of how the runner happens to build it.
    """
    del root
    return Harness(ScriptedSurface(SCRIPT, FrozenClock(DEFAULT_MOMENT)), lambda body: body())


def _terminal(root: Path) -> Harness:
    """The real Terminal Surface, driven headlessly.

    ``headless=True`` is the same Textual application with the display and the keyboard taken
    away — the widgets are built, the CSS is parsed, every update is applied on the
    application's own thread and the conversation runs on the thread ``run_session`` spawns.
    It is what lets CI, which has no TTY at all, exercise this adapter rather than skip it.
    """
    surface = TerminalSurface(
        renderer_for(root),
        FrozenClock(DEFAULT_MOMENT),
        locale="en",
        session_id="contract",
        debug_status=True,
        headless=True,
    )
    return Harness(surface, surface.run_session, yields_turns=False)


#: Every adapter of the port, keyed by the name the test ids use. F25 appends its own.
SURFACES: dict[str, SurfaceBuilder] = {
    "ScriptedSurface": _scripted,
    "ScriptedSurface(unrendered)": _scripted_unrendered,
    "TerminalSurface(headless)": _terminal,
}


@pytest.fixture(params=sorted(SURFACES), ids=sorted(SURFACES))
def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    return SURFACES[request.param](tmp_path)


# -- the checks, as functions, so the second half of the file can prove they bite -------------


def check_identity_is_stable(surface: PresentationSurface) -> None:
    first, second = surface.surface_id, surface.surface_id
    assert first, "a surface with no id cannot be found in a telemetry record"
    assert first == second, f"surface_id changed between two reads: {first!r}, {second!r}"


def check_shows_every_act(surface: PresentationSurface) -> None:
    """Every Act in the closed vocabulary, and every Act with an empty payload."""
    shown = set()
    for act in acts():
        surface.show(act)
        shown.add(act.act)
    assert shown == set(ACT_VOCABULARY), f"the Act vocabulary was not covered: {shown}"


def check_accepts_every_notice(surface: PresentationSurface) -> None:
    for notice in notices():
        surface.notify(notice)


def check_closes_idempotently(surface: PresentationSurface) -> None:
    surface.close()
    surface.close()


def check_answers_none_once_closed(surface: PresentationSurface) -> None:
    """The close signal is the only one the port has, so it may not be a one-shot."""
    surface.close()
    assert surface.next_turn() is None, "a closed surface yielded a turn"
    assert surface.next_turn() is None, "a closed surface stopped answering None"


def check_everything(surface: PresentationSurface) -> None:
    """Every promise at once — what a broken surface is run through below."""
    check_identity_is_stable(surface)
    check_shows_every_act(surface)
    check_accepts_every_notice(surface)
    check_closes_idempotently(surface)
    check_answers_none_once_closed(surface)


# -- the contract ----------------------------------------------------------------------------


def test_the_adapter_satisfies_the_protocol(harness: Harness) -> None:
    assert isinstance(harness.surface, PresentationSurface)


def test_the_surface_has_a_stable_identity(harness: Harness) -> None:
    harness.drive(lambda: check_identity_is_stable(harness.surface))


def test_every_act_in_the_vocabulary_can_be_shown(harness: Harness) -> None:
    harness.drive(lambda: check_shows_every_act(harness.surface))


def test_every_notice_code_is_accepted(harness: Harness) -> None:
    """A notice is what the program is doing; it may never be why a conversation ended."""
    harness.drive(lambda: check_accepts_every_notice(harness.surface))


def test_a_turn_that_arrives_is_a_user_turn(harness: Harness) -> None:
    """The port promises a ``UserTurn`` or ``None`` and nothing else.

    Which turns arrive, and whether any do at all, is the surface's own business: a terminal
    nobody types into is as correct as a script with two lines in it. That is also why this is
    the one check a headless run has to skip rather than fake.
    """
    if not harness.yields_turns:
        pytest.skip("this surface blocks until somebody types, and nothing is typing")

    def body() -> None:
        turn = harness.surface.next_turn()
        assert isinstance(turn, UserTurn)
        assert turn.index >= 0
        assert turn.received_at.tzinfo is not None

    harness.drive(body)


def test_closing_twice_is_the_same_as_closing_once(harness: Harness) -> None:
    harness.drive(lambda: check_closes_idempotently(harness.surface))


def test_a_closed_surface_keeps_answering_none(harness: Harness) -> None:
    harness.drive(lambda: check_answers_none_once_closed(harness.surface))


def test_a_surface_satisfies_every_rule_at_once(harness: Harness) -> None:
    harness.drive(lambda: check_everything(harness.surface))


# -- the proof that the suite bites ----------------------------------------------------------


class _BrokenSurface:
    """A surface that breaks exactly one promise, chosen at construction.

    Deliberately **not** in :data:`SURFACES`: its whole purpose is to fail the checks, and a
    suite that parametrised it would be red for ever. The tests below run it through the same
    ``check_*`` functions the contract uses and assert each one rejects it — which is what
    makes the green half of this file mean something.
    """

    def __init__(self, fault: str) -> None:
        self._fault = fault
        self._closes = 0

    @property
    def surface_id(self) -> str:
        return "" if self._fault == "nameless" else "fake:broken"

    def show(self, act: AssistantAct) -> None:
        if self._fault == "cannot_show" and act.act == CLOSE:
            raise RuntimeError(f"I do not know how to draw {act.act}")

    def next_turn(self) -> UserTurn | None:
        if self._fault == "never_closes":
            return UserTurn(index=0, text="still here", received_at=DEFAULT_MOMENT)
        return None

    def notify(self, notice: SurfaceNotice) -> None:
        if self._fault == "cannot_notify":
            raise RuntimeError(f"I do not know the notice {notice.code}")

    def close(self) -> None:
        self._closes += 1
        if self._fault == "closes_once" and self._closes > 1:
            raise RuntimeError("already closed")


def test_the_suite_rejects_a_surface_with_no_identity() -> None:
    with pytest.raises(AssertionError, match="no id"):
        check_everything(_BrokenSurface("nameless"))


def test_the_suite_rejects_a_surface_that_cannot_draw_an_act() -> None:
    with pytest.raises(RuntimeError, match="do not know how to draw"):
        check_everything(_BrokenSurface("cannot_show"))


def test_the_suite_rejects_a_surface_that_raises_on_a_notice() -> None:
    with pytest.raises(RuntimeError, match="do not know the notice"):
        check_everything(_BrokenSurface("cannot_notify"))


def test_the_suite_rejects_a_surface_that_can_only_be_closed_once() -> None:
    with pytest.raises(RuntimeError, match="already closed"):
        check_everything(_BrokenSurface("closes_once"))


def test_the_suite_rejects_a_surface_that_yields_a_turn_after_closing() -> None:
    with pytest.raises(AssertionError, match="yielded a turn"):
        check_everything(_BrokenSurface("never_closes"))


def test_the_broken_surface_would_otherwise_look_like_a_real_adapter() -> None:
    """The counterexamples are only worth something because the shell around them is valid."""
    assert isinstance(_BrokenSurface("none"), PresentationSurface)
