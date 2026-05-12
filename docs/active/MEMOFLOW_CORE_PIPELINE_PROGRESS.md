# Memoflow Core Pipeline Progress

Last updated: 2026-05-12
Status: active progress map against `MEMOFLOW_CORE_PIPELINE_ROADMAP.md`.

## Purpose

This document exists to prevent development from drifting into local details before the core pipeline is complete.

The controlling roadmap remains `docs/active/MEMOFLOW_CORE_PIPELINE_ROADMAP.md`. This file only maps current repository state to that roadmap and identifies the correct next implementation phase.

## Current Stage Summary

Memoflow has moved past pure scaffolding. The post-turn formation pipeline is now staged, durable, and connected to the app worker lifecycle.

The project is currently between:

```text
Phase C: Integration 2.0
Phase D: Storage Routing 2.0
```

The next mainline bottleneck is **Phase D: Storage Routing 2.0**, not more fine-grained Integration guardrails and not Retrieval/Lifecycle expansion.

## Phase Progress Matrix

| Roadmap phase | Repo state | Status | Evidence | Gap |
| --- | --- | --- | --- | --- |
| Phase A: Observation Layer | Observation extraction exists and persists artifacts. | Mostly done | `services/memory/formation/observations.py`, `memory_observations`, staged `observation_extraction` artifact | Observation quality can improve later through real cases, but it is no longer the main structural blocker. |
| Phase B: Formation 2.0 | Candidates are formed from observations and carry `memory_layer`, `storage_intent`, `evidence_policy`, `lifecycle_hint`. | Mostly done | `services/memory/formation/jobs.py`, `schemas.py`, formation scenario tests | Candidate strategy is still heuristic/LLM-hybrid, but mainline data shape exists. |
| Phase C: Integration 2.0 | Integration emits `ADD`, `NOOP`, `MERGE`, `UPDATE`, `SUPERSEDE`, `LINK`, `CONFLICT`, `NEEDS_REVIEW`; staged integration artifact exists. | Partial | `integration_planner.py`, `integration_llm.py`, `integration_schemas.py`, `memory_integration_planner_contract.py` | Semantic quality is not world-class yet, but further micro-guardrails risk overfitting before routing is solid. |
| Phase D: Storage Routing 2.0 | A `shape_planner` and store scaffolds exist. | Next bottleneck | `shape_planner.py`, `stores/*`, `MemoryWritePlan.canonical_store/projections` | Routing matrix is not yet the explicit source of truth for canonical vs projection writes; `state_kv`, `dag`, and `review_queue` still degrade rather than exist as clear route outcomes. |
| Phase E: Background Mainline Wiring | Formation is split into observation/candidate/integration/write/apply jobs; queue is persistent and app worker starts on lifecycle. | Mostly done | `jobs.py`, `worker.py`, `MemoryJobQueue`, `main.py` worker startup | Operational tuning can come later. Structurally this phase is connected. |
| Phase F: Retrieval Intent and Memory Package | Retrieval planner, repository, ranker, package, assembler, and runtime injection exist. | Scaffold/partial | `services/memory/retrieval/*`, `services/memory/runtime.py` | Should not be expanded next; retrieval quality depends on correct storage routing and source-of-truth semantics. |
| Phase G: Real-Case Trial and Harness Backfill | Scenario tests exist. | Not yet the main phase | `tests/fixtures/memory_*`, `memory_*_scenario_eval.py` | Roadmap says real-case trial follows core pipeline completion; do not expand harness around placeholder routing. |

## Recent Work Assessment

Recent commits correctly moved the project from scaffold toward mainline:

- Staged formation jobs and persisted artifacts made pipeline stages inspectable and rerunnable.
- App lifecycle worker startup made queued jobs actually run.
- Mutation apply idempotency made retries safer.
- Review/conflict actions now remain first-class instead of being disguised as `ADD`.
- Target-selection trace metadata improved auditability.

However, the last items are already close to Integration detail work. Continuing deeper there is lower leverage than implementing Phase D.

## Why Phase D Is Next

The roadmap defines storage routing as an architecture decision, not a candidate label.

Current code has the ingredients:

- `MemoryCandidateLite.memory_layer`
- `MemoryCandidateLite.storage_intent`
- `plan_storage_shape(...)`
- canonical stores and projection stores
- `MemoryWritePlan.canonical_store`
- `MemoryWritePlan.projections`
- SQLite tables for canonical records, graph edges, vector projections, review items

But the system still lacks a strong routing contract:

```text
candidate + integration operation + layer + storage intent + risk
-> explicit route
-> canonical write(s)
-> projection write(s)
-> unsupported/degraded route reason
```

Without this, downstream work will amplify ambiguity:

- Integration cannot know what kind of mutation it is planning against.
- Write planning cannot clearly distinguish source-of-truth writes from recall projections.
- Retrieval cannot know which stores are authoritative for each intent.
- Runtime memory packages may mix evidence, projection, and canonical memory without enough semantics.

## Correct Next Development Plan

### Step 1: Make routing an explicit contract

Create a routing module that owns the route matrix as data/code, instead of spreading route decisions across candidate defaults and `shape_planner` special cases.

Expected route output:

```text
canonical_store
projection_stores
source_of_truth_store
unsupported_routes
degrade_reasons
review_required
routing_reason
```

### Step 2: Refactor `shape_planner` behind the route matrix

Keep existing behavior where possible, but make `plan_storage_shape` delegate to the new route matrix. This preserves current tests while moving architecture forward.

### Step 3: Model unsupported stores honestly

The roadmap includes stores not fully implemented yet:

- `state_kv`
- `dag`
- `review_queue`

For now they should become explicit route outcomes with downgrade/review semantics, not invisible fallbacks.

### Step 4: Strengthen write planning around route semantics

Write plans should reflect:

- canonical source of truth
- projections only for retrieval/graph/vector surfaces
- review queue as review action, not silent canonical write
- vector never as source of truth

### Step 5: Add only minimal guardrail tests

Tests should verify the route contract for representative cases, not create a large harness:

- durable user preference
- project file rule
- task state
- decision event
- explicit dependency relation
- conflict/review
- vector-only intent downgrade
- unsupported DAG route downgrade

## Do Not Do Next

Do not spend the next cycle primarily on:

- More Integration target-selection heuristics.
- Retrieval package expansion.
- Lifecycle/dreaming/maintenance policies.
- UI/admin tooling.
- Large scenario harness expansion.
- Debug metadata beyond what Phase D needs.

## Immediate Next Commit Target

The next code commit should be:

```text
Add storage route matrix
```

Scope:

- New route contract/module under `services/memory/formation/`.
- `shape_planner.py` delegates to it.
- `MemoryWritePlan` keeps current fields but gains route trace only if needed.
- Existing memory tests continue to pass.
- Add a focused route matrix contract test.

Success means Phase D has begun as a mainline architecture implementation, not another local patch.
