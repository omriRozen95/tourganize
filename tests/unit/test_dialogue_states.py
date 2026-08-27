"""The Dialogue State machine: the guard refuses, and the table has no dead state.

The transition table is data, so what is worth testing is the *properties* of that data —
every state reachable, every resting state answerable, nothing walkable that is not declared —
rather than a list of edges a reader could equally well check by eye.
"""

from __future__ import annotations

import pytest

from tourganize.dialogue import (
    RESTING_STATES,
    TRANSITIONS,
    DialogueState,
    reachable_states,
    require_transition,
)
from tourganize.domain.errors import IllegalDialogueTransitionError, TourganizeError

S = DialogueState


def test_the_guard_refuses_an_undeclared_edge() -> None:
    with pytest.raises(IllegalDialogueTransitionError, match="CLOSED -> GREETING"):
        require_transition(S.CLOSED, S.GREETING)


def test_the_guard_returns_the_target_it_allowed() -> None:
    assert require_transition(S.GREETING, S.INTERPRETING) is S.INTERPRETING


def test_the_refusal_names_what_would_have_been_legal() -> None:
    with pytest.raises(IllegalDialogueTransitionError) as raised:
        require_transition(S.GREETING, S.SUMMARISING)

    assert "legal from GREETING: INTERPRETING" in str(raised.value)


def test_a_terminal_state_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(IllegalDialogueTransitionError, match="it is terminal"):
        require_transition(S.CLOSED, S.INTERPRETING)


def test_the_guard_refuses_something_that_is_not_a_state() -> None:
    with pytest.raises(IllegalDialogueTransitionError, match="not a DialogueState"):
        require_transition(S.GREETING, "sourcing")  # type: ignore[arg-type]


def test_a_dialogue_transition_error_is_a_tourganize_error() -> None:
    """A surface must be able to tell a modelled failure from a bug; both are ours."""
    assert issubclass(IllegalDialogueTransitionError, TourganizeError)


def test_every_state_is_reachable_from_the_greeting() -> None:
    """A state nothing can get to is a missing edge or a state that should not exist."""
    assert reachable_states() == frozenset(DialogueState)


def test_every_state_declares_its_outgoing_edges() -> None:
    assert set(TRANSITIONS) == set(DialogueState)


def test_only_the_closed_state_is_terminal() -> None:
    terminal = {state for state, targets in TRANSITIONS.items() if not targets}

    assert terminal == {S.CLOSED}


def test_every_edge_names_a_real_state() -> None:
    for state, targets in TRANSITIONS.items():
        for target in targets:
            assert target in TRANSITIONS, f"{state.name} -> {target!r}"


def test_the_table_is_read_only() -> None:
    """A feature that needs a new edge edits the table, where the machine is visible at once."""
    with pytest.raises(TypeError):
        TRANSITIONS[S.CLOSED] = frozenset({S.GREETING})  # type: ignore[index]


def test_every_resting_state_can_read_a_turn_or_is_closed() -> None:
    """A resting state is one a turn may arrive in, so it must be able to start interpreting."""
    for state in RESTING_STATES - {S.CLOSED}:
        assert S.INTERPRETING in TRANSITIONS[state], state.name


def test_interpreting_can_return_to_every_resting_state_it_may_be_entered_from() -> None:
    """A turn that changes nothing changes no state: the Director puts the session back."""
    for state in RESTING_STATES - {S.CLOSED}:
        assert state in TRANSITIONS[S.INTERPRETING], state.name


def test_closing_goes_through_the_summary() -> None:
    """`END_SESSION` may arrive from anywhere, but the session is never closed unsummarised."""
    into_closed = {state for state, targets in TRANSITIONS.items() if S.CLOSED in targets}

    assert into_closed == {S.SUMMARISING}


def test_refining_may_re_block_instead_of_sourcing() -> None:
    """A refinement that invalidates a value goes back to eliciting — the client's own rule."""
    assert TRANSITIONS[S.REFINING] == frozenset({S.SOURCING, S.ELICITING_BLOCKING})


def test_optional_elicitation_never_holds_anything_up() -> None:
    """One way out, and it is forward: optional filters are asked *alongside* a slate."""
    assert TRANSITIONS[S.ELICITING_OPTIONAL] == frozenset({S.AWAITING_CHOICE})
