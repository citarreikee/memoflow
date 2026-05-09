# Memoflow Memory Roadmap

Last updated: 2026-05-09
Purpose: this is the handoff roadmap for future Memoflow memory work. Start here after context compaction or in a new session.

## 1. Current Position

Memoflow is building a production-grade long-term memory framework for AI agents.

Current status is not a bare idea and not a finished memory system. It is a safe alpha architecture with the first closed-loop scaffolding in place.

Estimated progress toward the target of a world-class agent memory framework:

```text
Overall: 30% - 35%
Demo-level memory backend: roughly 60%
Production-grade long-term memory intelligence: still 65% - 70% remaining
```

The main project transition has already happened:

```text
From: can we bolt memory onto a chat backend?
To: can we make memory formation, integration, retrieval, and evolution trustworthy?
```

The next phase should prioritize quality, contracts, and semantic correctness over adding flashy new lifecycle features.

## 2. What Already Exists

### 2.1 Baseline Chat

Already present:

- API chat backend.
- Session and chat history management.
- Multi-provider model selection.
- Main conversation path that can work without memory enabled.

Approximate maturity: 80% for current local backend needs.

### 2.2 Runtime Center

Already present:

- `prepare_turn` and `finalize_turn` separation.
- Runtime policy for context pressure and checkpoint loading.
- Post-answer compaction instead of routine pre-answer compaction.
- Debug traces for runtime decisions.
- Memory failures intended to be non-blocking for chat.

Approximate maturity: 60%.

Main remaining risks:

- Emergency/pathological context behavior is less tested than normal post-turn behavior.
- Runtime debug and metrics need more product-level observability.
- More memory work still needs to move fully behind durable background jobs.

### 2.3 Short-Term Context Compaction

Already present:

- Checkpoint summaries for older context.
- Sidecar model compaction path.
- Rule-based fallback when sidecar compaction fails.
- Checkpoint audit and trace.
- Recent raw turns are preserved; compaction is not performed on every request.

Approximate maturity: 55%.

Main remaining risks:

- Checkpoint semantic quality is not yet evaluated with scenario-level golden cases.
- Current gates are mostly structural/heuristic, not semantic factuality checks.

### 2.4 Episode Evidence Layer

Already present:

- Raw conversation turns can be persisted as episodes.
- Derived memory can keep evidence links back to source episodes.
- This is the source-of-truth layer for future memory audits.

Approximate maturity: 65%.

Main remaining risks:

- Retention/export/replay tooling is not mature.
- Evidence coverage should become a strict invariant for all long-term writes.

### 2.5 Memory Formation Scaffold

Already present:

- Candidate extraction scaffold.
- Rule extractor fallback.
- Optional LLM extraction path.
- Natural-language plus JSON-fragment parsing tolerance.
- Candidate normalization.
- Candidate quality gate for empty text, duplicates, length caps, and max count.
- Write plan generation.

Approximate maturity: 30%.

Main remaining risks:

- The core question, what deserves long-term memory, is still weak.
- The schema is still light and does not fully encode layered memory taxonomy.
- LLM formation quality is not yet governed by strong scenario-level evaluation.

### 2.6 Integration Planning Scaffold

Already present:

- Separation between candidate extraction and candidate integration.
- Bounded neighborhood fetch before integration.
- Deterministic dry-run integration planner with ADD, MERGE, LINK, NOOP, NEEDS_REVIEW style actions.

Approximate maturity: 20%.

Main remaining risks:

- Integration planner is not yet a fully connected production stage in the background formation workflow.
- Existing memory matching is still lexical/scaffold-level.
- Conflict, update, supersession, and evolution semantics are not mature.

### 2.7 Durable Storage Scaffold

Already present:

- SQLite-backed storage tables for candidates, plans, canonical records, evidence links, vector projection metadata, graph edges, file suggestions, and reindex jobs.
- Storage is default-off and apply-plans is explicit.
- Projection rows are not treated as source of truth.

Approximate maturity: 40%.

Main remaining risks:

- No real embedding worker yet.
- Vector projection is metadata/lexical scaffold, not real vector retrieval.
- Graph/DAG semantics are shallow and should not be overused.
- Schema migration/versioning is not product-grade.

### 2.8 Retrieval Scaffold

Already present:

- Retrieval feature flag.
- Conservative trigger plan.
- Exact/lexical/one-hop graph retrieval scaffolds.
- Rerank and budget control basics.
- Runtime injection block for retrieved memory.

Approximate maturity: 25%.

Main remaining risks:

- Retrieval intent is rule-based and brittle.
- No true vector index path yet.
- No strong semantic reranker.
- Recall usage policy is not mature enough for default-on behavior.

### 2.9 Background Job Infrastructure

Already present:

- Durable SQLite job queue.
- Job statuses: pending, running, succeeded, failed, dead.
- Worker dispatcher.
- Worker CLI for once, until-idle, and loop modes.
- Runtime can enqueue background formation jobs rather than running them synchronously.

Approximate maturity: 50%.

Main remaining risks:

- Not all future memory maintenance tasks are represented as durable job types yet.
- Observability, retries, idempotency keys, and stale-lock recovery need strengthening.

### 2.10 Harness and Evaluation Infrastructure

Already present:

- Smoke tests for runtime, formation, storage, retrieval, job queue, worker, and worker CLI.
- Candidate/write-plan golden contract.
- Harness runner that emits structured reports.

Approximate maturity: 25%.

Main remaining risks:

- Existing golden tests are mostly low-level contracts, not scenario-level semantic evaluations.
- Compaction and formation need task-level golden scenarios.
- LLM critic/evaluator should be introduced only as non-blocking quality signal after deterministic baselines exist.

## 3. Canonical Product Goal

Memoflow should become a memory framework that can do five things reliably:

1. Preserve evidence.
2. Decide what is worth remembering.
3. Store each memory in the right shape.
4. Retrieve the right memory at the right time.
5. Evolve memory safely as facts, tasks, and preferences change.

The product goal is not just to save conversation summaries. It is to build an auditable memory operating system for agents.

## 4. Core Architecture Principles

### 4.1 Raw Evidence First

Raw episodes are the source of truth. All derived memories must be traceable back to evidence. If a memory cannot explain where it came from, it should not be trusted.

### 4.2 Main Chat Must Stay Thin

The main chat path should answer the user. Heavy memory work should not synchronously block it.

Allowed in the main path:

- Load already-prepared context.
- Use bounded retrieval packs when enabled.
- Persist the completed turn.
- Enqueue background work.

Not allowed as routine main-path work:

- Long LLM formation jobs.
- Expensive integration planning.
- Embedding generation.
- Lifecycle consolidation.
- Large graph maintenance.

### 4.3 Post-Turn Maintenance

Compaction and memory formation should generally happen after the answer, using idle time before the next user request.

### 4.4 Deterministic Safety, LLM Semantics

Use deterministic code for hard boundaries:

- Schema validation.
- Evidence requirements.
- Sensitive data blocks.
- Scope safety.
- Budget caps.
- Store routing constraints.
- Feature flags.

Use LLMs for semantic judgments:

- What deserves memory.
- Whether a candidate duplicates or updates an existing memory.
- Whether a conflict exists.
- Whether a checkpoint preserves task meaning.
- Whether a query needs long-term memory.
- Which retrieved memories are useful.

### 4.5 Plans Before Writes

No model should directly write canonical memory. The correct flow is:

```text
context/episode -> candidate -> quality gate -> neighborhood snapshot -> integration plan -> deterministic validator -> write plan -> storage apply
```

### 4.6 Storage Is Multi-Shape

Do not force every memory into a single atom type.

Expected storage shapes:

- Raw log for evidence and replay.
- State/KV for current stable facts and preferences.
- Event log for timeline and decisions.
- Vector projection for semantic recall.
- Graph edges for explicit relations.
- DAG only for dependency/order/provenance structures where acyclicity has meaning.
- File memory suggestions for project rules that should become human-reviewable persistent project context.

### 4.7 Retrieval Is Not Neighborhood Fetch

Candidate neighborhood fetch is a write-time operation. It finds nearby existing memories so the integration planner can avoid duplicates and detect conflicts.

Answer-time retrieval is a read-time operation. It selects memories that help answer the current user request under a prompt budget.

These two paths may share storage, but they have different triggers, scoring, output formats, and latency budgets.

## 5. Main Development Lines

### Line A: Runtime Finalization

Goal: make runtime the stable memory operating center.

Responsibilities:

- Prepare working context.
- Load checkpoints, file memory, and retrieval packs.
- Persist completed turns.
- Enqueue background work.
- Track debug traces.
- Keep memory failures non-blocking.

Needed work:

- Strengthen pathological context tests.
- Add structured runtime timing and failure counters.
- Tighten idempotency for finalize-turn side effects.
- Ensure each background workflow has a durable job type.
- Make debug output stable enough for harness assertions.

Success criteria:

- Chat works with memory disabled.
- Chat works when background workers are down.
- Runtime can explain exactly what memory material was loaded, skipped, saved, or enqueued.
- No heavy LLM memory task runs synchronously on the main path by default.

### Line B: Formation Quality

Goal: decide what should enter long-term memory.

Responsibilities:

- Read raw episodes and compacted context.
- Identify candidate memories.
- Classify memory layer and type.
- Filter non-memory and unsafe memory.
- Produce evidence-backed candidates.

Needed work:

- Upgrade scenario-level formation golden evaluation.
- Define layered memory taxonomy in schema.
- Add model-assisted formation as the primary semantic path.
- Keep rule extraction as fallback and smoke-test path.
- Add deterministic hard gates for sensitive data, transient commands, missing evidence, and low utility.

Target memory layers:

- Raw episode: original evidence, always append-only.
- Event layer: decisions, milestones, tool outcomes, important user actions.
- State layer: current task state, open loops, active constraints.
- Semantic layer: stable user preferences, project rules, durable facts.
- Insight layer: distilled patterns or learned strategies, only after repeated evidence.
- Relation layer: dependencies, references, supersession, ownership, causality.
- File/project layer: human-reviewable project instructions.

Success criteria:

- Explicit decisions are not dropped.
- Temporary instructions are not promoted to permanent preference.
- Sensitive data is not written into long-term memory.
- Every candidate has evidence.
- Candidate count remains bounded and explainable.
- Golden scenarios can detect regressions.

### Line C: Integration and Storage Routing

Goal: decide how a candidate changes long-term memory.

Responsibilities:

- Fetch bounded neighborhood snapshots.
- Detect duplicate, update, contradiction, supersession, link, or no-op.
- Route memory to the right storage shape.
- Produce deterministic write plans.
- Apply plans only after validation.

Needed work:

- Connect integration planner into background formation jobs.
- Upgrade neighborhood fetch beyond lexical-only matching.
- Add LLM-assisted integration planner over bounded snapshots.
- Add update/delete/supersede contract tests.
- Add schema versioning/migration for storage.
- Keep storage apply behind feature flags until contracts stabilize.

Storage routing guide:

| Memory kind | Preferred shape | Why |
| --- | --- | --- |
| Raw conversation | Episode log | Evidence and replay source. |
| Explicit decision | Event log + vector projection | Timeline plus semantic recall. |
| Current task status | State/KV + event evidence | Fast current-state load. |
| User preference | Semantic/KV record + evidence | Stable personalization. |
| Project rule | File suggestion or project semantic record | Needs reviewable persistence. |
| Tool outcome | Event log + artifact reference | Auditable work history. |
| Dependency/order | DAG edge if acyclic semantics matter | Supports prerequisites and provenance. |
| Loose relation | Graph edge | Supports associative traversal. |
| Searchable long text | Vector projection | Recall surface, not source of truth. |
| Conflicting fact | Review queue / supersession plan | Avoid silent overwrite. |

Success criteria:

- Same preference phrased slightly differently does not create endless duplicate records.
- Updates and supersessions are explicit and evidence-backed.
- Graph/DAG are used only when their semantics are justified.
- Storage writes are deterministic after plan validation.

### Line D: Retrieval and Working Set Use

Goal: retrieve the right memory and use it safely.

Responsibilities:

- Decide whether the current request needs long-term memory.
- Build retrieval plan.
- Search exact records, state, event logs, vector projections, and graph relations.
- Rerank and budget selected memories.
- Inject memory into working context in a bounded, explainable way.

Needed work:

- Add optional LLM retrieval planner for non-trivial queries.
- Implement true embedding/reindex worker before claiming vector retrieval.
- Add hybrid retrieval: exact, lexical, vector, graph, recency, scope.
- Add semantic reranker and contradiction handling.
- Add retrieval eval scenarios.
- Add memory pack format stability.

Success criteria:

- Trivial queries do not trigger expensive retrieval.
- Project/task continuation queries recall the right prior decisions.
- User preference queries retrieve stable preferences, not transient instructions.
- Retrieval packs are compact, cited, and budget-aware.
- Retrieval failure never blocks chat.

### Line E: Lifecycle and Evolution

Goal: make memory improve over time instead of accumulating noise.

This line should wait until formation, integration, and retrieval quality are stable.

Responsibilities:

- Consolidate repeated evidence.
- Merge duplicate records.
- Decay stale memory.
- Archive low-value memory.
- Detect contradictions.
- Suggest review items.
- Reindex after rewrites.

Needed work later:

- Lifecycle job queue types.
- Memory health metrics.
- Consolidation prompts.
- Conflict review contracts.
- Decay/archive policies.
- Human review surfaces for risky changes.

Success criteria:

- Lifecycle reduces noise rather than amplifying it.
- No destructive rewrite happens without evidence and trace.
- Old memories can be superseded without losing history.

### Line F: Evaluation and Observability

Goal: stop relying on subjective impressions.

Responsibilities:

- Define golden scenarios.
- Run harness reports.
- Track metrics and regressions.
- Explain failures.
- Separate infrastructure health from semantic quality.

Needed work:

- Scenario-level formation eval.
- Scenario-level compaction eval.
- Update/supersession contract suite.
- Retrieval eval suite.
- Non-blocking LLM critic for semantic quality.
- Report trend tracking.

Success criteria:

- Every important memory behavior has a regression case.
- Failed evals explain what was missing, hallucinated, duplicated, or unsafe.
- Harness can run quick deterministic checks and optional model-backed checks separately.

## 6. Phased Roadmap

### Phase A: Alpha Skeleton Stabilization

Status: mostly in progress / near completion.

Scope:

- Runtime prepare/finalize split.
- Post-turn compaction.
- Episode persistence.
- Formation scaffold.
- Storage scaffold.
- Retrieval scaffold.
- Durable job queue and worker.
- Harness runner.

Current completion estimate: 70% of this phase.

Remaining work:

- Finish connecting all non-chat memory work to durable jobs.
- Harden worker idempotency and stale running-job handling.
- Add pathological runtime tests.
- Keep feature flags default-safe.

Exit criteria:

- Quick harness is green.
- Main chat path survives memory failures.
- Worker can process durable queued jobs from CLI.
- Runtime debug is stable and useful.

### Phase B: Trustworthy Memory Formation

Scope:

- Define what is worth remembering.
- Build layered memory taxonomy into schema and prompts.
- Upgrade LLM formation under deterministic gates.
- Add scenario-level formation eval.
- Add compaction semantic eval.

Current completion estimate: 20% of this phase.

Implementation direction:

1. Build formation scenarios from raw episodes, not prewritten candidates.
2. Score expected memory types, layers, must-include facts, must-not-store facts, and evidence coverage.
3. Upgrade extractor prompts and parser only after eval exists.
4. Keep rule extractor as fallback and deterministic test path.
5. Add LLM critic later as a non-blocking evaluator.

Exit criteria:

- Formation eval catches over-memory and under-memory failures.
- Explicit decisions and open loops are reliably captured.
- Temporary or sensitive content is reliably blocked.
- Candidate schema can represent event, state, semantic, insight, relation, and file/project memory intent.

### Phase C: Trustworthy Integration and Retrieval

Scope:

- Make candidate integration real.
- Prevent duplicate memories.
- Handle updates, conflicts, and supersession.
- Implement true hybrid retrieval.
- Use retrieved memory safely in working set.

Current completion estimate: 10% - 15% of this phase.

Implementation direction:

1. Connect neighborhood fetch and integration planner into background formation jobs.
2. Add integration eval for duplicate/update/conflict/supersede cases.
3. Add storage router contracts for log/KV/vector/graph/DAG/file suggestions.
4. Implement embedding worker and reindex flow.
5. Add LLM retrieval planner and reranker behind flags.
6. Add retrieval scenario eval.

Exit criteria:

- Same fact with slightly different wording routes to merge/update, not duplicate ADD.
- Contradictions become review or supersession plans.
- Retrieval can recover relevant project decisions and user preferences.
- Retrieval output is cited, bounded, and does not flood the prompt.

### Phase D: Product-Grade Evolution

Scope:

- Lifecycle management.
- Long-term consolidation.
- Decay/archive.
- Review workflows.
- Observability and operations.

Current completion estimate: 5%.

Implementation direction:

1. Only start after Phase B and C have stable evals.
2. Add lifecycle job types to the durable queue.
3. Build memory health metrics and review queues.
4. Add consolidation over evidence clusters.
5. Add safe archive/supersession flows.
6. Add migration and export tools.

Exit criteria:

- Memory improves with use instead of accumulating clutter.
- Old memory can be retired without losing evidence.
- Operators can inspect why a memory exists, changed, or was suppressed.
- Long-running agents remain coherent across many sessions.

## 7. Near-Term Priority

The immediate next priority is not lifecycle/dreaming.

The immediate priority is:

```text
Make memory quality measurable, then improve formation and integration under that measurement.
```

Recommended near-term order:

1. Keep current uncommitted stabilization/background work intact and commit it after review.
2. Add scenario-level formation eval.
3. Add scenario-level compaction eval.
4. Connect integration planner into queued formation jobs.
5. Add update/merge/supersession contracts.
6. Only then upgrade LLM-assisted integration and retrieval.

## 8. What Not To Do Yet

Do not start full lifecycle/dreaming yet.

Reason: formation and integration quality are not stable enough. Lifecycle over unreliable memories will amplify bad memory.

Do not claim vector retrieval is real yet.

Reason: vector projection metadata exists, but embedding generation and vector index retrieval are not production paths yet.

Do not make memory default-on broadly yet.

Reason: semantic quality and integration correctness need stronger evals.

Do not route all memories into graph/DAG.

Reason: graph and DAG are specialized structures. Use DAG only for dependency/order/provenance where acyclicity matters.

Do not let LLM write directly to canonical memory.

Reason: LLMs should propose candidates/plans. Deterministic validators and storage appliers should perform writes.

## 9. Handoff Checklist For A New Session

When resuming this project in a new or compacted session:

1. Read this file first.
2. Read `MEMOFLOW_MEMORY_HARNESS_REVIEW.md` for the latest maturity audit.
3. Read `MEMOFLOW_MEMORY_HARNESS_RUNNER.md` for current validation commands.
4. Check `git status --short` before editing.
5. Run the quick harness before and after meaningful memory changes.
6. Do not revert unrelated dirty work.
7. Preserve the rule: main chat path stays thin; memory intelligence runs in background jobs.

Useful commands:

```powershell
python tools\memory_harness_runner.py --quick
python tests\memory_worker_cli_smoke.py
python -m compileall services\memory tests tools
git diff --check
```

## 10. Current Mental Model

Use this analogy when reasoning about the system:

- Episodes are the original meeting recording.
- Checkpoints are the secretary's running meeting brief.
- Formation is the secretary deciding which notes might matter later.
- Integration is the archivist comparing new notes with existing files.
- Storage routing is choosing the right cabinet: log, state, preference, graph, DAG, vector projection, or file suggestion.
- Retrieval is the assistant pulling the right folder before answering.
- Lifecycle is the archive manager cleaning and consolidating old files.
- Harness is the quality inspector making sure the secretary and archivist do not lose, invent, or misfile information.

The system is successful when future agents can resume long-running work with evidence-backed continuity, without flooding the prompt and without accumulating false memory.
