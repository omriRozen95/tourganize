# F12 — Session and plan persistence

- **Bounded context:** Platform (serving Dialogue)
- **Depends on:** [F05](F05-dialogue-director-and-session-lifecycle.md), [F11](F11-conversation-evaluation-harness.md)
- **Unlocks:** F13 (re-export), F25 (session list and reconnect); also the Plan Archive candidate in [D12](../architecture/decisions.md)
- **Size:** M
- **Status of the codebase when this starts:** conversations run bilingually with a real model and are
  pinned by the Golden Conversation suite — but everything lives in process memory. Closing the terminal
  destroys the plan; there is no way to resume a conversation or re-export yesterday's itinerary.

## Purpose

Give a Planning Session a life longer than a process. This feature adds the `SessionRepository` port with
a SQLite-backed adapter ([D8](../architecture/decisions.md)), snapshot serialisation of the session and
its Trip Plan, autosave after every turn, and the `resume` and `sessions` commands. It is what makes the
exported document (F13/F14) re-producible without re-planning, and what stops a crash mid-conversation
from costing the traveller their answers.

## Starting state

From F05: `PlanningSession` with `schema_version`, transcript, Dialogue State, focus, offer queue, and the
Trip Plan with components, slate history and selections. From F01: `TOURGANIZE_DATA_DIR`, `Clock`,
`Settings`. Nothing is serialised anywhere.

## Scope — what to implement

1. **Port** (`tourganize/ports/persistence.py`) — `SessionRepository` with `save(session)`,
   `load(session_id)`, `list_recent(limit)`, `delete(session_id)`, and `exists(session_id)`.
   `SessionSummary` — `session_id`, `created_at`, `updated_at`, `locale`, `state`, `selected_kinds`,
   `open_kinds`, `turn_count`, `title_hint` (the first mentioned kind plus the first `place` value, so a
   listing is readable without loading snapshots).
2. **Snapshot codec** (`tourganize/adapters/persistence/codec.py`) — pure functions
   `to_snapshot(session) -> Snapshot` / `from_snapshot(snapshot) -> PlanningSession`, producing a JSON
   document with an explicit `schema_version`. Requirements: **round-trip fidelity** — transcript, every
   slate round (not just the latest), selections with their turn indices, requirement values *with
   provenance and superseded history*, declined kinds, offer queue, locale history, and Dialogue State.
   The codec lives in the adapter layer, not the domain: the domain must not know it is persistable.
3. **Migration path** — `MIGRATIONS: Mapping[int, Callable[[dict], dict]]` applied in order when a stored
   `schema_version` is older than current. Version 1 ships with an identity migration and a test that
   proves the mechanism runs, so the first real migration is not the one that discovers it does not work.
   An unknown *newer* version raises `IncompatibleSnapshotError` rather than guessing.
4. **SQLite adapter** (`tourganize/adapters/persistence/sqlite/`) — one table
   `sessions(session_id TEXT PRIMARY KEY, created_at, updated_at, locale, state, turn_count, snapshot JSON)`
   plus an index on `updated_at`. Writes are transactional and atomic (write, commit, replacing the row);
   `PRAGMA journal_mode=WAL`; the schema is created on first use. Single writer by design — a second
   process attempting a concurrent write on the same session gets `SessionLockedError`, not corruption.
5. **In-memory adapter** — the test default, satisfying the same contract suite; the Golden Conversations
   run against it.
6. **Autosave** (`tourganize/application/session_runner.py`) — save after each `handle()` completes and
   once at close, controlled by `TOURGANIZE_AUTOSAVE`. A save failure logs at ERROR and emits a surface
   `notify()` — it must never lose the turn or kill the session, because an unsaved conversation is still
   a working conversation.
7. **Resume** — `tourganize resume <session_id | --last>`: load, rebuild the Director around the restored
   session (no replay of the transcript through the model — the state *is* the state), re-emit a
   `resume_summary` Act (new act in F05's vocabulary, added here with its message keys) showing what is
   already selected and what is open, then continue exactly where it stopped. Resuming a `CLOSED` session
   is allowed only with `--reopen`, which moves it to `SUMMARISING` so the traveller can export or extend
   rather than silently mutating a finished plan.
8. **Listing and hygiene** — `tourganize sessions [--limit N] [--json]`;
   `tourganize sessions delete <id>` (with confirmation); retention: sessions older than
   `TOURGANIZE_SESSION_RETENTION_DAYS` are reported by `doctor` but **never** auto-deleted (deleting a
   traveller's plan without being asked is not ours to do).
9. **Contract suite** (`tests/contracts/test_session_repository_contract.py`) — parametrised over both
   adapters: save/load round-trip equality, `list_recent` ordering and limit, overwrite semantics,
   unknown id raising `SessionNotFoundError`, and snapshot version handling.

## Contract (the Lego connectors)

**Inputs:** a `PlanningSession`; a session id; the data directory.

**Outputs:** durable snapshots; restored sessions; summaries.

```python
class SessionRepository(Protocol):
    def save(self, session: PlanningSession) -> None: ...
    def load(self, session_id: str) -> PlanningSession: ...       # SessionNotFoundError
    def list_recent(self, limit: int = 20) -> Sequence[SessionSummary]: ...
    def delete(self, session_id: str) -> None: ...
    def exists(self, session_id: str) -> bool: ...

@dataclass(frozen=True)
class Snapshot:
    schema_version: int
    session_id: str
    created_at: datetime
    updated_at: datetime
    payload: Mapping[str, object]        # the full session document
```

**Ports consumed:** `Clock`, `TelemetrySink`.

**Ports provided:** `SessionRepository` (`SqliteSessionRepository`, `InMemorySessionRepository`), the
snapshot codec, and the migration registry.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_SESSION_STORE` | `sqlite` / `memory` | `sqlite` |
| `TOURGANIZE_SESSION_DB_PATH` | SQLite file | `${TOURGANIZE_DATA_DIR}/sessions.db` |
| `TOURGANIZE_AUTOSAVE` | Save after every turn | `true` |
| `TOURGANIZE_SESSION_RETENTION_DAYS` | Age at which `doctor` reports old sessions | `90` |

**Errors/failure modes:** `SessionNotFoundError`, `IncompatibleSnapshotError` (stored version newer than
the code), `SessionLockedError` (concurrent writer), `PersistenceError` for I/O failures — the last of
which the runner catches, reports and continues past.

## Out of scope

Multi-user access, authentication, sharing, and any storage of personal traveller data beyond what the
conversation itself contains — explicitly out of scope project-wide ([D8](../architecture/decisions.md))
until the client says otherwise. Full-text search across plans. Server-side session hosting (F25 would
need it and must say so). Encryption at rest (noted as a client question if real personal data ever
lands).

## Replaceability notes

**Must be preserved:** the `SessionRepository` protocol and its contract suite; snapshot
`schema_version` and the migration mechanism; round-trip fidelity including slate history and requirement
provenance; the domain's ignorance of persistence.

**Free to change:** SQLite for Postgres or a document store; the table layout; whether snapshots are JSON
or msgpack; compression; autosave granularity.

## Definition of done

- [ ] `tourganize chat`, plan a lodging component, `Ctrl+C`; then `tourganize resume --last` shows a
      `resume_summary` naming the selected option and the open kinds, and the conversation continues —
      including accepting a pending offer.
- [ ] Round-trip fidelity test: a session with two components, three slate rounds on one, one selection,
      one declined kind, superseded requirement values and a locale switch serialises and restores to an
      **equal** session (structural equality assertion, not a smoke test).
- [ ] The repository contract suite passes for both SQLite and in-memory adapters, and a deliberately
      broken adapter fails it (wrong `list_recent` order, silent overwrite of a different id).
- [ ] `tourganize sessions` lists sessions newest-first with locale, state, selected/open kinds and a
      readable `title_hint`; `--json` output is stable.
- [ ] Autosave: killing the process (SIGKILL) after turn 3 leaves a snapshot resumable at turn 3;
      a test asserts the snapshot exists and is loadable.
- [ ] Save failure resilience: with an unwritable DB path, the session still completes and a `notify()`
      warning was shown — asserted through the scripted surface.
- [ ] Migration mechanism: a fixture snapshot at version 0 is migrated and loaded; a snapshot claiming a
      future version raises `IncompatibleSnapshotError`.
- [ ] Concurrency: two processes writing the same session id yield `SessionLockedError` for the second and
      an intact snapshot (test with two connections).
- [ ] Golden Conversations pass with `TOURGANIZE_SESSION_STORE=memory` (unchanged) **and** with `sqlite`
      (proving autosave does not perturb behaviour).
- [ ] `tourganize doctor` reports store type, DB path, session count and any sessions past retention.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.dialogue` does not import the codec.

## Open questions / risks

- **Implementer's call:** JSON shape of the snapshot payload; whether `Snapshot` is a dataclass or a plain
  dict; `--reopen` semantics; whether autosave is synchronous (recommended: yes — simpler, and turns are
  seconds apart).
- **Risk:** codec drift. Every domain field added later must be added to the codec or it is silently lost.
  Mitigation: a reflective test asserting every dataclass field of `PlanningSession` and `PlanComponent`
  appears in the snapshot payload — it fails the day someone adds a field and forgets.
- **Risk:** snapshot bloat from slate history over long refinement sessions. Acceptable now (kilobytes);
  if it grows, truncate *presented* slates but never selections.
- **Open (client):** does the exported plan or the stored session ever contain personal data that needs
  encryption or a retention policy? Currently assumed no.
