# F21 — Self-hosted model adapter and backend parity

- **Bounded context:** Language Services
- **Depends on:** [F09](F09-claude-code-backend-adapter.md), [F11](F11-conversation-evaluation-harness.md), [F20](F20-model-service-and-gpu-profile.md)
- **Unlocks:** F23; satisfies C7 end to end
- **Size:** M
- **Status of the codebase when this starts:** the Model Service runs on the GPU host and answers
  `compose`/`extract` over HTTP, with measured capacity. The application still talks only to Claude Code
  or the fake backend, and nobody knows whether the open-weights model is good enough.

## Purpose

Complete the swap the whole architecture was shaped for. This feature adds the `HostedModelGateway`
adapter behind `LlmGateway` — so `TOURGANIZE_LLM_BACKEND=hosted` is the only change needed to run
Tourganize entirely on the client's own open-weights model (C7, C8) — and then **proves parity**: the same
Golden Conversations, run against both backends, with a report comparing structural pass rate, tokens,
latency and cost. That report is what lets the client decide, on evidence, whether to keep paying for the
interim backend.

## Starting state

From F08: the port, prompt library, ledger, contract suite, backend registry. From F09: the Claude adapter
and its cassettes, now the comparison baseline. From F11: the conversation harness with a backend matrix
skeleton. From F20: the HTTP contract, capabilities endpoint and measured limits.

## Scope — what to implement

1. **Adapter** (`tourganize/adapters/llm/hosted/`) — `HostedModelGateway` implementing `LlmGateway`
   against F20's contract: render via the Prompt Library, POST to `/v1/extract` or `/v1/compose`, map the
   response into `ExtractionResult`/`CompositionResult` with usage, and map error envelopes onto the
   existing exception vocabulary (`model_overloaded` → retry with backoff then `GatewayTimeoutError`;
   `context_too_long` → `ContextTooLongError`; `schema_unenforceable` → fall through to F08's repair
   loop). `capabilities()` is fetched from `/v1/capabilities` at start-up and cached, so
   `max_context_tokens` and `serial_only` are the *service's* truth rather than a guess.
2. **Connection discipline** — a pooled HTTP session with connect/read timeouts, bounded retries **only**
   on retryable envelopes and connection errors, the optional `MODEL_SERVICE_TOKEN` sent as a header, and a
   readiness check at start-up that fails fast with a clear message when the service is not ready (rather
   than a first-turn timeout).
3. **Context budgeting** — using the reported `max_context_tokens`, refuse or trim over-long requests
   *before* sending: grounding Passages (F19) are dropped lowest-score-first, then transcript context, with
   what was dropped recorded in the ledger. A quantized 14B model with a 16k window is a real constraint,
   and silently truncated prompts are the worst way to discover it.
4. **Prompt-set compatibility** — the same prompt templates serve both backends, but a smaller model may
   need firmer instructions. Support a **prompt-set overlay**: `TOURGANIZE_PROMPT_SET_VERSION=v1` plus
   `TOURGANIZE_PROMPT_OVERLAY=hosted` resolving `prompts/v1/hosted/<template_id>.md` before
   `prompts/v1/<template_id>.md`. Overlays are diffable and per-backend, so tuning for the open model
   cannot regress the Claude path.
5. **Parity suite** (`tourganize eval parity`) — run the whole Golden Conversation suite against N
   backends and emit one report:
   - **structural pass rate** per conversation per backend (the acts, plan state and message keys the
     harness already asserts — wording-independent by design);
   - **extraction reliability**: schema-valid-first-attempt rate, repair rate, hard-failure rate;
   - **cost/latency**: tokens per turn, wall-clock per turn, and cost when a rate table exists;
   - **language fidelity**: the share of Hebrew-locale replies whose dominant script is actually Hebrew
     (a cheap, automatic proxy that catches the classic failure of a small model answering Hebrew in
     English);
   - a **side-by-side transcript dump** per conversation, so a human can judge the wording the metrics
     cannot.
6. **Judgement, honestly scoped** — automatic metrics cannot score naturalness. The report includes a
   review sheet (identical conversations, both backends, anonymised order) for a human — the client's
   Hebrew reviewer — to rate. The DoD requires the sheet to exist and be filled once, not that the model
   wins.
7. **Fallback policy** — `TOURGANIZE_LLM_BACKEND=hosted` with `TOURGANIZE_LLM_FALLBACK_BACKEND=claude_code`
   (optional, default unset): on `BackendUnavailableError` the gateway may fall back once per session,
   logging loudly and recording it in the ledger. Off by default, because a silent fallback to a paid
   backend is exactly the surprise nobody wants.
8. **Operational documentation** — an operator note: how to point the app at the service, what to check
   when quality drops (overlay, context budget, quantization), how to read the parity report, and the
   decision record of which backend is now the default.

## Contract (the Lego connectors)

**Inputs:** F08's `ExtractionRequest`/`CompositionRequest`; the Model Service HTTP contract.

```python
class HostedModelGateway:                 # implements LlmGateway
    backend_id = "hosted"
    def __init__(self, prompts: PromptLibrary, http: HttpClient, ledger: TurnLedger,
                 settings: HostedGatewaySettings) -> None: ...

@dataclass(frozen=True)
class HostedGatewaySettings:
    base_url: str                  # TOURGANIZE_MODEL_SERVICE_URL
    token: SecretValue | None      # TOURGANIZE_MODEL_SERVICE_TOKEN
    connect_timeout: float         # TOURGANIZE_MODEL_SERVICE_CONNECT_TIMEOUT
    read_timeout: float            # TOURGANIZE_MODEL_SERVICE_READ_TIMEOUT
    max_retries: int               # TOURGANIZE_MODEL_SERVICE_MAX_RETRIES
    prompt_overlay: str | None     # TOURGANIZE_PROMPT_OVERLAY
```

**Outputs:** the same result types every other backend returns; the parity report (JSON + markdown) under
`${TOURGANIZE_EVAL_REPORT_DIR}/parity/`.

**Ports consumed:** `PromptLibrary`, `TurnLedger`, `Clock`, `TelemetrySink`.

**Ports provided:** a third `LlmGateway` implementation; the parity report as a repeatable artefact.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_MODEL_SERVICE_URL` | Base URL of the Model Service | `http://model-service:8080` |
| `TOURGANIZE_MODEL_SERVICE_TOKEN` | Shared secret, if the service requires one | unset |
| `TOURGANIZE_MODEL_SERVICE_CONNECT_TIMEOUT` | Connect timeout (s) | `5` |
| `TOURGANIZE_MODEL_SERVICE_READ_TIMEOUT` | Read timeout (s) | `120` |
| `TOURGANIZE_MODEL_SERVICE_MAX_RETRIES` | Retries on retryable errors | `2` |
| `TOURGANIZE_PROMPT_OVERLAY` | Per-backend prompt overlay directory | unset |
| `TOURGANIZE_LLM_FALLBACK_BACKEND` | Optional one-shot fallback backend | unset |
| `TOURGANIZE_CONTEXT_TRIM_STRATEGY` | `grounding_first` / `transcript_first` / `refuse` | `grounding_first` |

**Errors/failure modes:** `BackendUnavailableError` (service unreachable or not ready — reported by
`doctor` at start-up, not at first turn); `ContextTooLongError` after trimming cannot fit the request;
`GatewayTimeoutError`; `ExtractionSchemaError` from F08's validator. No new exception types: the whole point
is that callers cannot tell which backend they are on.

## Out of scope

Any change to the Model Service (F20 owns it) or to the gateway port (F08). Streaming (F22). Fine-tuning
(F23). Automatic backend selection by quality. Deciding *for* the client which backend to keep — this
feature produces the evidence.

## Replaceability notes

**Must be preserved:** that no caller can distinguish backends; capabilities coming from the service;
the parity report's metric set (it is the client's comparison instrument, so its shape should stay stable);
prompt overlays being additive.

**Free to change:** HTTP client library; retry and pooling details; trimming strategy; the report's
presentation.

## Definition of done

- [ ] `TOURGANIZE_LLM_BACKEND=hosted tourganize llm probe` performs a real extraction and composition
      against the Model Service, printing ledger entries with the service's `model_id` and token counts.
- [ ] The **F08 gateway contract suite passes unmodified** against `HostedModelGateway` (against the tiny
      engine in CI, and against the real model on the GPU host).
- [ ] Full end-to-end on the open-weights model: the Paris-hotel conversation completes interactively in
      English **and** in Hebrew with `hosted`, including a refinement round and an export.
- [ ] **Backend swap is config-only:** a test asserts that switching `TOURGANIZE_LLM_BACKEND` between
      `fake`, `claude_code` and `hosted` requires no other change, and `git diff` for the switch touches
      only environment configuration.
- [ ] `tourganize eval parity --backends fake,claude_code,hosted` produces the report with structural pass
      rate, extraction reliability, tokens, latency, cost (where known) and Hebrew-script fidelity per
      backend, plus side-by-side transcripts. The report is committed as the baseline.
- [ ] Context budgeting: a request exceeding the reported context is trimmed grounding-first and the drop
      is recorded in the ledger; with `refuse`, `ContextTooLongError` is raised instead (both asserted).
- [ ] Retry/degradation: `503 model_overloaded` is retried then surfaces as a timeout;
      a stopped service yields `BackendUnavailableError` at start-up with an actionable message;
      with `TOURGANIZE_LLM_FALLBACK_BACKEND=claude_code` a single loud fallback occurs and is recorded.
- [ ] Prompt overlay: a `hosted` overlay for one template is picked up in preference to the base template,
      and the Claude path provably still uses the base (asserted on `prompt_version` in results).
- [ ] The human review sheet is generated and filled once for the Hebrew conversations, with the outcome
      recorded in the operator note (whatever it says).
- [ ] `doctor` reports the Model Service URL, readiness, `model_id`, quantization and context limit.
- [ ] Claude and fake backends still pass every Golden Conversation unchanged.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — the HTTP client is imported only under
      `adapters/llm/hosted/`.

## Open questions / risks

- **Risk (the one that matters):** the open-weights model may be materially worse at Hebrew than Claude,
  especially at 4-bit. The design already contains the mitigations — catalogue-based wording instead of
  composed prose (F10), overlays with firmer instructions, and a Hebrew-specialised candidate model
  ([D11](../architecture/decisions.md)) — and the parity report is what turns the question into a
  decision instead of an argument.
- **Risk:** extraction reliability, not fluency, is what breaks the product: a model that cannot return
  schema-valid JSON drives the repair loop and doubles latency. The parity report measures this first.
- **Risk:** two prompt sets drifting apart. Mitigated by overlays being *diffs* over a shared base and by
  both backends running the same suite in CI.
- **Open (client):** the acceptance bar for Hebrew quality, and who judges it (overview §9, question 7).
