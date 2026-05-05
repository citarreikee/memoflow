from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from providers import deepseek, kimi, ollama
from services.memory.formation.prompts import build_extraction_messages
from services.memory.formation.schemas import MemoryCandidateLite, normalize_candidate


EXPLICIT_MEMORY_MARKERS = (
    "remember",
    "from now on",
    "prefer",
    "decision",
    "rule",
    "do not",
    "记住",
    "以后",
    "偏好",
    "决定",
    "规则",
    "不要",
)

PROJECT_MARKERS = (
    "architecture",
    "design",
    "milestone",
    "dependency",
    "constraint",
    "spec",
    "架构",
    "设计",
    "阶段",
    "依赖",
    "约束",
    "文档",
)


def should_trigger_extraction(episode: Dict[str, Any], *, turn_interval: Optional[int] = None) -> bool:
    if turn_interval is None:
        turn_interval = settings.MEMORY_FORMATION_TURN_INTERVAL
    turn_index = int(episode.get("turn_index") or 0)
    text = _episode_text(episode).lower()
    if not text.strip():
        return False
    if any(marker in text for marker in EXPLICIT_MEMORY_MARKERS):
        return True
    if any(marker in text for marker in PROJECT_MARKERS):
        return True
    return bool(turn_interval > 0 and turn_index > 0 and turn_index % turn_interval == 0)


def extract_candidates_rule_based(episode: Dict[str, Any], *, max_candidates: Optional[int] = None) -> List[MemoryCandidateLite]:
    if max_candidates is None:
        max_candidates = settings.MEMORY_FORMATION_MAX_CANDIDATES
    text = _episode_text(episode)
    if not text.strip():
        return []
    candidates: List[MemoryCandidateLite] = []
    for sentence in _split_sentences(text):
        candidate = _candidate_from_sentence(sentence)
        if candidate:
            candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break
    return candidates


def parse_candidate_payload(payload: Any, *, max_candidates: int = 3) -> List[MemoryCandidateLite]:
    if isinstance(payload, dict):
        raw_items = payload.get("candidates", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    candidates: List[MemoryCandidateLite] = []
    for raw in raw_items[:max_candidates]:
        if isinstance(raw, dict):
            candidates.append(normalize_candidate(raw, fallback_id=f"cand_{uuid.uuid4().hex}"))
    return candidates


async def extract_candidates_with_llm(episode: Dict[str, Any]) -> Tuple[List[MemoryCandidateLite], Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = build_extraction_messages(episode, max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = parse_candidate_json(raw_text)
        candidates = parse_candidate_payload(payload, max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES)
        return candidates, {
            "provider": provider,
            "model": model,
            "candidate_count": len(candidates),
            "raw_preview": raw_text[:1200],
        }
    except Exception as exc:
        return [], {"provider": provider, "model": model, "error": str(exc)}


def parse_candidate_json(raw_text: str) -> Any:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("empty_extraction_response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        extracted = _extract_json_object(text)
        if extracted is None:
            raise
        return json.loads(extracted)


async def _run_completion(*, provider: str, model: str, messages: List[Dict[str, str]]) -> str:
    if provider == "ollama":
        return await ollama.generate_ollama_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_FORMATION_TIMEOUT_SECONDS,
            num_predict=600,
            think=False,
        )
    if provider == "deepseek":
        return await deepseek.generate_deepseek_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_FORMATION_TIMEOUT_SECONDS,
        )
    if provider == "kimi":
        return await kimi.generate_kimi_completion(
            model=model,
            messages=messages,
            timeout=settings.MEMORY_FORMATION_TIMEOUT_SECONDS,
        )
    raise ValueError(f"unsupported_memory_formation_provider:{provider}")


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


def _extract_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def _candidate_from_sentence(sentence: str) -> MemoryCandidateLite | None:
    cleaned = sentence.strip()
    if len(cleaned) < 8:
        return None
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ("不要", "do not", "规则", "rule")):
        memory_type = "project_rule"
        scope = "project"
        importance = 0.78
        stability = "stable"
    elif any(marker in lowered for marker in ("决定", "decision", "设计", "架构", "architecture", "design")):
        memory_type = "decision"
        scope = "project"
        importance = 0.74
        stability = "evolving"
    elif any(marker in lowered for marker in ("偏好", "prefer", "喜欢", "希望")):
        memory_type = "preference"
        scope = "user"
        importance = 0.72
        stability = "stable"
    elif any(marker in lowered for marker in ("依赖", "depends", "阻塞", "blocks", "取代", "supersedes")):
        memory_type = "entity_relation"
        scope = "project"
        importance = 0.70
        stability = "evolving"
    elif any(marker in lowered for marker in PROJECT_MARKERS):
        memory_type = "project_rule"
        scope = "project"
        importance = 0.66
        stability = "evolving"
    else:
        return None
    return MemoryCandidateLite(
        text=cleaned,
        type=memory_type,
        scope=scope,
        action="ADD",
        importance=importance,
        reason="Likely to affect future project or user-specific behavior.",
        stability=stability,
        candidate_id=f"cand_{uuid.uuid4().hex}",
    )


def _episode_text(episode: Dict[str, Any]) -> str:
    parts: List[str] = []
    for message in episode.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _split_sentences(text: str) -> List[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return []
    pieces = re.split(r"(?<=[。！？!?])\s+|(?<=[。！？!?])|(?<=\.)\s+", compact)
    return [piece.strip() for piece in pieces if piece.strip()]
