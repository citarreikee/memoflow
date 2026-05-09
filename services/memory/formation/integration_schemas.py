from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from services.memory.formation.schemas import MemoryCandidateLite


INTEGRATION_ACTIONS = {
    "ADD",
    "UPDATE",
    "MERGE",
    "SUPERSEDE",
    "LINK",
    "NOOP",
    "NEEDS_REVIEW",
}


@dataclass(frozen=True)
class ExistingMemorySnapshot:
    memory_id: str
    type: str
    scope: str
    text: str
    status: str = "active"
    namespace: Optional[str] = None
    key: Optional[str] = None
    confidence: float = 0.0
    version: int = 1
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    source_plan_id: Optional[str] = None
    supersedes_memory_id: Optional[str] = None
    superseded_by_memory_id: Optional[str] = None
    evidence_episode_ids: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    match: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryIntegrationPlan:
    candidate_id: str
    action: str
    confidence: float
    rationale: str
    target_memory_id: Optional[str] = None
    related_memory_ids: List[str] = field(default_factory=list)
    graph_relations: List[Dict[str, str]] = field(default_factory=list)
    suggested_text: Optional[str] = None
    blocked_reasons: List[str] = field(default_factory=list)
    needs_review_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_existing_memory(raw: Dict[str, Any]) -> ExistingMemorySnapshot:
    return ExistingMemorySnapshot(
        memory_id=str(raw.get("memory_id") or raw.get("id") or "").strip(),
        type=str(raw.get("type") or raw.get("memory_type") or "non_memory").strip(),
        scope=str(raw.get("scope") or "session").strip(),
        text=str(raw.get("text") or raw.get("value") or "").strip(),
        status=str(raw.get("status") or "active").strip(),
        namespace=str(raw.get("namespace") or "").strip() or None,
        key=str(raw.get("key") or "").strip() or None,
        confidence=_float(raw.get("confidence"), default=0.0),
        version=int(_float(raw.get("version"), default=1.0)),
        created_at=str(raw.get("created_at") or "").strip() or None,
        updated_at=str(raw.get("updated_at") or "").strip() or None,
        source_plan_id=str(raw.get("source_plan_id") or "").strip() or None,
        supersedes_memory_id=str(raw.get("supersedes_memory_id") or "").strip() or None,
        superseded_by_memory_id=str(raw.get("superseded_by_memory_id") or "").strip() or None,
        evidence_episode_ids=[str(item) for item in raw.get("evidence_episode_ids") or []],
        payload=raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
        match=raw.get("match") if isinstance(raw.get("match"), dict) else {},
    )


def empty_integration_plan(candidate: MemoryCandidateLite, *, reason: str) -> MemoryIntegrationPlan:
    return MemoryIntegrationPlan(
        candidate_id=candidate.candidate_id or "",
        action="NOOP",
        confidence=1.0,
        rationale=reason,
        blocked_reasons=[reason],
    )


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
