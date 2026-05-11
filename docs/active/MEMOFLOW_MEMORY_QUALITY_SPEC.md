# Memoflow Memory Quality Spec

Date: 2026-05-06
Status: v0.1-v0.4 stabilization quality baseline

## Why This Exists

A top-tier long-term memory framework is not a database that stores chat text. It is a decision system that decides which past information should influence future model behavior, at what scope, with what confidence, and through which retrieval path.

The first quality goal is conservative correctness:

- Remember fewer things rather than pollute durable memory.
- Preserve raw evidence before deriving summaries or structured facts.
- Separate memory formation, storage routing, retrieval, and prompt injection.
- Make every durable write explainable and reversible.
- Keep the main chat path non-blocking even when memory subsystems fail.

## Memory Quality Principles

1. Evidence first.
Every durable memory must point back to one or more episode ids. A memory without evidence is not a memory; it is an unsupported claim.

2. Scope is a safety boundary.
A session fact should not become a user preference. A user preference should not become a project rule. A project rule should not leak across unrelated workspaces.

3. Memory type controls storage shape.
The system must not default everything to vector, graph, or DAG. Each memory class has a preferred canonical store and optional projections.

4. Projections are not source of truth.
Vector rows, graph edges, and file suggestions are retrieval or review aids. Canonical memory remains in raw episodes, semantic records, task state records, or explicit file suggestions.

5. Temporary state is not stable identity.
A transient task status can be useful, but it must not become a permanent user or project truth unless later confirmed.

6. Graph is for explicit relations.
Graph/DAG storage is valuable for dependency, supersession, contradiction, derivation, causality, part-of, and blocking relations. It is harmful when used as a generic bucket for every memory.

7. Human-reviewable surfaces must not be silently rewritten.
Project file memory can be suggested, but not auto-applied unless a later explicit review/apply mechanism exists.

8. Retrieval must be budget-aware.
Remembered content should earn its place in the prompt. If it is low-confidence, redundant, stale, or too large, it should be omitted with a traceable reason.

## Memory Layers

| Layer | Purpose | Typical lifetime | Source of truth | Examples |
| --- | --- | --- | --- | --- |
| Raw episode log | Preserve exactly what happened | Long | append-only episode store | One user/assistant turn, tool result summary |
| Event layer | Durable event/fact observed in a turn | Medium/long | episode-backed record | User chose DeepSeek, repo initialized, smoke test passed |
| State layer | Current mutable working state | Short/medium | semantic/state record with supersession | Current milestone, open blocker, active TODO |
| Semantic layer | Stable preference/rule/fact/decision | Long | semantic KV or reviewed file memory | User prefers concise answers, project forbids auto file rewrites |
| Relation layer | Explicit edges among memories/events | Medium/long | graph edge table | A depends_on B, design X supersedes design Y |
| Projection layer | Retrieval accelerators | Rebuildable | vector/lexical/graph projections | Embedding rows, lexical shortcuts, graph one-hop hints |
| File suggestion layer | Human-reviewable project memory | Long after review | file suggestion, later reviewed project file | Proposed MEMORY.md rule update |

## Formation Pipeline Contract

Formation should eventually be model-driven, but the contract is model-independent:

1. Trigger policy receives an episode and decides whether extraction is worth running.
2. Extractor emits bounded `MemoryCandidateLite` items.
3. Evaluator rejects, noops, marks for review, or allows planning.
4. Shape planner maps memory type and scope to canonical store plus projections.
5. Mutation planner emits `MemoryWritePlan` with evidence ids, confidence, status, and blocked reasons.
6. Storage applier may persist candidates/plans and apply plans only when storage flags allow it.

No step should call retrieval or mutate prompt context directly.

## Candidate Contract

Current operative schema:

```python
MemoryCandidateLite(
    text=str,
    type=str,
    scope=str,
    action="ADD|UPDATE|DELETE|NOOP",
    importance=float,
    reason=str,
    stability="temporary|evolving|stable|unknown",
    candidate_id=str | None,
)
```

Required quality checks:

- `text` must be specific enough to be useful later.
- `type` must describe why this is memory, not where it is stored.
- `scope` must be the narrowest safe scope.
- `importance` must justify durable processing.
- `reason` must explain future utility.
- `stability` must reflect whether the fact is temporary, evolving, stable, or unknown.

## Write Plan Contract

Every write plan must answer:

- What action is being proposed?
- What canonical store owns the source of truth?
- What projections can accelerate retrieval?
- What scope and namespace should contain it?
- Which episode ids support it?
- Why is it allowed, blocked, noop, or review-only?

A plan should be blocked if:

- evidence is missing;
- text is empty;
- it contains sensitive markers;
- scope is invalid;
- memory type requires a relation but no explicit relation exists;
- canonical store cannot be selected;
- temporary content is being promoted to stable semantic memory.

## Storage Routing Baseline

| Memory type | Canonical store | Projections | Reason |
| --- | --- | --- | --- |
| `preference` | `semantic_kv` | `episode_log`, `vector_projection` | Stable user behavior should be exact-retrievable and semantically searchable. |
| `profile_fact` | `semantic_kv` | `episode_log` | Identity/profile facts need exact retrieval and strict scope. |
| `project_rule` | `file_memory` for project/workspace, downgraded to `semantic_kv` for user/session | `episode_log`, `vector_projection` | Project rules should become reviewable project knowledge, not silent file edits. |
| `procedure` | `file_memory` for project/workspace, downgraded to `semantic_kv` for user/session | `episode_log`, `vector_projection` | Procedures are often project-facing and should be reviewable. |
| `decision` | `semantic_kv` | `episode_log`, `vector_projection`, optional `relation_graph` | Decisions need exact recall; relation projection only when explicit relation exists. |
| `task_state` | `semantic_kv` | `episode_log`, optional `relation_graph` | Mutable state should be easy to supersede. |
| `entity_relation` | `relation_graph` only with explicit relation | `episode_log` | Graph/DAG is only useful when an edge has a clear semantic predicate. |
| `episodic_event` | `episode_log` | none | Raw event remains evidence; do not over-promote by default. |
| `embedding_hint` | `vector_projection` | `episode_log` | Rebuildable retrieval hint, not source of truth. |
| `non_memory` | none | none | Noise should not write. |

## Golden Case Categories

The baseline test suite should include these categories:

- Stable user preference: should become user-scoped semantic memory with vector projection.
- Stable project rule: should become project-scoped file suggestion plus vector projection, not silent file write.
- Architecture decision: should become project-scoped semantic memory with vector projection.
- Mutable task state: should become project/session state memory and remain supersedable.
- Explicit dependency relation: should become graph memory only when relation text is explicit.
- Vague relation-like text: should be blocked from graph canonical storage.
- Low-importance chatter: should become noop/non-memory.
- Sensitive content: should be blocked.
- Temporary non-state content: should be blocked rather than promoted.
- Missing evidence: should be blocked.

## Quality Metrics

The first practical metrics are contract metrics rather than model-score metrics:

- Formation precision on golden cases: expected type/scope/status/store match rate.
- Pollution rate: count of non-memory/sensitive/unsupported cases that reach planned write.
- Evidence coverage: percentage of non-noop plans with evidence ids.
- Routing accuracy: expected canonical store/projection match rate.
- Retrieval injection budget compliance: pack item count and char budget never exceeded.
- Main-path safety: disabled/failing memory subsystems never break `/api/chat`.

## Near-Term Implementation Rule

Until the golden cases pass consistently, do not add lifecycle/evolution/dreaming features. A lifecycle manager over weak formation quality will only preserve and amplify noise.
