# F08 — LLM gateway, prompt library and model-driven turn interpretation

- **Bounded context:** Language Services
- **Depends on:** [F05](F05-dialogue-director-and-session-lifecycle.md), [F07](F07-presentation-surface-and-terminal-shell.md)
- **Unlocks:** F09, F10, F11, F19, F20, F21
- **Size:** L — *split is offered below if the sitting runs long*
- **Status of the codebase when this starts:** a complete, human-usable conversation exists, but the
  language work is fake: turns are read by a keyword table and every sentence comes from a static
  Message Catalogue. No model is involved anywhere.

## Purpose

Introduce the one door through which every model call passes. `LlmGateway` offers exactly two call
shapes — **Extraction** (free text → schema-validated structure) and **Composition** (structured payload
+ locale → traveller-facing text) — so that Claude Code (F09) and a self-hosted open-weights model (F21)
are interchangeable by configuration, not by refactor ([D4](../architecture/decisions.md), C8). It also
ships the **Prompt Library** (versioned templates on disk, never inline strings), a deterministic **Fake
Backend**, the **Turn Ledger** telemetry the client will use to compare backends, and the
model-driven `TurnInterpreter` that retires the keyword stand-in.

**Optional split if needed:** F08a = gateway + prompt library + fake backend + ledger; F08b = the
model-driven interpreter and composed wording. F08a's DoD is fully observable on its own (contract
suite + `tourganize llm probe`), so the split is safe. Keep them together if the sitting allows.

## Starting state

From F05/F07: `TurnInterpreter` port with the keyword adapter; `AssistantAct` payloads with message
keys; the Act renderer and Message Catalogue; `TelemetrySink`; `Clock`; the Composition Root.

## Scope — what to implement

1. **Gateway port** (`tourganize/ports/llm.py`) — `ExtractionRequest` (`template_id`, `variables`,
   `output_schema`, `locale`, `max_attempts`, `grounding: Sequence[Passage] = ()`),
   `ExtractionResult` (`data`, `raw_text`, `attempts`, `usage`, `model_id`, `prompt_version`),
   `CompositionRequest` (`template_id`, `variables`, `locale`, `style`, `max_tokens`),
   `CompositionResult` (`text`, `usage`, `model_id`, `prompt_version`), `GatewayCapabilities`
   (`supports_streaming`, `supports_grounding`, `max_context_tokens`, `serial_only`, `model_id`).
   The `grounding` field exists now and is ignored until F19 — adding it later would change every
   adapter.
2. **Prompt Library** (`tourganize/language/prompts.py`) — loads
   `${TOURGANIZE_PROMPT_DIR}/<version>/<template_id>.md` with YAML front matter declaring
   `template_id`, `kind` (`extraction`|`composition`), `variables`, `output_schema` (file reference for
   extraction), `locales`, `notes`. Validation at load: declared variables match the placeholders in the
   body, every extraction template names a resolvable schema, and rendering with a missing variable
   raises `PromptRenderError` (never silently emits an empty slot). The active version comes from
   `TOURGANIZE_PROMPT_SET_VERSION` and is recorded on every result.
3. **Schema enforcement** — extraction output is parsed then validated against the template's JSON
   Schema. On failure, **one bounded repair loop**: re-prompt with the validator's error appended, at
   most `TOURGANIZE_LLM_MAX_ATTEMPTS` (default 2) total attempts, then raise
   `ExtractionSchemaError` carrying the last raw text. Validated data crossing into the domain is the
   invariant that keeps a weak model from corrupting a Trip Plan.
4. **Fake Backend** (`tourganize/adapters/llm/fake/`) — no network, two modes:
   `scripted` (a mapping from `(template_id, matcher)` → canned response, used by F11) and `derived`
   (deterministic synthesis from the request so any prompt gets a schema-valid answer). It reports
   plausible `usage` numbers so ledger assertions work. This is the default backend in tests forever.
5. **Turn Ledger** (`tourganize/language/ledger.py`) — one telemetry event per gateway call:
   `session_id`, `turn_index`, `template_id`, `prompt_version`, `backend_id`, `model_id`,
   `prompt_tokens`, `completion_tokens`, `latency_ms`, `attempts`, `schema_valid`, `estimated_cost`
   (from a configurable per-backend rate table, `null` when unknown rather than guessed), plus a
   `tool_calls` counter F15/F17 will increment. This is the artefact for the Claude-vs-own-model
   comparison the client asked for, so it exists from the **first** model feature, not later.
6. **Model-driven Turn Interpreter** (`tourganize/adapters/interpretation/model/`) — one extraction
   template, `interpret_turn`, whose schema is **generated from the Requirement Schemas** of the
   Component Kinds currently in play (so schema/prompt drift from F03 is structurally impossible):
   returns intent, mentioned kinds, requirement updates, choice reference, detected locale, confidence.
   Post-processing in the adapter (not the model): resolve relative dates ("this year", "next month")
   against the `Clock`; drop updates for fields the schema does not declare (log, and count it — this is
   the drift signal F03 asked for); clamp confidence; fall back to `UNKNOWN` intent rather than
   guessing. Selected by `TOURGANIZE_INTERPRETER=model`.
7. **Composed wording** (`tourganize/language/act_composer.py`) — an optional layer between Act and
   surface: for acts whose payload benefits from prose (`present_slate`, `confirm_selection`,
   `offer_unmentioned`, `deliver_summary`), call Composition with the locale and the structured payload;
   the **Message Catalogue remains the fallback** whenever the gateway is unavailable, disabled by
   `TOURGANIZE_COMPOSE_WORDING=false`, or slower than `TOURGANIZE_COMPOSE_TIMEOUT_SECONDS`. Composition
   may never introduce facts: it receives only the payload, and a post-check rejects output containing
   digits absent from the payload (cheap hallucination guard for prices and dates), falling back to the
   catalogue on rejection.
8. **Backend registry and contract suite** — `TOURGANIZE_LLM_BACKEND` (`fake` now, `claude_code` in F09,
   `hosted` in F21) resolved in the Composition Root, plus
   `tests/contracts/test_llm_gateway_contract.py`: the parametrised suite every backend must pass
   (valid extraction, repair-then-succeed, repair-then-fail raising `ExtractionSchemaError`, composition
   in both locales, timeout raising `GatewayTimeoutError`, usage populated, capabilities self-consistent).
9. **CLI** — `tourganize llm probe [--template T] [--locale L]` running one extraction and one
   composition against the configured backend and printing the ledger entry. This is how F09 and F21 are
   smoke-tested.

## Contract (the Lego connectors)

**Inputs:** template id + variables + (for extraction) a JSON Schema; the traveller's raw turn text.

**Outputs:** validated structures and locale-appropriate text, plus one ledger event per call.

```python
class LlmGateway(Protocol):
    @property
    def backend_id(self) -> str: ...
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
    def compose(self, request: CompositionRequest) -> CompositionResult: ...
    def capabilities(self) -> GatewayCapabilities: ...

@dataclass(frozen=True)
class ExtractionRequest:
    template_id: str
    variables: Mapping[str, object]
    output_schema: Mapping[str, object]
    locale: str = "en"
    max_attempts: int = 2
    grounding: Sequence[Passage] = ()          # populated from F19 onward
```

```markdown
<!-- config/prompts/v1/interpret_turn.md -->
---
template_id: interpret_turn
kind: extraction
output_schema: schemas/turn_interpretation.json
variables: [turn_text, dialogue_state, focus_kind, pending_question, slate_refs, known_kinds, today]
locales: [en, he]
---
You read one traveller message and report what it means as JSON. …
```

**Ports consumed:** `Clock`, `TelemetrySink`.

**Ports provided:** `LlmGateway` (with `FakeGateway`), `PromptLibrary`, `TurnLedger`, the model-driven
`TurnInterpreter`, and the act composer.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_LLM_BACKEND` | `fake` / `claude_code` / `hosted` | `fake` |
| `TOURGANIZE_PROMPT_DIR` | Prompt template root | `${TOURGANIZE_CONFIG_DIR}/prompts` |
| `TOURGANIZE_PROMPT_SET_VERSION` | Active prompt set directory | `v1` |
| `TOURGANIZE_LLM_MAX_ATTEMPTS` | Extraction attempts including repair | `2` |
| `TOURGANIZE_LLM_TIMEOUT_SECONDS` | Per-call timeout | `60` |
| `TOURGANIZE_INTERPRETER` | `keyword` / `model` | `model` (was `keyword`) |
| `TOURGANIZE_COMPOSE_WORDING` | Use Composition for act prose | `true` |
| `TOURGANIZE_COMPOSE_TIMEOUT_SECONDS` | Fall back to the catalogue beyond this | `10` |
| `TOURGANIZE_LLM_COST_TABLE` | Optional per-backend token rates for cost estimates | unset |

**Errors/failure modes:** `PromptRenderError` (missing variable — a bug, fails fast);
`ExtractionSchemaError` after the repair budget (the Director turns it into `clarify`, so the traveller
sees a re-ask, never a stack trace); `GatewayTimeoutError`; `BackendUnavailableError` (`doctor` reports
it and the app falls back to `keyword` + catalogue wording if
`TOURGANIZE_LLM_DEGRADE_TO_FAKE=true`, default `false` in prod). **The gateway is documented as serial**
— no caller may assume parallel fan-out ([D5](../architecture/decisions.md)).

## Out of scope

Any real backend (F09, F21). Streaming (F22). Retrieval and grounding content (F19) — only the field
exists. Changing any dialogue rule: the Director's state machine is untouched by this feature, which is
the whole point of [D2](../architecture/decisions.md). Prompt tuning as a deliverable (a follow-on
activity, not a feature).

## Replaceability notes

**Must be preserved:** the two call shapes and their request/result types; `GatewayCapabilities`;
schema validation before data enters the domain; prompts as versioned files with declared variables;
the ledger field set (the client's comparison depends on stability); the contract suite.

**Free to change:** the repair-prompt strategy; JSON Schema library; template file format; the composer's
hallucination guard; whether the interpreter's schema is generated or hand-written per prompt set.

## Definition of done

- [ ] `tourganize llm probe` against `TOURGANIZE_LLM_BACKEND=fake` prints a validated extraction, a
      composed sentence in `en` and in `he`, and one ledger entry per call with token counts and latency.
- [ ] The LLM contract suite passes for `FakeGateway` in both modes, and a deliberately broken fixture
      backend fails it on each of: unvalidated output, missing usage, ignored locale, ignored timeout.
- [ ] **Interpreter swap:** with `TOURGANIZE_INTERPRETER=model` and the scripted fake backend, the entire
      F05 scenario suite and the F07 scripted session pass **unchanged** — proving the Director is
      indifferent to how turns are read.
- [ ] Extraction repair: a fake first returning invalid JSON then valid JSON yields
      `attempts == 2, schema_valid == True`; always-invalid yields `ExtractionSchemaError`, and the
      Director converts it into a `clarify` Act (asserted end to end through a scripted session).
- [ ] Prompt validation: a template with an undeclared placeholder, one with a missing schema file, and a
      render with a missing variable each fail loudly with the template id in the message.
- [ ] Relative-date resolution is tested against `FrozenClock`: *"between the 23rd and 28th of October"*
      resolves to the next occurrence, and a Hebrew equivalent resolves identically.
- [ ] Schema drift is detected: a requirement update naming a field absent from `lodging.v1` is dropped,
      logged, and counted in telemetry (asserted).
- [ ] Composition guard: a fake composition returning a price absent from the payload is rejected and the
      Message Catalogue text is used instead (asserted on rendered output).
- [ ] `TOURGANIZE_COMPOSE_WORDING=false` produces a complete, sensible session using catalogue text only
      — the always-works path.
- [ ] Ledger entries appear in the telemetry file for a full scripted session, one per gateway call, and
      a test sums tokens per turn.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.dialogue` and `tourganize.domain` still
      import nothing from `tourganize.adapters.llm`.
- [ ] Phase 1 demo (F07) still runs identically with `TOURGANIZE_LLM_BACKEND=fake`.

## Open questions / risks

- **Implementer's call:** JSON Schema library (`jsonschema` vs. pydantic-generated); whether the
  interpreter schema is generated at start-up or built per turn; template front-matter dialect; how the
  cost table is expressed.
- **Risk:** the two call shapes proving too narrow (e.g. a future need for classification-with-logprobs).
  Widen the port with a new method plus a capability flag — never by letting callers reach a backend
  directly.
- **Risk:** prompts becoming the untested part of the system. The fake backend cannot validate prompt
  *quality*; only F11's Golden Conversations and F21's parity suite can, which is why both exist.
- **Risk:** composed wording drifting from the catalogue's meaning in Hebrew. F10 adds locale checks; the
  digit guard here is a floor, not a ceiling.
