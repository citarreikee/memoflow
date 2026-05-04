from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from config import settings


RISK_SAFE = "safe"
RISK_WATCH = "watch"
RISK_PREPARE_COMPACT = "prepare_compact"
RISK_EMERGENCY = "emergency"
RISK_HARD_FAIL = "hard_fail"


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
    risk_level: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimePolicyDecision:
    load_checkpoint: bool = False
    load_file_memory: bool = False
    emergency_compaction: bool = False
    post_turn_compaction: bool = False
    trigger_folding: bool = False
    enqueue_reflection: bool = False
    retrieval_mode: str = "recent_only"
    reason: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_token_pressure(estimated_tokens: int, token_budget: int) -> str:
    if token_budget <= 0:
        return RISK_SAFE
    ratio = estimated_tokens / token_budget
    if ratio >= 1.0:
        return RISK_HARD_FAIL
    if ratio >= 0.95:
        return RISK_EMERGENCY
    if ratio >= settings.CONTEXT_COMPACTION_TRIGGER_RATIO:
        return RISK_PREPARE_COMPACT
    if ratio >= 0.70:
        return RISK_WATCH
    return RISK_SAFE


def should_load_file_memory(user_message: str) -> bool:
    if not settings.FILE_MEMORY_ENABLED:
        return False
    lowered = user_message.lower()
    return any(
        keyword in lowered
        for keyword in (
            "project",
            "repo",
            "repository",
            "architecture",
            "design",
            "document",
            "文档",
            "架构",
            "设计",
            "工程",
            "代码",
            "目录",
        )
    )


def decide_prepare_policy(
    *,
    state: RuntimeState,
    latest_checkpoint: Optional[Dict[str, Any]],
    user_message: str,
) -> RuntimePolicyDecision:
    decision = RuntimePolicyDecision()
    if latest_checkpoint and latest_checkpoint.get("summary"):
        decision.load_checkpoint = True
        decision.reason.append("checkpoint_available")
    if should_load_file_memory(user_message):
        decision.load_file_memory = True
        decision.reason.append("project_context_query")
    parts: List[str] = []
    if decision.load_checkpoint:
        parts.append("checkpoint")
    if decision.load_file_memory:
        parts.append("file")
    parts.append("recent")
    decision.retrieval_mode = "_".join(parts) if parts else "recent_only"
    return decision


def decide_post_turn_policy(
    *,
    post_turn_estimated_tokens: int,
    token_budget: int,
    uncovered_user_turn_count: int,
    uncovered_old_turn_count: int,
    checkpoint_job_pending: bool = False,
) -> RuntimePolicyDecision:
    decision = RuntimePolicyDecision()
    reasons: List[str] = []
    ratio = post_turn_estimated_tokens / token_budget if token_budget > 0 else 0.0
    if ratio >= settings.CONTEXT_COMPACTION_TRIGGER_RATIO:
        reasons.append("post_turn_token_pressure")
    if uncovered_user_turn_count >= settings.CONTEXT_COMPACTION_MIN_USER_TURNS:
        reasons.append("uncovered_user_turn_threshold")
    if uncovered_old_turn_count > settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS:
        reasons.append("uncovered_old_turns_available")

    decision.post_turn_compaction = bool(reasons) and not checkpoint_job_pending
    if checkpoint_job_pending:
        reasons.append("checkpoint_job_pending")
    decision.reason = reasons
    decision.retrieval_mode = "post_turn_maintenance"
    return decision
