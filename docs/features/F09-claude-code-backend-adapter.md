# F09 — Claude Code backend adapter (interim language model)

- **Bounded context:** Language Services
- **Depends on:** [F08](F08-llm-gateway-and-prompt-library.md)
- **Unlocks:** F21 (as the parity baseline). Also lets F10 and F11 be exercised against a real model,
  though neither depends on it
- **Size:** M
- **Status of the codebase when this starts:** the `LlmGateway` port, Prompt Library, Turn Ledger,
  contract suite and Fake Backend all exist. Every model call in the system is fake; no real model has
  ever been invoked.

## Purpose

Make Tourganize actually intelligent, using the subscription the client already has. This feature adds
**one adapter** behind `LlmGateway` that drives the Claude Code CLI as a stateless, one-shot process per
call ([D5](../architecture/decisions.md)). Nothing outside this adapter learns that Claude exists;
switching to the self-hosted model later (F21) is a change to one environment variable. Visible outcome:
the Paris-hotel conversation works with genuinely understood free-text turns, in English and Hebrew, and
the ledger shows real tokens, latency and cost per turn.

## Starting state

From F08: the two call shapes, prompt templates, schema validation with the repair loop, the ledger, the
backend registry keyed by `TOURGANIZE_LLM_BACKEND`, and the gateway contract suite that this adapter must
pass unmodified.

## Scope — what to implement

1. **Transport** (`tourganize/adapters/llm/claude_code/transport.py`) — invoke the Claude Code CLI in
   non-interactive print mode, one process per gateway call, no reused session:
   - prompt delivered on **stdin** (never as an argv fragment — prompts contain newlines, quotes and
     Hebrew);
   - JSON output requested so the result text, model id and usage/cost metadata are parseable rather
     than scraped;
   - tool use and MCP disabled for the process: this adapter is a *language* backend, and Tourganize
     reaches the world through its own Tool Broker (F15). A prompt must never be able to make Claude
     touch the filesystem;
   - a system-prompt suffix stating "return only the requested output" for extraction calls;
   - hard timeout (`TOURGANIZE_LLM_TIMEOUT_SECONDS`), process group kill on timeout, stderr captured
     into the error, working directory set to an empty scratch dir.
   - **Pin the exact flag set** against `claude --help` in the container at implementation time and
     record it in the module docstring with the CLI version. Treat unknown-flag failures as a
     `BackendUnavailableError` with the flag named, so a CLI upgrade is diagnosable in one line.
   - If the Claude Agent SDK for Python is present in the image, it may be used instead of the CLI: the
     transport is internal to this adapter, and the contract suite is what proves either choice correct.
2. **Adapter** (`.../gateway.py`) — implement `extract` and `compose` on top of the transport: render
   via the Prompt Library, call, extract the result payload, hand the raw text to F08's validator (the
   repair loop is F08's, **not** duplicated here), and map usage/cost metadata into the ledger.
   `capabilities()` reports `serial_only=True`, `supports_streaming=False`,
   `supports_grounding=True` (grounding arrives as prompt content), and the model id actually reported by
   the CLI.
3. **JSON robustness** — models wrap JSON in prose or fences. Provide one narrow, well-tested
   `extract_json_payload(text)` that strips fences and takes the outermost balanced object, then defers
   to F08's schema validation. No regex soup, no "fix the JSON" heuristics beyond fences and balance.
4. **Rate and concurrency discipline** — a module-level serialising lock so two turns can never invoke
   the CLI at once (subscription limits, and `serial_only`), a bounded retry with jitter **only** on
   transport-level failures (non-zero exit with a recognised rate-limit or transient signature), and
   `RateLimitedError` after the budget with the retry-after hint if the CLI reports one.
5. **Doctor integration** — `tourganize doctor` runs a cheap probe: is the CLI on `PATH`, does it report
   a version, is it authenticated? Report each as pass/fail with remediation text; never print
   credentials, tokens or the prompt.
6. **Recorded transcripts** (`tourganize/adapters/llm/claude_code/recorded.py`) — a
   record-and-replay wrapper writing `(request digest → response)` cassettes to
   `${TOURGANIZE_DATA_DIR}/llm_cassettes/`. Its purpose is honest CI: F11's suite runs against replayed
   real responses without a subscription, and a re-record is an explicit, reviewable commit.
7. **Documentation** — a short operator note: how the container gets the CLI and its credentials, that
   the credential is the client's subscription (mounted, never baked into an image), the observed latency
   band, and the explicit statement that **no parallel fan-out is permitted**.

## Contract (the Lego connectors)

**Inputs:** `ExtractionRequest` / `CompositionRequest` (F08 types).

**Outputs:** `ExtractionResult` / `CompositionResult` with `usage` and `model_id` populated from the
CLI's own JSON metadata; one ledger event per call.

```python
class ClaudeCodeGateway:            # implements LlmGateway; the only Claude-aware class in the codebase
    backend_id = "claude_code"
    def __init__(self, prompts: PromptLibrary, transport: ClaudeCodeTransport,
                 ledger: TurnLedger, settings: ClaudeCodeSettings) -> None: ...

@dataclass(frozen=True)
class ClaudeCodeSettings:
    cli_path: str                  # TOURGANIZE_CLAUDE_CLI_PATH
    model: str | None              # TOURGANIZE_CLAUDE_MODEL (None = CLI default)
    timeout_seconds: float         # TOURGANIZE_LLM_TIMEOUT_SECONDS
    scratch_dir: Path              # TOURGANIZE_CLAUDE_WORKDIR
    max_transport_retries: int     # TOURGANIZE_CLAUDE_MAX_RETRIES
    cassette_mode: Literal["off", "record", "replay"]   # TOURGANIZE_CLAUDE_CASSETTES
```

**Ports consumed:** `PromptLibrary`, `TurnLedger`, `Clock`, `TelemetrySink`.

**Ports provided:** a second `LlmGateway` implementation. No new port.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_CLAUDE_CLI_PATH` | Executable to invoke | `claude` |
| `TOURGANIZE_CLAUDE_MODEL` | Model id passed to the CLI | unset (CLI default) |
| `TOURGANIZE_CLAUDE_WORKDIR` | Empty scratch cwd for the subprocess | `${TOURGANIZE_DATA_DIR}/claude-scratch` |
| `TOURGANIZE_CLAUDE_MAX_RETRIES` | Transport retries on transient failure | `2` |
| `TOURGANIZE_CLAUDE_CASSETTES` | `off` / `record` / `replay` | `off` |

**Errors/failure modes:** `BackendUnavailableError` (CLI missing, unauthenticated, or unknown flag —
message names which); `GatewayTimeoutError` (process killed); `RateLimitedError`;
`ExtractionSchemaError` (raised by F08's validator after the repair budget). Non-zero exits include the
captured stderr tail, truncated, **with the prompt redacted** so traveller text never reaches logs.

## Out of scope

Any change to the gateway port, the repair loop, or the ledger schema (F08 owns them). Streaming.
Parallel calls. Prompt caching or conversation reuse — statelessness is the decision, and revisiting it
means revisiting [D5](../architecture/decisions.md). Using Claude's own tool use or MCP for world data
(F15/F17 own that, deliberately).

## Replaceability notes

**Must be preserved:** that this adapter is the *only* Claude-aware module (enforced: a test greps for
`claude` outside `adapters/llm/claude_code/`, tests and docs); the `LlmGateway` contract; `serial_only`
in capabilities; the cassette format used by CI.

**Free to change:** everything inside — CLI vs. SDK transport, flags, retry policy, JSON extraction
details, whether a scratch dir is reused. Removing the adapter entirely (once F21 lands) must require no
change outside the Composition Root and configuration.

## Definition of done

- [ ] `TOURGANIZE_LLM_BACKEND=claude_code tourganize llm probe` performs one real extraction and one real
      composition, printing ledger entries with non-zero token counts, real latency, and a model id.
- [ ] The **F08 gateway contract suite passes unmodified** against this adapter (run in `record` mode,
      then re-run in `replay` mode in CI).
- [ ] End-to-end: the Paris-hotel session runs interactively with this backend, understands a free-text
      turn the keyword interpreter could not (e.g. *"somewhere central, nothing over 200 a night, decent
      reviews"*) and produces a correctly filtered slate.
- [ ] Hebrew end-to-end: the same request in Hebrew is interpreted correctly (mentioned kind, place,
      dates) and answered in Hebrew.
- [ ] JSON robustness: unit tests over recorded pathological outputs (fenced JSON, prose preamble,
      trailing commentary, JSON containing Hebrew and a `}` inside a string) all yield the right payload;
      genuinely broken output raises through to F08's repair loop rather than being "fixed" here.
- [ ] Timeout: with `TOURGANIZE_LLM_TIMEOUT_SECONDS=1` against a sleeping stub transport, the call raises
      `GatewayTimeoutError`, the child process group is gone (asserted), and the session recovers with a
      `clarify` Act rather than dying.
- [ ] Serialisation: a test starting two concurrent `extract` calls asserts the transport was entered
      once at a time.
- [ ] `tourganize doctor` reports CLI presence, version and auth state, with actionable text when the CLI
      is absent — and **no** credential material or prompt text in the output.
- [ ] A test asserts no traveller text and no credential appears in logs or errors on a failing call.
- [ ] Isolation test: `grep -rin "claude" tourganize/ --exclude-dir=adapters/llm/claude_code` returns
      nothing outside comments referencing this feature.
- [ ] Cassettes: recording a session then replaying it produces identical results with the CLI absent
      from `PATH` (proves CI independence).
- [ ] `TOURGANIZE_LLM_BACKEND=fake` still yields a fully working session — no path silently requires
      Claude.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass; the operator note is committed under `docs/`.

## Open questions / risks

- **Implementer's call:** CLI vs. Agent SDK transport; retry signatures; cassette digest algorithm;
  whether a per-call scratch dir is created and removed.
- **Risk (highest):** CLI flag and output-shape drift between Claude Code versions. Mitigations: pin the
  observed version in the docstring, fail with a named-flag error, and keep CI on cassettes so a CLI
  upgrade breaks one adapter test rather than the whole suite.
- **Risk:** latency. Two calls per turn (extraction + composition) times process start-up is a
  perceptible pause. Mitigations available without changing the design: turn off composed wording
  (`TOURGANIZE_COMPOSE_WORDING=false`), and show a "thinking" notice through the surface's `notify()`.
  If it remains unacceptable, that is an argument for advancing F20/F21, not for a stateful session.
- **Risk / client question:** whether driving a Claude Code subscription programmatically inside a
  container suits the client's licence and terms. Flagged, not decided here; the design's whole point is
  that F21 removes the dependency.
