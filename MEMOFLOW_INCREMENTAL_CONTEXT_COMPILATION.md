# Memoflow Incremental Context Compilation Design

Last updated: 2026-05-05

## 1. Purpose

This document clarifies how Memoflow should compile per-turn context without rebuilding every context source from scratch on every user input.

The current runtime correctly treats every user turn as a context compilation point. However, context compilation and context source rebuilding are different operations. Every turn needs a fresh decision about what the model should see, but not every turn needs a full history scan, file-memory reload, checkpoint reconstruction, or future long-term retrieval rebuild.

Core rule:

- Compile every turn.
- Rebuild stable sources only when dirty.
- Append turn deltas incrementally.
- Keep query-dependent retrieval separate from stable context frames.

## 2. Problem Statement

The v0.1 runtime is intentionally simple:

```text
prepare_turn
  -> load known history
  -> trim tool results
  -> group turns
  -> build runtime state
  -> decide checkpoint/file policy
  -> assemble working set
```

This is acceptable before long-term retrieval exists. But as the runtime grows, repeatedly rebuilding all source material has two costs:

1. Before token pressure reaches the compaction threshold, many context sources are unchanged and the work is duplicated.
2. After retrieval exists, some context is query-dependent and legitimately changes each turn, so naive caching of the whole prompt would be wrong.

The solution is not to skip compilation. The solution is to split compilation into reusable stable frames, incremental turn deltas, and per-turn retrieval packs.

## 3. Key Distinction

```text
Context compile every turn: yes.
Context source rebuild every turn: no.
```

`Context compile` means deciding the final bounded message pack for the current model call.

`Context source rebuild` means reloading or recomputing the stable ingredients that the compiler may use.

Every turn must produce a fresh `ContextPlan`. Only dirty sources should produce a new `CompiledContextFrame`.

## 4. Three-Part Context Model

### 4.1 Stable Context Frame

The stable frame contains reusable context material that changes only when its dependencies change.

Examples:

- system prompt
- developer prompt
- active checkpoint
- file memory
- project memory
- base recent raw turns
- runtime policy metadata
- context budget metadata

The stable frame should be cached per session/model/workspace scope.

### 4.2 Turn Delta

The turn delta contains information that always changes with the active turn.

Examples:

- current user message
- latest assistant response after completion
- tool calls and tool results for the current turn
- newly persisted episode
- incremental token counters

The turn delta should be appended or folded into runtime state without forcing a full stable-frame rebuild.

### 4.3 Query-Dependent Retrieval Pack

The retrieval pack contains dynamic memory or knowledge selected for the current user request.

Examples for future versions:

- semantic KV recall
- vector recall
- graph recall
- project rule recall
- user preference recall
- contradiction-sensitive historical facts

This pack can change every turn even when the stable frame is unchanged. It must not be cached as part of the stable frame.

## 5. Target Prepare Flow

Future `prepare_turn` should be structured like this:

```text
prepare_turn
  -> preflight_state_update
  -> get_or_build_stable_frame
  -> plan_dynamic_retrieval
  -> assemble_turn_context
  -> final_budget_guard
```

### 5.1 `preflight_state_update`

Responsibilities:

- read or update lightweight session runtime state
- check current model and token budget
- check latest checkpoint id
- check episode count and recent message count
- check file memory hash
- check memory index version once retrieval exists
- compute dirty flags

This phase should be cheap.

### 5.2 `get_or_build_stable_frame`

Responsibilities:

- return cached `CompiledContextFrame` when clean
- rebuild only dirty stable sources
- record which dependency invalidated the frame
- preserve debug visibility

This phase should avoid full reconstruction when all dependencies are unchanged.

### 5.3 `plan_dynamic_retrieval`

Responsibilities:

- decide whether long-term retrieval is needed for this turn
- choose stores to query
- allocate retrieval token budget
- return an empty retrieval pack when retrieval is disabled or unnecessary

This phase is intentionally per-turn and query-dependent.

### 5.4 `assemble_turn_context`

Responsibilities:

- combine stable frame, turn delta, retrieval pack, and current user input
- preserve source ordering rules
- ensure retrieval is capped and labeled
- avoid duplicate content already present in recent raw turns

### 5.5 `final_budget_guard`

Responsibilities:

- estimate final prompt tokens
- trim optional retrieval first
- trim older recent raw turns if needed
- trigger emergency compaction only when normal trimming cannot fit

## 6. Proposed Data Structures

### 6.1 Runtime Session State

```python
@dataclass
class RuntimeSessionState:
    session_id: str
    session_key: str
    workspace_dir: str
    model: str
    provider: str
    token_budget: int
    latest_checkpoint_id: str | None
    checkpoint_covered_count: int
    episode_count: int
    raw_message_count: int
    recent_turn_count: int
    estimated_raw_tokens: int
    estimated_recent_tokens: int
    file_memory_hash: str | None
    memory_index_version: str | None
    policy_version: str
    stable_frame_id: str | None
```

This state is not the prompt. It is the cheap metadata used to decide whether prompt ingredients are dirty.

### 6.2 Compiled Context Frame

```python
@dataclass
class CompiledContextFrame:
    frame_id: str
    session_id: str
    model: str
    provider: str
    token_budget: int
    checkpoint_id: str | None
    checkpoint_covered_count: int
    file_memory_hash: str | None
    memory_policy_version: str
    source_fingerprints: dict[str, str]
    base_messages: list[dict]
    estimated_tokens: int
    created_at: str
```

This frame can be reused until one of its fingerprints changes.

### 6.3 Turn Delta

```python
@dataclass
class TurnDelta:
    session_id: str
    user_message: str
    new_messages: list[dict]
    current_turn_index: int
    estimated_tokens: int
```

The delta is always fresh and should be appended to the final prompt after stable context material.

### 6.4 Retrieval Pack

```python
@dataclass
class RetrievalPack:
    mode: str
    query: str
    sources: list[str]
    items: list[dict]
    estimated_tokens: int
    skipped_reason: str | None = None
```

In v0.1-v0.2 this is usually empty. In later versions it becomes the dynamic long-term memory input.

### 6.5 Context Plan

```python
@dataclass
class ContextPlan:
    session_id: str
    stable_frame_id: str | None
    reused_stable_frame: bool
    dirty_reasons: list[str]
    retrieval_mode: str
    retrieval_sources: list[str]
    assembly_strategy: str
    estimated_tokens: int
```

The plan is the per-turn decision artifact. It should be rebuilt every turn even when the stable frame is reused.

## 7. Dirty Flags

The stable frame must be rebuilt when any of these changes:

| Dirty flag | Meaning |
| --- | --- |
| `checkpoint_changed` | active checkpoint id or covered count changed |
| `file_memory_changed` | file memory hash changed |
| `model_changed` | model/provider changed |
| `token_budget_changed` | resolved context budget changed |
| `policy_changed` | runtime/context policy version changed |
| `workspace_changed` | workspace scope changed |
| `recent_window_invalid` | recent raw turns no longer match cached frame assumptions |
| `memory_index_changed` | future retrieval index version changed enough to affect static memory material |

Dirty flags should be visible in `memory_debug`.

## 8. Append-Only Fast Path

Before token pressure reaches the watch threshold, many turns can use an append-only fast path.

Fast path is allowed when:

- token pressure is below `watch`, currently `< 0.70`
- stable frame dirty flags are empty
- retrieval planner returns `none`
- no checkpoint was saved after the previous turn
- file memory is unchanged
- model/provider/token budget are unchanged
- recent raw window is still valid

Fast path behavior:

```text
prepare_turn
  -> reuse stable frame
  -> append current user delta
  -> skip full history regrouping
  -> run final budget guard

finalize_turn
  -> append episode
  -> update counters
  -> mark dirty only if checkpoint/file/policy/index changed
```

This is an optimization, not a semantic shortcut. The runtime still produces a fresh `ContextPlan`.

## 9. Retrieval Interaction

Future retrieval changes the current turn, not the stable frame.

Correct composition:

```text
stable_frame + retrieval_pack + recent_delta + current_user_message -> final prompt
```

Retrieval should be planned each turn because it depends on the current user message and active task state.

Retrieval should not invalidate the stable frame unless the retrieval index is used as static top-of-mind memory, which is not planned for v0.2.

## 10. Source Ordering Rules

Recommended final order:

1. system/developer prompts
2. active checkpoint if loaded
3. file/project memory if loaded
4. bounded retrieval pack if any
5. recent raw turns
6. current user message

Rationale:

- stable instructions come first
- retrieval is context, not conversation history
- recent raw dialogue remains closest to the user turn
- current user message remains last

## 11. Debug Output

`memory_debug` should expose incremental compilation decisions.

Suggested shape:

```json
{
  "context_compile": {
    "stable_frame_id": "frame_001",
    "reused_stable_frame": true,
    "dirty_reasons": [],
    "append_only_fast_path": true,
    "retrieval_mode": "none",
    "retrieval_sources": [],
    "estimated_tokens": 4200
  }
}
```

When dirty:

```json
{
  "context_compile": {
    "stable_frame_id": "frame_002",
    "reused_stable_frame": false,
    "dirty_reasons": ["checkpoint_changed"],
    "append_only_fast_path": false,
    "retrieval_mode": "none"
  }
}
```

## 12. Implementation Plan

### Phase 1: Instrumentation

- Add `ContextPlan` debug output without changing assembly behavior.
- Add dirty-reason calculation based on existing checkpoint, model, token budget, and file memory inputs.
- Record whether a turn would qualify for append-only fast path.

### Phase 2: Stable Frame Cache

- Add in-memory `ContextFrameCache` scoped by session id.
- Cache stable frame dependencies and fingerprints.
- Reuse cached frame when dirty flags are empty.
- Keep fallback to current full builder.

### Phase 3: Incremental Counters

- Track episode count, raw message count, estimated tokens, and recent-turn count incrementally.
- Avoid full history regrouping on clean fast-path turns.
- Rebuild from source when counters are missing or inconsistent.

### Phase 4: Retrieval Pack Slot

- Add empty `RetrievalPack` interface before real retrieval exists.
- Make context assembly accept stable frame + retrieval pack + turn delta.
- Keep retrieval disabled by default.

### Phase 5: Retrieval-Aware Compilation

- Add retrieval planner feature flag.
- Allow per-turn retrieval without invalidating stable frame.
- Add dedupe between retrieval items and recent raw turns.

## 13. Non-Goals

This design does not require immediate implementation of:

- vector retrieval
- graph retrieval
- persistent context frame cache
- distributed job coordination
- exact token accounting for every cached fragment

The near-term goal is to make the runtime architecture ready for incremental compilation without blocking current v0.1-v0.2 behavior.

## 14. Non-Negotiable Rules

1. Do not skip per-turn context planning.
2. Do not rebuild stable context sources when clean.
3. Do not cache query-dependent retrieval as part of the stable frame.
4. Do not let retrieval invalidate checkpoint/file memory caches unless a static memory index dependency is explicitly introduced.
5. Do not hide dirty reasons; every cache miss must be explainable.
6. Do not optimize away final budget guard.
7. Do not make fast path semantically different from full compilation.

