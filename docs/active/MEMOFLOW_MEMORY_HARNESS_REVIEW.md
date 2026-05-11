# Memoflow Memory Harness End-to-End Review

Date: 2026-05-06
Scope: current v0.1-v0.4 memory harness, including the uncommitted formation quality gate work in the working tree.

## Executive Summary

The current Memoflow memory harness is already a safe, test-covered alpha loop. It can preserve raw episodes, compact short-term context into checkpoints, form memory candidates, generate write plans, persist storage records, retrieve matching memory, inject bounded retrieval context, and explain the path through debug traces.

However, it is not yet a product-grade long-term memory intelligence system. The strongest parts are deterministic runtime safety, episode/checkpoint persistence, feature flags, SQLite schema, write-plan blocking, and regression smoke tests. The weakest parts are semantic judgment: what deserves memory, what intent requires retrieval, how to rank retrieved memories, whether graph relations are meaningful, and how to evolve/supersede memories over time.

The right next step is not to add lifecycle/dreaming. The right next step is to improve the harness quality gates and semantic decision points while keeping deterministic safety boundaries rule-based.

## Maturity Legend

- Product-ready: safe enough as a foundation for real usage, with non-blocking failure behavior and regression coverage.
- Usable-alpha: works in the main path, but quality/coverage is still incomplete or conservative.
- Prototype: valuable scaffold, but simplified and not yet trustworthy for broad production use.
- Concept-only: documented or schema-shaped, but no real operational loop yet.

## End-to-End Harness Flow

1. `/api/chat` receives a request and resolves session/model/provider context.
2. Runtime `prepare_turn` builds state from current chat history and latest checkpoint.
3. Prepare policy decides whether to load checkpoint and file memory.
4. Retrieval pipeline may build a retrieval plan and fetch long-term memory if enabled.
5. Working set compiler assembles pinned prompts, checkpoint, file memory, retrieval block, and recent raw turns.
6. Provider answers the user.
7. Runtime `finalize_turn` persists the completed turn as an episode.
8. Formation may run after the answer, either background or inline depending on flags.
9. Formation extracts candidates, quality-gates them, evaluates them, plans storage shape, and emits write plans.
10. Storage optionally persists candidates/plans and optionally applies write plans into SQLite records/projections/edges/suggestions.
11. Post-turn compaction may summarize older turns into a checkpoint for future working sets.
12. Debug traces record policy choices, loaded sources, retrieval traces, formation plans, storage results, and checkpoint audit.

## Component Assessment Table

| Harness stage | Current implementation | Maturity | Rule vs LLM | What is good | Main gaps | Recommended next action |
| --- | --- | --- | --- | --- | --- | --- |
| API chat orchestration | `main.py` calls chat service/runtime and streams provider output. | Product-ready foundation | Rule/deterministic | Main path is straightforward and memory is not mandatory. | Needs richer operational metrics around memory latency/failures. | Add structured timing counters later. |
| Model context budget policy | `services.model_context`, `config.py`, runtime budget debug. | Usable-alpha | Rule | Provider/model-specific thresholds exist and are traceable. | Thresholds are policy estimates, not dynamically learned. | Keep rule-based; later add observed-token telemetry. |
| Prepare-turn runtime state | `runtime.py`, `policy.py`. | Product-ready foundation | Rule | Builds deterministic state, estimates pressure, loads checkpoint only by policy. | Emergency compaction path is less exercised than post-turn path. | Add targeted pathological context tests. |
| Working set assembly | `working_set.py`. | Product-ready foundation | Rule | Deterministic ordering and budget trimming; drops file/retrieval blocks before core history. | Token estimate is approximate; no priority scoring among retrieval/file blocks. | Keep rule-based; later add richer budget allocator. |
| File memory loading | `file_memory.py` loads `CLAUDE.md/MEMORY.md/README.md` candidates. | Usable-alpha | Rule | Simple, explainable, bounded. | File memory is load-only; no review/apply workflow. | Keep read path; build review/apply only after formation quality stabilizes. |
| Episode persistence | `transcript_store.py`, `session_store.py`. | Product-ready foundation | Rule | Append-like raw evidence is persisted before derived memory. | No retention policy or export tooling yet. | Keep deterministic; add retention/export later. |
| Post-turn checkpoint compaction | `compaction.py`, `sidecar_compaction.py`, checkpoint audit. | Usable-alpha approaching product-ready | LLM for summary, rule for fallback/quality gate | Correct timing: after answer. Sidecar failure falls back. Quality gate and trace exist. | Summary quality still depends on sidecar prompt; no golden compaction eval suite. | Use LLM for summary; add compaction golden cases and scoring. |
| Checkpoint quality/audit | `checkpoint_audit.py`, sanitize/trace. | Usable-alpha | Rule plus heuristic | Prevents obviously polluted summary and traces covered episodes. | Quality is structural/heuristic, not semantic factuality. | Keep rule gate; add LLM critic only as non-blocking evaluator. |
| Formation trigger | `extractor.should_trigger_extraction`. | Prototype | Hybrid, currently rule | Cheap and safe; default-off; can trigger on explicit memory/project markers. | Rule markers are brittle; multilingual/mojibake compatibility exists but is not elegant. | Use rule as cheap first-pass; add optional LLM trigger planner for ambiguous high-value turns. |
| Candidate extraction | `extractor.py`, optional LLM, rule fallback. | Prototype | Should be LLM-primary with rule fallback | LLM path exists; raw JSON fragment parsing exists; rule fallback works for simple cases. | Current rule extractor is shallow; prompt still too JSON-centric; no model-quality eval against golden cases. | Upgrade prompt and mock/model contract tests. |
| Candidate post-processing quality gate | `formation/quality.py` uncommitted. | Usable-alpha | Rule | Bounded, deterministic, debuggable: dedupe, clipping, empty drop, max candidates. | Does not judge semantics; no persisted quality metrics yet. | Keep rule-based and make it mandatory for all extractors. |
| Candidate normalization schema | `schemas.py`. | Usable-alpha | Rule | Clear enums and clamping prevent malformed model output from poisoning planner. | Schema is still lite; no explicit memory layer/storage intent field. | Extend only after current golden cases stabilize. |
| Candidate evaluation | `evaluator.py`. | Prototype | Hybrid, currently rule | Blocks empty, low-importance, sensitive, invalid temporary semantic memories. | Sensitivity markers and importance thresholds are crude. | Keep deterministic hard blocks; add LLM evaluator for nuanced utility/risk. |
| Storage shape planning | `shape_planner.py`. | Usable-alpha | Rule | Correct principle: not everything goes to graph/vector; file suggestions are reviewable. | Relation detection is keyword-based; storage shape taxonomy still simplified. | Keep rule mapping; use LLM only to classify memory type/relation predicate upstream. |
| Write plan generation | `mutation_planner.py`. | Usable-alpha | Rule | Good deterministic bridge from candidate to storage plan with evidence/status/reasons. | No conflict detection or existing-memory lookup in planning stage. | Add lookup-aware update/supersession planning before enabling writes broadly. |
| Memory integration planning | `integration_planner.py`, `integration_schemas.py`, `neighborhood.py`. | Prototype | Hybrid target, deterministic dry-run now | Separates candidate extraction from candidate integration with existing memory; now fetches bounded SQLite-backed candidate neighborhoods before ADD/MERGE/LINK/NOOP/NEEDS_REVIEW. | Still rule-based, lexical-only, and not yet connected to background formation jobs or LLM planning. | Next add optional LLM planner over bounded snapshots, then map integration plans into write plans. |
| Dry-run write sinks | `stores/*.py`. | Prototype | Rule | Useful for debugging and tests; fast JSONL outputs. | Mock/projection stores are not real storage contracts. | Keep as harness diagnostics, not product storage. |
| SQLite durable storage | `storage/sqlite_store.py`, `applier.py`. | Usable-alpha | Rule | Real tables for candidates, plans, records, evidence, projections, graph edges, file suggestions, reindex jobs. | No migrations/versioning; embeddings are pending metadata; graph is shallow; lifecycle absent. | Add schema versioning before broad use. |
| Canonical memory records | `memory_records`, `evidence_links`. | Usable-alpha | Rule | Evidence-backed records and supersession fields exist. | Supersession is basic; delete/update semantics need stronger tests. | Add update/delete/supersede contract suite. |
| Vector projection | `memory_vector_projections`. | Concept/prototype | Rule now, embedding model later | Projection table and reindex jobs exist. | No embedding generation, no vector DB/index, retrieval is lexical over projection text. | Implement embedding worker or explicitly keep disabled. |
| Relation graph/DAG | `memory_graph_edges`, graph one-hop retrieval. | Prototype | Hybrid | Graph is only used for explicit relations, which is the right principle. | No DAG constraints, no multi-hop traversal, no target node resolution, no cycle semantics. | Keep one-hop; design graph predicate schema before DAG work. |
| File suggestion memory | `memory_file_suggestions`. | Prototype | Rule | Suggestions are pending review and not auto-applied. | No UI/API review/apply/reject loop. | Defer until formation quality is stronger. |
| Retrieval trigger/intent | `retrieval/triggers.py`. | Prototype | Should be hybrid/LLM for intent, rule for trivial skips | Cheap and non-blocking; avoids trivial queries. | Keyword/multilingual markers are brittle; cannot understand implicit need for memory. | Add optional LLM retrieval planner, backed by rule fallback. |
| Retrieval repository | `retrieval/repository.py`. | Usable-alpha for lexical, prototype for graph/vector | Rule | Searches exact records, projections, graph edges. | Projection path is lexical, not vector; graph search is shallow. | Build real vector path after embedding worker exists. |
| Retrieval ranking | `ranker.py`. | Prototype | Hybrid | Dedupe, path priors, min score, item/char budgets. | No semantic reranker, freshness, confidence decay, or contradiction handling. | Add LLM/embedding reranker as optional second stage. |
| Retrieval prompt injection | `assembler.py`, `working_set.py`. | Usable-alpha | Rule | Bounded system block; omitted if no items or over budget. | Injected memory lacks evidence ids and recency/confidence explanation. | Add compact evidence/source metadata to retrieval block. |
| Runtime debug/audit | `runtime.py`, event logs, tests. | Usable-alpha | Rule | Good traceability for policy, budget, sources, events, retrieval, formation. | Debug not yet normalized as a stable API; versions are coarse. | Create formal debug schema and version each subsystem separately. |
| Test harness | `tests/*smoke.py`, golden cases. | Usable-alpha | Rule | Covers main safety paths, storage/retrieval, quality contracts, long-task compaction. | Mostly smoke scripts, not pytest-style matrix; no live LLM golden eval. | Add pytest or harness runner with scenario fixtures and reports. |

## What Is Already Product-Grade Enough As Foundation

These pieces are reasonable to rely on while building higher-level quality:

- Raw episode persistence before derived memory.
- Memory feature flags default-off and non-blocking.
- Post-turn compaction timing rather than pre-answer routine compaction.
- Working set assembly with deterministic ordering and budget trimming.
- Write plan evidence requirement and bad-plan blocking.
- SQLite schema as a local durable prototype store.
- Retrieval block omission when disabled, empty, failing, or over budget.
- Stabilization and quality contract smoke tests.

These are not necessarily finished products, but they are safe foundations.

## What Is Still Initial/Prototype

These pieces work but should not be treated as final intelligence:

- Rule-based formation trigger and extraction.
- Rule-based candidate type/scope inference.
- Rule-based retrieval intent classification.
- Lexical retrieval over records/projections.
- Simple path-prior ranking.
- Keyword-based explicit relation detection.
- Checkpoint semantic quality validation.

They are useful scaffolds, but the system should not claim SOTA memory quality based on them.

## What Is Concept-Only Or Not Really Operational Yet

The following are present as docs, schemas, fields, or jobs, but not as full operational systems:

- Real embedding creation and vector index retrieval.
- Reindex job worker loop.
- Multi-hop graph traversal or true DAG semantics.
- Memory lifecycle: consolidation, decay, promotion, merge, contradiction resolution, deletion review.
- File memory review/apply/reject UI or API.
- Stable frame cache / incremental context compilation beyond the checkpoint/working-set path.
- Live LLM evaluation harness for formation/retrieval quality.
- Long-term memory namespace/privacy policy beyond basic scope/namespace derivation.

## Where LLM Should Be Used

LLM is appropriate where the decision is semantic, contextual, and hard to encode reliably:

- Checkpoint summarization and compaction, with deterministic quality gate after it.
- Candidate extraction from episodes.
- Candidate utility/risk explanation and borderline rejection review.
- Memory type/scope classification when rule confidence is low.
- Relation predicate extraction: depends_on, supersedes, contradicts, derived_from, blocks.
- Retrieval intent planning for non-trivial user messages.
- Reranking retrieved items when multiple memories compete for prompt budget.
- Optional critic/evaluator for compaction and formation golden cases.

LLM outputs should not directly write storage. They should produce structured candidates/plans that pass deterministic validators.

## Where Rules Are Sufficient Or Preferable

Rules should remain the authority where correctness, safety, and reproducibility matter:

- Feature flags and default-off behavior.
- Token budget thresholds and hard prompt trimming.
- Schema normalization and enum validation.
- Evidence-required write plan policy.
- Sensitive hard-block markers as a minimum safety floor.
- Scope containment and namespace derivation.
- Storage routing once type/scope are known.
- Write application and database mutations.
- Retrieval pack size/char budget.
- Non-blocking failure handling and debug trace assembly.

## Productization Risks

1. Memory pollution risk.
If candidate extraction is too eager, durable memory will accumulate false preferences/rules. This is currently mitigated by default-off flags, dry-run mode, quality contract tests, and conservative write planning, but semantic quality is still weak.

2. Retrieval irrelevance risk.
Keyword intent and lexical search will miss implicit memory needs and sometimes retrieve weak matches. This is acceptable for alpha but not product-grade memory.

3. Graph overclaim risk.
The graph tables exist, but the system does not yet have true graph reasoning. We should keep graph usage narrow and explicit.

4. Vector overclaim risk.
Vector projection rows and reindex jobs exist, but no embedding worker/index is operational. Any documentation should call this projection metadata, not real vector retrieval.

5. Encoding/multilingual brittleness.
Historical mojibake markers and readable Chinese markers both exist because earlier tests/data contain encoding artifacts. This is a harness compatibility issue and should be cleaned with fixture normalization later.

6. Lifecycle premature complexity.
Adding memory evolution before formation/retrieval quality improves would amplify noise. Lifecycle should wait.

## Recommended Upgrade Order

1. Formalize the harness runner.
Move from standalone smoke scripts toward a single scenario runner that emits a JSON report: formation precision, blocked pollution cases, retrieval hit rate, compaction coverage, runtime safety.

2. Upgrade LLM formation extraction under deterministic validators.
Keep `postprocess_candidates`, `normalize_candidate`, `evaluate_candidate`, and `shape_planner` deterministic. Improve prompt and parsing so model output can be messy natural language plus JSON fragment.

3. Add model-assisted candidate judge for borderline cases.
Use LLM only to explain utility/risk for candidates near thresholds. Hard safety rules still win.

4. Add retrieval planner and reranker as optional LLM stages.
Use rules for trivial skip and budget. Use LLM only for non-trivial intent planning and final pack selection.

5. Build real embedding/reindex worker.
Only then call vector projection a real retrieval path.

6. Add update/supersession contract tests.
Before lifecycle, make basic ADD/UPDATE/DELETE/supersedes behavior robust.

7. Defer lifecycle/dreaming until quality metrics are stable.
Do not consolidate, decay, or dream over unreliable memories.

## Bottom Line

Current Harness status: safe alpha closed loop.

The architecture direction is sound: raw evidence first, deterministic runtime, post-turn maintenance, conservative formation, write plans before storage, projections not source of truth, bounded retrieval injection.

The intelligence quality is not yet top-tier. To approach a world-class long-term memory framework, the next phase should focus on evaluation harness, model-assisted formation, model-assisted retrieval planning/reranking, and real vector indexing, while preserving deterministic validators and feature-flag safety.
