# F15 — Tool broker port and the FastMCP consumer

- **Bounded context:** Option Sourcing
- **Depends on:** [F01](F01-project-foundation.md)
- **Unlocks:** F16, F17
- **Size:** M
- **Status of the codebase when this starts:** a complete, bilingual, persisted, exportable planning
  conversation — running entirely on fixture data. Nothing in the system has ever made a network call to
  reach the outside world.

## Purpose

Open a controlled door to the world. This feature introduces the `ToolBroker` port — invoke a **named
capability** with structured arguments, get a structured result — and its first adapter, an **MCP client
built on FastMCP** (C11). It deliberately arrives *before* anything uses it, because the property that
matters is determinism: the broker ships with recorded **Cassettes** and a fake, so every later feature
that touches the world stays testable offline. Visible outcome: `tourganize tools list` and
`tourganize tools call` against a configured MCP server.

## Starting state

From F01: Settings, logging, telemetry, `Clock`, the Composition Root, the CLI. From F06: the
`OptionSource` port that F17 will implement on top of this broker. No MCP dependency exists yet.

## Scope — what to implement

1. **Port** (`tourganize/ports/tools.py`):
   - `CapabilityDescriptor` — `capability_name`, `server_id`, `title`, `input_schema`, `output_schema`,
     `description`.
   - `ToolCall` — `capability_name`, `arguments`, `timeout_seconds`, `request_id`, `idempotent`.
   - `ToolResult` — `capability_name`, `server_id`, `payload`, `structured: bool`, `latency_ms`,
     `retrieved_at`, `diagnostics`, `is_error`.
   - `ToolBroker` — `list_capabilities()`, `invoke(call)`, `describe(capability_name)`, `health()`.
2. **Server registry** (`config/tools/servers.yaml`) — declarative, one entry per MCP server:
   `server_id`, transport (`stdio` with command/args/env, or `http`/`sse` with a URL), an **allowlist** of
   capability names Tourganize may call, per-server timeout and retry budget, and `required: true|false`.
   Capability names are configuration, so adding a server is not a code change. **The allowlist is
   mandatory**: an un-allowlisted capability raises even if the server offers it, so a compromised or
   chatty server cannot widen our surface.
3. **FastMCP adapter** (`tourganize/adapters/tools/fastmcp/`) — a client using FastMCP:
   - connection management per server with lazy connect, health check, bounded reconnect, and clean
     shutdown of stdio child processes (no orphans — the same discipline as F09's subprocess handling);
   - capability discovery mapped into `CapabilityDescriptor`s, cached for
     `TOURGANIZE_TOOL_DISCOVERY_TTL_SECONDS`;
   - invocation with input validation against the discovered `input_schema` **before** the call (fail fast,
     locally, with a clear error), and output normalisation into `ToolResult.payload`, preferring
     structured content and falling back to text with `structured=False`;
   - per-call timeout, retry only for calls declared `idempotent`, and MCP protocol errors mapped to
     `ToolInvocationError` — never leaking an SDK exception type upward.
   - **No LLM involvement.** Tourganize decides which capability to call ([D2](../architecture/decisions.md));
     the broker does not expose tools *to* a model, and F09 explicitly disables Claude's own tool use.
4. **Cassette recorder/replayer** (`tourganize/adapters/tools/recorded/`) — wraps any broker; keys on a
   canonical digest of `(capability_name, arguments)`; modes `off`, `record`, `replay`, `replay_or_record`.
   In `replay`, an unknown digest raises `CassetteMissError` (never a silent live call — that is how test
   suites start depending on the network without anyone noticing). Cassettes are committed under
   `fixtures/cassettes/`.
5. **Fake broker** — `FakeToolBroker` built from a dict of capability → callable, plus
   `FailingToolBroker` and `SlowToolBroker` for failure-path tests.
6. **Telemetry** — every invocation records `capability_name`, `server_id`, argument digest (**not** the
   arguments: they can contain traveller text), latency, retries, error class, and payload size, and
   increments the Turn Ledger's `tool_calls` counter (F08) so a turn's true cost is visible.
7. **Doctor and CLI** — `tourganize doctor` reports each configured server (reachable, capability count,
   allowlist coverage, `required` violations); `tourganize tools list [--server S]` and
   `tourganize tools call <capability> --args '<json>' [--record]`.
8. **Contract suite** (`tests/contracts/test_tool_broker_contract.py`) — for every broker adapter:
   allowlist enforcement, input validation before dispatch, timeout honoured, `is_error` results rather
   than exceptions for tool-level failures, structured-vs-text normalisation, and digest stability for
   equivalent argument orderings.

## Contract (the Lego connectors)

**Inputs:** a `ToolCall`; `config/tools/servers.yaml`; cassette files.

```python
class ToolBroker(Protocol):
    def list_capabilities(self, server_id: str | None = None) -> Sequence[CapabilityDescriptor]: ...
    def describe(self, capability_name: str) -> CapabilityDescriptor: ...
    def invoke(self, call: ToolCall) -> ToolResult: ...
    def health(self) -> Mapping[str, BrokerHealth]: ...
```

```yaml
# config/tools/servers.yaml
version: 1
servers:
  - server_id: feasibility            # the local service built in F16
    transport: {kind: stdio, command: python, args: ["-m", "services.mcp_feasibility"]}
    allow: [assess_feasibility, explain_conflicts]
    timeout_seconds: 10
    required: false
  - server_id: world_travel           # an external MCP server, when one is configured
    transport: {kind: http, url: "${WORLD_MCP_URL}"}
    allow: [search_air_travel, search_lodging, search_ground_transport]
    timeout_seconds: 20
    required: false
```

**Outputs:** `ToolResult`s; discovered capability descriptors; cassette files; telemetry.

**Ports consumed:** `Clock`, `TelemetrySink`.

**Ports provided:** `ToolBroker` (`FastMcpBroker`, `RecordedBroker`, `FakeToolBroker`,
`FailingToolBroker`).

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_TOOL_BROKER` | `fastmcp` / `recorded` / `fake` / `none` | `none` |
| `TOURGANIZE_TOOL_SERVERS_FILE` | Server registry path | `${TOURGANIZE_CONFIG_DIR}/tools/servers.yaml` |
| `TOURGANIZE_TOOL_CASSETTE_DIR` | Cassette root | `./fixtures/cassettes` |
| `TOURGANIZE_TOOL_CASSETTE_MODE` | `off` / `record` / `replay` / `replay_or_record` | `off` |
| `TOURGANIZE_TOOL_TIMEOUT_SECONDS` | Default per-call timeout | `15` |
| `TOURGANIZE_TOOL_DISCOVERY_TTL_SECONDS` | Capability cache lifetime | `300` |

**Errors/failure modes:** `ToolConfigurationError` (bad registry, unknown transport);
`CapabilityNotAllowedError`; `CapabilityNotFoundError`; `ToolArgumentError` (local schema validation);
`ToolTimeoutError`; `ToolInvocationError` (transport/protocol); `CassetteMissError`. A server marked
`required: false` that is unreachable degrades: `health()` reports it, `doctor` shows it, and callers
decide — which is exactly what F17 does when it falls back to fixtures.

## Out of scope

Any *use* of the broker for planning (F17). Building an MCP **server** (F16). Exposing Tourganize's own
tools to an external model. Authentication beyond passing configured headers/env through (real credentials
are a client-supplied concern). Streaming tool results.

## Replaceability notes

**Must be preserved:** the `ToolBroker` protocol and `ToolResult` shape; capability names as
configuration; the mandatory allowlist; the cassette digest scheme and `replay` strictness; that no SDK
exception type escapes the adapter.

**Free to change:** FastMCP for another MCP client; connection pooling and caching; retry policy; cassette
storage format; whether discovery is eager or lazy.

## Definition of done

- [ ] With a trivial local MCP server fixture (a two-tool stdio server used only by tests),
      `tourganize tools list` prints its capabilities and `tourganize tools call` returns a structured
      result; both work inside the container.
- [ ] Allowlist enforcement: calling a capability the server offers but the registry omits raises
      `CapabilityNotAllowedError` **without** dispatching (asserted by a spy transport).
- [ ] Local input validation: bad arguments raise `ToolArgumentError` before any transport activity.
- [ ] Timeouts: a deliberately slow tool raises `ToolTimeoutError` within the configured window, and no
      stdio child process is left running afterwards (asserted).
- [ ] Retries happen only for `idempotent` calls; a non-idempotent failing call is attempted exactly once.
- [ ] Tool-level errors (a tool returning an error payload) produce `ToolResult(is_error=True)` rather than
      an exception, and are recorded in telemetry.
- [ ] Cassettes: `record` then `replay` reproduces results with the server absent; an unknown digest in
      `replay` raises `CassetteMissError`; equivalent argument orderings hit the same cassette.
- [ ] Telemetry contains no raw arguments (asserted by scanning recorded events for a canary traveller
      string), and the Turn Ledger's `tool_calls` counter increments.
- [ ] The broker contract suite passes for `FastMcpBroker` (against the test server), `RecordedBroker` and
      `FakeToolBroker`.
- [ ] `tourganize doctor` reports per-server reachability and capability counts, and exits non-zero only
      when a `required: true` server is unreachable.
- [ ] With `TOURGANIZE_TOOL_BROKER=none` (the default) the entire existing system — chat, export, Golden
      Conversations — behaves exactly as before.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — FastMCP is imported only under
      `tourganize/adapters/tools/fastmcp/`.

## Open questions / risks

- **Implementer's call:** whether connections are per-process or per-call; the canonical argument digest;
  eager vs. lazy discovery; how `${VAR}` interpolation in the registry is implemented.
- **Risk:** MCP SDK churn. Contained by the port and by the cassette-based tests, which do not touch the
  SDK at all.
- **Risk:** an infrastructure-only feature with no user-visible value. It earns its place by being the
  determinism boundary: F16 and F17 would otherwise each invent their own client and their own mocking,
  and world access would become untestable. That justification is repeated in `roadmap.md`.
- **Risk:** stdio servers leaking child processes in long sessions. Explicit shutdown handling plus the
  no-orphan test.
