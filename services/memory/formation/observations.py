from __future__ import annotations

import uuid
from typing import Any, Dict, List, Tuple

from config import settings
from providers import deepseek, kimi, ollama
from services.memory.formation.extractor import (
    PROJECT_MARKERS,
    _episode_text,
    _extract_json_fragments,
    _run_completion,
    _split_sentences,
    parse_candidate_json,
)
from services.memory.formation.prompts import build_observation_messages
from services.memory.formation.quality import postprocess_candidates
from services.memory.formation.schemas import MemoryCandidateLite, MemoryObservation, normalize_observation


MAX_OBSERVATIONS = 8


def extract_observations_rule_based(episode: Dict[str, Any], *, max_observations: int = MAX_OBSERVATIONS) -> List[MemoryObservation]:
    episode_id = str(episode.get("episode_id") or "")
    text = _episode_text(episode)
    if not text.strip():
        return []
    observations: List[MemoryObservation] = []
    for sentence in _split_sentences(text):
        observation = _observation_from_sentence(sentence, episode_id=episode_id)
        if observation:
            observations.append(observation)
        if len(observations) >= max(0, max_observations):
            break
    return observations


async def extract_observations_with_llm(episode: Dict[str, Any]) -> Tuple[List[MemoryObservation], Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = build_observation_messages(episode, max_observations=MAX_OBSERVATIONS)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = parse_observation_json(raw_text)
        observations = parse_observation_payload(payload, episode_id=str(episode.get("episode_id") or ""))
        return observations, {
            "provider": provider,
            "model": model,
            "observation_count": len(observations),
            "raw_preview": raw_text[:1200],
        }
    except Exception as exc:
        return [], {"provider": provider, "model": model, "error": str(exc)}


def parse_observation_payload(payload: Any, *, episode_id: str) -> List[MemoryObservation]:
    if isinstance(payload, dict):
        raw_items = payload.get("observations", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    observations: List[MemoryObservation] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            observation = normalize_observation(raw, fallback_id=f"obs_{uuid.uuid4().hex}", episode_id=episode_id)
            if observation.text:
                observations.append(observation)
    return observations[:MAX_OBSERVATIONS]


def parse_observation_json(raw_text: str) -> Any:
    try:
        return parse_candidate_json(raw_text)
    except Exception as first_error:
        last_error: Exception = first_error
        for fragment in _extract_json_fragments(raw_text or ""):
            try:
                import json

                return json.loads(fragment)
            except Exception as exc:  # pragma: no cover - defensive parser fallback
                last_error = exc
        raise last_error


def _observation_from_sentence(sentence: str, *, episode_id: str) -> MemoryObservation | None:
    cleaned = sentence.strip()
    if len(cleaned) < 8:
        return None
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ("prefer", "from now on", "remember", "鍋忓ソ", "浠ュ悗", "璁颁綇")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="user_preference_signal",
            text=cleaned,
            scope_hint="user",
            confidence=0.76,
            reason="The episode contains an explicit user preference or memory request.",
        )
    if any(marker in lowered for marker in ("depends", "depends_on", "blocks", "supersedes", "contradicts", "derived from", "derived_from", "渚濊禆", "闃诲")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="relation_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.72,
            reason="The episode states an explicit relationship between project concepts.",
        )
    if any(marker in lowered for marker in ("rule", "do not", "must", "default-off", "瑙勫垯", "涓嶈")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="project_rule_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.74,
            reason="The episode states a project-level rule or constraint.",
        )
    if any(marker in lowered for marker in ("decision", "decided", "architecture", "design", "roadmap", "鍐冲畾", "璁捐", "鏋舵瀯")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="decision_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.72,
            reason="The episode contains a project decision or architecture direction.",
        )
    if any(marker in lowered for marker in ("todo", "blocker", "open issue", "next", "continue", "pending")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="open_loop_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.62,
            reason="The episode may contain unfinished project work.",
        )
    if any(marker in lowered for marker in ("file", "commit", "document", "api", "model", "文件", "文档")):
        return _make_observation(
            episode_id=episode_id,
            observation_type="artifact_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.62,
            reason="The episode mentions a potentially important project artifact.",
        )
    if any(marker in lowered for marker in PROJECT_MARKERS):
        return _make_observation(
            episode_id=episode_id,
            observation_type="project_rule_signal",
            text=cleaned,
            scope_hint="project",
            confidence=0.58,
            reason="The episode contains project-level language worth downstream inspection.",
        )
    return None


def _make_observation(
    *,
    episode_id: str,
    observation_type: str,
    text: str,
    scope_hint: str,
    confidence: float,
    reason: str,
) -> MemoryObservation:
    return MemoryObservation(
        observation_id=f"obs_{uuid.uuid4().hex}",
        episode_id=episode_id,
        type=observation_type,
        text=text,
        scope_hint=scope_hint,
        evidence_message_refs=[],
        confidence=confidence,
        reason=reason,
        negative=False,
    )


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


OBSERVATION_TO_CANDIDATE = {
    "user_preference_signal": {
        "type": "preference",
        "scope": "user",
        "stability": "stable",
        "memory_layer": "semantic",
        "storage_intent": "semantic_kv",
        "importance_floor": 0.70,
        "risk": "low",
    },
    "project_rule_signal": {
        "type": "project_rule",
        "scope": "project",
        "stability": "stable",
        "memory_layer": "file",
        "storage_intent": "file_memory",
        "importance_floor": 0.74,
        "risk": "medium",
    },
    "decision_signal": {
        "type": "decision",
        "scope": "project",
        "stability": "evolving",
        "memory_layer": "event",
        "storage_intent": "episode_log",
        "importance_floor": 0.72,
        "risk": "medium",
    },
    "task_state_signal": {
        "type": "task_state",
        "scope": "project",
        "stability": "temporary",
        "memory_layer": "state",
        "storage_intent": "state_kv",
        "importance_floor": 0.66,
        "risk": "low",
    },
    "open_loop_signal": {
        "type": "task_state",
        "scope": "project",
        "stability": "temporary",
        "memory_layer": "state",
        "storage_intent": "state_kv",
        "importance_floor": 0.62,
        "risk": "low",
        "lifecycle_hint": "archive_after_task",
    },
    "relation_signal": {
        "type": "entity_relation",
        "scope": "project",
        "stability": "evolving",
        "memory_layer": "relation",
        "storage_intent": "relation_graph",
        "importance_floor": 0.70,
        "risk": "medium",
    },
    "artifact_signal": {
        "type": "embedding_hint",
        "scope": "project",
        "stability": "evolving",
        "memory_layer": "semantic",
        "storage_intent": "vector_projection",
        "importance_floor": 0.58,
        "risk": "low",
    },
    "procedure_signal": {
        "type": "procedure",
        "scope": "project",
        "stability": "stable",
        "memory_layer": "file",
        "storage_intent": "file_memory",
        "importance_floor": 0.72,
        "risk": "medium",
    },
    "insight_signal": {
        "type": "procedure",
        "scope": "project",
        "stability": "evolving",
        "memory_layer": "insight",
        "storage_intent": "semantic_kv",
        "importance_floor": 0.68,
        "risk": "medium",
        "evidence_policy": "multi_evidence_preferred",
    },
}


def form_candidates_from_observations(
    observations: List[MemoryObservation],
    *,
    max_candidates: int,
) -> tuple[List[MemoryCandidateLite], Dict[str, Any]]:
    candidates: List[MemoryCandidateLite] = []
    skipped_negative = 0
    skipped_non_memory = 0
    for observation in observations:
        if observation.negative:
            skipped_negative += 1
            continue
        if observation.type == "non_memory_signal":
            skipped_non_memory += 1
            continue
        candidate = _candidate_from_observation(observation)
        if candidate:
            candidates.append(candidate)
    processed, quality_report = postprocess_candidates(candidates, max_candidates=max_candidates)
    return processed, {
        "input_observation_count": len(observations),
        "raw_candidate_count": len(candidates),
        "candidate_count": len(processed),
        "skipped_negative_count": skipped_negative,
        "skipped_non_memory_count": skipped_non_memory,
        "candidate_quality": quality_report.to_dict(),
        "mode": "observation_mapping",
    }


def _candidate_from_observation(observation: MemoryObservation) -> MemoryCandidateLite | None:
    template = OBSERVATION_TO_CANDIDATE.get(observation.type)
    if not template:
        return None
    memory_type = str(template["type"])
    scope = str(template.get("scope") or observation.scope_hint)
    importance = max(float(template.get("importance_floor", 0.0)), observation.confidence)
    return MemoryCandidateLite(
        text=observation.text,
        type=memory_type,
        scope=scope,
        action="ADD",
        importance=min(1.0, importance),
        reason=observation.reason or "Derived from structured episode observation.",
        stability=str(template.get("stability") or "unknown"),
        candidate_id=f"cand_{uuid.uuid4().hex}",
        source_observation_ids=[observation.observation_id],
        risk=str(template.get("risk") or "medium"),
        memory_layer=str(template.get("memory_layer") or "semantic"),
        storage_intent=str(template.get("storage_intent") or "semantic_kv"),
        evidence_policy=str(template.get("evidence_policy") or "required"),
        lifecycle_hint=str(template.get("lifecycle_hint") or "normal"),
    )
