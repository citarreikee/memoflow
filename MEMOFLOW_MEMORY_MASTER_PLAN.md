# Memoflow Memory Master Plan

Last updated: 2026-05-05

## 1. Purpose

This document is the controlling map for Memoflow memory development.

The project now has several focused specs:

- Runtime v0.1
- Memory Formation v0.2
- Durable Storage v0.3
- Incremental Context Compilation
- Retrieval v0.4

Those specs intentionally deep-dive different layers, but they also introduce overlapping dataclass names and partially overlapping concepts. This master plan defines the dependency order, canonical vocabulary, and how the specs fit together.

Core rule:

```text
Each spec owns a layer.
This master plan owns the cross-layer vocabulary and implementation order.
```

How to read the docs:

1. Start here to understand dependencies, canonical names, and implementation order.
2. Read the layer spec for the phase being implemented.
3. Check the later specs only at integration boundaries, not as sources of new names.
4. Update this master plan before adding a new cross-layer dataclass or changing ownership.

Current priority:

```text
Implement Retrieval v0.4 before Memory Evolution / Dreaming.
Dreaming is useful for long-term quality, but not required for the first memory closed loop.
```

## 2. Development Order

### Phase 0: Baseline Chat Runtime

Already present:

- provider adapters
- session history
- ReAct streaming
- basic context assembly

### Phase 1: Runtime v0.1

Spec:

- `MEMOFLOW_MEMORY_RUNTIME_DEV_SPEC.md`

Owns:

- `prepare_turn`
- `finalize_turn`
- episodes
- checkpoints
- compaction
- file memory loading
- runtime debug events

Does not own:

- semantic memory extraction
- durable long-term memory
- vector/graph retrieval

### Phase 2: Memory Formation v0.2

Spec:

- `MEMOFLOW_MEMORY_V0_2_DEV_SPEC.md`

Owns:

- lightweight candidate extraction
- worth-storing policy
- storage-shape planning
- write plans
- dry-run store planning

Does not own:

- real database storage
- retrieval injection
- memory consolidation/dreaming

### Phase 3: Durable Storage v0.3

Spec:

- `MEMOFLOW_MEMORY_V0_3_STORAGE_DESIGN.md`

Owns:

- SQLite-backed candidates and write plans
- canonical memory records
- evidence links
- vector projection metadata
- graph edge table
- file suggestions
- re-index jobs
- update/delete/supersede basics

Does not own:

- full retrieval planning
- embedding generation at production scale
- external vector or graph infrastructure

### Phase 4: Retrieval and Context Injection v0.4

Spec:

- `MEMOFLOW_MEMORY_V0_4_RETRIEVAL_DESIGN.md`

Owns:

- retrieval triggers
- multi-path retrieval planning
- exact KV lookup
- vector projection recall
- graph relation recall
- time/scope filtering
- merge/rerank/budget control
- retrieval pack injection into context assembly

Does not own:

- long-term memory consolidation/dreaming
- automatic memory rewrite beyond existing storage semantics

Immediate implementation focus:

1. Add a no-op `RetrievalPack` slot and debug trace.
2. Add exact active-record retrieval from `memory_records`.
3. Add lexical fallback over `memory_vector_projections` before production embeddings.
4. Add one-hop graph recall over `memory_graph_edges`.
5. Inject a bounded model-facing retrieval block before recent raw turns.
6. Keep all retrieval failures non-blocking for chat completion.

### Phase 5: Incremental Context Compilation

Spec:

- `MEMOFLOW_INCREMENTAL_CONTEXT_COMPILATION.md`

Owns:

- stable context frames
- turn deltas
- retrieval pack slot
- dirty flags
- append-only fast path
- cache-aware context assembly

This phase can be implemented before or alongside v0.4, but the retrieval pack abstraction should align with v0.4.

### Phase 6: Memory Evolution / Dreaming

Future spec:

- not yet implemented

Will own:

- consolidation
- dedupe/merge/split
- decay/suppression/archive
- contradiction review
- re-index after rewrite
- idle-time maintenance

This is not required to close the first write -> store -> retrieve -> inject loop.

## 3. Canonical Vocabulary

### 3.1 Runtime terms

| Canonical term | Meaning | Current code/spec aliases |
| --- | --- | --- |
| `RuntimeTurnInput` | Per-turn input envelope for context compilation | `RuntimeInput` |
| `RuntimeStateSnapshot` | Cheap runtime state used for decisions | `RuntimeState`, future `RuntimeSessionState` |
| `ContextPlan` | Per-turn decision artifact | policy decision + debug fields |
| `CompiledContextFrame` | Reusable stable context material | future stable frame cache |
| `TurnDelta` | Current turn's new messages and counters | recent current turn, active user message |
| `ContextPackage` | Final provider-ready messages and debug | existing `ContextPackage` |

Guidance:

- Keep existing code names until refactor is scheduled.
- New docs and new modules should prefer canonical terms.
- `RuntimeInput` can remain as the concrete v0.1 class, but it conceptually maps to `RuntimeTurnInput`.

### 3.2 Formation terms

| Canonical term | Meaning | Current code/spec aliases |
| --- | --- | --- |
| `MemoryCandidate` | Extractor-produced lightweight candidate | `MemoryCandidateLite` |
| `MemoryWritePlan` | Deterministic storage/write decision | same |
| `MemoryFormationJob` | Background extraction + planning unit | formation job runner |
| `WorthStoringPolicy` | Candidate evaluation gates | evaluator |
| `StorageShapePlan` | Canonical store + projections | shape planner |

Guidance:

- `MemoryCandidateLite` remains acceptable in code because it explicitly enforces small schema.
- Higher-level docs may call it `MemoryCandidate` when contrasting with records/plans.

### 3.3 Storage terms

| Canonical term | Meaning | Current code/spec aliases |
| --- | --- | --- |
| `EvidenceLog` | Raw episodes and evidence links | episodes + `memory_evidence_links` |
| `StagingStore` | candidates and write plans | `memory_candidates`, `memory_write_plans` |
| `CanonicalMemoryRecord` | Durable curated memory | `memory_records` |
| `VectorProjection` | Embedding-ready recall projection | `memory_vector_projections` |
| `RelationEdge` | Explicit graph relation | `memory_graph_edges` |
| `FileMemorySuggestion` | Reviewable project memory patch | `memory_file_suggestions` |
| `ReindexJob` | Projection refresh marker | `memory_reindex_jobs` |

Guidance:

- Only `CanonicalMemoryRecord` is curated memory truth.
- Projections are not truth.

### 3.4 Retrieval terms

| Canonical term | Meaning |
| --- | --- |
| `RetrievalIntent` | Why the current turn needs memory |
| `RetrievalPlan` | Which stores to query, with budgets and filters |
| `RetrievalCandidate` | Raw item returned from one retrieval path |
| `RetrievalPack` | Final curated bounded memory payload for context |
| `RetrievalTrace` | Debug record explaining trigger, path, score, and inclusion/exclusion |

Guidance:

- Retrieval is a decision process, not a single vector search function.
- Retrieval pack belongs in context assembly, not stable frame cache.

## 4. Dependency Graph

```text
Runtime v0.1
  -> Memory Formation v0.2
      -> Durable Storage v0.3
          -> Retrieval v0.4
              -> Incremental Context Compilation integration
                  -> Memory Evolution / Dreaming
```

Parallel relationship:

```text
Incremental Context Compilation
  depends on Runtime v0.1 concepts
  prepares a slot for Retrieval v0.4
  does not require Dreaming
```

Implementation sequencing rule:

```text
Do not implement Dreaming until Retrieval v0.4 can prove:
stored durable memory -> recalled memory pack -> injected answer context.
```

Spec dependency table:

| Implementing | Must read first | Uses as input | Must not change |
| --- | --- | --- | --- |
| Runtime v0.1 | `MEMOFLOW_MEMORY_RUNTIME_DEV_SPEC.md` | baseline chat/session history | formation/storage/retrieval schemas |
| Memory v0.2 | this plan + Runtime v0.1 | episodes and finalized turns | storage database schema |
| Storage v0.3 | this plan + Memory v0.2 | write plans and candidates | retrieval ranking/context assembly |
| Retrieval v0.4 | this plan + Storage v0.3 + Incremental Context slot | active records, projections, graph edges | canonical memory mutation/dreaming |
| Incremental Compilation | this plan + Runtime v0.1 + Retrieval pack contract | stable frame, turn delta, retrieval pack | retrieval ranking semantics |
| Dreaming | future spec after v0.4 | records, evidence, retrieval traces | hot-path retrieval behavior |

When specs disagree, this precedence applies:

```text
Master Plan canonical vocabulary
  > current phase spec
  > older architecture notes
  > exploratory deep-dive notes
```

## 5. Cross-Spec Ownership Boundaries

### Runtime owns orchestration

Runtime decides when to prepare, finalize, compact, schedule formation, and assemble context.

Runtime must not own extraction prompts, storage schema, or retrieval ranking details.

### Formation owns write planning

Formation decides what could be remembered and how it should be stored.

Formation must not own durable database semantics beyond emitting candidates/plans.

### Storage owns durable state

Storage applies plans into durable records and projections.

Storage must not decide whether memory should be injected into the current prompt.

### Retrieval owns read planning

Retrieval decides what stored memory is relevant to the current turn.

Retrieval must not mutate canonical memory.

### Context compilation owns prompt assembly

Context compilation decides how stable frame, retrieval pack, recent raw turns, and current user input fit into the model budget.

Context compilation must not treat retrieval as source-of-truth mutation.

## 6. Implementation Guardrails

1. Do not introduce new dataclass names for already-canonical concepts unless this document is updated.
2. Do not let a lower layer reach upward into a higher layer's decision logic.
3. Do not make retrieval depend on memory dreaming.
4. Do not treat vector results as canonical facts.
5. Do not make storage failures block chat completion.
6. Do not cache query-dependent retrieval inside stable context frames.
7. Do not remove evidence links during update/delete/supersede.

## 7. Refactor Debt Register

Known naming debt:

- `RuntimeInput` should eventually be documented as `RuntimeTurnInput` or renamed when a larger runtime refactor happens.
- `RuntimeState` and proposed `RuntimeSessionState` overlap. Future code should split them into `RuntimeStateSnapshot` and persisted/cacheable `RuntimeSessionState`.
- `ContextPackage` currently contains final messages and debug; future design should separate `ContextPlan`, `CompiledContextFrame`, and final `ContextPackage`.
- `MemoryCandidateLite` is the concrete lightweight extractor schema; docs should avoid introducing another concrete class unless needed.
- Runtime v0.1, Memory v0.2, Storage v0.3, and Incremental Context Compilation describe overlapping concepts at different abstraction levels. Treat their current dataclasses as layer-local adapters unless they are listed in Section 3 as canonical terms.
- Retrieval v0.4 must use `RetrievalIntent`, `RetrievalPlan`, `RetrievalCandidate`, and `RetrievalPack`; avoid adding names like `RecallResult`, `MemorySearchHit`, or `ContextMemoryBundle` for the same concepts.

Do not block v0.4 retrieval on these refactors. Instead, make new retrieval code use the canonical terms here.
