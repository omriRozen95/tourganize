# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is right now

**A specification repository whose planning domain is complete, driven by a conversation, and now
sourcing real option data.** F01–F06 have landed: there is an installable `tourganize` package, a test
suite, a CPU-only container and CI; a Trip Plan made of Plan Components whose *types* are declared as
data in `config/catalog/components.yaml`; per kind, a Requirement Schema in `config/catalog/schemas/`
saying what has to be known before that component can be planned; a Planning Agenda that answers "what
do we plan next" — mentioned Component Kinds first, then the rest, each band ordered by a replaceable
Priority Policy; a **Dialogue Director**, the explicit state machine that resolves blocking questions
before sourcing, presents Option Slates, runs the unbounded choose-or-refine loop, offers the Kinds
nobody mentioned and closes the session on their answer; and, behind its `OptionSlatePlanner` seam, a
real **Planning Service** over the `OptionSource` port — Fixture Providers reading
`fixtures/options/<kind_key>/*.json`, merged, soft-filtered, ranked and truncated to a slate. It emits
**Assistant Acts** and consumes **Turn Interpretations** through a port, so a deterministic keyword
interpreter drives it today and F08 replaces that by config. Working commands are `tourganize
--version`, `doctor`, `catalog show`, `catalog validate`, `catalog gaps`, `catalog agenda` and
`options search`; there is no surface yet, so the dialogue is driven from the tests until F07 wires
`chat`. The remaining 19 feature specs are still the plan, implemented one at a time, in order.

Four kinds of file, with different rules:

| Path | What it is | Rule |
|---|---|---|
| `project_demands.md` | The client's original words | **Never edit.** Source of truth for *intent*. |
| `architecture_brief.md` | The normalised brief that commissioned `docs/` | Do not edit. Source of truth for the *form* of the deliverable. |
| `docs/**` | The deliverable | Edit freely, but honour the consistency rules below. |
| `tourganize/**`, `tests/**`, `config/**`, `fixtures/**`, `docker/**` | The implementation | Governed by the feature file it belongs to plus the invariants below. |

The next thing to build is `docs/features/F07-presentation-surface-and-terminal-shell.md`. Read
`docs/roadmap.md` before starting any implementation work.

## Reading order for a new session

1. `docs/roadmap.md` — phases, dependency graph, ordered table, critical path. **Authoritative on
   dependency edges.**
2. `docs/architecture/glossary.md` — the ubiquitous language. **Authoritative on naming.**
3. `docs/architecture/overview.md` — contexts, ports, per-turn data flow, C1–C14 traceability, open
   client questions.
4. `docs/architecture/decisions.md` — D1–D19, each with cost and reversal path.
5. The one feature file you are implementing, plus the files of its declared dependencies.

A feature file is designed to be self-sufficient: implement from its Scope and Contract sections, and
treat its Definition of Done as the acceptance criteria.

## Commands

### Spec-integrity checks (after editing `docs/`)

The docs carry invariants that are easy to break by hand. Re-run these after editing anything in
`docs/`:

```bash
# roadmap edges must equal feature-file "Depends on", and numbering must stay topological
python3 - <<'PY'
import re, pathlib
spec = {int(f.name[1:3]): sorted({int(m) for m in re.findall(r"F(\d\d)",
        re.search(r"- \*\*Depends on:\*\*(.*)", f.read_text()).group(1))})
        for f in pathlib.Path("docs/features").glob("F*.md")}
road = dict((int(a[1:]), sorted({int(m) for m in re.findall(r"F(\d\d)", b)}))
        for a, b in re.findall(r"^\| (F\d\d) \| \[[^\]]+\]\([^)]+\) \| ([^|]+) \|",
        pathlib.Path("docs/roadmap.md").read_text(), re.M))
print("edge mismatches:", [f"F{n:02d}" for n in spec if spec[n] != road.get(n)] or "none",
      "| forward deps:", [f"F{n:02d}" for n in spec if any(d >= n for d in spec[n])] or "none")
PY

# every internal doc link resolves
cd docs && grep -rhoE '\]\([^)]+\.md[^)]*\)' . | sed -E 's/\]\(([^)#]+).*/\1/' | sort -u \
  | while read -r l; do [ -f "./$l" ] || [ -f "architecture/$l" ] || [ -f "features/$l" ] \
      || echo "UNRESOLVED: $l"; done
```

Every feature file must keep all of: `- **Bounded context:**`, `- **Depends on:**`, `- **Unlocks:**`,
`- **Size:**`, `- **Status of the codebase when this starts:**`, `## Purpose`, `## Starting state`,
`## Scope — what to implement`, `## Contract (the Lego connectors)` (with `**Inputs:**`,
`**Outputs:**`, `**Ports consumed:**`, `**Ports provided:**`, `**Config/env keys introduced:**`,
`**Errors/failure modes:**`), `## Out of scope`, `## Replaceability notes`,
`## Definition of done`, `## Open questions / risks`.

A parenthetical qualifier may precede the colon on `**Config/env keys introduced:**` — F16, F20 and
F24 use it to mark keys that are deliberately *not* `TOURGANIZE_*` (see Naming discipline). Match
that heading by prefix, not exactly.

### Code gates (all four run in CI, on 3.11 and 3.12)

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .     # lint + format
mypy --strict tourganize                  # type gate
lint-imports                              # import-linter: the DDD boundary enforcement
pytest                                    # full suite
pytest tests/unit/test_settings_defaults.py::test_every_documented_default            # single test
tourganize doctor                         # resolved settings, adapter selection, per-port health
tourganize catalog show                   # the declared Component Kinds; `validate` exits 0 or 3
tourganize catalog gaps --kind lodging    # the Gap Report; `--set '<json>'` supplies known values
tourganize catalog agenda --mentioned lodging   # the Planning Agenda: bands, ranks, reason codes
tourganize options search --kind lodging \
  --set '{"place":"Paris","date_range":"2026-10-23/2026-10-28"}'   # a real Option Slate
docker compose --profile dev-cpu run --rm app tourganize doctor
```

`lint-imports` must be on `PATH` for `tests/architecture/test_import_linter_enforcement.py` to run
rather than skip — that is the test which proves the gate rejects a planted violation.

`lint-imports` is not optional tooling — it is the mechanism that keeps the domain dependency-free.
If a contract has to be weakened to make a feature compile, that needs an ADR entry, not a config
tweak.

Commands added by later features (each defined in its own spec): `chat` (F07), `llm probe` (F08),
`messages lint` (F10), `eval` / `eval report` / `eval parity` (F11, F21), `sessions` / `resume` (F12),
`export` (F13), `tools list|call` (F15), `docs add|list|query|index` (F18, F19). Run one golden
conversation with `tourganize eval --only <conversation_id>`.

## Architecture: the two rules everything follows

1. **The domain imports nothing.** `tourganize/domain/` may import the standard library, itself and
   `tourganize/dialogue/` — never `tourganize/ports/`, never `tourganize/platform/`, and never an HTTP
   client, LLM SDK, MCP, PDF library, terminal library or database driver. `tourganize/dialogue/` has
   exactly one permission more: it may import `tourganize/ports/`, because the Dialogue Director's
   constructor names four port types (D17). `tourganize/ports/` in turn may import only the standard
   library and those two pure packages, so the extra permission admits nothing external.
2. **Everything external enters through a port** — an abstract protocol in `tourganize/ports/` with at
   least one fake. Adapters are selected from `Settings` in exactly one place:
   `tourganize/application/composition.py`.

Six bounded contexts: **Trip Planning** (pure core), **Dialogue**, **Language Services**, **Option
Sourcing**, **Knowledge Augmentation**, **Presentation & Export**. `docs/architecture/overview.md` §2
states what each owns *and* what it is forbidden to know.

### The central abstraction

A **Plan Component** identified by a `kind_key`, typed by a **Component Kind** declared as data in
`config/catalog/components.yaml`, whose requirements are declared as data in
`config/catalog/schemas/<schema_key>.yaml`, and whose *options* are declared as data in
`fixtures/options/<kind_key>/*.json`. Flights, lodging and ground transport are *configuration*, not
classes and not `if` branches. Adding `dining` must require zero Python changes — F02, F03, F06 and
F13 each carry a test that proves it (`tests/integration/test_fourth_component_kind.py` is F06's),
and F02's DoD asserts that grepping `tourganize/` for topic strings returns nothing.

### One turn, end to end

`PresentationSurface` → `UserTurn` → `TurnInterpreter` (keyword adapter, later LLM-backed) →
`TurnInterpretation` → `DialogueDirector` (explicit state machine) → requirement merge, agenda rebuild
→ either `ask_blocking` or `PlanningService` → `OptionQuery` → the registered `OptionSource`s, called
serially → merge, soft filter, rank, truncate → `OptionSlate` → `AssistantAct` → surface.

The Director emits **Assistant Acts** — structured, locale-neutral intents to communicate. It contains
all control flow and no wording; the surface and Language Services turn Acts into sentences.

### Ports and the feature that introduces each

`Clock`, `TelemetrySink` (F01) · `ComponentCatalog` (F02, `schema_for` completed by F03) ·
`PriorityPolicy` (F04, declared in the domain and re-exported by `ports/catalog.py`, because
`build_agenda` consumes it and the domain may import nothing) · `TurnInterpreter`,
`OptionSlatePlanner` (F05, declared in `dialogue/ports.py` and re-exported by
`ports/interpretation.py`, because their contracts are typed with Dialogue value objects — D17) ·
`OptionSource`, `OptionSourceRegistry`, `OptionRanking` (F06, declared in `ports/options.py`;
`OptionQuery`/`OptionSourceResult` are defined in `domain/options/query.py` and re-exported, because
they carry a `Selection` and `domain/trip` already imports `domain/options`) ·
`PresentationSurface` (F07) · `LlmGateway` (F08) · `LanguageDetector` (F10) ·
`SessionRepository` (F12) · `ItineraryRenderer` (F13) · `ToolBroker` (F15) ·
`KnowledgeCorpus`, `TextExtractor`, `PassageSplitter` (F18) · `KnowledgeRetriever`, `EmbeddingModel` (F19).

Contract suites in `tests/contracts/` are parametrised over *every* adapter of a port, including fakes.
A new adapter is done when that suite passes unmodified.

## Invariants that must not be broken

These are the client's stated rules and the reasons the design is shaped as it is. Each has a test that
guards it; if one starts failing, fix the code, not the test.

- **Mentioned-First** is a hard rule and lives in `build_agenda`, never in the `PriorityPolicy` — a
  replacement policy must be *unable* to violate it. `PlanningAgenda` refuses an interleaved sequence
  of entries outright, and a policy is handed one band at a time, so it never learns another exists.
  A policy that invents, drops or repeats a `kind_key` is refused at the seam with
  `ContractViolationError`: replaceable means checked, not trusted.
- **Outcome dependencies are soft.** `requires_outcome_of` constrains ordering only, and only while
  the kind it names is open *in the same agenda band*. A traveller who wants only a hotel is never
  blocked waiting on flights they never mentioned. `awaited_within` is that rule, in one place, and
  like Mentioned-First the *ordering* it implies is applied in `build_agenda` and never in a
  `PriorityPolicy` — order and `blocked_by` are computed from one answer, so no policy can make them
  disagree (D16).
- **Blocking gaps are resolved before sourcing; optional filters never block**, are asked at most once,
  and are bundled (max 2) alongside the first slate. A blocking obligation may be satisfied in more
  than one way — `BlockingRule.any_of` is a list of field groups, never a per-field flag — and a
  present-but-invalid value is reported as `GapReport.invalid`, never as a missing one.
- **A Requirement Set is immutable and nothing is dropped.** `with_updates` returns a new set; the
  merge precedence is `USER` > `INFERRED` > `CARRIED_OVER` > `DEFAULT`, later turn wins within a rank,
  and the losing value is kept in `superseded`. An update naming an undeclared field raises
  `UnknownFieldError` rather than being ignored — that is what surfaces prompt/schema drift.
- **Relative dates are resolved before the domain sees them.** F03 accepts only resolved values;
  "next month" is the interpretation layer's problem, against the `Clock` (F05/F08).
- **One blocking question per Act.** After `TOURGANIZE_DIALOGUE_MAX_REASKS` asks on the same Blocking
  Rule the Director offers the field's example instead, and then marks the component `FAILED` and moves
  on rather than looping. *Which* of a rule's candidate groups to pursue is asking policy and lives in
  the Director: the cheapest group to finish, ties broken by declaration order.
- **The choose-or-refine loop is unbounded** — refinement re-sources the *same* component with an
  incremented `round_index`, and slate history is never discarded.
- **Proactive offers start only when the mentioned band is empty**; a declined kind is never offered
  again in that session.
- **No prose in the domain.** `PlanOption` has no `title`/`description`; Act payloads carry message keys
  and structured data. All wording comes from the Message Catalogue or an LLM Composition call.
- **Logical text order everywhere except the terminal boundary.** Bidi shaping is applied only in the
  terminal surface; exports hand logical order to the typeset engine, and the web surface (F25) must
  never call the shaper.
- **Extraction output is schema-validated before it enters the domain**, with one bounded repair retry.
- **The `LlmGateway` is serial.** No caller may assume parallel fan-out (the Claude Code adapter is one
  process per call).
- **Fixtures and fakes stay the test default forever**, even after live providers exist, and a fixture's
  shape may never differ from the port contract — `tests/contracts/test_option_source_contract.py` is
  what makes that enforceable, and it carries deliberately broken adapters proving it bites.
- **Sourcing degrades, it never dies.** One source of several failing is a diagnostic and a source
  skipped; only *every* source for a Kind failing raises `OptionSourcingError`, which the Director
  turns into `report_sourcing_failure` without ending the session. An empty slate is not an exception
  at all. A query nothing matches is answered with a deterministic synthetic set marked `synthesised`.
- **Optional filters are soft and declared as data.** They demote and annotate, never discard, unless
  `TOURGANIZE_OPTION_FILTER_STRICT=true`; *which* option fact a field filters on is two keys in that
  field's `constraints` (D19), so a fourth Component Kind's filters are configuration too. A Filter
  Note is a field name, never a reason.
- **The same query yields a byte-identical slate**, in any process: sources are seeded by
  `requirements.digest()`, the query's `request_id` is derived rather than generated, and the ranking's
  last tie-break is the `option_id`. F11's replay rests on it.
- **Feasibility findings are advisory** — they annotate and demote options, they do not silently filter
  them.
- **The state machine is a table, and every move goes through one guard.** `TRANSITIONS` in
  `tourganize/dialogue/states.py` is the whole machine; an undeclared move raises
  `IllegalDialogueTransitionError`, and no state in the table is unreachable from `GREETING`.
- **A turn after `close` raises `SessionClosedError`.** Resuming a conversation is F12's `resume`,
  never a silent reopen.
- **One Turn Ledger entry per `handle()`**, carrying the Dialogue States either side of the turn, the
  Turn Intent, the focus `kind_key`, the Acts emitted and `PlanningAgenda.explain()`.
- **Every feature leaves the app runnable**, with previously working paths unaffected.

## Naming discipline

The glossary is the authority; use its terms in module names, class names, contract fields, config keys
and feature titles. Name by **role, not vendor**: `LlmGateway` (port) vs `ClaudeCodeBackend` (adapter);
`ItineraryRenderer` vs `TypesetRenderer`. `docs/architecture/glossary.md` §8 lists names that are
forbidden on purpose (`HotelSearcher`, `ClaudeClient`, `PdfWriter`, `FlaskApi`, `RagStore`, `chunk` in
domain code, …) with the term to use instead — using one is a review failure, because each hardcodes a
decision that was deliberately kept open.

Other conventions: every environment variable is `TOURGANIZE_*` with a documented default, loaded only
through `Settings.from_env` (service-side variables in `services/` use their own prefixes, e.g.
`MODEL_*`, `FEASIBILITY_*`). Secrets live in `SecretValue` wrappers that redact in `repr`/`str`.
English is the code and documentation language; Hebrew is a first-class *content* language.

## When editing the docs

- `docs/roadmap.md` is authoritative on dependency edges. If a feature file disagrees, fix the feature
  file — and keep feature numbering a valid topological order (`F07` may never depend on `F11`).
- New vocabulary goes into the glossary in the same change that introduces it.
- Reversing or weakening a decision (including an import-linter contract) means a new or amended entry
  in `docs/architecture/decisions.md` with rationale, cost and reversal path.
- Feature files must stay self-contained enough to hand to an implementer alone, and every Definition of
  Done item must be observable by running something.

## Deferred and blocked work

F22 (FastAPI migration), F23 (Unsloth tuning), F24 (live providers) and F25 (web surface) are an
explicitly optional track — nothing in F01–F21 depends on them. **F24 cannot start** until the client
answers whether real provider accounts and terms of use will exist. The seven open client questions in
`docs/architecture/overview.md` §9 all have a recommended default in force; none of them block work.
Hardware note: the GPU line-up in `project_demands.md` is believed to be misdescribed (the RTX 3090 Ti
is Ampere, not TU102) — D11 records the assumption we are proceeding on and F20's DoD requires replacing
it with measured numbers.
