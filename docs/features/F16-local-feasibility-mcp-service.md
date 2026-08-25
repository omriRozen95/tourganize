# F16 — Local MCP service: itinerary feasibility

- **Bounded context:** Option Sourcing (own deployable service)
- **Depends on:** [F15](F15-tool-broker-and-mcp-consumer.md)
- **Unlocks:** F17
- **Size:** M
- **Status of the codebase when this starts:** the Tool Broker exists with a FastMCP client, an allowlist,
  cassettes and a fake, exercised only against a throwaway test server. Tourganize consumes MCP but
  provides none, and the client's requirement for **at least one local MCP service** (C12) is unmet.

## Purpose

Build the local MCP service chosen in [D12](../architecture/decisions.md): an **Itinerary Feasibility**
service that answers whether a set of Plan Options actually coheres — do the connection times work, do the
lodging dates cover the arrival, is the drive reachable, what does the whole thing cost — and explains its
conflicts. It is pure computation over data it is given: no network, no database, fully deterministic, so
it is testable offline and useful the day it lands. It also exercises the FastMCP **server** side (typed
tools, structured output, structured errors), completing C11/C12 from both directions.

Chosen from five candidates; the alternatives and why they were deferred are recorded in
[D12](../architecture/decisions.md).

## Starting state

From F15: the broker, the server registry (which already contains a commented `feasibility` entry), the
allowlist mechanism, cassettes, `tourganize tools call`. From F02: `PlanOption`/`Selection` shapes, whose
`facts` this service reads — **by contract, not by import**.

## Scope — what to implement

1. **Service package** (`services/mcp_feasibility/`) — a standalone FastMCP server, deployable as its own
   container, that **must not import `tourganize`**. It is a separate bounded piece of software that
   communicates through a JSON contract; a CI check enforces the no-import rule. Shared vocabulary is
   duplicated deliberately (a small, documented, versioned input schema) rather than coupling the two.
2. **Tool 1 — `assess_feasibility`** — input: a set of `segments` (each: `kind_key`, `starts_at`,
   `ends_at`, `from_place`, `to_place`, `price`, plus a free `facts` bag) and `assumptions` overrides.
   Output: `verdict` (`ok` | `warn` | `conflict`), `findings` (each with `code`, `severity`, `segments`
   involved, `detail_key`, `measured`, `threshold`), `assumptions_used`, and `totals` per currency. Rules,
   all parameterised and all reported:
   - **connection time** between consecutive air-travel segments below the minimum (default 90 min
     international / 45 domestic);
   - **lodging coverage**: arrival before check-in, departure after check-out, uncovered nights;
   - **date ordering**: overlapping or reversed segments;
   - **ground reachability**: distance ÷ assumed average speed vs. the available window (great-circle
     distance from supplied coordinates when present; skipped with a finding when not — never invented);
   - **budget roll-up** against an optional ceiling, per currency, never summing across currencies;
   - **timezone sanity**: offsets present and consistent, since naive local times are the classic source
     of a plan that looks fine and is not.
3. **Tool 2 — `explain_conflicts`** — input: the findings from an assessment; output: an ordered,
   locale-neutral explanation structure (`detail_key` + measured values) that Tourganize renders through
   its own Message Catalogue. **The service returns no prose**: it emits keys and numbers, so bilingual
   wording stays with F10 where it belongs.
4. **Tool 3 — `resolve_place` (minimal)** — best-effort lookup of a place name to coordinates and a
   timezone from a small **bundled** offline table (major airports and cities), returning `unknown`
   rather than guessing. Reason for including it: without coordinates, reachability is unassessable, and a
   50-line bundled table beats an external dependency. Licence of the bundled data recorded in the
   service's README.
5. **Assumption transparency** — every response carries `assumptions_used`, and every finding carries the
   threshold that produced it. Defaults live in `services/mcp_feasibility/assumptions.yaml`, overridable
   per call. This is what keeps the service *advisory*: Tourganize annotates, it does not silently veto
   ([D12](../architecture/decisions.md)).
6. **Structured errors** — invalid input yields an MCP tool error with a machine-readable code
   (`invalid_segment`, `missing_timezone`, …), which F15's broker surfaces as `ToolResult(is_error=True)`.
7. **Container and compose** — `docker/mcp_feasibility.Dockerfile` (slim, CPU-only, no app dependencies)
   and an `mcp` compose profile. Two transports supported and both documented: `stdio` (default for dev —
   the app spawns it, nothing to orchestrate) and `http` (for the production profile where it runs as a
   long-lived service).
8. **Registry activation** — enable the `feasibility` entry in `config/tools/servers.yaml` with its
   allowlist, and record cassettes for the standard cases so the app's tests never need the service
   running.
9. **Service-side tests** (`services/mcp_feasibility/tests/`) — independent of the app's suite: unit tests
   per rule (each with a passing and a failing case), a property test that `verdict=ok` implies zero
   `conflict`-severity findings, and an in-process FastMCP client test asserting the tool schemas.

## Contract (the Lego connectors)

**Inputs:** MCP tool calls with JSON arguments (schema version `1`).

```json
// assess_feasibility — request
{"schema_version": 1,
 "segments": [
   {"segment_id": "a1", "kind_key": "air_travel",
    "from_place": "TLV", "to_place": "CDG",
    "starts_at": "2026-10-23T06:20:00+03:00", "ends_at": "2026-10-23T11:05:00+02:00",
    "price": {"amount_minor": 41000, "currency": "EUR"}},
   {"segment_id": "h1", "kind_key": "lodging", "at_place": "Paris",
    "starts_at": "2026-10-23T15:00:00+02:00", "ends_at": "2026-10-28T11:00:00+02:00",
    "price": {"amount_minor": 74000, "currency": "EUR"}}],
 "assumptions": {"min_connection_minutes_international": 90, "avg_drive_kmh": 70},
 "budget_ceiling": {"amount_minor": 150000, "currency": "EUR"}}
```

```json
// assess_feasibility — response
{"schema_version": 1, "verdict": "warn",
 "findings": [{"code": "lodging_late_check_in", "severity": "warn",
               "segments": ["a1", "h1"], "detail_key": "feasibility.lodging_late_check_in",
               "measured": {"gap_minutes": 235}, "threshold": {"gap_minutes": 0}}],
 "assumptions_used": {"min_connection_minutes_international": 90, "avg_drive_kmh": 70},
 "totals": [{"currency": "EUR", "amount_minor": 115000, "within_budget": true}]}
```

**Outputs:** the structured verdict above; MCP tool errors with codes.

**Ports consumed:** none — this service has no dependency on Tourganize's ports. It is reached *through*
`ToolBroker` from the app side.

**Ports provided:** three MCP capabilities: `assess_feasibility`, `explain_conflicts`, `resolve_place`.

**Config/env keys introduced (service side, not `TOURGANIZE_*`):**

| Key | Meaning | Default |
|---|---|---|
| `FEASIBILITY_TRANSPORT` | `stdio` / `http` | `stdio` |
| `FEASIBILITY_HTTP_PORT` | Port for HTTP transport | `8091` |
| `FEASIBILITY_ASSUMPTIONS_FILE` | Default assumption overrides | bundled file |
| `FEASIBILITY_LOG_LEVEL` | Log level | `INFO` |

**Errors/failure modes:** structured tool errors for invalid input; a missing timezone yields a finding
(`missing_timezone`, severity `warn`) rather than an error, because a partially specified plan is the
normal case mid-conversation; unknown places yield `unknown` from `resolve_place` and a skipped
reachability check with an explanatory finding.

## Out of scope

Any use of the verdict inside the conversation (F17 wires it and decides how it is presented). Live
transport data, real routing, traffic, or airline minimum-connection tables. Prose in any language.
Persistence. Authentication (it is a local service on a private network; if it is ever exposed, that is a
new decision).

## Replaceability notes

**Must be preserved:** the three capability names, the request/response schemas with their
`schema_version`, the advisory nature (a `conflict` verdict is information, never an enforced veto), the
no-prose rule, and the no-`tourganize`-import rule.

**Free to change:** every rule's internals and thresholds; the bundled place table; adding new findings
(consumers must treat unknown `code`s as opaque — asserted on the app side in F17); FastMCP internals;
transport.

## Definition of done

- [ ] `docker compose --profile mcp up mcp-feasibility` starts the service; from the app container,
      `tourganize tools list --server feasibility` shows all three capabilities with their schemas.
- [ ] `tourganize tools call assess_feasibility --args @fixtures/feasibility/tlv_cdg.json` returns the
      documented structure with `verdict`, `findings`, `assumptions_used` and `totals`.
- [ ] Each rule has a passing and a failing unit test: short connection, uncovered lodging night, reversed
      dates, unreachable drive, budget exceeded, missing timezone.
- [ ] Determinism: identical input yields identical output across two runs and two processes (asserted).
- [ ] No prose: a test asserts every response string is either an enum code or a `*_key`, containing no
      spaces.
- [ ] Advisory: a `conflict` verdict is returned as data with findings; the service exposes no mechanism to
      filter or reject options.
- [ ] Invalid input (a segment with `ends_at` before `starts_at`, a malformed price) yields a structured
      tool error whose code the broker surfaces as `is_error=True`.
- [ ] Isolation: a CI check asserts `services/mcp_feasibility/` does not import `tourganize`, and the
      service's own test suite runs with only its own dependencies installed.
- [ ] Both transports work: the same assertions pass over `stdio` and over `http`.
- [ ] Cassettes for the standard cases are recorded and committed; the app's test suite passes with the
      service **not** running.
- [ ] `assumptions_used` appears in every response, and a per-call override changes a verdict (asserted).
- [ ] The service README documents each rule, its default threshold, and the bundled data's licence.
- [ ] The app is unaffected when the service is down: `doctor` reports it as unreachable and non-required,
      and every existing flow still works.

## Open questions / risks

- **Implementer's call:** exact default thresholds; whether `resolve_place` is a separate tool or an
  internal helper (recommended: a tool — it is independently useful and independently testable); the size
  of the bundled place table.
- **Risk:** heuristic thresholds treated as truth. Mitigated by `assumptions_used` in every response and by
  keeping the service advisory; the moment a finding silently removes an option from a Slate, the design has
  drifted.
- **Risk:** schema duplication between service and app drifting. Mitigated by `schema_version` in both
  directions and by the cassettes failing loudly when shapes change.
- **Open (client):** would candidate 2 (Geo & Seasonality: holidays, climate, visa-free lookup) be more
  valuable as a demo than feasibility? It is a strictly additive second service if so.
