from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


MEMORY_TYPES = {
    "preference",
    "profile_fact",
    "project_rule",
    "procedure",
    "decision",
    "task_state",
    "entity_relation",
    "episodic_event",
    "embedding_hint",
    "non_memory",
}

MEMORY_ACTIONS = {"ADD", "UPDATE", "DELETE", "NOOP"}
MEMORY_SCOPES = {"session", "user", "project", "workspace"}
MEMORY_STABILITY = {"temporary", "evolving", "stable", "unknown"}
PLAN_STATUSES = {"planned", "blocked", "noop", "needs_review"}


@dataclass
class MemoryCandidateLite:
    text: str
    type: str
    scope: str
    action: str
    importance: float
    reason: str
    stability: str = "unknown"
    candidate_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryWritePlan:
    plan_id: str
    candidate_id: str
    action: str
    canonical_store: Optional[str]
    projections: List[str]
    scope: str
    evidence_episode_ids: List[str]
    confidence: float
    status: str
    blocked_reasons: List[str] = field(default_factory=list)
    type: str = "non_memory"
    text: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_candidate(raw: Dict[str, Any], *, fallback_id: str) -> MemoryCandidateLite:
    text = str(raw.get("text") or "").strip()
    memory_type = str(raw.get("type") or "non_memory").strip()
    if memory_type not in MEMORY_TYPES:
        memory_type = "non_memory"
    scope = str(raw.get("scope") or "session").strip()
    if scope not in MEMORY_SCOPES:
        scope = "session"
    action = str(raw.get("action") or "NOOP").strip().upper()
    if action not in MEMORY_ACTIONS:
        action = "NOOP"
    try:
        importance = float(raw.get("importance", 0.0))
    except (TypeError, ValueError):
        importance = 0.0
    importance = min(1.0, max(0.0, importance))
    reason = str(raw.get("reason") or "").strip()
    stability = str(raw.get("stability") or "unknown").strip()
    if stability not in MEMORY_STABILITY:
        stability = "unknown"
    candidate_id = str(raw.get("candidate_id") or fallback_id).strip() or fallback_id
    return MemoryCandidateLite(
        text=text,
        type=memory_type,
        scope=scope,
        action=action,
        importance=importance,
        reason=reason,
        stability=stability,
        candidate_id=candidate_id,
    )

