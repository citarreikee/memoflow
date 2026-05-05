# Memoflow Memory v0.4 Retrieval and Context Injection Design

Last updated: 2026-05-05

## 1. Purpose

This spec defines the next closed-loop milestone for Memoflow memory:

```text
write -> store -> retrieve -> inject -> answer
```

Long-term memory evolution / dreaming is intentionally out of scope. Retrieval v0.4 only needs to read the durable memory state created by Runtime v0.1, Memory Formation v0.2, and Durable Storage v0.3, then inject a small, explainable memory pack into the current model context.

## 2. Deep Dive Takeaways

The deep dive argues that memory retrieval is not plain RAG.

RAG shape:

```text
Query -> Vector Search -> Context -> Answer
```

Memory system shape:

```text
Experience -> Write -> Store -> Retrieve -> Use -> Update / Consolidate
```

v0.4 implements the read/use part of that loop. The important takeaways are:

1. Retrieval is multi-path, not vector-only.
2. Retrieved items are structured memories, not flat chunks.
3. Temporal, scope, and relation filters matter as much as semantic similarity.
4. A retrieval result must be explainable because memory can influence behavior.
5. Retrieval is query-dependent and must not be cached inside a stable context frame.

## 3. v0.4 Scope

In scope:

- retrieval trigger decision
- lightweight intent analysis from the current turn
- exact lookup over canonical memory records
- lexical / vector-projection recall path
- one-hop graph relation recall path
- time, scope, status, and namespace filtering
- merge, dedupe, rerank, and budget trim
- `RetrievalPack` injection into context assembly
- debug trace for included and excluded items

Out of scope:

- long-term memory dreaming / consolidation
- automatic contradiction resolution
- automatic rewrite of canonical memory records
- production embedding infrastructure
- external vector database or graph database dependency
- making retrieval blocking for chat completion

## 4. Dependency Position

Retrieval v0.4 depends on:

- Runtime v0.1 for `prepare_turn`, `ContextPackage`, recent turns, checkpoints, file memory, and debug events.
- Memory Formation v0.2 for `MemoryCandidate` / `MemoryWritePlan` semantics.
- Durable Storage v0.3 for `memory_records`, `memory_vector_projections`, `memory_graph_edges`, and evidence links.
- Incremental Context Compilation only for the retrieval-pack slot and source ordering rules.

Retrieval v0.4 must not depend on:

- Memory Evolution / Dreaming.
- Future external vector or graph services.
- A large dataclass rename across existing runtime / formation / storage code.

## 5. Mental Model

Per turn, retrieval follows this pipeline:

```text
RuntimeTurnInput
  -> RetrievalIntent
  -> RetrievalPlan
  -> multi-path store reads
  -> RetrievalCandidate[]
  -> merge / rerank / budget trim
  -> RetrievalPack
  -> ContextPackage.messages
```

The planner decides whether memory is needed. The repository only reads storage. The ranker makes inclusion decisions. The context assembler owns final prompt placement.

## 6. Canonical Data Structures

These names are canonical for new retrieval code. Do not introduce another overlapping set of names without updating `MEMOFLOW_MEMORY_MASTER_PLAN.md`.

### 6.1 `RetrievalIntent`

```python
@dataclass(frozen=True)
class RetrievalIntent:
    kind: str
    query: str
    entities: list[str]
    scopes: list[str]
    memory_types: list[str]
    needs_time: bool = False
    needs_graph: bool = False
    needs_preferences: bool = False
```

Suggested `kind` values:

- `none`
- `preference_lookup`
- `profile_lookup`
- `project_rule_lookup`
- `task_state_lookup`
- `decision_lookup`
- `relation_lookup`
- `broad_recall`

### 6.2 `RetrievalPlan`

```python
@dataclass(frozen=True)
class RetrievalPlan:
    intent: RetrievalIntent
    paths: list[str]
    namespace: str
    scopes: list[str]
    limit_per_path: int
    max_pack_items: int
    max_pack_chars: int
    min_score: float
```

Suggested path values:

- `exact_record`
- `lexical_projection`
- `graph_one_hop`
- `recent_evidence`

### 6.3 `RetrievalCandidate`

```python
@dataclass
class RetrievalCandidate:
    source: str
    memory_id: str | None
    projection_id: str | None
    edge_id: str | None
    scope: str
    memory_type: str
    text: str
    score: float
    reason: str
    evidence_episode_ids: list[str]
    payload: dict[str, object]
```

### 6.4 `RetrievalPackItem`

```python
@dataclass(frozen=True)
class RetrievalPackItem:
    memory_id: str | None
    source: str
    memory_type: str
    scope: str
    text: str
    reason: str
    score: float
```

### 6.5 `RetrievalPack`

```python
@dataclass(frozen=True)
class RetrievalPack:
    intent: RetrievalIntent
    items: list[RetrievalPackItem]
    omitted_count: int
    estimated_chars: int
    trace: list[dict[str, object]]
```

## 7. Retrieval Trigger Policy

Default behavior should be conservative.

Trigger retrieval when the current user turn contains at least one of these signals:

- asks about user preference, profile, prior choice, decision, rule, task state, or project convention
- references past work, previous conversation, remembered facts, or continuity
- contains entity names that may have graph relations
- requests personalized behavior
- current token budget has room for a bounded memory pack

Skip retrieval when:

- the message is a trivial greeting or one-shot small talk
- memory feature flags are disabled
- no durable storage backend is available
- context budget is already under severe pressure
- the current query is fully answered by recent raw turns

The first implementation may use deterministic heuristics. LLM-based retrieval intent classification can be added later behind a feature flag.

## 8. Query Paths

### 8.1 Exact canonical record path

Reads `memory_records` where:

- `status = active`
- scope matches `session`, `user`, or `workspace`
- namespace matches session key / user / workspace namespace
- type matches the intent when known
- key or value text matches query terms

This path is highest precision and should usually outrank broad vector-like recall.

### 8.2 Lexical projection path

Reads `memory_vector_projections` joined to `memory_records` where possible.

In v0.4, this can start as lexical scoring over projection `text` / payload because production embeddings are out of scope. The repository interface should still be named around projections so a later embedding implementation can replace the scoring backend without changing planner or assembler code.

### 8.3 Graph one-hop path

Reads `memory_graph_edges` for explicit relations involving matched memory records, entity names, or evidence episodes.

Initial graph recall should be one-hop only:

```text
seed memory/entity -> direct edges -> related memory/entity
```

No recursive graph traversal in v0.4.

### 8.4 Recent evidence path

Uses evidence links only to explain or support included memories. It should not dump raw episodes into context unless the pack needs a short source snippet and budget allows it.

## 9. Ranking and Merge Rules

Candidate score should combine:

- path prior: exact record > graph one-hop > lexical projection > recent evidence
- lexical/entity match strength
- memory importance / confidence if available in payload
- scope priority: current session/workspace > user-global > broader global
- recency for task state and episodic events
- stability for preferences, profile facts, and project rules

Dedupe order:

1. same `memory_id`
2. same normalized text
3. same graph edge payload
4. same evidence episode id and source text

The pack must be budgeted after ranking, not before ranking.

## 10. Context Injection Contract

Retrieval output is inserted as context, not conversation history.

Recommended message placement:

```text
1. system / developer instructions
2. checkpoint summary
3. file/project memory
4. retrieval pack
5. recent raw turns
6. current user message
```

Recommended rendered block:

```text
Relevant memory retrieved for this turn:
- [preference/session score=0.91] User prefers concise implementation plans.
- [project_rule/workspace score=0.86] For Memoflow memory work, update the master plan before adding layer specs.
```

Rules:

- Keep the pack short and bounded.
- Never present retrieved memory as user text.
- Include memory only when it can change the answer.
- Prefer precise records over broad semantically similar snippets.
- Do not expose internal IDs in model-facing text unless needed for debugging.
- Keep full IDs and scores in debug output.

## 11. Proposed Module Layout

```text
services/memory/retrieval/
  __init__.py
  schemas.py          # RetrievalIntent, RetrievalPlan, RetrievalCandidate, RetrievalPack
  triggers.py         # deterministic trigger and intent classification
  repository.py       # read-only access over MemorySQLiteStore
  lexical.py          # tokenization and lexical scoring fallback
  ranker.py           # merge, dedupe, rerank, trim
  assembler.py        # model-facing retrieval block rendering
  pipeline.py         # plan -> read -> rank -> pack orchestration
```

Required storage additions:

- `MemorySQLiteStore.search_active_records(...)`
- `MemorySQLiteStore.search_vector_projections(...)`
- `MemorySQLiteStore.search_graph_edges(...)`
- `MemorySQLiteStore.list_evidence_links(...)`

Required runtime additions:

- create `MemoryRetrievalPipeline` in `MemoryRuntime.__init__`
- call retrieval during `prepare_turn` after policy/state calculation and before final context assembly
- pass the rendered retrieval block into working-set/context assembly
- add retrieval trace into `ContextPackage.debug["retrieval"]`

## 12. Implementation Order

### Phase 1: Empty retrieval slot

- Add retrieval dataclasses and no-op pipeline.
- Return empty `RetrievalPack` with trace reason `disabled_or_no_intent`.
- Surface debug output without changing provider messages.

### Phase 2: Exact record retrieval

- Add read-only SQLite search over `memory_records`.
- Implement deterministic trigger heuristics.
- Inject a bounded exact-record pack into context.
- Add smoke test: stored preference is recalled on a later turn.

### Phase 3: Lexical projection retrieval

- Add read-only search over `memory_vector_projections`.
- Implement lexical fallback scoring.
- Merge projection results with exact records.
- Add smoke test: semantically related decision/procedure text is recalled.

### Phase 4: Graph one-hop retrieval

- Add read-only search over `memory_graph_edges`.
- Seed graph lookup from exact/lexical candidates and explicit query entities.
- Add one-hop related memories when budget allows.
- Add smoke test: relation memory brings in a related decision or dependency.

### Phase 5: Runtime context injection hardening

- Add pack rendering to working-set assembly.
- Enforce final budget guard after injection.
- Dedupe against recent raw turns.
- Add debug trace for excluded items and budget trimming.

### Phase 6: Retrieval quality fixtures

- Add small deterministic fixtures for preference, profile fact, project rule, task state, decision, and entity relation.
- Measure precision-oriented behavior before adding embedding or LLM planner complexity.

## 13. Debug Output

Expected debug shape:

```json
{
  "retrieval": {
    "enabled": true,
    "intent": {
      "kind": "preference_lookup",
      "entities": [],
      "scopes": ["session", "user"]
    },
    "plan": {
      "paths": ["exact_record", "lexical_projection"],
      "limit_per_path": 8,
      "max_pack_items": 5
    },
    "candidate_count": 7,
    "included_count": 3,
    "omitted_count": 4,
    "trace": [
      {
        "source": "exact_record",
        "memory_id": "mem_...",
        "score": 0.91,
        "decision": "included",
        "reason": "type_and_term_match"
      }
    ]
  }
}
```

Debug requirements:

- show whether retrieval was skipped and why
- show which paths ran
- show candidate counts before and after dedupe/ranking
- show included item scores and exclusion reasons
- never require model-facing text to include internal IDs

## 14. Acceptance Criteria

v0.4 is complete when:

1. A stored preference/profile/project rule can be retrieved and injected on a later turn.
2. Retrieval can be disabled without changing baseline chat behavior.
3. Retrieval failure degrades to no pack and does not fail chat completion.
4. `RetrievalPack` is bounded by item count and character budget.
5. Retrieval debug explains trigger, paths, ranking, and omissions.
6. Query-dependent retrieval is not cached inside stable context frames.
7. Tests cover exact record, lexical projection, graph one-hop, and budget trimming paths.

## 15. Non-Negotiable Rules

1. Do not call retrieval a vector search wrapper.
2. Do not mutate canonical memory during retrieval.
3. Do not make dreaming a prerequisite for recall.
4. Do not inject raw database rows directly into model context.
5. Do not let low-confidence broad recall outrank exact canonical records.
6. Do not add new overlapping dataclass names for intent, plan, candidate, or pack.
7. Do not hide retrieval decisions; trace every included and excluded candidate.

