"""Requirement Values and the immutable Requirement Set they live in.

A Requirement Set is what the system knows about one Plan Component so far. It is **frozen**:
:meth:`RequirementSet.with_updates` returns a new set and the receiver is untouched, so a
turn that is later abandoned cannot have edited the plan on its way through, and a stored
session and a live one can share the same object safely.

Two rules do the interesting work.

**Precedence.** Values arrive from four places and they are not equal. A traveller's own words
overwrite anything; something the system inferred overwrites only a default or a value carried
over from an earlier session; a default never overwrites anything that was not also a default.
Within one rank the later turn wins, which is what makes "actually, make it the 24th" work —
and on the *same* turn the later update wins too, because two values for one field in one turn
are a correction in mid-sentence ("Paris — no, Lisbon"), and the last thing said is the thing
that was meant. :data:`PRECEDENCE` is the whole rule, as data.

**Nothing is dropped, and nothing is confused with anything else.** When two values contradict
each other, the one that stops being in force goes into :attr:`RequirementSet.superseded` as a
:class:`SupersededValue` that says *how* it lost: ``REPLACED`` for a value that was held and
was pushed out, ``OVERRULED`` for one that arrived and never took hold because the standing
value outranked it. Both are kept — "we heard you, but your earlier answer stands" is a thing
the dialogue may need to say — and they are kept apart, because a refinement explained from
values the traveller never actually held would be a lie. One history, in the order the
contradictions happened.

The schema is a parameter of :meth:`~RequirementSet.with_updates` rather than a field of the
set. A set is small, copied on every turn and persisted by F12; the schema is shared, loaded
from a file and versioned. The merge needs it because it **normalises** — what a value's
normalised form is, is a fact about its Field Spec and about nothing else — and, being there,
it also refuses a field the schema does not declare. Neither of those requires *this*
signature: a module-level ``merge(schema, set, updates)`` would do both. What this signature
buys is that a Requirement Set never holds a schema it would then have to be persisted with
— D14 in the decision log.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Final

from tourganize.domain.errors import (
    InvariantViolationError,
    RequirementValueError,
    UnknownFieldError,
)
from tourganize.domain.invariants import require_text
from tourganize.domain.options.money import Money
from tourganize.domain.requirements.schema import FieldSpec, RequirementSchema
from tourganize.domain.requirements.validation import DateRange, normalise

__all__ = [
    "PRECEDENCE",
    "RequirementSet",
    "RequirementSource",
    "RequirementUpdate",
    "RequirementValue",
    "SupersededValue",
    "Supersession",
]

#: How many hex characters :meth:`RequirementSet.digest` returns. Sixteen is a collision
#: probability nobody will meet in one conversation and a length that fits in a log line.
_DIGEST_LENGTH: Final = 16


class RequirementSource(Enum):
    """Where one Requirement Value came from."""

    USER = "user"
    INFERRED = "inferred"
    DEFAULT = "default"
    CARRIED_OVER = "carried_over"


#: The precedence order, highest first. ``CARRIED_OVER`` outranks ``DEFAULT`` because a value
#: the traveller gave in an earlier session was still, once, something they said; a default is
#: something nobody ever said.
PRECEDENCE: Final[Mapping[RequirementSource, int]] = MappingProxyType(
    {
        RequirementSource.USER: 3,
        RequirementSource.INFERRED: 2,
        RequirementSource.CARRIED_OVER: 1,
        RequirementSource.DEFAULT: 0,
    }
)


@dataclass(frozen=True, slots=True)
class RequirementValue:
    """One known value, and how it came to be known.

    ``field_name`` is held on the value itself, not only as the key it is filed under: the
    superseded history is a flat tuple, and a replaced value nobody can attribute to a field
    cannot explain anything.
    """

    field_name: str
    value: object
    source: RequirementSource
    turn_index: int
    confidence: float | None = None

    def __post_init__(self) -> None:
        require_text(self.field_name, "RequirementValue.field_name")
        if not isinstance(self.source, RequirementSource):
            raise InvariantViolationError(
                f"{self.field_name}: source must be a RequirementSource, got {self.source!r}"
            )
        if isinstance(self.turn_index, bool) or not isinstance(self.turn_index, int):
            raise InvariantViolationError(
                f"{self.field_name}: turn_index must be an integer, got {self.turn_index!r}"
            )
        if self.turn_index < 0:
            raise InvariantViolationError(
                f"{self.field_name}: turn_index must not be negative, got {self.turn_index}"
            )
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolationError(
                f"{self.field_name}: confidence must be between 0 and 1, got {self.confidence!r}"
            )

    @property
    def rank(self) -> int:
        """This value's place in :data:`PRECEDENCE`."""
        return PRECEDENCE[self.source]


class Supersession(Enum):
    """How a Requirement Value stopped being the one in force."""

    #: It was held for its field, and a value that outranked it took its place.
    REPLACED = "replaced"
    #: It arrived for a field that already held a value which outranked it, so it never took
    #: hold. Kept anyway: the traveller said it, and may need to be told why it did not win.
    OVERRULED = "overruled"


@dataclass(frozen=True, slots=True)
class SupersededValue:
    """One Requirement Value that is not in force, and why it is not.

    F05 explains a refinement from this history and F12 persists it, so the distinction is
    load-bearing rather than diagnostic: ``REPLACED`` is something the traveller once had,
    ``OVERRULED`` is something they asked for and did not get.
    """

    held: RequirementValue
    outcome: Supersession

    def __post_init__(self) -> None:
        if not isinstance(self.held, RequirementValue):
            raise InvariantViolationError(
                f"SupersededValue.held must be a RequirementValue, got {self.held!r}"
            )
        if not isinstance(self.outcome, Supersession):
            raise InvariantViolationError(
                f"{self.held.field_name}: outcome must be a Supersession, got {self.outcome!r}"
            )

    @property
    def field_name(self) -> str:
        """The field this value was offered for — what the history is filed by."""
        return self.held.field_name


@dataclass(frozen=True, slots=True)
class RequirementUpdate:
    """One value offered for one field, as a turn produces it.

    ``raw_text`` is the traveller's own words for this value where they are known. It is never
    stored in the Requirement Set — the *value* is what the domain reasons about — but it is
    what lets a re-ask quote what was actually said instead of paraphrasing it.
    """

    field_name: str
    value: object
    source: RequirementSource = RequirementSource.USER
    turn_index: int = 0
    confidence: float | None = None
    raw_text: str | None = None

    def __post_init__(self) -> None:
        require_text(self.field_name, "RequirementUpdate.field_name")


@dataclass(frozen=True, slots=True)
class RequirementSet:
    """Everything known about one Plan Component's requirements. Immutable."""

    component_kind: str
    values: Mapping[str, RequirementValue] = field(default_factory=dict)
    superseded: tuple[SupersededValue, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.component_kind, "RequirementSet.component_kind")
        if not isinstance(self.values, Mapping):
            raise InvariantViolationError(
                f"{self.component_kind}: values must be a mapping of field name to "
                f"RequirementValue, got {self.values!r}"
            )
        for name, held in self.values.items():
            if not isinstance(held, RequirementValue):
                raise InvariantViolationError(
                    f"{self.component_kind}.{name}: expected a RequirementValue, got {held!r}"
                )
            if held.field_name != name:
                raise InvariantViolationError(
                    f"{self.component_kind}: value filed under {name!r} names field "
                    f"{held.field_name!r}"
                )
        if type(self.superseded) is not tuple:
            raise InvariantViolationError(
                f"{self.component_kind}: superseded must be a tuple, got {self.superseded!r}"
            )
        for entry in self.superseded:
            if not isinstance(entry, SupersededValue):
                raise InvariantViolationError(
                    f"{self.component_kind}: superseded must hold SupersededValue entries, "
                    f"got {entry!r}"
                )
        # A read-only view, so a set handed to an Option Source cannot be edited underneath it.
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @classmethod
    def empty(cls, component_kind: str) -> RequirementSet:
        """A set that knows nothing yet — how every Plan Component starts."""
        return cls(component_kind)

    def __contains__(self, field_name: object) -> bool:
        return field_name in self.values

    def __len__(self) -> int:
        return len(self.values)

    def value_of(self, field_name: str) -> object | None:
        """The normalised value held for ``field_name``, or ``None`` when there is none."""
        held = self.values.get(field_name)
        return None if held is None else held.value

    def provenance_of(self, field_name: str) -> RequirementValue | None:
        """The whole Requirement Value — value, source, turn, confidence — or ``None``."""
        return self.values.get(field_name)

    def superseded_for(self, field_name: str) -> tuple[SupersededValue, ...]:
        """Every value this field has lost, oldest first, each saying how it lost."""
        return tuple(entry for entry in self.superseded if entry.field_name == field_name)

    def with_updates(
        self, updates: Sequence[RequirementUpdate], *, schema: RequirementSchema
    ) -> RequirementSet:
        """Return a **new** set with ``updates`` merged in. The receiver is never modified.

        Raises :class:`~tourganize.domain.errors.UnknownFieldError` for a field ``schema`` does
        not declare. An *invalid* value, by contrast, is stored as it arrived: gap analysis is
        what reports it, and a set that refused it would leave the dialogue nothing to re-ask
        about — the value would have vanished along with the knowledge that it was wrong.
        """
        if schema.component_kind != self.component_kind:
            raise InvariantViolationError(
                f"schema {schema.schema_key} describes {schema.component_kind!r}, not "
                f"{self.component_kind!r}"
            )
        merged = dict(self.values)
        superseded = list(self.superseded)
        for update in updates:
            spec = schema.field(update.field_name)
            if spec is None:
                declared = ", ".join(schema.field_names) or "no fields"
                raise UnknownFieldError(
                    update.field_name,
                    f"{self.component_kind}: schema {schema.schema_key} does not declare a "
                    f"field {update.field_name!r}; it declares {declared}",
                )
            candidate = _value_of(spec, update)
            standing = merged.get(spec.name)
            if standing is None:
                merged[spec.name] = candidate
            elif _outranks(candidate, standing):
                merged[spec.name] = candidate
                superseded.append(SupersededValue(standing, Supersession.REPLACED))
            else:
                # The incoming value lost. It is still recorded — a contradiction the traveller
                # will hear about again is better than one that silently disappeared — but as
                # OVERRULED, because it was never in force and must not read as a refinement.
                superseded.append(SupersededValue(candidate, Supersession.OVERRULED))
        return RequirementSet(self.component_kind, merged, tuple(superseded))

    def digest(self) -> str:
        """A stable fingerprint of *what was asked for*, for the slate's ``requirements_digest``.

        Only the Component Kind and the field values go in: not the source, not the turn, not
        the confidence. Two sets that ask for the same thing must digest the same, whichever
        route the values took, because F06 seeds a deterministic search with this — and a
        refinement that changes nothing should not change the slate.

        The material is JSON with sorted keys rather than ``name=value`` lines: a value may
        contain a newline or an ``=``, and with a separator that can appear inside a value,
        ``{"a": "b\\nc=d"}`` and ``{"a": "b", "c": "d"}`` hash to the same thing. Two different
        requirements sharing one digest would have F06 answer the second with the first one's
        slate, which is the one failure this fingerprint exists to prevent.
        """
        material = json.dumps(
            {
                "component_kind": self.component_kind,
                "values": {
                    name: _canonical(self.values[name].value) for name in sorted(self.values)
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]


def _value_of(spec: FieldSpec, update: RequirementUpdate) -> RequirementValue:
    return RequirementValue(
        field_name=spec.name,
        value=_normalised_or_raw(spec, update.value),
        source=update.source,
        turn_index=update.turn_index,
        confidence=update.confidence,
    )


def _normalised_or_raw(spec: FieldSpec, value: object) -> object:
    """Normalise where the value allows it, and keep it verbatim where it does not."""
    try:
        return normalise(spec, value)
    except RequirementValueError:
        return value


def _outranks(incoming: RequirementValue, standing: RequirementValue) -> bool:
    """Whether ``incoming`` replaces ``standing``. The whole merge precedence, in three lines.

    ``>=`` rather than ``>``: within one rank a later turn wins, and a value arriving on the
    *same* turn wins too. Two values for one field in one turn are a correction in mid-sentence
    — "Paris, no, Lisbon" — so the last update of a turn is the one that stands.
    """
    if incoming.rank != standing.rank:
        return incoming.rank > standing.rank
    return incoming.turn_index >= standing.turn_index


def _canonical(value: object) -> str:
    """Render one value so that the same requirement always hashes to the same digest.

    Written out per type rather than left to ``repr``: ``repr`` of a ``date`` is
    ``datetime.date(2026, 10, 23)``, which would tie the digest to a Python spelling and break
    the moment a value is round-tripped through a stored session (F12) as a string.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, DateRange):
        return str(value)
    if isinstance(value, date):  # datetime is a date; isoformat() is right for both
        return value.isoformat()
    if isinstance(value, Money):
        return f"{value.amount_minor} {value.currency}"
    if isinstance(value, timedelta):
        return f"{value.total_seconds():.0f}s"
    if isinstance(value, str):
        return value
    # Numbers, and anything a validator refused and stored verbatim. ``repr`` is stable for
    # both, and a value that never normalised has no canonical spelling to prefer over it.
    return repr(value)
