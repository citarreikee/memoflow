# Memoflow Docs Index

This folder keeps project design documents out of the repository root while preserving a clear handoff path for future sessions.

## Active Docs

Read these when resuming active memory-system development:

- `active/MEMOFLOW_CORE_PIPELINE_ROADMAP.md`: controlling roadmap for current core-pipeline work.
- `active/MEMOFLOW_MEMORY_ROADMAP.md`: broader memory roadmap and phase overview.
- `active/MEMOFLOW_MEMORY_HARNESS_REVIEW.md`: latest maturity audit of the harness and current implementation gaps.
- `active/MEMOFLOW_MEMORY_HARNESS_RUNNER.md`: validation commands and harness usage notes.
- `active/MEMOFLOW_MEMORY_QUALITY_SPEC.md`: quality contracts for memory formation, integration, storage, and retrieval.
- `active/MEMOFLOW_INCREMENTAL_CONTEXT_COMPILATION.md`: planned runtime/context optimization that is not fully implemented yet.
- `active/memory-deep-dive.md`: long-form research and design discussion used as a reference source.

## Archive Docs

Read these only when historical context is needed:

- `archive/MEMOFLOW_MEMORY_ARCHITECTURE_V2.md`: earlier architecture design superseded by the core-pipeline roadmap.
- `archive/MEMOFLOW_MEMORY_MASTER_PLAN.md`: previous master plan, useful for context but not the current control document.
- `archive/MEMOFLOW_MEMORY_RUNTIME_DEV_SPEC.md`: runtime development spec from an earlier implementation stage.
- `archive/MEMOFLOW_MEMORY_STABILIZATION_AUDIT.md`: stabilization audit from a previous phase.
- `archive/MEMOFLOW_MEMORY_V0_2_DEV_SPEC.md`: historical v0.2 design notes.
- `archive/MEMOFLOW_MEMORY_V0_3_STORAGE_DESIGN.md`: historical v0.3 storage design notes.
- `archive/MEMOFLOW_MEMORY_V0_4_RETRIEVAL_DESIGN.md`: historical v0.4 retrieval design notes.
- `archive/RUNTIME_DESIGN_DRAFT.md`: older runtime draft kept for reference.

## Rule Of Thumb

If a document still guides unfinished work or should be checked during most development sessions, keep it in `active/`.
If it mainly explains why earlier decisions were made, keep it in `archive/`.
