# Memoflow Core Pipeline Roadmap

Last updated: 2026-05-10
Status: controlling roadmap for the next development phase.

## 1. Strategic Reset

Memoflow is not currently blocked by lack of concepts. It is blocked by an incomplete core memory pipeline.

The previous roadmap correctly emphasized safety, contracts, evaluation, and harnesses, but the project has now reached a point where those supporting systems can become premature if they harden scaffold-level implementations too early.

The next phase must follow this principle:

```text
First build the world-class core memory pipeline.
Then run real cases through it.
Then turn discovered failures into harness cases, feature changes, simplifications, and strategy corrections.
```

Evaluation remains important, but it is not the main product at this stage. It is a guardrail around core-pipeline construction.

## 2. Current Diagnosis

The system already has useful scaffolding:

- API chat backend and multi-provider chat path.
- Runtime split between `prepare_turn` and `finalize_turn`.
- Episode persistence and evidence links.
- Context compaction checkpoint path.
- Background memory job queue.
- Candidate extraction and write-plan scaffold.
- Layered candidate fields: `memory_layer`, `storage_intent`, `evidence_policy`, `lifecycle_hint`.
- SQLite-backed storage scaffold.
- Retrieval scaffold and runtime injection slot.
- Quick harness and scenario-level tests.

But many modules are still strategy placeholders:

- Formation still jumps too directly from episode to candidate.
- Integration planning is not yet a strong semantic memory-evolution engine.
- Storage routing has intent fields but not a fully realized routing matrix.
- Retrieval is not yet intent-driven and multi-store aware.
- Runtime consumes memory results but does not yet orchestrate a complete memory package lifecycle.
- Harnesses mostly prove scaffold stability, not world-class memory intelligence.

Therefore the next goal is not to add peripheral safety systems. The next goal is to replace placeholder strategies with a complete, advanced mainline.

## 3. Target System Shape

The final Memoflow architecture should behave like an AI memory kernel.

The core pipeline is:

```text
chat turn
-> episode evidence
-> observation extraction
-> memory candidate formation
-> candidate framing and risk annotation
-> neighborhood snapshot
-> integration decision
-> storage routing
-> canonical/projection write plan
-> background apply
-> retrieval intent planning
-> multi-store recall
-> memory package assembly
-> runtime injection
-> post-turn maintenance
```

Each stage owns a distinct question:

| Stage | Core question | Output |
| --- | --- | --- |
| Episode | What exactly happened? | Immutable evidence record |
| Observation | What should the memory system notice? | Structured observations |
| Formation | What might be worth remembering? | Memory candidates |
| Framing | How valuable/risky/stable is it? | Candidate annotations |
| Neighborhood | What existing memory is nearby? | Bounded snapshot |
| Integration | Is this new, duplicate, update, conflict, or relation? | Integration operation |
| Routing | What storage shape matches its future use? | Storage route |
| Write Planning | What canonical/projection writes are safe? | Write plan |
| Apply | What can be committed now? | Durable records/projections |
| Retrieval Planning | What memory would help this turn? | Retrieval intent |
| Recall | Which records should be loaded? | Candidate memories |
| Packaging | How should memory be shown to the main model? | Memory package |
| Runtime | What enters the model context? | Provider messages |
| Maintenance | What can be processed after the answer? | Background jobs |

## 4. Development Philosophy

### 4.1 Core pipeline first

Do not spend the next phase primarily on:

- Large harness expansion.
- Lifecycle/dreaming.
- UI/admin tooling.
- Extensive observability polish.
- Fine-grained debug contracts around placeholder algorithms.
- Stable frame cache optimization.

These are valuable later. They are not the next bottleneck.

### 4.2 Aggressive theory, replaceable engineering

Use advanced theory to design the ideal mainline, but implement it as replaceable modules:

- Prompts can be upgraded.
- Stores can be swapped.
- Routing rules can be replaced.
- LLM decisions can be audited or downgraded.
- Retrieval methods can move from lexical scaffold to embeddings/graph traversal without changing the mainline contract.

### 4.3 Minimal guardrail testing during buildout

During core-pipeline construction, tests should only guard against:

- Chat path breakage.
- Schema breakage.
- Job queue breakage.
- Unsafe writes.
- Severe regressions in known scenarios.

Do not overfit a large harness to placeholder behavior.

### 4.4 Real-case feedback after pipeline completion

After the mainline exists end-to-end, use real cases to find failures:

- Long Feishu project conversations.
- Multi-session coding tasks.
- User preference drift.
- Architecture decision reversals.
- Project state handoff.
- Conflicting instructions.
- Similar memories with slightly different wording.
- Retrieval pollution cases.
- Memory over-insertion cases.

Only then should failures become golden cases and harness items.

## 5. Phase A: Observation Layer

### Goal

Insert a structured observation layer between raw episodes and memory candidates.

Current issue:

```text
episode -> candidate
```

This is too abrupt. It forces the extractor to decide both what happened and what deserves long-term memory in one step.

Target flow:

```text
episode -> observations -> candidates
```

### Observation types

Initial observation taxonomy:

| Observation type | Meaning |
| --- | --- |
| `user_preference_signal` | The user expressed a durable or possible preference. |
| `project_rule_signal` | The user stated a project-level rule or constraint. |
| `decision_signal` | A decision was made, revised, accepted, or rejected. |
| `task_state_signal` | Active task state changed. |
| `open_loop_signal` | A question, TODO, blocker, or unresolved issue appeared. |
| `relation_signal` | A dependency, conflict, cause, supersession, or derivation appeared. |
| `artifact_signal` | A file, document, commit, API, model, or external artifact became important. |
| `procedure_signal` | A reusable process or workflow was established. |
| `insight_signal` | A reusable lesson or architectural insight emerged. |
| `non_memory_signal` | The episode was noticed but should not produce long-term memory. |

### Observation fields

```python
{
  "observation_id": str,
  "episode_id": str,
  "type": str,
  "text": str,
  "scope_hint": "session|user|project|workspace",
  "evidence_message_refs": list[str],
  "confidence": float,
  "reason": str,
  "negative": bool,
}
```

### Implementation plan

1. Add observation schema.
2. Add LLM observation extractor prompt.
3. Add conservative rule fallback.
4. Persist observations as staging artifacts if storage is enabled.
5. Let formation consume observations, not only raw episodes.

### Acceptance bar

Observation extraction should separate:

```text
what happened
```

from:

```text
what should become durable memory
```

It should not directly write memory records.

## 6. Phase B: Formation 2.0

### Goal

Upgrade candidate formation from simple candidate extraction into observation-driven memory proposal.

### Input

```text
observations + episode metadata + memory policy
```

### Output

```python
{
  "candidate_id": str,
  "source_observation_ids": list[str],
  "text": str,
  "type": str,
  "scope": str,
  "action": "ADD|UPDATE|DELETE|NOOP",
  "importance": float,
  "stability": "temporary|evolving|stable|unknown",
  "memory_layer": "raw|event|state|semantic|insight|relation|file|non_memory",
  "storage_intent": "episode_log|state_kv|semantic_kv|vector_projection|relation_graph|dag|file_memory|review_queue|none",
  "evidence_policy": "required|multi_evidence_preferred|review_required|none",
  "lifecycle_hint": "normal|volatile|reinforce|supersedes|archive_after_task|review_before_apply",
  "risk": "low|medium|high",
  "reason": str,
}
```

### Strategy

Formation should not decide final writes. It should propose durable memory candidates with enough structure for downstream integration.

Formation should be LLM-driven for semantic judgment and rule-bounded for safety.

LLM responsibilities:

- Interpret observations.
- Distinguish temporary instruction from durable preference.
- Separate project state from project rule.
- Identify relation candidates.
- Identify reusable insights.
- Propose layer and storage intent.

Rule responsibilities:

- Clamp enum fields.
- Enforce max candidate count.
- Drop empty candidates.
- Block obvious sensitive data.
- Require evidence references.
- Normalize shape for downstream modules.

### Implementation plan

1. Extend candidate schema with `source_observation_ids` and `risk`.
2. Build `observations_to_candidates` path.
3. Keep episode-to-candidate fallback only as degraded path.
4. Make current rule extractor produce observations first where practical.
5. Keep parser tolerant of natural language plus JSON fragments.

## 7. Phase C: Integration 2.0

### Goal

Turn integration from a shallow planner into the semantic editor of long-term memory.

Integration owns this question:

```text
How does this candidate relate to what the system already remembers?
```

### Input

```text
candidate + bounded neighborhood snapshot + storage policy
```

Neighborhood snapshot should include:

- Exact or normalized text matches.
- Same scope/type records.
- Same entity or artifact records.
- Recent active state records.
- Related graph edges.
- Conflicting or superseded records.

This is not full user-query retrieval. It is a small write-time memory snapshot used for safe integration.

### Output operations

| Operation | Meaning |
| --- | --- |
| `ADD` | New memory. |
| `NOOP` | Not worth writing or already covered. |
| `MERGE` | Combine with existing equivalent memory. |
| `UPDATE` | Modify current state/fact without invalidating the old record entirely. |
| `SUPERSEDE` | New memory replaces older memory. |
| `LINK` | Add relation edge without changing canonical records. |
| `CONFLICT` | Candidate conflicts with existing memory. |
| `NEEDS_REVIEW` | High-value but unsafe or ambiguous. |

### Strategy

Use LLM for semantic comparison and operation selection. Use deterministic rules for safety gates and unsupported operations.

Integration prompt should answer:

- Is the candidate already represented?
- Does it update an existing record?
- Does it contradict a record?
- Does it supersede a decision or preference?
- Should it create a graph/DAG relation?
- Is evidence sufficient?
- Should it be deferred to review?

### Implementation plan

1. Expand integration schema to include `CONFLICT` and richer references.
2. Replace lexical-only matching with bounded hybrid neighborhood builder.
3. Add LLM integration planner as primary path.
4. Keep deterministic fallback for obvious ADD/NOOP/MERGE cases.
5. Ensure integration planner still does not directly mutate canonical stores.

## 8. Phase D: Storage Routing 2.0

### Goal

Make storage routing a real architecture decision, not just a field attached to candidates.

### Storage model

| Store | Best for | Not for |
| --- | --- | --- |
| `episode_log` | Immutable raw evidence and event history. | Direct prompt injection as durable fact. |
| `state_kv` | Current task/session/project state. | Historical decisions and relations. |
| `semantic_kv` | Stable facts, preferences, constraints. | Raw logs and dependency graphs. |
| `file_memory` | Human-readable project rules/procedures. | Ephemeral session preferences. |
| `relation_graph` | Explicit entity relations and conflicts. | Vague semantic similarity. |
| `dag` | Ordered dependency/evolution/causal chains. | General facts or all memories. |
| `vector_projection` | Recall surface and fuzzy matching. | Source of truth. |
| `review_queue` | Valuable but unsafe/ambiguous candidates. | Silent production writes. |

### Routing principle

Route by future use, not by label alone.

Examples:

```text
User durable preference -> semantic_kv canonical + vector projection + evidence.
Project coding rule -> file_memory canonical/suggestion + semantic projection + evidence.
Task state -> state_kv canonical + episode evidence.
Decision event -> episode_log canonical + semantic/vector projection.
Decision that changes a roadmap -> semantic_kv or file_memory update plus episode evidence.
Explicit dependency -> relation_graph canonical + episode evidence.
Decision evolution chain -> DAG projection or canonical depending maturity.
Uncertain contradiction -> review_queue.
```

### Implementation plan

1. Define routing matrix in code, not only docs.
2. Distinguish canonical store from projection stores.
3. Treat vector as projection only.
4. Treat graph/DAG as specialized structures, not universal stores.
5. Add unsupported-route handling that degrades honestly.

## 9. Phase E: Background Mainline Wiring

### Goal

Move the full memory formation/integration/write pipeline behind durable background jobs.

Main chat should not synchronously run heavyweight memory work.

### Desired post-turn flow

```text
finalize_turn
-> persist episode
-> enqueue compaction job when post-turn policy says old raw turns should be compressed
-> enqueue observation job
-> enqueue candidate formation job
-> enqueue integration/routing job
-> enqueue safe apply job
-> enqueue projection/index jobs
```

### Job requirements

- Durable SQLite queue.
- Idempotent job handlers.
- Retry with bounded attempts.
- Stale running recovery.
- Traceable job outputs.
- Non-blocking chat failure behavior.
- Post-turn compaction must be queued, not awaited in `finalize_turn`; sidecar compaction can be slow and must not block the next turn's session state.

### Implementation plan

1. Extend current formation job runner into a staged pipeline.
2. Store intermediate artifacts or debug blobs for observations and candidates.
3. Ensure each stage can be rerun safely.
4. Add `--until-idle` smoke path for the complete pipeline.

## 10. Phase F: Retrieval Intent and Memory Package

### Goal

Upgrade retrieval from similarity recall into intent-driven memory loading.

### Retrieval planning question

```text
What kind of memory would help answer this user turn without polluting context?
```

### Retrieval intents

| Intent | Store preference |
| --- | --- |
| `active_state` | state_kv, recent episode summaries |
| `user_preferences` | semantic_kv |
| `project_rules` | file_memory, semantic_kv |
| `historical_decisions` | episode_log, semantic_kv, DAG |
| `dependencies` | relation_graph, DAG |
| `similar_experience` | vector_projection, episode_log |
| `artifact_context` | file_memory, semantic_kv, relation_graph |
| `conflict_check` | semantic_kv, relation_graph, review_queue |

### Memory package shape

Runtime should receive a package, not loose records:

```python
{
  "active_state": list[dict],
  "relevant_rules": list[dict],
  "user_preferences": list[dict],
  "project_decisions": list[dict],
  "open_loops": list[dict],
  "related_artifacts": list[dict],
  "relations": list[dict],
  "conflicts_or_warnings": list[dict],
  "evidence_refs": list[dict],
  "budget": dict,
}
```

### Implementation plan

1. Add retrieval intent planner.
2. Add multi-store retrieval adapters behind current scaffold.
3. Add package assembler and budget trimming.
4. Inject package into runtime as a compact, clearly labeled memory block.
5. Keep retrieval failure non-blocking and conservative.

## 11. Phase G: Real-Case Trial and Harness Backfill

### Goal

After the core pipeline exists, run hard real cases and convert failures into targeted tests and improvements.

### Case sources

- Existing Feishu conversation history.
- Long Memoflow design sessions.
- Multi-turn coding tasks.
- User corrections and reversals.
- Similar preferences with different wording.
- Conflicting roadmap decisions.
- Memory extraction false positives.
- Retrieval pollution incidents.

### What to measure then

- Did the system notice the right observations?
- Did it propose the right candidates?
- Did it avoid non-memory chatter?
- Did it merge duplicates?
- Did it update/supersede changed decisions?
- Did it choose the right storage form?
- Did retrieval load useful memory only when needed?
- Did injected memory improve the answer?
- Did any background failure affect chat?

### Harness policy

Only add a harness case after observing a real or high-likelihood failure.

Harness should capture failures, not freeze early assumptions.

## 12. Deferred Work

Do not prioritize these until the core pipeline exists and real-case trials reveal specific needs:

- Memory dreaming / lifecycle consolidation.
- Large-scale graph optimization.
- Stable frame cache / incremental context compilation beyond minimal slots.
- Admin UI.
- Complex retention/export tooling.
- External vector DB or graph DB migration.
- Heavy observability dashboards.
- Broad benchmark suite.

These are later multipliers. They are not the next foundation.

## 13. Immediate Next Development Order

The next implementation sequence should be:

```text
1. Observation Layer
2. Formation 2.0 over observations
3. Integration 2.0 semantic planner
4. Storage Routing 2.0 matrix
5. Background staged pipeline wiring
6. Retrieval intent planner and memory package skeleton
7. Real-case trial runner
8. Harness backfill from observed failures
```

Do not skip directly to retrieval or lifecycle. If observations and formation are poor, every downstream module will amplify bad memory.

## 14. Definition of Progress

Progress in this roadmap means:

```text
A new stage exists in the mainline.
The stage uses a strategy close to the target architecture.
The stage has clear input/output data.
The stage is connected to upstream and downstream modules.
The stage can fail without breaking chat.
The stage is ready to be stressed by real cases.
```

Progress does not mean:

```text
More concepts in docs.
More tests around scaffold behavior.
More debug fields for placeholder algorithms.
More storage types without routing logic.
More retrieval paths without memory package quality.
```

## 15. North Star

Memoflow should remember like a world-class project partner:

```text
It watches what happens.
It extracts what matters.
It understands how new information changes old memory.
It stores each memory in the shape that matches future use.
It recalls only what helps the current task.
It explains where memory came from.
It keeps the main conversation fast and safe.
```

The immediate mission is to build that mainline first.
