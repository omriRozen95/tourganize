"""The Planning Agenda: which Plan Component is worked on now, and why that one.

One question is asked every turn — *what do we plan next?* — and this module is the answer's
shape. :func:`~tourganize.domain.catalog.prioritization.build_agenda` produces it; the
Dialogue Director (F05) reads it and never sorts anything itself.

Two bands, never interleaved. Component Kinds the traveller raised are the ``MENTIONED``
band, everything else the ``UNMENTIONED`` one, and every mentioned entry precedes every
unmentioned entry. That is the client's Mentioned-First Rule, and :class:`PlanningAgenda`
refuses any other arrangement at construction: the rule is enforced by the *type*, not only by
the function that happens to build one today.

``reason_code`` is an opaque code, never a sentence — the domain holds no prose, and F10
phrases whatever the traveller is eventually told. Four codes exist so far; the vocabulary is
free to grow, so a consumer must treat a code it does not recognise as "no reason it knows
about" rather than as an error. Only :data:`FAILED_SKIPPED` means "do not work on this", which
is why :attr:`AgendaEntry.is_actionable` names that one code instead of listing the others.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_key, require_text

__all__ = [
    "AWAITS_OUTCOME",
    "DEFAULT_AGENDA_FAILURE_SKIP",
    "FAILED_SKIPPED",
    "NOT_PLANNABLE",
    "READY",
    "REASON_CODES",
    "REASON_CODE_PATTERN",
    "AgendaBand",
    "AgendaEntry",
    "PlanningAgenda",
]

#: A reason code is lower snake case for the same reason a ``kind_key`` is: it ends up in
#: telemetry fields and message keys, and one shape is safe in all of them.
REASON_CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")

#: Nothing is known to be in the way: elicit or source this one now.
READY: Final = "ready"
#: An Outcome Dependency of this Kind is still open *in the same band*, so this entry ranks
#: after it. Ordering only — the entry stays actionable if the other one stalls.
AWAITS_OUTCOME: Final = "awaits_outcome"
#: The component still has blocking Gaps, so the dialogue elicits rather than sources.
NOT_PLANNABLE: Final = "not_plannable"
#: Sourcing has failed too often in a row, so the Agenda steps over it: one broken Component
#: Kind must not be able to deadlock the conversation. The only code that makes an entry
#: unactionable.
FAILED_SKIPPED: Final = "failed_skipped"

#: The codes this release emits, for tests and telemetry. Consumers never match against this
#: tuple — an unknown code is opaque, not invalid.
REASON_CODES: Final = (READY, AWAITS_OUTCOME, NOT_PLANNABLE, FAILED_SKIPPED)

#: How many consecutive sourcing failures a Component Kind gets before the Agenda skips it.
#: This is the documented default of ``TOURGANIZE_AGENDA_FAILURE_SKIP``, defined here so the
#: number has one home: ``Settings`` reads it rather than spelling a second 2 of its own.
DEFAULT_AGENDA_FAILURE_SKIP: Final = 2


class AgendaBand(Enum):
    """The two bands of the Agenda. Their *order* is the Mentioned-First Rule."""

    MENTIONED = "mentioned"
    UNMENTIONED = "unmentioned"


#: The bands in the only order an Agenda may present them.
_BAND_ORDER: Final = (AgendaBand.MENTIONED, AgendaBand.UNMENTIONED)


@dataclass(frozen=True, slots=True)
class AgendaEntry:
    """One Component Kind's place in the Agenda, and what is known about that place.

    ``rank`` is the position *within* the band, from zero, so an entry reads the same however
    many mentioned Kinds happen to precede it.
    """

    kind_key: str
    band: AgendaBand
    rank: int
    blocked_by: tuple[str, ...] = ()
    reason_code: str = READY

    def __post_init__(self) -> None:
        require_text(self.kind_key, "AgendaEntry.kind_key")
        if not isinstance(self.band, AgendaBand):
            raise InvariantViolationError(
                f"{self.kind_key}: band must be an AgendaBand, got {self.band!r}"
            )
        if type(self.rank) is not int or self.rank < 0:
            raise InvariantViolationError(
                f"{self.kind_key}: rank must be a whole number, got {self.rank!r}"
            )
        if type(self.blocked_by) is not tuple:
            raise InvariantViolationError(
                f"{self.kind_key}: blocked_by must be a tuple of kind_keys, got {self.blocked_by!r}"
            )
        for blocker in self.blocked_by:
            require_text(blocker, f"{self.kind_key}.blocked_by")
        if self.kind_key in self.blocked_by:
            raise InvariantViolationError(f"{self.kind_key}: a Kind cannot await its own outcome")
        # A reason code reaches telemetry fields and message keys, so it is held to the shape
        # a key must have. The *vocabulary* is open; the spelling is not.
        require_key(self.reason_code, f"{self.kind_key}.reason_code", REASON_CODE_PATTERN)

    @property
    def is_actionable(self) -> bool:
        """True when the dialogue may work on this entry now.

        :data:`FAILED_SKIPPED` is the one code that says no. Everything else — including a
        code a later feature adds — is actionable, because an entry whose reason the dialogue
        does not recognise is still an entry it was handed.
        """
        return self.reason_code != FAILED_SKIPPED


@dataclass(frozen=True, slots=True)
class PlanningAgenda:
    """The ordered queue of Component Kinds still to be planned.

    Recomputed every turn and never stored: it is a projection of a Trip Plan and a Component
    Catalog, and a stored copy would be one turn out of date. An **empty** agenda is a
    meaningful answer rather than a failure — everything is selected or declined, which is
    F05's cue to summarise.
    """

    entries: tuple[AgendaEntry, ...] = ()

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple:
            raise InvariantViolationError(f"entries must be a tuple, got {self.entries!r}")
        _require_entries(self.entries)
        _require_mentioned_first(self.entries)
        _require_dense_ranks(self.entries)

    def next_actionable(self) -> AgendaEntry | None:
        """The entry to work on now, or ``None`` when there is nothing left to work on.

        The first actionable entry in Agenda order, which is the Mentioned-First order: a
        mentioned Kind is reached before any unmentioned one, whatever their weights.
        """
        return next((entry for entry in self.entries if entry.is_actionable), None)

    def mentioned_open(self) -> tuple[str, ...]:
        """The Component Kinds the traveller raised that are still open, in Agenda order."""
        return self._band(AgendaBand.MENTIONED)

    def unmentioned_open(self) -> tuple[str, ...]:
        """The Component Kinds nobody raised that are still open, in Agenda order."""
        return self._band(AgendaBand.UNMENTIONED)

    def is_mentioned_band_empty(self) -> bool:
        """True when no Component Kind the traveller mentioned is still open.

        The gate for Proactive Offers (F05): the assistant suggests a Kind nobody asked for
        only once everything they *did* ask for is settled — selected or declined.
        """
        return not self.mentioned_open()

    def explain(self) -> tuple[tuple[str, str, int, str], ...]:
        """Return ``(kind_key, band, rank, reason_code)`` per entry, in Agenda order.

        For telemetry (F05) and assertions (F11). The traveller never sees any of it: these
        are codes, and nothing here is phrased.
        """
        return tuple(
            (entry.kind_key, entry.band.name, entry.rank, entry.reason_code)
            for entry in self.entries
        )

    def _band(self, band: AgendaBand) -> tuple[str, ...]:
        return tuple(entry.kind_key for entry in self.entries if entry.band is band)


def _require_entries(entries: tuple[AgendaEntry, ...]) -> None:
    """Every entry is an entry, and one Component Kind is one entry."""
    seen: set[str] = set()
    for entry in entries:
        if type(entry) is not AgendaEntry:
            raise InvariantViolationError(f"an agenda holds AgendaEntry, got {entry!r}")
        if entry.kind_key in seen:
            raise InvariantViolationError(
                f"{entry.kind_key} appears twice; one Component Kind is one Agenda entry"
            )
        seen.add(entry.kind_key)


def _require_mentioned_first(entries: tuple[AgendaEntry, ...]) -> None:
    """The Mentioned-First Rule, enforced by the type rather than by its builder.

    Each band is one contiguous run, and the mentioned run comes first. A Priority Policy that
    wanted to interleave them has no ``PlanningAgenda`` to say so in.
    """
    runs: list[AgendaBand] = []
    for entry in entries:
        if not runs or runs[-1] is not entry.band:
            runs.append(entry.band)
    if len(runs) != len(set(runs)):
        raise InvariantViolationError(
            "the bands are interleaved: "
            + " then ".join(band.name for band in runs)
            + "; mentioned Component Kinds are planned before unmentioned ones, as one block"
        )
    if runs != [band for band in _BAND_ORDER if band in runs]:
        raise InvariantViolationError(
            "the unmentioned band precedes the mentioned one; mentioned Component Kinds are "
            "always planned first"
        )


def _require_dense_ranks(entries: tuple[AgendaEntry, ...]) -> None:
    """Ranks count from zero within each band, with nothing skipped."""
    next_rank: dict[AgendaBand, int] = {}
    for entry in entries:
        expected = next_rank.get(entry.band, 0)
        if entry.rank != expected:
            raise InvariantViolationError(
                f"{entry.kind_key}: rank {entry.rank} is not {expected}, the next rank in band "
                f"{entry.band.name}"
            )
        next_rank[entry.band] = expected + 1
