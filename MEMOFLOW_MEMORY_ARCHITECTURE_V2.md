# Memoflow Memory Architecture v2

Last updated: 2026-04-24

## 1. Scope

This document defines the engineering architecture for Memoflow's next memory system on top of the current conversation-only backend.

Current baseline in this repository:

- `api/chat_routes.py` exposes `/api/chat`
- `services/chat_service.py` builds the prompt context from raw session history
- `providers/react_orchestrator.py` runs the main ReAct tool loop
- session history is preserved in-process only
- there is no persistent memory, no retrieval injection, no background reflection, no graph memory

This v2 document is intentionally not a generic survey. It is an implementation plan that maps external SOTA systems into Memoflow modules, data structures, trigger timing, and prompt responsibilities.

## 2. Design Position

### 2.1 Core judgment

The previous direction was wrong in one key way: it tried to perform context compaction, semantic extraction, retrieval, and memory writeback in the same per-turn hot path.

That design is rejected.

The new design separates memory into independent functions with different triggers:

- working-context management
- background semantic memory formation
- long-term graph / fact memory
- file-based project memory
- retrieval planning and assembly

Each function has:

- its own trigger timing
- its own prompt contract
- its own storage representation
- its own failure boundary

### 2.2 Architecture principles

1. Keep the hot path minimal.
2. Preserve recent raw conversation verbatim until compression is actually needed.
3. Treat compaction and memory extraction as different jobs.
4. Treat episodic evidence and semantic memory as different stores.
5. Use project/file memory for procedural guidance, not for conversational facts.
6. Never force all memory products into every turn.
7. Retrieval must be planned, not blindly appended.

## 3. What We Borrow From Existing Systems

### 3.1 Claude Code / Anthropic

Borrow:

- raw recent transcript retention
- threshold-triggered compaction
- file-based project memory (`CLAUDE.md` style)
- separation between working context and durable memory artifacts

Apply in Memoflow:

- `working_set.py` keeps recent turns verbatim
- `compaction.py` only runs near token pressure
- `file_memory.py` manages workspace-level markdown memories

### 3.2 Letta / MemGPT

Borrow:

- virtual context hierarchy
- explicit memory tiers
- compaction as state transition, not a side effect on every turn

Apply in Memoflow:

- working set is assembled from tiered sources:
  - pinned prompts
  - recent transcript
  - active checkpoint
  - optional retrieved memories
- checkpoint objects represent compacted working state

### 3.3 LangMem

Borrow:

- hot-path vs background-path separation
- delayed processing / reflection jobs
- namespace-scoped memory management

Apply in Memoflow:

- semantic extraction never blocks the main answer path
- reflection jobs run after debounce thresholds
- memory is always written under explicit scope:
  - `user`
  - `project`
  - `workspace`
  - `session`

### 3.4 Mem0

Borrow:

- memory extraction as a structured decision problem
- memory actions such as `ADD`, `UPDATE`, `DELETE`, `NONE`
- configurable extraction instructions

Apply in Memoflow:

- `semantic_manager.py` writes structured memory actions, not blind append
- each candidate memory has provenance, scope, confidence, and mutation intent

### 3.5 Zep / Graphiti

Borrow:

- episode-first ingestion
- temporal fact modeling
- contradiction handled as invalidation / supersession, not destructive overwrite
- hybrid semantic plus temporal retrieval

Apply in Memoflow:

- raw turns are persisted as episodes before semantic derivation
- long-term facts point back to episode evidence
- fact edges carry validity windows and supersedes relationships

### 3.6 Cognee

Borrow:

- typed knowledge units
- graph, vector, and structured storage split by job
- ontology-first mindset for durable memory

Apply in Memoflow:

- memory objects are typed:
  - preference
  - identity
  - task fact
  - project fact
  - procedure
  - decision
  - open issue
- storage backend is hybrid, not one-size-fits-all

### 3.7 OpenAI / Gemini / Copilot

Borrow:

- scope boundaries
- user controls and isolation
- top-of-mind loading instead of bulk injection
- project-only memory concepts

Apply in Memoflow:

- policy layer determines what memory is eligible for which conversation
- retrieval returns a capped curated set, never full dumps

## 4. Target Architecture

## 4.1 Layered model

### Layer A: Working Context Kernel

Purpose:

- support the next answer with minimal latency
- preserve exact recent dialogue and tool traces
- compact only when token pressure requires it

Stores:

- in-memory session transcript
- checkpoint snapshots

### Layer B: Semantic Memory Manager

Purpose:

- convert selected episodes into reusable semantic memory
- update existing memories conservatively

Stores:

- relational metadata store
- vector embeddings

### Layer C: Temporal Graph Memory

Purpose:

- represent evolving facts with provenance and time
- support contradiction resolution and multi-hop retrieval

Stores:

- graph database or graph tables

### Layer D: File / Project Memory

Purpose:

- persist durable procedural/project knowledge in human-editable form

Stores:

- markdown files in workspace

### Layer E: Retrieval Planner and Context Assembler

Purpose:

- decide whether retrieval is needed
- decide which stores to query
- assemble a bounded context pack for the next turn

Stores:

- none; this is a composition layer

## 4.2 Proposed module layout

These modules should be introduced incrementally under `services/memory/`:

```text
services/memory/
  __init__.py
  policy.py
  session_store.py
  transcript_store.py
  working_set.py
  checkpoints.py
  compaction.py
  file_memory.py
  semantic_manager.py
  background_reflection.py
  embedding_index.py
  graph_ingestor.py
  graph_retriever.py
  retrieval_planner.py
  context_assembler.py
```

## 4.3 Hook points in the current codebase

### `services/chat_service.py`

Future responsibilities:

- replace `_build_context_messages(...)` with a layered context assembler
- keep current recent-turn trimming as the fallback path
- call retrieval planning before the provider request only when policy says retrieval is necessary

### `providers/react_orchestrator.py`

Future responsibilities:

- remain the main answer path
- emit post-turn completion events that can schedule background memory jobs
- not perform semantic extraction inline

### `main.py` and API layer

Future responsibilities:

- add memory-aware configuration flags
- add background worker startup
- add memory debug endpoints later, but not in v0.1

## 5. Canonical Data Structures

## 5.1 Episode

Raw evidence unit. Never rewritten for semantic convenience.

```json
{
  "episode_id": "ep_01",
  "session_id": "sess_01",
  "turn_index": 18,
  "source": "chat",
  "messages": [
    {"role": "user", "content": "..." },
    {"role": "assistant", "content": "...", "tool_calls": []},
    {"role": "tool", "name": "current_time", "content": "..."}
  ],
  "created_at": "2026-04-24T09:00:00Z",
  "token_estimate": 742
}
```

## 5.2 Working Checkpoint

Compacted working-state object for a session.

```json
{
  "checkpoint_id": "ckpt_01",
  "session_id": "sess_01",
  "covers_episode_ids": ["ep_01", "ep_02", "ep_03"],
  "summary": {
    "conversation": "...",
    "task_state": "...",
    "open_loops": ["..."],
    "decisions": ["..."]
  },
  "created_at": "2026-04-24T09:10:00Z"
}
```

## 5.3 Memory Atom

Structured semantic memory candidate.

```json
{
  "memory_id": "mem_01",
  "scope": "project",
  "type": "decision",
  "subject": "backend architecture",
  "content": "Memoflow v0.1 only builds working-context kernel, not long-term retrieval injection.",
  "salience": 0.91,
  "confidence": 0.88,
  "action": "ADD",
  "supersedes": [],
  "evidence_episode_ids": ["ep_03"],
  "created_at": "2026-04-24T09:15:00Z"
}
```

## 5.4 Temporal Fact Edge

For graph memory.

```json
{
  "edge_id": "edge_01",
  "from_node": "project:memoflow",
  "relation": "uses_architecture",
  "to_node": "design:working_context_kernel",
  "valid_from": "2026-04-24T09:15:00Z",
  "valid_to": null,
  "status": "active",
  "evidence_episode_ids": ["ep_03"],
  "superseded_by": null
}
```

## 5.5 Retrieval Pack

Bounded memory payload injected into the next prompt.

```json
{
  "session_id": "sess_01",
  "reason": "token_pressure_and_project_question",
  "items": [
    {"kind": "checkpoint", "id": "ckpt_01", "content": "..."},
    {"kind": "file_memory", "path": "CLAUDE.md", "content": "..."},
    {"kind": "semantic_memory", "id": "mem_12", "content": "..."}
  ],
  "token_estimate": 1280
}
```

## 6. Trigger Timing Matrix

## 6.1 Hot path

Runs inside `/api/chat` before or during the model answer:

- append raw user turn to transcript
- assemble recent working set
- optionally load existing checkpoint
- optionally retrieve project/file memory
- only perform compaction if token pressure threshold is crossed

Must not run inline:

- semantic memory extraction
- contradiction reconciliation
- graph expansion
- background reflection over multiple episodes

## 6.2 Background path

Runs after the assistant turn completes:

- persist episode
- enqueue reflection job if debounce and salience thresholds match
- run semantic extraction prompt
- run memory mutation planning
- optionally update graph edges

## 6.3 Trigger policy

Default trigger rules:

1. Every completed turn:
   - persist transcript
   - persist episode

2. Near token threshold:
   - run compaction
   - create checkpoint

3. After N turns or after a salient task-state change:
   - schedule semantic reflection

4. On explicit project/workspace question:
   - query file memory

5. On fact lookup / user preference / prior decision query:
   - query semantic memory

6. On contradiction-sensitive queries:
   - query temporal graph

## 7. Prompt Responsibility Split

This is a strict separation boundary. One prompt should not do all jobs.

### 7.1 Compaction prompt

Input:

- covered raw episodes
- previous checkpoint if one exists

Output:

- updated working checkpoint

Must do:

- summarize task state
- preserve open loops
- preserve decisions and unresolved items

Must not do:

- emit durable semantic memories
- mutate long-term memory

### 7.2 Semantic extraction prompt

Input:

- one episode or a small episode batch
- optional recent checkpoint
- extraction policy

Output:

- candidate memory atoms with action labels:
  - `ADD`
  - `UPDATE`
  - `DELETE`
  - `NONE`

Must do:

- extract only durable and reusable facts/preferences/decisions/procedures

Must not do:

- assemble next-turn prompt context
- rewrite transcript

### 7.3 Retrieval planning prompt

Input:

- current user turn
- current session metadata
- token budget state
- available stores

Output:

- which stores to query
- how many items to load
- max token allocation per store

Must do:

- decide necessity and budget

Must not do:

- write new memory

### 7.4 Context assembly prompt

Preferred implementation: code first, prompt second.

The first version should assemble context deterministically in code:

- pinned prompts
- recent raw turns
- latest checkpoint
- retrieved project memory
- retrieved semantic memory

Use an LLM here only if later evidence shows deterministic packing is insufficient.

### 7.5 Contradiction / graph update prompt

Input:

- candidate memory atom
- existing related facts
- evidence episodes

Output:

- graph mutation plan:
  - create fact
  - supersede fact
  - mark uncertain

Must not do:

- generate user-facing answer text

## 8. Implementable Source -> Module -> Data -> Trigger -> Prompt Table

The table below is the coding blueprint. Each row should become one concrete implementation unit.

| Borrowed from | Memoflow module | Core data structure | Trigger timing | Prompt responsibility |
|---|---|---|---|---|
| Claude Code recent transcript retention | `services/memory/transcript_store.py` | `Episode`, raw message list | every completed turn | none; deterministic persistence only |
| Claude Code threshold compaction | `services/memory/compaction.py` | `WorkingCheckpoint` | only when token budget threshold is crossed | compress covered episodes into session checkpoint |
| Letta context hierarchy | `services/memory/working_set.py` | layered context segments | before provider call | none in v0.1; deterministic tier ordering |
| Letta state transition via compaction | `services/memory/checkpoints.py` | checkpoint metadata, covered episode ids | immediately after successful compaction | none; storage and versioning only |
| Anthropic / Claude file memory | `services/memory/file_memory.py` | markdown memory docs, file index | on project-scoped queries or startup scan | optional later summarization prompt for oversized files |
| LangMem delayed processing | `services/memory/background_reflection.py` | reflection job record | after answer completes and debounce conditions pass | route episode batches into extraction tasks |
| LangMem namespace scoping | `services/memory/policy.py` | scope policy objects | before retrieval and before write | none; deterministic policy checks |
| Mem0 structured memory actions | `services/memory/semantic_manager.py` | `MemoryAtom` with `ADD/UPDATE/DELETE/NONE` | background only | extract candidate durable memories and intended mutations |
| Mem0 configurable instructions | `services/memory/semantic_manager.py` | extraction policy config | background extraction start | tell extraction model what counts as memory in each scope |
| Zep episode-first provenance | `services/memory/session_store.py` + `transcript_store.py` | episode-to-session mapping | every completed turn | none |
| Zep / Graphiti temporal facts | `services/memory/graph_ingestor.py` | nodes, temporal edges, supersession edges | after semantic memory accepted | determine whether new fact is additive, conflicting, or superseding |
| Zep / Graphiti graph retrieval | `services/memory/graph_retriever.py` | graph query results | only on contradiction-sensitive or historical queries | optional planner prompt selects graph store |
| Cognee typed datapoints | `services/memory/semantic_manager.py` | typed memory taxonomy | during semantic extraction | emit typed memories, not free-form blobs |
| OpenAI / Gemini top-of-mind loading | `services/memory/retrieval_planner.py` | retrieval plan, token budget allocation | before provider call, only when needed | decide what to retrieve and how much |
| Copilot / OpenAI scope boundaries | `services/memory/policy.py` | `user/project/workspace/session` namespaces | every retrieval and every write | none; enforce isolation |
| Hybrid bounded context assembly | `services/memory/context_assembler.py` | `RetrievalPack` | before provider call after retrieval | no prompt in v0.1-v0.2; deterministic assembly |

## 9. Rollout Plan

## 9.1 v0.1: Working Context Kernel

Deliver:

- transcript persistence
- episode persistence
- token-aware working set builder
- threshold-triggered compaction
- checkpoint loading into next prompt
- optional file memory loading for project guidance

Do not deliver:

- semantic extraction
- vector retrieval
- graph memory
- contradiction resolution

Main code impact:

- extend `services/chat_service.py`
- add `services/memory/transcript_store.py`
- add `services/memory/working_set.py`
- add `services/memory/compaction.py`
- add `services/memory/checkpoints.py`
- add `services/memory/file_memory.py`

## 9.2 v0.2: Background Semantic Memory

Deliver:

- reflection jobs
- model-driven memory atom extraction
- scoped semantic store
- retrieval planner for semantic memory

Main code impact:

- add `background_reflection.py`
- add `semantic_manager.py`
- add `embedding_index.py`
- add `retrieval_planner.py`

## 9.3 v0.3: Temporal Graph Memory

Deliver:

- graph ingest
- supersession / invalidation
- temporal retrieval

## 9.4 v0.4: Full Hybrid Retrieval Planner

Deliver:

- multi-store retrieval policy
- relevance plus recency plus scope ranking
- bounded context pack generation

## 10. Concrete v0.1 Coding Rules

1. Do not call an LLM for memory on every turn.
2. Do not inject semantic memories into every prompt.
3. Do not mix compaction prompt and semantic extraction prompt.
4. Keep recent raw turns as the first-class context source.
5. Preserve tool-call and tool-result boundaries in episodes.
6. Any compacted state must record `covers_episode_ids`.
7. Any future semantic memory must point back to episode evidence.
8. File memory is editable source-of-truth, not a hidden cache.

## 11. Minimal initial implementation order

1. `session_store.py` and `transcript_store.py`
2. `working_set.py`
3. `checkpoints.py`
4. `compaction.py`
5. `file_memory.py`
6. hook `working_set` into `services/chat_service.py`
7. add threshold metrics and debug logging
8. only after v0.1 is stable, add background reflection

## 12. Reference systems

Primary public references used for this architecture:

- Mem0 OSS repo and docs: `github.com/mem0ai/mem0`, `docs.mem0.ai`
- Letta repo and docs: `github.com/letta-ai/letta`, `docs.letta.com`
- LangMem repo and docs: `github.com/langchain-ai/langmem`, `langchain-ai.github.io/langmem`
- Zep and Graphiti: `github.com/getzep/zep`, `github.com/getzep/graphiti`, `help.getzep.com`
- Cognee repo and docs: `github.com/topoteretes/cognee`, `docs.cognee.ai`
- OpenAI ChatGPT memory / projects help docs
- Anthropic Claude Code memory docs and memory tool docs
- Google Gemini memory help
- Microsoft Copilot memory help

The point is not to clone any one system. The point is to take:

- Claude/Letta's working-context discipline
- LangMem's background execution discipline
- Mem0's structured semantic mutation discipline
- Zep/Graphiti's temporal provenance discipline
- Cognee's typed data-model discipline
- OpenAI/Gemini/Copilot's scope discipline

and combine them into one implementable system that fits this repository's current conversation backend.
