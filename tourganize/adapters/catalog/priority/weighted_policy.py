"""``WeightedCatalogPolicy`` — the shipped ``PriorityPolicy``: declared weights, declared order.

The client deferred their importance metric ("to be defined later"), so this policy is
deliberately the simplest thing that reads their intent out of *configuration* rather than out
of code (D3). Two declarations decide everything:

* ``priority_weight`` — higher is planned earlier. Editing a number in
  ``config/catalog/components.yaml`` re-orders the conversation, so "for a road trip, plan the
  place to sleep before the way of getting there" is a config change, not a code change.
* ``requires_outcome_of`` — an Outcome Dependency, applied as a **soft** constraint: a Kind
  ranks after the Kinds it reads *only* while those are open in the same band. The policy never
  sees another band, so it cannot express anything stronger than that even by mistake.

``build_agenda`` applies that second constraint to whatever *any* policy answers, and is the
authority on it (D16): this policy's own dependency-aware sort is a **preference**, not the
guarantee, and it is deliberately redundant. It is kept because a policy whose answer already
respects the declarations gives the Agenda nothing to adjust, so the shipped path does no
reordering at all — and because ``order`` is a port method that may be read on its own. If the
two ever disagreed, ``build_agenda`` would win and the Agenda would still be correct.

Ties break by the order the catalog declares its Kinds in, which is why the port promises that
order: two Kinds of equal weight must not swap places between one turn and the next, or the
Agenda would flicker and the traveller would be asked about a different thing each turn for no
stated reason.

The ``plan`` argument is deliberately unused. This policy is context-free — that is the cost D3
records, and the reason the port hands a plan to every policy is so that the *replacement* can
be context-sensitive without the seam changing.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Final, final

from tourganize.domain.catalog import ComponentKind, awaited_within
from tourganize.domain.trip import TripPlan
from tourganize.platform.logging import get_logger

__all__ = ["WEIGHTED_POLICY_ID", "WeightedCatalogPolicy"]

#: What this policy answers ``policy_id`` with, and the value of
#: ``TOURGANIZE_PRIORITY_POLICY`` that selects it.
WEIGHTED_POLICY_ID: Final = "weighted"


@final
class WeightedCatalogPolicy:
    """Order one Agenda band by declared weight, then by declared Outcome Dependencies."""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger if logger is not None else get_logger("prioritization")

    @property
    def policy_id(self) -> str:
        return WEIGHTED_POLICY_ID

    def order(self, candidates: Sequence[ComponentKind], plan: TripPlan) -> tuple[str, ...]:
        """Return every candidate's ``kind_key``, heaviest first, dependencies first of all."""
        del plan  # context-free by decision, not by omission: see the module docstring.
        preferred = sorted(candidates, key=lambda kind: -kind.priority_weight)
        return self._dependencies_first(preferred, declared=candidates)

    def _dependencies_first(
        self, preferred: Sequence[ComponentKind], declared: Sequence[ComponentKind]
    ) -> tuple[str, ...]:
        """Take the weight order and move each Kind after the ones it awaits.

        A selection sort rather than a general topological sort, and for a reason: at every
        step it takes the *heaviest* Kind whose in-band dependencies have already been placed,
        so the weight order is preserved everywhere the dependencies do not actually contradict
        it. Sorting topologically first and by weight second would let one declared dependency
        rewrite an order nobody asked to change.
        """
        open_in_band = frozenset(kind.kind_key for kind in preferred)
        awaited = {
            kind.kind_key: frozenset(awaited_within(kind, open_in_band)) for kind in preferred
        }
        remaining = [kind.kind_key for kind in preferred]
        ordered: list[str] = []
        placed: set[str] = set()
        warned = False
        while remaining:
            available = next((key for key in remaining if awaited[key] <= placed), None)
            if available is None:
                available = _earliest_declared(remaining, declared)
                if not warned:
                    warned = True
                    self._log_cycle(remaining)
            ordered.append(available)
            placed.add(available)
            remaining.remove(available)
        return tuple(ordered)

    def _log_cycle(self, deadlocked: Sequence[str]) -> None:
        """Report a dependency cycle once and carry on in declaration order.

        Unreachable through a loaded catalog: ``catalog_problems`` refuses a cycle and every
        adapter raises ``CatalogError`` before a Kind reaches this policy. It is still handled
        rather than left to loop for ever, because a policy that hangs the conversation over a
        bad edge is a worse failure than one that orders a cycle arbitrarily and says so.
        """
        self._logger.warning(
            "Outcome Dependency cycle among %s; ordering them by declaration order instead",
            ", ".join(deadlocked),
            extra={"kind": "prioritization"},
        )


def _earliest_declared(keys: Sequence[str], declared: Sequence[ComponentKind]) -> str:
    """The one of ``keys`` the catalog declares first — a deterministic tie-break for a cycle."""
    return next(kind.kind_key for kind in declared if kind.kind_key in keys)
