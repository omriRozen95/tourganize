# F13 — Itinerary document projection and the text/markdown renderer

- **Bounded context:** Presentation & Export
- **Depends on:** [F10](F10-bilingual-and-rtl-handling.md), [F12](F12-session-and-plan-persistence.md)
- **Unlocks:** F14, F25
- **Size:** M
- **Status of the codebase when this starts:** plans are complete, bilingual, persisted and resumable, and
  the closing summary appears **on screen only**. Nothing can be handed to a traveller: there is no file,
  no format configuration, and no renderer port.

## Purpose

Turn an accepted plan into a document. This feature introduces the **Itinerary Document** — the
locale-resolved, renderer-agnostic projection of a Trip Plan's Selections — the `ItineraryRenderer` port,
and the **always-works** text/markdown renderer that needs nothing but Python
([D10](../architecture/decisions.md)). It also adds the `export` command and the export-format
configuration, so the client's "summary in a configured format" requirement is satisfied end to end
before the heavier PDF stack arrives in F14.

## Starting state

From F02/F05: `TripPlan`, `Selection`, `PlanCompleteness`, the `deliver_summary` Act. From F10: locale
formatting helpers, Message Catalogue, display profiles. From F12: sessions can be loaded by id, so
export does not require a live conversation.

## Scope — what to implement

1. **Itinerary Document** (`tourganize/domain/export/document.py` — pure, no renderer knowledge):
   - `ItineraryDocument` — `plan_id`, `session_id`, `locale`, `direction`, `generated_at`, `title`,
     `sections`, `notes`, `citations`, `completeness_summary`.
   - `ItinerarySection` — one per Component Kind (ordered by the plan's chronology when dates exist,
     otherwise by the Priority Policy's order), each with `heading`, `facts: tuple[FactLine, ...]`,
     `provenance`, `alternatives_considered` (from slate history — the client's refinement rounds are part
     of the story), and `status` (selected / declined / open).
   - `FactLine` — `label`, `value_text`, `emphasis`. Values are **already locale-formatted strings**: the
     projection is where F10's helpers are applied, so every renderer inherits correct dates, money and
     direction for free.
   - `totals` — summed prices per currency (never across currencies), with a note when currencies differ.
2. **Projection** (`tourganize/application/export_service.py`) —
   `project(session, locale) -> ItineraryDocument`, driven by the same **display profiles** as the option
   tables (F07) so the document and the screen never disagree about which facts matter. Declined kinds
   appear as one line ("not planned"), open kinds as an honest "still open" line — a partial plan exports
   rather than refusing.
3. **Renderer port** (`tourganize/ports/export.py`) — `ItineraryRenderer` with `format_key`,
   `media_type`, `render(document) -> RenderedArtifact`, and `availability() -> RendererAvailability`
   (so a renderer whose native stack is missing reports it instead of exploding at render time).
   `RenderedArtifact` — `content: bytes`, `media_type`, `suggested_filename`, `warnings`.
4. **Renderers** (`tourganize/adapters/export/text/`) — two, sharing one implementation:
   `format_key="markdown"` (headings, tables, a citations section) and `format_key="text"` (plain, no
   markup, wrapped to `TOURGANIZE_EXPORT_TEXT_WIDTH`). Both handle RTL: logical order is preserved in the
   file (correct for any bidi-aware viewer), and the text renderer optionally applies F10's shaping when
   `TOURGANIZE_EXPORT_TEXT_VISUAL=true` for consumption in non-bidi viewers — off by default, because a
   file is not a terminal.
5. **Renderer registry** — resolve `TOURGANIZE_EXPORT_FORMAT` to a registered renderer; on an unavailable
   renderer, fall back down `TOURGANIZE_EXPORT_FALLBACK_CHAIN` (default `markdown,text`) with a warning in
   the artefact and a `notify()` to the surface. This is the mechanism that makes F14's native-dependency
   risk survivable, and it is why F13 ships first.
6. **Delivery** — `ExportService.export(session, format_key=None) -> Path` writing to
   `${TOURGANIZE_EXPORT_DIR}/<session_id>/<plan_id>-<timestamp>.<ext>`, with the path returned in the
   `deliver_summary` Act payload so the surface can show it. Existing files are never overwritten
   (timestamped names), and the directory is created on demand.
7. **CLI** — `tourganize export <session_id|--last> [--format F] [--locale L] [--out PATH]`, and the
   session close path exporting automatically when `TOURGANIZE_EXPORT_ON_CLOSE=true` (default `true`).
8. **Contract suite** (`tests/contracts/test_itinerary_renderer_contract.py`) — every renderer, present
   and future: honours `format_key`/`media_type`, produces non-empty bytes for a minimal document, renders
   every section status, never raises on a document containing Hebrew, includes every citation it was
   given, and produces byte-identical output for identical input (determinism, so exports are diffable).

## Contract (the Lego connectors)

**Inputs:** a `PlanningSession` (live or loaded); a locale; a `format_key`.

```python
@dataclass(frozen=True)
class ItineraryDocument:
    plan_id: str
    session_id: str
    locale: str
    direction: Literal["ltr", "rtl"]
    generated_at: datetime
    title: str
    sections: tuple[ItinerarySection, ...]
    totals: tuple[FactLine, ...] = ()
    notes: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()          # populated from F19 onward

class ItineraryRenderer(Protocol):
    @property
    def format_key(self) -> str: ...
    @property
    def media_type(self) -> str: ...
    def availability(self) -> RendererAvailability: ...
    def render(self, document: ItineraryDocument) -> RenderedArtifact: ...
```

**Outputs:** a file on disk, its path in the `deliver_summary` payload, and the `RenderedArtifact`.

**Ports consumed:** `SessionRepository` (F12), formatting helpers (F10), `ComponentCatalog` (section
ordering and labels), `Clock`, `TelemetrySink`.

**Ports provided:** `ItineraryRenderer` (`MarkdownRenderer`, `TextRenderer`, `FailingRenderer` fake), the
`ItineraryDocument` projection, and the renderer registry F14/F25 plug into.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_EXPORT_FORMAT` | Preferred `format_key` | `pdf` (falls back until F14 lands) |
| `TOURGANIZE_EXPORT_FALLBACK_CHAIN` | Ordered fallbacks | `markdown,text` |
| `TOURGANIZE_EXPORT_DIR` | Output root | `${TOURGANIZE_DATA_DIR}/exports` |
| `TOURGANIZE_EXPORT_ON_CLOSE` | Export automatically at session close | `true` |
| `TOURGANIZE_EXPORT_TEXT_WIDTH` | Wrap width for the text renderer | `80` |
| `TOURGANIZE_EXPORT_TEXT_VISUAL` | Apply bidi shaping in the text file | `false` |
| `TOURGANIZE_EXPORT_INCLUDE_ALTERNATIVES` | Include rejected options per section | `true` |

**Errors/failure modes:** `RendererUnavailableError` (caught by the registry, triggering fallback);
`ExportWriteError` (permissions, disk) — surfaced as a `notify()` warning with the session id, never
losing the plan since the session is persisted and re-exportable; an empty plan exports a document
stating nothing was selected rather than raising.

## Out of scope

PDF and any native typesetting stack (F14). Branding, letterhead and page geometry (F14, pending the
client's answer). Emailing or uploading the document. Editing a document after export. Citations content
(F19 fills the field).

## Replaceability notes

**Must be preserved:** `ItineraryDocument` and its section/fact shape (F14 and F25 consume it, and a
renderer must never need the Trip Plan); the `ItineraryRenderer` port including `availability()`; the
registry and fallback-chain behaviour; that locale formatting happens in the projection, not in renderers;
determinism of rendered bytes.

**Free to change:** markdown/text layout; whether the two renderers share a class; file naming; the
projection's section ordering heuristic; where alternatives appear.

## Definition of done

- [ ] `tourganize export --last --format markdown` writes a document containing: a title with destination
      and dates, one section per planned Component Kind with locale-formatted facts and prices, per-currency
      totals, declined kinds marked as not planned, and rejected alternatives when enabled.
- [ ] `--format text` produces a wrapped plain-text file with no markup; both are byte-identical across two
      runs (determinism test).
- [ ] Hebrew export: a Hebrew session exports with Hebrew headings, Hebrew-formatted dates, `direction=rtl`
      recorded in the document, and logical-order text in the file; a test asserts a known Hebrew heading
      and that Latin hotel names are intact.
- [ ] Fallback: with `TOURGANIZE_EXPORT_FORMAT=pdf` and no PDF renderer registered (the state until F14),
      the export succeeds as markdown, the artefact carries a warning, and the surface shows a notice.
- [ ] Auto-export on close: finishing a session writes a file and the `deliver_summary` Act payload
      contains its path (asserted through a scripted session).
- [ ] Export from a *loaded* session works with no conversation running:
      `tourganize export <id>` after a process restart produces the same document.
- [ ] A partial plan (one selection, one open, one declined) exports honestly — asserted on section
      statuses.
- [ ] The renderer contract suite passes for both renderers and fails for a deliberately broken renderer
      (non-deterministic output, dropped citations, raising on Hebrew).
- [ ] `ExportWriteError` on an unwritable directory produces a warning and a non-zero CLI exit, while an
      interactive session continues.
- [ ] Golden Conversations extended with one export assertion (a file exists and contains the selected
      option's reference).
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.domain.export` imports no renderer.

## Open questions / risks

- **Implementer's call:** markdown table vs. definition-list layout; how alternatives are presented;
  filename scheme; whether `totals` are computed in the projection (recommended) or per renderer.
- **Risk:** the projection quietly becoming kind-specific. It must stay driven by display profiles, or
  adding a Component Kind stops being a configuration change.
- **Risk:** `TOURGANIZE_EXPORT_FORMAT` defaulting to `pdf` before F14 exists means every export logs a
  fallback warning. That is deliberate — the client's stated default is PDF, and the warning is the
  honest signal that F14 is not yet in.
