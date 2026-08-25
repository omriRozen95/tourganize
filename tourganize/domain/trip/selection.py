"""``Selection`` — the Plan Option the traveller accepted, and the turn they accepted it on.

Its own module because both the Plan Component that holds one and the Trip Plan that records
one need the type, and neither should have to import the other to get it.
"""

from __future__ import annotations

from dataclasses import dataclass

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options import PlanOption

__all__ = ["Selection"]


@dataclass(frozen=True, slots=True)
class Selection:
    """One accepted Plan Option. The turn index is kept so a plan can explain itself."""

    kind_key: str
    option: PlanOption
    chosen_at_turn: int

    def __post_init__(self) -> None:
        if type(self.option) is not PlanOption:
            raise InvariantViolationError(
                f"Selection.option must be a PlanOption, got {self.option!r}"
            )
        if self.kind_key != self.option.kind_key:
            raise InvariantViolationError(
                f"Selection({self.kind_key!r}) holds an option of kind "
                f"{self.option.kind_key!r}"
            )
        if type(self.chosen_at_turn) is not int:
            raise InvariantViolationError(
                f"Selection.chosen_at_turn must be an integer, got {self.chosen_at_turn!r}"
            )
        if self.chosen_at_turn < 0:
            raise InvariantViolationError(
                f"Selection.chosen_at_turn must not be negative, got {self.chosen_at_turn}"
            )

    @property
    def option_id(self) -> str:
        return self.option.option_id
