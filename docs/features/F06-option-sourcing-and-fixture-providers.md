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
