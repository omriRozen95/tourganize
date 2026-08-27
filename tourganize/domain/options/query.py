"""``OptionQuery`` and ``OptionSourceResult`` — what an Option Source is asked, and what it answers.

Both live in the domain rather than beside the ``OptionSource`` protocol, for the reason
recorded for ``PriorityPolicy`` (D15) and for the two dialogue ports (D17): a port's contract
has to name the types it carries, and these two carry a
:class:`~tourganize.domain.requirements.values.RequirementSet`, a
:class:`~tourganize.domain.trip.selection.Selection` and a
:class:`~tourganize.domain.options.option.PlanOption`. :mod:`tourganize.ports.options` imports
and re-exports them, and *that* is the documented import path.

They are deliberately **not** re-exported from ``tourganize.domain.options``. A query names a
Selection, Selections live in ``tourganize.domain.trip``, and that package already imports
``tourganize.domain.options`` for the Option Slate a Trip Plan records — so re-exporting from
the package ``__init__`` would make the two packages import each other at import time, and the
order they happened to be loaded in would decide whether it worked. Importing this module by
path costs one longer import line and removes the question. It is the same move
``requirements/values.py`` already makes for ``Money``.

The two shapes divide the work between the Planning Service and a source. A **query** is one
Component Kind's whole wish: what is known about it, how many options are wanted, and the
Selections its Outcome Dependencies entitle it to read — never the Trip Plan, never the
Planning Session, never the Dialogue State. A **result** is one source's answer, with the
source's own identity on it and room to say that the answer is incomplete: ``partial`` when a
source knows it has less than it could have had, and ``diagnostics`` for the opaque codes a
caller records rather than reads.

Neither type carries prose, for the reason a Plan Option does not: a diagnostic is a code —
``synthesised``, ``no_match`` — and what a traveller is told about one is F07's and F10's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.invariants import require_aware, require_text
from tourganize.domain.options.option import PlanOption
from tourganize.domain.requirements.values import RequirementSet
from tourganize.domain.trip.selection import Selection

__all__ = [
    "DEFAULT_QUERY_LOCALE",
    "NO_MATCH",
    "SYNTHESISED",
    "OptionQuery",
    "OptionSourceResult",
]

#: The locale a query carries when nobody says otherwise. Spelled here rather than imported
#: from the dialogue's turn vocabulary, so that a query built by hand is still a valid one.
DEFAULT_QUERY_LOCALE: Final = "en"

#: The diagnostic a source sets when it had no recorded data for a query and answered with a
#: deterministic set derived from the query itself, so that a demonstration never dead-ends.
SYNTHESISED: Final = "synthesised"

#: The diagnostic a source sets when it holds data for this Component Kind but none of it
#: matched. It is *not* an error: an empty slate is an answer, and F05 already has an Act for it.
NO_MATCH: Final = "no_match"


@dataclass(frozen=True, slots=True)
class OptionQuery:
    """One Component Kind's request for candidates, as an Option Source receives it."""

    kind_key: str
    requirements: RequirementSet
    slate_size: int
    locale: str = DEFAULT_QUERY_LOCALE
    context_selections: Mapping[str, Selection] = field(default_factory=dict)
    request_id: str = ""

    def __post_init__(self) -> None:
        require_text(self.kind_key, "OptionQuery.kind_key")
        if not isinstance(self.requirements, RequirementSet):
            raise InvariantViolationError(
                f"OptionQuery.requirements must be a RequirementSet, got {self.requirements!r}"
            )
        if self.requirements.component_kind != self.kind_key:
            raise InvariantViolationError(
                f"OptionQuery({self.kind_key!r}) carries the requirements of "
                f"{self.requirements.component_kind!r}"
            )
        if type(self.slate_size) is not int or self.slate_size < 1:
            raise InvariantViolationError(
                f"OptionQuery.slate_size must be a whole number of options, one or more, got "
                f"{self.slate_size!r}"
            )
        require_text(self.locale, "OptionQuery.locale")
        for kind_key, selection in self.context_selections.items():
            if type(selection) is not Selection:
                raise InvariantViolationError(
                    f"OptionQuery.context_selections[{kind_key!r}] must be a Selection, got "
                    f"{selection!r}"
                )
        # A read-only view, so a source cannot edit the plan's Selections through the query.
        object.__setattr__(
            self, "context_selections", MappingProxyType(dict(self.context_selections))
        )

    def digest(self) -> str:
        """The fingerprint of *what was asked for*, which is the Requirement Set's.

        Read through the query as well as through the set because it is what seeds a
        deterministic source: identical requirements must produce identical options in an
        identical order, and a refinement must visibly produce different ones.
        """
        return self.requirements.digest()

    def value_of(self, field_name: str) -> object | None:
        """The normalised value the traveller's requirements hold for ``field_name``."""
        return self.requirements.value_of(field_name)

    def selection_of(self, kind_key: str) -> Selection | None:
        """The Selection this query is entitled to read for ``kind_key``, or ``None``."""
        return self.context_selections.get(kind_key)


@dataclass(frozen=True, slots=True)
class OptionSourceResult:
    """One Option Source's answer to one Option Query."""

    options: tuple[PlanOption, ...]
    source_id: str
    retrieved_at: datetime
    partial: bool = False
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.options) is not tuple:
            raise InvariantViolationError(
                f"OptionSourceResult.options must be a tuple of PlanOption, got {self.options!r}"
            )
        for option in self.options:
            if type(option) is not PlanOption:
                raise InvariantViolationError(
                    f"OptionSourceResult({self.source_id!r}) holds {option!r}, not a PlanOption"
                )
        require_text(self.source_id, "OptionSourceResult.source_id")
        require_aware(self.retrieved_at, "OptionSourceResult.retrieved_at")
        if type(self.partial) is not bool:
            raise InvariantViolationError(
                f"OptionSourceResult.partial must be a boolean, got {self.partial!r}"
            )
        if type(self.diagnostics) is not tuple:
            raise InvariantViolationError(
                f"OptionSourceResult.diagnostics must be a tuple of opaque codes, got "
                f"{self.diagnostics!r}"
            )
        for code in self.diagnostics:
            require_text(code, "OptionSourceResult.diagnostics")

    def __len__(self) -> int:
        return len(self.options)
