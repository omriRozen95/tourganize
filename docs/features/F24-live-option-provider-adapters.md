# F24 — Live option provider adapters

- **Bounded context:** Option Sourcing
- **Depends on:** [F06](F06-option-sourcing-and-fixture-providers.md), [F17](F17-world-backed-option-source.md)
- **Unlocks:** nothing structurally — it replaces fixture content with commercial data
- **Size:** M **per provider** (one feature instance per provider; do not batch three providers into one)
- **Track:** **deferred / optional** — blocked on the client supplying accounts, keys and terms of use
- **Status of the codebase when this starts:** options come from fixtures or from MCP capabilities, both
  behind the `OptionSource` port, with feasibility annotation, soft filters, deterministic tests and a
  contract suite. Nothing has ever called a commercial travel API.

## Purpose

Replace fixture content with real inventory for one Component Kind at a time, behind the **same**
`OptionSource` port ([D9](../architecture/decisions.md)) — so the conversation, the presentation, the
export and every test stay exactly as they are, and the only thing that changes is where options come from.
The port's contract suite is what proves a live adapter has not quietly changed the shape of the world.

## Starting state

From F06: `OptionSource`, `OptionQuery`, the registry with per-kind profiles, soft optional filters, the
contract suite that every adapter must pass. From F17: declarative query/result mapping, normalisation
discipline, `feasibility_notes`, telemetry, and the fixture-fallback mechanism.

## Scope — what to implement (per provider)

1. **Provider adapter** (`tourganize/adapters/options/live/<provider>/`) — implements `OptionSource` for
   one or more `kind_keys`: authenticated HTTP client, request building from the `OptionQuery`, response
   normalisation into `PlanOption`s with full `Provenance` (including the provider's own reference and
   retrieval timestamp), and mapping of provider errors onto the existing failure vocabulary. **No new
   exception types and no new port**: if a provider cannot be expressed through `OptionQuery`, that is a
   finding to report, not a licence to widen the domain.
2. **Credentials** — read via `Settings`/`SecretValue` only (F01), never logged, never in an image, never
   in a fixture. `doctor` reports "configured / not configured" and nothing more.
3. **Quota discipline** — a per-provider rate limiter and a monthly/daily call budget
   (`..._MAX_CALLS_PER_DAY`); exceeding it degrades to the fixture source with a diagnostic rather than
   failing the conversation, and emits a loud telemetry event. Refinement loops are the risk here: a
   traveller refining ten times must not silently burn a paid quota, so repeated identical queries within a
   session are served from a **per-session response cache** keyed by `requirements.digest()`.
4. **Terms-of-use compliance** — a committed note per provider recording: what the licence permits
   (caching duration, display requirements, attribution, whether prices may be stored or exported),
   required attribution text wired into the display profile and the exported document, and any prohibition
   the design must honour. Where the terms forbid something the product does (e.g. persisting prices in an
   exported PDF), that is a **blocking finding for the client**, surfaced before the adapter ships.
5. **Recorded fixtures** — record real responses (with credentials and personal data scrubbed) as cassettes
   for tests, plus **contract-drift tests**: a scheduled job replaying recorded requests against the live
   API and diffing response *shape* (not content), so a provider's breaking change is caught before a
   traveller sees it.
6. **Registry activation** — enable the provider for its kinds via
   `TOURGANIZE_OPTION_SOURCE_PROFILE=lodging=live,air_travel=fixture` (per-kind, so migration is
   incremental) with `TOURGANIZE_OPTION_FALLBACK=fixture` retained.
7. **Data minimisation** — send only what the search needs. Traveller free text is never forwarded; only
   the structured Requirement Values a mapping declares are sent, and the request digest (not the request)
   goes to telemetry — the same rule as F15.

## Contract (the Lego connectors)

**Inputs:** an `OptionQuery` (F06); provider credentials; a per-kind mapping.

**Outputs:** `OptionSourceResult`s **indistinguishable in shape** from the fixture source's — that is the
whole contract, and the F06 suite is its test.

```python
class LiveOptionSource:                    # implements OptionSource, one per provider
    source_id = "provider-name"
    kind_keys = frozenset({"lodging"})
    def __init__(self, http: HttpClient, credentials: ProviderCredentials,
                 limiter: RateLimiter, cache: SessionResponseCache,
                 mapping: ProviderMapping) -> None: ...
    def search(self, query: OptionQuery) -> OptionSourceResult: ...
```

**Ports consumed:** `Clock`, `TelemetrySink`, the fixture `OptionSource` (as configured fallback).

**Ports provided:** an additional `OptionSource` implementation. No new port.

**Config/env keys introduced (per provider, prefixed by its id):**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_PROVIDER_<ID>_API_KEY` | Credential (secret) | unset |
| `TOURGANIZE_PROVIDER_<ID>_BASE_URL` | API base URL | provider default |
| `TOURGANIZE_PROVIDER_<ID>_RATE_PER_MINUTE` | Client-side rate limit | provider-documented |
| `TOURGANIZE_PROVIDER_<ID>_MAX_CALLS_PER_DAY` | Budget before degrading to fixtures | `500` |
| `TOURGANIZE_PROVIDER_<ID>_TIMEOUT_SECONDS` | Per-call timeout | `20` |
| `TOURGANIZE_PROVIDER_CACHE_TTL_SECONDS` | Per-session response cache lifetime | `900` |

**Errors/failure modes:** authentication failure → `BackendUnavailableError`-equivalent for this source,
reported by `doctor`, degrading to fixtures; rate limit → wait-then-degrade;
provider 5xx → retry (idempotent searches only) then degrade; malformed rows dropped individually with
diagnostics (F17's rule). `OptionSourcingError` only when the live source and the fallback both fail.

## Out of scope

Booking, holding, payment, or any state-changing provider call — Tourganize plans, it does not transact,
and adding that is a different product with different legal obligations. Cross-session or shared caching of
provider data (licence-dependent). Price monitoring or alerts. Aggregating multiple providers into one
ranked market view beyond F06's existing merge.

## Replaceability notes

**Must be preserved:** the `OptionSource` contract and suite; fixtures remaining the test default forever;
per-kind profile switching; credentials only through `SecretValue`; the fixture fallback.

**Free to change:** everything inside the adapter — client library, pagination, mapping, caching, retry.
Removing a provider must be a configuration change plus deleting one directory.

## Definition of done (per provider)

- [ ] The **F06 `OptionSource` contract suite passes** for the live adapter (against recorded cassettes in
      CI, and once against the live API manually).
- [ ] A full conversation runs on live data for the enabled kind: real names, prices and provenance appear
      in the slate, the refinement loop works, and the exported document carries the required attribution.
- [ ] Per-kind switching works: `lodging=live,air_travel=fixture` sources each kind from its configured
      profile (asserted through the registry).
- [ ] Credentials: absent credentials degrade to fixtures with a diagnostic and a `doctor` warning; a test
      asserts no credential appears in logs, telemetry, cassettes or errors.
- [ ] Quota discipline: exceeding `MAX_CALLS_PER_DAY` degrades to fixtures with a loud telemetry event; ten
      identical refinements within a session make **one** provider call (cache asserted).
- [ ] Data minimisation: a test asserts the outbound request contains no traveller free text and that
      telemetry stores only the request digest.
- [ ] Failure paths: 401, 429, 500 and a timeout each behave as documented, and the conversation always
      continues.
- [ ] Malformed rows are dropped individually; a cassette with three bad rows out of ten yields seven
      options.
- [ ] The terms-of-use note is committed, including caching and attribution obligations; any conflict with
      current product behaviour is raised with the client **before** the adapter is enabled by default.
- [ ] The contract-drift job exists and passes; its failure output names the changed fields.
- [ ] All fixture-profile Golden Conversations pass unchanged; `mypy --strict`, `ruff`, `lint-imports` pass.

## Open questions / risks

- **Blocked on the client:** which providers, whose accounts, and under what terms (overview §9,
  question 2). Until answered this feature cannot start, and nothing else waits for it.
- **Risk:** provider semantics not fitting `OptionQuery` (multi-city itineraries, fare families, availability
  that changes between search and display). The honest response is a spec finding and possibly a new
  Component Kind or requirement field — not a leaky adapter.
- **Risk:** cost surprises from refinement loops. The per-session cache and daily budget are the guards, and
  the telemetry makes spend visible per session.
- **Risk:** licence terms that forbid storing prices in an exported document would collide with the
  product's core deliverable. Flagged deliberately as a client-blocking question rather than discovered
  late.
