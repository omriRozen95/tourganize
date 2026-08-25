# F11 — Golden-conversation evaluation harness

- **Bounded context:** Dialogue (test infrastructure; cross-cutting by nature)
- **Depends on:** [F08](F08-llm-gateway-and-prompt-library.md), [F10](F10-bilingual-and-rtl-handling.md)
- **Unlocks:** F12–F25 (every later feature must keep these conversations green), F21 (parity)
- **Size:** M
- **Status of the codebase when this starts:** the full conversation works bilingually with a real or fake
  model. Behaviour is covered by unit tests per component and a couple of ad-hoc scripted sessions, but
  the *conversational rules the client actually stated* are not pinned as a suite, and there is no way to
  compare two backends on identical inputs.

## Purpose

Make conversational behaviour a regression-tested asset. A **Golden Conversation** is a stored script of
traveller turns plus expected Assistant Acts and plan state, replayed through the real Director with a
scripted Fake Backend (or replayed cassettes) so it is deterministic and needs no subscription, no
network and no GPU. This is what allows the client to refactor, swap the model, or hand a feature to
someone new without silently breaking the Mentioned-First rule, the blocking-question rule, or the
choose-or-refine loop. It is also the measuring instrument F21 uses to compare Claude with the
self-hosted model.

## Starting state

From F07: `ScriptedSurface` capturing Acts. From F08: `FakeGateway` in `scripted` mode, prompt versions in
results. From F09: LLM cassettes. From F10: locale detection and Hebrew catalogues. From F06: deterministic
fixture options.

## Scope — what to implement

1. **Conversation file format** (`fixtures/conversations/*.yaml`) — declarative, reviewable, one file per
   scenario: `meta` (id, title, locale, tags), `settings` (overrides), `fixtures` (option fixture set,
   frozen clock instant, prompt version), `llm` (`fake:scripted` responses keyed by
   `(template_id, matcher)`, or `cassette: <name>`), `turns` (traveller text), and `expect` blocks
   (per-turn and final).
2. **Assertion vocabulary** — expressive enough to pin the client's rules, narrow enough not to be
   brittle: `acts` (ordered act names, with `contains`/`exact` modes), `act_payload` (JSONPath-ish key
   assertions — e.g. slate length, the field a question is about), `plan` (component statuses,
   selections by `external_ref`, declined kinds, round indices), `state`, `locale`,
   `no_act` (an act that must *not* appear — this is how "never blocked on an unmentioned component" is
   asserted), and `ledger` (call counts per template; the guard against a refactor that quietly doubles
   model calls). Assertions are on **structure and message keys**, never on composed prose, so wording
   changes do not break the suite.
3. **Runner** (`tests/conversations/runner.py` + `tourganize eval` CLI) — builds a Container from the
   file's settings, replays turns through the real Director and `ScriptedSurface`, evaluates assertions,
   and produces a **readable diff** on failure (expected vs. actual act sequence side by side, plus the
   plan state). Exit code 0/1; `--json` for CI; `--only <id>`; `--record` to fill in a new file's expected
   acts from an actual run (reviewable, never auto-committed).
4. **The starting suite** — at minimum these, each pinning something the client asked for:
   - `paris_lodging_happy_en` — mention lodging, one blocking question, slate, choose, offer, decline all,
     summary.
   - `paris_lodging_happy_he` — the same in Hebrew, asserting Hebrew message keys, RTL direction and
     Hebrew-formatted dates.
   - `blocking_question_precedes_sourcing` — asserts `no_act: present_slate` before the date range exists.
   - `refine_three_times` — three refinements, round indices 1→3, no selection, all rounds retained.
   - `refine_then_choose` — refine twice then choose from the third slate.
   - `mentioned_first_two_kinds` — flights and lodging both mentioned; asserts flights is planned first by
     weight and that ground transport is only ever *offered*.
   - `offer_accept_then_plan` — accept the offer, plan the offered kind, close.
   - `offer_decline_all_then_summary` — decline everything, summary reports declines honestly.
   - `mid_slate_locale_switch` — English opening, Hebrew refinement, Hebrew from there on.
   - `letterless_turn_keeps_locale` — `"2"` inside a Hebrew session stays Hebrew.
   - `invalid_date_range_reask` — reversed range yields `report_invalid_value`, then recovery.
   - `sourcing_failure_recovers` — a failing source yields `report_sourcing_failure` and the conversation
     continues to the next kind.
   - `end_mid_conversation` — `END_SESSION` mid-slate summarises open components, then closes.
   - `extraction_repair_then_success` — fake returns invalid then valid JSON; one `clarify` avoided,
     ledger shows 2 attempts.
5. **Backend matrix** — the same suite runnable against `fake:scripted` (CI default), `claude_code` in
   cassette-replay mode (CI), and `claude_code` live or `hosted` (opt-in, `--backend`), with per-run
   ledger totals written to `${TOURGANIZE_DATA_DIR}/eval/`. Prose assertions are automatically relaxed for
   real backends: structure must match, wording may differ. This matrix is the skeleton F21 fills in.
6. **Quality report** (`tourganize eval report`) — a table of suite pass/fail, per-conversation turn count,
   model calls, tokens, wall-clock and (when known) cost, per backend. This is the artefact the client
   reads when deciding whether the self-hosted model is good enough.
7. **CI wiring** — the suite runs on every push against the fake backend and cassettes; it is a required
   gate. A `nightly` job may run it against a live backend if credentials exist, and its failure does not
   block merges (real models are not deterministic).

## Contract (the Lego connectors)

**Inputs:** conversation YAML files; fixtures; a backend selection.

```yaml
# fixtures/conversations/paris_lodging_happy_en.yaml
meta: {id: paris_lodging_happy_en, locale: en, tags: [happy-path, lodging, offers]}
fixtures: {options: default, now: "2026-08-25T09:00:00Z", prompt_version: v1}
llm: {backend: "fake:scripted", responses: "responses/paris_lodging_happy_en.yaml"}
turns:
  - say: "find me a hotel in Paris between the 23rd and 28th of October"
    expect:
      acts: {contains: [present_slate]}
      act_payload: {present_slate.count: 3, present_slate.kind_key: lodging}
      no_act: [ask_blocking]          # both blocking rules were satisfied by this turn
  - say: "2"
    expect:
      acts: {contains: [confirm_selection, offer_unmentioned]}
      plan: {selected: {lodging: "px-hotel-002"}}
  - say: "no thanks, that's all"
    expect:
      acts: {contains: [deliver_summary, close]}
      plan: {declined: [air_travel, ground_transport]}
final:
  state: CLOSED
  ledger: {calls: {interpret_turn: 3}}
```

**Outputs:** exit status, a human-readable diff on failure, a JSON result document, and the quality
report.

**Ports consumed:** every port, through fakes — `ScriptedSurface`, `FakeGateway`, `FixtureOptionSource`,
`FrozenClock`, in-memory repository (from F12 onward).

**Ports provided:** the harness itself, plus the conventions later features extend: F17 adds tool
cassettes, F19 adds knowledge fixtures, F21 adds the backend matrix run.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_EVAL_DIR` | Conversation files root | `./fixtures/conversations` |
| `TOURGANIZE_EVAL_REPORT_DIR` | Where reports are written | `${TOURGANIZE_DATA_DIR}/eval` |
| `TOURGANIZE_EVAL_STRICT_PROSE` | Assert composed prose too (fake backends only) | `false` |

**Errors/failure modes:** a malformed conversation file fails loudly with the file and line
(`EvalDefinitionError`); a missing scripted response for a template raises rather than falling through to
a derived answer (silent fallthrough would make a broken prompt look fine); an assertion failure prints
the diff and exits 1.

## Out of scope

Judging *language quality* (a human or a rubric-based comparison in F21 does that; this harness asserts
structure). Load or latency benchmarking beyond recorded wall-clock. Any change to dialogue behaviour —
if a Golden Conversation is wrong, the fix is a spec discussion, not a quiet edit to the expectations.
Testing adapters against real networks.

## Replaceability notes

**Must be preserved:** the conversation file format and assertion vocabulary (later features add
conversations, not new formats); structure-not-prose assertions; determinism under the fake backend; the
suite as a required CI gate.

**Free to change:** the runner's implementation and diff rendering; YAML vs. another declarative format;
where reports are written; whether the runner is pytest-parametrised or a standalone CLI (recommended:
both, sharing one library).

## Definition of done

- [ ] `tourganize eval` runs all listed conversations against the fake backend and exits 0, in under 30
      seconds on a laptop with no network.
- [ ] Every one of the fourteen conversations above exists, and each asserts the rule named in its title.
- [ ] The harness bites: mutation checks prove it. Temporarily (a) letting the Director source a component
      with an unresolved blocking rule, (b) making offers before the mentioned band empties, (c) capping
      refinement rounds at one, and (d) ignoring `detected_locale`, each fail at least one conversation
      with a readable diff. Documented as four `xfail`-style guard tests using deliberately patched
      directors, so the proof lives in CI rather than in a commit message.
- [ ] `tourganize eval --backend claude_code` passes in cassette-replay mode with the CLI absent.
- [ ] `tourganize eval report` prints the per-backend table with call counts, tokens and wall-clock;
      the JSON output is committed as a sample.
- [ ] A wording-only change (edit an `en.yaml` string) leaves the whole suite green — proving prose
      independence.
- [ ] `--record` produces a runnable file for a new scenario, and a test asserts recorded files are
      byte-stable across two runs.
- [ ] Adding a conversation requires no Python change.
- [ ] The suite is a required CI gate; the nightly live-backend job is present and non-blocking.
- [ ] `mypy --strict`, `ruff` pass; the previous demo commands still work.

## Open questions / risks

- **Implementer's call:** the assertion path syntax; whether to reuse pytest parametrisation; diff
  rendering; how scripted LLM responses are keyed (suggested: `template_id` + a substring matcher on the
  turn).
- **Risk:** over-specified expectations making the suite brittle, so that people "fix" it by loosening
  assertions. Mitigation: assert only structure, message keys and plan state; keep `contains` the default
  mode; review changes to `expect` blocks as spec changes.
- **Risk:** the suite drifting into the only place bilingual behaviour is checked, hiding real terminal
  rendering bugs. F10's snapshot tests stay the authority for visual output.
