# Tourganize — Architecture Design Brief

**Audience:** the LLM/agent that will produce the architecture and the feature specs.
**Source of truth for intent:** `project_demands.md` (the client's original words — do not edit it).
**Status of this document:** normalized, executable restatement of that file, with gaps closed and defaults proposed. Where this brief and `project_demands.md` appear to conflict, `project_demands.md` wins on *intent*; this brief wins on *form of the deliverable*.

---

## 0. Your job in one paragraph

Design a Python application called **Tourganize** (tour + organize) and express that design as a set of Markdown files: one overview, one glossary, one ordering/dependency file, and one file per building block ("feature"). Each feature file must state its starting state, what is to be implemented, its input/output contract, and its definition of done. The client will then implement the features **one at a time, in the order you define** — so the decomposition, not the prose, is the real product.

**You are not writing the application.** Deliverable = Markdown only. Code fences are welcome, but only for *contracts*: function/class signatures, JSON schemas, directory trees, config samples, CLI/API shapes. No implementations, no scaffolding, no `requirements.txt` pinning exercise.

---

## 1. The product

A conversational assistant that plans trips. The user states what they want ("find me a hotel in Paris between the 23rd and 28th of October"); the system interviews them for what is missing, plans the trip **component by component**, offers a short list of options per component, lets the user pick or push back, and finally emits a written plan (PDF by default).

Essential behaviours:

1. **Bilingual input/output** — English and Hebrew (Hebrew implies RTL rendering and bidi-safe export).
2. **Component-by-component planning** — flights, lodging, ground transport / car rental, and further components later. Components are planned in a deliberate order, not all at once.
3. **Mentioned-first ordering** — components the user explicitly raised are handled before components they did not. Ordering among the rest follows a priority metric that is *not yet defined* (see §7, D3) and must be a replaceable policy, not hardcoded.
4. **Proactive offers** — after finishing the mentioned components, the assistant may offer to plan the unmentioned ones; the user's answer continues or ends the conversation.
5. **Per-component requirement elicitation** — every component owns its own set of *mandatory* fields (blocking: e.g. some date range must exist) and *optional* filters (e.g. budget ceiling, minimum review score). Blocking gaps must be resolved before planning that component; optional ones are asked for opportunistically, never blocking.
6. **Choose-or-refine loop** — options are presented, the user either selects one (advance to the next component) or supplies corrections/extra detail (re-plan the same component with the enriched requirements). This loop must be able to run any number of times.
7. **Final summary export** — the accepted selections are rendered to a document in a configured format; default PDF, format pluggable.

---

## 2. Hard constraints from the client

| # | Constraint | Notes for you |
|---|---|---|
| C1 | Python project | — |
| C2 | Domain-driven design | Bounded contexts, ubiquitous language, ports & adapters. See §4. |
| C3 | Every feature is a "Lego piece" | Well-defined input and output; easy to compose; easy to *replace* with a better version. |
| C4 | Gradual build, explicitly ordered | Never "everything at once". Each feature must be implementable and verifiable on its own. |
| C5 | Runs inside Docker | Application container(s); GPU-bearing container for the model server when that arrives. |
| C6 | Production host: 3 usable GPUs — 1× GeForce RTX 3090 Ti, 2× Quadro RTX 6000 | See §8 for the hardware facts that constrain model choice. |
| C7 | The LLM must ultimately be an open-weights model from Hugging Face | — |
| C8 | Interim LLM: Claude Code (the client has a subscription) | The Claude-mediated conversation must *mimic* the eventual OSS-model conversation, i.e. both sit behind the same port. Swapping them is a config change, not a refactor. |
| C9 | The OSS model is reached over an HTTP API — Flask or FastAPI; starting with Flask and upgrading later is acceptable | If you start on Flask, the upgrade must be its own feature with its own DoD. |
| C10 | Supplementary documents must be usable by the model (e.g. an airline's fare rules) — via RAG (chunked) or via Unsloth fine-tuning | Both paths are named by the client; you decide which is the first-class path and which is optional/later (recommendation in §7, D6). |
| C11 | "World" information (live flights etc.) is reached via MCP, using FastMCP | Consumer side is mandatory. |
| C12 | At least one **local** MCP service, and you must propose ideas for what it should be | Deliver 3–5 concrete candidates with a recommended first one. |
| C13 | UI is GUI **or** TUI | Client left it open. See §7, D1. |
| C14 | Application name is "Tourganize" | Use it in module/package naming. |

---

## 3. Naming and language ("Lingo")

The client asked for names that are clear and informative **but not over-restricting**. Concretely:

- Establish a **glossary** first and use exactly those terms everywhere afterwards — file names, module names, feature titles, contract fields.
- Name by **role, not by vendor or technology**. `LlmGateway`, not `ClaudeClient`. `ItineraryRenderer`, not `PdfWriter`. `TravelDataProvider`, not `SkyscannerAdapter` (that is the name of an *adapter implementation*, which is fine).
- Name the **generic abstraction** for a plannable topic, so that flights/lodging/car-rental/dining/insurance are instances of it rather than four hardcoded branches. Pick one term (e.g. "plan component", "trip segment", "planning domain") and hold it. Do not embed the four known topics in any core type name.
- Avoid names that lock in a decision you deliberately deferred (no `FlaskApi`, no `RagStore` if the ingestion path may become fine-tuning).
- Feature titles read as capabilities ("Bilingual requirement elicitation"), not as tickets ("Fix language stuff").
- Hebrew is a first-class content language but English is the code/doc language.

---

## 4. Architecture principles you must apply

1. **Bounded contexts with explicit ubiquitous language** — separate the *conversation/dialogue* concern, the *trip planning domain* concern, the *option sourcing* (external world) concern, the *knowledge augmentation* concern, and the *presentation/export* concern. Name them; state what each owns and what it must never know about.
2. **Domain core is dependency-free** — the planning domain knows nothing about LLMs, HTTP, MCP, PDFs, or the UI. Everything external enters through a port.
3. **One port per replaceable thing.** At minimum, there must be ports for: the language model, the option/data sources, the knowledge/document retrieval, the export renderer, the user-facing presentation surface, and session persistence. Every port gets: a stated purpose, a typed interface sketch, at least one stub/fake adapter for testing, and a note on what a replacement must honour.
4. **Contract-first features.** Each feature spec's contract section is the integration surface. A downstream feature may depend only on things another feature's spec *declares*, never on its internals.
5. **Vertical slices where possible.** Prefer a thin end-to-end slice early (user turn → LLM → one component planned with stubbed data → text output) over building all infrastructure layers first. Later features deepen it.
6. **Every feature leaves the app runnable.** The definition of done includes "the application still starts and the previously working paths still work".
7. **Stubs before integrations.** Real external providers and the real GPU model land late; fakes exist from the first slices so nothing is blocked on them.

---

## 5. Deliverable: exact file set

Produce this tree (adjust only the feature file names/count):

```
docs/
  architecture/
    overview.md          # system shape, bounded contexts, ports, data flow, C4-ish diagrams (mermaid)
    glossary.md          # the ubiquitous language; every domain term, one line each
    decisions.md         # ADR log: the decisions from §7, one entry each
  features/
    F01-<slug>.md
    F02-<slug>.md
    ...
  roadmap.md             # ordering + dependency graph + phases (the file the client asked for by name)
```

Rules:
- Feature files are numbered in a **valid topological order** of their dependencies, so `F07` never depends on `F11`.
- Every feature file is **self-contained enough to hand to an implementer alone**, with links to its dependencies' files.
- `roadmap.md` and the feature files must not contradict each other; the dependency edges in `roadmap.md` are the authority.
- Use mermaid for diagrams (`graph TD`, `sequenceDiagram`). Keep each diagram under ~25 nodes.

---

## 6. Mandatory templates

### 6.1 Feature spec — `docs/features/Fnn-<slug>.md`

```markdown
# Fnn — <Capability name>

- **Bounded context:** <which context this lives in>
- **Depends on:** Fxx, Fyy (or "none")
- **Unlocks:** Fzz, ...
- **Size:** S | M | L   (S ≈ one focused sitting, L ≈ split it if you can)
- **Status of the codebase when this starts:** <what exists, what does not>

## Purpose
One paragraph: what capability the system gains, in user-visible or operator-visible terms.

## Starting state
Concretely what is already in place (modules, ports, fakes, config keys) that this feature builds on.

## Scope — what to implement
Bulleted, ordered, specific. Include the module/package paths you expect to be created or touched.

## Contract (the Lego connectors)
- **Inputs:** types/schemas, with a sketch.
- **Outputs:** types/schemas, with a sketch.
- **Ports consumed:** which interfaces this calls, and against which adapter/fake during development.
- **Ports provided:** which interfaces this newly exposes for later features.
- **Config/env keys introduced:** name, meaning, default.
- **Errors/failure modes:** what it raises or degrades to.

## Out of scope
Explicitly what this feature must not attempt (usually: things owned by later features).

## Replaceability notes
What a better/alternative implementation of this feature must preserve for the rest of the system to keep working. What is deliberately internal and free to change.

## Definition of done
Checklist of **verifiable** statements. Each item must be observable by running something (a test, a command, a container, a manual script). Include:
- functional acceptance criteria (behavioural, not "code written")
- tests that must exist and pass (unit + at least one integration/fixture-based where meaningful)
- docs/config updates
- "app still runs, previously working paths unaffected"

## Open questions / risks
Anything the implementer may legitimately decide, and anything that could invalidate this spec.
```

### 6.2 Ordering file — `docs/roadmap.md`

Must contain:
1. **Phases** — named groups of features delivering a coherent, demoable milestone each (e.g. "walking skeleton", "real dialogue", "real data", "own model", "polish/export"). One sentence on what the client can *see* at the end of each phase.
2. **Dependency graph** — mermaid `graph TD`, feature IDs as nodes.
3. **Ordered table** — `# | Feature | Depends on | Phase | Size | Why here`.
4. **Critical path** and which features are parallelizable.
5. **Deferred/optional track** — features that are explicitly "later, if wanted" (e.g. FastAPI migration, Unsloth path, second UI surface), so the client can skip them without breaking the chain.

---

## 7. Decisions you must make and record in `docs/architecture/decisions.md`

For each: state the decision, 2–4 sentence rationale, what it costs, and how it can be reversed (which feature would replace it). Recommended defaults are given — adopt them unless you have a stated reason not to.

| ID | Decision | Recommended default |
|----|----------|---------------------|
| D1 | GUI or TUI first (C13) | **TUI first**, behind a presentation port, because it is trivial to run in a container over SSH and cheap to drive from tests; a GUI/web surface becomes a later, optional adapter feature. Must handle RTL/Hebrew and multi-line option lists. |
| D2 | Dialogue control: LLM-driven or state-machine-driven | **Explicit state machine in the domain, LLM used for language tasks** (understanding a turn, extracting requirements, phrasing questions and summaries). Deterministic control flow is testable and keeps the OSS-model swap safe. |
| D3 | The "importance" metric for component ordering (undefined in the source) | Mentioned-first (hard rule), then a **declarative priority policy** — each component type declares a priority weight and its dependency on other components' outcomes (e.g. lodging dates follow flight dates). Policy is data/config, replaceable. |
| D4 | LLM interaction protocol between core and gateway | Single `LlmGateway` port with structured, schema-validated requests/responses (extraction and generation calls), so Claude Code and the future HF server are interchangeable. Prompts live as versioned templates, not inline strings. |
| D5 | How Claude Code is actually invoked as the interim backend | Decide and document (subprocess/CLI session vs. API-style call), isolate it entirely inside one adapter, and note the constraints it imposes (latency, statefulness, no parallel fan-out assumptions). |
| D6 | RAG vs Unsloth for supplementary documents (C10) | **RAG first** (ingest → chunk → embed → retrieve → inject, all behind a `KnowledgeRetriever` port); **Unsloth fine-tuning as a separate optional later feature** that supplies an alternative/complementary adapter. Reason: per-document turnaround, no retraining per upload, traceable citations. |
| D7 | Flask now or FastAPI now (C9) | Either is defensible; if Flask, the FastAPI migration is a named optional feature on the deferred track, and the model-server API surface must be defined independently of the framework. State your choice plainly. |
| D8 | Session/plan persistence | Needed but unspecified in the source: pick the simplest thing that supports resuming a conversation and re-exporting a plan (e.g. file/SQLite-backed repository behind a port). Note multi-user/auth as explicitly out of scope unless the client says otherwise. |
| D9 | Real external providers vs. stubs for options (flights/hotels/cars) | **Stubbed/fixture providers first**, real ones as separate late features behind the same port; API keys, rate limits, and terms-of-use are the client's to supply. Never let a stub's shape differ from the real port contract. |
| D10 | Export stack for PDF with Hebrew | Choose a renderer that handles bidi + an embedded font with Hebrew glyphs, and say which. Provide a plain-text/Markdown renderer as the always-works fallback. |
| D11 | Model serving stack on the given GPUs | Pick the inference server and quantization strategy consistent with §8, and name a concrete candidate model family + size band. |
| D12 | Which local MCP service is built first (C12) | Propose 3–5 candidates and pick one that is genuinely useful and testable offline. |

---

## 8. Technical realities to account for (do not skip)

- **Hardware note / likely error in the source.** The source describes all three GPUs as "TU102-based". The 2× Quadro RTX 6000 are indeed TU102 (Turing, 24 GB each); the **RTX 3090 Ti is GA102 (Ampere, 24 GB)**. This matters: Turing has no bfloat16 and weaker FP16 accumulate paths, so a mixed Turing+Ampere pool is awkward for tensor parallelism. Plan for either (a) single-GPU-per-worker serving with the model sized to fit 24 GB (quantized), or (b) tensor parallelism across the **two matched Quadros** only, leaving the 3090 Ti for embeddings/reranking/fine-tuning jobs. State the assumption; ask the client to confirm the hardware.
- **VRAM budget:** 24 GB per card. That is the real ceiling on model choice — plan around quantized weights (e.g. 4-bit/8-bit) and be explicit about context-length vs. batch trade-offs.
- **Docker + GPU:** requires the NVIDIA container runtime; separate the CPU-only app container from the GPU model-server container, with compose profiles so a developer can run the app with the Claude adapter and no GPU at all.
- **Hebrew is more than translation:** language detection per turn, mixed-language turns, RTL layout in the UI, correct bidi in the exported document, Hebrew-capable fonts, and date/number/locale formatting. Treat it as a cross-cutting concern with its own feature, not a flag.
- **Tool calls via MCP need determinism for tests:** the FastMCP consumer must be mockable, with recorded fixtures.
- **Evaluation:** conversational behaviour cannot be pinned by unit tests alone. Include a feature for a scripted-transcript / golden-conversation harness with a stubbed LLM, so that the choose-or-refine loop, the mentioned-first rule, and the blocking-question rule are all provably tested without hitting a real model.
- **Cost/latency observability:** log tokens, latency, and tool calls per turn from the first LLM feature; the client will compare Claude vs. the OSS model on this.

---

## 9. Coverage checklist

Every concern below must be owned by **some** feature (you decide the split and the names — do not treat this as the feature list):

conversation session lifecycle & turn handling · dialogue state machine and the choose-or-refine loop · language detection and bilingual/RTL handling · per-component requirement schemas with mandatory vs. optional fields · blocking-question resolution · component prioritization policy and the mentioned-first rule · proactive offers for unmentioned components · option sourcing port + stub providers · real provider adapters (later) · MCP consumer via FastMCP · at least one local MCP server · LLM gateway port · Claude Code adapter · HF/OSS model server (Flask, and the FastAPI upgrade) · prompt/template management · document ingestion + RAG retrieval · optional Unsloth path · selection & plan assembly domain model · plan summary rendering (PDF default + fallback, format configurable) · session/plan persistence · presentation surface (TUI, optional GUI later) · configuration & secrets management · logging/metrics/cost observability · test fixtures, fakes, and the conversation-evaluation harness · Docker/compose (CPU dev profile + GPU production profile) · CI/quality gates.

---

## 10. Sizing rules for features

- A feature should be completable in one focused implementation session. If it needs three, split it and say so.
- A feature must be **verifiable when it lands** — if you cannot write its definition of done as observable checks, the split is wrong.
- Prefer 12–25 features total. Fewer means they are too coarse to build gradually; many more means the ordering file becomes the bottleneck.
- No circular dependencies. No feature whose only value appears two features later without any check of its own.
- Infrastructure-only features are allowed but must earn their place by unlocking something concrete, and must be justified in one line in `roadmap.md`.

---

## 11. Self-review before you return

- [ ] Every file in §5 exists, and the feature files are numbered in topological order.
- [ ] Every feature file follows §6.1 verbatim, including a filled contract section and an observable DoD.
- [ ] `roadmap.md` has phases, mermaid graph, ordered table, critical path, and a deferred/optional track.
- [ ] Every constraint C1–C14 is traceable to at least one feature (add a short traceability table to `overview.md`).
- [ ] Every decision D1–D12 has an ADR entry with rationale, cost, and reversal path.
- [ ] Every concern in §9 is owned by exactly one feature (shared concerns explicitly noted as cross-cutting).
- [ ] Glossary terms are used consistently, and no core type name hardcodes flights/hotels/cars, Claude, Flask, or PDF.
- [ ] Phase 1 alone produces something the client can run and see.
- [ ] No implementation code was written.

---

## 12. Questions to raise rather than silently assume

Ask the client (in a short list at the end of `overview.md`) about anything you could not decide safely, and specifically about: confirmation of the GPU line-up (§8); whether real flight/hotel provider accounts and API keys will exist, or whether stubs are the permanent state; whether multi-user access, authentication, or storing personal traveller data is in scope; whether there is a target for a first demo that should shape phase 1; and whether the exported plan has any required layout/branding. Proceed with the recommended defaults in the meantime — do not block on answers.
