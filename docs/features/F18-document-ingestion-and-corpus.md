# F18 — Supplementary document ingestion and the knowledge corpus

- **Bounded context:** Knowledge Augmentation
- **Depends on:** [F01](F01-project-foundation.md)
- **Unlocks:** F19, F23
- **Status of the codebase when this starts:** the assistant plans, converses bilingually, sources options
  from fixtures or MCP, and exports documents. It has no way to be *told* anything: hand it an airline's
  fare-rules PDF and there is nowhere for it to go.
- **Size:** M

## Purpose

Accept the client's supplementary documents (C10) and turn them into retrievable **Passages**. This
feature owns the **Knowledge Corpus**: registering a document, extracting its text, splitting it into
Passages with source anchors, and recording ingestion metadata — with no embedding model and no retrieval
yet, so it lands as a small, verifiable step. Visible outcome: `tourganize docs add fare-rules.pdf`
followed by `tourganize docs show` listing the document, its Passage count and its language.

## Starting state

From F01: Settings, `TOURGANIZE_DATA_DIR`, logging, telemetry, CLI. From F10: locale detection, reusable
for per-document and per-Passage language tagging. Nothing knowledge-related exists.

## Scope — what to implement

1. **Domain types** (`tourganize/domain/knowledge/`) — pure, no extraction libraries:
   - `DocumentRecord` — `document_id`, `title`, `source_path`, `media_type`, `sha256`, `locale`,
     `page_count`, `ingested_at`, `ingestor_version`, `tags`, `scope` (see below), `status`.
   - `Passage` — `passage_id`, `document_id`, `ordinal`, `text`, `anchor` (`page`, `char_start`,
     `char_end`, `heading_path`), `locale`, `token_estimate`.
   - `KnowledgeCorpus` — the registry view: `documents()`, `passages_of(document_id)`, `stats()`.
   - **Scope** — `global` (applies to any conversation), or a restriction such as
     `{"kind_key": "air_travel", "carrier": "XY"}`. Fare rules for one airline must not silently ground
     answers about another, and scope is how F19 filters. Scope is free-form metadata, matched by F19
     against the Trip Plan.
2. **Extraction** (`tourganize/adapters/knowledge/ingestion/`) — one extractor per media type behind a
   small `TextExtractor` protocol: PDF (`pypdf`, page-anchored), plain text, Markdown, HTML, DOCX
   (optional extra). Each yields `(text, anchor)` units. A scanned/image-only PDF yields **no** text and
   is recorded with `status="no_text_extracted"` and a clear warning — never a silent empty document. OCR
   is explicitly out of scope and named as a possible later feature.
3. **Chunking into Passages** (`.../chunking.py`) — a `PassageSplitter` protocol with one shipped
   implementation: structure-aware (split on headings and blank-line paragraph boundaries), packing to
   `TOURGANIZE_PASSAGE_TARGET_TOKENS` (default 350) with `TOURGANIZE_PASSAGE_OVERLAP_TOKENS` (default 50),
   never splitting mid-sentence where a boundary is available, and carrying `heading_path` so a Citation
   can say *"Fare rules § 4.2, p. 11"*. Hebrew and mixed documents must chunk correctly: token estimation
   is script-aware (a character-based heuristic per script, documented as an estimate — the real tokenizer
   belongs to the model and arrives in F19/F21).
4. **Corpus store** (`tourganize/adapters/knowledge/corpus/`) — SQLite (same file as sessions or its own,
   configurable) holding documents and passages, with `sha256`-based **idempotency**: re-adding an
   identical file is a no-op reporting "unchanged"; a changed file creates a **new version** of the
   document (`version` column) and marks the old one superseded rather than mutating history, because
   fare rules change and answers must be attributable to a version.
5. **CLI** — `tourganize docs add <path> [--tags k=v] [--scope k=v] [--locale L] [--title T]`,
   `docs list`, `docs show <id> [--passages]`, `docs remove <id>`, `docs reindex <id>` (re-chunk after a
   splitter change), and `docs verify` (checksums still match, files still present).
6. **Ingestion report** — every `docs add` prints and records: extraction status, page count, Passage
   count, detected locale, mean/median Passage size, and any warnings (empty pages, very long
   unsplittable blocks, mixed locales). This is the operator's only window into chunk quality before
   retrieval exists.
7. **Telemetry** — one event per ingestion with document id, size, passage count, duration, extractor and
   splitter versions. `ingestor_version` on the record is what makes a future re-chunk auditable.

## Contract (the Lego connectors)

**Inputs:** a file path, plus optional title, tags, scope and locale hint.

```python
@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    title: str
    source_path: str
    media_type: str
    sha256: str
    locale: str
    version: int
    page_count: int | None
    ingested_at: datetime
    ingestor_version: str
    tags: Mapping[str, str]
    scope: Mapping[str, str]
    status: Literal["ready", "no_text_extracted", "superseded", "failed"]

@dataclass(frozen=True)
class Passage:
    passage_id: str
    document_id: str
    ordinal: int
    text: str
    anchor: PassageAnchor          # page, char_start, char_end, heading_path
    locale: str
    token_estimate: int

class TextExtractor(Protocol):
    media_types: frozenset[str]
    def extract(self, path: Path) -> Sequence[ExtractedUnit]: ...

class PassageSplitter(Protocol):
    splitter_id: str
    def split(self, units: Sequence[ExtractedUnit], locale: str) -> Sequence[Passage]: ...
```

**Outputs:** `DocumentRecord`s and `Passage`s in the corpus store; an ingestion report.

**Ports consumed:** `Clock`, `TelemetrySink`, `LanguageDetector` (F10).

**Ports provided:** `KnowledgeCorpus`, `TextExtractor`, `PassageSplitter` (plus fakes:
`InMemoryCorpus`, `FixedSplitter`).

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_CORPUS_DB_PATH` | Corpus store | `${TOURGANIZE_DATA_DIR}/corpus.db` |
| `TOURGANIZE_CORPUS_FILE_DIR` | Where added files are copied for durability | `${TOURGANIZE_DATA_DIR}/corpus/files` |
| `TOURGANIZE_PASSAGE_TARGET_TOKENS` | Target Passage size | `350` |
| `TOURGANIZE_PASSAGE_OVERLAP_TOKENS` | Overlap between Passages | `50` |
| `TOURGANIZE_PASSAGE_MAX_TOKENS` | Hard cap before forced split | `800` |
| `TOURGANIZE_INGEST_MAX_FILE_MB` | Refuse larger files | `50` |

**Errors/failure modes:** `UnsupportedMediaTypeError` (naming supported types);
`DocumentTooLargeError`; `ExtractionFailedError` (recorded as `status="failed"`, keeping the record so the
operator sees it); a corrupt file fails one document, never the corpus. `docs verify` reports drift rather
than repairing it.

## Out of scope

Embeddings, vector search, retrieval, grounding, citations in answers — all F19. Fine-tuning (F23). OCR.
Translation of documents. Any change to conversation behaviour: after this feature the assistant still
knows nothing new; it merely holds documents.

## Replaceability notes

**Must be preserved:** `DocumentRecord`/`Passage`/`PassageAnchor` shapes (F19's index and Citations depend
on anchors); content-hash idempotency and versioning; scope metadata; `ingestor_version` and
`splitter_id` being recorded.

**Free to change:** extraction libraries; the splitter algorithm (`docs reindex` exists precisely so it
can change); the store backend; token estimation.

## Definition of done

- [ ] `tourganize docs add fixtures/knowledge/fare_rules_en.pdf --scope carrier=XY` prints an ingestion
      report with page count, Passage count and detected locale, and `docs list` then shows the document.
- [ ] `docs show <id> --passages` prints Passages with page numbers and heading paths.
- [ ] Idempotency: re-adding the identical file reports "unchanged" and creates no new Passages; adding a
      modified file creates version 2 and marks version 1 `superseded` (both asserted).
- [ ] Formats: PDF, plain text, Markdown and HTML fixtures all ingest with correct anchors; an image-only
      PDF is recorded `no_text_extracted` with a warning and no Passages.
- [ ] Hebrew: a Hebrew PDF ingests with `locale=he`, Passages in logical order, no mojibake, and correct
      page anchors (asserted on a known string and its page).
- [ ] A mixed-language document tags Passages individually where scripts differ (asserted on a fixture
      containing both).
- [ ] Chunking quality: Passages respect target/overlap/cap; a fixture with a 3,000-token unbroken table is
      force-split at the cap with a warning rather than producing one giant Passage.
- [ ] `docs reindex` with different target sizes changes Passage counts, leaves the document record intact
      (same `document_id`, new `splitter_id` recorded), and is idempotent.
- [ ] `docs remove` deletes document and Passages; `docs verify` detects a mutated source file.
- [ ] Oversized and unsupported files raise the documented errors with actionable messages.
- [ ] Telemetry event per ingestion with extractor and splitter versions.
- [ ] The conversation is provably unchanged: all Golden Conversations pass, and a test asserts no
      knowledge lookup occurs during a session (there is nothing to look up yet).
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — `tourganize.domain.knowledge` imports no extraction
      library.

## Open questions / risks

- **Implementer's call:** PDF library choice; heading detection heuristics; whether files are copied or
  referenced (recommended: copied — the source may be a temporary upload); token-estimation constants.
- **Risk:** chunk quality is the single biggest determinant of retrieval quality, and it cannot be measured
  until F19. Mitigation: the ingestion report, `docs reindex`, and treating splitter parameters as
  configuration from day one.
- **Risk:** scope being ignored later, letting one airline's rules answer questions about another. F19's DoD
  includes a scope-isolation test for exactly this.
- **Open (client):** who supplies the documents, how often do they change, and are any of them
  confidential? Storage is currently plaintext in a container volume.
