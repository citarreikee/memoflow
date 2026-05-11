# Memoflow Memory v0.3 Storage Design

Last updated: 2026-05-05

## 1. Purpose

Memory v0.3 moves Memoflow from dry-run write plans to real durable memory storage.

This milestone should not simply replace JSONL mocks with database tables. The storage layer must preserve the architecture established in v0.2:

- evidence first
- typed canonical memories
- vector as projection, not truth
- graph as relation structure, not universal storage
- file memory as human-reviewable project knowledge
- memory lifecycle support for staging, validation, rewrite, forgetting, and re-indexing

Core rule:

```text
Raw evidence is the source of truth.
Canonical memory records are curated state.
Indexes and graph edges are projections.
```

## 2. Deep Dive Engineering Takeaways

The deep-dive material points to several storage principles that should directly shape v0.3.

### 2.1 Memory is a data evolution pipeline

Useful long-term memory is not written in one step.

```text
Raw Event -> Structured Candidate -> Canonical Memory -> Index Projection -> Consolidated Insight
```

Each stage needs its own representation, status, and failure boundary.

### 2.2 Each storage substrate is non-replaceable

- Log answers: what actually happened?
- KV answers: what stable state do we currently believe?
- Vector answers: what is semantically similar?
- Graph answers: why or how are things related?
- File memory answers: what should humans be able to inspect and edit?

No single database shape should pretend to solve all of these.

### 2.3 Retrieval is multi-path

Future read path should support:

```text
Query
  -> exact KV lookup
  -> vector recall
  -> graph traversal
  -> time/scope filtering
  -> merge + rerank + budget control
```

Therefore storage must maintain consistent identifiers and provenance across all projections.

### 2.4 Memory must evolve

The system needs lifecycle transitions:

```text
Observe -> Stage -> Consolidate -> Validate -> Rewrite / Forget -> Re-index -> Runtime Feedback
```

This means v0.3 must support statuses, versions, supersession, tombstones, and index rebuild markers from the beginning.

## 3. v0.3 Scope

v0.3 includes:

- SQLite-backed durable memory store.
- Persistent staged candidates and write plans.
- Canonical semantic memory records.
- Evidence links from canonical memories to episodes.
- Vector projection metadata table without requiring embeddings on day one.
- Relation graph edge table for explicit relations.
- File memory suggestion table, not silent file rewriting.
- Memory lifecycle statuses.
- Basic update/delete/supersede mechanics.
- Re-index queue markers for vector/graph/file projections.

v0.3 excludes:

- Production-grade vector ANN engine.
- External graph database.
- Full retrieval injection into `prepare_turn`.
- Automatic file patch application.
- Full AutoDream-style consolidation.
- Distributed workers or durable background queue infrastructure.

## 4. Recommended Storage Backend

Use SQLite first.

Reasons:

- The current repository already uses SQLite for sessions, messages, episodes, and checkpoints.
- v0.3 needs schema discipline more than scale.
- SQLite supports transactions, indexes, JSON text payloads, and simple migration.
- It keeps local development easy.

Do not introduce a vector DB or graph DB yet. Model those as durable tables and projection contracts first.

Future migration path:

```text
SQLite canonical store
  -> external vector index for embeddings
  -> external graph store if graph traversal becomes a bottleneck
```

## 5. Store Ownership Model

Every durable memory product must fit one of these roles.

| Role | Table family | Source of truth |
| --- | --- | --- |
| Evidence | existing `episodes` plus evidence links | yes for raw facts |
| Staging | `memory_candidates`, `memory_write_plans` | no |
| Canonical state | `memory_records` | yes for curated memory |
| Projection | `memory_vector_projections`, `memory_graph_edges`, `memory_file_suggestions` | no |
| Lifecycle | `memory_reindex_jobs`, versions, tombstones | operational truth |

## 6. Schema Overview

### 6.1 `memory_candidates`

Persistent version of `MemoryCandidateLite`.

Purpose:

- preserve extractor output
- allow debugging and replay
- decouple extraction from planning

Suggested columns:

```sql
candidate_id TEXT PRIMARY KEY,
session_id TEXT NOT NULL,
episode_id TEXT NOT NULL,
type TEXT NOT NULL,
scope TEXT NOT NULL,
action TEXT NOT NULL,
importance REAL NOT NULL,
stability TEXT NOT NULL,
text TEXT NOT NULL,
reason TEXT NOT NULL,
extractor_mode TEXT NOT NULL,
extractor_model TEXT,
status TEXT NOT NULL,
created_at TEXT NOT NULL,
payload_json TEXT NOT NULL
```

Statuses:

- `extracted`
- `invalid`
- `planned`
- `blocked`
- `applied`
- `superseded`

### 6.2 `memory_write_plans`

Persistent version of `MemoryWritePlan`.

Purpose:

- preserve deterministic planning decisions
- support review before applying
- support dry-run and replay

Suggested columns:

```sql
plan_id TEXT PRIMARY KEY,
candidate_id TEXT NOT NULL,
session_id TEXT NOT NULL,
action TEXT NOT NULL,
canonical_store TEXT,
scope TEXT NOT NULL,
confidence REAL NOT NULL,
status TEXT NOT NULL,
blocked_reasons_json TEXT NOT NULL,
projections_json TEXT NOT NULL,
evidence_episode_ids_json TEXT NOT NULL,
created_at TEXT NOT NULL,
applied_at TEXT,
payload_json TEXT NOT NULL
```

Statuses:

- `planned`
- `needs_review`
- `blocked`
- `noop`
- `applied`
- `failed`

### 6.3 `memory_records`

Canonical durable memory records.

Purpose:

- store current curated memory state
- support exact lookup and future retrieval planning
- hold lifecycle metadata

Suggested columns:

```sql
memory_id TEXT PRIMARY KEY,
scope TEXT NOT NULL,
namespace TEXT NOT NULL,
type TEXT NOT NULL,
key TEXT NOT NULL,
value TEXT NOT NULL,
status TEXT NOT NULL,
confidence REAL NOT NULL,
version INTEGER NOT NULL,
source_plan_id TEXT NOT NULL,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
valid_from TEXT,
valid_until TEXT,
supersedes_memory_id TEXT,
superseded_by_memory_id TEXT,
payload_json TEXT NOT NULL
```

Recommended unique index:

```sql
UNIQUE(scope, namespace, type, key, status)
```

Only one active record should exist for the same logical key unless the type explicitly supports multiple values.

Statuses:

- `active`
- `staged`
- `needs_review`
- `superseded`
- `suppressed`
- `archived`
- `deleted`

### 6.4 `memory_evidence_links`

Connect canonical memory to raw episodes.

Purpose:

- enforce evidence-backed memory
- support audit and contradiction review

Suggested columns:

```sql
link_id TEXT PRIMARY KEY,
memory_id TEXT NOT NULL,
episode_id TEXT NOT NULL,
plan_id TEXT NOT NULL,
evidence_role TEXT NOT NULL,
created_at TEXT NOT NULL
```

Evidence roles:

- `source`
- `supporting`
- `contradicting`
- `superseding`

### 6.5 `memory_vector_projections`

Projection table for future embedding retrieval.

Purpose:

- track what should be embedded
- keep vector records tied to canonical memory/evidence
- allow re-indexing without rewriting canonical state

Suggested columns:

```sql
projection_id TEXT PRIMARY KEY,
memory_id TEXT,
episode_id TEXT,
source_plan_id TEXT NOT NULL,
scope TEXT NOT NULL,
text TEXT NOT NULL,
embedding_model TEXT,
embedding_status TEXT NOT NULL,
embedding_ref TEXT,
source_of_truth INTEGER NOT NULL DEFAULT 0,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL
```

Embedding statuses:

- `pending`
- `embedded`
- `stale`
- `failed`
- `disabled`

Rule:

```text
source_of_truth must always be false.
```

### 6.6 `memory_graph_edges`

Projection table for explicit relations.

Purpose:

- represent dependencies, contradictions, supersession, derivation, and temporal links
- support future graph traversal

Suggested columns:

```sql
edge_id TEXT PRIMARY KEY,
source_memory_id TEXT,
target_memory_id TEXT,
source_episode_id TEXT,
target_episode_id TEXT,
relation_type TEXT NOT NULL,
scope TEXT NOT NULL,
confidence REAL NOT NULL,
status TEXT NOT NULL,
source_plan_id TEXT NOT NULL,
created_at TEXT NOT NULL,
valid_from TEXT,
valid_until TEXT,
payload_json TEXT NOT NULL
```

Allowed relation types:

- `depends_on`
- `supersedes`
- `contradicts`
- `derived_from`
- `caused_by`
- `part_of`
- `blocks`
- `valid_from`
- `valid_until`

Statuses:

- `active`
- `superseded`
- `suppressed`
- `deleted`

### 6.7 `memory_file_suggestions`

Reviewable project memory suggestions.

Purpose:

- avoid silent file mutation
- preserve suggested project handbook edits

Suggested columns:

```sql
suggestion_id TEXT PRIMARY KEY,
source_plan_id TEXT NOT NULL,
session_id TEXT NOT NULL,
workspace_dir TEXT,
target_path TEXT,
suggested_patch TEXT NOT NULL,
status TEXT NOT NULL,
auto_apply INTEGER NOT NULL DEFAULT 0,
created_at TEXT NOT NULL,
reviewed_at TEXT,
payload_json TEXT NOT NULL
```

Statuses:

- `pending_review`
- `accepted`
- `rejected`
- `applied`
- `superseded`

Rule:

```text
auto_apply must default to false.
```

### 6.8 `memory_reindex_jobs`

Operational queue for projection refresh.

Purpose:

- mark vector, graph, temporal, and KV shortcuts stale after memory mutation
- support future worker implementation

Suggested columns:

```sql
job_id TEXT PRIMARY KEY,
memory_id TEXT,
projection_id TEXT,
job_type TEXT NOT NULL,
status TEXT NOT NULL,
reason TEXT NOT NULL,
created_at TEXT NOT NULL,
updated_at TEXT NOT NULL,
payload_json TEXT NOT NULL
```

Job types:

- `embed_vector`
- `refresh_vector`
- `refresh_graph`
- `refresh_temporal_index`
- `refresh_kv_shortcut`
- `suppress_projection`

Statuses:

- `pending`
- `running`
- `done`
- `failed`
- `cancelled`

## 7. Applying Write Plans

v0.3 should introduce a `MemoryWriteApplier`.

Input:

- `MemoryWritePlan`
- existing records in same scope/namespace/type/key
- evidence episodes

Output:

- canonical memory record mutation
- evidence links
- projection rows
- re-index jobs

### 7.1 ADD

```text
ADD
  -> derive logical key
  -> detect duplicate active record
  -> create memory_record or mark needs_review
  -> create evidence links
  -> create projections
  -> enqueue reindex jobs
```

### 7.2 UPDATE

```text
UPDATE
  -> find target by scope/type/key similarity
  -> increment version
  -> update value/confidence/status
  -> preserve old value in payload or history table
  -> refresh projections
```

### 7.3 DELETE

```text
DELETE
  -> do not hard-delete by default
  -> mark record deleted or suppressed
  -> suppress projections
  -> preserve evidence trail
```

Hard delete should be reserved for privacy, legal, or explicit user removal requests.

### 7.4 NOOP

Persist candidate and plan for debug only. Do not create canonical records.

## 8. Logical Key Strategy

Canonical memory needs a stable key to support updates and dedupe.

Suggested initial key strategy:

| Type | Key shape |
| --- | --- |
| `preference` | normalized preference topic |
| `profile_fact` | normalized fact subject |
| `project_rule` | project rule category + short hash |
| `procedure` | procedure name or normalized title |
| `decision` | decision topic + short hash |
| `task_state` | task/session key |
| `entity_relation` | source + relation + target |
| `embedding_hint` | text hash |

v0.3 can begin with deterministic text normalization plus hash fallback. LLM-generated keys should not be required.

## 9. Namespace and Scope

Every durable memory should include both `scope` and `namespace`.

Suggested namespace rules:

- `session:<session_id>` for session-only state
- `user:<user_id>` when user identity is known
- `project:<workspace_hash>` for project memory
- `workspace:<workspace_hash>` for workspace-level procedural memory

If identity or workspace is unknown, downgrade to `session` scope.

## 10. Lifecycle Semantics

### 10.1 Stage first

Extractor output should always be persisted as candidate/plan before canonical mutation.

This preserves replayability and prevents premature insight formation.

### 10.2 Validate before promoting

Canonical records require:

- at least one evidence episode
- valid scope
- supported canonical store
- acceptable confidence
- safety policy pass

### 10.3 Rewrite instead of append forever

Memory records must support:

- merge
- split later
- supersede
- suppress
- archive
- delete

v0.3 only needs initial update/supersede/delete mechanics, but the schema must not block later consolidation.

### 10.4 Re-index after mutation

Any canonical record mutation should enqueue projection refresh jobs.

The initial implementation may leave jobs pending. The important part is that stale projections become visible.

## 11. Integration With v0.2 Code

Current v0.2 modules should evolve as follows:

| v0.2 module | v0.3 evolution |
| --- | --- |
| `formation/schemas.py` | remains API schema boundary |
| `formation/extractor.py` | persists candidates after extraction |
| `formation/mutation_planner.py` | persists write plans |
| `stores/write_plan_log.py` | replaced or supplemented by SQLite write-plan table |
| `stores/*MockStore` | replaced by SQLite-backed store implementations |
| `formation/jobs.py` | runs extract -> plan -> persist -> apply when enabled |

Recommended new modules:

```text
services/memory/storage/
  __init__.py
  schema.py
  sqlite_store.py
  keys.py
  applier.py
  reindex.py
  lifecycle.py
```

## 12. Migration Plan

### Phase 1: Schema and repository

- Add SQLite migrations or idempotent table creation.
- Add `MemoryStorage` repository with methods for candidates, plans, records, links, projections, edges, suggestions, and jobs.
- Keep JSONL dry-run path available behind config.

### Phase 2: Persist staging artifacts

- Persist `MemoryCandidateLite` into `memory_candidates`.
- Persist `MemoryWritePlan` into `memory_write_plans`.
- Do not apply to canonical records yet.

### Phase 3: Apply safe canonical writes

- Apply `planned` records for `semantic_kv` and `episode_log`.
- Generate evidence links.
- Mark file memory as suggestions only.
- Keep vector and graph as projection rows.

### Phase 4: Update/delete/supersede

- Add logical key matching.
- Support version increment.
- Support soft delete/suppress.
- Add supersession edge generation.

### Phase 5: Re-index jobs

- Enqueue vector/graph/file projection refresh jobs.
- Add debug output for pending re-index work.

## 13. Runtime Debug Requirements

`memory_debug.memory_storage` should answer:

- Were candidates persisted?
- Were write plans persisted?
- Which canonical records were created or updated?
- Which evidence episodes support each record?
- Which projections were created?
- Which file suggestions need review?
- Which re-index jobs are pending?
- Which writes were blocked and why?

Suggested shape:

```json
{
  "memory_storage": {
    "candidate_count": 2,
    "plan_count": 2,
    "canonical_writes": 1,
    "evidence_links": 1,
    "vector_projections": 1,
    "graph_edges": 0,
    "file_suggestions": 1,
    "reindex_jobs": 1,
    "blocked_count": 0
  }
}
```

## 14. Acceptance Criteria

v0.3 storage is complete when:

1. Candidates and write plans are persisted in SQLite.
2. Canonical memory records can be created with evidence links.
3. Vector projection rows are created but never treated as source of truth.
4. Relation graph rows are created only for explicit relation plans.
5. File memory writes produce reviewable suggestions only.
6. UPDATE can version an existing canonical record.
7. DELETE suppresses or tombstones records without losing evidence by default.
8. Supersession can link old and new memory records.
9. Re-index jobs are created after canonical mutations.
10. Runtime debug reports storage writes and blocked reasons.
11. Existing v0.1/v0.2 behavior works when real storage is disabled.

## 15. Non-Negotiable Rules

1. Do not promote memory without episode evidence.
2. Do not make vector projections authoritative.
3. Do not silently apply file memory suggestions.
4. Do not hard-delete by default.
5. Do not use graph for standalone facts.
6. Do not collapse candidate, plan, canonical record, and projection into one table.
7. Do not block chat completion on storage failure.
8. Do not introduce external vector or graph infrastructure before SQLite semantics are stable.

