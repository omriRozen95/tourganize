"""The Planning Agenda as a value: its reads, and the arrangements it refuses.

Nothing here builds an agenda from a plan — that is ``build_agenda``'s job and
``test_prioritization.py``'s subject. This file is about the type that carries the answer, and
in particular about the fact that the Mentioned-First Rule is enforced by the *type*: an
interleaved sequence of entries cannot be made into a ``PlanningAgenda`` at all, so no future
policy or caller can produce one by mistake.
"""

from __future__ import annotations

import pytest

from tourganize.domain.catalog import (
    AWAITS_OUTCOME,
    FAILED_SKIPPED,
    NOT_PLANNABLE,
    READY,
    REASON_CODES,
    AgendaBand,
    AgendaEntry,
    PlanningAgenda,
)
from tourganize.domain.errors import InvariantViolationError

M = AgendaBand.MENTIONED
U = AgendaBand.UNMENTIONED


def entry(
    kind_key: str,
    band: AgendaBand = M,
    rank: int = 0,
    *,
    awaits: tuple[str, ...] = (),
    reason: str = READY,
) -> AgendaEntry:
    return AgendaEntry(
        kind_key=kind_key, band=band, rank=rank, blocked_by=awaits, reason_code=reason
    )


def test_an_entry_defaults_to_ready_and_unblocked() -> None:
    only = entry("alpha")

    assert only.blocked_by == ()
    assert only.reason_code == READY
    assert only.is_actionable


def test_an_entry_is_frozen() -> None:
    with pytest.raises(AttributeError):
        entry("alpha").rank = 3  # type: ignore[misc]


def test_every_reason_code_this_release_emits_is_a_usable_key() -> None:
    """They reach telemetry fields and message keys, so the spelling is held to one shape."""
    for code in REASON_CODES:
        assert entry("alpha", reason=code).reason_code == code


@pytest.mark.parametrize("reason", ["", "   ", "Ready", "not plannable", "awaits-outcome"])
def test_a_reason_code_must_be_lower_snake_case(reason: str) -> None:
    with pytest.raises(InvariantViolationError):
        entry("alpha", reason=reason)


def test_only_a_skipped_entry_is_unactionable() -> None:
    """The vocabulary may grow, so a code nobody recognises is still an entry to work on."""
    assert not entry("alpha", reason=FAILED_SKIPPED).is_actionable
    assert entry("alpha", reason=NOT_PLANNABLE).is_actionable
    assert entry("alpha", reason=AWAITS_OUTCOME).is_actionable
    assert entry("alpha", reason="invented_by_f11").is_actionable


@pytest.mark.parametrize("rank", [-1, 1.5, True])
def test_a_rank_must_be_a_whole_number_that_is_not_negative(rank: object) -> None:
    with pytest.raises(InvariantViolationError):
        AgendaEntry(kind_key="alpha", band=M, rank=rank)  # type: ignore[arg-type]


def test_an_entry_needs_a_real_band() -> None:
    with pytest.raises(InvariantViolationError):
        AgendaEntry(kind_key="alpha", band="mentioned", rank=0)  # type: ignore[arg-type]


def test_an_entry_may_not_await_its_own_outcome() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        entry("alpha", awaits=("alpha",))

    assert "own outcome" in str(raised.value)


def test_blocked_by_must_be_a_tuple_of_keys() -> None:
    with pytest.raises(InvariantViolationError):
        entry("alpha", awaits=["beta"])  # type: ignore[arg-type]
    with pytest.raises(InvariantViolationError):
        entry("alpha", awaits=("",))


def test_an_empty_agenda_is_a_valid_answer() -> None:
    """Everything is selected or declined: F05 reads this as "time to summarise"."""
    empty = PlanningAgenda()

    assert empty.entries == ()
    assert empty.next_actionable() is None
    assert empty.mentioned_open() == ()
    assert empty.unmentioned_open() == ()
    assert empty.is_mentioned_band_empty()
    assert empty.explain() == ()


def test_the_bands_are_reported_separately_in_agenda_order() -> None:
    agenda = PlanningAgenda((entry("alpha", M, 0), entry("beta", U, 0), entry("gamma", U, 1)))

    assert agenda.mentioned_open() == ("alpha",)
    assert agenda.unmentioned_open() == ("beta", "gamma")
    assert not agenda.is_mentioned_band_empty()


def test_the_mentioned_band_is_empty_when_only_unmentioned_kinds_are_left() -> None:
    agenda = PlanningAgenda((entry("beta", U, 0),))

    assert agenda.is_mentioned_band_empty()
    assert agenda.mentioned_open() == ()


def test_next_actionable_steps_over_a_skipped_entry_without_losing_it() -> None:
    agenda = PlanningAgenda(
        (entry("alpha", M, 0, reason=FAILED_SKIPPED), entry("beta", M, 1, reason=NOT_PLANNABLE))
    )

    actionable = agenda.next_actionable()

    assert actionable is not None
    assert actionable.kind_key == "beta"
    # Skipped, not dropped: the reason a Kind is being passed over stays visible.
    assert agenda.mentioned_open() == ("alpha", "beta")


def test_an_agenda_of_nothing_but_skipped_entries_has_nothing_actionable() -> None:
    agenda = PlanningAgenda((entry("alpha", M, 0, reason=FAILED_SKIPPED),))

    assert agenda.next_actionable() is None
    assert not agenda.is_mentioned_band_empty()


def test_explain_names_the_band_and_carries_the_reason_code() -> None:
    agenda = PlanningAgenda(
        (entry("alpha", M, 0), entry("beta", U, 0, awaits=("gamma",), reason=AWAITS_OUTCOME))
    )

    assert agenda.explain() == (
        ("alpha", "MENTIONED", 0, "ready"),
        ("beta", "UNMENTIONED", 0, "awaits_outcome"),
    )


def test_an_interleaved_agenda_cannot_be_constructed_at_all() -> None:
    """The Mentioned-First Rule, held by the type: there is no object to say it in."""
    with pytest.raises(InvariantViolationError) as raised:
        PlanningAgenda((entry("alpha", M, 0), entry("beta", U, 0), entry("gamma", M, 1)))

    assert "interleaved" in str(raised.value)


def test_the_unmentioned_band_may_not_come_first() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        PlanningAgenda((entry("beta", U, 0), entry("alpha", M, 0)))

    assert "mentioned Component Kinds are always planned first" in str(raised.value)


def test_ranks_count_from_zero_within_each_band() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        PlanningAgenda((entry("alpha", M, 0), entry("beta", M, 2)))

    assert "rank 2 is not 1" in str(raised.value)


def test_a_band_starts_at_rank_zero_however_many_entries_precede_it() -> None:
    agenda = PlanningAgenda((entry("alpha", M, 0), entry("beta", M, 1), entry("gamma", U, 0)))

    assert [item.rank for item in agenda.entries] == [0, 1, 0]


def test_one_component_kind_is_one_entry() -> None:
    with pytest.raises(InvariantViolationError) as raised:
        PlanningAgenda((entry("alpha", M, 0), entry("alpha", M, 1)))

    assert "twice" in str(raised.value)


def test_an_agenda_holds_agenda_entries() -> None:
    with pytest.raises(InvariantViolationError):
        PlanningAgenda(("alpha",))  # type: ignore[arg-type]
    with pytest.raises(InvariantViolationError):
        PlanningAgenda([entry("alpha")])  # type: ignore[arg-type]


def test_an_agenda_is_frozen() -> None:
    with pytest.raises(AttributeError):
        PlanningAgenda().entries = (entry("alpha"),)  # type: ignore[misc]
