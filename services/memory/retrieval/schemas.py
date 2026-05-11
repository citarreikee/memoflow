from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ReconstructedQuery:
    query: str
    target_intents: List[str] = field(default_factory=list)
    target_source_hints: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceStrategy:
    intent: str
    primary_sources: List[str] = field(default_factory=list)
    secondary_sources: List[str] = field(default_factory=list)
    budget_ratio: Dict[str, float] = field(default_factory=dict)
    vector_fallback: bool = False
    min_primary_items: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalBudgetAllocation:
    total_chars: int
    per_intent_chars: Dict[str, int] = field(default_factory=dict)
    per_source_chars: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalIntent:
    kind: str
    query: str
    entities: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    memory_types: List[str] = field(default_factory=list)
    needs_time: bool = False
    needs_graph: bool = False
    needs_preferences: bool = False
    intents: List[str] = field(default_factory=list)
    reconstructed_queries: List[Dict[str, Any]] = field(default_factory=list)
    sufficiency_threshold: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalPlan:
    intent: RetrievalIntent
    paths: List[str]
    namespace: str
    scopes: List[str]
    limit_per_path: int
    max_pack_items: int
    max_pack_chars: int
    min_score: float
    source_strategies: List[SourceStrategy] = field(default_factory=list)
    budget_allocation: Dict[str, int] = field(default_factory=dict)
    recent_context_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["intent"] = self.intent.to_dict()
        return data


@dataclass
class RetrievalCandidate:
    source: str
    memory_id: Optional[str]
    projection_id: Optional[str]
    edge_id: Optional[str]
    scope: str
    memory_type: str
    text: str
    score: float
    reason: str
    evidence_episode_ids: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)
    authority: str = "authoritative"
    intent: str = ""
    source_priority: float = 1.0
    load_mode: str = "inline"
    conflict: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalPackItem:
    memory_id: Optional[str]
    source: str
    memory_type: str
    scope: str
    text: str
    reason: str
    score: float
    authority: str = "authoritative"
    intent: str = ""
    evidence_episode_ids: List[str] = field(default_factory=list)
    load_mode: str = "inline"
    conflict: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalPack:
    intent: RetrievalIntent
    items: List[RetrievalPackItem]
    omitted_count: int
    estimated_chars: int
    trace: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "omitted_count": self.omitted_count,
            "estimated_chars": self.estimated_chars,
            "trace": self.trace,
        }
