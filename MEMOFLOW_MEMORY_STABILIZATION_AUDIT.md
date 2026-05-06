# Memoflow Memory Stabilization Audit

Date: 2026-05-06
Scope: v0.1 runtime, v0.2 formation, v0.3 storage, v0.4 retrieval. This audit intentionally does not advance v0.5 lifecycle/evolution work.

## Executive Summary

The repository currently has a minimal v0.1-v0.4 memory loop: post-turn episode persistence, post-turn checkpoint compaction, dry-run/planned memory formation, SQLite-backed durable memory storage, and retrieval injection into the next prompt. The loop is intentionally guarded by feature flags, default-off for long-term memory behavior, and non-blocking around sidecar/formation/retrieval failures.

The stabilization priority is not to add more memory intelligence. It is to harden the interfaces and acceptance boundaries that make the current loop safe to iterate on:

- P0: main chat must remain usable when memory features are disabled, empty, failing, or over budget.
- P0: memory formation/storage/retrieval must not run silently in production unless explicitly enabled.
- P1: data contracts across candidate -> write plan -> storage -> retrieval -> prompt injection must be consistent and test-covered.
- P1: debug traces must explain why memory was skipped, retrieved, omitted, blocked, or scheduled.

## Status Legend

- done: implemented and covered by at least one smoke/contract path.
- partial: implemented in a simplified or incomplete way that can support v0.x iteration.
- stub: code shape exists but behavior is mostly placeholder/mock.
- missing: not implemented.
- deferred: intentionally postponed beyond v0.4 stabilization.
- design_mismatch: implementation works but does not match the latest design intent closely enough.

## v0.1 Runtime Audit

| Spec item | Current implementation | Status | Risk | Next action |
| --- | --- | --- | --- | --- |
| Episode persistence before checkpoint save | `finalize_turn` builds and persists an episode before policy/checkpoint work. | done | Low. | Keep regression test coverage. |
| Short chats do not compact | `decide_post_turn_policy` requires pressure/turn thresholds; smoke covers long-task path. | done | Low. | Add explicit no-compaction regression if failures appear. |
| Post-turn compaction, not pre-answer compaction | Normal compaction is in `finalize_turn`; `prepare_turn` only has emergency fallback if assembled context still cannot fit. | done | Low. | Keep this boundary stable. |
| Emergency compaction only if assembled context exceeds budget | `prepare_turn` sets `emergency_compaction` only after working-set assembly remains over budget. | done | Medium. | Add focused test later with pathological context. |
| Checkpoint does not duplicate covered raw turns | `assemble_working_set` drops turns covered by checkpoint count. | partial | Medium: coverage assumes turn count equals episode coverage order. | Add stronger checkpoint/episode mapping tests. |
| Sidecar compaction failure never blocks answer | `finalize_turn` catches checkpoint build exceptions and records failure event. | done | Low. | Keep smoke test with fallback/sidecar failure. |
| `memory_debug` explains policy/budget/sources/events | `_build_debug` and `_merge_finalize_debug` include policy, budget, source, retrieval, audit and event detail blocks. | partial | Medium: version naming says `runtime_version=0.2` while retrieval v0.4 exists. | Split version fields or rename to `memory_runtime_version` later. |
| Model-specific context policy | `services.model_context` resolves provider/model context windows and thresholds; smoke covers DeepSeek/Kimi/Ollama/custom fallback. | done | Medium: values depend on vendor docs and should stay sourced. | Keep source labels and avoid unsourced exact thresholds. |

## v0.2 Formation Audit

| Spec item | Current implementation | Status | Risk | Next action |
| --- | --- | --- | --- | --- |
| Formation happens after completed turns | `finalize_turn` calls `_run_memory_formation` after episode persistence. | done | Low. | Keep. |
| Formation disabled by default | `MEMORY_FORMATION_ENABLED=false` in config examples and runtime skip path. | done | Low. | Add stabilization smoke. |
| Background/non-blocking formation mode | `MemoryFormationJobRunner.schedule` uses `asyncio.create_task`; runtime returns scheduled debug. | partial | Medium: background task exceptions are not surfaced except task object state. | Later add background task error sink/logging. |
| LLM/rule extractor emits bounded `MemoryCandidateLite` | extractor limits candidates and normalizes fields. | partial | Medium: current schema is still lite and does not fully encode state/event/insight layers. | Keep as v0.2 bootstrap; plan richer taxonomy after stabilization. |
| Deterministic mutation/write planning | `mutation_planner` builds `MemoryWritePlan` from candidates and shape planner. | partial | Medium: conflict/update semantics are basic. | Add contract tests around blocked/needs_review plans. |
| Graph planning only for explicit relation candidates | `shape_planner.has_explicit_graph_relation` gates relation graph shape. | partial | Medium: keyword-based relation detection is crude. | Keep conservative; later move to model-assisted classifier. |
| File planning produces reviewable suggestions only | file memory is suggestion-backed; no silent file rewrite. | done | Low. | Keep storage smoke coverage. |
| Extraction failures isolated from provider calls | `_run_memory_formation` catches exceptions and returns debug. | done | Low. | Add stabilization smoke with failing runner. |

## v0.3 Storage Audit

| Spec item | Current implementation | Status | Risk | Next action |
| --- | --- | --- | --- | --- |
| SQLite durable store for candidates/plans/records/evidence/projections/edges/suggestions/jobs | `MemorySQLiteStore` creates and writes the tables. | done | Low for local v0.x. | Keep schema smoke. |
| Storage disabled by default | `MEMORY_STORAGE_ENABLED=false`; formation can dry-run without SQLite apply. | done | Low. | Add default-off smoke. |
| Apply plans only with explicit flag | `MEMORY_STORAGE_APPLY_PLANS=false` controls applier execution. | done | Low. | Add contract smoke. |
| Bad/blocked plans do not silently write canonical records | `MemoryWriteApplier` blocks non-planned statuses, missing evidence, and non-file `needs_review`. | done | Low. | Add stabilization smoke. |
| Vector is projection, not source of truth | vector table rows use `source_of_truth=0`; canonical records remain separate when applicable. | done | Low. | Keep smoke. |
| Graph edges are explicit relation/provenance structures | graph inserts happen for explicit relations and supersession. | partial | Medium: no real DAG traversal semantics yet. | Keep one-hop retrieval only; avoid overusing DAG. |
| File memory is reviewable suggestion | file suggestions table uses pending review and auto_apply=0. | done | Low. | Keep. |

## v0.4 Retrieval Audit

| Spec item | Current implementation | Status | Risk | Next action |
| --- | --- | --- | --- | --- |
| Retrieval disabled by default | `MEMORY_RETRIEVAL_ENABLED=false`; pipeline skips if retrieval or storage disabled. | done | Low. | Add stabilization smoke that baseline messages do not change. |
| Triggered retrieval plan | `triggers.py` classifies query intent using conservative lexical rules. | partial | Medium: lexical rules are a bootstrap, not final intent recognition. | Later replace/augment with model-driven trigger planning. |
| Multi-path retrieval | repository supports exact record, lexical projection, graph one-hop. | partial | Medium: no vector embedding search yet; projection search is lexical. | Keep path names honest in debug. |
| Merge/rerank/budget | `ranker.py` dedupes, scores, caps item count/chars and records omissions. | done | Low. | Add over-budget smoke. |
| Runtime injection into working set | `prepare_turn` renders retrieval block and `working_set` injects it after file memory. | done | Low. | Keep retrieval smoke. |
| Retrieval failure non-blocking | pipeline catches exceptions and returns empty failed trace. | done | Low. | Add stabilization smoke with failing store. |
| Scope/namespace isolation | storage keying uses derived namespace; retrieval plan uses session key namespace. | partial | Medium: namespace derivation must match write/read paths for all scopes. | Add focused isolation test before enabling by default. |

## Cross-Version Contract Gaps

| Gap | Current status | Stabilization decision |
| --- | --- | --- |
| Rich memory taxonomy: raw log, event layer, state layer, semantic/insight layer, relation graph, file projection | Design discussed, but `MemoryCandidateLite` is still the operative v0.2 schema. | Mark partial; do not add v0.5 lifecycle until taxonomy is finalized. |
| Incremental context compilation / stable frame cache | Documented in `MEMOFLOW_INCREMENTAL_CONTEXT_COMPILATION.md`; not implemented as a cache layer. | deferred. |
| Long-term memory lifecycle/evolution/dreaming | Not implemented. | deferred until v0.1-v0.4 contracts stabilize. |
| Real embedding vector retrieval | Vector projection metadata exists; embeddings are pending jobs. | deferred/partial. |
| Model-driven extraction and trigger planning as production default | LLM extractor exists but default is rule and online sidecar is configurable. | partial; keep default-off. |

## Stabilization Test Plan

The new stabilization smoke should lock the following invariants:

- Formation disabled still persists raw episode and returns a skipped debug block.
- Formation enabled with an internal failure does not raise through `finalize_turn`.
- Retrieval disabled does not inject a retrieval system message.
- Retrieval enabled with empty/failing storage returns an empty pack and does not block `prepare_turn`.
- Retrieval pack obeys char/item budget and records omitted candidates.
- Storage applier blocks bad plans without writing canonical records.

## Recommended Next Work After This Pass

- Fix any P0/P1 failures exposed by stabilization smoke.
- Normalize version/debug naming so v0.1 runtime, v0.2 formation, v0.3 storage, and v0.4 retrieval are visible separately.
- Add a schema decision document for the layered memory taxonomy before expanding lifecycle management.
- Only after the above should v0.5 lifecycle/evolution work resume.
