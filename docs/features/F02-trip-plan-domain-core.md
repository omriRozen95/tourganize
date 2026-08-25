# F02 — Trip Plan domain core: plan components and the component catalog

- **Bounded context:** Trip Planning (core domain)
- **Depends on:** [F01](F01-project-foundation.md)
- **Unlocks:** F03, F04, F06, F13
- **Size:** M
- **Status of the codebase when this starts:** the package skeleton, Settings, logging, the `Clock` and
  `TelemetrySink` ports, the CLI stub and the CPU container exist. There is no domain model, no
  dialogue, no adapters.

## Purpose

Introduce the vocabulary the whole system is built on: a **Trip Plan** made of **Plan Components**,
where a Plan Component's *type* is a **Component Kind** declared as **data** in the Component Catalog.
This is the feature that makes flights/lodging/car-rental configuration rather than code, and it gives
the system its assembly model — Option Slates, Selections and Plan Completeness — with no idea that
LLMs, networks or documents exist. Visible outcome: `tourganize catalog show` lists the configured
Component Kinds and their declared properties.

## Starting state

From F01: package tree, `Settings` (with `config_dir`), errors, `Clock`. `tourganize/domain/` exists
but is empty. `config/` exists but has no catalog file.

## Scope — what to implement

1. **Component Kind and Catalog** (`tourganize/domain/catalog/`):
   - `ComponentKind` — frozen: `kind_key`, `message_key`, `priority_weight`, `requires_outcome_of`,
     `schema_key`, `enabled`.
   - `ComponentCatalog` protocol (in `tourganize/ports/catalog.py`) and `YamlComponentCatalog` loading
     `${TOURGANIZE_CONFIG_DIR}/catalog/components.yaml`, plus an `InMemoryComponentCatalog` fake for
     tests.
   - Validation at load: unique `kind_key`s, `requires_outcome_of` references resolve, no dependency
     cycles, weights are integers. Failures raise `CatalogError(ConfigurationError)`.
   - Ship the three kinds — `air_travel`, `lodging`, `ground_transport` — **in YAML only**. Grep for
     these strings in Python must return nothing outside fixtures and tests.
2. **Value objects** (`tourganize/domain/options/`): `Money` (minor units + ISO currency, no floats),
   `Provenance` (`source_id`, `retrieved_at`, `external_ref`, `citations`), `PlanOption`
   (`option_id`, `kind_key`, `facts: Mapping[str, object]`, `price: Money | None`, `provenance`) and
   `OptionSlate` (`kind_key`, `round_index`, `options`, `requirements_digest`). `PlanOption` carries
   **no prose**: assert in review that it has no `title`/`description` string field.
3. **Plan Component** (`tourganize/domain/trip/component.py`): `ComponentStatus` enum
   (`PENDING`, `ELICITING`, `READY`, `SOURCING`, `AWAITING_CHOICE`, `SELECTED`, `DECLINED`, `FAILED`),
   `PlanComponent` holding `kind_key`, `requirements` (opaque here — typed in F03), `slates` history,
   `selection`, `status`, `mentioned_on_turn: int | None`, and the **legal transition table** as data
   with `advance_to(status)` raising `IllegalTransitionError` on anything else.
4. **Selection and assembly** (`tourganize/domain/trip/plan.py`): `Selection`
   (`kind_key`, `option`, `chosen_at_turn`), `TripPlan` aggregate (`plan_id`, `components`,
   `created_at`) with the only mutators the domain allows — `ensure_component(kind_key)`,
   `record_slate`, `record_selection`, `decline`, `mark_mentioned` — and derived reads
   `settled_kinds()`, `open_kinds()`, `completeness()`.
5. **Plan Completeness** (`tourganize/domain/trip/completeness.py`): `PlanCompleteness` with
   `selected`, `declined`, `open`, `is_closeable` (no open *mentioned* component remains). This is what
   F05 consults before summarising and F13 renders.
6. **Slate history semantics**: recording a slate for a component that already has one appends a round
   with `round_index = len(slates)`; recording a Selection on a component whose latest slate does not
   contain that `option_id` raises `UnknownOptionError`. Refinement history is never discarded — F14's
   exported plan may show what was rejected.
7. **CLI** — implement `tourganize catalog show` (table of kinds, weights, dependencies, schema keys)
   and `tourganize catalog validate` (exit 0/3). First genuinely working sub-command.

## Contract (the Lego connectors)

**Inputs:** `config/catalog/components.yaml`; in-process calls from later features.

```yaml
# config/catalog/components.yaml — data, not code
version: 1
kinds:
  - kind_key: air_travel
    message_key: component.air_travel
    priority_weight: 300
    schema_key: air_travel.v1
    requires_outcome_of: []
    enabled: true
  - kind_key: lodging
    message_key: component.lodging
    priority_weight: 200
    schema_key: lodging.v1
    requires_outcome_of: [air_travel]     # reads chosen dates when air travel is selected
    enabled: true
  - kind_key: ground_transport
    message_key: component.ground_transport
    priority_weight: 100
    schema_key: ground_transport.v1
    requires_outcome_of: [lodging]
    enabled: true
```

**Outputs:** the domain types below, importable by any later feature.

```python
@dataclass(frozen=True)
class ComponentKind:
    kind_key: str
    message_key: str
    priority_weight: int
    schema_key: str
    requires_outcome_of: tuple[str, ...] = ()
    enabled: bool = True

@dataclass
class PlanComponent:
    kind_key: str
    status: ComponentStatus = ComponentStatus.PENDING
    requirements: RequirementSet | None = None      # typed in F03
    slates: tuple[OptionSlate, ...] = ()
    selection: Selection | None = None
    mentioned_on_turn: int | None = None
    def latest_slate(self) -> OptionSlate | None: ...
    def advance_to(self, status: ComponentStatus) -> None: ...   # raises IllegalTransitionError

@dataclass
class TripPlan:
    plan_id: str
    created_at: datetime
    components: dict[str, PlanComponent] = field(default_factory=dict)
    def ensure_component(self, kind_key: str) -> PlanComponent: ...
    def mark_mentioned(self, kind_key: str, turn_index: int) -> None: ...
    def record_slate(self, slate: OptionSlate) -> None: ...
    def record_selection(self, selection: Selection) -> None: ...   # raises UnknownOptionError
    def decline(self, kind_key: str) -> None: ...
    def completeness(self) -> PlanCompleteness: ...
```

**Ports consumed:** `Clock` (for `created_at`, so tests are deterministic).

**Ports provided:** `ComponentCatalog`, with `YamlComponentCatalog` and `InMemoryComponentCatalog`.

**Config/env keys introduced:** `TOURGANIZE_CATALOG_PATH` — path to the catalog file; default
`${TOURGANIZE_CONFIG_DIR}/catalog/components.yaml`.

**Errors/failure modes:** `CatalogError` (invalid or cyclic catalog, at load), `IllegalTransitionError`,
`UnknownOptionError`, `UnknownComponentKindError`. All derive from `TourganizeError`. The domain never
logs and never degrades — it raises, and callers decide.

## Out of scope

Requirement Schemas and Gap analysis (F03) — `requirements` stays a typed hole here. Agenda ordering
(F04). Anything that *produces* Plan Options (F06). Persistence (F12). Rendering (F13). No Component
Kind may gain topic-specific behaviour: if a kind needs special handling, that is a declared property in
YAML, not a subclass.

## Replaceability notes

**Must be preserved:** the `ComponentKind`/`PlanComponent`/`TripPlan`/`Selection`/`OptionSlate`/
`PlanOption` names and field meanings; `kind_key` as the only identity of a topic; the `ComponentStatus`
values and the fact that transitions are validated; `completeness()` as the closing gate; the catalog as
loaded data.

**Free to change:** the YAML shape behind the `ComponentCatalog` protocol (JSON, database, remote
config all legal); how the transition table is stored; whether `TripPlan.components` is a dict or an
ordered structure; the `requirements_digest` algorithm.

## Definition of done

- [ ] `tourganize catalog show` prints the three shipped kinds with weights and dependencies;
      `tourganize catalog validate` exits 0 on the shipped file and 3 on a fixture with a duplicate key,
      a dangling `requires_outcome_of`, and (separately) a dependency cycle.
- [ ] `grep -rn "air_travel\|lodging\|ground_transport" tourganize/` returns **no** hits — proven by an
      automated test, not by eye.
- [ ] Unit tests cover: catalog load and every validation failure; every legal `ComponentStatus`
      transition and at least three illegal ones raising `IllegalTransitionError`; slate rounds
      incrementing; `record_selection` with an option absent from the latest slate raising
      `UnknownOptionError`; `completeness()` across selected/declined/open combinations.
- [ ] A test asserts `PlanOption` has no free-text prose field and that `Money` rejects float
      construction.
- [ ] A test builds a Trip Plan with two components, records two slates on one, selects from the second
      slate, and asserts the earlier round is still present in history.
- [ ] `mypy --strict` and `lint-imports` pass — in particular `tourganize.domain` still imports nothing
      but stdlib (the YAML loader lives behind the port, in the adapter/infrastructure module, not in
      `domain/`).
- [ ] Adding a fourth kind to the YAML (e.g. `dining`) requires **zero** Python changes; a test proves
      it by loading a fixture catalog with an extra kind and asserting it appears in `kinds()`.
- [ ] App still runs: `tourganize doctor` exits 0 and now reports the catalog as loadable.

## Open questions / risks

- **Implementer's call:** where the YAML parsing lives (suggested: `tourganize/adapters/catalog/yaml/`
  so `domain/` stays import-clean); `requirements_digest` hashing scheme; whether `TripPlan` is a
  dataclass or has a private constructor.
- **Risk:** leaking presentation into `PlanOption`. The moment an option grows a `description` string,
  the bilingual requirement breaks, because prose must be composed per locale at presentation time.
- **Risk:** `requires_outcome_of` is easy to over-interpret. It constrains *ordering* only; it must
  never mean "component B cannot be planned unless A was selected", or a traveller who only wants a
  hotel gets blocked.
