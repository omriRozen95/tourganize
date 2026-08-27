"""``FixedOrderPolicy`` — the ``PriorityPolicy`` fake, and a usable policy in its own right.

Two jobs, which is unusual for a fake and is why they are spelled out here.

**As a fake**, it is how a test says "plan them in *this* order" without inventing weights that
have nothing to do with what the test is about. ``build_agenda`` then has an ordering it can be
held to, and the Mentioned-First Rule can be checked against a policy that would happily
violate it if the rule lived anywhere but in ``build_agenda``.

**As a policy**, ``FixedOrderPolicy()`` — configured with nothing — keeps the order it is
handed, which is the order the Component Catalog declares its Kinds in. That is exactly what
``TOURGANIZE_PRIORITY_POLICY=fixed`` means: *plan them in the order the file lists them and
ignore the weights.* An operator who wants to re-order a conversation by moving two blocks of
YAML around, rather than by reasoning about numbers, has a policy that does that.

``verbatim`` exists for one purpose: to return an order that does **not** match the candidates,
so a test can prove ``build_agenda`` refuses a policy that invents or drops a ``kind_key``. A
seam that is never driven wrong is a seam nobody knows is there. It is off by default and no
production path sets it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final, final

from tourganize.domain.catalog import ComponentKind
from tourganize.domain.trip import TripPlan

__all__ = ["FIXED_POLICY_ID", "FixedOrderPolicy"]

#: What this policy answers ``policy_id`` with, and the value of
#: ``TOURGANIZE_PRIORITY_POLICY`` that selects it.
FIXED_POLICY_ID: Final = "fixed"


@final
class FixedOrderPolicy:
    """Order one Agenda band by an explicit list of ``kind_key``, then by what was handed in."""

    def __init__(self, kind_keys: Iterable[str] = (), *, verbatim: bool = False) -> None:
        self._kind_keys = tuple(kind_keys)
        self._verbatim = verbatim

    @property
    def policy_id(self) -> str:
        return FIXED_POLICY_ID

    @property
    def kind_keys(self) -> tuple[str, ...]:
        """The configured order, for ``doctor`` and for a test that wants to assert on it."""
        return self._kind_keys

    def order(self, candidates: Sequence[ComponentKind], plan: TripPlan) -> tuple[str, ...]:
        """Return the configured Kinds first, then every other candidate in the order given.

        Candidates the configured list does not name keep their relative order, and configured
        Kinds that are not candidates are dropped — this band may hold two Kinds out of five, or
        none of the configured ones at all, and either way the answer has to be a permutation of
        what was asked about.
        """
        del plan  # an explicit order is an explicit order; the plan cannot change it.
        if self._verbatim:
            return self._kind_keys
        given = [kind.kind_key for kind in candidates]
        named = [key for key in self._kind_keys if key in given]
        return (*named, *(key for key in given if key not in named))
