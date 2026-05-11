# Memoflow Memory v0.2 Development Spec

Last updated: 2026-05-04

## 1. Purpose

Memory v0.2 introduces memory formation and storage planning on top of the v0.1 runtime.

v0.2 is not a memory database milestone. It does not need to persist long-term memories into production storage. Its job is to define how Memoflow decides what is worth remembering, what shape that memory should take, and which storage substrate should eventually own it.

Core rule:

- LLM extraction stays lightweight.
- Storage planning is deterministic policy code.
- Every planned memory write must be explainable and traceable to episode evidence.
- DAG / graph memory is optional and specialized, not the universal memory model.

## 2. Mental Model

Think of v0.2 as a memory compiler.

- Episodes are the source files.
- Lightweight candidates are semantic hints produced by an LLM.
- The storage planner is the compiler backend.
- Mock stores are dry-run targets.
- Debug output is the compiler report.

The LLM should answer small semantic questions:

- Is this worth remembering?
- What kind of memory is it?
- Should it be added, updated, deleted, or ignored?
- Why might it matter later?

The runtime should answer engineering questions:

- Which canonical store owns this memory?
- Which projections should be generated?
- Which scope is allowed?
- Which evidence episode supports it?
- Which write plan should be emitted?

## 3. v0.2 Scope

v0.2 includes:

- Background memory extraction after completed turns.
- Lightweight candidate schema.
- Worth-storing policy.
- Storage-shape planning across log, KV, vector, file, and graph substrates.
- Dry-run or JSONL-backed mock stores.
- Debug reporting for extraction and planning decisions.
- Safety gates for scope, privacy, and low-confidence memories.

v0.2 excludes:

- Production database migrations.
- Full vector retrieval injection.
- Full graph retrieval.
- Automatic project-file rewriting without explicit policy approval.
- Contradiction reconciliation beyond planned `UPDATE` / `DELETE` intent.
- Running extraction synchronously in the hot answer path.

## 4. Design Position

### 4.1 Candidate first, plan second

The extractor must not output a full database write plan.

Instead, v0.2 uses two layers:

1. `MemoryCandidateLite`: small LLM-generated semantic candidate.
2. `MemoryWritePlan`: deterministic system-generated storage plan.

This keeps LLM output fast and stable while still designing future storage shape.

### 4.2 Substrate plurality

Memoflow must not force all memory into DAGs or vectors.

Each memory type has a canonical owner and optional projections:

- Episode log stores raw evidence.
- Semantic KV stores stable structured facts, preferences, and state.
- Vector projection supports fuzzy recall but is not source of truth.
- Relation graph stores dependencies, supersession, contradiction, and temporal edges.
- File memory stores human-editable project rules and procedures.
- Runtime cache stores hot working state only.

### 4.3 Canonical owner rule

Every durable memory must have one canonical store.

Other substrates are projections. A vector hit or graph edge should point back to a canonical memory record and episode evidence instead of becoming an unsupported fact.

## 5. Storage Substrate Responsibilities

| Substrate | Role | Canonical for | Not for |
| --- | --- | --- | --- |
| `episode_log` | Append-only evidence | raw turns, completed episodes, source provenance | semantic truth, ranking |
| `semantic_kv` | Structured durable records | preferences, profile facts, task state, stable decisions | fuzzy recall, multi-hop reasoning |
| `vector_projection` | Recall index | searchable text projections | source-of-truth facts |
| `relation_graph` | Relationship and temporal edges | dependencies, supersession, contradiction, derivation | simple standalone facts |
| `file_memory` | Human-editable handbook | project rules, procedures, architecture notes | hidden user facts, volatile chat details |
| `runtime_cache` | Hot working state | active turn/session temporary state | durable memory |

## 6. Memory Taxonomy

The extractor should classify candidates into a small set of stable types:

| Type | Meaning | Default canonical store | Typical projections |
| --- | --- | --- | --- |
| `preference` | User communication or behavior preference | `semantic_kv` | `vector_projection`, `episode_log` |
| `profile_fact` | User identity, environment, or long-lived fact | `semantic_kv` | `episode_log` |
| `project_rule` | Durable project convention or constraint | `file_memory` | `vector_projection`, `episode_log` |
| `procedure` | Reusable workflow or operating instruction | `file_memory` or `semantic_kv` | `vector_projection`, `episode_log` |
| `decision` | Design or product decision | `semantic_kv` | `vector_projection`, `relation_graph`, `episode_log` |
| `task_state` | Current task progress or open item | `semantic_kv` | `episode_log`, optional `relation_graph` |
| `entity_relation` | Relationship between entities | `relation_graph` | `episode_log`, optional `vector_projection` |
| `episodic_event` | One-time event worth retaining as evidence only | `episode_log` | none |
| `embedding_hint` | Low-structure text useful only for fuzzy recall | `vector_projection` | `episode_log` |
| `non_memory` | Not worth storing | none | none |

## 7. Lightweight Candidate Schema

`MemoryCandidateLite` is the only structure the LLM should produce in v0.2.

```python
@dataclass
class MemoryCandidateLite:
    text: str
    type: str
    scope: str
    action: str
    importance: float
    reason: str
    stability: str = "unknown"
```

Field rules:

- `text`: concise memory statement, not a transcript excerpt.
- `type`: one of the taxonomy values in section 6.
- `scope`: coarse scope such as `session`, `user`, `project`, or `workspace`.
- `action`: `ADD`, `UPDATE`, `DELETE`, or `NOOP`.
- `importance`: value from `0.0` to `1.0`.
- `reason`: short explanation for future usefulness.
- `stability`: `temporary`, `evolving`, `stable`, or `unknown`.

The extractor should usually emit zero to three candidates per extraction job.

## 8. Write Plan Schema

`MemoryWritePlan` is generated by policy code, not directly by the LLM.

```python
@dataclass
class MemoryWritePlan:
    plan_id: str
    candidate_id: str
    action: str
    canonical_store: str | None
    projections: list[str]
    scope: str
    evidence_episode_ids: list[str]
    confidence: float
    status: str
    blocked_reasons: list[str]
```

Field rules:

- `canonical_store` is selected from the storage substrate table.
- `projections` are derived from memory type and policy.
- `confidence` is derived from importance, stability, extraction confidence, and evidence quality.
- `status` is `planned`, `blocked`, `noop`, or `needs_review`.
- `blocked_reasons` explains privacy, scope, low-confidence, or unsupported-store blocks.

v0.2 may persist write plans as JSONL for debugging. These plans are not production storage records.

## 9. Worth-Storing Policy

The planner should accept a candidate only when it passes deterministic gates.

### 9.1 Positive signals

- The information is likely to affect future answers.
- The information is stable or intentionally evolving.
- The information is scoped clearly.
- The information can be traced to one or more episodes.
- The information is not already represented by a higher-quality memory.
- The information is user-stated, project-stated, or strongly implied by repeated behavior.

### 9.2 Negative signals

- The candidate is generic conversation filler.
- The candidate is a one-off detail with no future use.
- The candidate contains sensitive personal data without an explicit need.
- The candidate is ambiguous or unsupported by evidence.
- The candidate duplicates recent raw context only.
- The candidate would require graph reasoning but lacks entities or relation type.

### 9.3 Suggested thresholds

```text
importance < 0.45:       NOOP
importance 0.45 - 0.65:  needs_review or episode_log only
importance 0.65 - 0.80:  planned if scope and stability pass
importance >= 0.80:      planned unless blocked by safety policy
```

These are Memoflow policy thresholds, not model guarantees.

## 10. Storage Shape Planning Rules

### 10.1 Default mapping

```text
preference      -> semantic_kv + episode_log + optional vector_projection
profile_fact    -> semantic_kv + episode_log
project_rule    -> file_memory + episode_log + vector_projection
procedure       -> file_memory/semantic_kv + episode_log + vector_projection
decision        -> semantic_kv + episode_log + vector_projection + optional relation_graph
task_state      -> semantic_kv + episode_log + optional relation_graph
entity_relation -> relation_graph + episode_log
episodic_event  -> episode_log only
embedding_hint  -> vector_projection + episode_log
non_memory      -> none
```

### 10.2 When to use graph

Use `relation_graph` only when a candidate contains an explicit relation such as:

- `depends_on`
- `supersedes`
- `contradicts`
- `derived_from`
- `caused_by`
- `part_of`
- `blocks`
- `valid_from` / `valid_until`

Do not use graph for standalone preferences, profile facts, simple notes, or raw summaries.

### 10.3 When to use vector

Use `vector_projection` when fuzzy semantic recall is useful.

Vector records must include references to canonical memory records or episode IDs. They must not be treated as authoritative facts.

### 10.4 When to use file memory

Use `file_memory` for project-level rules, procedures, and architecture decisions that humans should be able to inspect and edit.

v0.2 should generate planned file-memory patches or suggestions, not silently rewrite project memory files.

## 11. Trigger Policy

Memory extraction should run after `finalize_turn`, not inside `prepare_turn`.

Suggested triggers:

- Every N completed user turns for active sessions.
- When a turn contains explicit memory language such as remember, prefer, decision, rule, do not, from now on.
- When a project or architecture discussion contains high-signal terms such as design, convention, policy, milestone, dependency, or constraint.
- When runtime debug flags request extraction.

Suggested non-triggers:

- Very short acknowledgements.
- Tool-only turns without user-level semantic content.
- Turns already covered by a recent extraction job.
- High-load conditions where background jobs should be skipped.

## 12. Proposed Module Layout

Add these modules incrementally under `services/memory/`:

```text
services/memory/
  formation/
    __init__.py
    schemas.py
    extractor.py
    evaluator.py
    shape_planner.py
    mutation_planner.py
    prompts.py
  stores/
    __init__.py
    episode_log.py
    semantic_kv.py
    vector_projection.py
    relation_graph.py
    file_projection.py
    write_plan_log.py
```

Responsibilities:

- `extractor.py`: calls the extraction model and parses `MemoryCandidateLite`.
- `evaluator.py`: applies worth-storing gates.
- `shape_planner.py`: maps candidate type to canonical store and projections.
- `mutation_planner.py`: resolves `ADD`, `UPDATE`, `DELETE`, and `NOOP` into write plans.
- `write_plan_log.py`: writes dry-run plans to JSONL for inspection.
- Store modules define interfaces and mock implementations, not production databases.

## 13. Extraction Prompt Contract

The extraction prompt should be short and strict.

Prompt requirements:

- Return only JSON.
- Emit at most three candidates.
- Prefer `NOOP` when unsure.
- Do not include sensitive personal data unless the user explicitly asks it to be remembered or it is necessary for task continuity.
- Do not infer hidden preferences from one ambiguous turn.
- Keep `reason` under one sentence.

The model should not decide concrete database tables, graph edges, embedding metadata, TTL, or internal IDs.

## 14. Runtime Integration

`services/memory/runtime.py` should remain the orchestrator.

Integration points:

- `finalize_turn` emits or schedules a memory formation job after episode persistence.
- Formation jobs read episode evidence from `transcript_store.py` or `episode_log.py`.
- Formation jobs write `MemoryCandidateLite` and `MemoryWritePlan` debug records.
- `prepare_turn` does not depend on v0.2 write plans for correctness.
- Retrieval planner may inspect mock semantic/vector records only behind a feature flag.

Failure behavior:

- Extraction failure must not affect the user response.
- Invalid candidate JSON becomes a debug event, not a runtime exception.
- Unsupported memory types become `blocked` plans.
- Store mock write failures become debug warnings.

## 15. Debug Output

v0.2 debug output should answer:

- Was extraction triggered?
- Which episode IDs were considered?
- Which candidates were emitted?
- Which candidates were rejected?
- Which write plans were generated?
- Which store would own each memory?
- Which safety or scope rules blocked a write?

Example debug shape:

```json
{
  "memory_formation": {
    "triggered": true,
    "episode_ids": ["ep_001"],
    "candidate_count": 2,
    "planned_count": 1,
    "blocked_count": 1,
    "plans": [
      {
        "type": "project_rule",
        "action": "ADD",
        "canonical_store": "file_memory",
        "projections": ["episode_log", "vector_projection"],
        "status": "needs_review"
      }
    ]
  }
}
```

## 16. Acceptance Criteria

v0.2 is complete when:

1. Completed turns can schedule background memory formation without blocking chat responses.
2. The extractor emits only `MemoryCandidateLite` records with a small bounded schema.
3. Deterministic policy converts candidates into `MemoryWritePlan` records.
4. Write plans identify canonical store, projections, scope, action, evidence, and blocked reasons.
5. DAG / graph planning occurs only for explicit relation candidates.
6. Vector planning is projection-only and never source-of-truth.
7. File-memory planning produces reviewable suggestions, not silent project file rewrites.
8. Debug output explains trigger, extraction, rejection, and planning decisions.
9. Extraction failures are isolated from `prepare_turn` and provider calls.
10. Tests cover candidate parsing, store-shape mapping, graph gating, and low-importance rejection.

## 17. Implementation Order

Recommended sequence:

1. Add schemas for `MemoryCandidateLite` and `MemoryWritePlan`.
2. Add deterministic evaluator and shape planner tests.
3. Add mock `write_plan_log.py` JSONL writer.
4. Add extractor prompt and parser behind a feature flag.
5. Hook formation scheduling into `finalize_turn`.
6. Add debug output to runtime events.
7. Add optional dry-run CLI or test helper for replaying episodes.

## 18. Non-Negotiable Rules

1. Do not call extraction in the hot answer path.
2. Do not ask the LLM to output full storage plans.
3. Do not treat vectors as memory truth.
4. Do not treat graph as the default memory format.
5. Do not write user-sensitive facts to file memory.
6. Do not create durable memories without episode evidence.
7. Do not silently rewrite human-editable memory files in v0.2.
8. Do not fail chat completion because memory formation failed.

