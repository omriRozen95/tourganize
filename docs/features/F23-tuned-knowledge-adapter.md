# F23 — Tuned knowledge adapter (Unsloth fine-tuning path)

- **Bounded context:** Knowledge Augmentation
- **Depends on:** [F19](F19-knowledge-retrieval-and-grounding.md), [F20](F20-model-service-and-gpu-profile.md), [F21](F21-self-hosted-model-adapter-and-parity.md)
- **Unlocks:** nothing — it is an alternative path, by design
- **Size:** L
- **Track:** **deferred / optional** — retrieval is the first-class path ([D6](../architecture/decisions.md))
- **Status of the codebase when this starts:** supplementary documents are ingested into Passages and
  reach the model through hybrid retrieval with Citations, behind the `KnowledgeRetriever` port. The
  self-hosted model serves the application. No weights have ever been modified.

## Purpose

Deliver the second path the client named in C10: adapt the open-weights model to document content with
**Unsloth** LoRA fine-tuning, exposed behind the *same* `KnowledgeRetriever` port so the rest of the system
cannot tell which mechanism answered. It is deliberately optional and late: it buys style and terminology
adherence and lower per-turn token cost, at the price of a training cycle per document change and weaker
attribution — which is exactly why [D6](../architecture/decisions.md) made retrieval first-class.

## Starting state

From F18/F19: the corpus, Passages with anchors and scope, the retriever port, the strict-refusal policy,
Citations end to end. From F20: the Model Service, the engine abstraction, and the reserved **RTX 3090 Ti
(Ampere)** — the only card with bfloat16, hence the training card ([D11](../architecture/decisions.md)).
From F21: the hosted-model gateway (the tuned model is reached through it, which is why F21 is a hard
dependency) and the parity report, which this feature reuses as its evaluation instrument.

## Scope — what to implement

1. **Training-set builder** (`services/model_tuning/dataset/`) — turn Passages into an instruction dataset
   **without inventing facts**: generate question/answer pairs by templating over Passages
   (extraction-style Q&A grounded in the Passage text, with the Passage as the answer's support), keep a
   per-example link to `passage_id` for provenance, hold out a stratified evaluation split, and refuse to
   train when the corpus is below `TUNING_MIN_PASSAGES` (too little data is how a model learns to
   hallucinate confidently). Dataset files are versioned artefacts with a manifest recording corpus
   document ids and versions.
2. **Tuning job** (`services/model_tuning/train.py`) — Unsloth LoRA/QLoRA over the same base model the
   Model Service serves: 4-bit base, bf16 compute (Ampere only), configurable rank/alpha/learning
   rate/epochs, deterministic seed, and a run manifest capturing base model id, dataset version, every
   hyperparameter, GPU, duration and final losses. Output: a **LoRA adapter**, never a merged full model —
   adapters are small, stackable and revertible.
3. **Adapter registry** (`services/model_tuning/registry/`) — `adapter_id`, base model, dataset version,
   corpus scope, metrics, `status` (`candidate`/`active`/`retired`), stored under a mounted volume with a
   manifest per adapter. Promotion to `active` is an explicit, recorded action.
4. **Serving integration** (extends F20 without changing its contract) — `MODEL_ADAPTER_ID` loads a LoRA
   adapter at start-up; `/v1/capabilities` reports the loaded adapter id, so the application can record it
   in the ledger. If the engine supports hot-swapping adapters, expose it; otherwise document that a
   restart is required. **The wire contract does not change.**
5. **`TunedRecallAdapter`** (`tourganize/adapters/knowledge/tuned/`) — implements `KnowledgeRetriever`
   without a vector index: it asks the tuned model, through the gateway, which document sections are
   relevant, and returns `RetrievedPassage`s resolved from the corpus by the returned anchors. The port
   requires anchors, so a tuned path that cannot point at *something* is not acceptable
   ([D6](../architecture/decisions.md)'s reversal clause) — where the model returns an unresolvable
   anchor, the result is dropped with a diagnostic rather than cited.
6. **Composite mode** — `TOURGANIZE_KNOWLEDGE_BACKEND=hybrid+tuned` merges retrieval and tuned recall,
   preferring retrieval-anchored Passages for Citations. This is the recommended production shape if
   tuning is adopted at all: tuning for fluency and terminology, retrieval for attribution.
7. **Evaluation** — reuse F21's parity harness with a knowledge-question suite: answer accuracy against a
   labelled fixture set, citation resolvability rate, refusal correctness (does it still say "not in the
   documents"?), tokens per turn, and latency — comparing `hybrid`, `tuned` and `hybrid+tuned`. A
   **regression gate**: adopting a tuned adapter requires refusal correctness no worse than the retrieval
   baseline, because a fine-tuned model that has stopped saying "I don't know" is worse than useless for
   fare rules.
8. **Operator documentation** — when tuning is worth it, the cost per run on the 3090 Ti, the "documents
   changed → retrain" cycle, how to roll back an adapter, and the explicit statement that **the Claude
   backend cannot use this path at all**.

## Contract (the Lego connectors)

**Inputs:** the Knowledge Corpus; training configuration; a base model id.

```python
class TunedRecallAdapter:                 # implements KnowledgeRetriever
    retriever_id = "tuned"
    def __init__(self, gateway: LlmGateway, corpus: KnowledgeCorpus,
                 adapter_id: str, settings: TunedRetrieverSettings) -> None: ...
    def retrieve(self, query: KnowledgeQuery) -> Sequence[RetrievedPassage]: ...
```

```json
// services/model_tuning/registry/<adapter_id>/manifest.json
{"adapter_id": "fare-rules-2026-09-a", "base_model_id": "Qwen2.5-14B-Instruct-AWQ",
 "dataset_version": "ds-2026-09-01", "corpus_documents": [{"document_id": "…", "version": 2}],
 "hyperparameters": {"rank": 16, "alpha": 32, "lr": 2e-4, "epochs": 3, "seed": 1234},
 "gpu": "NVIDIA GeForce RTX 3090 Ti", "duration_minutes": 41,
 "metrics": {"eval_loss": 0.71, "answer_accuracy": 0.78, "refusal_correctness": 0.91},
 "status": "candidate"}
```

**Outputs:** LoRA adapters with manifests; an alternative `KnowledgeRetriever`; an evaluation comparison.

**Ports consumed:** `KnowledgeCorpus`, `LlmGateway`, `TelemetrySink`, `Clock`.

**Ports provided:** a second `KnowledgeRetriever` implementation; the adapter registry.

**Config/env keys introduced:**

| Key | Meaning | Default |
|---|---|---|
| `TOURGANIZE_KNOWLEDGE_BACKEND` | gains `tuned` and `hybrid+tuned` | `hybrid` |
| `TOURGANIZE_TUNED_ADAPTER_ID` | Adapter the app expects the service to serve | unset |
| `MODEL_ADAPTER_ID` | Adapter the Model Service loads (service-side) | unset |
| `TUNING_GPU_INDEX` | Training GPU (the Ampere card) | `0` |
| `TUNING_MIN_PASSAGES` | Refuse to train below this | `200` |
| `TUNING_OUTPUT_DIR` | Adapter registry root | `/models/adapters` |

**Errors/failure modes:** `InsufficientCorpusError` (below the minimum — refuse, do not train a placebo);
`TuningJobFailedError` with the run manifest retained for diagnosis; `AdapterIncompatibleError` (adapter
built for a different base model — the service refuses to load rather than serve nonsense);
unresolvable anchors are dropped with diagnostics.

## Out of scope

Full fine-tuning or merged weights. Continuous/automated retraining. Tuning for dialogue behaviour rather
than document knowledge (the state machine owns behaviour — [D2](../architecture/decisions.md); tuning the
model to "be a better planner" is explicitly not this feature). Any change to F19's retrieval or to F20's
wire contract. Multi-adapter serving beyond one active adapter.

## Replaceability notes

**Must be preserved:** the `KnowledgeRetriever` port including anchor-bearing results; the adapter registry
manifest (auditability of what was trained on what); the refusal-correctness gate; the ability to run with
`TOURGANIZE_KNOWLEDGE_BACKEND=hybrid` and no adapter at all.

**Free to change:** Unsloth for another trainer; dataset generation strategy; hyperparameters; whether
adapters hot-swap.

## Definition of done

- [ ] A tuning run on the fixture corpus completes on the Ampere card and produces an adapter with a
      complete manifest; the run is reproducible from the manifest (same seed → same eval loss within
      tolerance).
- [ ] `InsufficientCorpusError` is raised for a corpus below `TUNING_MIN_PASSAGES`.
- [ ] The Model Service loads the adapter, reports its id in `/v1/capabilities`, and the application
      records it in the ledger; an adapter for a different base model is refused with
      `AdapterIncompatibleError`.
- [ ] `TunedRecallAdapter` passes the same knowledge-question conversations as the retrieval path, with
      Citations whose anchors resolve to real Passages; unresolvable anchors are dropped, not cited
      (asserted).
- [ ] **Refusal gate:** on the labelled question set, refusal correctness with the tuned adapter is no
      worse than the retrieval baseline. If it is worse, the adapter stays `candidate` — and the DoD is
      still met, because the gate did its job and the result is recorded.
- [ ] The comparison report (`hybrid` vs `tuned` vs `hybrid+tuned`) covers answer accuracy, citation
      resolvability, refusal correctness, tokens per turn and latency, and is committed.
- [ ] `hybrid+tuned` prefers retrieval-anchored Passages for Citations (asserted).
- [ ] Rollback: unsetting `MODEL_ADAPTER_ID` restores base-model behaviour, and
      `TOURGANIZE_KNOWLEDGE_BACKEND=hybrid` restores pure retrieval — both tested.
- [ ] Everything works with tuning absent: the full Golden Conversation suite passes with no adapter
      configured (this feature must be skippable).
- [ ] The operator note covers the retrain cycle, cost, rollback and the Claude limitation.
- [ ] `mypy --strict`, `ruff`, `lint-imports` pass; `services/model_tuning/` does not import `tourganize`.

## Open questions / risks

- **Risk (the reason this is deferred):** fine-tuning on templated Q&A over a small corpus teaches
  confident recall of an approximation. For fare rules, a wrong-but-fluent answer is worse than a citation
  the traveller can check — hence the refusal gate and the recommendation of `hybrid+tuned` over `tuned`.
- **Risk:** every document change invalidates the adapter, so operational cost is per-change, not
  per-document. Retrieval has no such cost, which is the whole of [D6](../architecture/decisions.md).
- **Risk:** VRAM. QLoRA on a 14B base on a 24 GB Ampere card is feasible; a 32B base is not comfortable.
  Sizing must be measured, like F20's capacity numbers.
- **Open (client):** is this path wanted at all, or was it listed as an option? Retrieval already satisfies
  C10; this feature exists so the answer can be "yes" without a redesign.
