# Memoflow Agent Memory Architecture

Version: 0.1  
Status: Draft for implementation  
Owner: Memoflow core backend  
Workdir: `d:\Joestar\jojorn\memoflow`

## 1. Document Purpose

This document defines the software architecture for Memoflow's agent memory system.

The target is not a demo memory layer and not a generic vector wrapper. The target is a production-grade memory kernel for agent systems with:

- short-term context management under strict token budgets
- long-term memory construction with typed, auditable records
- retrieval and loading that is query-aware and scope-aware
- explicit support for ReAct-style agents and coding agents
- operational characteristics that can survive real workloads

This document is written as an implementation blueprint. Every section is intended to map to concrete code modules, storage schemas, APIs, and rollout phases.

## 2. Design Goals

### 2.1 Primary goals

1. Preserve working context across turns without blowing the prompt budget.
2. Build durable long-term memory from interactions, tools, and outcomes.
3. Retrieve only the minimum relevant memory required for the current turn.
4. Keep all memory auditable back to source turns, tool calls, and artifacts.
5. Support contradiction, revision, and time validity instead of blindly overwriting facts.
6. Support both user memory and project/repository/task memory.

### 2.2 Non-goals for v1

- fully autonomous self-editing memory graphs
- multimodal memory
- enterprise multi-tenant permissioning beyond basic scope isolation
- speculative memory synthesis without evidence links
- black-box “auto memory” behavior that cannot be debugged

## 3. Core Architectural Position

Memoflow will combine five proven patterns from existing SOTA systems:

- Mem0 style memory atom extraction: extract structured facts from turns instead of embedding whole transcripts.
- Graphiti/Zep style entity and temporal relations: represent durable relationships and changes over time.
- Letta style split between core resident memory and archival retrievable memory.
- LangMem style short-term summarization and token-budget management.
- Hindsight style procedural reflection: remember what works, what fails, and under what conditions.

The resulting architecture is not a copy of any one system. It is a layered memory operating model optimized for engineering agents.

## 4. System Boundaries

### 4.1 In scope

- chat turn ingestion
- tool trace capture
- episode persistence
- memory extraction and classification
- memory storage and indexing
- memory retrieval and context assembly
- short-term compression and rolling summaries
- contradiction tracking and supersession
- evaluation and regression harness

### 4.2 Out of scope

- external product UI
- notification systems
- org-level identity providers
- generalized workflow orchestration

## 5. High-Level Architecture

```text
User / Agent Input
    ->
Turn Ingestor
    ->
ShortTermContextManager
    ->
MemoryRetriever
    ->
ContextAssembler
    ->
LLM / ReAct Runtime
    ->
Tool Execution + Final Answer
    ->
Episode Store
    ->
MemoryIngestor
    ->  SemanticMemoryStore
    ->  ProceduralMemoryStore
    ->  EntityGraphStore
    ->  Profile Store
```

### 5.1 Runtime path

The live response path must remain fast and deterministic:

1. load short-term state
2. retrieve relevant long-term memory
3. assemble context under token budget
4. execute turn
5. persist episode
6. asynchronously update long-term memory

### 5.2 Background path

Long-term memory construction runs asynchronously:

1. parse completed turn and tool trace
2. extract candidate memory atoms
3. classify and normalize
4. deduplicate
5. link entities and relations
6. detect contradictions and supersessions
7. write indexes and graph edges
8. update profile snapshots

This separation is mandatory. Long-term memory processing must not sit in the critical path of user response generation.

## 6. Memory Taxonomy

Memoflow will treat memory as multiple distinct classes, not one blob.

### 6.1 Short-term memory

Scope: current thread or current task window  
Purpose: maintain immediate context for the next few turns

Components:

- recent turns
- running conversation summary
- task state summary
- unresolved decisions
- compact tool trace summary

### 6.2 Episodic memory

Scope: durable, auditable turn-level event history  
Purpose: provide source evidence and support replay/debugging

Examples:

- raw user turn
- assistant action decision
- tool call/result pair
- generated artifact path
- error event

### 6.3 Semantic memory

Scope: stable facts and constraints  
Purpose: answer “what is true”

Examples:

- user preference
- repository constraint
- environment requirement
- current project architecture fact
- dependency version constraint

### 6.4 Procedural memory

Scope: durable action patterns and lessons  
Purpose: answer “how should the agent do this”

Examples:

- preferred debugging sequence
- command safety rule
- known failing workflow
- required startup order
- coding convention discovered from repo behavior

### 6.5 Entity-relational memory

Scope: entities, aliases, relations, temporal states  
Purpose: answer “who/what is connected to what, and when”

Examples:

- project X depends on service Y
- current active model for bridge is Z
- old secret replaced by new secret
- this repo’s chat backend is bound to this Feishu bridge

### 6.6 Core profile

Scope: very small resident memory kept prompt-side  
Purpose: keep essential identity and stable context always available

Examples:

- current workdir
- project mission
- current active system objective
- stable user operating preference

## 7. Data Model

### 7.1 Episode

An episode is the atomic unit of observed interaction.

Suggested fields:

```text
Episode
- id
- scope_type: user | project | repo | agent | session
- scope_id
- session_id
- turn_id
- source: web | cli | feishu | api
- user_message
- assistant_final
- tool_trace
- files_touched[]
- artifacts[]
- started_at
- completed_at
- metadata
```

### 7.2 MemoryAtom

MemoryAtom is the canonical durable memory record.

```text
MemoryAtom
- id
- type: semantic | procedural | preference | project_state | warning | relation
- scope_type: user | project | repo | agent | session | org
- scope_id
- content
- normalized_content
- evidence_episode_ids[]
- evidence_message_ids[]
- evidence_tool_call_ids[]
- entities[]
- keywords[]
- embedding
- confidence
- importance
- created_at
- observed_at
- valid_from
- valid_until
- version
- status: active | superseded | contradicted | expired
- supersedes[]
- contradicts[]
- hash
- metadata
```

### 7.3 Entity

```text
Entity
- id
- canonical_name
- entity_type
- aliases[]
- scope_type
- scope_id
- embedding
- metadata
- created_at
- updated_at
```

### 7.4 Relation

```text
Relation
- id
- subject_entity_id
- relation_type
- object_entity_id
- scope_type
- scope_id
- confidence
- observed_at
- valid_from
- valid_until
- source_episode_ids[]
- status
- metadata
```

### 7.5 CoreProfile

```text
CoreProfile
- scope_type
- scope_id
- identity_summary
- stable_preferences
- active_project_state
- current_objectives
- updated_at
```

## 8. Storage Strategy

No single store is enough.

### 8.1 Recommended v1 stores

- relational store: PostgreSQL
- vector index: pgvector or Qdrant
- keyword index: PostgreSQL full-text or SQLite FTS5 for local mode
- graph layer: relational tables first, graph database optional later

### 8.2 Store ownership

- Episodes: relational only
- MemoryAtom: relational + vector + full-text
- Entity: relational + vector
- Relation: relational
- CoreProfile: relational JSON document

### 8.3 Why not start with a graph database

Because the first bottleneck is not graph traversal performance. The first bottleneck is correctness of extraction, scope isolation, indexing, and loading policy. Relations can sit in relational tables initially. A dedicated graph engine becomes justified only after:

- relation count is large enough
- temporal traversal is frequent
- multi-hop graph retrieval is validated by benchmarks

## 9. Short-Term Context Compression

Short-term context must be explicit and deterministic.

### 9.1 Compression policy

Keep:

- the most recent 4 to 8 full user turns
- all messages from the active unresolved tool cycle
- current task state summary
- compact running summary of earlier turns

Compress:

- older conversation messages
- verbose tool results
- repeated assistant explanations

Never break apart:

- assistant tool_calls and matching tool messages
- unresolved error investigation context
- active file paths and artifact references for the current task

### 9.2 Running summary model

Maintain three summaries, not one:

1. conversation summary
2. task state summary
3. open issues / decisions summary

This avoids one common failure mode where a generic summary loses actionable engineering state.

### 9.3 Tool trace compression

A tool trace summary record should include:

- tool name
- intent
- argument summary
- result summary
- files changed
- artifact paths
- error type
- retry/recovery outcome

Do not persist full raw tool stdout into the prompt path unless explicitly needed.

### 9.4 Summary coverage bookkeeping

Every summary update must record which message ids it covers.

```text
SummaryCoverage
- summary_id
- covered_message_ids[]
- generated_at
```

This is required to avoid repeated summarization drift and duplicate context.

## 10. Long-Term Memory Construction

### 10.1 Write model

Memoflow v1 will use append-first memory construction.

That means:

- new memory is added as a new record
- old memory is not overwritten in place
- contradictions and revisions are represented explicitly

This is the only safe starting point for a production agent memory system.

### 10.2 Extraction pipeline

After each completed episode:

1. extract candidate facts
2. extract candidate procedures
3. extract candidate entities and relations
4. classify memory type
5. normalize text
6. compute hash and dedupe
7. score importance/confidence
8. persist and index

### 10.3 Extraction rules

Write memory only if one of the following is true:

- it is likely to matter in future turns
- it changes the project state
- it records a stable preference or constraint
- it records a reusable procedure
- it records a failure pattern worth avoiding

Do not write memory for:

- generic chit-chat
- transient filler
- repeated paraphrases of existing memory
- unsupported inferences without evidence

### 10.4 Contradiction and supersession

If a new atom conflicts with an old atom:

- keep both
- mark the old atom as superseded or contradicted
- attach evidence links
- prefer newer higher-confidence memory at retrieval time

This design is mandatory for environments where configuration and project state change over time.

## 11. Retrieval Architecture

Retrieval must be multi-signal.

### 11.1 Retrieval inputs

For each turn, retrieval consumes:

- current user message
- active task summary
- recent entities mentioned
- scope context
- currently active files/artifacts

### 11.2 Retrieval channels

Run these in parallel:

1. semantic vector retrieval
2. keyword / full-text retrieval
3. entity exact and alias lookup
4. relation expansion from matched entities
5. recent high-importance project state retrieval
6. procedural memory retrieval
7. core profile load

### 11.3 Ranking signals

Final ranking should combine:

- semantic similarity
- lexical match
- scope match
- entity overlap
- recency
- confidence
- importance
- contradiction penalty
- procedural priority for task-oriented turns

### 11.4 Retrieval output classes

The retriever should not return a flat list. It should return categorized memory:

```text
RetrievalBundle
- core_profile
- task_state
- semantic_facts[]
- procedures[]
- entities[]
- relations[]
- evidence_snippets[]
- warnings[]
```

## 12. Context Assembly

Context assembly is the final control point before prompt injection.

### 12.1 Assembly layout

Recommended order:

1. system/developer prompts
2. core profile
3. active task state
4. relevant semantic memory
5. relevant procedural memory
6. critical warnings and contradictions
7. recent turns
8. active tool trace

### 12.2 Budget policy

The assembler must allocate a fixed token budget by section.

Example:

- core profile: 8%
- task state: 15%
- semantic memory: 20%
- procedural memory: 15%
- warnings: 5%
- recent turns: 27%
- active tool trace: 10%

Budget percentages should be configurable per provider/model.

### 12.3 Compression fallback order

When over budget:

1. compress evidence snippets
2. compress semantic memory wording
3. reduce low-confidence facts
4. reduce low-priority procedures
5. compress older recent turns
6. never drop active warning records for the current task

## 13. Module Architecture

The memory system should be implemented as independent modules.

### 13.1 Required modules

`ShortTermContextManager`
- manages recent turns
- rolling summaries
- tool trace compaction

`EpisodeStore`
- writes auditable episodes
- retrieves evidence

`MemoryIngestor`
- extracts and writes memory atoms
- runs async

`SemanticMemoryStore`
- stores facts and preferences

`ProceduralMemoryStore`
- stores reusable methods and lessons

`EntityGraphStore`
- stores entities and relations

`ProfileManager`
- maintains core resident profile

`MemoryRetriever`
- performs multi-signal retrieval

`ContextAssembler`
- converts memory bundle into prompt-ready context

`MemoryEvaluator`
- runs offline benchmarks and regression tests

### 13.2 Suggested service boundaries in this repo

For this backend, the likely home is:

- `services/memory/short_term.py`
- `services/memory/episodes.py`
- `services/memory/ingestor.py`
- `services/memory/retriever.py`
- `services/memory/assembler.py`
- `services/memory/profile.py`
- `services/memory/evaluator.py`
- `services/memory/stores/*.py`

## 14. APIs

### 14.1 Internal runtime API

```text
prepare_memory_context(turn_input) -> RetrievalBundle
record_episode(turn_output) -> episode_id
ingest_episode(episode_id) -> ingestion_result
retrieve_memory(query, scope) -> RetrievalBundle
update_short_term(session_id, turn) -> ShortTermState
```

### 14.2 Operational APIs

```text
POST /api/memory/ingest
POST /api/memory/search
GET  /api/memory/{id}
GET  /api/memory/episodes/{id}
POST /api/memory/profile/rebuild
POST /api/memory/evaluate
```

### 14.3 Administrative APIs

```text
POST /api/memory/reindex
POST /api/memory/reconcile
POST /api/memory/expire
POST /api/memory/mark-superseded
```

## 15. Scope Model

Scope isolation is not optional.

Memoflow memory must support:

- user scope
- project scope
- repo scope
- agent scope
- session scope

Resolution order for retrieval should generally be:

1. session
2. repo
3. project
4. user
5. agent

This ordering can be task-specific, but the default should privilege immediate working context over broad historical memory.

## 16. Evaluation Strategy

Without evaluation, memory systems decay into anecdotes.

### 16.1 Offline evaluation

Use benchmark-inspired suites for:

- long conversation recall
- temporal consistency
- contradiction handling
- procedural recall
- project-state continuity

Recommended external references:

- LongMemEval style long-context retention
- LoCoMo style multi-hop and temporal reasoning
- custom coding-agent tasks for repo-specific continuity

### 16.2 Online evaluation

Track:

- retrieval hit rate
- prompt token cost by section
- memory write volume
- duplicate rate
- contradiction rate
- hallucination rate after memory injection
- latency added by retrieval and assembly

### 16.3 Acceptance criteria for production use

- short-term compression does not break tool protocol correctness
- long-term retrieval improves task completion on benchmark tasks
- memory write amplification remains bounded
- contradiction handling is explicit and testable
- memory injection stays within defined latency budgets

## 17. Operational Requirements

### 17.1 Reliability

- all writes idempotent where possible
- reindexable from episode store
- memory atom generation should be replayable
- degraded mode must allow chat without long-term retrieval

### 17.2 Safety

- secrets must not be extracted into long-term memory by default
- transient auth tokens must be blocked from storage
- raw credential patterns require hard filters before persistence

### 17.3 Debuggability

Every injected memory item must be explainable:

- why it was retrieved
- which scores contributed
- what source episode supports it
- whether it superseded another memory

If the system cannot explain a memory injection, it is not production-ready.

## 18. Implementation Plan

### Phase 1: Working memory kernel

Deliver:

- episode store
- short-term running summary
- semantic memory atoms
- vector + FTS retrieval
- basic context assembler

Do not deliver yet:

- graph traversal
- contradiction resolution UI
- procedural reflection

### Phase 2: Durable long-term layer

Deliver:

- procedural memory
- core profile
- contradiction/supersession logic
- entity and relation tables
- async ingestion workers

### Phase 3: Advanced retrieval

Deliver:

- graph-aware retrieval
- temporal relation reasoning
- fused ranking improvements
- memory evaluation harness

### Phase 4: Memory operating system capabilities

Deliver:

- multi-agent shared scopes
- memory lifecycle policies
- decay and expiry
- correction and reconciliation workflows

## 19. Engineering Lessons From Claw Code

`claw-code` is useful to Memoflow primarily as a short-term memory and session-engineering reference.

It does not provide the full long-term external memory architecture that Memoflow requires. In particular, it does not give us a complete solution for:

- typed long-term memory atoms
- durable semantic/procedural/entity memory stores
- hybrid long-term retrieval
- temporal contradiction and supersession handling

What it does demonstrate well is how to engineer short-term working context so that it remains inspectable, resumable, and operationally safe.

### 19.1 What Claw Code gets right

#### A. Session state is durable, explicit, and resumable

Useful lesson:

- raw session state should be persisted as first-class runtime state
- resume must restore a real working session, not rely on hidden in-memory prompt assembly

Why this should be adopted:

- the agent must survive process restarts, bridge restarts, and CLI reconnects
- debugging is impossible if session state only exists as hidden prompt residue
- resumability is a prerequisite for short-term continuity

How Memoflow should apply it:

- persist short-term thread state independently from long-term memory
- expose session id, short-term summary version, and recent-turn window as inspectable state
- make session recovery deterministic

#### B. Session transcript is not the same thing as durable memory

Useful lesson:

- a raw session log is a source of truth for replay and evidence
- it should not be treated as the final memory abstraction

Why this should be adopted:

- transcripts are noisy
- direct transcript replay wastes tokens
- direct transcript embedding produces low-quality long-term recall

How Memoflow should apply it:

- keep episodes/transcripts for audit and replay
- derive short-term summaries from transcripts
- derive long-term memory atoms from episodes asynchronously

#### C. Context compression should be an explicit subsystem

Useful lesson:

- context compaction must be a named, inspectable operation, not an invisible side effect

Why this should be adopted:

- hidden summarization causes silent context drift
- if context changes are not inspectable, failures are not diagnosable
- short-term memory quality depends on stable compaction rules

How Memoflow should apply it:

- implement a dedicated `ShortTermContextManager`
- version every compaction result
- record which message ids were compacted into which summary
- expose compaction diagnostics through internal APIs

#### D. Machine-readable state is better than opaque prompt state

Useful lesson:

- working state should be structured and queryable
- failure modes should be represented in machine-readable state, not only in natural language

Why this should be adopted:

- agents need deterministic runtime recovery
- tooling needs to reason over state transitions
- debugging and testing require stable state artifacts

How Memoflow should apply it:

- represent short-term state as structured objects:
  - recent turn ids
  - active tool cycle
  - current task summary
  - unresolved issues
  - known warnings
- do not rely on free-form summary text alone

#### E. Project memory and session memory must be separated

Useful lesson:

- repository/project instructions are a distinct layer from live conversational state

Why this should be adopted:

- stable project rules and active turn context have different lifecycles
- mixing them creates stale prompt state and poor retrieval behavior

How Memoflow should apply it:

- keep `CoreProfile` and project constraints separate from the rolling session summary
- inject project memory as a stable section
- inject session memory as a volatile section

#### F. Operational state should stay outside model memory unless task-relevant

Useful lesson:

- not everything observed by the system belongs in the model context window

Why this should be adopted:

- notifications, bridge status, telemetry, and transport events are usually not task memory
- polluting short-term memory with operational noise reduces response quality

How Memoflow should apply it:

- keep telemetry and delivery state outside prompt assembly by default
- only elevate operational state into memory if it affects the active task
- treat “task relevance” as an explicit filter in context assembly

### 19.2 Concrete short-term memory practices Memoflow should adopt

The following practices are directly actionable and should be part of implementation.

#### Practice 1. Keep recent turns verbatim, compact older turns

Implementation:

- preserve the last N complete user turns verbatim
- compact older turns into running summaries
- never compact across an active tool cycle boundary

Reason:

- this keeps immediacy for the current task while stabilizing token usage

#### Practice 2. Maintain separate summaries for different purposes

Implementation:

- maintain:
  - conversation summary
  - task state summary
  - open decisions summary

Reason:

- one generic summary loses engineering state and makes recovery weaker

#### Practice 3. Persist compaction coverage

Implementation:

- every compaction operation stores:
  - source message ids
  - generated summary id
  - generation time
  - model/version used

Reason:

- without coverage tracking, summaries will drift and double-count prior state

#### Practice 4. Make short-term state inspectable and exportable

Implementation:

- provide an internal view/export of:
  - recent turns
  - summary blocks
  - active task state
  - unresolved items
  - current token budget usage

Reason:

- if short-term memory cannot be inspected, it cannot be debugged or trusted

#### Practice 5. Separate prompt assembly from state persistence

Implementation:

- persist structured short-term state first
- assemble prompt sections from that state second

Reason:

- this avoids hidden coupling between storage and prompt formatting
- the same state can serve debugging, evaluation, and replay

### 19.3 What Memoflow should not copy from Claw Code

There are limits to the reference value.

- do not stop at session persistence; Memoflow still needs long-term external memory
- do not treat project instruction files as a replacement for typed memory stores
- do not rely on transcript replay as the retrieval mechanism
- do not leave procedural memory implicit inside past conversations

### 19.4 Final judgment

Claw Code is not a blueprint for long-term memory.

It is a strong reference for how short-term working context should be engineered:

- explicit
- resumable
- inspectable
- structured
- separated from stable project memory

Memoflow should absorb these engineering lessons into the short-term memory layer and then build the long-term memory kernel on top of them.

## 20. Task-Specialized Memory Harness

Memoflow should not expose a single universal memory strategy.

The correct architectural model is:

- one shared memory kernel
- multiple task-specialized memory harnesses

This follows a practical engineering constraint: different agent workloads require different memory behavior.

A coding agent, a research agent, and a general conversation agent should not:

- compact context the same way
- write memory at the same thresholds
- prioritize the same memory types
- retrieve memory with the same ranking weights
- inject memory into prompts with the same layout

### 20.1 Definition

A MemoryHarness is the task-level policy layer that controls how the shared memory kernel behaves for a given workload.

Suggested structure:

```text
MemoryHarness
- name
- task_type
- short_term_policy
- ingestion_policy
- schema_policy
- retrieval_policy
- assembly_policy
- evaluation_suite
- version
```

This layer does not replace the kernel. It configures it.

### 20.2 Why this layer is required

Without a harness layer, memory architecture tends to collapse into a lowest-common-denominator design.

That produces predictable failures:

- conversation tuning hurts coding-task recall
- coding-task procedural memory pollutes casual chat
- retrieval budgets become too generic to be effective
- compaction rules erase task-critical state

The harness layer prevents this by making memory behavior task-aware.

### 20.3 Harness responsibilities

Each harness should define:

#### A. Short-term policy

- recent turn window size
- compaction trigger threshold
- summary structure
- tool trace retention rules
- unresolved issue retention rules

#### B. Ingestion policy

- what qualifies as write-worthy memory
- whether to favor semantic, procedural, or relational extraction
- whether writes happen on every turn or only on turn completion
- what data must be filtered out

#### C. Schema policy

- which memory atom types are enabled
- which metadata fields are required
- which scopes are valid
- what evidence quality threshold is required

#### D. Retrieval policy

- which retrieval channels are active
- top-k per channel
- score fusion weights
- contradiction penalty
- recency bonus
- procedural vs factual priority

#### E. Assembly policy

- prompt section order
- token budget per section
- compression fallback order
- whether evidence snippets are injected
- whether warnings always survive trimming

#### F. Evaluation policy

- which benchmark suite to run
- which task fixtures to use
- which acceptance metrics define success

### 20.4 Default harnesses for Memoflow

Memoflow should ship with at least four default harnesses.

#### 1. Conversation harness

Primary goal:

- preserve user continuity and stable preferences

Behavior:

- prioritize semantic memory and profile
- lower procedural weight
- aggressive compaction of verbose tool traces
- favor preference and project-state recall

#### 2. Coding harness

Primary goal:

- preserve actionable engineering state across long tasks

Behavior:

- prioritize procedural memory, project constraints, warnings, and active file state
- preserve unresolved issue lists
- preserve tool trace summaries and changed-file references
- use stricter contradiction handling for environment facts

#### 3. Planning harness

Primary goal:

- preserve decomposition state, milestones, decisions, and dependencies

Behavior:

- prioritize task-state memory and open decisions
- compact raw turns more aggressively
- promote dependency and timeline relations
- inject structured plan state before semantic recall

#### 4. Research harness

Primary goal:

- preserve sources, claims, comparisons, and uncertainty

Behavior:

- prioritize evidence snippets and source-linked semantic memory
- keep contradiction and confidence signals prominent
- preserve claim-to-source associations
- downrank unsupported memory without evidence

### 20.5 Example harness deltas

The same kernel should behave differently depending on harness.

Example:

```text
conversation_harness
- recent_turns: 6
- summary_blocks: conversation, preferences, active topic
- retrieval_priority: profile > semantic > recent entities > procedures

coding_harness
- recent_turns: 8
- summary_blocks: task state, unresolved issues, file/artifact state
- retrieval_priority: procedures > project_state > warnings > semantic > relations

planning_harness
- recent_turns: 5
- summary_blocks: milestones, open decisions, dependency map
- retrieval_priority: task_state > relations > semantic > procedures
```

This is the correct way to share infrastructure while allowing behavior to differ by workload.

### 20.6 Configuration model

Harnesses should be implemented as versioned configuration plus code hooks.

Suggested split:

- declarative config for thresholds, weights, budgets, enabled channels
- code hooks for specialized extraction or assembly behavior

Example:

```text
HarnessConfig
- id
- version
- enabled_memory_types[]
- recent_turn_limit
- compaction_token_threshold
- retrieval_weights
- assembly_budgets
- required_evidence_level
- default_scope_order[]
```

This gives two critical advantages:

- benchmark tuning without refactoring the whole kernel
- explicit rollback when a harness change regresses quality

### 20.7 Integration with the shared kernel

The shared kernel remains stable:

- EpisodeStore
- ShortTermContextManager
- SemanticMemoryStore
- ProceduralMemoryStore
- EntityGraphStore
- ProfileManager
- MemoryRetriever
- ContextAssembler

The harness sits above these components and controls their runtime behavior.

Recommended call shape:

```text
prepare_memory_context(turn_input, harness)
record_episode(turn_output, harness)
ingest_episode(episode_id, harness)
retrieve_memory(query, scope, harness)
assemble_context(bundle, harness)
```

### 20.8 Evaluation implications

The harness layer only matters if it is benchmarked separately.

Memoflow should evaluate each harness independently.

Examples:

- conversation harness: user continuity, preference recall, personalization
- coding harness: long task continuity, procedural recall, environment correctness
- planning harness: dependency retention, milestone continuity, decision consistency
- research harness: source attribution, contradiction handling, claim recall

This is operationally important. A harness that helps one workload can easily degrade another.

### 20.9 v1 implementation rule

Memoflow v1 should implement the harness abstraction now, even if harness selection is initially static.

That means:

- one default harness selected per session/task
- no automatic harness evolution yet
- no search over memory programs yet
- but all major policies must already be represented through the harness layer

This avoids a future rewrite.

### 20.10 Final engineering judgment

The harness abstraction is the missing layer between memory theory and deployable agent systems.

For Memoflow, the practical rule is:

- memory kernel provides durable capability
- memory harness provides task-specific behavior

This separation is necessary if Memoflow is expected to support advanced agents instead of a single narrow workflow.

## 21. Immediate Next Engineering Decisions

The next concrete decisions for this repository are:

1. relational store choice: SQLite for local mode, PostgreSQL for production mode
2. vector backend choice: pgvector or Qdrant
3. memory ingestion worker model: in-process async task vs external queue worker
4. short-term summarizer interface and token budget policy
5. memory atom schema versioning strategy

These decisions must be locked before implementation starts.

## 22. Final Position

Memoflow should be built as a memory kernel, not a convenience wrapper.

The architecture must preserve three invariants:

1. every durable memory is evidence-backed
2. every retrieval is scope-aware and scoreable
3. every injected context respects strict token and protocol constraints

If these invariants hold, Memoflow can grow from a local memory-enabled agent backend into a production-grade memory infrastructure for advanced agents.
