"""The Planning Service: the real ``OptionSlatePlanner``, over the ``OptionSource`` port.

F05 gave the Dialogue Director a seam — "produce one Option Slate for one Plan Component in one
round" — and a fake behind it. This is what goes behind it for real, and it is the only place
in the application that knows sourcing is a multi-step business:

1. build one :class:`~tourganize.domain.options.query.OptionQuery` — the Requirement Set, the
   slate size, the Selections this Component Kind's Outcome Dependencies entitle it to read;
2. call the sources the registry names, **serially** and each within a time budget;
3. merge what came back and de-duplicate by ``(source_id, external_ref)``;
4. apply the optional filters the Requirement Schema declares — *softly*, annotating rather than
   discarding, unless ``TOURGANIZE_OPTION_FILTER_STRICT`` says otherwise;
5. rank with the replaceable ``OptionRanking`` and truncate to the slate size;
6. record one telemetry event, and answer with the slate.

Three properties are load-bearing.

**Serial, always.** D5 forbids assuming parallel fan-out anywhere in the design — the Claude
Code backend is one process per call and a subscription has rate limits — and a design that
quietly relies on concurrency in one place is a design that cannot be moved onto a serial
transport later. The port promises nothing either way, so a future source that *permits*
concurrency can have it; nothing above this method may assume it.

**An empty slate is an answer, not an exception.** Zero options after merging returns an empty
:class:`~tourganize.domain.options.option.OptionSlate` carrying diagnostics, and F05 already
turns that into a ``report_sourcing_failure`` Act. Only *every* source for a Kind failing raises
:class:`~tourganize.platform.errors.OptionSourcingError`, because at that point there is nothing
to report about — and even that, the Director converts into an Act rather than the end of a
conversation.

**Deterministic.** The same requirements produce the same slate, byte for byte: the query's
``request_id`` is derived from the digest rather than generated, sources are seeded by the
digest, and the ranking's last tie-break is the ``option_id``. The Golden Conversations (F11)
depend on it, and so does anyone trying to reproduce a bug report.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Final, final

from tourganize.dialogue import DEFAULT_LOCALE
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.domain.options import OptionSlate, PlanOption
from tourganize.domain.options.filters import OptionFilter, filter_notes_for, filters_of
from tourganize.domain.options.query import (
    SYNTHESISED,
    OptionQuery,
    OptionSourceResult,
)
from tourganize.domain.requirements import RequirementSet
from tourganize.domain.trip import Selection, TripPlan
from tourganize.platform.errors import ContractViolationError, OptionSourcingError
from tourganize.ports.catalog import ComponentCatalog
from tourganize.ports.options import OptionRanking, OptionSource, OptionSourceRegistry
from tourganize.ports.platform import Clock, TelemetryEvent, TelemetrySink

__all__ = ["SOURCING_EVENT_KIND", "PlanningService"]

_LOGGER: Final = logging.getLogger(__name__)

#: The ``kind`` of the telemetry event recorded once per sourcing call.
SOURCING_EVENT_KIND: Final = "option_sourcing"

#: How many candidates the service asks each source for, per option it will present.
#:
#: A source is asked for more than the slate holds so that filtering and ranking have something
#: to choose *between*: with a factor of one, a slate would be whichever options the first
#: source happened to pick, and "cheapest first" would be a sort of three arbitrary rows.
#: Four is small enough that a live provider is not asked to do real work nobody sees, and large
#: enough that a three-option slate is drawn from a dozen candidates. It is a constant rather
#: than a setting because it tunes nothing a client would want to tune: the number they care
#: about is ``TOURGANIZE_SLATE_SIZE``, and this one only says how hard to look.
CANDIDATE_FACTOR: Final = 4

#: The diagnostic recorded for a source that raised, prefixed to its ``source_id``.
SOURCE_FAILED: Final = "source_failed"

#: The diagnostic recorded for a source that answered, but too late to be used.
SOURCE_TIMED_OUT: Final = "source_timed_out"

#: The diagnostic recorded when strict filtering removed every option that was found.
FILTERED_OUT: Final = "filtered_out"

#: The diagnostic recorded when the sources agreed on an empty answer without failing.
NOTHING_FOUND: Final = "nothing_found"


@final
class PlanningService:
    """Assembles one Option Slate from the Option Sources registered for a Component Kind."""

    def __init__(
        self,
        catalog: ComponentCatalog,
        registry: OptionSourceRegistry,
        ranking: OptionRanking,
        clock: Clock,
        telemetry: TelemetrySink,
        *,
        slate_size: int,
        filter_strict: bool = False,
        timeout_seconds: float = 10.0,
        locale: str = DEFAULT_LOCALE,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._ranking = ranking
        self._clock = clock
        self._telemetry = telemetry
        self._slate_size = slate_size
        self._filter_strict = filter_strict
        self._timeout_seconds = timeout_seconds
        self._locale = locale

    def plan(
        self,
        kind_key: str,
        requirements: RequirementSet,
        plan: TripPlan,
        round_index: int,
    ) -> OptionSlate:
        """Return the Option Slate for ``kind_key``'s round ``round_index``."""
        started_at = self._clock.now()
        query = self.query_for(kind_key, requirements, plan)
        results, diagnostics, failures = self._search(query)
        merged = _merged(results)
        filtered, filter_diagnostics = self._filtered(merged, kind_key, requirements)
        ordered = self._ranked(filtered, query)
        options = tuple(ordered[: self._slate_size])
        if not options and not diagnostics and not filter_diagnostics:
            diagnostics = (*diagnostics, NOTHING_FOUND)
        slate = OptionSlate(
            kind_key=kind_key,
            round_index=round_index,
            options=options,
            requirements_digest=query.digest(),
            diagnostics=(*diagnostics, *filter_diagnostics),
        )
        self._record(query, slate, results, failures, started_at)
        return slate

    def query_for(self, kind_key: str, requirements: RequirementSet, plan: TripPlan) -> OptionQuery:
        """Build the Option Query one round asks its sources.

        The query's ``slate_size`` is the **candidate** count — ``TOURGANIZE_SLATE_SIZE`` times
        :data:`CANDIDATE_FACTOR` — not the number of options the traveller will see. A source is
        asked for a pool and the slate is the top of it after filtering and ranking; a source
        asked for exactly three would be choosing the slate itself, and every replaceable piece
        below this line would be decoration.

        ``request_id`` is derived rather than generated: two identical rounds must produce
        identical slates, and a random id would put a different string into the telemetry of
        every replay of the same conversation.
        """
        return OptionQuery(
            kind_key=kind_key,
            requirements=requirements,
            slate_size=self._slate_size * CANDIDATE_FACTOR,
            locale=self._locale,
            context_selections=self._context_selections(kind_key, plan),
            request_id=f"{kind_key}:{requirements.digest()}",
        )

    # -- the steps ---------------------------------------------------------------------------

    def _context_selections(self, kind_key: str, plan: TripPlan) -> dict[str, Selection]:
        """The Selections this Component Kind's Outcome Dependencies entitle it to read.

        Its *declared* dependencies and no others: a source is handed the chosen flight because
        the dates of a stay follow it, and nothing else about the trip. A Kind the catalog
        does not declare reads nothing, which is the honest answer rather than an error — the
        Component Kind a planner is asked about is its caller's business, not its own.
        """
        try:
            declared = self._catalog.kind(kind_key).requires_outcome_of
        except UnknownComponentKindError:
            return {}
        found: dict[str, Selection] = {}
        for awaited in declared:
            if not plan.has_component(awaited):
                continue
            selection = plan.component(awaited).selection
            if selection is not None:
                found[awaited] = selection
        return found

    def _search(
        self, query: OptionQuery
    ) -> tuple[tuple[OptionSourceResult, ...], tuple[str, ...], int]:
        """Call every source for this Kind, serially, and collect what survives.

        A source that raises or overruns its budget is logged, recorded as a diagnostic and
        skipped. Only when every one of them fails is there nothing left to answer with, and
        that is the one case that raises.
        """
        sources = self._registry.sources_for(query.kind_key)
        results: list[OptionSourceResult] = []
        diagnostics: list[str] = []
        failures = 0
        for source in sources:
            outcome = self._search_one(source, query)
            if outcome is None:
                failures += 1
                diagnostics.append(f"{SOURCE_FAILED}:{source.source_id}")
                continue
            result, overran = outcome
            if overran:
                failures += 1
                diagnostics.append(f"{SOURCE_TIMED_OUT}:{source.source_id}")
                continue
            results.append(result)
            diagnostics += [f"{code}:{result.source_id}" for code in result.diagnostics]
        if sources and failures == len(sources):
            raise OptionSourcingError(
                f"every Option Source for {query.kind_key!r} failed: "
                f"{', '.join(source.source_id for source in sources)}"
            )
        return tuple(results), tuple(diagnostics), failures

    def _search_one(
        self, source: OptionSource, query: OptionQuery
    ) -> tuple[OptionSourceResult, bool] | None:
        """One source's answer and whether it overran, or ``None`` when it raised.

        The budget is enforced *after* the fact rather than by cancelling the call. Cancelling a
        synchronous call needs a thread per source, which buys nothing for a provider that reads
        a file and would make the frozen-clock replay of a recorded conversation depend on real
        elapsed time. A source that talks to a network is expected to hold its own transport to
        the same budget — F17 and F24 both pass it to their client — and this check is the
        backstop that keeps a source which ignores it from holding a conversation open.
        """
        started_at = self._clock.now()
        try:
            result = source.search(query)
        except Exception as exc:  # a source may raise anything at all; none of it ends a turn
            _LOGGER.warning(
                "option source %s failed for %s: %s: %s",
                source.source_id,
                query.kind_key,
                type(exc).__name__,
                exc,
                extra={"kind": "sourcing"},
            )
            return None
        self._require_result(source, result, query)
        elapsed = (self._clock.now() - started_at).total_seconds()
        if elapsed > self._timeout_seconds:
            _LOGGER.warning(
                "option source %s took %.3fs for %s, over its %.3fs budget: skipping it",
                source.source_id,
                elapsed,
                query.kind_key,
                self._timeout_seconds,
                extra={"kind": "sourcing"},
            )
            return result, True
        return result, False

    def _require_result(self, source: OptionSource, result: object, query: OptionQuery) -> None:
        """Check a source's answer at the seam. It is replaceable, so it is not trusted."""
        name = type(source).__name__
        if type(result) is not OptionSourceResult:
            raise ContractViolationError(
                f"OptionSource {name!r} must return an OptionSourceResult, got {result!r}"
            )
        foreign = [
            option.option_id for option in result.options if option.kind_key != query.kind_key
        ]
        if foreign:
            raise ContractViolationError(
                f"OptionSource {name!r} was asked about {query.kind_key!r} and answered with "
                f"options of another Component Kind: {', '.join(foreign)}"
            )
        if len(result.options) > query.slate_size:
            raise ContractViolationError(
                f"OptionSource {name!r} was asked for at most {query.slate_size} options and "
                f"returned {len(result.options)}"
            )

    def _filtered(
        self, options: Sequence[PlanOption], kind_key: str, requirements: RequirementSet
    ) -> tuple[tuple[PlanOption, ...], tuple[str, ...]]:
        """Annotate every option with the optional filters it fails, and demote or discard.

        Soft by default and by argument: a traveller who says "under €150" is shown the €160
        room *marked*, because an empty slate answers nothing and a filter they mentioned once
        should not silently become a rule. ``TOURGANIZE_OPTION_FILTER_STRICT`` is for the
        installation that would rather see nothing than see an option it asked to exclude.
        """
        filters = self._filters_for(kind_key, requirements)
        if not filters:
            return tuple(options), ()
        noted = [option.with_filter_notes(filter_notes_for(option, filters)) for option in options]
        if not self._filter_strict:
            return tuple(noted), ()
        kept = tuple(option for option in noted if option.satisfies_every_filter)
        if noted and not kept:
            return kept, (FILTERED_OUT,)
        return kept, ()

    def _filters_for(self, kind_key: str, requirements: RequirementSet) -> tuple[OptionFilter, ...]:
        """The declared filters this Component Kind's schema and the traveller between them make."""
        try:
            schema = self._catalog.schema_for(kind_key)
        except UnknownComponentKindError:
            # A Kind the catalog does not declare has no declared filters, so nothing is
            # demoted. Sourcing still answers: what may be planned is the catalog's question,
            # and it was already asked before anything reached this service.
            return ()
        return filters_of(schema, requirements)

    def _ranked(self, options: Sequence[PlanOption], query: OptionQuery) -> list[PlanOption]:
        """Apply the replaceable ranking, and refuse an answer that is not a reordering."""
        ordered = list(self._ranking.order(options, query))
        before = sorted(option.option_id for option in options)
        after = sorted(option.option_id for option in ordered)
        if before != after:
            raise ContractViolationError(
                f"OptionRanking {self._ranking.ranking_id!r} must return exactly the options it "
                f"was given, reordered; it answered with {', '.join(after) or 'nothing'} for "
                f"{', '.join(before) or 'nothing'}"
            )
        return ordered

    def _record(
        self,
        query: OptionQuery,
        slate: OptionSlate,
        results: Sequence[OptionSourceResult],
        failures: int,
        started_at: datetime,
    ) -> None:
        """One event per sourcing call: what was asked, who answered, and how long it took.

        A frozen clock answers a latency of zero, which is the honest number for a replayed
        conversation: what matters is the latency that was recorded, not the one a replay
        happens to take.
        """
        finished_at = self._clock.now()
        elapsed_ms = round((finished_at - started_at).total_seconds() * 1000, 3)
        self._telemetry.record(
            TelemetryEvent(
                kind=SOURCING_EVENT_KIND,
                session_id=None,
                occurred_at=finished_at,
                fields={
                    "kind_key": slate.kind_key,
                    "round_index": slate.round_index,
                    "request_id": query.request_id,
                    "requirements_digest": slate.requirements_digest,
                    "profile": self._registry.profile_for(slate.kind_key),
                    "source_ids": tuple(result.source_id for result in results),
                    "options_found": sum(len(result) for result in results),
                    "options_presented": len(slate.options),
                    "sources_failed": failures,
                    "synthesised": any(SYNTHESISED in result.diagnostics for result in results),
                    "latency_ms": elapsed_ms,
                    "ranking_id": self._ranking.ranking_id,
                    "diagnostics": slate.diagnostics,
                },
            )
        )


def _merged(results: Sequence[OptionSourceResult]) -> tuple[PlanOption, ...]:
    """Every option from every source, de-duplicated by ``(source_id, external_ref)``.

    Two sources offering the same room under their own references are two options, and the
    traveller may reasonably choose either — de-duplication is *within* a source's identity, not
    across providers, because nothing here can prove that two references are the same room.

    A source that publishes no ``external_ref`` is keyed by ``option_id`` instead of by
    ``None``. Keying every unreferenced option the same way would silently collapse a whole
    slate into its first row, and "the provider does not expose stable references" is a
    perfectly ordinary thing for a provider to be. ``option_id`` is checked in its own right for
    the same reason it is the fallback: :class:`OptionSlate` refuses a repeat outright, so a
    source that reuses one would make the slate unbuildable rather than merely wrong.
    """
    seen: set[tuple[str, str]] = set()
    identifiers: set[str] = set()
    merged: list[PlanOption] = []
    for result in results:
        for option in result.options:
            reference = option.provenance.external_ref or option.option_id
            keyed = (result.source_id, reference)
            if keyed in seen or option.option_id in identifiers:
                continue
            seen.add(keyed)
            identifiers.add(option.option_id)
            merged.append(option)
    return tuple(merged)
