# F07 — Presentation surface port and the terminal shell (walking skeleton)

- **Bounded context:** Presentation & Export
- **Depends on:** [F05](F05-dialogue-director-and-session-lifecycle.md), [F06](F06-option-sourcing-and-fixture-providers.md)
- **Unlocks:** F08, F10, F25 — and the first client-visible demo
- **Size:** M
- **Status of the codebase when this starts:** the whole planning conversation works **in tests**: the
  Director drives blocking questions, real fixture-backed slates, choose-or-refine, offers and closing.
  There is no way for a human to talk to it — `tourganize chat` still exits 2.

## Purpose

Close the walking skeleton. This feature introduces the `PresentationSurface` port, the **Terminal
Surface** a person can actually type into ([D1](../architecture/decisions.md)), the **Scripted Surface**
that replays turns headlessly, and the Act-rendering layer that turns locale-neutral Assistant Acts into
lines on a screen. At the end of it the client runs one command in a container and plans a hotel in
Paris end to end. This is the Phase 1 deliverable.

## Starting state

From F05/F06: `DialogueDirector.handle()` returning `AssistantAct`s; the closed act vocabulary; the real
Planning Service and fixture data; the keyword Turn Interpreter; `Container` with slots for these.
Nothing renders anything; there is no message catalogue yet.

## Scope — what to implement

1. **Port** (`tourganize/ports/presentation.py`): `PresentationSurface` with `show(act)`,
   `next_turn() -> UserTurn | None` (`None` = the traveller closed the surface), `notify(notice)` for
   out-of-band status (sourcing in progress, errors), and `close()`.
2. **Act renderer** (`tourganize/language/act_renderer.py`) — the *only* place Acts become text in
   Phase 1: `render(act, locale) -> RenderedAct` (a heading, body lines, and an optional numbered
   option table). Text comes from a **Message Catalogue** (`config/messages/<locale>.yaml`) keyed by the
   Act's message keys, with `{placeholder}` substitution from the payload. Ship `en.yaml` **and a
   minimal `he.yaml`** so the RTL path exists from day one (full bilingual handling is F10; this is the
   seam it plugs into). A missing key renders a visible `⟪missing:key⟫` marker and logs — never a
   crash, never a silent blank.
3. **Option table rendering** — Plan Options become rows built from `facts` via a per-kind **display
   profile** in `config/messages/display.<locale>.yaml` (which facts, in which order, with which units).
   This keeps `PlanOption` prose-free (F02) while letting lodging and air travel look different. Rows
   are numbered 1..n, and `filter_notes` from F06 render as a visible marker (e.g. `above budget`) so
   soft filtering is never invisible.
4. **Terminal Surface** (`tourganize/adapters/presentation/terminal/`) — Textual app with a scrolling
   transcript pane, an input line, a status line (current Component Kind, Dialogue State, session id),
   and paged option tables. Requirements: renders Hebrew text without exceptions (visual correctness is
   F10's job), supports multi-line paste, `Ctrl+C` closes the surface cleanly by returning `None` from
   `next_turn()`, and the input line is disabled while the Director is working so no turn is lost.
5. **Scripted Surface** (`tourganize/adapters/presentation/scripted/`) — constructed from a list of
   strings (or a transcript file), returns them in order then `None`, and records every Act it was shown
   in a `captured: list[AssistantAct]`. This is the harness backbone (F11) and the cheapest possible
   integration test.
6. **Session runner** (`tourganize/application/session_runner.py`) — the loop that owns neither dialogue
   nor rendering: `run(director, surface)` → `begin()`, then `while (turn := surface.next_turn())`,
   `handle(turn)`, `show()` each Act, stop on `close` or `None`. Roughly twenty lines, and the only
   place the two ports meet.
7. **CLI** — `tourganize chat [--locale en|he] [--script FILE]` wiring Settings → Composition Root →
   surface (`terminal` or `scripted` per `TOURGANIZE_SURFACE`, `--script` implying `scripted`). This is
   the first fully working end-to-end command.
8. **Container** — add the `terminal` extra to the app image; document the `docker compose run` incantation
   that gives Textual a working TTY.

## Contract (the Lego connectors)

**Inputs:** `AssistantAct`s from the Director; keystrokes or a script file.

**Outputs:** `UserTurn`s to the Director; rendered output to a terminal or a capture list; process exit
code (`0` normal close, `1` unhandled error, `3` configuration error).

```python
class PresentationSurface(Protocol):
    @property
    def surface_id(self) -> str: ...
    def show(self, act: AssistantAct) -> None: ...
    def next_turn(self) -> UserTurn | None: ...
    def notify(self, notice: SurfaceNotice) -> None: ...
    def close(self) -> None: ...

@dataclass(frozen=True)
class RenderedAct:
    heading: str | None
    lines: tuple[str, ...]
    option_rows: tuple[OptionRow, ...] = ()
    direction: Literal["ltr", "rtl"] = "ltr"      # F10 fills this in properly
```

```yaml
# config/messages/en.yaml (excerpt)
greet: "Hi — tell me about the trip you have in mind."
ask.lodging.date_range: "Which dates should I look at for the stay?"
present_slate.lodging: "Here are {count} places that fit:"
confirm_selection: "Noted: {choice}. Moving on."
offer_unmentioned: "Would you like me to plan {kinds} as well?"
component.lodging: "accommodation"
```

**Ports consumed:** all of the Director's ports transitively; directly `PresentationSurface`.

**Ports provided:** `PresentationSurface` (`TerminalSurface`, `ScriptedSurface`), plus the Act renderer
and Message Catalogue that F10 will extend and F25 will reuse.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_SURFACE` | `terminal` / `scripted` | `terminal` |
| `TOURGANIZE_MESSAGE_DIR` | Message Catalogue and display profiles | `${TOURGANIZE_CONFIG_DIR}/messages` |
| `TOURGANIZE_DEFAULT_LOCALE` | Locale when nothing is detected | `en` |
| `TOURGANIZE_SUPPORTED_LOCALES` | Comma list of Locale Tags | `en,he` |

**Errors/failure modes:** a missing message key renders a marker and logs (never raises); a surface
exception is caught by the runner, which attempts a final `notify()` and exits 1 with the session id so
the transcript can be found in telemetry; a `SessionClosedError` from the Director ends the loop
normally.

## Out of scope

Correct bidi/RTL layout, mixed-language turns, locale-aware dates and numbers — all F10 (this feature
only guarantees Hebrew does not crash and that `direction` is plumbed). Any LLM-composed wording (F08).
Exported documents (F13/F14) — the closing summary here is screen text only. Persistence, so a session
ends when the process ends (F12). Mouse, themes, or a web surface (F25).

## Replaceability notes

**Must be preserved:** the `PresentationSurface` protocol, including `next_turn() -> None` as the close
signal; the session runner's separation (a surface never touches the Director's internals, and the
Director never learns which surface is attached); the Message Catalogue key convention and the
`⟪missing:key⟫` behaviour; the display-profile mechanism keeping prose out of `PlanOption`.

**Free to change:** Textual vs. prompt_toolkit vs. plain readline; layout, colours, paging; the
`RenderedAct` shape (internal to presentation); the message file format.

## Definition of done

- [ ] **The Phase 1 demo:** `docker compose --profile dev-cpu run --rm app tourganize chat` starts a
      terminal session; typing *"find me a hotel in Paris between the 23rd and 28th of October 2026"*
      produces the blocking question if anything is missing, then a numbered slate of 3 lodging options
      with prices and review scores; typing *"2"* confirms the choice; the offer to plan air travel
      follows; declining twice prints a plan summary and exits 0.
- [ ] The same transcript runs headlessly: `tourganize chat --script fixtures/conversations/paris.txt`
      exits 0 and the `ScriptedSurface` captured Acts match a stored expectation (this becomes F11's
      first Golden Conversation).
- [ ] A refinement path is demonstrable interactively: *"cheaper, and at least 8.5 review score"*
      produces a new numbered slate for the same component.
- [ ] Hebrew does not crash: `--locale he` with a Hebrew script file completes a full session; a test
      asserts every Act rendered without exception and that `direction == "rtl"` for `he`.
- [ ] Missing message keys: a fixture catalogue with a deleted key renders `⟪missing:...⟫`, logs at
      WARNING, and the session still completes.
- [ ] `filter_notes` from F06 are visible in the option table (test on a query where every option
      exceeds the budget ceiling).
- [ ] `Ctrl+C` in the terminal surface exits 0 with a farewell Act, not a traceback.
- [ ] Unit tests: Act renderer per act type against a fixture catalogue; option-row building from a
      display profile; the session runner with a two-turn `ScriptedSurface` and a fake Director.
- [ ] `tourganize doctor` reports the selected surface, locale and message directory; `catalog *` and
      `options search` still work.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.dialogue` still imports nothing from
      `adapters.presentation`.
- [ ] `docs/architecture/overview.md` §6 ("Phase 1 in one line") is verified true by running it, and the
      command is recorded in the repository README.

## Open questions / risks

- **Implementer's call:** Textual widget structure; whether the status line shows the Dialogue State
  (useful in dev, noise for a client demo — suggest a `--debug-status` flag); how paging works for long
  slates.
- **Risk:** the Act renderer growing conditional logic per Component Kind. It must stay driven by the
  display profile, or the "add a kind with no code change" property (F02, F06) dies here.
- **Risk:** Textual in a container without a TTY — the compose invocation must be documented and tested
  in CI via the scripted surface, since CI has no TTY.
- **Risk:** the temptation to hand-write Hebrew strings that look right in an editor. F10 owns the real
  bidi work; anything done here is provisional by design.
