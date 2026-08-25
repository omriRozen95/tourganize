# F05 — Dialogue director: session lifecycle, blocking questions and the choose-or-refine loop

- **Bounded context:** Dialogue
- **Depends on:** [F02](F02-trip-plan-domain-core.md), [F03](F03-requirement-schemas-and-gap-analysis.md), [F04](F04-component-prioritization-policy.md)
- **Unlocks:** F06, F07, F08, F11, F12
- **Size:** L — *deliberately kept whole; see "why this is not split" below*
- **Status of the codebase when this starts:** the planning domain is complete and inert. Trip Plans,
  Requirement Schemas, Gap Reports and the Planning Agenda all exist and are tested, but nothing drives
  them: there is no conversation, no turn, no state.

## Purpose

Turn the inert domain into a conversation. This feature owns the **Planning Session** and the explicit
**state machine** that implements every behavioural rule the client stated: resolve blocking gaps before
planning a component, present an Option Slate, accept a choice **or** a refinement and re-plan the same
component any number of times, offer to plan the components the traveller never mentioned once the
mentioned ones are settled, and close the session on their answer. It emits **Assistant Acts** —
structured intents to communicate — and consumes **Turn Interpretations** through a port, so it works
today with a deterministic keyword interpreter and tomorrow with an LLM, unchanged
([D2](../architecture/decisions.md)).

### Why this is not split

The choose-or-refine loop, blocking-question resolution and proactive offers are three faces of one
state machine; splitting them would ship two features whose Definition of Done cannot be written as
observable behaviour ("half a state machine"). Instead the sizing risk is managed by building it in the
order in the Scope list — states 1–4 are testable before states 5–7 exist — and by the fact that F05 has
no I/O at all, so its entire DoD is unit-testable without any adapter.

## Starting state

From F01–F04: `Settings`, `Clock`, `TelemetrySink`; `TripPlan`, `PlanComponent`, `ComponentStatus`,
`OptionSlate`, `Selection`; `RequirementSet`, `GapReport`, `analyse()`; `build_agenda()` and
`PlanningAgenda`. No dialogue package contents, no surface, no LLM.

## Scope — what to implement

1. **Session aggregate** (`tourganize/dialogue/session.py`):
   `PlanningSession` — `session_id`, `created_at`, `locale: str`, `state: DialogueState`,
   `plan: TripPlan`, `transcript: tuple[TranscriptEntry, ...]`, `focus_kind: str | None`,
   `turn_index: int`, `pending_question: PendingQuestion | None`,
   `offer_queue: tuple[str, ...]`, `schema_version: int`. Mutation happens only through the Director.
2. **Turn and act types** (`tourganize/dialogue/turns.py`):
   - `UserTurn` — `index`, `text`, `received_at`, `locale_hint`.
   - `TurnIntent` enum — `STATE_REQUEST`, `ANSWER_QUESTION`, `CHOOSE_OPTION`, `REFINE`, `ACCEPT_OFFER`,
     `DECLINE_OFFER`, `END_SESSION`, `SMALL_TALK`, `UNKNOWN`.
   - `TurnInterpretation` — `intent`, `mentioned_kinds`, `requirement_updates`, `chosen_option_ref`,
     `detected_locale`, `confidence`, `notes`.
   - `AssistantAct` — `act` (see act vocabulary below), `payload: Mapping[str, object]`, `locale`,
     `kind_key: str | None`. Payloads are **locale-neutral structured data**: message keys, field names,
     Plan Options, never composed sentences.
   - Act vocabulary (closed set): `greet`, `ask_blocking`, `ask_optional`, `report_invalid_value`,
     `present_slate`, `confirm_selection`, `offer_unmentioned`, `deliver_summary`, `clarify`,
     `report_sourcing_failure`, `close`.
3. **State machine** (`tourganize/dialogue/states.py`, `director.py`):
   `DialogueState` — `GREETING`, `INTERPRETING`, `ELICITING_BLOCKING`, `ELICITING_OPTIONAL`, `SOURCING`,
   `PRESENTING_SLATE`, `AWAITING_CHOICE`, `REFINING`, `OFFERING_UNMENTIONED`, `SUMMARISING`, `CLOSED`.
   The transition table is **data** (`TRANSITIONS: Mapping[DialogueState, frozenset[DialogueState]]`) and
   every transition passes through one guarded `_transition()`; an illegal transition raises
   `IllegalDialogueTransitionError`. `DialogueDirector.handle(turn) -> tuple[AssistantAct, ...]` is the
   only entry point, and it is **pure with respect to I/O**: sourcing enters through the
   `OptionSourcePlanner` callable injected at construction (implemented in F06; a fake supplies slates
   here).
4. **Blocking-question resolution** — on focusing a component: `analyse()` it; while
   `report.blocking` is non-empty, emit `ask_blocking` for `report.next_blocking()` and store a
   `PendingQuestion` (`field names`, `rule name`, `asked_on_turn`, `attempts`). An `ANSWER_QUESTION`
   turn merges the updates and re-analyses. **One blocking question per Act** — never a wall of
   questions. After `TOURGANIZE_DIALOGUE_MAX_REASKS` failed attempts on the same rule, emit `clarify`
   with the field's example and, if it still fails, mark the component `FAILED` and move on rather than
   loop forever. Invalid-but-present values produce `report_invalid_value` (naming field and reason
   key), which is a re-ask, not a rejection of the turn.
5. **Opportunistic optional filters** — optional gaps are **never** blocking. At most
   `TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT` (default 2) optional fields may be bundled into a single
   `ask_optional` Act, emitted *alongside* the first slate presentation for a component, and never
   re-asked if the traveller ignores them. Any turn may still supply them.
6. **Choose-or-refine loop** — in `AWAITING_CHOICE`:
   - `CHOOSE_OPTION` → resolve `chosen_option_ref` against the latest slate (accept a 1-based ordinal or
     an `option_id`); record the Selection; `confirm_selection`; advance the agenda.
   - `REFINE` → merge `requirement_updates` into the component's Requirement Set, re-analyse (a
     refinement may *introduce* a blocking gap by invalidating a value — then go back to
     `ELICITING_BLOCKING`), otherwise re-enter `SOURCING` for the **same** `kind_key` with
     `round_index + 1`. Unbounded rounds; a monotonically increasing `round_index` is the only bookkeeping.
   - An unresolvable choice reference (out of range, unknown id) → `clarify`, state unchanged.
   - A turn that mentions a *different* Component Kind while awaiting a choice: record the mention (so
     the agenda knows), finish the current component first (Mentioned-First applies to the *agenda*, not
     to interrupting a slate), and note it in the `confirm_selection` payload.
7. **Proactive offers** — when `agenda.is_mentioned_band_empty()` and unmentioned, non-declined kinds
   remain: emit `offer_unmentioned` naming the top-ranked one (or up to
   `TOURGANIZE_DIALOGUE_OFFER_BATCH`, default 2, as a single Act). `ACCEPT_OFFER` marks those kinds
   mentioned-by-offer and re-enters planning; `DECLINE_OFFER` calls `plan.decline(kind_key)` and offers
   the next, until the offer queue is empty → `SUMMARISING`. A declined kind is never offered again in
   the same session.
8. **Closing** — `SUMMARISING` emits `deliver_summary` with the `PlanCompleteness` and the Selections,
   then `close` → `CLOSED`. `END_SESSION` from any state jumps to `SUMMARISING` (a traveller may leave
   mid-slate; the summary then reports open components honestly). A turn arriving in `CLOSED` raises
   `SessionClosedError` — resuming is F12's job, not a silent reopen.
9. **Keyword Turn Interpreter** (`tourganize/adapters/interpretation/keyword/`) — deterministic, no
   model: locale by script detection (Hebrew block → `he`), intent by a **configurable** phrase table
   (`config/interpretation/keywords.<locale>.yaml`), mentioned kinds by per-kind keyword lists, ordinals
   and quoted ids as choice references, and ISO-ish dates/places by regex. Explicitly a **stand-in**:
   its DoD is only that it makes the machine driveable and the Golden Conversations writable. F08
   replaces it.
10. **Turn telemetry** — every `handle()` records one event: `session_id`, `turn_index`, state before and
    after, intent, `focus_kind`, acts emitted, agenda `explain()`, latency. This is the skeleton that
    F08 enriches with model tokens and cost.

## Contract (the Lego connectors)

**Inputs:** `UserTurn` (from any surface), `TurnInterpretation` (from the `TurnInterpreter` port),
`OptionSlate` (from the injected planner callable).

**Outputs:** a tuple of `AssistantAct`s per turn, plus mutations to the `PlanningSession`.

```python
class TurnInterpreter(Protocol):                        # tourganize/ports/interpretation.py
    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation: ...

@dataclass(frozen=True)
class DialogueContext:              # everything an interpreter may know — no session object leaks out
    state: DialogueState
    locale: str
    focus_kind: str | None
    pending_question: PendingQuestion | None
    slate_option_refs: tuple[str, ...]
    known_kind_keys: tuple[str, ...]
    turn_index: int

class OptionSlatePlanner(Protocol):                     # implemented by F06's planning service
    def plan(self, kind_key: str, requirements: RequirementSet,
             plan: TripPlan, round_index: int) -> OptionSlate: ...

class DialogueDirector:
    def __init__(self, catalog: ComponentCatalog, policy: PriorityPolicy,
                 interpreter: TurnInterpreter, planner: OptionSlatePlanner,
                 clock: Clock, telemetry: TelemetrySink, settings: DialogueSettings) -> None: ...
    def begin(self, locale: str = "en") -> tuple[AssistantAct, ...]: ...      # emits greet
    def handle(self, turn: UserTurn) -> tuple[AssistantAct, ...]: ...
    @property
    def session(self) -> PlanningSession: ...
```

**Ports consumed:** `ComponentCatalog`, `PriorityPolicy` (F02/F04), `TurnInterpreter` (introduced here,
keyword adapter here), `OptionSlatePlanner` (introduced here, fake here → real in F06), `Clock`,
`TelemetrySink`.

**Ports provided:** `TurnInterpreter` and `OptionSlatePlanner` protocols; `PlanningSession` and
`AssistantAct` as the contract every surface (F07, F25) and the harness (F11) consume.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_DIALOGUE_MAX_REASKS` | Attempts on one blocking rule before `clarify`, then `FAILED` | `3` |
| `TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT` | Optional fields bundled into one `ask_optional` | `2` |
| `TOURGANIZE_DIALOGUE_OFFER_BATCH` | Unmentioned kinds named in one `offer_unmentioned` | `2` |
| `TOURGANIZE_INTERPRETER` | `keyword` (here) or `model` (F08) | `keyword` |
| `TOURGANIZE_KEYWORD_CONFIG_DIR` | Phrase tables for the keyword interpreter | `${TOURGANIZE_CONFIG_DIR}/interpretation` |

**Errors/failure modes:** `IllegalDialogueTransitionError` (a bug, never a traveller's fault);
`SessionClosedError` on a turn after close; a sourcing failure from the planner is caught and becomes a
`report_sourcing_failure` Act plus a `FAILED` component after `TOURGANIZE_AGENDA_FAILURE_SKIP`
failures — **the conversation never dies because a provider did**. An interpreter raising is caught once
and becomes `clarify`; raising twice in a row propagates (the interpreter is broken, not the input).

## Out of scope

Wording: no Act payload may contain a composed sentence — phrasing is F10's Message Catalogue and F08's
Composition calls. Drawing anything on a screen (F07). Real option data (F06). Any model call (F08).
Persistence and resume (F12). Export (F13). Multi-session or multi-traveller concerns (out of scope
project-wide per [D8](../architecture/decisions.md)).

## Replaceability notes

**Must be preserved:** `AssistantAct`'s closed act vocabulary and locale-neutral payloads;
`DialogueDirector.handle(turn) -> tuple[AssistantAct, ...]` as the only entry point; the
`TurnInterpreter` and `OptionSlatePlanner` protocols; `PlanningSession`'s field set (F12 serialises it);
and the behavioural invariants — blocking gaps before sourcing, unbounded refinement rounds, offers only
after the mentioned band empties, declined kinds never re-offered.

**Free to change:** the internal transition table and state names *if* the invariants and the act
vocabulary hold; how `PendingQuestion` is tracked; the keyword interpreter entirely (it is scaffolding);
telemetry field names.

## Definition of done

Behavioural criteria are stated as scenarios; each is a test using the keyword interpreter and a fake
planner returning fixed slates.

- [ ] **Blocking before planning:** given *"find me a hotel in Paris"* (no dates), the first Act is
      `ask_blocking` for the `when` rule and **no** slate is produced; after *"23–28 October 2026"*, a
      `present_slate` Act for `lodging` follows.
- [ ] **One question at a time:** with both `where` and `when` missing, exactly one `ask_blocking` Act is
      emitted per turn.
- [ ] **Optional never blocks:** with all blocking rules satisfied and every optional field empty, a
      slate is presented on that same turn, accompanied by at most one `ask_optional` Act naming at most
      2 fields; ignoring it does not re-ask.
- [ ] **Invalid value:** a reversed date range yields `report_invalid_value` naming the field, and the
      component does not advance to `SOURCING`.
- [ ] **Choose:** selecting *"2"* records a Selection referencing the second option of the latest slate,
      emits `confirm_selection`, and moves focus to the next agenda entry.
- [ ] **Refine, repeatedly:** three consecutive refinements produce three new slates for the *same*
      `kind_key` with `round_index` 1, 2, 3, no Selection recorded, and all rounds present in history.
- [ ] **Refinement that re-blocks:** a refinement invalidating the date range returns the machine to
      `ELICITING_BLOCKING` instead of sourcing.
- [ ] **Mentioned-first end to end:** the Paris-hotel opening plans `lodging` before any offer of
      `air_travel` or `ground_transport` is made — asserted on the Act sequence, not on the agenda.
- [ ] **Proactive offer and decline:** after the lodging Selection, an `offer_unmentioned` Act names
      `air_travel` (top-ranked unmentioned); `DECLINE_OFFER` marks it declined, the next offer names
      `ground_transport`, and declining that leads to `deliver_summary` then `close`.
- [ ] **Accept offer:** `ACCEPT_OFFER` re-enters `ELICITING_BLOCKING`/`SOURCING` for the offered kind and
      that kind is never offered again.
- [ ] **Never re-offer declined:** a declined kind mentioned *by the traveller* later is still planned
      (decline is about offers, not prohibition) — asserted explicitly.
- [ ] **End anywhere:** `END_SESSION` mid-slate yields `deliver_summary` reporting the open component,
      then `close`; a further turn raises `SessionClosedError`.
- [ ] **Failure containment:** a planner raising on every call yields `report_sourcing_failure`, marks
      the component `FAILED` after the configured attempts, and continues to the next agenda entry.
- [ ] **No prose in the domain:** an automated test walks every Act payload produced across the scenario
      suite and asserts no value is a natural-language sentence (heuristic: no payload string contains a
      space-separated run of >3 words outside a `message_key`/`option facts` allowlist).
- [ ] **Transition guard:** a test drives an illegal transition directly and gets
      `IllegalDialogueTransitionError`; the transition table has no unreachable state (asserted by graph
      walk from `GREETING`).
- [ ] One telemetry event per `handle()` call, containing states before/after, intent and agenda
      explanation.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.dialogue` imports only stdlib and
      `tourganize.domain`/`tourganize.ports`. `catalog *` CLI commands still work; `tourganize chat`
      still exits 2 (F07 wires it).

## Open questions / risks

- **Implementer's call:** exact `DialogueState` names and whether `INTERPRETING` is a real state or a
  phase inside `handle()`; whether Acts are emitted eagerly or collected; `PendingQuestion` shape.
- **Risk (the main one):** this feature accreting wording or presentation. Every sentence the traveller
  ever reads must come from F10/F08, or the bilingual requirement is quietly lost.
- **Risk:** the keyword interpreter being good enough to become permanent scaffolding nobody replaces.
  Its config file should stay small; F08's DoD includes swapping it out by config.
- **Open:** should a mid-slate mention of another Kind ever *preempt* the current component? Current
  answer: no (finish, then re-order the agenda). Worth confirming with the client after the first demo —
  it is a one-line policy change in the `AWAITING_CHOICE` handler.
