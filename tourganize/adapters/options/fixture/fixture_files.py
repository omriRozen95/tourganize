"""Reading, validating and matching the JSON files a Fixture Provider serves.

The split is the one the catalog reader already makes: what a *valid* fixture file is, and how
one is matched against an Option Query, live here in the adapter; what a Plan Option is stays
in the domain. A file that is malformed raises
:class:`~tourganize.platform.errors.ConfigurationError` naming the file, because a fixture tree
nobody can read is a misconfigured installation, exactly as a missing schema is.

One file, one Component Kind, any number of options::

    {
      "kind_key": "alpha",
      "matchable": ["place", "date_range"],
      "match": {"place": ["Paris", "פריז"], "date_range": ["2026-01-01/2027-06-30"]},
      "options": [
        {"external_ref": "px-alpha-001",
         "facts": {"name": "Hôtel Saint-Germain", "review_score": 8.7, "nights": 5},
         "price": {"amount_minor": 74000, "currency": "EUR"}}
      ]
    }

``matchable`` names the requirement fields this file may be matched on; ``match`` supplies the
values, and a field listed in ``matchable`` with nothing in ``match`` is simply unconstrained.
The comparison is chosen by the *type of the traveller's value*, never by the field's name —
that is what keeps this reader free of any knowledge of travel:

* a Date Range matches when it **overlaps** one of the declared ranges,
* a date matches when it falls inside one,
* anything else matches when its text form equals a declared spelling, case- and
  accent-insensitively, so that ``paris`` and ``Paris`` are the same place and ``פריז`` is a
  second declared spelling rather than a second rule.

A requirement the query does not hold is never a mismatch. A traveller who has not said where
they are going has not ruled anything out.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

from tourganize.domain.errors import InvariantViolationError
from tourganize.domain.options import Money, PlanOption, Provenance
from tourganize.domain.options.query import OptionQuery
from tourganize.domain.requirements import DateRange
from tourganize.platform.errors import ConfigurationError

__all__ = ["FIXTURE_FILE_SUFFIX", "FixtureFile", "FixtureOption", "load_fixture_files"]

#: Fixture data is JSON, not the YAML subset the configuration uses. Option data is a recording
#: of what a provider returned, and providers speak JSON; D13's reader exists for files a human
#: edits by hand, which these are not.
FIXTURE_FILE_SUFFIX: Final = ".json"

_DOCUMENT_KEYS: Final = frozenset({"kind_key", "matchable", "match", "options"})
_OPTION_KEYS: Final = frozenset({"external_ref", "facts", "price"})
_PRICE_KEYS: Final = frozenset({"amount_minor", "currency"})


@dataclass(frozen=True, slots=True)
class FixtureOption:
    """One recorded candidate, before it is given Provenance and an ``option_id``."""

    external_ref: str
    facts: Mapping[str, object]
    price: Money | None

    def as_plan_option(self, kind_key: str, source_id: str, retrieved_at: datetime) -> PlanOption:
        """Turn the recording into a Plan Option of ``kind_key``.

        The ``option_id`` is ``<source_id>:<external_ref>``, which is stable across identical
        queries — the contract suite checks exactly that — and unique across sources, so two
        providers offering the same room cannot collide.
        """
        return PlanOption(
            option_id=f"{source_id}:{self.external_ref}",
            kind_key=kind_key,
            facts=dict(self.facts),
            price=self.price,
            provenance=Provenance(
                source_id=source_id,
                retrieved_at=retrieved_at,
                external_ref=self.external_ref,
            ),
        )


@dataclass(frozen=True, slots=True)
class FixtureFile:
    """One fixture file: the Component Kind it serves, what it matches on, and its options."""

    path: Path
    kind_key: str
    matchable: tuple[str, ...]
    match: Mapping[str, tuple[str, ...]]
    options: tuple[FixtureOption, ...]

    def matches(self, query: OptionQuery) -> bool:
        """Whether this file answers ``query``: every declared constraint it can check, met."""
        return all(
            _value_matches(query.value_of(field_name), spellings)
            for field_name, spellings in self.match.items()
        )


def load_fixture_files(directory: Path, kind_key: str) -> tuple[FixtureFile, ...]:
    """Read every fixture file under ``directory``, in file-name order.

    File-name order rather than directory order, because a slate has to be identical on two
    machines and a file system's own order is not.
    """
    if not directory.is_dir():
        return ()
    return tuple(
        _load_file(path, kind_key) for path in sorted(directory.glob(f"*{FIXTURE_FILE_SUFFIX}"))
    )


def _load_file(path: Path, kind_key: str) -> FixtureFile:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(
            f"the fixture file {path} could not be read: {exc.strerror or exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"invalid fixture file {path}: {exc}") from exc

    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"invalid fixture file {path}: the file must be an object with `kind_key` and "
            f"`options` keys"
        )
    unknown = sorted(str(key) for key in document if key not in _DOCUMENT_KEYS)
    if unknown:
        raise ConfigurationError(
            f"invalid fixture file {path}: unknown key(s) {', '.join(unknown)}; a fixture file "
            f"declares {', '.join(sorted(_DOCUMENT_KEYS))}"
        )
    declared = document.get("kind_key")
    if declared != kind_key:
        raise ConfigurationError(
            f"invalid fixture file {path}: it declares kind_key {declared!r}, but its directory "
            f"says {kind_key!r}"
        )
    matchable = _text_tuple(document, "matchable", path)
    match = _match_of(document, path, matchable)
    return FixtureFile(
        path=path,
        kind_key=kind_key,
        matchable=matchable,
        match=match,
        options=_options_of(document, path),
    )


def _match_of(
    document: Mapping[str, object], path: Path, matchable: Sequence[str]
) -> Mapping[str, tuple[str, ...]]:
    declared = document.get("match")
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise ConfigurationError(
            f"invalid fixture file {path}: `match` must be an object of field name to accepted "
            f"values, got {declared!r}"
        )
    found: dict[str, tuple[str, ...]] = {}
    for name, values in declared.items():
        field_name = str(name)
        if field_name not in matchable:
            raise ConfigurationError(
                f"invalid fixture file {path}: `match` constrains {field_name!r}, which "
                f"`matchable` does not list; it lists {', '.join(matchable) or 'nothing'}"
            )
        if isinstance(values, str) or not isinstance(values, Sequence):
            raise ConfigurationError(
                f"invalid fixture file {path}: match.{field_name} must be a list of accepted "
                f"values, got {values!r}"
            )
        found[field_name] = tuple(str(value) for value in values)
    return found


def _options_of(document: Mapping[str, object], path: Path) -> tuple[FixtureOption, ...]:
    declared = document.get("options")
    if isinstance(declared, str) or not isinstance(declared, Sequence):
        raise ConfigurationError(
            f"invalid fixture file {path}: `options` must be a list, got {declared!r}"
        )
    options = tuple(
        _option_of(entry, path, position) for position, entry in enumerate(declared, start=1)
    )
    refs = [option.external_ref for option in options]
    if len(set(refs)) != len(refs):
        raise ConfigurationError(
            f"invalid fixture file {path}: two options share an external_ref, so one of them "
            f"could never be presented"
        )
    return options


def _option_of(entry: object, path: Path, position: int) -> FixtureOption:
    where = f"invalid fixture file {path}: option {position}"
    if not isinstance(entry, Mapping):
        raise ConfigurationError(f"{where} is not an object ({entry!r})")
    unknown = sorted(str(key) for key in entry if key not in _OPTION_KEYS)
    if unknown:
        raise ConfigurationError(
            f"{where}: unknown key(s) {', '.join(unknown)}; an option declares "
            f"{', '.join(sorted(_OPTION_KEYS))}"
        )
    reference = entry.get("external_ref")
    if not isinstance(reference, str) or not reference.strip():
        raise ConfigurationError(f"{where}: external_ref must be text, got {reference!r}")
    facts = entry.get("facts", {})
    if not isinstance(facts, Mapping):
        raise ConfigurationError(f"{where}: facts must be an object, got {facts!r}")
    return FixtureOption(
        external_ref=reference,
        facts={str(name): value for name, value in facts.items()},
        price=_price_of(entry.get("price"), where),
    )


def _price_of(declared: object, where: str) -> Money | None:
    if declared is None:
        return None
    if not isinstance(declared, Mapping):
        raise ConfigurationError(
            f"{where}: price must be an object with amount_minor and currency, got {declared!r}"
        )
    unknown = sorted(str(key) for key in declared if key not in _PRICE_KEYS)
    if unknown:
        raise ConfigurationError(f"{where}: price has unknown key(s) {', '.join(unknown)}")
    try:
        return Money(
            amount_minor=_amount_of(declared.get("amount_minor"), where),
            currency=str(declared.get("currency")),
        )
    except InvariantViolationError as exc:
        # The domain owns what a price is; this adapter owns saying which file failed to be one.
        raise ConfigurationError(f"{where}: {exc}") from exc


def _amount_of(declared: object, where: str) -> int:
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise ConfigurationError(
            f"{where}: price.amount_minor must be a whole number of minor units, got {declared!r}"
        )
    return declared


def _text_tuple(document: Mapping[str, object], key: str, path: Path) -> tuple[str, ...]:
    value = document.get(key)
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ConfigurationError(f"invalid fixture file {path}: `{key}` must be a list")
    return tuple(str(item) for item in value)


def _value_matches(held: object, spellings: Sequence[str]) -> bool:
    """Whether the traveller's value for one field is one this file serves."""
    if held is None or not spellings:
        return True
    if isinstance(held, DateRange):
        return any(_overlaps(held, _range_of(spelling)) for spelling in spellings)
    # `datetime` is a `date`, and comparing the two raises. A `date` Field Kind normalises to a
    # plain date, so anything else falls through to the text comparison rather than blowing up.
    if isinstance(held, date) and not isinstance(held, datetime):
        return any(_covers(_range_of(spelling), held) for spelling in spellings)
    wanted = _folded(str(held))
    return any(_folded(spelling) == wanted for spelling in spellings)


def _range_of(spelling: str) -> DateRange | None:
    """Read ``2026-01-01/2027-06-30`` as a range, or answer ``None`` for anything else."""
    start, separator, end = spelling.partition("/")
    if not separator:
        return None
    try:
        return DateRange(date.fromisoformat(start.strip()), date.fromisoformat(end.strip()))
    except (ValueError, InvariantViolationError):
        return None


def _overlaps(wanted: DateRange, available: DateRange | None) -> bool:
    if available is None:
        return False
    return wanted.start <= available.end and available.start <= wanted.end


def _covers(available: DateRange | None, moment: date) -> bool:
    if available is None:
        return False
    return available.start <= moment <= available.end


def _folded(text: str) -> str:
    """Case- and accent-insensitive form, so ``Zürich`` and ``zurich`` are one place.

    Decomposing and dropping the combining marks is what makes that true without a table of
    every accented letter — and it leaves a Hebrew or Arabic spelling untouched, because those
    scripts carry no case and no combining accents to fold away.
    """
    decomposed = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))
