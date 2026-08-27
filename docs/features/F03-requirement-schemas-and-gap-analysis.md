# F03 — Requirement schemas and gap analysis

- **Bounded context:** Trip Planning (core domain)
- **Depends on:** [F02](F02-trip-plan-domain-core.md)
- **Unlocks:** F04, F05, F06
- **Size:** M
- **Status of the codebase when this starts:** the Trip Plan domain exists with Plan Components whose
  `requirements` field is a typed hole. The Component Catalog is loaded from YAML and each kind declares
  a `schema_key` that nothing yet resolves.

## Purpose

Teach the system what it needs to know before it can plan anything. Each Component Kind declares its
own **Requirement Schema**: which fields describe the traveller's wish, which of them are **blocking**
(planning may not start without them) and which are **optional filters** (asked opportunistically,
never blocking). The feature also produces the **Gap Report** — the exact list of what is still missing
— which is the input the dialogue will turn into questions. Visible outcome:
`tourganize catalog gaps --kind lodging --set '{"place":"Paris"}'` prints what is still blocking and
what is merely optional.

## Starting state

From F02: `ComponentKind.schema_key`, `PlanComponent.requirements: RequirementSet | None`, the
`ComponentCatalog` port with `schema_for()` declared but unimplemented, `CatalogError`.

## Scope — what to implement

1. **Schema types** (`tourganize/domain/requirements/schema.py`):
   - `FieldKind` enum: `date_range`, `date`, `place`, `integer`, `money`, `score`, `text`, `enum`,
     `boolean`, `duration`.
   - `Obligation` enum: `BLOCKING`, `OPTIONAL`.
   - `FieldSpec` — `name`, `field_kind`, `obligation`, `prompt_message_key`, `example_message_key`,
     `enum_values`, `constraints: Mapping[str, object]` (e.g. `{"min": 0, "max": 10}`).
   - `RequirementSchema` — `schema_key`, `component_kind`, `fields`, with `blocking_fields()`,
     `optional_fields()`, `field(name)`.
   - `satisfied_by` semantics: a **field group** may satisfy a blocking requirement in more than one way
     (`any_of` groups), because the client's own example — "there should be some time range, if not a
     specific start and end date" — is exactly that: `date_range` OR (`start_date` AND `end_date`)
     satisfies the same obligation. Model this as `BlockingRule` with `any_of: tuple[tuple[str, ...], ...]`
     rather than as per-field flags.
2. **Schema loading** — `${TOURGANIZE_CONFIG_DIR}/catalog/schemas/<schema_key>.yaml`, wired into
   `YamlComponentCatalog.schema_for()`. Validation at load: every `BlockingRule` group references
   declared fields; every field has a `prompt_message_key`; enum fields declare values. Ship
   `air_travel.v1`, `lodging.v1`, `ground_transport.v1`.
3. **Value types** (`tourganize/domain/requirements/values.py`):
   - `RequirementSource` enum: `USER`, `INFERRED`, `DEFAULT`, `CARRIED_OVER`.
   - `RequirementValue` — `value`, `source`, `turn_index`, `confidence: float | None`.
   - `RequirementSet` — frozen mapping `field name → RequirementValue` plus `component_kind`, with
     `with_updates(updates)` returning a **new** set (never mutate), `digest()` for the slate's
     `requirements_digest`, and `provenance_of(name)`.
   - Merge precedence when a later turn contradicts an earlier value: `USER` overwrites anything;
     `INFERRED` overwrites only `DEFAULT`/`CARRIED_OVER`; a same-source update always wins by turn
     index (later turn wins — and a value arriving on the *same* turn wins too, because two values
     for one field in one turn are a correction in mid-sentence). Contradictions are recorded, not
     silently dropped: `RequirementSet.superseded` keeps the replaced values so a refinement can be
     explained, each entry saying **how** the value stopped being in force (see the Contract).
4. **Validation and normalisation** (`tourganize/domain/requirements/validation.py`) — per `FieldKind`,
   pure functions: date ranges must have `end >= start`; a `score` must sit inside its constraints;
   `money` must carry a currency; a `place` is normalised to a trimmed, case-preserved string (real
   resolution to codes/coordinates is F16/F17, explicitly not here). Invalid values raise
   `RequirementValueError` naming the field and the reason — the dialogue turns that into a re-ask.
   Relative expressions ("this year", "next month") are **not** interpreted here: F03 accepts only
   resolved values, and resolution against the `Clock` happens in the interpretation layer (F05/F08).
5. **Gap analysis** (`tourganize/domain/requirements/gaps.py`) — pure:
   `analyse(schema, requirement_set) -> GapReport` with `blocking` (unsatisfied `BlockingRule` groups,
   each carrying the candidate field sets that would satisfy it), `optional` (declared optional fields
   with no value), `invalid` (values present but failing validation) and `is_plannable`
   (nothing blocking is missing, and no value a `BlockingRule` *reads* is invalid).
   > **Reconciliation.** This feature first said `is_plannable = not blocking and not invalid`, which
   > would let an unusable *optional filter* hold sourcing up. CLAUDE.md's standing invariant —
   > "blocking gaps are resolved before sourcing; **optional filters never block**" — governs, so an
   > invalid optional value is still listed in `invalid` and still re-asked, alongside the first slate
   > rather than instead of it. Each `InvalidValue` therefore carries `blocks`, true when a
   > `BlockingRule` reads its field.
6. **Ask ordering** — `GapReport.next_blocking()` returns the single most useful blocking gap to ask
   about, ordered by schema declaration order. One question at a time is the dialogue's job (F05), but
   the *ordering* is a domain fact and lives here. Only that ordering: *which* of a rule's candidate
   groups to pursue, and which field of it to ask for, is asking policy and is F05's — each group
   arrives carrying its own still-missing fields, which is everything a policy needs.
7. **CLI** — `tourganize catalog gaps --kind <k> [--set <json>]` printing the Gap Report; extends
   `catalog validate` to validate all schema files too.

## Contract (the Lego connectors)

**Inputs:** schema YAML files; a `RequirementSet` (possibly empty).

```yaml
# config/catalog/schemas/lodging.v1.yaml
schema_key: lodging.v1
component_kind: lodging
fields:
  - name: place            # where the traveller wants to stay
    field_kind: place
    obligation: blocking
    prompt_message_key: ask.lodging.place
  - name: date_range
    field_kind: date_range
    obligation: blocking
    prompt_message_key: ask.lodging.date_range
  - name: check_in
    field_kind: date
    obligation: optional
    prompt_message_key: ask.lodging.check_in
  - name: check_out
    field_kind: date
    obligation: optional
    prompt_message_key: ask.lodging.check_out
  - name: guests
    field_kind: integer
    obligation: optional
    prompt_message_key: ask.lodging.guests
    constraints: {min: 1, max: 12}
  - name: budget_ceiling
    field_kind: money
    obligation: optional
    prompt_message_key: ask.lodging.budget_ceiling
  - name: min_review_score
    field_kind: score
    obligation: optional
    prompt_message_key: ask.lodging.min_review_score
    constraints: {min: 0, max: 10}
blocking_rules:
  - name: where
    any_of: [[place]]
  - name: when                      # a range, or an explicit pair — either satisfies "when"
    any_of: [[date_range], [check_in, check_out]]
```

**Outputs:**

```python
@dataclass(frozen=True)
class GapReport:
    component_kind: str
    blocking: tuple[BlockingGap, ...]     # each: rule name + candidate groups (fields + missing)
    optional: tuple[FieldSpec, ...]
    invalid: tuple[InvalidValue, ...]     # each carries `blocks`: does a BlockingRule read it?
    @property
    def is_plannable(self) -> bool: ...   # not blocking and no *blocking* value invalid
    def next_blocking(self) -> BlockingGap | None: ...

@dataclass(frozen=True)
class RequirementSet:
    component_kind: str
    values: Mapping[str, RequirementValue]
    superseded: tuple[SupersededValue, ...] = ()   # each: the value + REPLACED or OVERRULED
    def with_updates(
        self, updates: Sequence[RequirementUpdate], *, schema: RequirementSchema
    ) -> "RequirementSet": ...
    def digest(self) -> str: ...
    def provenance_of(self, field_name: str) -> RequirementValue | None: ...
```

`with_updates` takes the schema as a *parameter* rather than the set holding one as a field: a set is
small, copied every turn and persisted by F12, while a schema is shared, versioned and loaded from a
file, and a persisted set that carried a schema would be persisting a copy of a file. The merge needs
the schema because it **normalises** — what a value's normalised form is, is a fact about its Field
Spec — and, being there, it also refuses a field the schema does not declare. Neither is exclusive to
this signature: a module-level `merge(schema, set, updates)`, or a check when the update is built,
would raise `UnknownFieldError` just as well. Recorded as
[D14](../architecture/decisions.md) because it amends this Contract block.

`with_updates` normalises what it can and stores what it cannot verbatim: an *invalid* value has to
survive into the set, or `GapReport.invalid` would have nothing to report and the dialogue nothing to
re-ask about.

`superseded` keeps **both** kinds of loser, and says which is which. A value that was held and was
pushed out is `replaced`; a value that arrived and never took hold because the standing value
outranked it is `overruled`. Both are kept — "we heard you, but your earlier answer stands" is a thing
the dialogue may need to say — and they are distinguishable, because F05 explaining a refinement from
a value the traveller never actually held would be a lie. One history, in the order the contradictions
happened.

**Ports consumed:** `ComponentCatalog` (F02) for `schema_for(kind_key)`.

**Ports provided:** none new — this completes `ComponentCatalog.schema_for()`. `analyse()` is a pure
domain function, deliberately not a port.

**Config/env keys introduced:** `TOURGANIZE_SCHEMA_DIR` — directory of schema files; default
`${TOURGANIZE_CONFIG_DIR}/catalog/schemas`.

**Errors/failure modes:** `SchemaError(CatalogError)` at load for malformed schemas;
`RequirementValueError` for a value that fails its field's validation (carries `field_name` and
`reason_message_key` so it can be re-asked in the traveller's language); `UnknownFieldError` when an
update names a field the schema does not declare — **not** silently ignored, because it usually means an
extraction prompt and a schema have drifted apart.

## Out of scope

Asking the questions (F05). Extracting values from free text (F08). Interpreting relative dates
(F05/F08 resolve them against the `Clock` before values reach this feature). Translating prompt message
keys into sentences (F10). Using optional filters to actually filter options (F06).

## Replaceability notes

**Must be preserved:** `FieldSpec`/`RequirementSchema`/`RequirementSet`/`GapReport` names and field
meanings; `Obligation` as exactly two values; the `any_of` blocking-rule model; `is_plannable` as *the*
gate the dialogue reads; immutability of `RequirementSet` and the merge precedence order; that unknown
fields raise.

**Free to change:** the YAML dialect and file layout; how validators are registered per `FieldKind`; the
`digest()` algorithm; whether `superseded` is a tuple or a richer history object; adding new
`FieldKind`s (additive, no consumer changes).

## Definition of done

- [ ] `tourganize catalog gaps --kind lodging` with an empty set reports both blocking rules (`where`,
      `when`) and all five optional fields; with `--set '{"place":"Paris","date_range":"2026-10-23/2026-10-28"}'`
      it reports `is_plannable = true` and still lists the optional fields.
- [ ] A test proves the client's own rule: `{"check_in": ..., "check_out": ...}` satisfies the `when`
      rule **without** `date_range`, and `{"check_in": ...}` alone does not.
- [ ] `tourganize catalog validate` exits 3 for fixtures with: a blocking rule naming an undeclared
      field, a field with no `prompt_message_key`, an enum field with no values, and a schema whose
      `component_kind` disagrees with the catalog.
- [ ] Unit tests cover every `FieldKind` validator including failures (reversed date range, score out of
      constraints, money with no currency), and each `RequirementValueError` names its field.
- [ ] Merge precedence is tested for all four sources, including that a `USER` value overwrites an
      `INFERRED` one, an `INFERRED` value does **not** overwrite a `USER` one, and the replaced value
      appears in `superseded` — as `REPLACED`, distinguishable from the `OVERRULED` entry the losing
      incoming value leaves.
- [ ] `RequirementSet.with_updates` is proven not to mutate the receiver (identity and content check).
- [ ] `analyse()` returns `invalid` (not `blocking`) for a present-but-invalid blocking value, and
      `is_plannable` is false in that case — while an invalid *optional filter* is reported with
      `blocks = false` and leaves `is_plannable` true.
- [ ] `digest()` is proven not to collide for `{"a": "b\nc=d"}` against `{"a": "b", "c": "d"}`.
- [ ] Adding a new optional field to `lodging.v1` requires no Python change; a test with a fixture schema
      proves it.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass; `tourganize doctor` and `catalog show` still work.

## Open questions / risks

- **Implementer's call:** validator registration mechanism; whether `RequirementUpdate` carries a raw
  string alongside the parsed value (recommended: yes, for error messages); the `digest()` hash.
- **Risk:** the temptation to interpret relative dates here. It would pull the `Clock` — and soon a
  locale calendar — into the pure domain. Keep resolution at the interpretation boundary.
- **Risk:** schema/prompt drift. F08's extraction prompts must be generated from, or checked against,
  these schemas; the `UnknownFieldError` behaviour is what surfaces the drift instead of hiding it.
- **Open:** whether optional-field questions should be capped per turn (see F05's asking policy) — a
  dialogue concern, noted here so the Gap Report's ordering stays sufficient for it.
