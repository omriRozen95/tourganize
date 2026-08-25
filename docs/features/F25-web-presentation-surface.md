# F25 — Web presentation surface

- **Bounded context:** Presentation & Export
- **Depends on:** [F07](F07-presentation-surface-and-terminal-shell.md), [F10](F10-bilingual-and-rtl-handling.md), [F13](F13-itinerary-rendering-and-text-renderer.md)
- **Unlocks:** nothing — it is a second surface, by design
- **Size:** L
- **Track:** **deferred / optional** — the client chose "GUI **or** TUI" (C13) and
  [D1](../architecture/decisions.md) took the terminal first
- **Status of the codebase when this starts:** the product is complete behind a terminal surface and a
  headless scripted surface, both behind `PresentationSurface`. Sessions persist and resume, plans export
  to PDF in both languages. Nothing in the system knows what a browser is.

## Purpose

Add a browser surface for people who will not use a terminal, **without touching the Dialogue Director**.
This feature exists mainly to prove the port boundary was real: a second surface should be an adapter plus
a transport, not a redesign. It also lets Hebrew be rendered by a browser, which is a genuinely better bidi
environment than any terminal ([D1](../architecture/decisions.md)'s stated cost).

## Starting state

From F07: the `PresentationSurface` port, the Act renderer, Message Catalogue, display profiles, the session
runner. From F10: locale detection, formatting, and the direction handling a browser can honour natively.
From F12: persistence and resume, which a web surface needs more than a terminal does. From F13: export
paths to offer as downloads.

## Scope — what to implement

1. **Transport** (`tourganize/adapters/presentation/web/`) — a small HTTP + WebSocket application (FastAPI
   is the natural choice if F22 has landed; otherwise any ASGI framework) exposing: a session page,
   `POST /session` (begin), a WebSocket carrying `UserTurn`s up and rendered Acts down, `GET /export/<id>`
   (download an artefact), and `GET /healthz`. The **runner and Director are unchanged**: the web surface
   is an adapter that satisfies `next_turn()` by awaiting the socket and `show()` by pushing a frame.
2. **Turn queue and back-pressure** — `next_turn()` blocks on a queue; a turn arriving while the Director is
   working is queued (never dropped, never interleaved), and the client is told the assistant is thinking.
   Exactly one Director per session, enforced by a per-session lock so two browser tabs cannot corrupt one
   plan (F12's `SessionLockedError` is the backstop).
3. **Act rendering for the browser** — reuse the Act renderer's structure and the Message Catalogue, but
   render to HTML fragments: option slates as selectable cards with the display-profile facts,
   `filter_notes` and `feasibility_notes` as visible badges, a click on a card sending the same
   `CHOOSE_OPTION` text a typed `"2"` would produce, so **no new dialogue intent is introduced**.
4. **RTL done properly** — `dir="rtl"` and logical-order text: **no bidi shaping** (a test asserts
   `shape_for_terminal` is never called on this path — the browser does it correctly). Mirrored layout,
   Hebrew-capable web fonts, locale-formatted dates and money from F10.
5. **Resume and history** — a session list from F12 with `title_hint`, resuming an existing session
   (re-emitting `resume_summary`), and a download link per exported artefact.
6. **Session lifecycle over the network** — reconnect to the same session id after a dropped socket without
   losing the plan (persistence makes this cheap); an idle timeout that saves and detaches rather than
   closing the plan; a `close` Act ending the socket cleanly.
7. **Deployment** — its own container/compose service in a `web` profile, with the app importable in both
   modes so `TOURGANIZE_SURFACE=web` is the only application-level change. Bound to localhost by default:
   **no authentication is in scope** ([D8](../architecture/decisions.md)), and the note must state plainly
   that exposing it publicly requires an auth feature that does not exist.
8. **Tests** — the surface passes a `PresentationSurface` contract suite (introduced here, retrofitted to
   the terminal and scripted surfaces): an Act shown is rendered exactly once, `next_turn()` returns turns
   in order, `None` ends the session, and `close()` is idempotent. Plus an end-to-end browser-driven test
   (Playwright) covering the English and Hebrew happy paths and one refinement round.

## Contract (the Lego connectors)

**Inputs:** WebSocket frames from the browser; HTTP requests for pages and exports.

```json
// client → server
{"type": "turn", "text": "cheaper, and at least 8.5 review score"}
{"type": "choose", "option_ref": "2"}          // becomes the same CHOOSE_OPTION interpretation
// server → client
{"type": "act", "act": "present_slate", "locale": "he", "direction": "rtl",
 "html": "<section …>", "payload_digest": "…"}
{"type": "notice", "kind": "working", "message_key": "notice.sourcing"}
{"type": "closed", "export_url": "/export/…"}
```

**Outputs:** rendered Acts in the browser; downloadable exports; `UserTurn`s to the unchanged Director.

**Ports consumed:** `PresentationSurface` (implemented here), `SessionRepository` (F12),
`ItineraryRenderer` (F13), plus every port the Director already uses.

**Ports provided:** `WebSurface`, and the `PresentationSurface` contract suite that all surfaces now share.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_SURFACE` | gains `web` | `terminal` |
| `TOURGANIZE_WEB_HOST` / `TOURGANIZE_WEB_PORT` | Bind address | `127.0.0.1` / `8000` |
| `TOURGANIZE_WEB_SESSION_IDLE_MINUTES` | Detach-and-save timeout | `60` |
| `TOURGANIZE_WEB_MAX_SESSIONS` | Concurrent sessions admitted | `8` |
| `TOURGANIZE_WEB_ALLOW_RESUME` | Expose the session list and resume | `true` |

**Errors/failure modes:** a dropped socket detaches without losing the session (autosave has it); a second
tab on the same session gets a clear "already open elsewhere" notice, not a corrupted plan; exceeding
`MAX_SESSIONS` returns a busy page; a Director exception ends that session with an error Act and is logged
with the session id.

## Out of scope

Authentication, accounts, multi-tenancy, and sharing links — all still out of scope
([D8](../architecture/decisions.md)); a public deployment needs a feature that does not exist yet.
Streaming token output (needs F22). Mobile-native apps. Any change to the Director, the act vocabulary, or
the domain — if this feature needs one, the port boundary was wrong and that is the finding.

## Replaceability notes

**Must be preserved:** the `PresentationSurface` contract and its new shared suite; that a card click is
expressed as an ordinary choice interpretation; logical-order text with browser-side bidi; one Director per
session.

**Free to change:** the web framework, the front-end approach (server-rendered fragments recommended —
there is no client-side state worth owning), styling, transport (WebSocket vs. SSE + POST).

## Definition of done

- [ ] `TOURGANIZE_SURFACE=web docker compose --profile web up` serves a page where a full English
      conversation completes: blocking question, slate cards, a click-to-choose, a refinement round, offers,
      summary, and a working export download.
- [ ] The same in Hebrew: RTL layout, Hebrew fonts, Hebrew-formatted dates and prices, correct rendering of
      a line mixing Hebrew, a Latin hotel name and a price — verified in a browser screenshot artefact.
- [ ] A test asserts the web path never calls `shape_for_terminal`.
- [ ] **The Director is untouched:** `git diff` for this feature shows no change under `tourganize/dialogue/`
      or `tourganize/domain/`, and all Golden Conversations pass unchanged.
- [ ] The `PresentationSurface` contract suite passes for all three surfaces (web, terminal, scripted).
- [ ] Turn ordering and back-pressure: submitting two turns rapidly processes them in order with a
      "working" notice in between; no Act is rendered twice (asserted on frame ids).
- [ ] Reconnect: killing the socket mid-conversation and reloading resumes the same session with a
      `resume_summary` and no lost selections.
- [ ] Second-tab protection: opening the same session twice yields the "already open" notice and the plan
      remains intact.
- [ ] Playwright tests cover the English and Hebrew happy paths and one refinement round, running headless
      in CI.
- [ ] `TOURGANIZE_SURFACE=terminal` still works exactly as before (regression run of the Phase 1 demo).
- [ ] The operator note states plainly that the surface is unauthenticated and must not be exposed
      publicly.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — the web framework is imported only under
      `adapters/presentation/web/`.

## Open questions / risks

- **Risk:** a web surface invites feature creep (accounts, sharing, mobile) that the client never asked
  for and that [D8](../architecture/decisions.md) put out of scope. The DoD's "Director untouched" gate is
  the discipline.
- **Risk:** an unauthenticated web surface being deployed beyond localhost. Called out in the note and in
  the default bind address; a real deployment needs an auth feature first.
- **Risk:** duplicating the Act renderer for HTML and drifting from the terminal's wording. Mitigated by
  sharing the Message Catalogue and display profiles, and by both surfaces passing one contract suite.
- **Implementer's call:** framework, server-rendered fragments vs. a small client app, styling approach,
  whether the session list is exposed at all.
