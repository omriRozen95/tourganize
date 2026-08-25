"""``PlanOption`` and ``OptionSlate`` — candidates, and the round they were offered in.

The rule that shapes both types: **no prose**. A Plan Option carries structured ``facts`` and
a price, never a ``title`` or a ``description``, because the traveller may be reading in
Hebrew and wording has to be composed per locale at presentation time. Anything a human is
meant to read is a message key or an LLM Composition call, and neither belongs in here.

An Option Slate is one *round* for one Plan Component. Rounds are numbered from zero and the
history is kept: refinement adds a round, it never overwrites one, so an exported plan can
still show what was turned down.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options.money import Money
from tourganize.domain.options.provenance import Provenance

__all__ = ["OptionSlate", "PlanOption"]


@dataclass(frozen=True, slots=True)
class PlanOption:
    """One candidate for one Plan Component: structured facts plus where they came from."""

    option_id: str
    kind_key: str
    facts: Mapping[str, object]
    price: Money | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _require_text(self.option_id, "PlanOption.option_id")
        _require_text(self.kind_key, "PlanOption.kind_key")
        if self.price is not None and type(self.price) is not Money:
            raise InvariantViolationError(
                f"PlanOption.price must be Money or None, got {self.price!r}"
            )
        if type(self.provenance) is not Provenance:
            raise InvariantViolationError(
                "PlanOption.provenance is required — an option nobody can trace back to a "
                f"source cannot be presented; got {self.provenance!r}"
            )
        # A read-only view, so a slate handed to the dialogue cannot be edited underneath it.
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))

    def fact(self, name: str) -> object | None:
        """Return one declared fact, or ``None`` when this option does not carry it."""
        return self.facts.get(name)


@dataclass(frozen=True, slots=True)
class OptionSlate:
    """The options offered for one Plan Component in one round of the choose-or-refine loop."""

    kind_key: str
    round_index: int
    options: tuple[PlanOption, ...] = ()
    requirements_digest: str = ""

    def __post_init__(self) -> None:
        _require_text(self.kind_key, "OptionSlate.kind_key")
        if type(self.round_index) is not int:
            raise InvariantViolationError(
                f"OptionSlate.round_index must be an integer, got {self.round_index!r}"
            )
        if self.round_index < 0:
            raise InvariantViolationError(
                f"OptionSlate.round_index must not be negative, got {self.round_index}"
            )
        foreign = [option.option_id for option in self.options if option.kind_key != self.kind_key]
        if foreign:
            raise InvariantViolationError(
                f"OptionSlate({self.kind_key!r}) was given options of another Component Kind: "
                f"{', '.join(foreign)}"
            )
        identifiers = [option.option_id for option in self.options]
        if len(set(identifiers)) != len(identifiers):
            raise InvariantViolationError(
                f"OptionSlate({self.kind_key!r}, round {self.round_index}) repeats an option_id"
            )

    def option(self, option_id: str) -> PlanOption | None:
        """Return the option with this id, or ``None`` when the slate does not hold it."""
        for option in self.options:
            if option.option_id == option_id:
                return option
        return None

    def contains(self, option_id: str) -> bool:
        return self.option(option_id) is not None

    def __len__(self) -> int:
        return len(self.options)


def _require_text(value: str, field_name: str) -> None:
    if type(value) is not str or not value.strip():
        raise InvariantViolationError(f"{field_name} must be a non-empty string, got {value!r}")
