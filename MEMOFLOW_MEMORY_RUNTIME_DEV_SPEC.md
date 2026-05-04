# Memoflow Memory Runtime Development Spec

Last updated: 2026-05-03

## 1. Purpose

Memory Runtime is the control center for Memoflow memory. It is not a memory database and not a retrieval feature. It compiles the right working context for the current turn, schedules maintenance after the turn, and makes every memory decision explainable.

Core rule:

- `prepare_turn` stays lightweight and normally does not run compaction.
- `finalize_turn` persists the turn and prepares for the next turn while the user is idle.
- `emergency_compaction` runs only when the current turn cannot fit after normal trimming.

## 2. Mental Model

Think of the runtime as a meeting secretary.

- Raw messages are the full meeting transcript.
- Episodes are archived meeting records.
- Checkpoints are compact meeting notes for continuing the current task.
- File memory is the project handbook.
- Context assembly is arranging the desk before the agent answers.
- Post-turn compaction is cleaning the desk after the meeting so the next meeting starts cleanly.

The runtime optimizes future decisions. It does not try to preserve every past detail in the prompt.

## 3. Runtime v0.1 Scope

Runtime v0.1 includes:

- Recent raw turns.
- Episode persistence.
- Active checkpoint loading.
- Post-turn checkpoint compaction.
- Emergency compaction only when necessary.
- Checkpoint folding.
- File memory loading.
- Runtime events and debug output.

Runtime v0.1 excludes:

- Memory atom extraction.
- Vector retrieval.
- Graph retrieval.
- User profile memory.
- Cross-session preference storage.
- Proactive long-term recall.

## 4. Module Plan

Add or refactor these modules under `services/memory/`:

- `runtime.py`: orchestrates `prepare_turn` and `finalize_turn`.
- `policy.py`: owns trigger decisions and threshold checks.
- `runtime_events.py`: defines structured runtime events and debug aggregation.
- `working_set.py`: becomes deterministic context assembly, not policy logic.
- `compaction.py`: keeps compaction and folding logic.
- `sidecar_compaction.py`: keeps sidecar text-section compaction.
- `transcript_store.py`: persists episodes and raw evidence.
- `checkpoints.py`: persists and loads checkpoints.

`services/chat_service.py` should call runtime APIs instead of directly coordinating checkpoint, file memory, and compaction details.

## 5. Runtime Input

```python
@dataclass
class RuntimeInput:
    session_id: str
    session_key: str
    model: str
    provider: str
    input_source: str
    user_message: str
    workspace_dir: str
    history_messages: list[Any]
    latest_checkpoint: Optional[dict]
    token_budget: int
```

Field meanings:

- `session_id`: current conversation id.
- `session_key`: stable session alias such as `main` or a Feishu chat key.
- `model`: model selected for the main answer.
- `provider`: provider selected for the main answer.
- `input_source`: source of the input, such as `web`, `feishu`, or `smoke`.
- `user_message`: current user message.
- `workspace_dir`: repository directory for file memory lookup.
- `history_messages`: raw session messages currently known in memory.
- `latest_checkpoint`: latest saved working checkpoint, if any.
- `token_budget`: resolved model context budget for this provider.

## 6. Runtime State

```python
@dataclass
class RuntimeState:
    raw_message_count: int
    user_turn_count: int
    uncovered_turn_count: int
    episode_count: int
    checkpoint_covered_count: int
    estimated_full_tokens: int
    estimated_checkpoint_tokens: int
    estimated_recent_tokens: int
    token_budget: int
    token_pressure_ratio: float
```

Risk levels:

```text
safe:              ratio < 0.70
watch:             ratio >= 0.70
prepare_compact:   ratio >= 0.82
emergency:         ratio >= 0.95
hard_fail:         ratio >= 1.00 after trimming
```

`token_pressure_ratio` is `estimated_context_tokens / token_budget`.

## 7. Policy Decision

```python
@dataclass
class RuntimePolicyDecision:
    load_checkpoint: bool
    load_file_memory: bool
    emergency_compaction: bool
    post_turn_compaction: bool
    trigger_folding: bool
    enqueue_reflection: bool
    retrieval_mode: str
    reason: list[str]
```

Expected example:

```json
{
  "load_checkpoint": true,
  "load_file_memory": false,
  "emergency_compaction": false,
  "post_turn_compaction": true,
  "trigger_folding": false,
  "enqueue_reflection": false,
  "retrieval_mode": "checkpoint_only",
  "reason": ["checkpoint_available", "post_turn_token_pressure"]
}
```

## 8. Prepare Turn Strategy

`prepare_turn` compiles context for the current model call.

```python
async def prepare_turn(runtime_input: RuntimeInput) -> ContextPackage:
    ...
```

Steps:

1. Receive the current user message from `chat_service` after it is added to session history.
2. Build `RuntimeState`.
3. Load the latest checkpoint if one exists.
4. Remove raw turns already covered by checkpoint.
5. Load file memory only if policy says the query needs project context.
6. Keep recent raw turns from newest to oldest.
7. Trim long tool outputs before dropping turns.
8. Trigger emergency compaction only if the assembled context still cannot fit.

Normal compaction is not scheduled in `prepare_turn`. It belongs in `finalize_turn`.

## 9. Loading Policies

Checkpoint loading condition:

```text
latest_checkpoint exists
AND latest_checkpoint.covers_episode_ids is not empty
```

Checkpoint rules:

- Convert checkpoint summary into a system message.
- Remove raw turns covered by the checkpoint.
- Never inject both checkpoint and its covered raw turns.

File memory loading condition:

```text
FILE_MEMORY_ENABLED is true
AND user_message looks project/file/architecture related
```

Initial keywords:

```text
project, repo, repository, architecture, design, file, code, module,
项目, 架构, 文件, 代码, 模块, 文档
```

File memory budget:

```text
file_memory_max_tokens = min(token_budget * 0.10, 1200)
```

File memory rules:

- Prefer `CLAUDE.md`, `MEMORY.md`, `README.md`, and explicit architecture docs.
- Load at most 1-2 files in v0.1.
- Do not summarize with LLM in v0.1.
- Truncate deterministically when over budget.

## 10. Recent Raw Turn Policy

Defaults:

```text
min_recent_user_turns = 2
target_recent_user_turns = 4
max_recent_user_turns = 8
```

Rules:

- Always include the current user message.
- Keep whole user-turn groups.
- Do not split assistant tool calls from tool results.
- Trim large tool result content before dropping the whole turn.

## 11. Emergency Compaction Policy

Emergency compaction is a fallback, not the normal path.

Trigger only when:

```text
assembled_context_tokens > token_budget
AND old raw turns have already been removed
AND file memory has already been trimmed or removed
AND long tool results have already been trimmed
```

Processing:

- Compact the oldest uncovered turns synchronously.
- Keep at least `min_recent_user_turns` raw turns.
- If sidecar fails, use rule-based summary.
- If still over budget, return an explicit context budget error.

## 12. Context Package

```python
@dataclass
class ContextPackage:
    messages: list[dict]
    token_budget: int
    estimated_tokens: int
    policy_decision: RuntimePolicyDecision
    pending_checkpoint: Optional[dict]
    debug: dict
```

Message ordering:

1. Pinned system/developer prompts.
2. Working checkpoint.
3. File memory.
4. Future retrieval pack.
5. Recent raw turns.

## 13. Finalize Turn Strategy

`finalize_turn` persists the turn and prepares the next turn.

```python
async def finalize_turn(
    runtime_input: RuntimeInput,
    context_package: ContextPackage,
    turn_messages: list[Any],
) -> RuntimeFinalizeResult:
    ...
```

Steps:

1. Persist the full turn as an episode.
2. Recompute post-turn token risk for the next turn.
3. Decide whether post-turn compaction is needed.
4. Compact old uncovered turns when needed.
5. Fold checkpoint when it exceeds hard max.
6. Save checkpoint with covered episode ids.
7. Record runtime events.

## 14. Episode Format

Episode is source of truth.

```json
{
  "episode_id": "ep_20260503_021",
  "session_id": "sess_001",
  "turn_index": 21,
  "source": "feishu",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    {"role": "tool", "name": "...", "content": "..."}
  ],
  "token_estimate": 1234,
  "created_at": "2026-05-03T08:00:00Z"
}
```

Rules:

- Never rewrite episodes.
- Never use checkpoint as evidence.
- Every checkpoint must point back to episode ids.

## 15. Post-Turn Compaction Policy

Post-turn compaction is the normal compaction path.

Trigger when:

```text
post_turn_estimated_next_context_tokens / token_budget >= 0.82
AND uncovered_user_turn_count >= 8
AND uncovered_old_turns_count > 4
AND no checkpoint job is already pending for this session
```

Defaults:

```text
CONTEXT_COMPACTION_TRIGGER_RATIO = 0.82
CONTEXT_COMPACTION_MIN_USER_TURNS = 8
CONTEXT_COMPACTION_KEEP_RECENT_TURNS = 4
```

Checkpoint output:

```json
{
  "summary": {...},
  "covers_episode_ids": ["ep_1", "ep_2"],
  "token_estimate": 900
}
```

## 16. Checkpoint Summary Format

Checkpoint summary is the meeting note itself.

```python
{
  "task_kernel": str,
  "conversation": str,
  "task_state": str,
  "open_loops": list[str],
  "decisions": list[str],
  "important_artifacts": list[str],
  "tool_outcomes": list[str],
  "user_constraints": list[str],
}
```

Field meanings:

- `task_kernel`: one sentence describing the current core task.
- `conversation`: concise reason why the task reached its current state.
- `task_state`: current progress, current blockage, and immediate next work.
- `open_loops`: unresolved tasks, questions, or validation gaps.
- `decisions`: decisions already made and not to be reopened casually.
- `important_artifacts`: files, modules, APIs, docs, model names, or ids that matter.
- `tool_outcomes`: tool results that still affect future work.
- `user_constraints`: explicit user requirements and boundaries.

Expected example:

```json
{
  "task_kernel": "Implement Memoflow Memory Runtime as the context compiler and memory scheduling center.",
  "conversation": "The project moved away from eager per-turn memory extraction and confirmed a runtime-first design with post-turn compaction.",
  "task_state": "Runtime policy and thresholds are being specified before coding begins.",
  "open_loops": ["Implement runtime.prepare_turn", "Run multi-turn /api/chat smoke test"],
  "decisions": ["Normal compaction happens in finalize_turn", "Checkpoint is runtime artifact, not long-term memory"],
  "important_artifacts": ["services/memory/runtime.py", "services/memory/policy.py", "services/chat_service.py"],
  "tool_outcomes": ["Fixed section-text protocol is more parseable than JSON for sidecar compaction"],
  "user_constraints": ["Do not return to per-turn atom extraction", "Do not use qwen3:1.7b as automatic fallback"]
}
```

## 17. Sidecar Output Protocol

Sidecar compaction must not ask `qwen3:30b-a3b` for JSON.

Expected model output:

```text
TASK_KERNEL:
Implement Memoflow Memory Runtime as a context compiler.

CONVERSATION:
The design moved from eager memory operations to post-turn runtime maintenance.

TASK_STATE:
Runtime thresholds and policy fields are being finalized before coding.

OPEN_LOOPS:
- Implement runtime.prepare_turn
- Implement runtime.finalize_turn

DECISIONS:
- Normal compaction happens after the turn
- Emergency compaction is only a fallback

IMPORTANT_ARTIFACTS:
- services/memory/runtime.py
- services/memory/policy.py

TOOL_OUTCOMES:
- qwen3:30b-a3b is unreliable for JSON

USER_CONSTRAINTS:
- Do not use qwen3:1.7b as fallback
```

Parser standards:

- Section headers are uppercase and end with `:`.
- Paragraph sections become strings.
- List sections accept only bullet lines beginning with `- `.
- Explanation lines before the first section are ignored.
- Schema placeholder text must be filtered.
- Dirty sidecar output falls back to rule-based summary.

Quality gate:

```text
task_kernel is non-empty
AND at least one of conversation/task_state is non-empty
AND at least one of open_loops/decisions/important_artifacts/user_constraints is non-empty
AND no schema placeholder text appears
```

## 18. Checkpoint Folding Policy

Trigger when:

```text
checkpoint_token_estimate > CONTEXT_CHECKPOINT_HARD_MAX_TOKENS
```

Defaults:

```text
CONTEXT_CHECKPOINT_TARGET_TOKENS = 900
CONTEXT_CHECKPOINT_HARD_MAX_TOKENS = 1400
```

Keep first:

- `task_kernel`
- `open_loops`
- `decisions`
- `user_constraints`
- `important_artifacts`

Trim first:

- `conversation`
- repeated details
- closed issues
- long tool outcomes
- explanatory prose

Hard rule:

```text
after_folding_tokens <= hard_max
```

## 19. Runtime Events

Runtime events are the source for `memory_debug`.

```python
@dataclass
class RuntimeEvent:
    type: str
    timestamp: str
    data: dict
```

Event types:

```text
turn_started
runtime_state_built
checkpoint_loaded
file_memory_loaded
context_compiled
emergency_compaction_triggered
episode_persisted
post_turn_compaction_triggered
sidecar_compaction_succeeded
sidecar_compaction_failed
checkpoint_folded
checkpoint_saved
```

## 20. Memory Debug Output

Expected SSE debug shape:

```json
{
  "runtime_version": "0.1",
  "policy": {
    "load_checkpoint": true,
    "load_file_memory": true,
    "post_turn_compaction_scheduled": true,
    "emergency_compaction": false,
    "retrieval_mode": "checkpoint_file_recent"
  },
  "budget": {
    "token_budget": 24576,
    "estimated_tokens": 20100,
    "pressure_ratio": 0.82,
    "risk_level": "prepare_compact"
  },
  "sources": {
    "checkpoint_id": "ckpt_20260503_004",
    "recent_turn_count": 4,
    "covered_episode_count": 16,
    "file_memory": ["MEMOFLOW_MEMORY_ARCHITECTURE_V2.md"],
    "retrieval_pack": []
  },
  "events": ["turn_started", "runtime_state_built", "checkpoint_loaded", "context_compiled", "episode_persisted"]
}
```

This must answer three questions:

- What did the agent see this turn?
- Why did runtime choose that context?
- Did runtime prepare the next turn after answering?

## 21. Failure Handling

Sidecar timeout:

- Do not block the current answer.
- Record `sidecar_compaction_failed`.
- Fallback to rule-based summary.
- Save no checkpoint if fallback also fails.

Dirty sidecar output:

- Fail parser quality gate.
- Fallback to rule-based summary.
- Include `raw_preview` in debug.

Checkpoint save failure:

- Do not fail the chat turn.
- Record event.
- Next turn falls back to raw turns.

Prepare-turn over-budget:

- Trim old raw turns.
- Trim file memory.
- Trim tool output.
- Trigger emergency compaction.
- Return explicit context budget error only if still over budget.

## 22. Implementation Order

Implement in this order:

1. Add `services/memory/runtime_events.py`.
2. Add `services/memory/policy.py`.
3. Add `services/memory/runtime.py`.
4. Refactor `working_set.py` into deterministic assembly.
5. Move compaction scheduling from prepare path to finalize path.
6. Move `_finalize_turn_memory()` into runtime.
7. Stabilize sidecar section parser quality gate.
8. Return stable `memory_debug` in `/api/chat` SSE.
9. Run multi-turn `/api/chat` smoke test.

## 23. Acceptance Criteria

Runtime v0.1 is acceptable when:

- Normal short chats do not trigger compaction.
- Post-turn compaction triggers when next-turn risk crosses threshold.
- Emergency compaction triggers only when current context cannot fit.
- Checkpoint never duplicates covered raw turns in prompt.
- Episode persistence always happens before checkpoint save.
- `memory_debug` clearly explains policy, budget, sources, and events.
- Sidecar failure never blocks current answer.
- No semantic memory or graph memory is introduced in v0.1.
