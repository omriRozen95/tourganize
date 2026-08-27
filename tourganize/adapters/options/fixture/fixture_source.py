"""``FixtureOptionSource`` — the Option Source that serves recorded data from disk.

**One provider, driven by data, not one class per Component Kind.** Everything topic-specific
is a file under ``${TOURGANIZE_FIXTURE_DIR}/<kind_key>/``; this class knows about a directory,
a JSON shape and an ordering rule, and nothing about travel. That is the whole of what makes
"adding ``dining`` costs no Python" true for option data as well as for the catalog.

Two behaviours are worth stating, because both are contracts something else depends on.

**Deterministic, and seeded by what was asked for.** The same query returns the same options in
the same order, on any machine and in any process — the Golden Conversations (F11) cannot exist
otherwise. The order is *not* file order, though: it is a stable permutation seeded by
``requirements.digest()``, so that a refinement which changes the requirements visibly changes
which options come back, rather than returning the same three with a different sentence around
them. Two queries that ask for the same thing share a digest and therefore share an answer,
which is the property the digest was designed for (F03).

**A demonstration never dead-ends.** A query no recorded file matches is answered with a
deterministic *synthetic* set derived from the query, marked ``synthesised`` in the result's
diagnostics. It is not pretending: the diagnostic travels with the slate, ``doctor`` reports how
many real files were found, and the synthetic facts are deliberately anonymous — a variant
number and a price — because inventing plausible-looking travel data is how a fixture tree
starts lying to the people reading it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, final

from tourganize.adapters.options.fixture.fixture_files import (
    FIXTURE_FILE_SUFFIX,
    FixtureFile,
    FixtureOption,
    load_fixture_files,
)
from tourganize.domain.options import Money, PlanOption, Provenance
from tourganize.domain.options.query import NO_MATCH, SYNTHESISED, OptionQuery, OptionSourceResult
from tourganize.ports.platform import Clock

__all__ = ["FIXTURE_SOURCE_ID", "SYNTHETIC_CURRENCY", "FixtureOptionSource"]

#: What every option this provider serves names as its source.
FIXTURE_SOURCE_ID: Final = "fixture"

#: The currency the synthetic fallback prices in. One currency, because a synthetic set exists
#: to keep a demonstration moving, and a slate that mixes currencies for no reason is noise.
SYNTHETIC_CURRENCY: Final = "EUR"

_SYNTHETIC_BASE_MINOR: Final = 8_000
_SYNTHETIC_SPREAD_MINOR: Final = 24_000
_SYNTHETIC_STEP_MINOR: Final = 500


@final
class FixtureOptionSource:
    """Serves ``${root}/<kind_key>/*.json`` as Plan Options. The permanent test default (D9)."""

    def __init__(self, root: Path, clock: Clock, *, source_id: str = FIXTURE_SOURCE_ID) -> None:
        self._root = root
        self._clock = clock
        self._source_id = source_id
        # Read once and kept: a conversation must not see its options change underneath it
        # because someone edited a file mid-session, and a re-read per turn would allow it.
        self._files: dict[str, tuple[FixtureFile, ...]] = {}

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def root(self) -> Path:
        """Where this provider reads from — what ``doctor`` prints."""
        return self._root

    @property
    def kind_keys(self) -> frozenset[str]:
        """The Component Kinds with a directory of recorded data under :attr:`root`.

        Advisory, as the port says: a Kind with no directory is still answered, with a
        synthetic set. What this property is *for* is ``doctor`` and the contract suite —
        "here is what was actually recorded" — not a gate on what may be asked.
        """
        if not self._root.is_dir():
            return frozenset()
        return frozenset(
            path.name
            for path in self._root.iterdir()
            if path.is_dir() and any(path.glob(f"*{FIXTURE_FILE_SUFFIX}"))
        )

    def files_for(self, kind_key: str) -> tuple[FixtureFile, ...]:
        """Every fixture file recorded for ``kind_key``, loaded and validated once."""
        cached = self._files.get(kind_key)
        if cached is None:
            cached = load_fixture_files(self._root / kind_key, kind_key)
            self._files[kind_key] = cached
        return cached

    def search(self, query: OptionQuery) -> OptionSourceResult:
        """Return the recorded options matching ``query``, or a synthetic set derived from it."""
        retrieved_at = self._clock.now()
        recorded = self.files_for(query.kind_key)
        matched = [
            option for fixture in recorded if fixture.matches(query) for option in fixture.options
        ]
        if not matched:
            # Two different silences, told apart: nothing was *recorded* for this Component
            # Kind, or something was and none of it answers this query. Both are answered the
            # same way and neither is an error, but an operator reading the diagnostics needs
            # to know which of the two they are looking at.
            missing = (NO_MATCH,) if recorded else ()
            return OptionSourceResult(
                options=self._synthesised(query, retrieved_at),
                source_id=self._source_id,
                retrieved_at=retrieved_at,
                partial=True,
                diagnostics=(*missing, SYNTHESISED),
            )
        ordered = _seeded_order(matched, query.digest())
        chosen = ordered[: query.slate_size]
        return OptionSourceResult(
            options=tuple(
                option.as_plan_option(query.kind_key, self._source_id, retrieved_at)
                for option in chosen
            ),
            source_id=self._source_id,
            retrieved_at=retrieved_at,
            partial=len(chosen) < len(ordered),
        )

    def _synthesised(self, query: OptionQuery, retrieved_at: datetime) -> tuple[PlanOption, ...]:
        """A deterministic stand-in set, derived from the query and marked as such.

        The facts are deliberately anonymous — a variant number, and the fact that this option
        was synthesised — because a fixture provider that invents hotel names is a fixture
        provider whose output nobody can tell apart from a recording.
        """
        seed = _seed(f"{query.kind_key}\x00{query.digest()}")
        return tuple(
            PlanOption(
                option_id=f"{self._source_id}:synthetic:{query.kind_key}:{position}",
                kind_key=query.kind_key,
                facts={"variant": position, SYNTHESISED: True},
                price=Money(_synthetic_amount(seed, position), SYNTHETIC_CURRENCY),
                provenance=Provenance(
                    source_id=self._source_id,
                    retrieved_at=retrieved_at,
                    external_ref=f"synthetic:{query.kind_key}:{position}",
                ),
            )
            for position in range(1, query.slate_size + 1)
        )


def _seeded_order(options: Sequence[FixtureOption], digest: str) -> tuple[FixtureOption, ...]:
    """A stable permutation of ``options``, decided by ``digest`` and the options' own refs.

    Sorting by a hash of the two, rather than shuffling with a seeded generator, is what makes
    the order identical across Python versions: a random number generator's stream is an
    implementation detail nobody promised, and a hash is a promise.
    """
    return tuple(sorted(options, key=lambda option: _seed(f"{digest}\x00{option.external_ref}")))


def _seed(material: str) -> str:
    return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()


def _synthetic_amount(seed: str, position: int) -> int:
    """A price that is stable for one query and different between its options."""
    offset = int(seed[:8], 16) % _SYNTHETIC_SPREAD_MINOR
    rounded = (offset // _SYNTHETIC_STEP_MINOR) * _SYNTHETIC_STEP_MINOR
    return _SYNTHETIC_BASE_MINOR + rounded + (position - 1) * _SYNTHETIC_STEP_MINOR
