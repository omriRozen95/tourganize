"""``FixedSlatePlanner`` — the ``OptionSlatePlanner`` fake, and the whole of F05's sourcing.

It manufactures a slate of the size it was asked for, with option ids derived from the Component
Kind and the round, so that a test can assert on *which* option was chosen and *which* round it
came from without inventing fixture files. Every option carries Provenance naming this planner:
an option nobody can trace back to a source is not presentable, and a fake is a source.

The facts it declares are the two the dialogue itself can check — the round the option came from
and its position on the slate — and nothing else. It is not pretending to be a travel provider;
real option data arrives in F06 behind the ``OptionSource`` port, and this planner's whole job is
to let the state machine be driven with no I/O at all.

Two knobs, both for tests. ``slate_size`` is how many options a round holds, and ``fails_for``
names the Component Kinds this planner refuses to source — which is how the failure-containment
rule is exercised without a broken provider.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, final

from tourganize.domain.options import Money, OptionSlate, PlanOption, Provenance
from tourganize.domain.requirements import RequirementSet
from tourganize.domain.trip import TripPlan
from tourganize.platform.errors import PortUnavailableError
from tourganize.ports.platform import Clock

__all__ = ["FIXED_PLANNER_SOURCE_ID", "FixedSlatePlanner"]

#: What every option this planner produces names as its source.
FIXED_PLANNER_SOURCE_ID: Final = "fake:fixed_slate"

#: The price of the first option of every slate, in minor units, and the step between options.
#: Prices at all, because a slate with no numbers on it exercises nothing that a presentation
#: layer or a feasibility check will later have to read.
_BASE_PRICE_MINOR: Final = 10_000
_PRICE_STEP_MINOR: Final = 2_500
_CURRENCY: Final = "EUR"


@final
class FixedSlatePlanner:
    """Answers every request with a manufactured slate. Replaced by F06's planning service."""

    def __init__(
        self,
        clock: Clock,
        *,
        slate_size: int = 3,
        fails_for: Iterable[str] = (),
    ) -> None:
        self._clock = clock
        self._slate_size = slate_size
        self._fails_for = frozenset(fails_for)

    def plan(
        self,
        kind_key: str,
        requirements: RequirementSet,
        plan: TripPlan,
        round_index: int,
    ) -> OptionSlate:
        """Return ``slate_size`` manufactured options for ``kind_key``'s round ``round_index``."""
        del plan  # this planner reads no Selection: Outcome Dependencies are F06's to honour.
        if kind_key in self._fails_for:
            raise PortUnavailableError(
                f"{FIXED_PLANNER_SOURCE_ID} is configured to fail for {kind_key!r}"
            )
        retrieved_at = self._clock.now()
        return OptionSlate(
            kind_key=kind_key,
            round_index=round_index,
            options=tuple(
                PlanOption(
                    option_id=f"{kind_key}-r{round_index}-{position}",
                    kind_key=kind_key,
                    facts={"round_index": round_index, "position": position},
                    price=Money(_BASE_PRICE_MINOR + _PRICE_STEP_MINOR * (position - 1), _CURRENCY),
                    provenance=Provenance(
                        source_id=FIXED_PLANNER_SOURCE_ID, retrieved_at=retrieved_at
                    ),
                )
                for position in range(1, self._slate_size + 1)
            ),
            requirements_digest=requirements.digest(),
        )
