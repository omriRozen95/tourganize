# F10 — Bilingual conversation: language detection, RTL rendering and locale formatting

- **Bounded context:** Language Services (cross-cutting; consumed by Presentation & Export)
- **Depends on:** [F07](F07-presentation-surface-and-terminal-shell.md), [F08](F08-llm-gateway-and-prompt-library.md)
- **Unlocks:** F11, F13, F25 (and F14 transitively, through F13)
- **Size:** M
- **Status of the codebase when this starts:** the conversation works with a real model in English, and
  Hebrew "does not crash": `he.yaml` exists, `direction` is plumbed, and a Hebrew session completes —
  but text order in the terminal is wrong for mixed content, dates and numbers are formatted the English
  way, and the locale is decided once per session rather than per turn.

## Purpose

Make Hebrew a first-class language rather than a flag. Language is detected **per turn** (including
mixed-script turns), the reply locale follows a stated policy, Hebrew renders in correct visual order in
the terminal, and dates, numbers, currencies and component labels follow the locale. This is the
cross-cutting feature the brief insists on: it owns the *mechanism*, and F13/F14/F25 consume it.

## Starting state

From F07: Message Catalogue per locale, `RenderedAct.direction`, display profiles, `TOURGANIZE_SUPPORTED_LOCALES`.
From F08: Composition calls that take a `locale`, and the interpreter's `detected_locale` field (currently
advisory and unused).

## Scope — what to implement

1. **Language Detector port** (`tourganize/ports/language.py`) and `ScriptRatioDetector`
   (`tourganize/language/detection.py`) — deterministic, no model: count Hebrew-block vs. Latin letters,
   ignore digits/punctuation/emoji, and return `LocaleReading(locale, confidence, mixed, script_counts)`.
   Rules, chosen deliberately and documented: any Hebrew letters at all in a turn ⇒ `he` (a traveller
   writing Hebrew with English hotel names is writing Hebrew); Latin-only ⇒ `en`; no letters at all
   (`"2"`, a date) ⇒ **inherit the session locale**, which is the case that a naive detector gets wrong on
   every choice turn. A `ModelAssistedDetector` fallback exists for low-confidence readings but is off by
   default (`TOURGANIZE_LANGUAGE_DETECT=script`).
2. **Session locale policy** (`tourganize/language/locale_policy.py`) — the session holds
   `locale` (current reply language) and `locale_history`. Policy: reply in the locale of the **latest
   letter-bearing turn**; switch immediately when the traveller switches; require
   `TOURGANIZE_LOCALE_SWITCH_CONFIRM` consecutive turns (default 1 = immediate) before switching; never
   switch on a letterless turn. An explicit switch request (handled as a normal requirement-free intent)
   overrides. Every switch is a telemetry event so the client can see how often it happens.
3. **Bidi shaping for the terminal** (`tourganize/language/bidi.py`) — `shape_for_terminal(text, base_dir)`
   applying the Unicode Bidirectional Algorithm (via `python-bidi`) to convert logical order to visual
   order, because terminals do not reorder. Applied **only** at the terminal surface boundary — the
   domain, the ledger, exports and telemetry all keep **logical** order. A named constant documents this:
   `LOGICAL_ORDER_EVERYWHERE_EXCEPT_TERMINAL`. Hebrew needs no glyph shaping (unlike Arabic), so no
   reshaper is introduced.
4. **RTL layout in the terminal surface** — right-aligned transcript and input for RTL locales, option
   tables mirrored (number column on the right), digits and Latin substrings kept in logical order inside
   RTL lines, and column widths computed on **display width** (East-Asian-width aware) rather than
   `len()`, so mixed lines do not tear. Bracket/parenthesis mirroring left to the bidi algorithm.
5. **Locale formatting** (`tourganize/language/formatting.py`) — `format_date`, `format_date_range`,
   `format_number`, `format_money`, `format_duration` per locale, with Hebrew using
   day-month-year ordering and Hebrew month names, and money formatted per currency conventions rather
   than by naive symbol prefixing. Backed by `babel` if available, with a small built-in table as the
   always-works fallback (the container must not require a full ICU build). Rendering **never** formats
   dates inline: the Act renderer and display profiles call these helpers.
6. **Message Catalogue completeness** — `he.yaml` filled for every key used by any Act, plus Hebrew
   display profiles and Hebrew component labels. A **catalogue parity test** fails CI when a key exists in
   one locale and not another, and `tourganize messages lint` reports missing keys, unused keys, and
   placeholder mismatches between locales.
7. **Bilingual prompts** — for each prompt template, either a per-locale body
   (`interpret_turn.he.md`) or a documented locale variable; composition templates instruct the model to
   answer **in** the locale, and a post-check rejects a reply whose dominant script disagrees with the
   requested locale (falling back to the catalogue, reusing F08's fallback path).
8. **Mixed-language content** — Hebrew reply text may legitimately contain Latin proper nouns (hotel and
   airline names from fixtures). Those are inserted as-is in logical order and left to the bidi algorithm;
   a test pins one such line's expected visual output so regressions are caught.
9. **CLI** — `tourganize messages lint`; `tourganize chat --locale he` documented as an initial hint only,
   since detection takes over from the first turn.

## Contract (the Lego connectors)

**Inputs:** turn text; a locale tag; structured values to format.

```python
class LanguageDetector(Protocol):
    def detect(self, text: str, session_locale: str) -> LocaleReading: ...

@dataclass(frozen=True)
class LocaleReading:
    locale: str                 # "en" | "he"
    confidence: float
    mixed: bool
    letters_found: bool         # False for "2" or a bare date → inherit the session locale
    script_counts: Mapping[str, int]

def shape_for_terminal(text: str, base_dir: Literal["ltr", "rtl"]) -> str: ...
def format_money(amount: Money, locale: str) -> str: ...
def format_date_range(start: date, end: date, locale: str) -> str: ...
```

**Outputs:** the reply locale per turn; visually correct terminal lines; locale-formatted strings; a
complete Hebrew catalogue.

**Ports consumed:** `TelemetrySink` (locale switches), `LlmGateway` (optional model-assisted detection
only).

**Ports provided:** `LanguageDetector` (`ScriptRatioDetector`, `ModelAssistedDetector`,
`FixedLocaleDetector` fake), the formatting helpers, and `shape_for_terminal` — all reused by F13/F14/F25.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_LANGUAGE_DETECT` | `script` / `model` / `fixed` | `script` |
| `TOURGANIZE_LOCALE_SWITCH_CONFIRM` | Consecutive turns needed to switch reply locale | `1` |
| `TOURGANIZE_BIDI_SHAPING` | Apply visual reordering at the terminal | `true` |
| `TOURGANIZE_FORMATTING_BACKEND` | `babel` / `builtin` | `babel` if importable, else `builtin` |

**Errors/failure modes:** an unsupported detected locale falls back to `TOURGANIZE_DEFAULT_LOCALE` with a
warning (never raises mid-conversation). A missing Hebrew key renders the `⟪missing:key⟫` marker (F07) and
fails CI via the parity test rather than at runtime. Bidi shaping failure logs once and passes text
through unshaped — an ugly line beats a lost turn.

## Out of scope

Additional languages (the mechanism is general; only `en`/`he` are shipped and tested). Translating
traveller-supplied documents (F18/F19). Bidi in the exported document — same helpers, but F14 owns it and
delegates to Pango there. Hebrew calendar dates. Voice or transliteration.

## Replaceability notes

**Must be preserved:** logical order everywhere except the terminal boundary; `LanguageDetector` and its
`letters_found` semantics (the choice-turn case); the per-turn locale policy; the formatting helper
signatures; catalogue key parity as a CI gate.

**Free to change:** the detector's internals (a model-based detector is already an alternative adapter);
`python-bidi` for another implementation; `babel` vs. built-in tables; alignment and mirroring details in
the terminal.

## Definition of done

- [ ] Per-turn detection: a session that opens in English and switches to Hebrew mid-conversation replies
      in Hebrew from that turn on, and switching back works — asserted on rendered output through a
      scripted session.
- [ ] The choice-turn case: replying `"2"` inside a Hebrew session keeps the session Hebrew (this is the
      regression test that a ratio-only detector fails).
- [ ] Mixed-script turn (Hebrew sentence containing a Latin hotel name) is detected `he` with
      `mixed=True`, and the reply is Hebrew.
- [ ] Bidi: a golden test pins the visual output of a Hebrew line containing a Latin name, a price and a
      date; `TOURGANIZE_BIDI_SHAPING=false` shows the unshaped baseline, so the transformation is visible
      in the diff.
- [ ] Terminal RTL layout: a snapshot test of the Hebrew slate shows right-aligned text with the numbering
      column on the right and no torn columns for a mixed-width row.
- [ ] Formatting: unit tests for dates, ranges, numbers, currencies and durations in `en` and `he`,
      including a Hebrew month name and a non-EUR currency; the `builtin` backend passes the same tests
      (proving the fallback).
- [ ] `tourganize messages lint` exits 0 on the shipped catalogues, and CI fails on a fixture with a key
      present only in `en`, and on a placeholder mismatch between locales.
- [ ] Composition locale guard: a fake composition replying in English to a Hebrew request is rejected and
      the Hebrew catalogue text is used (asserted).
- [ ] Full Hebrew session end to end with the Claude backend: *"מצא לי מלון בפריז בין ה-23 ל-28 באוקטובר"*
      is understood (kind `lodging`, place, dates), a Hebrew slate is presented, a choice is made, and the
      summary is Hebrew.
- [ ] Every locale switch emits a telemetry event with from/to and confidence.
- [ ] English sessions are byte-identical to before this feature (regression snapshot), and the F05/F07
      suites pass unchanged.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `python-bidi` is imported only from
      `tourganize.language.bidi`.

## Open questions / risks

- **Implementer's call:** whether to depend on `babel`; the exact right-alignment strategy in Textual; how
  aggressively to reject a wrong-language composition.
- **Risk (real):** terminal emulators vary in how they handle RTL. Some reorder themselves, which double-
  reverses our shaped output. Hence `TOURGANIZE_BIDI_SHAPING` as an operator escape hatch, and hence the
  exported document — not the terminal — being the authoritative Hebrew artefact
  ([D1](../architecture/decisions.md), [D10](../architecture/decisions.md)).
- **Risk:** Hebrew wording quality from a quantized open-weights model is unknown until F21 measures it.
  The catalogue fallback means the product stays usable in Hebrew even if composed wording must be turned
  off for the self-hosted backend.
- **Open (client):** is Hebrew the primary traveller language for the demo? If so, snapshot coverage
  should favour Hebrew, and the exported-document work (F13/F14) should be pulled forward.
