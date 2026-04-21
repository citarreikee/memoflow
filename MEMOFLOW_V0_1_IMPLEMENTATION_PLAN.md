# Memoflow v0.1 Implementation Plan

Status: Ready for implementation planning  
Target repo: `d:\Joestar\jojorn\memoflow`  
Base system: pure ReAct conversation backend

## 1. Current Baseline

The current backend already provides:

- FastAPI service
- `/api/chat` streaming endpoint
- in-process session management
- DeepSeek / Kimi / Ollama provider adapters
- ReAct orchestration
- minimal tool registry
- CLI chat client

The current backend intentionally does not provide:

- persistent episodes
- short-term compaction
- long-term memory
- retrieval
- memory injection
- memory inspection

v0.1 adds the first memory layer without disrupting the existing chat path.

## 2. v0.1 Goal

v0.1 should turn the pure conversation backend into a memory-aware backend with:

1. durable episode storage
2. structured short-term state
3. explicit context compaction
4. ADD-only semantic memory atoms
5. basic keyword + vector retrieval
6. context assembly before model invocation
7. inspectable memory/debug APIs

v0.1 should not implement temporal graph memory, advanced contradiction handling, or autonomous memory evolution.

## 3. First-Stage Product Behavior

After v0.1, the system should behave as follows:

1. User sends a chat message.
2. Backend loads the session's short-term state.
3. Backend retrieves relevant long-term memory for that session/project.
4. Backend assembles a prompt context under token budget.
5. Model responds through the existing ReAct pipeline.
6. Backend writes an episode record for the completed turn.
7. Backend asynchronously extracts memory atoms from the episode.
8. Future turns can recall those memory atoms.
9. Operators can inspect what memory was stored and why it was injected.

## 4. What v0.1 Adds To The Current Pure Chat Backend

### 4.1 Persistent episode store

Purpose:

- save every completed turn as an auditable source record

Adds:

- persistent record of user message
- assistant final answer
- ReAct tool calls/results
- session id
- source channel
- timestamps
- metadata

Reason:

- long-term memory must be evidence-backed
- later reindexing must be possible without relying on transient session state

### 4.2 Short-term state manager

Purpose:

- replace uncontrolled in-memory history growth with structured short-term state

Adds:

- recent turn window
- running conversation summary
- task state summary
- open issue summary
- summary coverage tracking

Reason:

- model context must remain bounded
- compaction must be explicit and inspectable

### 4.3 Memory harness layer

Purpose:

- make memory behavior task-aware from the beginning

Adds:

- default `conversation` harness
- default `coding` harness
- static harness selection per session

Reason:

- coding tasks need different memory priorities from general conversation
- adding this abstraction later would force a rewrite

### 4.4 ADD-only semantic memory atoms

Purpose:

- store durable facts without risky overwrite behavior

Adds:

- fact/preference/project_state/warning memory types
- evidence links to episodes
- confidence and importance scores
- hash-based dedupe

Reason:

- v0.1 should avoid destructive memory mutation
- contradiction handling can be layered later

### 4.5 Basic retrieval

Purpose:

- load relevant memory before each model call

Adds:

- keyword search
- vector search
- simple score fusion
- scope filtering

Reason:

- memory is useful only if selectively loaded
- raw transcript replay is not acceptable

### 4.6 Context assembler

Purpose:

- convert short-term state and retrieved memory into model-ready messages

Adds:

- fixed context section order
- per-section token budget
- prompt patch generation

Reason:

- retrieval result should not be dumped raw into the prompt
- assembly policy must be testable and versioned

### 4.7 Debug and inspection APIs

Purpose:

- make memory behavior observable

Adds:

- list episodes
- inspect short-term state
- inspect memory atoms
- inspect last retrieval bundle
- inspect assembled context preview

Reason:

- memory failures must be debuggable
- no opaque memory behavior

## 5. Proposed Module Layout

```text
services/
  memory/
    __init__.py
    harness.py
    short_term.py
    episodes.py
    ingestor.py
    retriever.py
    assembler.py
    schemas.py
    token_budget.py
    stores/
      __init__.py
      sqlite_store.py
      vector_store.py
api/
  memory_routes.py
```

## 6. Storage Choice For v0.1

Use SQLite for v0.1 local mode.

Reason:

- minimal operational overhead
- works immediately in local development
- supports durable episode and atom storage
- supports FTS5 for keyword search
- easy to migrate to PostgreSQL later

Vector storage options:

1. v0.1 minimal: store embeddings in SQLite JSON and do brute-force cosine for small local datasets.
2. v0.1 stronger: use Chroma or Qdrant.
3. production path: pgvector.

Recommendation:

- start with SQLite + FTS5
- define a vector store interface immediately
- make backend swappable before scale work

## 7. Database Tables

### 7.1 episodes

```sql
CREATE TABLE episodes (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  session_key TEXT,
  harness TEXT NOT NULL,
  source TEXT NOT NULL,
  user_message TEXT NOT NULL,
  assistant_answer TEXT,
  tool_trace_json TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT NOT NULL
);
```

### 7.2 short_term_states

```sql
CREATE TABLE short_term_states (
  session_id TEXT PRIMARY KEY,
  harness TEXT NOT NULL,
  recent_turn_ids_json TEXT NOT NULL,
  conversation_summary TEXT,
  task_state_summary TEXT,
  open_issues_summary TEXT,
  covered_message_ids_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### 7.3 memory_atoms

```sql
CREATE TABLE memory_atoms (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  content TEXT NOT NULL,
  normalized_content TEXT NOT NULL,
  evidence_episode_ids_json TEXT NOT NULL,
  entities_json TEXT,
  keywords_json TEXT,
  confidence REAL NOT NULL,
  importance REAL NOT NULL,
  status TEXT NOT NULL,
  hash TEXT NOT NULL,
  embedding_json TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  observed_at TEXT NOT NULL
);
```

### 7.4 memory_atoms_fts

```sql
CREATE VIRTUAL TABLE memory_atoms_fts
USING fts5(content, normalized_content, keywords_json, content='memory_atoms', content_rowid='rowid');
```

Note:

- The exact FTS linkage may need adjustment because `memory_atoms.id` is TEXT. v0.1 can either add an integer rowid surrogate or maintain a standalone FTS table.

### 7.5 retrieval_events

```sql
CREATE TABLE retrieval_events (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  query TEXT NOT NULL,
  harness TEXT NOT NULL,
  retrieved_atom_ids_json TEXT NOT NULL,
  scores_json TEXT NOT NULL,
  assembled_context_preview TEXT,
  created_at TEXT NOT NULL
);
```

## 8. Harness Defaults

### 8.1 conversation harness

```text
recent_turn_limit: 6
compaction_token_threshold: 12000
memory_types: semantic, preference, project_state
retrieval_priority: profile > semantic > project_state > warning
tool_trace_policy: compact_aggressively
```

### 8.2 coding harness

```text
recent_turn_limit: 8
compaction_token_threshold: 18000
memory_types: semantic, project_state, procedural, warning
retrieval_priority: warning > project_state > procedural > semantic
tool_trace_policy: preserve_changed_files_and_errors
```

v0.1 default:

- use `coding` for this repo unless explicitly overridden

Reason:

- Memoflow is currently being built as a coding/agent infrastructure project

## 9. Chat Pipeline Changes

Current simplified flow:

```text
prepare_chat_turn
  -> build context from session history
  -> react_orchestrator.stream_with_react
  -> save assistant response to session
```

v0.1 target flow:

```text
prepare_chat_turn
  -> resolve harness
  -> load short-term state
  -> retrieve long-term memory
  -> assemble context
  -> react_orchestrator.stream_with_react
  -> save assistant response to session
  -> record episode
  -> update short-term state
  -> enqueue memory ingestion
```

Important:

- existing `/api/chat` response shape should remain compatible
- memory metadata can be added as extra SSE events later, but not required in first implementation

## 10. API Additions

### 10.1 list episodes

```text
GET /api/memory/episodes?session_id=...
```

### 10.2 inspect short-term state

```text
GET /api/memory/short-term/{session_id}
```

### 10.3 search memory

```text
POST /api/memory/search
```

Body:

```json
{
  "query": "what did we decide about short-term compaction?",
  "scope_type": "project",
  "scope_id": "memoflow",
  "harness": "coding"
}
```

### 10.4 inspect memory atom

```text
GET /api/memory/atoms/{id}
```

### 10.5 inspect retrieval events

```text
GET /api/memory/retrieval-events?session_id=...
```

## 11. Implementation Order

### Step 1. Add schemas and store interfaces

Files:

- `services/memory/schemas.py`
- `services/memory/stores/sqlite_store.py`

Deliver:

- dataclasses or Pydantic models
- SQLite initialization
- CRUD for episodes and memory atoms

### Step 2. Add EpisodeStore integration

Files:

- `services/memory/episodes.py`
- `services/chat_service.py`

Deliver:

- completed chat turns write episode records
- tool trace from ReAct messages is captured

### Step 3. Add ShortTermContextManager

Files:

- `services/memory/short_term.py`
- `services/memory/token_budget.py`

Deliver:

- recent turn window
- placeholder summarization interface
- summary coverage bookkeeping
- deterministic compaction boundaries

### Step 4. Add harness config

Files:

- `services/memory/harness.py`

Deliver:

- `conversation` and `coding` harness
- static harness resolution
- section budget definitions

### Step 5. Add basic memory ingestor

Files:

- `services/memory/ingestor.py`

Deliver:

- rule-assisted LLM extraction prompt
- ADD-only atoms
- hash dedupe
- evidence episode linkage

### Step 6. Add retriever

Files:

- `services/memory/retriever.py`

Deliver:

- SQLite FTS search
- vector interface stub
- simple score fusion
- retrieval event persistence

### Step 7. Add context assembler

Files:

- `services/memory/assembler.py`

Deliver:

- prompt patch generation
- section budgets
- assembled context preview

### Step 8. Add memory APIs

Files:

- `api/memory_routes.py`
- `main.py`

Deliver:

- episode inspection
- short-term state inspection
- memory search
- atom inspection
- retrieval event inspection

### Step 9. Add tests and fixtures

Files:

- `tests/test_memory_short_term.py`
- `tests/test_memory_ingestor.py`
- `tests/test_memory_retriever.py`
- `tests/test_memory_chat_integration.py`

Deliver:

- deterministic unit tests
- one end-to-end chat memory regression

## 12. Acceptance Criteria

v0.1 is complete when:

1. `/api/chat` still works without memory enabled.
2. `/api/chat` works with memory enabled.
3. completed turns are persisted as episodes.
4. short-term state can be inspected per session.
5. old turns can be compacted without breaking tool call boundaries.
6. memory atoms are written ADD-only with evidence links.
7. keyword search returns relevant memory atoms.
8. context assembler injects memory under budget.
9. retrieval events are inspectable.
10. memory can be disabled through config for fallback.

## 13. Configuration Flags

Add:

```env
MEMORY_ENABLED=false
MEMORY_DB_PATH=./data/memoflow_memory.sqlite3
MEMORY_DEFAULT_HARNESS=coding
MEMORY_INGEST_ASYNC=true
MEMORY_COMPACTION_ENABLED=true
MEMORY_RETRIEVAL_TOP_K=8
MEMORY_CONTEXT_TOKEN_BUDGET=4000
```

Default should be conservative:

- memory disabled until implementation is verified
- explicit opt-in during development

## 14. Risks

### 14.1 LLM extraction quality

Risk:

- noisy or hallucinated memory atoms

Mitigation:

- require evidence episode ids
- start with conservative extraction prompt
- ADD-only writes
- hash dedupe

### 14.2 Prompt pollution

Risk:

- retrieved memory degrades answer quality

Mitigation:

- context assembler section budgets
- retrieval event debugging
- disable memory flag

### 14.3 Tool protocol breakage during compaction

Risk:

- assistant tool call and tool result get separated

Mitigation:

- compact only complete turns
- add unit tests for tool-call boundary preservation

### 14.4 Overengineering before signal

Risk:

- building graph/temporal features before the simple memory loop proves useful

Mitigation:

- v0.1 explicitly excludes graph traversal and advanced contradiction handling

## 15. First Milestone

The first milestone should be:

```text
Persistent episodes + inspectable short-term state
```

This should be implemented before memory atom extraction.

Reason:

- it gives us durable evidence
- it gives us replay/debugging
- it establishes the foundation for every later memory feature

## 16. Summary

v0.1 turns Memoflow from a pure ReAct chat backend into a memory-ready backend.

The key first-stage additions are:

- persist the conversation as episodes
- manage short-term context explicitly
- introduce task-specialized harness configuration
- extract simple ADD-only long-term memory atoms
- retrieve and inject memory through a controlled assembler

This keeps the implementation grounded while preserving the architecture needed for later SOTA features.
