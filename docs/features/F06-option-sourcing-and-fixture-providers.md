# F06 — Option sourcing port and fixture providers

- **Bounded context:** Option Sourcing
- **Depends on:** [F02](F02-trip-plan-domain-core.md), [F03](F03-requirement-schemas-and-gap-analysis.md), [F05](F05-dialogue-director-and-session-lifecycle.md)
- **Unlocks:** F07, F17, F24
- **Size:** M
- **Status of the codebase when this starts:** the Dialogue Director runs the full conversation against a
  **fake** planner that returns fixed slates. Requirement Sets and Gap Reports are real; option data is
  not.

## Purpose

Give the conversation real option data — real in *shape*, from fixtures in *content*. This feature
introduces the `OptionSource` port, the `Option Query` that carries a Component Kind's Requirement Set
to it, the **Planning Service** that assembles a query, calls the right sources, applies optional
filters, ranks and truncates to a Slate; and **Fixture Providers** that serve recorded data for the
three shipped Component Kinds. Per [D9](../architecture/decisions.md) fixtures are the permanent test
default, and every later source — MCP-backed (F17), live commercial (F24) — implements this exact port.

## Starting state

From F05: `OptionSlatePlanner` protocol consumed by the Director, satisfied by a fake. From F02/F03:
`PlanOption`, `OptionSlate`, `Money`, `Provenance`, `RequirementSet`, `RequirementSchema` with
`optional_fields()`.

## Scope — what to implement

1. **Port and query types** (`tourganize/ports/options.py`, `tourganize/domain/options/query.py`):
   `OptionQuery` — `kind_key`, `requirements`, `slate_size`, `locale`, `context_selections`
   (the Selections this kind's `requires_outcome_of` entitles it to read), `request_id`.
   `OptionSourceResult` — `options`, `source_id`, `retrieved_at`, `partial: bool`, `diagnostics`.
2. **Source registry** (`tourganize/adapters/options/registry.py`) — maps `kind_key` → ordered sources
   from `TOURGANIZE_OPTION_SOURCE_PROFILE` (`fixture` now; `world` in F17, `live` in F24, and a
   per-kind override syntax `lodging=live,air_travel=fixture` so profiles can be mixed). Registering
   two sources for one kind is legal: results are merged and de-duplicated by
   `(source_id, external_ref)`.
3. **Fixture Providers** (`tourganize/adapters/options/fixture/`) — one generic provider driven by data,
   **not** one class per kind:
   - loads `fixtures/options/<kind_key>/*.json`;
   - matches on the requirement fields declared `matchable` in the fixture file (place, date overlap),
     with a documented fallback: if nothing matches, return a deterministic synthetic set derived from
     the query (so a demo never dead-ends) and set `diagnostics=["synthesised"]`;
   - is deterministic: same query → same options in the same order, seeded by
     `requirements.digest()` so refinements visibly change the slate.
4. **Planning Service** (`tourganize/application/planning_service.py`) implementing
   `OptionSlatePlanner` for real:
   - build the `OptionQuery` (slate size from settings, `context_selections` from the plan, locale from
     the session);
   - call sources (serially — [D5](../architecture/decisions.md) forbids assuming parallel fan-out
     anywhere) with a per-source timeout;
   - **apply optional filters** as declared in the Requirement Schema: filters are *soft* — options
     failing an optional filter are demoted, not discarded, unless
     `TOURGANIZE_OPTION_FILTER_STRICT=true`. Rationale: a traveller who says "under €150" should still
     be shown a €160 option rather than an empty slate, marked as exceeding the ceiling. Each option
     carries `facts["_filter_notes"]` listing which optional filters it fails;
   - rank by a small, explicit, replaceable `OptionRanking` (price ascending, then declared filter
     satisfaction, then source order) and truncate to `slate_size`;
   - return an `OptionSlate` with `requirements_digest` set, and record telemetry (source ids, counts,
     latency, whether synthesised).
5. **Empty and failure handling** — zero options after merging is **not** an exception: return an empty
   slate with `diagnostics`, and the Director (F05) already turns that into
   `report_sourcing_failure`. A source raising or timing out is logged, recorded in `diagnostics`, and
   skipped; only *all* sources failing produces `OptionSourcingError`.
6. **Contract test suite** (`tests/contracts/test_option_source_contract.py`) — a parametrised suite
   every present and future `OptionSource` adapter must pass: honours `slate_size`, returns only its
   declared `kind_keys`, sets `Provenance` on every option, prices carry a currency, `option_id` is
   unique within a result and stable across identical queries, and no option contains prose. This suite
   is what makes [D9](../architecture/decisions.md)'s "a stub's shape may never differ from the real
   port" enforceable.
7. **Fixture data** — at least 8 lodging, 8 air-travel and 5 ground-transport options across two
   cities, with prices in two currencies and varied review scores, so filters, ranking and refinement
   are all visibly exercised.
8. **CLI** — `tourganize options search --kind lodging --set '<json>'` printing the resulting slate
   (the first command that shows real option data).

## Contract (the Lego connectors)

**Inputs:** an `OptionQuery`; fixture files on disk.

```python
@dataclass(frozen=True)
class OptionQuery:
    kind_key: str
    requirements: RequirementSet
    slate_size: int
    locale: str
    context_selections: Mapping[str, Selection] = field(default_factory=dict)
    request_id: str = ""

@dataclass(frozen=True)
class OptionSourceResult:
    options: tuple[PlanOption, ...]
    source_id: str
    retrieved_at: datetime
    partial: bool = False
    diagnostics: tuple[str, ...] = ()

class OptionSource(Protocol):
    @property
    def source_id(self) -> str: ...
    @property
    def kind_keys(self) -> frozenset[str]: ...
    def search(self, query: OptionQuery) -> OptionSourceResult: ...
```

```json
// fixtures/options/lodging/paris.json — content is fixture; shape is the contract
{
  "kind_key": "lodging",
  "matchable": ["place", "date_range"],
  "match": {"place": ["Paris", "פריז"]},
  "options": [
    {"external_ref": "px-hotel-001",
     "facts": {"name": "Hôtel Saint-Germain", "area": "6e", "review_score": 8.7,
               "room_type": "double", "refundable": true, "nights": 5},
     "price": {"amount_minor": 74000, "currency": "EUR"}}
  ]
}
```

**Outputs:** `OptionSlate` (F02 type) via `OptionSlatePlanner.plan(...)`; `OptionSourceResult` from each
source.

**Ports consumed:** `ComponentCatalog` (schema, for optional-filter declarations), `Clock`,
`TelemetrySink`.

**Ports provided:** `OptionSource` (+ `FixtureOptionSource`, `FailingOptionSource` and
`RecordedOptionSource` fakes), `OptionRanking`, and the real `OptionSlatePlanner` implementation.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_OPTION_SOURCE_PROFILE` | `fixture` / `world` / `live`, or per-kind overrides | `fixture` |
| `TOURGANIZE_FIXTURE_DIR` | Root of fixture option data | `./fixtures/options` |
| `TOURGANIZE_SLATE_SIZE` | Options presented per round | `3` |
| `TOURGANIZE_OPTION_FILTER_STRICT` | Optional filters discard instead of demote | `false` |
| `TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS` | Per-source timeout | `10` |

**Errors/failure modes:** `OptionSourcingError` only when every source for a kind fails;
`UnknownComponentKindError` if no source is registered for a kind (a configuration bug, surfaced by
`doctor`); individual source failures degrade to diagnostics.

Nine places where the shipped code says something different from this file. They are labelled the
way F05's are: **forced** means a rule that outranks this file left no choice, **spec-authorised**
means this file's own Open questions or Scope allow it, and **implementer's choice** means a
judgement that could have gone the other way and is defended here on its merits.

- *(forced)* `OptionQuery` and `OptionSourceResult` are **defined** in
  `tourganize/domain/options/query.py` and re-exported by `tourganize/ports/options.py`, which is
  the documented import path — the third instance of the rule [D15](../architecture/decisions.md)
  and [D17](../architecture/decisions.md) already record: a port's contract has to name the types
  it carries, and these carry a `RequirementSet`, a `Selection` and a `PlanOption`. They are also
  deliberately **not** re-exported from `tourganize.domain.options`: a query names a `Selection`,
  `domain/trip` already imports `domain/options` for the Option Slate a Trip Plan records, and a
  package-level re-export would make the two import each other at import time. The same applies to
  `domain/options/filters.py`. Both modules say so in their own docstrings.
- *(forced)* **The Option Query's locale is the application default, not the session's.** Scope
  item 4 asks for "locale from the session", and the `OptionSlatePlanner` contract — F05's, which
  the Definition of done requires to pass *unchanged* — carries no locale in `plan(...)`. Widening
  it would have broken the very suite this feature is measured against. `PlanningService` takes a
  `locale` constructor argument defaulting to `DEFAULT_LOCALE`, so a surface can build a
  per-session planner the moment the port grows one; F07 is where a session locale first has
  somewhere to come from.
- *(spec-authorised)* **`filter_notes` is a typed sibling of `facts`, not `facts["_filter_notes"]`.**
  The Open questions recommend exactly this "if it can be added without disturbing F02's
  `PlanOption`", and it could: a defaulted tuple field, `with_filter_notes()` returning a copy, and
  F02's own "no prose" test extended to name it. `facts` is what a source declared and the notes are
  what Tourganize concluded; burying one inside the other makes both harder to trust. The notes are
  carried into the `present_slate` Act payload, which is what F07's DoD item hangs off.
- *(implementer's choice)* **Optional filters are *declared* in the Requirement Schema rather than
  inferred from a field's type.** Scope item 4 says "as declared in the Requirement Schema" without
  saying how; the how is two keys in the Field Spec's `constraints` bag — `filters` and `comparison`
  — recorded as [D19](../architecture/decisions.md). Inferring "a money field is a ceiling, a score
  is a floor" would work for the three shipped Kinds and break the zero-Python promise for the
  fourth, which is the failure mode F02 exists to prevent arriving through a side door.
- *(implementer's choice)* **The ranking puts filter satisfaction first, then price.** This file's
  sketch says "price ascending, then declared filter satisfaction". Taken literally, demotion means
  nothing: a cheap option that fails the review-score filter would still lead the slate, and "the
  €160 room is shown *below* the ones under €150, marked" is the behaviour the same paragraph asks
  for. The Open questions make the tie-breaks the implementer's call. Full order: filters satisfied,
  price ascending within a currency, source order, `option_id`.
- *(implementer's choice)* **Two currencies are grouped by code rather than converted.** `Money`
  refuses to order two currencies — there is no exchange rate in the domain — so the ranking sorts by
  `(currency, amount)`. Arbitrary between groups, stable, and honest; a query is answered in one
  currency in practice.
- *(implementer's choice)* **A source is asked for more candidates than the slate holds** —
  `TOURGANIZE_SLATE_SIZE × CANDIDATE_FACTOR`, a constant of 4. With a factor of one, a single source
  would be *choosing* the slate and every replaceable piece below it would be decoration: "cheapest
  first" would be a sort of three arbitrary rows. `slate_size` on the query is therefore the
  candidate count the port promises to honour, and the *slate* is truncated to the setting.
- *(implementer's choice)* **The per-source timeout bounds the call with a watchdog thread.**
  `search` runs on one worker and the service waits at most
  `TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS` for it; a source that hangs is abandoned, whatever it
  eventually produces is discarded, and the slate is built from the survivors. Measuring the elapsed
  time *after* `search` returns can only describe a source that has already come back, so a provider
  that never does would hold a traveller's turn open for as long as it liked — which is the one
  thing the setting exists to prevent. This is not the parallel fan-out
  [D5](../architecture/decisions.md) forbids: exactly one source is in flight at a time, the next is
  not asked until this one is finished with, and the `OptionSource` protocol stays synchronous, so no
  adapter learns that a watchdog exists. The budget is checked a second time against the injected
  `Clock` once the call is back, because that is the clock a source with a recorded or simulated
  latency moves, and a replay should see the latency it was recorded with. A source that overruns
  either check is counted as failed, so *all* of them overrunning is `OptionSourcingError`, exactly
  as all of them raising is.
- *(implementer's choice)* **`OptionSlate` gained a `diagnostics` field.** Scope item 5 asks for "an
  empty slate with `diagnostics`" and F02's type had nowhere to put them. Opaque codes, like an
  Agenda Reason Code — `synthesised:fixture`, `source_failed:world`, `filtered_out` — because "here
  are three options, and one provider was unreachable" is a different answer from "here are three
  options", and only the slate can carry the difference to a surface.

## Out of scope

Any network call, MCP, or real provider (F15, F17, F24). Feasibility assessment of combinations (F16).
Presenting or wording options (F07/F10). Caching across sessions. Pagination or "more options" — the
client's model is a short slate plus refinement, and refinement re-queries.

## Replaceability notes

**Must be preserved:** the `OptionSource` protocol and the contract suite; `OptionQuery`'s field set;
that sources return structured `PlanOption`s with `Provenance` and no prose; determinism of the fixture
provider (the Golden Conversations depend on it); soft-filter semantics unless configured strict.

**Free to change:** fixture file format and matching logic; the ranking implementation; merge and
de-duplication strategy; whether sources are called serially or (later, if a provider permits it)
concurrently — the port makes no promise either way.

## Definition of done

- [ ] `tourganize options search --kind lodging --set '{"place":"Paris","date_range":"2026-10-23/2026-10-28"}'`
      prints 3 options with prices, review scores and provenance.
- [ ] The Director now uses the real Planning Service: the F05 scenario suite passes **unchanged** with
      the fake planner swapped for the real one via the Composition Root.
- [ ] Determinism: the same query twice yields byte-identical slates; a refinement changing
      `min_review_score` yields a **different** slate (proving the digest seeds ordering).
- [ ] Optional filters: with `budget_ceiling` set below every fixture price, the slate is still
      non-empty and every option carries a `_filter_notes` entry; with
      `TOURGANIZE_OPTION_FILTER_STRICT=true`, the slate is empty and the Director emits
      `report_sourcing_failure`.
- [ ] `slate_size` is honoured (test with 1, 3, 10 where fewer options exist than requested).
- [ ] The contract suite in `tests/contracts/` passes for `FixtureOptionSource` and for a deliberately
      broken adapter fixture that **fails** it (proving the suite bites) on: prose in an option, a
      missing currency, a duplicate `option_id`, and returning an undeclared `kind_key`.
- [ ] One failing source out of two registered for a kind yields a slate from the survivor plus a
      diagnostic; both failing raises `OptionSourcingError`, which the Director converts to
      `report_sourcing_failure` without ending the session.
- [ ] A per-kind profile override (`TOURGANIZE_OPTION_SOURCE_PROFILE=lodging=fixture,air_travel=fixture`)
      is parsed and reflected by `tourganize doctor`.
- [ ] Telemetry records per sourcing call: kind, source ids, option counts, latency, synthesised flag.
- [ ] Adding a fourth Component Kind's fixtures requires no Python change (test with a `dining` fixture
      directory and a catalog entry).
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass (`adapters.options` must not import
      `adapters.presentation` or any other adapter package).

## Open questions / risks

- **Implementer's call:** fixture matching depth (place + date overlap is enough); the synthetic
  fallback generator; the exact ranking tie-breaks; whether `_filter_notes` lives in `facts` or a
  sibling field (recommended: a typed sibling, `filter_notes`, if it can be added without disturbing
  F02's `PlanOption`).
- **Risk:** fixtures flattering the design — real providers return sparse, inconsistent fields.
  Mitigated by the contract suite and by F24's obligation to pass it, but the risk is real until a live
  provider exists.
- **Risk:** soft filtering surprising a traveller ("I said under €150"). The presentation layer must
  make `filter_notes` visible (an F07 DoD item), or this behaviour reads as ignoring the request.
