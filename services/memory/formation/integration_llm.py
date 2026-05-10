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
    prompt_memories = [_existing_prompt_view(index, memory) for index, memory in enumerate(existing_memories)]
    messages = build_integration_messages(prompt_candidate, prompt_memories)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = parse_integration_json(raw_text)
        decision = normalize_integration_decision(payload)
        plan = decision_to_integration_plan(candidate, existing_memories=existing_memories, decision=decision)
        return plan, {
            "mode": "llm_minimal_integration",
            "provider": provider,
            "model": model,
            "decision": decision.to_dict(),
            "raw_preview": raw_text[:1200],
        }
    except Exception as exc:
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=0.0,
            rationale="LLM integration failed; fallback required.",
            needs_review_reasons=["llm_integration_failed"],
        ), {"mode": "llm_minimal_integration", "provider": provider, "model": model, "error": str(exc)}


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
