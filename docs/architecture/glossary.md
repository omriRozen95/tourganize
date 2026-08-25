# Glossary — Tourganize Ubiquitous Language

This file is the naming authority. Every other document, module name, class name, config key and
feature title uses **exactly** these terms. If a term is missing here, it does not exist yet: add it
here first, in the feature that introduces it.

Rules that produced this list:

- Name by **role**, never by vendor or technology. The port is `LlmGateway`; `ClaudeCodeBackend` is
  one adapter behind it.
- No core type may name a concrete travel topic. There is no `HotelSearch`, no `FlightPlanner`.
  Flights, lodging and ground transport are **data** in the Component Catalog.
- English is the code and documentation language. Hebrew is a first-class **content** language.

---

## 1. Domain — trip planning

| Term | Meaning |
|---|---|
| **Tourganize** | The application. Python distribution and root package name: `tourganize`. |
| **Trip Plan** | Aggregate root of the planning domain: the whole plan being assembled for one traveller conversation. Owns its Plan Components and their Selections. |
| **Plan Component** | One plannable topic instance inside a Trip Plan (the traveller's lodging, their air travel, …). Holds a Requirement Set, the history of Option Slates, and at most one Selection. **This is the generic abstraction** the client asked for — never subclassed per topic. |
| **Component Kind** | The *type* of a Plan Component, declared as data in the Component Catalog. Identified by a `kind_key` such as `lodging`, `air_travel`, `ground_transport`. New kinds are configuration, not code. |
| **Component Catalog** | The declarative registry of Component Kinds: their keys, display message keys, priority weight, outcome dependencies, and the key of their Requirement Schema. |
| **Component Status** | Lifecycle of one Plan Component: `PENDING → ELICITING → READY → SOURCING → AWAITING_CHOICE → SELECTED`, plus the terminal `DECLINED` and `FAILED`. |
| **Requirement Schema** | Per-Component-Kind declaration of the fields that describe what the traveller wants: each field's type, its Obligation, and the message key of the question that asks for it. |
| **Field Spec** | One field inside a Requirement Schema. |
| **Obligation** | A Field Spec is either `blocking` (planning that component may not start until it is known) or `optional` (a filter; asked for opportunistically, never blocking). |
| **Requirement Set** | The values collected so far for one Plan Component, each carrying its Provenance. Immutable; refinement produces a new Requirement Set. |
| **Requirement Value** | One value plus how it was obtained (`user`, `inferred`, `default`, `carried_over`) and the turn index it arrived on. |
| **Gap** | A Field Spec of a Component Kind for which the Requirement Set holds no value. |
| **Gap Report** | The Gaps of one Plan Component split into `blocking` and `optional`. A component is **Plannable** when its blocking list is empty. |
| **Planning Agenda** | The ordered queue of Component Kinds still to be planned for a Trip Plan, produced by the Priority Policy. Recomputed after every turn — never a fixed list. |
| **Mentioned-First Rule** | Hard, non-overridable ordering rule: Component Kinds the traveller raised explicitly are planned before Kinds they never mentioned. |
| **Priority Policy** | The replaceable policy that orders the Agenda *within* the mentioned and unmentioned bands, from declared priority weights and outcome dependencies. Data, not branches. |
| **Outcome Dependency** | A declaration that one Component Kind reads another's Selection (lodging dates follow the chosen air travel). Constrains the Agenda; never a hard requirement to plan the other kind. |
| **Option Slate** | The short list of Plan Options presented to the traveller for one Plan Component in one round. Numbered rounds; the history is kept. |
| **Plan Option** | One candidate on a Slate: structured facts (price, times, rating, references) plus Provenance. Holds **no prose** — wording is produced at presentation time in the traveller's locale. |
| **Provenance** | Where a Plan Option or Knowledge Passage came from: source id, retrieval timestamp, external reference, optional Citations. |
| **Selection** | The Plan Option the traveller accepted for a Plan Component, with the turn it was chosen on. |
| **Refinement** | A traveller turn that supplies corrections or extra detail instead of a choice. Enriches the Requirement Set and triggers a new Slate for the *same* component. |
| **Choose-or-Refine Loop** | The core interaction: present a Slate → the traveller selects (advance) or refines (re-source the same component). Runs an unbounded number of rounds. |
| **Proactive Offer** | The assistant's suggestion to plan a Component Kind the traveller never mentioned, once the mentioned ones are settled. Accepting extends the Agenda; declining marks that Kind `DECLINED`. |
| **Plan Completeness** | Derived summary of a Trip Plan: which Kinds are selected, declined, still open. Gates the closing summary. |

## 2. Dialogue

| Term | Meaning |
|---|---|
| **Planning Session** | The conversation aggregate: identity, transcript, locale state, Dialogue State, and the Trip Plan under construction. The unit that is persisted and resumed. |
| **User Turn** | One inbound traveller utterance with its arrival time, index and optional locale hint. |
| **Turn Interpretation** | The structured reading of a User Turn: intent, mentioned Kinds, requirement updates, an optional chosen-option reference, detected Locale Tag, confidence. Produced by a **Turn Interpreter**. |
| **Turn Interpreter** | Port that turns free text into a Turn Interpretation. First adapter is keyword-based and deterministic; later adapters call the LLM Gateway. |
| **Dialogue State** | The explicit state of the Planning Session's state machine (`GREETING`, `ELICITING_BLOCKING`, `PRESENTING_SLATE`, `AWAITING_CHOICE`, `REFINING`, `OFFERING_UNMENTIONED`, `SUMMARISING`, `CLOSED`, …). |
| **Dialogue Director** | The application service that owns the state machine: it consumes a Turn Interpretation, mutates the Planning Session, and emits Assistant Acts. Contains all control flow; contains no wording and no I/O. |
| **Assistant Act** | An intent-to-communicate emitted by the Director (`ask_blocking`, `present_slate`, `offer_unmentioned`, `deliver_summary`, …) with a structured, locale-neutral payload. The Presentation Surface renders it; the Language Services phrase it. |
| **Transcript** | The ordered record of User Turns and Assistant Acts for a Planning Session. |
| **Turn Ledger** | The per-turn observability record: Dialogue State before/after, LLM calls, tool calls, tokens, latency, cost. |

## 3. Language services

| Term | Meaning |
|---|---|
| **LLM Gateway** | The single port through which anything reaches a language model. Two call shapes only: **Extraction Call** (text → schema-validated structure) and **Composition Call** (structure → traveller-facing text). |
| **Extraction Call** | A Gateway request naming a Prompt Template and a JSON Schema; the response is validated against that schema before it is allowed into the domain. |
| **Composition Call** | A Gateway request that produces natural-language text in a named Locale Tag from a structured payload. |
| **Prompt Template** | A versioned, on-disk template with declared variables and an expected output schema. Prompts are never inline strings in Python. |
| **Prompt Library** | The loader and version registry for Prompt Templates. A Prompt Set Version is recorded on every Turn Ledger entry. |
| **Backend** | One adapter behind the LLM Gateway: the Fake Backend, the Claude Code Backend, the Hosted Model Backend. Selected by config. |
| **Backend Parity** | The property that all Backends satisfy the same Gateway contract, proven by one shared conformance suite. |
| **Locale Tag** | BCP-47-style language tag used throughout: `en`, `he`. Carries writing direction (`ltr` / `rtl`). |
| **Language Detector** | Port that assigns a Locale Tag to a turn, including mixed-script turns. |
| **Bidi Shaping** | Converting logical-order bilingual text into correctly ordered visual output for a target surface (terminal, typeset document). |
| **Message Catalogue** | The locale-keyed store of assistant phrasings that must not be left to the model (labels, units, fixed prompts), keyed by message key. |

## 4. Option sourcing and the outside world

| Term | Meaning |
|---|---|
| **Option Source** | Port that answers an Option Query with candidate Plan Options for one or more Component Kinds. |
| **Option Query** | The request handed to an Option Source: Component Kind, the Requirement Set, the Slate size, the Locale Tag, and any Selections it may read via Outcome Dependencies. |
| **Fixture Provider** | An Option Source adapter serving recorded/synthetic option data from disk. Exists from the first slice and remains the test default forever. |
| **Live Provider** | An Option Source adapter calling a real commercial travel API. Late, optional, one feature per provider. |
| **Tool Broker** | Port for invoking named external capabilities with structured arguments and receiving structured results. The MCP consumer is its first adapter. |
| **Tool Call / Tool Result** | One invocation through the Tool Broker and its outcome, both recordable as fixtures. |
| **World Capability** | A named capability reachable through the Tool Broker (`search_air_travel`, `assess_feasibility`, …). Capability names are configuration. |
| **Cassette** | A recorded Tool Call/Result pair replayed in tests so world access is deterministic. |
| **Feasibility Assessment** | The verdict of the local MCP service on whether a combination of Plan Options is physically coherent (connection times, date coverage, distances, budget roll-up). |

## 5. Knowledge augmentation

| Term | Meaning |
|---|---|
| **Supplementary Document** | A traveller- or operator-supplied document (an airline's fare rules, a visa leaflet) whose content must reach the model. |
| **Knowledge Corpus** | The registered set of Supplementary Documents plus their ingestion metadata. |
| **Passage** | An ingested, retrievable slice of a Supplementary Document with its source anchors. (The chunking unit; "chunk" is an implementation word, "Passage" is the domain word.) |
| **Knowledge Retriever** | Port returning ranked Passages for a Knowledge Query. Its adapters may retrieve (vector search) or recall (a tuned model) — the port does not care. |
| **Citation** | The pointer from an assistant statement back to a Passage, carried into the exported document. |
| **Grounding** | Injecting retrieved Passages into a Gateway call so the answer is anchored in the Knowledge Corpus. |

## 6. Presentation and export

| Term | Meaning |
|---|---|
| **Presentation Surface** | Port for the traveller-facing surface: renders Assistant Acts, yields User Turns. The Terminal Surface is the first adapter; a Web Surface is an optional later one. |
| **Scripted Surface** | A headless Presentation Surface adapter that replays a scripted list of turns and captures Acts. The backbone of the evaluation harness. |
| **Itinerary Document** | The locale-resolved, render-ready projection of a Trip Plan's Selections: sections, facts, citations, formatting hints. Renderer-agnostic. |
| **Itinerary Renderer** | Port that turns an Itinerary Document into a Rendered Artifact for one `format_key` (`text`, `markdown`, `pdf`, …). |
| **Rendered Artifact** | Bytes plus media type plus suggested filename, as produced by an Itinerary Renderer. |
| **Export Format** | The configured `format_key`. Default `pdf`; the text/markdown renderer is the always-works fallback. |

## 7. Platform

| Term | Meaning |
|---|---|
| **Composition Root** | The single place where adapters are chosen from configuration and wired into the domain. Nothing else constructs adapters. |
| **Container** | What the Composition Root returns: one slot per port, holding the adapter chosen for this process. The only object that knows which adapters are in use. |
| **Fake** | An in-process adapter of a port, shipped by the feature that introduces the port, written so no test needs a network, a key or a GPU. A Fake's shape may never differ from the port contract. |
| **Clock** | Port for reading the current moment. Nothing calls the wall clock directly, so a session can be replayed with the timestamps it was recorded with. |
| **Telemetry Event** | One structured record handed to the Telemetry Sink: a `kind`, an optional session id, the moment it occurred, and a free `fields` mapping. The Turn Ledger is a `kind` of Telemetry Event, not a second mechanism. |
| **Secret Value** | The wrapper every secret is held in from the moment Settings is built. It redacts in `repr`, `str` and `format`; reading the real value takes an explicit `reveal()` call, which makes every use of a secret greppable. |
| **Doctor** | The `tourganize doctor` command: resolved Settings with secrets redacted, the selected adapters, and a pass/fail line per wired port. The first thing to run in a new environment. |
| **Tourganize Error** | The root of the exception hierarchy. Every error the application raises deliberately derives from it, so a surface can tell a modelled failure from a bug. Its three children name the three ways things go wrong: **Configuration Error** (settings could not be resolved; exit code 3), **Port Unavailable Error** (a port has no adapter wired, or its adapter cannot be reached) and **Contract Violation Error** (data crossing a port boundary did not satisfy the declared contract). No feature derives from bare `Exception`. |
| **Log Context** | Fields bound around a block of work — `session_id`, `turn_index` — that attach themselves to every log record emitted inside it. Correlation without threading a logger through call signatures, so two contexts can be traced together without either knowing about the other. |
| **Settings** | The typed, validated configuration object. All keys are `TOURGANIZE_*` environment variables with documented defaults. |
| **Session Repository** | Port for persisting and reloading Planning Sessions (and thereby Trip Plans). |
| **Telemetry Sink** | Port receiving Turn Ledger entries and other structured events. |
| **Model Service** | The self-hosted HTTP service that serves the open-weights model on the GPU host. Its wire contract is defined independently of the web framework serving it. |
| **Inference Engine** | The library actually executing the model inside the Model Service (vLLM, llama.cpp, …). Swappable behind the Model Service contract. |
| **Golden Conversation** | A stored scripted transcript plus its expected Assistant Acts, used to pin conversational behaviour without a live model. |

---

## 8. Names that are forbidden on purpose

Using any of these is a review failure — each one hardcodes a decision we deliberately kept open.

| Do not write | Write instead | Why |
|---|---|---|
| `HotelSearcher`, `FlightPlanner`, `CarRentalService` | `OptionSource` + a `kind_key` | Component Kinds are data; four hardcoded branches is the thing we are avoiding. |
| `ClaudeClient`, `ClaudeService` in core | `LlmGateway` (port), `ClaudeCodeBackend` (adapter only) | The interim backend must be swappable by config. |
| `PdfWriter`, `PdfExporter` | `ItineraryRenderer` (port), `TypesetRenderer` (adapter) | PDF is the default format, not the only one. |
| `FlaskApi`, `FlaskServer` | `ModelService` (contract), `model_service.flask_app` (adapter) | The framework is scheduled to change. |
| `RagStore`, `RagPipeline` | `KnowledgeRetriever`, `KnowledgeCorpus` | The ingestion path may become model tuning. |
| `TuiApp` as the domain's entry point | `PresentationSurface` port, `TerminalSurface` adapter | A GUI surface must be addable without touching the Director. |
| `chunk` (domain code) | `Passage` | Keeps the retrieval mechanism out of the domain vocabulary. |
| `topic`, `part`, `section`, `leg` for a plannable topic | `Plan Component` / `Component Kind` | One word, held everywhere. |
