# Architecture Decision Log

One entry per decision the design brief required (D1–D12), plus decisions taken while implementing
(D13 onwards). Every entry states the decision, why, what it costs, and **which feature reverses it** —
because reversibility is the point of the Lego-piece rule.

Status values: `accepted` (in force), `accepted-provisional` (in force, pending a client answer that
cannot block work).

---

## D1 — Terminal surface first, behind a Presentation Surface port

**Status:** accepted · **Owning feature:** [F07](../features/F07-presentation-surface-and-terminal-shell.md) · **Reversed by:** [F25](../features/F25-web-presentation-surface.md)

**Decision.** The first traveller-facing surface is a **terminal UI built with Textual**, sitting behind
the `PresentationSurface` port. A headless **Scripted Surface** adapter ships in the same feature. A
web/GUI surface is an optional later adapter, not a rewrite.

**Rationale.** A terminal surface runs unchanged inside the app container over SSH, needs no browser,
no asset pipeline and no second process, and Textual is driveable from tests (`Pilot`, snapshot
assertions). Because every surface is reached only through Assistant Acts, the Director never learns
which surface is attached — so the GUI question genuinely stays open instead of being decided by
accident.

**Cost.** Terminals are a weak bidi environment: they do not reorder RTL runs, and column alignment of
mixed Hebrew/Latin text is approximate. We therefore do our own Bidi Shaping for display (F10) and
accept that the terminal is not the pixel-accurate Hebrew surface — the exported document is (F14).
Textual also constrains us to a monospace grid, which limits option-table density.

**Reversal path.** F25 adds a Web Surface adapter implementing the same port. No domain change; the
only edit outside F25 is one Composition Root branch and one config value (`TOURGANIZE_SURFACE`).

---

## D2 — Deterministic dialogue state machine in the domain; the model does language, not control

**Status:** accepted · **Owning feature:** [F05](../features/F05-dialogue-director-and-session-lifecycle.md) · **Reversed by:** no planned feature (see below)

**Decision.** All control flow — which Plan Component is planned next, whether a blocking question must
be asked, whether a turn advances or re-sources, when a Proactive Offer is made, when the session
closes — lives in an explicit state machine (`DialogueDirector`) in the domain. The language model is
used **only** for language tasks: reading a turn into a `TurnInterpretation` and phrasing Assistant
Acts. The model never decides the next state and never calls a tool on its own initiative.

**Rationale.** The client's rules are hard rules (Mentioned-First, blocking-before-planning,
choose-or-refine), and hard rules belong in code that can be unit-tested. It also makes the
Claude→open-weights swap safe: a weaker model degrades wording quality, never the flow. And it is what
makes the Golden Conversation harness (F11) possible at all.

**Cost.** Less conversational flexibility than an agent loop: a traveller who does three things in one
sentence is handled only as far as the Interpretation schema allows, and genuinely novel intents fall
through to a `clarify` Act. We pay for that in schema breadth over time.

**Reversal path.** Deliberately *not* planned. If a future agentic controller is wanted, it enters as
an alternative `DialogueDirector` implementation consuming the same Planning Session aggregate and
emitting the same Assistant Acts; the Golden Conversations then become its acceptance suite. Nothing
else in the system may assume which director is in use.

---

## D3 — Mentioned-First as a hard rule; everything else a declarative Priority Policy

**Status:** accepted · **Owning feature:** [F04](../features/F04-component-prioritization-policy.md) · **Reversed by:** a replacement policy adapter (no renumbering needed)

**Decision.** The "importance metric" the client left undefined is split in two. (1) **Mentioned-First**
is a hard, non-configurable rule: Kinds the traveller named outrank Kinds they did not. (2) Within each
band, order comes from a **declarative Priority Policy** read from the Component Catalog: each
Component Kind declares a `priority_weight` and its `requires_outcome_of` Outcome Dependencies. The
shipped default weights are `air_travel > lodging > ground_transport`, on the grounds that air travel
constrains dates and cost the most and is the least substitutable.

**Rationale.** The client explicitly deferred the metric, so it must not become branches. Weights plus
dependencies in configuration cover every ordering the client is likely to want (including "hotels
first" for a road trip) without a code change, and the policy object is small enough to replace whole.

**Cost.** A single scalar weight cannot express context-sensitive importance ("in August, lodging is
the scarce resource"). When the client defines the real metric, the scalar may need to become a
function of the Trip Plan.

**Reversal path.** `PriorityPolicy` is a port with one shipped implementation
(`WeightedCatalogPolicy`). A smarter policy is a new adapter plus a config value; the Agenda contract
(`ordered kind_keys`) is what must be preserved.

---

## D4 — One LLM Gateway port, two schema-validated call shapes, versioned prompt templates

**Status:** accepted · **Owning feature:** [F08](../features/F08-llm-gateway-and-prompt-library.md) · **Reversed by:** n/a (contract widened, never replaced)

**Decision.** Everything that touches a model goes through `LlmGateway` with exactly two call shapes:
**Extraction** (free text + Prompt Template + JSON Schema → validated structure) and **Composition**
(structured payload + Locale Tag → traveller-facing text). Extraction results are validated before
they may enter the domain, with a bounded repair-retry on schema violation. Prompts are versioned files
on disk loaded by the Prompt Library; the Prompt Set Version is recorded on every Turn Ledger entry.

**Rationale.** Two narrow shapes are the smallest surface that covers every use we have, and a narrow
surface is what makes Claude Code and a 14B open-weights model actually interchangeable. Schema
validation at the boundary means a weaker model produces a *retry or a clear failure*, never corrupt
domain state. Versioned prompt files let the client A/B prompt sets across Backends without touching
Python.

**Cost.** No streaming token-by-token UX through this port in its first form, and no free-form agentic
tool use by the model. Prompt authoring becomes a real, versioned artefact with its own review burden.

**Reversal path.** Streaming is added as an *additional* Composition method with a capability flag
(`GatewayCapabilities.supports_streaming`), consumed by F22 and F25; existing callers are unaffected.

---

## D5 — Claude Code is invoked as a stateless one-shot CLI process, isolated in one adapter

**Status:** accepted · **Owning feature:** [F09](../features/F09-claude-code-backend-adapter.md) · **Reversed by:** [F21](../features/F21-self-hosted-model-adapter-and-parity.md) (config switch, adapter retained)

**Decision.** The interim Backend shells out to the Claude Code CLI in non-interactive print mode
(`claude -p`) with JSON output, **one process per Gateway call**, no reused interactive session. The
adapter passes the rendered prompt on stdin/argv, requests JSON output, disables tool use, parses the
result text and the usage/cost fields into the Turn Ledger, and enforces a timeout. The exact flag set
is pinned by the implementer against `claude --help` in the container and recorded in the adapter's
module docstring. If the Claude Agent SDK for Python is available in the image, it is the preferred
transport for the same adapter — the choice is internal to F09.

**Rationale.** Statelessness is free for us: D2 already keeps all conversation state in the Planning
Session, so each Gateway call is a self-contained language task. One process per call means no session
affinity, no pty handling, no drift between what the domain thinks the history is and what a
long-lived CLI session remembers. It also matches exactly what the future Model Service will offer,
which is what makes the swap a config change.

**Cost.** Process start-up on every call (order of a second before the model even starts), so a turn
with an Extraction plus a Composition pays it twice. Subscription-based rate limits mean **no parallel
fan-out** may be assumed anywhere in the design; the Gateway is documented as serial. The CLI must be
installed and credentialed inside the container, which couples the dev image to a login the client
controls, and cost data is per-call metadata rather than a billing API.

**Reversal path.** F21 introduces the Hosted Model Backend behind the same port;
`TOURGANIZE_LLM_BACKEND=hosted` switches. The Claude adapter stays in the tree as the comparison
baseline for the parity suite.

---

## D6 — Retrieval first behind a Knowledge Retriever port; model tuning is an alternative adapter later

**Status:** accepted · **Owning features:** [F18](../features/F18-document-ingestion-and-corpus.md), [F19](../features/F19-knowledge-retrieval-and-grounding.md) · **Reversed by:** [F23](../features/F23-tuned-knowledge-adapter.md)

**Decision.** Supplementary Documents reach the model by **retrieval**: ingest → Passages → embeddings
→ ranked retrieval → grounded Gateway call with Citations, all behind the `KnowledgeRetriever` port.
The Unsloth fine-tuning path the client named is a separate, optional, later feature that supplies an
*alternative* adapter behind the same port (and may complement, not replace, retrieval).

**Rationale.** Per-document turnaround is minutes, not a training run; a document can be added,
corrected or withdrawn without touching model weights; and answers carry Citations, which matters when
the content is an airline's fare rules and being wrong has consequences. Tuning also cannot be applied
to the interim Claude Backend at all, so retrieval is the only path that works in Phase 5.

**Cost.** Retrieval quality work (chunk sizing, embedding choice, reranking) is ongoing, Hebrew
embedding quality is an open risk, and every grounded call spends context tokens on Passages.

**Reversal path.** F23 registers a `TunedRecallAdapter` under the same port and a config value selects
it. The port contract (ranked Passages with Provenance) is what any replacement must honour — a tuned
model that cannot cite must still return anchors, even coarse ones.

---

## D7 — The Model Service ships on Flask; the async upgrade is a named deferred feature

**Status:** accepted · **Owning feature:** [F20](../features/F20-model-service-and-gpu-profile.md) · **Reversed by:** [F22](../features/F22-model-service-async-upgrade.md)

**Decision.** The self-hosted Model Service is first served by a **thin Flask application** (three
endpoints: `POST /v1/compose`, `POST /v1/extract`, `GET /healthz`) in front of the Inference Engine.
The **wire contract is specified as a schema document independent of the framework**, so the FastAPI
migration is mechanical. That migration is F22 on the deferred track.

**Rationale.** The client named Flask-first as an acceptable path, and the surface is genuinely tiny:
three synchronous request/response endpoints whose real concurrency limit is the GPU, not the web
layer. Keeping the heavy lifting inside the Inference Engine means the framework carries almost no
logic to port later.

**Cost.** No native async and no token streaming, so concurrency is bounded by worker count under a
WSGI server, and a streaming UI cannot be built until F22. No automatic OpenAPI schema, so the wire
contract must be hand-maintained (it is, in F20's contract section) and drift is possible.

**Reversal path.** F22 re-implements the same documented contract on FastAPI with streaming and async,
keeping the Flask app in place until the contract tests pass against both.

---

## D8 — SQLite-backed Session Repository; single traveller, no auth

**Status:** accepted-provisional · **Owning feature:** [F12](../features/F12-session-and-plan-persistence.md) · **Reversed by:** a new repository adapter

**Decision.** Planning Sessions (transcript, Dialogue State, Trip Plan) persist as JSON snapshots in a
**SQLite** file on a mounted volume, behind the `SessionRepository` port, with an in-memory adapter for
tests. This supports resuming a conversation and re-exporting a plan without re-planning. **Multi-user
access, authentication, and any handling of personal traveller data beyond the current session are
explicitly out of scope** until the client says otherwise.

**Rationale.** SQLite needs no extra container, is a single file to back up or delete, gives us
transactional snapshot writes, and supports the two queries we actually have (load by id, list
recent). A snapshot-per-session model avoids designing a relational schema for a domain that is still
moving.

**Cost.** No concurrent writers, no cross-session querying ("all plans mentioning Paris"), and schema
migration of stored snapshots becomes our problem — hence a `schema_version` on every snapshot.

**Reversal path.** A `PostgresSessionRepository` (or document store) behind the same port; the port's
snapshot contract and `schema_version` are what must be honoured. Multi-user support would additionally
require a traveller identity concept, which is a new feature and a client decision, not a swap.

---

## D9 — Fixture Providers first; Live Providers are late, optional, one feature each

**Status:** accepted-provisional · **Owning features:** [F06](../features/F06-option-sourcing-and-fixture-providers.md), [F24](../features/F24-live-option-provider-adapters.md)

**Decision.** Plan Options come from **Fixture Providers** — recorded/synthetic data on disk — from the
first slice onward, behind the `OptionSource` port. Real commercial providers arrive as separate late
features behind the identical port. A Fixture Provider's output shape may never differ from the port
contract, and fixtures remain the permanent test default even after Live Providers exist.

**Rationale.** Everything the client asked to see — the choose-or-refine loop, Mentioned-First,
blocking questions, the exported document — is fully demonstrable on fixtures, so no early feature
should be blocked on accounts, keys, rate limits or terms of use, which are the client's to supply.
Deterministic option data is also the only way the Golden Conversations can assert anything.

**Cost.** Fixture data can quietly drift from what real providers return (missing fields, unrealistic
prices, no pagination or availability races), which flatters our design. F24 must therefore include a
contract-conformance test that both fixture and live adapters pass.

**Reversal path.** Not a reversal so much as an addition: `TOURGANIZE_OPTION_SOURCE_PROFILE` selects
`fixture`, `world` (MCP-backed, F17) or `live` (F24) per Component Kind.

---

## D10 — Typeset export via WeasyPrint with an embedded Hebrew font; text/markdown as the always-works fallback

**Status:** accepted · **Owning features:** [F13](../features/F13-itinerary-rendering-and-text-renderer.md), [F14](../features/F14-typeset-itinerary-renderer.md)

**Decision.** The default `pdf` Export Format is produced by **WeasyPrint** from an HTML/CSS template,
with **Noto Sans Hebrew** (and Noto Sans for Latin) embedded via `@font-face` and `direction: rtl` on
Hebrew sections. WeasyPrint lays text out through Pango, which performs real Unicode bidi resolution —
so the domain hands it **logical-order** text and applies no manual reordering. A pure-Python
`text`/`markdown` renderer ships **first** (F13) and remains the fallback whenever the typeset stack is
unavailable.

**Rationale.** Hebrew export is the requirement most likely to be silently broken, and hand-rolled
bidi over a low-level PDF library (the ReportLab/`python-bidi` route) is exactly where mixed
Hebrew/Latin lines and embedded numbers go wrong. Delegating bidi to Pango and layout to CSS is the
lower-risk choice, and an HTML template is also the artefact the client can restyle without Python.

**Cost.** WeasyPrint pulls native libraries (Pango, cairo, GDK-PixBuf) into the image, adding size and
one class of platform-specific build failure; and the font must be shipped in-image for reproducible
output. That is precisely why the fallback renderer exists and why F13 lands before F14.

**Reversal path.** Any renderer registering the `pdf` `format_key` and consuming an Itinerary Document
replaces it. Because F13's renderer is always present, a broken typeset stack degrades the export
format rather than failing the session.

---

## D11 — Single-GPU-per-worker serving, 4-bit weights, 14B-class multilingual model; the Ampere card is not in the serving pool

**Status:** accepted-provisional (hardware unconfirmed) · **Owning feature:** [F20](../features/F20-model-service-and-gpu-profile.md)

**Decision.** The Model Service runs **one model replica pinned to one GPU**, with **4-bit quantized
weights** (AWQ or GPTQ, fp16 compute — *not* bf16), served by **vLLM**, with **llama.cpp/GGUF as the
documented fallback engine** if Turing kernel support bites. Candidate model: the **Qwen2.5-Instruct
family at 14B** as the first pick (≈9–10 GB of 4-bit weights on a 24 GB card, leaving real room for KV
cache), with 32B-class 4-bit as a stretch at reduced context, and **DictaLM-2.0-Instruct** named as the
Hebrew-specialised alternative to evaluate. GPU allocation: **the two matched Quadro RTX 6000 cards
serve** (independently, or tensor-parallel across the pair since they are identical); the **RTX 3090 Ti
is reserved for embeddings, reranking and any tuning job** — it is the only Ampere card, so it is the
only one with bf16 and the natural home for F19 and F23.

**Rationale.** The brief's hardware correction is the driver: the source calls all three GPUs
TU102, but the RTX 3090 Ti is GA102/Ampere while the Quadros are TU102/Turing. Mixing architectures in
one tensor-parallel group is the awkward case (no bf16 on Turing, different kernel paths), so we keep
serving on the matched pair and give the odd card the job that benefits from bf16. 24 GB per card is
the hard ceiling, and 4-bit weights are what turn a 14B–32B model with a usable context window into
something that fits alongside its KV cache.

**Cost.** No single-replica model larger than roughly 32B-at-4-bit; quantization costs some quality,
especially in Hebrew, which is exactly what F21's parity suite has to measure. Turing also means no
bf16 and no newest-generation kernels, so some vLLM features are unavailable — hence the named
fallback engine.

**Reversal path.** Engine, quantization and GPU mapping are all configuration
(`TOURGANIZE_MODEL_ENGINE`, `TOURGANIZE_MODEL_QUANTIZATION`, `TOURGANIZE_MODEL_GPU_INDICES`,
`TOURGANIZE_MODEL_ID`) read by F20's container. Changing any of them must not touch a line of
application code. **Client confirmation of the GPU line-up is requested** (see overview.md §9).

---

## D12 — The first local MCP service is an Itinerary Feasibility service

**Status:** accepted · **Owning feature:** [F16](../features/F16-local-feasibility-mcp-service.md)

**Decision.** Of the candidates below, **Itinerary Feasibility** is built first.

| # | Candidate local MCP service | What it exposes | Verdict |
|---|---|---|---|
| 1 | **Itinerary Feasibility** | `assess_feasibility` (do these Selections cohere: connection times, date coverage, arrival-before-check-in, drive-time reachability, budget roll-up), `explain_conflicts` | **Chosen first.** Pure computation, zero network, fully deterministic, and it improves answers the day it lands by filtering incoherent Slates. |
| 2 | **Geo & Seasonality Reference** | `resolve_place` (city/airport codes, coordinates), `public_holidays`, `climate_normals`, `visa_free_lookup` | Strong second; needs a bundled offline dataset and its licensing checked. |
| 3 | **Knowledge Lookup bridge** | The Knowledge Corpus (D6) exposed as MCP tools so any MCP client can query the ingested fare rules | Valuable, but depends on F18/F19 and duplicates an existing in-process port. Later. |
| 4 | **Plan Archive** | `list_plans`, `load_plan`, `diff_plans`, `re_export` over the Session Repository | Useful operationally; blocked on F12 and mostly a convenience wrapper. |
| 5 | **Traveller Profile Vault** | Stored passports, loyalty programmes, dietary and mobility constraints as reusable Requirement defaults | Deferred deliberately: it stores personal data, which D8 puts out of scope pending a client decision. |

**Rationale.** Candidate 1 is the only one that is simultaneously genuinely useful, testable entirely
offline, and free of new data-licensing or privacy questions. It also exercises the FastMCP **server**
side properly (typed tools, structured errors) while the Tool Broker exercises the consumer side, so
one small service validates both halves of C11/C12.

**Cost.** Feasibility rules are heuristics with assumptions baked in (minimum connection time, average
driving speed), so the service must return its assumptions alongside its verdict and never silently
veto an option — the Director treats a negative assessment as advisory annotation, not as a filter,
unless configured otherwise.

**Reversal path.** Any of candidates 2–5 can be added as an additional MCP service without touching
the Tool Broker; capability names are configuration. Dropping feasibility entirely means removing one
config entry and the annotation step in F17.

---

## D13 — Configuration files are read by a strict in-tree YAML subset reader, not a YAML library

**Status:** accepted · **Owning feature:** [F02](../features/F02-trip-plan-domain-core.md) · **Reversed by:** any feature that needs YAML we do not support

**Decision.** `config/` is read by `tourganize/platform/yaml_subset.py`, about two hundred lines of
standard library, rather than by PyYAML. It accepts block mappings, block sequences, single-line flow
collections, the scalar types the config files use, comments and one optional leading `---`. Everything
else — anchors, aliases, tags, block scalars, multiple documents, tab indentation, a plain scalar
containing `": "` — is **refused** with a `ConfigurationError` naming the file and the line.

**Rationale.** F01 states that the base install stays pure-Python and that the CPU image builds with
nothing to fetch, and `tourganize doctor` has to run in that image unmodified. The Component Catalog —
and, later, the Requirement Schemas, the prompt manifests and the Message Catalogue — use a small,
boring corner of YAML, so the choice is between a runtime dependency for the whole distribution and a
reader for the corner we actually use. The repository already parses its own `KEY=value` secrets file
for the same reason. Being strict is what makes this safe: an unsupported construct is a loud failure
at load, never a quiet mis-reading of what someone meant.

**Cost.** A config file cannot use YAML features the reader does not know, and F10's Message Catalogue
in particular may want block scalars for long Hebrew strings. The reader also has to be trusted, which
is why it carries its own unit suite covering both the subset and every refusal. Neither cost lands on
the domain: parsing sits in `platform`, behind whichever port reads the file.

**Reversal path.** Add `pyyaml` to `[project.dependencies]` and replace the body of
`read_yaml_subset` with `yaml.safe_load`, keeping the same `ConfigurationError` on failure. One module,
one dependency line; no caller changes, because every caller already receives plain Python data.

---

## D14 — The Requirement Schema is a parameter of `with_updates`, not a field of the Requirement Set

**Status:** accepted · **Owning feature:** [F03](../features/F03-requirement-schemas-and-gap-analysis.md) · **Reversed by:** no planned feature (see below)

**Decision.** `RequirementSet.with_updates(updates, *, schema)` takes the Requirement Schema as a
keyword argument. F03's Contract block originally wrote `with_updates(self, updates)`; this entry
records the amendment, because a normative Contract is not something to widen quietly.

**Rationale.** The merge cannot be done without the schema: it stores every value in its Field Kind's
**normalised** form, and what "normalised" means for one field is a fact about that field's Field
Spec and nothing else. The question is only *where* the schema comes from, and there were three
candidates: a field on the set, a module-level `merge(schema, set, updates)`, or a parameter. A field
on the set is the one that is actually wrong — a Requirement Set is small, copied on every turn and
persisted by F12, and a set carrying a schema would persist a copy of a versioned file alongside every
session, with two of them able to disagree after an upgrade. Between the other two, a parameter keeps
one call reading as one operation. Refusing an undeclared field (`UnknownFieldError`) happens here too,
but that is a consequence, not the reason: a module-level merge, or a check when the Requirement Update
is constructed, would raise it equally well.

**Cost.** Every caller of `with_updates` must have the schema to hand — in practice the Director, which
has just asked the `ComponentCatalog` for it — and a Requirement Set cannot merge anything on its own,
which makes it a slightly less self-sufficient object than it looks. Tests pay this too: every merge
test declares a schema. The keyword-only form is what keeps the cost honest, since no call site can
pass one by accident.

**Reversal path.** Move the body to a module-level `merge(schema, requirement_set, updates)` in
`domain/requirements/values.py` and leave `with_updates` delegating to it, or delete it. The merge
logic does not move; only the receiver does. One module, and the call sites F05 and F12 will have by
then. Putting the schema *on* the set is the direction this decision closes, and reopening it means
answering the persistence question first.
