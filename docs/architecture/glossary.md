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
| **Component Status** | Lifecycle of one Plan Component: `PENDING → ELICITING → READY → SOURCING → AWAITING_CHOICE → SELECTED`, plus `DECLINED` and `FAILED`. `DECLINED` has exactly one edge out, `DECLINED -> ELICITING`, and only the traveller's own mention walks it ([D18](decisions.md)): declining answers an *offer*, so a kind they turn down is never **offered** again in that session, but one they later raise themselves is planned. `FAILED` is not terminal either: sourcing failures are usually transient, so a failed component may re-enter `SOURCING`, and F04 is what eventually skips one that keeps failing. The legal transitions are a table in `domain/trip/component.py`, and `advance_to` refuses everything else. |
| **Requirement Schema** | Per-Component-Kind declaration of the fields that describe what the traveller wants: each field's type, its Obligation, and the message key of the question that asks for it, plus the Blocking Rules that say when planning may start. Data, like the Component Catalog: one file per `schema_key` under `${TOURGANIZE_SCHEMA_DIR}`, reached through `ComponentCatalog.schema_for()`. |
| **Field Spec** | One field inside a Requirement Schema: its name, its Field Kind, its Obligation, the message keys that ask for it and illustrate it, the values an `enum` accepts, and its bounds. |
| **Field Kind** | The *type* of a Field Spec, and therefore which validator reads its values: `date_range`, `date`, `place`, `integer`, `money`, `score`, `text`, `enum`, `boolean`, `duration`. Adding one is additive — a table entry and a function — and no consumer changes. |
| **Obligation** | A Field Spec is either `blocking` (planning that component may not start until it is known) or `optional` (a filter; asked for opportunistically, never blocking). Exactly two values, forever; "sometimes blocking" is what a Blocking Rule is for. An optional field says **how** it filters in its own `constraints`: `filters` names the Plan Option fact to read (`price` means the option's own price) and `comparison` is `at_most` / `at_least` / `equals`. An optional field that declares neither is a preference nothing can be measured against, and demotes nothing. |
| **Blocking Rule** | One named obligation of a Requirement Schema, and every combination of fields that would satisfy it: `any_of` is a list of field groups, and the rule is met once *all* the fields of *any one* group hold a value. This is the client's own rule — "there should be some time range, if not a specific start and end date" — and it is why blocking is modelled per rule rather than per field. A group may name a field whose own Obligation is `optional`. |
| **Requirement Set** | The values collected so far for one Plan Component, each carrying its Provenance. Immutable; refinement produces a new Requirement Set, and the values that stop being in force are kept as **superseded** rather than dropped, so a contradiction can be explained. |
| **Requirement Value** | One value plus how it was obtained (`user`, `inferred`, `default`, `carried_over`) and the turn index it arrived on. The four sources are ranked: `user` overwrites anything, `inferred` overwrites only `default` and `carried_over`, and within one rank the later turn wins — as does a value arriving on the *same* turn, because two values for one field in one turn are a correction in mid-sentence. |
| **Requirement Update** | One value *offered* for one field by a turn, before the merge decides whether it wins. Carries the traveller's own words alongside the parsed value, so a re-ask can quote what was actually said. |
| **Superseded Value** | One entry of a Requirement Set's history: a Requirement Value plus how it stopped being in force — `replaced` (it was held, and something outranking it took its place) or `overruled` (it arrived and never took hold, because the standing value outranked it). The two are kept apart on purpose: explaining a refinement from a value the traveller never actually held would be a lie. |
| **Date Range** | The normalised form of a `date_range` value: two resolved, ordered, inclusive dates. Never holds a relative expression — "next month" is resolved against the Clock at the interpretation boundary, before any value reaches the domain. |
| **Gap** | Something a Plan Component still needs. A **blocking** gap is one unsatisfied Blocking Rule — not one field — carrying every candidate field group and, per group, the fields of it that still hold no value; an **optional** gap is one declared optional Field Spec the Requirement Set holds no value for. |
| **Gap Report** | What one Plan Component still needs: `blocking` (unsatisfied Blocking Rules, each carrying the field groups that would satisfy it), `optional` (declared optional fields with no value) and `invalid` (values that are present but fail their Field Kind). A component is **Plannable** when `blocking` is empty and no *blocking* value is invalid: an unusable optional filter is still reported and still re-asked, but it never holds sourcing up. `next_blocking()` is the one gap to ask about next, in the schema's declaration order; *which* candidate group to pursue is asking policy and belongs to F05. |
| **Invalid Value** | A present value that fails its Field Spec — a reversed Date Range, a score outside its bounds, an amount with no currency. It is reported *separately* from a Gap, because asking again for something the traveller already answered is not the same as telling them what was wrong with it. It says whether it **blocks**: true when a Blocking Rule reads that field, false when the field is only a filter. |
| **Planning Agenda** | The ordered queue of Component Kinds still to be planned for a Trip Plan: two Agenda Bands, each ordered by the Priority Policy and then settled against the declared Outcome Dependencies, concatenated mentioned-first by `build_agenda`. Recomputed after every turn — never a fixed list, and never stored. An **empty** Agenda is a meaningful answer: everything is settled, which is F05's cue to summarise. |
| **Agenda Band** | One of the two halves of a Planning Agenda: `MENTIONED` (Component Kinds the traveller raised) and `UNMENTIONED` (everything else). A band is ordered internally by the Priority Policy, which never sees more than one of them — that is what makes the Mentioned-First Rule unbreakable by a policy. |
| **Agenda Entry** | One Component Kind's place in the Agenda: its `kind_key`, its Agenda Band, its `rank` within that band (from zero), the Kinds it is `blocked_by`, and a Reason Code. |
| **Reason Code** | The opaque code on an Agenda Entry saying what is known about its place: `ready`, `awaits_outcome`, `not_plannable`, `failed_skipped`. Never a sentence — the domain holds no prose. The vocabulary may grow, so a consumer treats a code it does not recognise as opaque; only `failed_skipped` means "do not work on this one now". |
| **Mentioned-First Rule** | Hard, non-overridable ordering rule: Component Kinds the traveller raised explicitly are planned before Kinds they never mentioned. Lives in `build_agenda`, never in a Priority Policy, and the `PlanningAgenda` type refuses an interleaved sequence of entries outright — so a replacement policy is structurally unable to violate it. |
| **Priority Policy** | The replaceable port that orders the Agenda *within* one Agenda Band, from the declared priority weights. Data, not branches. It does **not** own the Outcome Dependencies: `build_agenda` applies those to whatever a policy answers, so a policy that never reads `requires_outcome_of` is still correct (D16). Names itself with a `policy_id`, and returns exactly the Component Kinds it was given: one that invents, drops or repeats a `kind_key` is refused at the seam with a Contract Violation Error, because a policy is replaceable and so its output is checked rather than trusted. Shipped adapters: `WeightedCatalogPolicy` (the default) and `FixedOrderPolicy`. |
| **Outcome Dependency** | A declaration that one Component Kind reads another's Selection (lodging dates follow the chosen air travel). **Soft:** it constrains ordering, and only while the Kind it names is open in the *same* Agenda Band — so a traveller who wants only a hotel is never held waiting on flights they never mentioned. Never a requirement to plan the other Kind at all. Applied by `build_agenda`, never by a Priority Policy, and applied in the same breath as the `blocked_by` label it produces, so an entry can never be labelled as awaiting a Kind it ranks ahead of. |
| **Option Slate** | The short list of Plan Options presented to the traveller for one Plan Component in one round. Numbered rounds; the history is kept. Carries the `requirements_digest` it was sourced against and the Diagnostics of the round that produced it — "here are three options, and one provider was unreachable" is a different answer from "here are three options". |
| **Plan Option** | One candidate on a Slate: structured facts (price, times, rating, references) plus Provenance, and the Filter Notes of the optional requirements it fails. Holds **no prose** — wording is produced at presentation time in the traveller's locale. `filter_notes` is a typed sibling of `facts` rather than a reserved key inside them, because `facts` is what a source declared and the notes are what Tourganize concluded. |
| **Provenance** | Where a Plan Option or Knowledge Passage came from: source id, retrieval timestamp, external reference, optional Citations. Required on every Plan Option: an option nobody can trace back to a source is not presentable. |
| **Money** | An exact amount: minor units (agorot, cents) as an integer, plus an ISO 4217 currency. Never a float, and never summed across currencies — there is no exchange rate anywhere in the domain. |
| **Selection** | The Plan Option the traveller accepted for a Plan Component, with the turn it was chosen on. |
| **Refinement** | A traveller turn that supplies corrections or extra detail instead of a choice. Enriches the Requirement Set and triggers a new Slate for the *same* component. |
| **Choose-or-Refine Loop** | The core interaction: present a Slate → the traveller selects (advance) or refines (re-source the same component). Runs an unbounded number of rounds. |
| **Proactive Offer** | The assistant's suggestion to plan a Component Kind the traveller never mentioned, once the mentioned ones are settled. The Kinds currently on the table are the Planning Session's **offer queue**. Accepting one is a mention — the traveller has now asked for it, so it joins the mentioned Agenda Band and is planned like anything else they raised; declining marks that Kind `DECLINED`, and a declined Kind is never offered again in that session — the rule holds structurally rather than by a second check, because the only way back out of `DECLINED` is a mention, and a mentioned Kind is in the band offers are never drawn from. |
| **Plan Completeness** | Derived summary of a Trip Plan: which Kinds are selected, declined, still open. Gates the closing summary. |

## 2. Dialogue

| Term | Meaning |
|---|---|
| **Planning Session** | The conversation aggregate: identity, Transcript, locale state, Dialogue State, the outstanding question, the Proactive Offers on the table, and the Trip Plan under construction. The unit that is persisted and resumed, which is why it carries a `schema_version` from the first day. Mutated **only** by the Dialogue Director. |
| **User Turn** | One inbound traveller utterance with its arrival time, index and optional locale hint. Turns arrive in order; a surface asks the session for `next_turn_index` rather than counting for itself. |
| **Turn Interpretation** | The structured reading of a User Turn: Turn Intent, mentioned Kinds, requirement updates, an optional chosen-option reference, detected Locale Tag, confidence. Produced by a **Turn Interpreter**. Everything but the intent is optional — an interpreter that fills nothing else is still a working one. |
| **Turn Intent** | What one User Turn is *for*, as far as the state machine is concerned: `STATE_REQUEST`, `ANSWER_QUESTION`, `CHOOSE_OPTION`, `REFINE`, `ACCEPT_OFFER`, `DECLINE_OFFER`, `END_SESSION`, `SMALL_TALK`, `UNKNOWN`. A closed set: an interpreter that cannot place an utterance answers `UNKNOWN` and the Director asks for clarification. |
| **Turn Interpreter** | Port that turns free text into a Turn Interpretation. It is a *language* component and nothing else: it never decides what happens next and never touches the Trip Plan. Resolving relative dates against the Clock is **this port's** obligation, because the domain accepts only resolved values — the first adapter is keyword-based and deterministic and deliberately does *not* do it (it offers no value for "next month" rather than a guessed one; F08's adapter closes that gap). What the contract forbids either way is passing a relative expression through as if it were a date. Everything it may know is a **Dialogue Context**. |
| **Dialogue Context** | Everything a Turn Interpreter is allowed to know about the conversation: the Dialogue State the turn arrived in, the Locale Tag, the Plan Component in focus, the Pending Question, the option references on the latest Option Slate, the declared `kind_key`s, and the field names the focused Requirement Schema declares. No session object leaks out, so a replacement interpreter is structurally unable to start making planning decisions. |
| **Option Slate Planner** | Port the Director calls to obtain one Option Slate for one Plan Component in one round. It is the seam that keeps the Director free of I/O: F05 drives it with a fake, F06 implements it over the Option Source port. Raising is how it says "nothing could be sourced". |
| **Dialogue State** | The explicit state of the Planning Session's state machine: `GREETING`, `INTERPRETING`, `ELICITING_BLOCKING`, `ELICITING_OPTIONAL`, `SOURCING`, `PRESENTING_SLATE`, `AWAITING_CHOICE`, `REFINING`, `OFFERING_UNMENTIONED`, `SUMMARISING`, `CLOSED`. The legal transitions are a table in `dialogue/states.py` and every move passes through one guard, so an impossible conversation cannot be recorded. |
| **Resting State** | The subset of Dialogue States a session is ever *observed* in between two turns — `GREETING`, `ELICITING_BLOCKING`, `AWAITING_CHOICE`, `OFFERING_UNMENTIONED`, `CLOSED`. Everything else is passed through inside one turn. A turn that changes nothing returns the session to the Resting State it arrived in, and so does a turn that found nothing to say: `ELICITING_BLOCKING` is entered by *asking*, so a session is never left there claiming to wait for an answer to a question nobody asked. |
| **Dialogue Director** | The application service that owns the state machine: it consumes a Turn Interpretation, mutates the Planning Session, and emits Assistant Acts. `handle(turn)` is the only entry point. Contains all control flow; contains no wording and no I/O. |
| **Pending Question** | The blocking obligation currently outstanding: the Blocking Rule it is about, every field that would help satisfy it, the turn it was raised on, and how many times it has been raised. It names a **rule** rather than a field, because an obligation may be satisfied in more than one way — so an `ask_blocking` and a `report_invalid_value` about a field that rule reads are two attempts on *one* Pending Question, not two independent ones. The count is what lets the Director stop, and it is not reset by giving up. |
| **Assistant Act** | An intent-to-communicate emitted by the Director with a structured, locale-neutral payload. The vocabulary is **closed**: `greet`, `ask_blocking`, `ask_optional`, `report_invalid_value`, `present_slate`, `confirm_selection`, `offer_unmentioned`, `deliver_summary`, `clarify`, `report_sourcing_failure`, `close`. A payload holds message keys, field names, `kind_key`s, opaque codes and structured Plan Option data, and **never** a composed sentence. The Presentation Surface renders it; the Language Services phrase it. |
| **Clarification Code** | The opaque code on a `clarify` Act saying why the Director could not act on a turn: `not_understood`, `unresolved_choice`, `still_missing`, `interpreter_failed`, `undeclared_field`. Never a sentence, and opaque like an Agenda Reason Code — a consumer that does not recognise one still knows it means "ask again". |
| **Transcript** | The ordered record of User Turns and Assistant Acts for a Planning Session, one **Transcript Entry** per exchange. The opening greeting is an entry with no turn, because it is the one thing the assistant says without having been spoken to. |
| **Turn Ledger** | The per-turn observability record: Dialogue State before/after, Turn Intent, focus `kind_key`, the Acts emitted, the Agenda's own explanation of itself, latency — and, from F08, LLM calls, tool calls, tokens and cost. It is a `kind` of Telemetry Event, not a second mechanism, and exactly one is recorded per `handle()`. |
| **Phrase Table** | The keyword Turn Interpreter's per-locale configuration (`keywords.<locale>.yaml`): which phrases mean which Turn Intent, which words raise which Component Kind, which field name each recognisable value shape is filed under, the place markers, the range separators and the month names. Deliberately small: the keyword interpreter is scaffolding F08 replaces, and a phrase table that grew into a grammar would be scaffolding nobody ever replaced. |

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
| **Option Source** | Port that answers an Option Query with candidate Plan Options for one or more Component Kinds. Its contract is checked rather than trusted: every adapter — fixture, world-backed, live — passes one shared contract suite, which is what makes D9's "a stub's shape may never differ from the real port" enforceable. Raising is how it says it could not answer. |
| **Option Query** | The request handed to an Option Source: Component Kind, the Requirement Set, the Slate size, the Locale Tag, any Selections it may read via Outcome Dependencies, and a request id. It carries the Requirement Set's `digest()`, which is what seeds a deterministic source. Never the Trip Plan, never the Planning Session, never the Dialogue State. |
| **Option Source Result** | One Option Source's answer to one Option Query: the Plan Options, the source's own id, when they were retrieved, whether the answer is `partial`, and its Diagnostics. |
| **Diagnostic** | An opaque code on an Option Source Result or an Option Slate saying something about *how* the answer was produced — `synthesised`, `no_match`, `source_failed:<id>`, `filtered_out`. Never a sentence, and opaque like an Agenda Reason Code: a consumer that does not recognise one records it rather than acting on it. |
| **Planning Service** | The application service that implements the Option Slate Planner for real: it builds the Option Query, calls the registered Option Sources **serially**, merges and de-duplicates what comes back, applies the optional filters, ranks, truncates to the Slate size and records one telemetry event. It is the only place that knows sourcing is more than one step. |
| **Option Source Registry** | Port answering "which Option Sources serve this Component Kind, in what order". Where the Source Profile lands. Two sources for one Kind is a supported configuration: results are merged and de-duplicated. |
| **Source Profile** | The configured choice of Option Sources: `fixture`, `world` (F17) or `live` (F24), set once for every Component Kind or per Kind (`lodging=live,air_travel=fixture`) — because a client with an account for one topic and none for another has to be able to mix them. |
| **Option Ranking** | The replaceable order a Slate is presented in. The shipped one is filters-satisfied first, then price ascending within a currency, then source order, then the option id. It never adds, removes or edits an option, and is checked at the seam like a Priority Policy. |
| **Filter Note** | The name of an optional requirement field a Plan Option **fails**. Soft filtering is visible rather than silent: a traveller who said "under €150" is shown the €160 room *marked*, not shown it as though they had never spoken. A field name, never a reason — the Message Catalogue phrases it from that field's own message key. |
| **Fixture Provider** | An Option Source adapter serving recorded option data from disk (`fixtures/options/<kind_key>/*.json`). One generic provider driven by data, never one class per Component Kind. Deterministic: the same query returns the same options in the same order, seeded by the Requirement Set's digest. A query nothing matches is answered with a deterministic **synthetic** set marked `synthesised`, so a demonstration never dead-ends. Exists from the first slice and remains the test default forever. |
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
| **Tourganize Error** | The root of the exception hierarchy. Every error the application raises deliberately derives from it, so a surface can tell a modelled failure from a bug. Its children name the ways things go wrong: **Configuration Error** (settings, or a configuration file, could not be resolved; exit code 3), **Port Unavailable Error** (a port has no adapter wired, or its adapter cannot be reached), **Contract Violation Error** (data crossing a port boundary did not satisfy the declared contract) and **Invariant Violation Error** (a domain value object or aggregate was handed data it forbids). F02 adds four more: **Catalog Error** (a Component Catalog is missing, unreadable or not a valid catalog — a *Configuration Error*, because a self-contradictory catalog file is a misconfigured installation; exit code 3), **Illegal Transition Error** (a Plan Component was asked for a Component Status it cannot legally reach), **Unknown Component Kind Error** (a `kind_key` the Component Catalog does not declare, or declares but disables) and **Unknown Option Error** (a Selection named a Plan Option that is not on that component's latest Option Slate). F03 adds three: **Schema Error** (a Requirement Schema is missing, unreadable, invalid, or describes a different Component Kind — a *Catalog Error*, because a Component Kind and the schema it names are one declaration split across two files; exit code 3), **Requirement Value Error** (a value failed its Field Kind's validation; carries `field_name` and `reason_message_key` so the dialogue can re-ask in the traveller's language) and **Unknown Field Error** (a Requirement Update named a field the schema does not declare — never ignored, because it usually means an extraction prompt and a schema have drifted apart). No feature derives from bare `Exception`. The root class is defined in `domain/errors.py`, because the domain may import only the standard library and itself, and re-exported from `platform/errors.py`, which stays the one place to read the whole hierarchy. F04 moves **Contract Violation Error** the same way and for the same mechanical reason — the Planning Agenda checks a replaceable Priority Policy's output inside the domain — and its documented import path, `platform/errors.py`, is unchanged. |
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
| `FixtureLoader`, `OptionFetcher`, `SearchClient` in core | `OptionSource` (port), `FixtureOptionSource` (adapter) | Where options come from is configuration; the port must read the same for a file and for an API. |
