# F17 — World-backed option source and feasibility annotation

- **Bounded context:** Option Sourcing
- **Depends on:** [F06](F06-option-sourcing-and-fixture-providers.md), [F15](F15-tool-broker-and-mcp-consumer.md), [F16](F16-local-feasibility-mcp-service.md)
- **Unlocks:** F24
- **Size:** M
- **Status of the codebase when this starts:** the conversation is complete and exportable but every option
  it has ever shown came from a fixture file. The Tool Broker can reach MCP servers and the local
  feasibility service runs, but nothing in the planning path calls either.

## Purpose

Connect the planning conversation to the world. This feature adds a `WorldOptionSource` that satisfies
F06's `OptionSource` port by calling MCP capabilities through the Tool Broker, normalising whatever they
return into `PlanOption`s — and it uses the local feasibility service (F16) to **annotate** options and
the assembled plan with coherence findings the traveller can see. Fixtures remain the fallback and the
test default; the profile switch decides which is live.

## Starting state

From F06: `OptionSource`, `OptionQuery`, the source registry with a `world` profile placeholder, the
contract suite, soft optional filters, the Planning Service. From F15: broker, allowlist, cassettes. From
F16: `assess_feasibility`, `explain_conflicts`, `resolve_place`.

## Scope — what to implement

1. **World option source** (`tourganize/adapters/options/world/`):
   - map `kind_key` → capability name via `config/tools/option_capabilities.yaml` (data, so a new
     Component Kind needs no code);
   - translate `OptionQuery` → capability arguments using a **declarative field mapping** per capability
     (`requirements.place → arguments.destination`, date-range splitting, currency and passenger counts),
     with required-argument checking before dispatch;
   - normalise results into `PlanOption`s: stable `option_id` (a digest of `server_id` + the provider's
     reference so refinement rounds can reference the same option), `facts` filtered to the keys the
     display profile and feasibility rules actually use, `price` as `Money` (rejecting an unparseable
     price rather than defaulting to zero), and full `Provenance` (`source_id`, `retrieved_at`,
     `external_ref`);
   - drop malformed entries individually with a diagnostic — one bad row must not lose a whole slate;
   - honour `slate_size` and the per-source timeout, and set `partial=True` when a provider truncated.
2. **Place resolution** — when a Requirement Set carries a free-text place and a capability needs a code or
   coordinates, call `resolve_place` once per turn and **cache it on the session**; an `unknown` result
   falls back to passing the raw string through, with a diagnostic. Never fabricate a code.
3. **Feasibility annotation** (`tourganize/application/feasibility_service.py`):
   - after a Slate is assembled, build segments from the candidate option **plus the plan's existing
     Selections** and call `assess_feasibility` — one call per slate, not per option, with all candidates
     batched where the schema allows, so a slate costs one tool call;
   - attach findings to each option as structured `feasibility_notes` (codes + measured values, no prose)
     and, per [D12](../architecture/decisions.md), **demote rather than remove**: a `conflict` option sorts
     last and is clearly marked, unless `TOURGANIZE_FEASIBILITY_MODE=filter` is set explicitly;
   - annotate the whole plan at summary time so `deliver_summary` and the exported document can carry
     "your flight lands after check-in closes" as a warning line;
   - unknown finding codes are passed through as opaque (rendered via a generic message key), so F16 can
     add rules without breaking the app.
4. **Presentation and export** — extend F07's option rows and F13's `ItinerarySection` to render
   `feasibility_notes` through the Message Catalogue in both locales. Add the `feasibility.*` message keys
   (a per-code key plus a generic fallback for unknown codes).
5. **Registry and degradation** — `TOURGANIZE_OPTION_SOURCE_PROFILE=world` selects the world source per
   kind, with fixtures as the configured fallback: if the broker is unavailable, a capability is missing, or
   the call fails, log, record a diagnostic, and **fall back to the fixture source** for that kind so the
   conversation continues (`TOURGANIZE_OPTION_FALLBACK=fixture|none`). Feasibility being unavailable
   removes annotations only — never options.
6. **Cassettes and Golden Conversations** — record cassettes for one lodging search, one air-travel search
   and the feasibility assessment; add two conversations: `world_lodging_replay` (world source in replay
   mode, asserting normalised options and provenance) and `feasibility_warning_shown` (a deliberately
   incoherent flight/hotel pair, asserting the warning reaches both the slate and the export).
7. **Telemetry** — per sourcing call: capability, server, latency, raw and kept option counts, dropped-row
   count, fallback used, feasibility verdict distribution. These are the numbers that tell the client
   whether a real provider is worth adding (F24).

## Contract (the Lego connectors)

**Inputs:** an `OptionQuery` (F06); MCP capability results; feasibility responses.

```yaml
# config/tools/option_capabilities.yaml
version: 1
mappings:
  - kind_key: lodging
    server_id: world_travel
    capability: search_lodging
    arguments:
      destination: {from: requirements.place, required: true}
      check_in:    {from: requirements.date_range.start, required: true}
      check_out:   {from: requirements.date_range.end, required: true}
      guests:      {from: requirements.guests, default: 2}
      max_price:   {from: requirements.budget_ceiling.amount, optional: true}
    result:
      items_path: options
      option_ref: id
      price: {amount: price.amount, currency: price.currency}
      facts: [name, area, review_score, refundable, room_type, latitude, longitude]
```

**Outputs:** `OptionSourceResult`s indistinguishable in shape from the fixture source (the F06 contract
suite is the proof); options carrying `feasibility_notes`; annotated summaries and exports.

**Ports consumed:** `ToolBroker` (F15), `OptionSource` (fixture, as fallback), `ComponentCatalog`, `Clock`,
`TelemetrySink`.

**Ports provided:** `WorldOptionSource` (a second `OptionSource` implementation) and the feasibility
annotation service.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_OPTION_CAPABILITIES_FILE` | Kind → capability mappings | `${TOURGANIZE_CONFIG_DIR}/tools/option_capabilities.yaml` |
| `TOURGANIZE_OPTION_FALLBACK` | `fixture` / `none` when a world call fails | `fixture` |
| `TOURGANIZE_FEASIBILITY_MODE` | `annotate` / `filter` / `off` | `annotate` |
| `TOURGANIZE_PLACE_RESOLUTION` | `mcp` / `off` | `mcp` |

**Errors/failure modes:** `OptionMappingError` (a mapping file referencing an unknown requirement field —
fails at start-up, surfaced by `doctor`); missing required arguments produce a diagnostic and a fixture
fallback rather than an exception; broker errors are caught per source. `OptionSourcingError` only when the
world source *and* the configured fallback both fail.

## Out of scope

Commercial provider APIs with keys, quotas and terms of use (F24 — one feature per provider). Booking,
holding or paying for anything. Caching option results across sessions. Changing the feasibility service
(F16 owns its rules). Letting a model choose which capability to call
([D2](../architecture/decisions.md)).

## Replaceability notes

**Must be preserved:** the `OptionSource` contract and its suite (a world source is not special); the
declarative mapping file as the way kinds reach capabilities; annotate-not-filter as the default; opaque
handling of unknown finding codes; the fixture fallback.

**Free to change:** the mapping dialect; normalisation internals; whether feasibility is called per slate
or per option; the caching of place resolution; the demotion ranking.

## Definition of done

- [ ] With `TOURGANIZE_OPTION_SOURCE_PROFILE=world` and cassettes in `replay` mode, a full lodging
      conversation runs on world data: options show real-looking names, prices and provenance naming the
      server, and the F06 contract suite passes for `WorldOptionSource`.
- [ ] Adding a mapping for a fourth Component Kind requires no Python change (proved with a fixture
      mapping and cassette).
- [ ] Normalisation robustness: unit tests over recorded payloads containing a missing price, a null
      review score, an unparseable date and an extra unknown field — each bad row is dropped with a
      diagnostic while the rest of the slate survives.
- [ ] `option_id` stability: the same option across two identical queries has the same id (so a refinement
      round can reference it), asserted.
- [ ] Feasibility annotation: the `feasibility_warning_shown` conversation asserts a `warn` finding is
      rendered in the slate row and appears in the exported document, in both `en` and `he`.
- [ ] Demotion not removal: an option with a `conflict` finding is still present, sorted last, and marked;
      with `TOURGANIZE_FEASIBILITY_MODE=filter` it is absent (both asserted).
- [ ] Unknown finding code: a cassette containing an invented code renders via the generic message key
      without raising.
- [ ] Degradation: with the broker disabled mid-run, sourcing falls back to fixtures, the session
      completes, and a diagnostic plus telemetry record the fallback; with
      `TOURGANIZE_OPTION_FALLBACK=none` the Director emits `report_sourcing_failure` and continues.
- [ ] Feasibility service down → options are still presented, annotations are absent, no error reaches the
      traveller (asserted).
- [ ] One tool call per slate (not per option) — asserted on the broker spy's call count.
- [ ] Place resolution is called at most once per turn and cached on the session (asserted).
- [ ] Telemetry includes raw vs. kept option counts and the fallback flag; `doctor` validates the mapping
      file.
- [ ] All existing Golden Conversations still pass on the `fixture` profile, unchanged.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `adapters.options.world` imports the broker port, not
      FastMCP.

## Open questions / risks

- **Risk:** no real MCP travel server may exist to point this at. That is why the cassettes and the
  fixture fallback are part of the DoD: the feature is complete and testable against recorded data, and
  pointing it at a real server later is a registry entry.
- **Risk:** normalisation becoming per-provider special-casing. If the mapping file cannot express a
  provider, the honest answer is a dedicated adapter under F24, not conditionals here.
- **Risk:** feasibility annotations reading as scolding. Wording is F10's, and the default is `annotate`;
  the client may prefer them only in the export — a one-line policy change.
- **Open (client):** which world MCP server(s), if any, they intend to use — see overview §9, question 2.
