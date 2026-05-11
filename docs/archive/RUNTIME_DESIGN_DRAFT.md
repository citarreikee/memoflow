# MemoryRuntime Mechanism Design — Draft v0.2

> v0.1 已于 2026-05-03 送审，用户给出10条反馈。v0.2 从第一性原理重设计，标记了已知妥协和演进方向。

---

## 0. v0.1 → v0.2：根本修正

v0.1 有三个核心错误，均已被 deep-dive 中的设计原则否定：

| 错误 | deep-dive 里对应的正确原则 | 根因 |
|------|--------------------------|------|
| "每 turn 都做语义提取" | **Compression must be offline** | 把 offline consolidation 当成了 per-turn 操作 |
| "每 turn 都注入所有 file memory" | **Selection + budget constraint** | 把"可访问"等同于"已加载" |
| "compaction 在热路径中同步阻塞" | **Lazy compression, src-first** | 没有把写/转换操作移到热路径之外 |

v0.2 的设计核心从"每 turn 做什么"变成"**什么条件下触发什么操作**"。

---

## 1. 从第一性原理出发

物理约束只有一个：

> **Context Window 是有限的计算预算（compute budget），不是存储空间。**

从这个约束推导出 Runtime 需要回答的五个问题：

```
Q1. 每个 turn，context 里放什么？          → Assembly（热路径）
Q2. 什么条件下触发重活（压缩/提取/整理）？  → Trigger（决策逻辑）
Q3. 重活怎么执行？                        → Background Worker（执行机制）
Q4. 重活的结果如何反哺 context？           → State Update（缓存更新）
Q5. 系统如何长期演化？                     → Evolution（生命周期）
```

这五个问题对应五个接口，不是五个模块。

---

## 2. 架构：三层时间尺度

Memory 操作天然分布在三个时间尺度上。强行合并就会出错。

```
Layer 1: Per-Turn（毫秒级，热路径）
  ├── 组装 context（从缓存读，零 I/O）
  ├── 持久化原始消息（同步写 SQLite，~5ms）
  └── 评估是否需要触发后台任务（决策逻辑，纯算力）

Layer 2: Deferred（秒级，后台异步）
  ├── 语义提取：积累 N 个 turn 后，批量调一次轻量 LLM
  ├── Compaction：达到 token 阈值后，调 sidecar 模型
  └── 两者独立队列，不互阻

Layer 3: Offline（分钟级，后台周期）
  ├── Consolidation：去重、合并语义记忆
  ├── Evolution：event → insight 升级、冲突检测
  └── Cleanup：过期记忆遗忘、索引重建
```

**为什么是三层？**
- Layer 1 和 Layer 2 的分离是最低要求——v0.1 没做对这一点
- Layer 2 和 Layer 3 的分离是必要的——避免 120s 的 compaction 阻塞 30s 的 extraction
- 三层对应的调度策略不同：per-turn（确定性）、deferred（阈值触发）、offline（周期或 idle 触发）

---

## 3. State Model

```
SessionMemoryState（每 session 一个，纯内存）
├── session_id
├── checkpoint
│   ├── data: Dict | None
│   ├── version: int
│   └── pending: bool           # compaction job 是否在运行
├── semantic
│   ├── items: List[SemanticMemory]
│   ├── version: int
│   ├── last_extraction_turn: int  # 上次提取覆盖到的 turn index
│   └── dirty_turns_since: int     # 自上次提取以来的未处理 turn 数
└── file_memory
    ├── items: List[{path, content, mtime}]
    └── checked_at: float
```

规则不变：
- `assemble_context` 只读 state
- Background worker 只写 state
- `commit_turn` 只更新 flags（`pending`, `dirty_turns_since`）

---

## 4. 热路径：assemble_context

### 签名
```
assemble_context(
    session: Session,
    user_message: str,
    provider: str,
    workspace_dir: str,
) → ContextPacket
```

### ContextPacket
```
ContextPacket
├── messages: List[Dict]      # 注入 LLM 的最终 prompt
├── token_estimate: int
├── token_budget: int
├── needs_compaction: bool    # 供 commit_turn 用的标记
├── needs_extraction: bool    # 供 commit_turn 用的标记
└── debug: Dict
```

### 组装顺序（不变）
```
Layer 0: Pinned prompts        (system + developer, 固定)
Layer 1: Checkpoint            (如果有且未 pending)
Layer 2: Semantic memory block (如果有新提取的记忆且与当前 query 相关)
Layer 3: File memory block     (如果有 workspace 文件且内容与当前对话主题有重叠)
Layer 4: Recent raw history    (最近 N 个 turn 的原文)
```

### Layer 2 触发条件（修正 v0.1 的"总是注入"）

```
If semantic.items is empty:
    → skip Layer 2

Else:
    → Filter: keep items where (keyword_overlap(item.content, user_message) > 0
                                 OR item.created_at is within last 3 turns)
    → Sort: score = confidence × recency_decay(item.created_at)
    → Select: top K where total chars < SEMANTIC_MAX_INJECTED_CHARS (default: 800)
    → If none pass filter → skip Layer 2
```

**为什么不用 embedding？** 当前阶段语义记忆量小（<100条/ session），keyword overlap 作为轻量过滤够用。

**[COMPROMISE]** keyword overlap 对中文效果差于英文，且无法捕获语义相关但不同词的记忆。当语义记忆超过 200 条时，需要替换为 embedding-based 检索。过渡期可接受。

### Layer 3 触发条件（修正 v0.1 的"总是注入"）

```
If not workspace_dir 或 file_memory Cache 为空:
    → skip Layer 3

Else:
    → For each cached file:
        relevance = keyword_overlap(file.content, user_message) > 0
        OR file was modified within last 24h
    → If relevance:
        inject full content (capped at FILE_MEMORY_MAX_CHARS_PER_FILE)
    → Else:
        inject only file path as reference hint (1 line)
```

**为什么不全量注入？** 用户的反馈是：无关文件内容浪费上下文窗口，淹没有用信息。改为：有重叠才注入全文，无重叠只注入路径作为"可访问提示"。

**[COMPROMISE]** "keyword overlap" 作为 relevance proxy 不够精确。未来应替换为：
- 文件摘要缓存（文件大时先做摘要）
- 或基于最近 N 个 turn 的上下文做 embedding 匹配
- 当前阶段文件量小（2个文件），碰撞概率低，可接受。

### Layer 4 的 token 预算分配

```
remaining_budget = token_budget - tokens_used_by(Layer0 + Layer1 + Layer2 + Layer3)
history_turns = group_messages_by_user_turn(recent_raw_history)
for turn in oldest_first(history_turns):
    if estimate_tokens(assembled + turn) <= remaining_budget:
        assembled += turn
    else:
        break
```

**为什么从最旧到最新？** 保留最近的完整原文，丢弃最旧的（因为它们已经有 checkpoint 覆盖或即将被 compaction 覆盖）。

### needs_compaction 标记

```
If (estimated_tokens > token_budget * COMPACTION_TRIGGER_RATIO
    AND turn_count >= COMPACTION_MIN_TURNS
    AND checkpoint.pending == False):
    → needs_compaction = True
```

### needs_extraction 标记

```
If (state.semantic.dirty_turns_since >= EXTRACTION_BATCH_MIN_TURNS  # default: 5
    OR user_message contains explicit remember/note/save intent):
    → needs_extraction = True
```

**为什么是批量触发？** v0.1 每 turn 都做提取（成本高、浪费）。正确的做法是积累足够多的未处理内容后再批量提取。一次 LLM 调用处理 N 个 turn，比 N 次调用分别处理更高效。

---

## 5. 冷路径：commit_turn

### 签名
```
commit_turn(session, context_packet, turn_episode) → None
```

### 同步执行（必须在 response 返回前完成）

1. Persist episode + messages to SQLite (~5ms)
2. Update `state.semantic.dirty_turns_since += 1`
3. If `needs_compaction`: set `state.checkpoint.pending = True`, **persist compaction task to SQLite**
4. If `needs_extraction`: no state change needed (job will read `dirty_turns_since`)

### 异步执行（fire-and-forget，不阻塞 response）

1. If `needs_compaction`: enqueue `CompactionJob` to `compaction_queue`
2. If `needs_extraction`: enqueue `ExtractionJob` to `extraction_queue`

**为什么 needs_compaction 要持久化到 SQLite？** 用户反馈：进程崩溃时内存 flag 丢失，可能导致永远不触发 compaction。改为写 SQLite pending_tasks 表，启动时恢复。

---

## 6. 后台 Worker：双队列设计

v0.1 是单队列单 worker。用户指出：慢 compaction（120s）会阻塞快 extraction（30s）。v0.2 改为双队列。

```
compaction_queue  ─── compaction_worker (1 worker)
extraction_queue  ─── extraction_worker (1 worker)
```

### Job 类型

```
CompactionJob
├── session_id
├── older_turns: List[List[Dict]]
├── previous_checkpoint: Dict | None
└── target_tokens: int

ExtractionJob
├── session_id
├── turns: List[List[Dict]]      # batch of N unprocessed turns
├── since_turn_index: int         # starting turn index for this batch
└── episode_ids: List[str]
```

**[COMPROMISE]** 双队列仍然是每队列单 worker。在高并发会话下，一个慢 compaction 仍会阻塞同一会话的后续 compaction。极端情况下（10+ 并发会话同时触发 compaction），队列会堆积。

**改进方向：**
- Compaction worker 池化（N workers，configurable）
- Extraction 可以用更轻量的模型（甚至本地 embedding + rule extraction 混合），避免全部依赖 LLM

### Extraction Worker 的逻辑细节

```
job = await extraction_queue.get()

# Read the turns from SQLite (job.turns may be stale, re-read for safety)
turns = session_store.get_episodes_since(session_id, job.since_turn_index)

# Build extraction prompt with ALL turns in one call
prompt = build_extraction_prompt(turns)  # see Section 7

# Call lightweight model
result = await call_model(prompt, timeout=30s)

if result.success:
    new_memories = parse_and_validate(result.json)

    # Merge with existing: dedup by content hash
    existing_hashes = {m.content_hash for m in state.semantic.items}
    for m in new_memories:
        if hash(m.content) not in existing_hashes:
            m.content_hash = hash(m.content)
            state.semantic.items.append(m)

    state.semantic.version += 1
    state.semantic.last_extraction_turn = job.since_turn_index + len(turns)
    state.semantic.dirty_turns_since = 0

    # Persist to SQLite
    session_store.insert_semantic_memories(new_memories)
```

**为什么要 merge + dedup？** 用户指出 v0.1 的 `.extend()` 会导致同一偏好多次表达时产生重复记忆。改为按 content hash 去重。

**为什么用 content hash 做 ID？** 用户建议的"确定性 ID"方案。`memory_id = sha256(content + session_id)[:16]`，天然支持去重和跨 session 引用。

### Compaction Worker 的逻辑细节

```
job = await compaction_queue.get()

result = await compact_with_sidecar(job.older_turns, job.previous_checkpoint)

if result.success:
    state.checkpoint.data = result.summary
    state.checkpoint.version += 1
    state.checkpoint.pending = False

    # Persist to SQLite AND remove from pending_tasks
    session_store.save_checkpoint(...)
    session_store.delete_pending_task(job.session_id, "compaction")
else:
    state.checkpoint.pending = False  # 放锁，允许下次重试
    # Log error, don't crash worker
```

### Compaction pending 时的上下文超预算缓解策略

用户指出：pending 状态下用旧 checkpoint + 全部 raw turns 可能严重超预算。v0.2 增加：

```
If checkpoint.pending:
    → Use old checkpoint
    → Aggressive trim: keep only COMPACTION_KEEP_RECENT_TURNS (default: 4)
      raw turns, NOT all raw turns
    → Set Budget hard-cap: context total must stay within 1.2 × token_budget
    → If still over 1.2× after trimming to 4 turns:
        drop checkpoint message entirely, keep 2 most recent turns + pinned prompts
```

**为什么是 1.2x 而不是 tighter?** 偶尔超出预算是可接受的（大部分模型有 context window 余量）。但 >2x 会导致模型理解质量显著下降。

---

## 7. 语义提取机制（v0.2 修正版）

### 提取频率

v0.1：每 turn。**错误。**

v0.2：
- **批量提取**：积累 ≥5 个 dirty_turns 后触发
- **显式触发**：用户说"记住"/"保存"/"mark as important"
- **Session 结束时**：提取所有剩余未处理 turns

### 批量提取的 Prompt 设计

```
System: You are a memory extraction system. Review the following conversation
turns and extract structured memories. Return JSON only.

Turns:
---
Turn 1:
User: ...
Assistant: ...
Tool results: ...

Turn 2:
...
---

Extract memories that are:
- preference: user's stated like/dislike/constraint/habit
- fact: confirmed information, context, decisions
- pattern: recurring behavior or workflow

For each memory provide:
{ "type": "...", "content": "concise statement", "confidence": 0.0-1.0,
  "source_turn": N, "spanning": false }

Rules:
- confidence >= 0.6 only
- content should be a standalone fact (no pronoun references)
- If the same fact appears across multiple turns, mark spanning=true and use the first source_turn
- Return empty list if nothing worth extracting
- Max 10 memories per batch
```

### 数据结构

```python
@dataclass
class SemanticMemory:
    memory_id: str           # sha256(content + session_id)[:16]
    session_id: str
    content: str
    memory_type: str         # "preference" | "fact" | "pattern"
    confidence: float
    source_episode_id: str
    created_at: str
    updated_at: str
    content_hash: str        # sha256(content)[:16], for dedup
```

### 检索时的排序

```
score = confidence × recency_weight

where recency_weight = 0.5 ^ (age_in_turns / RECENCY_HALF_LIFE_TURNS)  # default half-life: 10 turns
```

**为什么不用纯 confidence 排序？** 用户指出：最近的、相关的记忆比早期高置信度记忆更可能对当前对话有用。recency decay 确保最新记忆有更高权重。

---

## 8. File Memory（v0.2 修正版）

### 始终缓存，按需注入

- Cache: 首次读取时将文件内容 + mtime 缓存到 SessionMemoryState
- Re-read: 仅当 mtime 变化
- Inject: 仅当文件内容与当前话题有关联时才注入全文，否则只注入文件名

### 注入格式

```
[Project memory available:
 - CLAUDE.md: <first 100 chars as preview>...
 - MEMORY.md: <first 100 chars as preview>...
Use memory tools to retrieve full content when needed.]
```

当 relevance 命中时：
```
[Project memory from CLAUDE.md (relevant to current topic):
<full content clipped to 4000 chars>]
```

---

## 9. 持久化与恢复

### pending_tasks 表

```sql
CREATE TABLE pending_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_type TEXT NOT NULL,   -- 'compaction'
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
```

### 启动恢复流程

```
on_startup:
    tasks = session_store.get_pending_tasks()
    for task in tasks:
        if task.type == "compaction":
            re-enqueue CompactionJob from task.payload
    # Extraction jobs don't need recovery (they're best-effort)
```

### SessionMemoryState 淘汰

```
# LRU-based eviction, not TTL-based
max_states = 1000  # configurable

on_access:
    move state to front of LRU list

on_evict:
    # Write checkpoint version + semantic version to SQLite
    # State can be reconstructed: checkpoint + semantic_memories + file_memory(mtime)
    # Always evict the least recently accessed
```

**[COMPROMISE]** 淘汰后恢复需要从 SQLite 全量重建 semantic_memories。当单 session 语义记忆超过 10K 条时，重建会慢。改进方向：按时间分段加载（最近 200 条 + 按需检索更旧的）。

---

## 10. Impact on Existing Code

| File | Change |
|------|--------|
| `services/memory/runtime.py` | **NEW.** MemoryRuntime class + SessionMemoryState + dual workers |
| `services/memory/semantic_extraction.py` | **NEW.** Batch extraction with dedup |
| `services/memory/working_set.py` | Refactor into `MemoryRuntime.assemble_context()` |
| `services/memory/compaction.py` | Sidecar call moved to background worker; pending mitigation added |
| `services/chat_service.py` | `prepare_chat_turn()` → `runtime.assemble_context()`; `_finalize_turn_memory()` → `runtime.commit_turn()` |
| `services/memory/session_store.py` | Add `semantic_memories` + `pending_tasks` tables |
| `services/memory/file_memory.py` | Add mtime-based caching + relevance filtering |

---

## 11. Configuration

```python
# Runtime
RUNTIME_STATE_MAX_SESSIONS: int = 1000

# Semantic extraction
SEMANTIC_EXTRACTION_ENABLED: bool = True
SEMANTIC_EXTRACTION_PROVIDER: str = "ollama"
SEMANTIC_EXTRACTION_MODEL: str = "qwen3:4b"
SEMANTIC_EXTRACTION_TIMEOUT: float = 30.0
SEMANTIC_EXTRACTION_BATCH_MIN_TURNS: int = 5
SEMANTIC_MAX_INJECTED_CHARS: int = 800
RECENCY_HALF_LIFE_TURNS: int = 10

# Compaction (existing, keep)
CONTEXT_COMPACTION_TRIGGER_RATIO: float = 0.92
CONTEXT_COMPACTION_MIN_USER_TURNS: int = 10
CONTEXT_COMPACTION_KEEP_RECENT_TURNS: int = 4
CONTEXT_COMPACTION_PENDING_MAX_RATIO: float = 1.2

# File memory
FILE_MEMORY_ENABLED: bool = True
FILE_MEMORY_MAX_FILES: int = 2
FILE_MEMORY_MAX_CHARS_PER_FILE: int = 4000
FILE_MEMORY_RELEVANCE_THRESHOLD: int = 1  # keyword overlap hits needed to inject full content
```

---

## 12. 已知妥协汇总

| # | 妥协 | 当前方案 | 为什么接受 | 改进方向 | 触发改进的条件 |
|---|------|---------|-----------|---------|--------------|
| 1 | 语义记忆检索用 keyword overlap | str.contains() | 记忆量 <200 条时 false positive 可接受 | embedding-based ANN | 语义记忆 >200 条 |
| 2 | File memory relevance 用 keyword overlap | str.contains() | 2个文件, 碰撞概率低 | 文件摘要缓存 + embedding 匹配 | 文件数 >5 或单文件 >10K chars |
| 3 | 双队列各单 worker | 1 compaction + 1 extraction worker | 并发会话 <5 时不会堆积 | worker pooling | 并发会话 >10 |
| 4 | 语义记忆全量加载到缓存 | SELECT * FROM semantic_memories WHERE session_id = ? | 单 session 记忆 <1K 条时毫秒级 | 分段加载 + 按需检索 | 单 session 记忆 >10K 条 |
| 5 | Extraction 用 LLM 非 rule | qwen3:4b 批量调用 | rule extraction 太脆弱，LLM 只多花几十毫秒（异步不计入延迟） | 混合：rule 做第一遍过滤 + LLM 做质量控制 | 成本成为实际问题时 |
| 6 | Compaction pending 时激进裁剪至 4 turns | 仅保留最近 4 turns 原文 | 比阻塞 120s 好得多 | 更短的 compaction timeout + 更快的 fallback model | 并发 compaction 成为常态时 |

---

## 13. Design Decisions Log

每个 "为什么" 记录在此。

1. **为什么三层时间尺度而不是两层？**
   Deferred（秒级）和 Offline（分钟级）的成本结构不同：deferred 影响下一个 turn 的上下文质量，offline 只影响长期演化。用同一个队列会导致慢任务阻塞快任务。

2. **为什么批量提取而不是 per-turn 提取？**
   Per-turn 提取 = 每 turn 一次 LLM 调用。10 个 turn = 10 次调用。批量提取 = 10 个 turn 一次调用。成本降到 1/10。更重要的是，批量提取能跨 turn 发现 pattern（如"用户三次问了同一个问题"），per-turn 做不到。

3. **为什么语义提取的 trigger 不是"总是"而是"每 N turns"？**
   大多数 turn 不产生值得提取的新信息（如 "yes", "next", "run the tests"）。每 turn 提取浪费 90% 的 LLM 调用。积累后批量提取，一次调用覆盖多个 turn，且能 cross-reference。

4. **为什么 file memory 不总是注入全文？**
   Context window 是计算预算，不是存储空间。无关内容不仅浪费 token，还降低模型对重要内容的注意力（attention dilution）。只注入相关的 + 路径提示 = 保持上下文紧凑。

5. **为什么 needs_compaction 要持久化？**
   进程崩溃不可预测。如果 compaction flag 在内存中丢失且 turn_count 条件在重启后不再满足（因为会话已恢复），compaction 可能永远不被触发。持久化保证不死锁。

6. **为什么 state 淘汰用 LRU 而不是 TTL？**
   TTL 淘汰活跃会话（用户去喝了一杯咖啡回来），导致不必要的重建。LRU 只淘汰真正不用的会话。配合启动恢复，冷会话的状态总是可重建的。
