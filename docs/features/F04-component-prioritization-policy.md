# F04 — Component prioritization: mentioned-first rule and the planning agenda

- **Bounded context:** Trip Planning (core domain)
- **Depends on:** [F02](F02-trip-plan-domain-core.md), [F03](F03-requirement-schemas-and-gap-analysis.md)
- **Unlocks:** F05
- **Size:** S
- **Status of the codebase when this starts:** Trip Plans hold Plan Components with typed Requirement
  Sets and Gap Reports. Component Kinds declare `priority_weight` and `requires_outcome_of`, but nothing
  reads them: there is no notion of "what do we plan next".

## Purpose

Answer the single question the dialogue will ask every turn: **which Plan Component do we work on
now?** The answer is the **Planning Agenda**: mentioned Component Kinds first (a hard rule the client
stated explicitly), then unmentioned ones, ordered inside each band by a **replaceable Priority
Policy** built from declared weights and outcome dependencies. The client's "importance metric, to be
defined later" therefore has a real home that can be swapped without touching the dialogue. Visible
outcome: `tourganize catalog agenda --mentioned lodging` prints the exact planning order.

## Starting state

From F02/F03: `ComponentKind` with `priority_weight` and `requires_outcome_of`; `TripPlan` with
`mark_mentioned()`, `settled_kinds()`, `open_kinds()`; `GapReport.is_plannable`.

## Scope — what to implement

1. **Agenda types** (`tourganize/domain/catalog/agenda.py`):
   - `AgendaBand` enum: `MENTIONED`, `UNMENTIONED`.
   - `AgendaEntry` — `kind_key`, `band`, `rank`, `blocked_by: tuple[str, ...]`, `reason_code`.
   - `PlanningAgenda` — ordered `entries`, with `next_actionable()`, `mentioned_open()`,
     `unmentioned_open()`, `is_mentioned_band_empty()`.
2. **The hard rule** (`tourganize/domain/catalog/prioritization.py`) —
   `build_agenda(plan, catalog, policy) -> PlanningAgenda`:
   - partition open (non-settled, non-declined, enabled) kinds into `MENTIONED`
     (`component.mentioned_on_turn is not None`) and `UNMENTIONED`;
   - order **within** each band by the injected `PriorityPolicy`;
   - concatenate `MENTIONED` then `UNMENTIONED` — **never** interleaved. This concatenation is the
     Mentioned-First Rule and is not configurable. A single unit test named
     `test_mentioned_first_is_not_overridable_by_weight` pins it: a mentioned kind with weight 1 must
     precede an unmentioned kind with weight 1000.
3. **The replaceable policy** — `PriorityPolicy` protocol (`tourganize/ports/catalog.py`) and
   `WeightedCatalogPolicy`:
   - sort by descending `priority_weight`, tie-broken by catalog declaration order (stable, so the
     agenda never flickers between turns);
   - apply **Outcome Dependencies as a soft constraint**: if kind B declares
     `requires_outcome_of: [A]` and A is *also open in the same band*, B ranks after A and records
     `blocked_by=("A",)` with `reason_code="awaits_outcome"`. If A is settled, declined, or in a
     different band, B is unconstrained — a traveller who only wants a hotel must never be blocked
     waiting for flights they did not ask for. A second test pins exactly that case.
   - break dependency cycles defensively by declaration order and emit one WARNING (the catalog
     validator in F02 should already have rejected them).
4. **Actionability** — `next_actionable(plan, catalog, policy, analyse)` returns the first entry whose
   component is either not yet Plannable (so the dialogue elicits) or Plannable (so the dialogue
   sources). A `FAILED` component is skipped after `TOURGANIZE_AGENDA_FAILURE_SKIP` consecutive
   failures so one broken kind cannot deadlock the conversation.
5. **Explainability** — `PlanningAgenda.explain()` returning per-entry `(kind_key, band, rank,
   reason_code)`. F05 uses it for telemetry and F11 asserts on it; the traveller never sees it.
6. **CLI** — `tourganize catalog agenda [--mentioned k1,k2] [--settled k3] [--declined k4]` printing the
   agenda table with bands, ranks and reason codes.

## Contract (the Lego connectors)

**Inputs:** a `TripPlan`, a `ComponentCatalog`, a `PriorityPolicy`.

**Outputs:**

```python
@dataclass(frozen=True)
class AgendaEntry:
    kind_key: str
    band: AgendaBand
    rank: int
    blocked_by: tuple[str, ...] = ()
    reason_code: str = "ready"          # ready | awaits_outcome | not_plannable | failed_skipped

@dataclass(frozen=True)
class PlanningAgenda:
    entries: tuple[AgendaEntry, ...]
    def next_actionable(self) -> AgendaEntry | None: ...
    def is_mentioned_band_empty(self) -> bool: ...   # gate for Proactive Offers (F05)
    def explain(self) -> tuple[tuple[str, str, int, str], ...]: ...

class PriorityPolicy(Protocol):
    @property
    def policy_id(self) -> str: ...
    def order(self, candidates: Sequence[ComponentKind], plan: TripPlan) -> Sequence[str]: ...
```

**Ports consumed:** `ComponentCatalog`.

**Ports provided:** `PriorityPolicy`, with `WeightedCatalogPolicy` (default) and
`FixedOrderPolicy` (test fake taking an explicit list).

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_PRIORITY_POLICY` | Which policy the Composition Root builds (`weighted`, `fixed`) | `weighted` |
| `TOURGANIZE_AGENDA_FAILURE_SKIP` | Consecutive sourcing failures before a kind is skipped | `2` |

**Errors/failure modes:** none raised in normal operation — an empty agenda is a valid, meaningful
result (everything is settled or declined; F05 reads it as "time to summarise"). A policy that returns
a `kind_key` it was not given, or drops one, raises `ContractViolationError`: policies are replaceable,
so their contract is checked at the seam.

## Out of scope

Deciding *what to do* with the next entry — eliciting, sourcing, offering, closing — is F05. Asking the
traveller anything. Any notion of turn-taking. Learning weights from behaviour, or context-sensitive
importance (an explicit non-goal of the shipped policy; see D3's reversal path).

## Replaceability notes

**Must be preserved:** the Mentioned-First concatenation, which lives in `build_agenda`, **not** in the
policy — a replacement policy must be unable to violate it. The `PlanningAgenda` /`AgendaEntry` shape,
`next_actionable()`, `is_mentioned_band_empty()`, and the rule that Outcome Dependencies are soft.

**Free to change:** everything inside `PriorityPolicy.order` — weights, an LLM-scored policy, a
learned policy, seasonality. The `reason_code` vocabulary may grow (consumers must treat unknown codes
as opaque). The stability tie-break may change as long as it stays deterministic.

## Definition of done

- [ ] `tourganize catalog agenda --mentioned lodging` prints `lodging` in band `MENTIONED` at rank 0,
      followed by `air_travel` and `ground_transport` in band `UNMENTIONED` ordered by weight.
- [ ] `test_mentioned_first_is_not_overridable_by_weight` passes: mentioned weight-1 kind precedes
      unmentioned weight-1000 kind.
- [ ] `test_outcome_dependency_is_soft` passes: with only `lodging` mentioned and `air_travel`
      unmentioned, `lodging` is actionable immediately and is **not** marked `awaits_outcome`.
- [ ] `test_outcome_dependency_orders_within_band` passes: with both `air_travel` and `lodging`
      mentioned, `air_travel` ranks first and `lodging` carries `blocked_by=("air_travel",)`.
- [ ] The agenda is stable: building it twice from the same plan yields identical entries, and settling
      one component does not reorder the remainder (test with three kinds).
- [ ] `is_mentioned_band_empty()` is true exactly when no mentioned kind is open — tested including the
      case where a mentioned kind was declined.
- [ ] A `FixedOrderPolicy` returning an unexpected `kind_key`, and one dropping a `kind_key`, both raise
      `ContractViolationError`.
- [ ] Swapping to `TOURGANIZE_PRIORITY_POLICY=fixed` changes the order with no other code change — proven
      by an integration test through the Composition Root.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass; `catalog show`/`validate`/`gaps` still work.

## Open questions / risks

- **Implementer's call:** the exact `reason_code` strings; whether `next_actionable` needs the
  `analyse` function injected or takes a precomputed plannability map (the latter is easier to test).
- **Risk:** the Mentioned-First rule drifting into the policy for convenience. If a future policy needs
  to "slightly" reorder across bands, that is a change to the client's stated requirement and needs an
  ADR, not a code tweak.
- **Open (for the client, not blocking):** the real importance metric. Until it is defined, the shipped
  weights (`air_travel 300 > lodging 200 > ground_transport 100`) are the documented default, justified
  in [D3](../architecture/decisions.md).
