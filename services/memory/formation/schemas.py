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
OBSERVATION_TYPES = {
    "user_preference_signal",
    "project_rule_signal",
    "decision_signal",
    "task_state_signal",
    "open_loop_signal",
    "relation_signal",
    "artifact_signal",
    "procedure_signal",
    "insight_signal",
    "non_memory_signal",
}
MEMORY_RISKS = {"low", "medium", "high"}

# Memory layer describes what kind of thing the candidate is before any concrete
# storage backend is chosen. It is the bridge between "what deserves memory" and
# the later storage router design.
MEMORY_LAYERS = {
    "raw",
    "event",
    "state",
    "semantic",
    "insight",
    "relation",
    "file",
    "non_memory",
}

# Storage intent is a routing hint, not a guarantee that the corresponding
# backend exists today. Current write plans still validate against implemented
# scaffold stores before anything can be applied.
STORAGE_INTENTS = {
    "auto",
    "episode_log",
    "state_kv",
    "semantic_kv",
    "vector_projection",
    "relation_graph",
    "dag",
    "file_memory",
    "review_queue",
    "none",
}

EVIDENCE_POLICIES = {
    "required",
    "multi_evidence_preferred",
    "review_required",
    "none",
}

LIFECYCLE_HINTS = {
    "normal",
    "volatile",
    "reinforce",
    "supersedes",
    "archive_after_task",
    "review_before_apply",
}

PLAN_STATUSES = {"planned", "blocked", "noop", "needs_review"}

TYPE_DEFAULT_LAYERS = {
    "preference": "semantic",
    "profile_fact": "semantic",
    "project_rule": "file",
    "procedure": "file",
    "decision": "event",
    "task_state": "state",
    "entity_relation": "relation",
    "episodic_event": "event",
    "embedding_hint": "semantic",
    "non_memory": "non_memory",
}

TYPE_DEFAULT_STORAGE_INTENTS = {
    "preference": "semantic_kv",
    "profile_fact": "semantic_kv",
    "project_rule": "file_memory",
    "procedure": "file_memory",
    "decision": "episode_log",
    "task_state": "state_kv",
    "entity_relation": "relation_graph",
    "episodic_event": "episode_log",
    "embedding_hint": "vector_projection",
    "non_memory": "none",
}


@dataclass
class MemoryObservation:
    observation_id: str
    episode_id: str
    type: str
    text: str
    scope_hint: str
    evidence_message_refs: List[str]
    confidence: float
    reason: str
    negative: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    source_observation_ids: List[str] = field(default_factory=list)
    risk: str = "medium"
    memory_layer: str = "semantic"
    storage_intent: str = "auto"
    evidence_policy: str = "required"
    lifecycle_hint: str = "normal"

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
    integration_action: Optional[str] = None
    write_strategy: Optional[str] = None
    target_memory_id: Optional[str] = None
    related_memory_ids: List[str] = field(default_factory=list)
    graph_relations: List[Dict[str, str]] = field(default_factory=list)
    memory_layers: List[str] = field(default_factory=list)
    needs_review_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_candidate(raw: Dict[str, Any], *, fallback_id: str) -> MemoryCandidateLite:
    text = str(raw.get("text") or raw.get("memory_content") or "").strip()
    memory_type = str(raw.get("type") or raw.get("memory_type") or "non_memory").strip()
    if memory_type not in MEMORY_TYPES:
        memory_type = "non_memory"
    scope = str(raw.get("scope") or default_memory_scope(memory_type)).strip()
    if scope not in MEMORY_SCOPES:
        scope = default_memory_scope(memory_type)
    action = str(raw.get("action") or _action_from_tendency(raw.get("write_tendency")) or "NOOP").strip().upper()
    if action not in MEMORY_ACTIONS:
        action = "NOOP"
    try:
        importance = float(raw.get("importance", default_importance(memory_type, action=action)))
    except (TypeError, ValueError):
        importance = 0.0
    importance = min(1.0, max(0.0, importance))
    reason = str(raw.get("reason") or "").strip()
    stability = str(raw.get("stability") or default_stability(memory_type)).strip()
    if stability not in MEMORY_STABILITY:
        stability = default_stability(memory_type)
    candidate_id = str(raw.get("candidate_id") or fallback_id).strip() or fallback_id
    source_observation_ids = _normalize_string_list(raw.get("source_observation_ids"))
    risk = str(raw.get("risk") or "medium").strip()
    if risk not in MEMORY_RISKS:
        risk = "medium"

    default_layer = default_memory_layer(memory_type)
    memory_layers = _normalize_string_list(raw.get("memory_layers"))
    memory_layer = str(raw.get("memory_layer") or (memory_layers[0] if memory_layers else "") or default_layer).strip()
    if memory_layer not in MEMORY_LAYERS:
        memory_layer = default_layer

    default_intent = default_storage_intent(memory_type, scope=scope)
    storage_intent = str(raw.get("storage_intent") or default_intent).strip()
    if storage_intent not in STORAGE_INTENTS:
        storage_intent = default_intent
    if storage_intent == "auto":
        storage_intent = default_intent

    evidence_policy = str(raw.get("evidence_policy") or "required").strip()
    if evidence_policy not in EVIDENCE_POLICIES:
        evidence_policy = "required"

    lifecycle_hint = str(raw.get("lifecycle_hint") or "normal").strip()
    if lifecycle_hint not in LIFECYCLE_HINTS:
        lifecycle_hint = "normal"

    return MemoryCandidateLite(
        text=text,
        type=memory_type,
        scope=scope,
        action=action,
        importance=importance,
        reason=reason,
        stability=stability,
        candidate_id=candidate_id,
        source_observation_ids=source_observation_ids,
        risk=risk,
        memory_layer=memory_layer,
        storage_intent=storage_intent,
        evidence_policy=evidence_policy,
        lifecycle_hint=lifecycle_hint,
    )


def default_memory_layer(memory_type: str) -> str:
    return TYPE_DEFAULT_LAYERS.get(memory_type, "non_memory")


def default_memory_scope(memory_type: str) -> str:
    if memory_type in {"preference", "profile_fact"}:
        return "user"
    if memory_type == "non_memory":
        return "session"
    return "project"


def default_importance(memory_type: str, *, action: str) -> float:
    if action == "NOOP" or memory_type == "non_memory":
        return 0.0
    return {
        "preference": 0.72,
        "profile_fact": 0.68,
        "project_rule": 0.78,
        "procedure": 0.74,
        "decision": 0.74,
        "task_state": 0.66,
        "entity_relation": 0.70,
        "episodic_event": 0.58,
        "embedding_hint": 0.56,
    }.get(memory_type, 0.5)


def default_stability(memory_type: str) -> str:
    if memory_type in {"task_state", "episodic_event"}:
        return "temporary"
    if memory_type in {"preference", "profile_fact", "project_rule", "procedure"}:
        return "stable"
    if memory_type in {"decision", "entity_relation", "embedding_hint"}:
        return "evolving"
    return "unknown"


def _action_from_tendency(value: Any) -> str:
    tendency = str(value or "").strip()
    if tendency in {"do_not_write", "noop", "ignore"}:
        return "NOOP"
    if tendency in {"write", "propose", "review", "needs_review"}:
        return "ADD"
    return ""


def default_storage_intent(memory_type: str, *, scope: str) -> str:
    if memory_type == "project_rule" and scope in {"session", "user"}:
        return "semantic_kv"
    if memory_type == "procedure" and scope in {"session", "user"}:
        return "semantic_kv"
    return TYPE_DEFAULT_STORAGE_INTENTS.get(memory_type, "none")


def normalize_observation(raw: Dict[str, Any], *, fallback_id: str, episode_id: str) -> MemoryObservation:
    observation_type = str(raw.get("type") or raw.get("observation_type") or "non_memory_signal").strip()
    if observation_type not in OBSERVATION_TYPES:
        observation_type = "non_memory_signal"
    text = str(raw.get("text") or raw.get("content") or "").strip()
    scope_hint = str(raw.get("scope_hint") or default_observation_scope(observation_type)).strip()
    if scope_hint not in MEMORY_SCOPES:
        scope_hint = default_observation_scope(observation_type)
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    return MemoryObservation(
        observation_id=str(raw.get("observation_id") or fallback_id).strip() or fallback_id,
        episode_id=str(raw.get("episode_id") or episode_id).strip() or episode_id,
        type=observation_type,
        text=text,
        scope_hint=scope_hint,
        evidence_message_refs=_normalize_string_list(raw.get("evidence_message_refs")),
        confidence=confidence if raw.get("confidence") is not None else default_observation_confidence(observation_type),
        reason=str(raw.get("reason") or "").strip(),
        negative=_observation_negative(raw),
    )


def default_observation_scope(observation_type: str) -> str:
    if observation_type == "user_preference_signal":
        return "user"
    if observation_type == "non_memory_signal":
        return "session"
    return "project"


def default_observation_confidence(observation_type: str) -> float:
    if observation_type == "non_memory_signal":
        return 0.0
    return 0.7


def _observation_negative(raw: Dict[str, Any]) -> bool:
    disposition = str(raw.get("disposition") or "").strip()
    if disposition in {"suppress", "ignore", "non_memory"}:
        return True
    return bool(raw.get("negative", False))


def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
