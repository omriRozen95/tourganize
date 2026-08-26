"""``PlanCompleteness`` — the derived answer to "are we done?".

Nothing sets a completeness; it is computed from the Plan Components every time it is asked
for, so it can never drift from the plan it describes. F05 consults it before summarising and
F13 renders it.

``is_closeable`` deliberately ignores unmentioned Component Kinds. A traveller who asked for a
hotel and nothing else has a closeable plan the moment the hotel is chosen — the assistant may
still *offer* flights, but it must not treat them as unfinished business. A component that
failed counts as open: the conversation can retry it or the traveller can decline it, and
either way the plan is not silently closed over a hole.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PlanCompleteness"]


@dataclass(frozen=True, slots=True)
class PlanCompleteness:
    """Which Component Kinds are settled, which are still open, and whether we may close."""

    selected: tuple[str, ...] = ()
    declined: tuple[str, ...] = ()
    open: tuple[str, ...] = ()
    open_mentioned: tuple[str, ...] = ()

    @property
    def is_closeable(self) -> bool:
        """True when no Component Kind the traveller mentioned is still open."""
        return not self.open_mentioned

    @property
    def is_empty(self) -> bool:
        """True for a plan with no components at all — nothing has been discussed yet."""
        return not (self.selected or self.declined or self.open)
