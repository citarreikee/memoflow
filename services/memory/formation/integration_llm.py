from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from services.memory.formation.extractor import _extract_json_fragments, _run_completion, parse_candidate_json
from services.memory.formation.integration_schemas import ExistingMemorySnapshot, MemoryIntegrationPlan
from services.memory.formation.prompts import build_integration_messages
from services.memory.formation.schemas import MEMORY_LAYERS, MemoryCandidateLite


WRITE_STRATEGIES = {
    "add_new",
    "do_not_write",
    "merge_with_existing",
    "update_existing",
    "supersede_existing",
    "link_as_relation",
    "mark_conflict",
    "needs_review",
}

STRATEGY_TO_ACTION = {
    "add_new": "ADD",
    "do_not_write": "NOOP",
    "merge_with_existing": "MERGE",
    "update_existing": "UPDATE",
    "supersede_existing": "SUPERSEDE",
    "link_as_relation": "LINK",
    "mark_conflict": "CONFLICT",
    "needs_review": "NEEDS_REVIEW",
}

RELATION_TYPES = {
    "depends_on",
    "blocks",
    "supersedes",
    "contradicts",
    "derived_from",
    "part_of",
    "caused_by",
}


@dataclass(frozen=True)
class MinimalIntegrationDecision:
    memory_content: str
    memory_layers: List[str]
    write_strategy: str
    related_existing_indices: List[int] = field(default_factory=list)
    reason: str = ""
    relation_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


async def plan_memory_integration_with_llm(
    candidate: MemoryCandidateLite,
    *,
    existing_memories: List[ExistingMemorySnapshot],
) -> Tuple[MemoryIntegrationPlan, Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    prompt_candidate = _candidate_prompt_view(candidate)
    selected_memories = select_existing_memories_for_llm(candidate, existing_memories)
    prompt_memories = [_existing_prompt_view(index, memory) for index, memory in selected_memories]
    messages = build_integration_messages(prompt_candidate, prompt_memories)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = parse_integration_json(raw_text)
        decision = normalize_integration_decision(payload)
        plan = decision_to_integration_plan(candidate, existing_memories=selected_memories, decision=decision)
        return plan, {
            "mode": "llm_minimal_integration",
            "provider": provider,
            "model": model,
            "decision": decision.to_dict(),
            "prompt_candidate": prompt_candidate,
            "prompt_related_existing_memories": prompt_memories,
            "selected_existing_count": len(selected_memories),
            "selected_existing_memory_ids": [memory.memory_id for memory in selected_memories],
            "raw_preview": raw_text[:1200],
        }
    except Exception as exc:
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=0.0,
            rationale="LLM integration failed; fallback required.",
            needs_review_reasons=["llm_integration_failed"],
        ), {
            "mode": "llm_minimal_integration",
            "provider": provider,
            "model": model,
            "prompt_candidate": prompt_candidate,
            "prompt_related_existing_memories": prompt_memories,
            "selected_existing_count": len(selected_memories),
            "selected_existing_memory_ids": [memory.memory_id for memory in selected_memories],
            "error": str(exc),
        }


def parse_integration_json(raw_text: str) -> Any:
    try:
        return parse_candidate_json(raw_text)
    except Exception as first_error:
        last_error: Exception = first_error
        for fragment in _extract_json_fragments(raw_text or ""):
            try:
                return json.loads(fragment)
            except Exception as exc:  # pragma: no cover - defensive parser fallback
                last_error = exc
        raise last_error


def normalize_integration_decision(payload: Any) -> MinimalIntegrationDecision:
    raw = payload if isinstance(payload, dict) else {}
    strategy = str(raw.get("write_strategy") or "needs_review").strip()
    if strategy not in WRITE_STRATEGIES:
        strategy = "needs_review"
    layers = _normalize_layers(raw.get("memory_layers"))
    relation_type = str(raw.get("relation_type") or "").strip() or None
    if relation_type == "null" or relation_type not in RELATION_TYPES:
        relation_type = None
    return MinimalIntegrationDecision(
        memory_content=str(raw.get("memory_content") or "").strip(),
        memory_layers=layers,
        write_strategy=strategy,
        related_existing_indices=_normalize_indices(raw.get("related_existing_indices")),
        reason=str(raw.get("reason") or "").strip(),
        relation_type=relation_type,
    )


def decision_to_integration_plan(
    candidate: MemoryCandidateLite,
    *,
    existing_memories: List[ExistingMemorySnapshot],
    decision: MinimalIntegrationDecision,
) -> MemoryIntegrationPlan:
    action = STRATEGY_TO_ACTION[decision.write_strategy]
    related_indices = [index for index in decision.related_existing_indices if 0 <= index < len(existing_memories)]
    related_memory_ids = [existing_memories[index].memory_id for index in related_indices]
    target_memory_id = related_memory_ids[0] if related_memory_ids else None
    blocked_reasons: List[str] = []
    review_reasons: List[str] = []

    if decision.related_existing_indices and len(related_indices) != len(decision.related_existing_indices):
        review_reasons.append("invalid_related_existing_index")

    if action in {"MERGE", "UPDATE", "SUPERSEDE"} and not target_memory_id:
        action = "NEEDS_REVIEW"
        review_reasons.append("target_memory_required")

    if action == "LINK" and (not target_memory_id or not decision.relation_type):
        action = "NEEDS_REVIEW"
        review_reasons.append("relation_target_or_type_missing")

    if action == "CONFLICT" and not target_memory_id:
        action = "NEEDS_REVIEW"
        review_reasons.append("conflict_target_missing")

    if action == "NOOP":
        blocked_reasons.append("llm_do_not_write")

    if action == "CONFLICT":
        review_reasons.append("llm_marked_conflict")

    graph_relations = []
    if action == "LINK" and target_memory_id and decision.relation_type:
        graph_relations.append({"relation_type": decision.relation_type, "target_memory_id": target_memory_id})
    if action == "SUPERSEDE" and target_memory_id:
        graph_relations.append({"relation_type": "supersedes", "target_memory_id": target_memory_id})

    suggested_text = decision.memory_content or candidate.text
    return MemoryIntegrationPlan(
        candidate_id=candidate.candidate_id or "",
        action=action,
        confidence=0.0,
        rationale=decision.reason or "Minimal LLM integration decision.",
        target_memory_id=target_memory_id,
        related_memory_ids=related_memory_ids,
        graph_relations=graph_relations,
        suggested_text=suggested_text if action not in {"NOOP"} else None,
        memory_layers=decision.memory_layers,
        write_strategy=decision.write_strategy,
        related_existing_indices=related_indices,
        blocked_reasons=blocked_reasons,
        needs_review_reasons=review_reasons,
    )


MAX_LLM_RELATED_MEMORIES = 8
MAX_LLM_MEMORY_CHARS = 500
MAX_LLM_TOTAL_MEMORY_CHARS = 2400


def select_existing_memories_for_llm(
    candidate: MemoryCandidateLite,
    existing_memories: List[ExistingMemorySnapshot],
    *,
    max_items: int = MAX_LLM_RELATED_MEMORIES,
    max_total_chars: int = MAX_LLM_TOTAL_MEMORY_CHARS,
) -> List[ExistingMemorySnapshot]:
    """Choose the smallest useful memory set for semantic integration.

    The neighborhood repository can return implementation-oriented snapshots with
    exact, lexical, and compatible matches. The LLM should not see every snapshot;
    it only needs enough content to judge duplicate/update/conflict/relation.
    """

    active = [memory for memory in existing_memories if memory.status == "active"]
    ranked = sorted(active, key=lambda memory: _llm_relevance_score(candidate, memory), reverse=True)
    selected: List[ExistingMemorySnapshot] = []
    seen_texts: set[str] = set()
    total_chars = 0
    for memory in ranked:
        if not _is_candidate_relevant_for_llm(candidate, memory):
            continue
        if _llm_relevance_score(candidate, memory) <= 0:
            continue
        normalized_text = " ".join((memory.text or "").lower().split())
        if not normalized_text or normalized_text in seen_texts:
            continue
        clipped = _clip_for_llm(memory.text, MAX_LLM_MEMORY_CHARS)
        if total_chars + len(clipped) > max_total_chars:
            break
        selected.append(
            ExistingMemorySnapshot(
                memory_id=memory.memory_id,
                type=memory.type,
                scope=memory.scope,
                text=clipped,
                status=memory.status,
                namespace=memory.namespace,
                key=memory.key,
                confidence=memory.confidence,
                version=memory.version,
                created_at=memory.created_at,
                updated_at=memory.updated_at,
                source_plan_id=memory.source_plan_id,
                supersedes_memory_id=memory.supersedes_memory_id,
                superseded_by_memory_id=memory.superseded_by_memory_id,
                evidence_episode_ids=list(memory.evidence_episode_ids),
                payload=dict(memory.payload),
                match=dict(memory.match),
            )
        )
        seen_texts.add(normalized_text)
        total_chars += len(clipped)
        if len(selected) >= max_items:
            break
    return selected


def _is_candidate_relevant_for_llm(candidate: MemoryCandidateLite, memory: ExistingMemorySnapshot) -> bool:
    if str(memory.match.get("path", "")) == "exact_key":
        return True
    try:
        if float(memory.match.get("score", 0.0)) > 0:
            return True
    except (TypeError, ValueError):
        pass
    if memory.type == candidate.type and memory.scope == candidate.scope:
        return True
    candidate_tokens = _tokens(candidate.text)
    memory_tokens = _tokens(memory.text)
    return bool(candidate_tokens and memory_tokens and candidate_tokens & memory_tokens)


def _llm_relevance_score(candidate: MemoryCandidateLite, memory: ExistingMemorySnapshot) -> float:
    score = float(memory.match.get("score", 0.0)) if isinstance(memory.match, dict) else 0.0
    if memory.type == candidate.type:
        score += 0.35
    if memory.scope == candidate.scope:
        score += 0.20
    if memory.status == "active":
        score += 0.10
    if str(memory.match.get("path", "")) == "exact_key":
        score += 1.0
    candidate_tokens = _tokens(candidate.text)
    memory_tokens = _tokens(memory.text)
    if candidate_tokens and memory_tokens:
        score += len(candidate_tokens & memory_tokens) / len(candidate_tokens | memory_tokens)
    return score


def _tokens(text: str) -> set[str]:
    import re

    return {token for token in re.findall(r"[a-zA-Z0-9_\-]+", (text or "").lower()) if len(token) >= 2}


def _clip_for_llm(text: str, max_chars: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def _candidate_prompt_view(candidate: MemoryCandidateLite) -> Dict[str, Any]:
    return {
        "content": candidate.text,
        "candidate_type_hint": candidate.type,
        "scope_hint": candidate.scope,
        "memory_layer_hint": candidate.memory_layer,
        "storage_intent_hint": candidate.storage_intent,
        "stability_hint": candidate.stability,
    }


def _existing_prompt_view(index: int, memory: ExistingMemorySnapshot) -> Dict[str, Any]:
    payload_layers = memory.payload.get("memory_layers") if isinstance(memory.payload, dict) else None
    return {
        "index": index,
        "content": memory.text,
        "memory_type": memory.type,
        "scope": memory.scope,
        "memory_layers": _normalize_layers(payload_layers) or [],
        "status": memory.status,
    }


def _normalize_layers(value: Any) -> List[str]:
    raw_values = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    layers: List[str] = []
    for item in raw_values:
        layer = str(item).strip()
        if layer in MEMORY_LAYERS and layer != "non_memory" and layer not in layers:
            layers.append(layer)
    return layers


def _normalize_indices(value: Any) -> List[int]:
    raw_values = value if isinstance(value, list) else [value]
    indices: List[int] = []
    for item in raw_values:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if index not in indices:
            indices.append(index)
    return indices


def _resolve_provider() -> str:
    if settings.MEMORY_FORMATION_PROVIDER:
        return settings.MEMORY_FORMATION_PROVIDER
    if settings.SIDECAR_COMPACTION_PROVIDER:
        return settings.SIDECAR_COMPACTION_PROVIDER.lower()
    return "deepseek"


def _resolve_model(provider: str) -> str:
    if settings.MEMORY_FORMATION_MODEL:
        return settings.MEMORY_FORMATION_MODEL
    if provider == "deepseek":
        return settings.SIDECAR_COMPACTION_MODEL or "deepseek-v4-flash"
    if provider == "kimi":
        return settings.KIMI_MODELS.split(",")[0].strip() if settings.KIMI_MODELS else "kimi-k2.5"
    if provider == "ollama":
        return settings.SIDECAR_COMPACTION_MODEL or "qwen3:30b-a3b"
    return settings.SIDECAR_COMPACTION_MODEL
