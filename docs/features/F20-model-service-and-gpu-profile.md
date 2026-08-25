# F20 — Self-hosted model service and the GPU deployment profile

- **Bounded context:** Language Services (own deployable service)
- **Depends on:** [F01](F01-project-foundation.md), [F08](F08-llm-gateway-and-prompt-library.md)
- **Unlocks:** F21, F22, F23
- **Size:** L — *the service and the GPU packaging are each a sitting; split guidance below*
- **Status of the codebase when this starts:** the whole product works, in two languages, with documents
  and world data — driven by Claude Code. The client's core constraint that the LLM must ultimately be an
  **open-weights model from Hugging Face** (C7) is not yet met, and no GPU has been used.

## Purpose

Stand up Tourganize's own model. This feature builds the **Model Service**: an HTTP service, in its own
GPU container, that serves a quantized open-weights instruct model and exposes exactly the two call shapes
the `LlmGateway` needs — `compose` and `extract` — behind a framework-independent wire contract, on Flask
([D7](../architecture/decisions.md)), sized and placed for the client's actual hardware
([D11](../architecture/decisions.md)). It deliberately stops short of switching the app over; F21 does
that, so a failure here cannot break a working product.

**Optional split:** F20a = the wire contract, the Flask app, the engine abstraction and a **CPU-tiny-model
smoke path** (verifiable on any laptop); F20b = the GPU image, compose profile, quantized model and
capacity measurements on the real host. F20a is fully testable without a GPU, which is what makes this
splittable.

## Starting state

From F08: the `LlmGateway` contract, the two request/result shapes, prompt templates, schema validation and
the ledger — all the semantics this service must serve. From F01: the container and compose conventions,
Settings and telemetry.

## Scope — what to implement

1. **Wire contract** (`docs/architecture/model_service_api.md` + `services/model_service/schemas/*.json`)
   — the authority, defined **independently of Flask** so F22's migration is mechanical:
   - `POST /v1/compose` — `{template_rendered, locale, max_tokens, temperature, stop}` →
     `{text, usage, model_id, finish_reason}`;
   - `POST /v1/extract` — `{template_rendered, output_schema, locale, max_attempts}` →
     `{data, raw_text, attempts, schema_valid, usage, model_id}`;
   - `GET /healthz` (liveness), `GET /readyz` (model loaded, VRAM available),
     `GET /v1/capabilities` (`model_id`, `max_context_tokens`, `quantization`, `supports_streaming`,
     `serial_only`, `gpu_indices`);
   - one error envelope: `{error: {code, message, retryable}}` with codes `model_overloaded`,
     `context_too_long`, `schema_unenforceable`, `internal`.
   **Note:** the service performs schema-constrained generation where the engine supports it, and validates
   before returning; F08's repair loop remains the client-side safety net. Both layers are intentional.
2. **Engine abstraction** (`services/model_service/engine/`) — an `InferenceEngine` protocol
   (`generate`, `generate_constrained`, `capabilities`, `load`, `unload`) with three implementations:
   - **vLLM** (primary): fp16 compute, AWQ/GPTQ 4-bit weights, one replica pinned to one GPU;
   - **llama.cpp/GGUF** (documented fallback for Turing kernel trouble — [D11](../architecture/decisions.md));
   - **Tiny CPU** (a small HF model, or a canned echo engine) so the service is testable in CI with no GPU.
3. **Flask application** (`services/model_service/flask_app.py`) — thin: request validation against the
   JSON schemas, dispatch to the engine, error envelope, request id echoed for correlation, structured
   access logs. Served by a production WSGI server (gunicorn/waitress) with worker count derived from
   `MODEL_SERVICE_CONCURRENCY` and a **single-flight queue in front of the engine**, because the real
   bottleneck is the GPU, not the web layer. No business logic, no prompt knowledge: it receives
   **already-rendered** prompts, since the Prompt Library stays in the app (F08).
4. **Model choice and placement** ([D11](../architecture/decisions.md)) — configuration, not code:
   `MODEL_ID` (first pick: a **Qwen2.5-14B-Instruct AWQ** build; alternatives documented, including
   **DictaLM-2.0-Instruct** for Hebrew and a 32B-class 4-bit stretch), `MODEL_QUANTIZATION`,
   `MODEL_MAX_CONTEXT`, `MODEL_GPU_INDICES` (default: one of the two matched Quadro RTX 6000 cards),
   `MODEL_TENSOR_PARALLEL` (only meaningful across the matched pair; **never** including the 3090 Ti),
   `MODEL_DTYPE=float16` (**not** bfloat16 — Turing does not support it). Weights are mounted from a host
   cache, never baked into the image.
5. **GPU container and compose** — `docker/model_service.Dockerfile` on a CUDA base with the NVIDIA
   container runtime, and a **`gpu` compose profile** wiring device reservations, the weights volume and the
   service port. The existing `dev-cpu` profile must remain **completely GPU-free**: a developer runs the
   whole app with the Claude or fake backend and no NVIDIA runtime installed. A `model-cpu` profile runs
   the tiny engine for wiring tests.
6. **Capacity measurement** (`services/model_service/tools/measure.py`) — a script reporting, for the
   configured model: VRAM at load, VRAM at max context, tokens/second at concurrency 1 and 2, and the
   context length at which it OOMs. Its **output is committed** as the record of what the hardware
   actually does, because [D11](../architecture/decisions.md) is provisional until measured.
7. **Operational hygiene** — model load on start-up with `readyz` false until loaded; graceful shutdown
   releasing VRAM; a request timeout shorter than the app's gateway timeout; per-request telemetry (tokens,
   latency, queue wait, GPU index) exported in the same JSON-lines shape the app uses, so F21's parity
   report can join both sides.
8. **Service tests** — contract tests against the JSON schemas using the tiny engine (no GPU, CI-safe):
   both endpoints, the error envelope, context-too-long, constrained extraction, concurrency serialisation,
   and `readyz` behaviour before load. A separate, manually-run GPU suite covers the real model.

## Contract (the Lego connectors)

**Inputs:** HTTP requests carrying **rendered** prompts (the app owns templating).

```http
POST /v1/extract
{"template_rendered": "You read one traveller message …",
 "output_schema": {"type": "object", "required": ["intent"], "...": "..."},
 "locale": "he", "max_attempts": 2, "request_id": "…"}

200 OK
{"data": {"intent": "state_request", "mentioned_kinds": ["lodging"], "...": "..."},
 "raw_text": "{…}", "attempts": 1, "schema_valid": true,
 "usage": {"prompt_tokens": 812, "completion_tokens": 96},
 "model_id": "Qwen2.5-14B-Instruct-AWQ"}
```

**Outputs:** the responses above; capability metadata; telemetry.

**Ports consumed:** none — this service depends on nothing in `tourganize` and **must not import it** (CI
check, same rule as F16).

**Ports provided:** the HTTP contract that F21's `HostedModelGateway` consumes.

**Config/env keys introduced (service side):**

| Key | Meaning | Default |
|---|---|---|
| `MODEL_SERVICE_HOST` / `MODEL_SERVICE_PORT` | Bind address | `0.0.0.0` / `8080` |
| `MODEL_SERVICE_ENGINE` | `vllm` / `llamacpp` / `tiny` | `vllm` |
| `MODEL_ID` | Hugging Face model id or local path | Qwen2.5-14B-Instruct AWQ build |
| `MODEL_QUANTIZATION` | `awq` / `gptq` / `gguf-q4` / `none` | `awq` |
| `MODEL_DTYPE` | Compute dtype | `float16` |
| `MODEL_MAX_CONTEXT` | Max context tokens | `16384` |
| `MODEL_GPU_INDICES` | Visible GPUs for this replica | `1` (a Quadro) |
| `MODEL_TENSOR_PARALLEL` | TP degree (matched cards only) | `1` |
| `MODEL_SERVICE_CONCURRENCY` | Concurrent requests admitted | `1` |
| `MODEL_WEIGHTS_DIR` | Mounted weights cache | `/models` |
| `MODEL_SERVICE_TOKEN` | Optional shared secret required on requests | unset |

**Errors/failure modes:** `503` + `model_overloaded` when the queue is full (retryable);
`413`/`context_too_long` with the measured token count; `422`/`schema_unenforceable` when the engine cannot
constrain to the schema (the app then falls back to F08's repair loop); `500`/`internal` with a request id
and no prompt content in the log. OOM at load fails `readyz` with the VRAM figures rather than crash-looping
silently.

## Out of scope

Switching the app to this service (F21). Streaming and async (F22). Fine-tuning (F23). Multi-model routing,
autoscaling, or a second replica. Authentication beyond an optional shared secret — it is a private-network
service. Prompt management, which stays in the app so both backends share one prompt set.

## Replaceability notes

**Must be preserved:** the wire contract (F21 codes against it, F22 re-implements it, and the schemas are
the contract tests); the engine abstraction; that the service receives rendered prompts and returns usage;
`readyz` semantics; not importing `tourganize`.

**Free to change:** Flask → FastAPI (that is F22); engine, model, quantization and GPU placement (all
config); the queue implementation; whether constrained decoding is used.

## Definition of done

- [ ] **CI, no GPU:** `MODEL_SERVICE_ENGINE=tiny` starts the service; the schema contract tests pass for
      `/v1/compose`, `/v1/extract`, `/healthz`, `/readyz`, `/v1/capabilities`, and every error code is
      reproducible.
- [ ] **On the GPU host:** `docker compose --profile gpu up model-service` loads the configured model and
      `readyz` turns true; `curl` against both endpoints returns valid responses with real token counts.
- [ ] Capacity measurements are committed: VRAM at load and at max context, tokens/second at concurrency 1
      and 2, and the OOM context length — with the actual GPU model and driver recorded. If the numbers
      contradict [D11](../architecture/decisions.md), the ADR is updated in the same change.
- [ ] Hebrew generation works: a Hebrew composition request returns Hebrew text (asserted by script
      ratio), and a Hebrew extraction returns schema-valid JSON.
- [ ] Constrained extraction: a request with a strict `output_schema` returns `schema_valid=true` without
      repair for the standard interpretation schema; where the engine cannot constrain, `422` is returned
      and documented.
- [ ] Serialisation and queueing: with `MODEL_SERVICE_CONCURRENCY=1`, two simultaneous requests are served
      one at a time and neither is dropped; a full queue returns `503 model_overloaded` (retryable).
- [ ] Timeout discipline: a request exceeding the service timeout returns an error envelope, and the GPU
      worker is not left wedged (asserted by a following successful request).
- [ ] `dev-cpu` remains GPU-free: on a machine with no NVIDIA runtime, the full app still builds and runs
      (existing demo commands pass), proving the profiles are genuinely separate.
- [ ] Isolation: CI asserts `services/model_service/` does not import `tourganize`; the service's tests run
      with only its own dependencies.
- [ ] Weights are mounted, not baked: the image builds with no model present, and `readyz` reports the
      missing-weights condition clearly.
- [ ] Telemetry lines are emitted per request in the app's JSON-lines shape, including queue wait and GPU
      index.
- [ ] The app is untouched: `TOURGANIZE_LLM_BACKEND` still resolves to `claude_code`/`fake`, and every
      Golden Conversation passes exactly as before.
- [ ] The wire contract document and JSON schemas are committed, and an operator note covers model
      download, the weights volume, driver/runtime prerequisites and the fallback engine.

## Open questions / risks

- **Risk (highest):** Turing (TU102) kernel support. vLLM supports compute 7.5, but bfloat16 is
  unavailable, some quantization kernels are Ampere-only, and feature support shifts between releases.
  Mitigations: `MODEL_DTYPE=float16` enforced, AWQ/GPTQ chosen for Turing compatibility, the llama.cpp
  fallback engine specified up front, and the measurement script committed so reality is recorded rather
  than assumed. **Hardware confirmation is still pending** (overview §9, question 1).
- **Risk:** quality at 4-bit, especially in Hebrew. Unknown until F21 measures it — which is exactly why
  F21 exists and why the Claude adapter stays in the tree.
- **Risk:** a 14B model at 16k context plus KV cache on 24 GB is comfortable; 32B at 4-bit is not, and
  context must then shrink. The measurement script settles it rather than the docs.
- **Implementer's call:** WSGI server; queue implementation; constrained-decoding library; whether
  `/v1/extract` retries internally (recommended: no — one repair path, in F08).
