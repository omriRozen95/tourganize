# F22 — Model service async upgrade (FastAPI + streaming)

- **Bounded context:** Language Services (own deployable service)
- **Depends on:** [F20](F20-model-service-and-gpu-profile.md)
- **Unlocks:** streaming surfaces (a prerequisite if F25 wants token-by-token output)
- **Size:** M
- **Track:** **deferred / optional** — the client may skip it with no consequence for any other feature
- **Status of the codebase when this starts:** the Model Service serves `compose` and `extract` on Flask
  behind a single-flight queue, and the application runs on it via `HostedModelGateway`. Concurrency is
  bounded by WSGI workers, there is no streaming, and the wire contract is hand-maintained.

## Purpose

Execute the migration [D7](../architecture/decisions.md) promised: re-implement the **same** wire contract
on **FastAPI**, gaining async request handling, generated OpenAPI documentation, and **token streaming** —
without changing a line of the application. It exists as its own feature precisely so that choosing Flask
first cost the client nothing but a later, well-scoped piece of work.

## Starting state

From F20: the framework-independent wire contract and its JSON schemas, the `InferenceEngine` abstraction,
the queue, the error envelope, the tiny CPU engine, the GPU image and compose profile, and the service
contract tests — which are the acceptance criteria for this feature.

## Scope — what to implement

1. **FastAPI application** (`services/model_service/fastapi_app.py`) — the same five endpoints with the
   same request/response shapes, pydantic models generated from (or validated against) F20's JSON schemas,
   so the contract is provably identical rather than merely similar.
2. **Async engine path** — an async wrapper over `InferenceEngine`, with generation running in a worker
   context that does not block the event loop, and the single-flight queue reimplemented with an async
   semaphore. GPU concurrency stays the operator's choice (`MODEL_SERVICE_CONCURRENCY`) — async improves
   *admission* and cancellation, not GPU throughput, and the note must say so plainly.
3. **Streaming** — `POST /v1/compose/stream` emitting server-sent events (`token`, `usage`, `done`,
   `error`), plus `supports_streaming: true` in `/v1/capabilities`. Client-side consumption is optional and
   is **not** part of this feature; the gateway keeps using the non-streaming endpoint unless a consumer
   asks for it.
4. **Cancellation** — a disconnected client aborts generation and frees the GPU slot. This is the concrete
   operational win: an abandoned turn currently occupies the model until it finishes.
5. **Dual-serve transition** — both apps runnable from one image, selected by
   `MODEL_SERVICE_FRAMEWORK=flask|fastapi` (default `flask` until the DoD is met, then `fastapi`), so the
   change is reversible in one environment variable and can be rolled back mid-deployment.
6. **Contract equivalence tests** — run F20's entire service contract suite against **both** apps in CI,
   parametrised on the framework, and a differential test issuing identical requests to both and comparing
   normalised responses field by field.
7. **OpenAPI** — `/openapi.json` published and **diffed against F20's hand-written schemas in CI**, so the
   two definitions of the contract cannot drift apart.
8. **Retirement** — once green, flip the default and mark the Flask app deprecated with a removal note; do
   not delete it in this feature.

## Contract (the Lego connectors)

**Inputs:** identical to F20's wire contract — the same requests on the same five endpoints. That
identity *is* the contract of this feature.

**Outputs:** identical responses, field for field, plus one addition — the streaming endpoint:

```http
POST /v1/compose/stream          # text/event-stream
event: token   data: {"text": "מל"}
event: token   data: {"text": "ון"}
event: usage   data: {"prompt_tokens": 812, "completion_tokens": 96}
event: done    data: {"finish_reason": "stop", "model_id": "…"}
```

**Ports consumed:** none (the service imports nothing from `tourganize`).

**Ports provided:** the unchanged HTTP contract plus the streaming endpoint and `supports_streaming`.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `MODEL_SERVICE_FRAMEWORK` | `flask` / `fastapi` | `flask`, then `fastapi` on completion |
| `MODEL_SERVICE_STREAM_ENABLED` | Expose the streaming endpoint | `true` |
| `MODEL_SERVICE_STREAM_KEEPALIVE_SECONDS` | SSE keep-alive interval | `15` |

**Errors/failure modes:** the same error envelope and codes; mid-stream failures emit an `error` event and
close (never a truncated success); a cancelled request is logged as `cancelled`, not as an error.

## Out of scope

Any change to the gateway, the app, or the prompt sets. Consuming streams in the terminal surface (a small
follow-on for F07/F25 if wanted). Multi-replica routing or autoscaling. Changing model, engine or
quantization.

## Replaceability notes

**Must be preserved:** byte-level compatibility of the non-streaming endpoints; the error envelope; the
capabilities document; the engine abstraction. **Free to change:** everything internal — async structure,
SSE framing details, whether pydantic models are generated or hand-written.

## Definition of done

- [ ] F20's full service contract suite passes against `MODEL_SERVICE_FRAMEWORK=fastapi` **unmodified**.
- [ ] The differential test shows identical normalised responses from both frameworks for a fixture set of
      requests, including every error case.
- [ ] `/openapi.json` is published and matches F20's JSON schemas (CI diff gate).
- [ ] Streaming: a compose stream yields incremental `token` events then `usage` and `done`; concatenated
      tokens equal the non-streaming response for a deterministic (temperature 0) request.
- [ ] Hebrew streaming does not corrupt multi-byte characters (asserted by streaming a Hebrew reply and
      comparing the reassembled string).
- [ ] Cancellation: a client disconnecting mid-generation frees the GPU slot within the configured window,
      proven by an immediately following request succeeding (and by a `cancelled` log line).
- [ ] Concurrency: with `MODEL_SERVICE_CONCURRENCY=1`, ten simultaneous requests are all served, in order,
      with no drops and no event-loop blocking (measured by a health check responding throughout).
- [ ] `TOURGANIZE_LLM_BACKEND=hosted` works against the FastAPI app with **no application change**, and the
      F21 parity report is re-run showing no regression in structural pass rate.
- [ ] Rollback works: flipping `MODEL_SERVICE_FRAMEWORK=flask` restores the previous behaviour, tested.
- [ ] The Flask app is marked deprecated with a removal note; the operator note records the measured
      latency and concurrency differences.

## Open questions / risks

- **Risk:** the async wrapper introducing subtle ordering differences under load (a request served out of
  queue order looks like unfairness). The differential and concurrency tests are the guard.
- **Risk:** streaming being built and never consumed. It is genuinely optional; if no surface wants it,
  `MODEL_SERVICE_STREAM_ENABLED=false` and the feature is still worth it for async admission and
  cancellation.
- **Implementer's call:** ASGI server (uvicorn vs. hypercorn); SSE vs. chunked JSON lines (SSE recommended
  for browser reuse in F25); whether pydantic models are generated from the schemas.
