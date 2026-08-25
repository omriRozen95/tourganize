# F19 — Knowledge retrieval and grounded answers

- **Bounded context:** Knowledge Augmentation
- **Depends on:** [F08](F08-llm-gateway-and-prompt-library.md), [F18](F18-document-ingestion-and-corpus.md)
- **Unlocks:** F23
- **Size:** L — *split offered below*
- **Status of the codebase when this starts:** documents are ingested into Passages with anchors, scope and
  versions, but nothing reads them. The `ExtractionRequest.grounding` field exists (F08) and is always
  empty; `ItineraryDocument.citations` exists (F13) and is always empty.

## Purpose

Let the assistant use what it has been told. This feature embeds Passages, retrieves the relevant ones for
a turn, injects them into Gateway calls as **grounding**, and carries **Citations** through to the answer
and the exported document ([D6](../architecture/decisions.md)). It is what makes "what's the change fee on
this fare?" answerable from the client's own PDF rather than from the model's imagination.

**Optional split:** F19a = embeddings + index + `KnowledgeRetriever` + `tourganize docs query` (a
verifiable retrieval CLI with no dialogue changes); F19b = grounding in the conversation, Citations and
export. F19a's DoD is fully observable alone.

## Starting state

From F18: corpus, Passages with anchors and scope, `docs` CLI. From F08: gateway with a `grounding` field,
prompt library, ledger. From F13: `citations` on the Itinerary Document. From F05: the Act vocabulary,
which gains one act here.

## Scope — what to implement

1. **Retriever port** (`tourganize/ports/knowledge.py`) — `KnowledgeQuery` (`text`, `locale`,
   `scope_filter`, `top_k`, `min_score`), `RetrievedPassage` (`passage`, `score`, `retriever_id`),
   `KnowledgeRetriever.retrieve(query)`. This is the port [D6](../architecture/decisions.md) promises F23
   an alternative adapter behind, so it must not mention vectors.
2. **Embedding port and adapter** (`tourganize/adapters/knowledge/embedding/`) — `EmbeddingModel` with
   `embed_documents` / `embed_query` and `dimensions`; a local `sentence-transformers` adapter
   (`TOURGANIZE_EMBEDDING_MODEL`, default a **multilingual** model since Hebrew is first-class — e.g. a
   multilingual MiniLM/E5-class model), plus a deterministic `HashEmbedding` fake so tests never download
   weights. Runs on CPU by default; may use the Ampere GPU when available
   ([D11](../architecture/decisions.md) reserves the 3090 Ti for exactly this).
3. **Index** (`tourganize/adapters/knowledge/vector/`) — a persistent vector index over Passage
   embeddings, keyed by `passage_id`, storing `document_id`, `scope`, `locale` and `version` for
   filtering. Start with a small, dependency-light store (numpy-backed flat index with cosine similarity,
   memory-mapped, or SQLite+`sqlite-vec`); the port makes the choice reversible. Requirements: incremental
   add on `docs add`, removal on `docs remove`, rebuild on `docs reindex`, a stored
   `embedding_model_id` + `dimensions` with a **mismatch check** that refuses to mix embeddings from two
   models, and `tourganize docs index --rebuild`.
4. **Hybrid retrieval** — vector similarity plus a lexical (BM25-ish or SQLite FTS5) channel, merged by
   reciprocal-rank fusion. Reason: fare rules are full of exact tokens (fare codes, "XY123", clause
   numbers) that embeddings blur, and Hebrew embedding quality is an open risk — the lexical channel is
   the cheap insurance. Configurable weights; either channel can be disabled.
5. **Scope filtering** — a retrieval must never cross scope: only `global` Passages and those whose scope
   matches the current Trip Plan's facts (carrier of the selected air travel, `kind_key` in focus, tags).
   Superseded document versions are excluded unless explicitly requested. **This is a correctness
   requirement, not a nicety** — see the DoD's isolation test.
6. **Grounding in the conversation** — a `KnowledgeGroundingService` that, for turns whose interpretation
   indicates a question about rules/policies/conditions (a new `ASK_KNOWLEDGE` intent added to F05's enum
   with its extraction schema field), retrieves top-k Passages and issues a Composition call with them as
   `grounding`, producing a new `answer_knowledge` Act carrying the answer **and its Citations**. Rules:
   - a grounded answer that finds no Passages above `min_score` says so — it must not fall back to
     ungrounded model knowledge (`TOURGANIZE_KNOWLEDGE_STRICT=true` default);
   - every sentence-level claim carries at least one Citation, and a post-check drops an answer with no
     citations back to "I don't have that in the documents I was given";
   - grounding never changes plan state: no Requirement Values are extracted from a grounded answer.
7. **Citations end to end** — `Citation` (`document_id`, `document_title`, `version`, `passage_id`,
   `page`, `heading_path`, `quote`) rendered in the slate/answer view (F07 message keys) and collected into
   `ItineraryDocument.citations` (F13) so the exported plan lists its sources — including in Hebrew.
8. **Optional grounding of option presentation** — when a selected option's provider matches a scoped
   document (e.g. the carrier's fare rules), attach the top Passage as a note on the itinerary section.
   Config-gated, default on, cheap, and the most concretely useful use of the corpus.
9. **CLI** — `tourganize docs query "<text>" [--scope k=v] [--top-k N] [--explain]` printing ranked
   Passages with scores, channel contributions and anchors; `tourganize docs index --rebuild|--status`.
10. **Golden Conversations** — `knowledge_question_grounded` (a fare-rules question answered with a
    Citation from a fixture document) and `knowledge_question_no_source` (a question with nothing relevant,
    asserting the honest refusal).

## Contract (the Lego connectors)

**Inputs:** a `KnowledgeQuery`; the corpus; an embedding model.

```python
@dataclass(frozen=True)
class KnowledgeQuery:
    text: str
    locale: str
    scope_filter: Mapping[str, str] = field(default_factory=dict)
    top_k: int = 5
    min_score: float = 0.0

@dataclass(frozen=True)
class RetrievedPassage:
    passage: Passage
    score: float
    retriever_id: str
    channel_scores: Mapping[str, float] = field(default_factory=dict)

class KnowledgeRetriever(Protocol):
    @property
    def retriever_id(self) -> str: ...
    def retrieve(self, query: KnowledgeQuery) -> Sequence[RetrievedPassage]: ...
```

**Outputs:** ranked Passages; `answer_knowledge` Acts with Citations; citations in the export; index files
under `TOURGANIZE_DATA_DIR`.

**Ports consumed:** `KnowledgeCorpus` (F18), `LlmGateway` (F08), `Clock`, `TelemetrySink`.

**Ports provided:** `KnowledgeRetriever` (`HybridRetriever`, `NullRetriever`, `FixedRetriever` fake),
`EmbeddingModel` (`SentenceTransformerEmbedding`, `HashEmbedding` fake).

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_KNOWLEDGE_BACKEND` | `none` / `hybrid` / `vector` / `lexical` | `hybrid` |
| `TOURGANIZE_EMBEDDING_MODEL` | Model id (or `hash` for the fake) | a multilingual sentence-embedding model |
| `TOURGANIZE_EMBEDDING_DEVICE` | `cpu` / `cuda:N` | `cpu` |
| `TOURGANIZE_VECTOR_INDEX_PATH` | Index location | `${TOURGANIZE_DATA_DIR}/knowledge/index` |
| `TOURGANIZE_RETRIEVAL_TOP_K` | Default `top_k` | `5` |
| `TOURGANIZE_RETRIEVAL_MIN_SCORE` | Score floor for grounding | `0.25` |
| `TOURGANIZE_RETRIEVAL_FUSION` | Channel weights, e.g. `vector=0.6,lexical=0.4` | `vector=0.6,lexical=0.4` |
| `TOURGANIZE_KNOWLEDGE_STRICT` | Refuse ungrounded answers | `true` |
| `TOURGANIZE_KNOWLEDGE_GROUND_OPTIONS` | Attach scoped Passages to itinerary sections | `true` |

**Errors/failure modes:** `EmbeddingModelUnavailableError` (model not downloadable/loadable → `doctor`
reports it, and the system degrades to the lexical channel rather than losing retrieval);
`IndexMismatchError` (index built with a different embedding model — refuses to query, tells the operator to
rebuild); an empty index yields zero results and the honest refusal, never an exception; a grounding
Composition timeout degrades to "I could not check the documents in time".

## Out of scope

Fine-tuning (F23). Cross-document reasoning or summarising a whole corpus. Automatic re-ingestion on file
change. Using retrieval to *fill Requirement Values* — grounded answers inform the traveller, they do not
mutate the plan. A reranker model (a natural, additive later improvement; noted, not built).

## Replaceability notes

**Must be preserved:** the `KnowledgeRetriever` port with no vector vocabulary; `Citation` shape and the
requirement that grounded answers cite; scope isolation; the strict-refusal default; the
`embedding_model_id` consistency check.

**Free to change:** embedding model, index implementation, fusion strategy, whether a reranker is added,
chunk-time vs. query-time expansion.

## Definition of done

- [ ] `tourganize docs query "change fee" --scope carrier=XY --explain` prints ranked Passages with
      scores, per-channel contributions, page numbers and heading paths.
- [ ] `knowledge_question_grounded` conversation: a fare-rules question is answered with an
      `answer_knowledge` Act whose Citations name the fixture document, version and page; the same in
      Hebrew against a Hebrew fixture document.
- [ ] `knowledge_question_no_source`: with `TOURGANIZE_KNOWLEDGE_STRICT=true` an unanswerable question
      yields the honest refusal and **no** ungrounded prose (asserted on the Act payload).
- [ ] **Scope isolation test:** with two carriers' fare rules ingested, a query scoped to carrier XY never
      returns a YZ Passage — asserted directly on retrieval *and* through a full conversation.
- [ ] Superseded versions are excluded: re-adding a modified document and querying returns only version 2
      Passages.
- [ ] Hybrid retrieval: a query for an exact token (`"XY123"`) ranks the lexically matching Passage first
      even when embeddings disagree; disabling the lexical channel changes that ranking (proving fusion is
      real).
- [ ] Determinism with the `hash` embedding fake: the same query twice yields identical rankings, and the
      whole suite runs with no model download and no network.
- [ ] Index lifecycle: `docs add` incrementally indexes, `docs remove` removes, `docs reindex` rebuilds,
      `docs index --status` reports document/Passage/vector counts, and a model change raises
      `IndexMismatchError` until `--rebuild`.
- [ ] Degradation: with the embedding model unavailable, retrieval still works through the lexical channel
      and `doctor` says so.
- [ ] Citations reach the export: the PDF and markdown documents list sources with page numbers, in both
      locales.
- [ ] Grounding does not mutate plans: a test asserts no Requirement Value changes across a grounded
      question turn.
- [ ] Ledger shows grounding token counts separately from ordinary calls, so the client can see what
      retrieval costs per turn.
- [ ] All prior Golden Conversations pass with `TOURGANIZE_KNOWLEDGE_BACKEND=none` and with `hybrid`.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass — no embedding library imported outside
      `adapters/knowledge/`.

## Open questions / risks

- **Implementer's call:** vector store implementation; embedding model choice; fusion weights; whether
  `ASK_KNOWLEDGE` gets its own prompt template (recommended: yes).
- **Risk (real):** Hebrew retrieval quality. Multilingual embeddings are uneven on Hebrew; the lexical
  channel and `--explain` output exist to make that visible rather than mysterious. Measuring it needs a
  small labelled question set — worth adding as a follow-on if Hebrew documents matter to the client.
- **Risk:** grounding inflating every turn's token cost. Mitigated by intent-gated grounding (only
  `ASK_KNOWLEDGE` turns), `top_k` and `min_score`.
- **Risk:** citation theatre — a citation that does not actually support the sentence. The post-check only
  proves a citation *exists*. Honest limitation; a proper claim-level check is a research task, and the
  strict-refusal default is the pragmatic guard.
