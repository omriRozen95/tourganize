# F14 — Typeset itinerary renderer with Hebrew and bidi support (PDF default)

- **Bounded context:** Presentation & Export
- **Depends on:** [F13](F13-itinerary-rendering-and-text-renderer.md)
- **Unlocks:** nothing structurally — it completes the client's default export format
- **Size:** M
- **Status of the codebase when this starts:** plans export as markdown and plain text from live or stored
  sessions, with locale-formatted values and a fallback chain. `TOURGANIZE_EXPORT_FORMAT=pdf` is the
  configured default and currently always falls back with a warning.

## Purpose

Deliver the document the client actually asked for: a PDF, correct in Hebrew. This feature adds a
`format_key="pdf"` renderer built on **WeasyPrint** with an HTML/CSS template and an embedded
Hebrew-capable font ([D10](../architecture/decisions.md)), so bidi resolution is done by Pango rather than
by hand and the layout is restyleable without touching Python. When it lands, the default export format
stops falling back.

## Starting state

From F13: `ItineraryDocument` with locale-formatted `FactLine`s and a `direction`; the `ItineraryRenderer`
port with `availability()`; the registry and fallback chain; the renderer contract suite; the `export`
command and auto-export on close.

## Scope — what to implement

1. **Renderer** (`tourganize/adapters/export/typeset/`) — `TypesetRenderer` with `format_key="pdf"`,
   `media_type="application/pdf"`, rendering an HTML string through WeasyPrint. `availability()` imports
   the stack lazily and reports a structured reason when it is missing, so a slim image degrades to
   markdown instead of crashing.
2. **Template** (`config/export/templates/itinerary.html.j2` + `itinerary.css`) — Jinja2, consuming
   **only** the `ItineraryDocument` (a template that needs the Trip Plan is a design failure). Structure:
   cover block (title, destination, dates, generation timestamp), one section per Component Kind with a
   fact table, per-currency totals, an optional alternatives block, a citations block, and a footer with
   page numbers. Template and stylesheet directories are configurable so the client can restyle without a
   rebuild.
3. **Bidi and direction** — set `dir="rtl"` and `direction: rtl; unicode-bidi: plaintext` on the document
   root for RTL locales, per-section `dir` when a section mixes languages, and hand Pango **logical-order**
   text: F10's `shape_for_terminal` must **not** be applied here (a test asserts the typeset path never
   calls it). Numbers, prices and Latin names inside Hebrew lines are left to the algorithm. Tables mirror
   naturally via CSS `direction`.
4. **Fonts** — vendor **Noto Sans Hebrew** and **Noto Sans** (both SIL OFL 1.1) under `assets/fonts/`,
   embedded through `@font-face` with absolute paths resolved by the renderer, and a `LICENSE` file
   alongside them. `TOURGANIZE_TYPESET_FONT_FAMILY` allows an override; a missing font file is an
   `availability()` failure, not a silent Latin-only fallback that would render Hebrew as boxes.
5. **Page setup** — A4 default (`TOURGANIZE_TYPESET_PAGE_SIZE`, also `letter`), sensible margins, repeated
   table headers across page breaks, `page-break-inside: avoid` on sections, and a print-safe palette that
   survives greyscale printing.
6. **PDF metadata and reproducibility** — title, author (`Tourganize`), language tag, and creation date
   taken from `document.generated_at` (not the wall clock) so that rendering the same document twice with a
   frozen clock produces byte-identical PDFs. Document the WeasyPrint options needed for that, and skip the
   assertion with a clear message if the installed version cannot honour it.
7. **Container** — add the `typeset` extra and the native libraries (Pango, cairo, GDK-PixBuf) to a
   **separate image layer or stage**, keeping the base app image slim; compose profile `dev-cpu` uses the
   full image, and a documented slim variant proves the fallback path still works. Image size before/after
   is recorded in the operator note.
8. **Visual verification** — golden-PDF testing is brittle, so verify in layers: (a) assert on the
   generated **HTML** (structure, `dir` attributes, every fact present) — the deterministic part; (b) extract
   text from the produced PDF (`pdfminer.six`) and assert Hebrew strings, dates and prices are present and
   in the right sections; (c) render one reference document to PNG and store it as a **reviewed artefact**
   in CI (uploaded, not diffed) so a human can eyeball drift.
9. **CLI** — `tourganize export --format pdf` becomes real; `tourganize export --preview-html` emits the
   intermediate HTML for template debugging.

## Contract (the Lego connectors)

**Inputs:** an `ItineraryDocument` (F13); template and font paths.

**Outputs:** a `RenderedArtifact` with PDF bytes, `media_type="application/pdf"`, and a `.pdf` filename;
optionally the intermediate HTML.

```python
class TypesetRenderer:                     # implements ItineraryRenderer
    format_key = "pdf"
    media_type = "application/pdf"
    def __init__(self, template_dir: Path, stylesheet: Path,
                 font_family: str, page_size: str) -> None: ...
    def availability(self) -> RendererAvailability: ...   # reports missing native libs or fonts
    def render(self, document: ItineraryDocument) -> RenderedArtifact: ...
    def render_html(self, document: ItineraryDocument) -> str: ...   # used by tests and --preview-html
```

**Ports consumed:** none new — it consumes the `ItineraryDocument` only. (That constraint is the feature's
main architectural value.)

**Ports provided:** a third `ItineraryRenderer` implementation, registered for `pdf`.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_TYPESET_TEMPLATE_DIR` | Jinja2 templates | `${TOURGANIZE_CONFIG_DIR}/export/templates` |
| `TOURGANIZE_TYPESET_STYLESHEET` | CSS file | `${TOURGANIZE_CONFIG_DIR}/export/templates/itinerary.css` |
| `TOURGANIZE_TYPESET_FONT_FAMILY` | Primary font family name | `Noto Sans` |
| `TOURGANIZE_TYPESET_FONT_DIR` | Directory of embedded fonts | `./assets/fonts` |
| `TOURGANIZE_TYPESET_PAGE_SIZE` | `A4` / `letter` | `A4` |

**Errors/failure modes:** `RendererUnavailableError` from `availability()` when native libraries or fonts
are missing → the F13 registry falls back to markdown with a warning (**the export never fails outright**);
`TemplateRenderError` naming the template and the missing variable; a WeasyPrint failure is wrapped with
the intermediate HTML written to the data dir for diagnosis.

## Out of scope

Any change to `ItineraryDocument`, the port, or the registry (F13 owns them). Interactive PDF features
(forms, links beyond plain URLs). Branding and letterhead beyond the template hook — pending the client's
answer (overview §9, question 5). Other typeset formats (DOCX, HTML-as-deliverable) — each would be a small
new renderer feature.

## Replaceability notes

**Must be preserved:** `format_key="pdf"`; consuming only the `ItineraryDocument`; `availability()`
reporting rather than raising; logical-order input (no manual bidi anywhere in this feature); the templates
and fonts being configuration rather than code.

**Free to change:** WeasyPrint for another engine that honours bidi (a ReportLab-based renderer would have
to solve bidi itself — noted as the reason it was not chosen); Jinja2 for another templating engine; page
design; whether HTML is an intermediate artefact at all.

## Definition of done

- [ ] `tourganize export --last --format pdf` writes a PDF that opens in a standard viewer with correct
      sections, facts, prices and totals; no fallback warning is emitted.
- [ ] **Hebrew correctness:** exporting a Hebrew session produces a PDF whose extracted text contains the
      expected Hebrew headings and values; a human-reviewed PNG artefact of that page is attached in CI and
      referenced in the DoD sign-off. A Hebrew line containing a Latin hotel name, a price and a date is
      verified to read correctly right-to-left with the Latin run and digits intact.
- [ ] No glyph fallback: a test asserts the embedded Hebrew font is used (font names present in the PDF's
      resources) and that removing the font directory makes `availability()` fail rather than producing
      boxes.
- [ ] Structural assertions on `render_html`: `dir="rtl"` for Hebrew documents, one section per planned
      kind, every `FactLine` present, citations block present when citations exist.
- [ ] Determinism: two renders of the same document with a frozen clock produce identical bytes (or the
      test skips with an explicit message naming the WeasyPrint limitation).
- [ ] Page behaviour: a document long enough to paginate keeps table headers repeated and does not split a
      section mid-row (asserted on the HTML/CSS rules and checked in the reviewed PNG).
- [ ] Graceful degradation: on the slim image (no native libs) `tourganize export --format pdf` produces
      markdown with a warning and exits 0, and `doctor` reports the PDF renderer as unavailable with the
      missing library named.
- [ ] The F13 renderer contract suite passes for `TypesetRenderer`.
- [ ] `--preview-html` writes the intermediate HTML.
- [ ] Font licences are committed alongside the fonts; the operator note records image size before/after
      and the compose invocation for the slim variant.
- [ ] Golden Conversations still pass; markdown/text exports unchanged; `mypy --strict`, `ruff`,
      `lint-imports` pass.

## Open questions / risks

- **Implementer's call:** template structure and CSS; whether to also ship an HTML export format (nearly
  free, given the template); PNG rendering tool for the review artefact.
- **Risk (main one):** native dependency friction in the container, and platform differences in Pango
  versions producing slightly different line breaking. Mitigated by the fallback chain, by pinning the base
  image, and by not diffing rendered pixels in CI.
- **Risk:** Hebrew that *looks* plausible but is subtly wrong (reversed digit runs inside RTL text is the
  classic). Text extraction alone cannot catch it, which is why the reviewed PNG artefact is part of the
  DoD rather than optional.
- **Open (client):** page size, branding, letterhead, and whether the document should be bilingual
  (side-by-side) rather than single-locale. All three are template-level changes once this lands.
