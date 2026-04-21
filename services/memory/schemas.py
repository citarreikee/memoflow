from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class Episode:
    id: str
    session_id: str
    session_key: str
    harness: str
    source: str
    user_message: str
    assistant_answer: str
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShortTermState:
    session_id: str
    harness: str
    recent_turn_ids: List[str] = field(default_factory=list)
    conversation_summary: str = ""
    task_state_summary: str = ""
    open_issues_summary: str = ""
    covered_message_ids: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryAtom:
    id: str
    type: str
    scope_type: str
    scope_id: str
    content: str
    normalized_content: str
    evidence_episode_ids: List[str]
    entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    confidence: float = 0.5
    importance: float = 0.5
    status: str = "active"
    hash: str = ""
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    observed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalEvent:
    id: str
    session_id: str
    query: str
    harness: str
    retrieved_atom_ids: List[str]
    scores: Dict[str, float] = field(default_factory=dict)
    assembled_context_preview: str = ""
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
