"""``PlanOption`` and ``OptionSlate`` — candidates, and the round they were offered in.

The rule that shapes both types: **no prose**. A Plan Option carries structured ``facts`` and
a price, never a ``title`` or a ``description``, because the traveller may be reading in
Hebrew and wording has to be composed per locale at presentation time. Anything a human is
meant to read is a message key or an LLM Composition call, and neither belongs in here.

``filter_notes`` is the one thing on a Plan Option that is not the provider's. It is the list
of *optional* requirement fields this option fails — a field name each, never a sentence — put
there by the Planning Service (F06) so that soft filtering is visible rather than silent: a
traveller who said "under €150" is shown the €160 room **marked**, not shown it as though they
had never spoken. It is a typed sibling of ``facts`` rather than a reserved key inside them,
because ``facts`` is what a source declared and this is what Tourganize concluded, and burying
one inside the other makes both harder to trust.

An Option Slate is one *round* for one Plan Component. Rounds are numbered from zero and the
history is kept: refinement adds a round, it never overwrites one, so an exported plan can
still show what was turned down. Its ``diagnostics`` are the opaque codes the round was
produced under — a source that failed, a set that had to be synthesised — kept on the slate
because "here are three options, and one provider was unreachable" is a different answer from
"here are three options", and only the slate can carry the difference to a surface.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_text
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
    filter_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.option_id, "PlanOption.option_id")
        require_text(self.kind_key, "PlanOption.kind_key")
        if type(self.filter_notes) is not tuple:
            raise InvariantViolationError(
                f"PlanOption.filter_notes must be a tuple of requirement field names, got "
                f"{self.filter_notes!r}"
            )
        for note in self.filter_notes:
            require_text(note, "PlanOption.filter_notes")
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

    @property
    def satisfies_every_filter(self) -> bool:
        """True when no optional filter demoted this option."""
        return not self.filter_notes

    def with_filter_notes(self, notes: Sequence[str]) -> PlanOption:
        """Return a copy of this option carrying ``notes``. The receiver is untouched.

        A method rather than ``dataclasses.replace`` at the call site, because the notes are
        the *only* thing anything outside a source is allowed to add to an option: everything
        else on it came from the provider and stays as it arrived.

        A bare string is refused rather than accepted as a sequence of characters. It is the
        one mistake this signature invites, and ``tuple("budget_ceiling")`` would produce
        fourteen notes that each look almost plausible in a log line.
        """
        if isinstance(notes, str):
            raise InvariantViolationError(
                f"PlanOption.with_filter_notes takes a sequence of field names, not one "
                f"string: got {notes!r}"
            )
        return replace(self, filter_notes=tuple(notes))


@dataclass(frozen=True, slots=True)
class OptionSlate:
    """The options offered for one Plan Component in one round of the choose-or-refine loop."""

    kind_key: str
    round_index: int
    options: tuple[PlanOption, ...] = ()
    requirements_digest: str = ""
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.kind_key, "OptionSlate.kind_key")
        if type(self.diagnostics) is not tuple:
            raise InvariantViolationError(
                f"OptionSlate.diagnostics must be a tuple of opaque codes, got {self.diagnostics!r}"
            )
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
