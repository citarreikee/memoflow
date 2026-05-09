from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from providers import deepseek, kimi, ollama
from services.memory.formation.prompts import build_extraction_messages
from services.memory.formation.quality import CandidateQualityReport, postprocess_candidates
from services.memory.formation.schemas import MemoryCandidateLite, normalize_candidate


EXPLICIT_MEMORY_MARKERS = (
    "remember",
    "from now on",
    "prefer",
    "decision",
    "rule",
    "do not",
    "depends_on",
    "file memory",
    "storage",
    "记住",
    "以后",
    "偏好",
    "决定",
    "规则",
    "不要",
    "璁飁綇",
    "浠ュ悗",
    "鍋忓好",
    "鍀冲畝",
    "瑰勫垥",
    "涓嶶渚",
)


PROJECT_MARKERS = (
    "architecture",
    "design",
    "milestone",
    "dependency",
    "constraint",
    "spec",
    "depends_on",
    "file memory",
    "storage",
    "架构",
    "设计",
    "阶段",
    "依赖",
    "约束",
    "文档",
    "鏋舌瀝",
    "璁捐宸",
    "闃舵涆",
    "渚濈禱",
    "绾︶潿",
    "鏂囩欢",
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
    processed, _report = postprocess_candidates(candidates, max_candidates=max_candidates)
    return processed


def parse_candidate_payload(payload: Any, *, max_candidates: int = 3) -> List[MemoryCandidateLite]:
    candidates, _report = parse_candidate_payload_with_report(payload, max_candidates=max_candidates)
    return candidates


def parse_candidate_payload_with_report(
    payload: Any,
    *,
    max_candidates: int = 3,
) -> Tuple[List[MemoryCandidateLite], CandidateQualityReport]:
    if isinstance(payload, dict):
        raw_items = payload.get("candidates", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    candidates: List[MemoryCandidateLite] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            candidates.append(normalize_candidate(raw, fallback_id=f"cand_{uuid.uuid4().hex}"))
    return postprocess_candidates(candidates, max_candidates=max_candidates)


async def extract_candidates_with_llm(episode: Dict[str, Any]) -> Tuple[List[MemoryCandidateLite], Dict[str, Any]]:
    provider = _resolve_provider()
    model = _resolve_model(provider)
    messages = build_extraction_messages(episode, max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES)
    try:
        raw_text = await _run_completion(provider=provider, model=model, messages=messages)
        payload = parse_candidate_json(raw_text)
        candidates, quality_report = parse_candidate_payload_with_report(
            payload,
            max_candidates=settings.MEMORY_FORMATION_MAX_CANDIDATES,
        )
        return candidates, {
            "provider": provider,
            "model": model,
            "candidate_count": len(candidates),
            "candidate_quality": quality_report.to_dict(),
            "raw_preview": raw_text[:1200],
        }
    except Exception as exc:
        return [], {"provider": provider, "model": model, "error": str(exc)}


def parse_candidate_json(raw_text: str) -> Any:
    text = (raw_text or "").strip().lstrip("﻿")
    if not text:
        raise ValueError("empty_extraction_response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        first_valid_payload: Any = None
        last_error: Exception = first_error
        for fragment in _extract_json_fragments(text):
            try:
                payload = json.loads(fragment)
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if first_valid_payload is None:
                first_valid_payload = payload
            if _looks_like_candidate_payload(payload):
                return payload
        if first_valid_payload is not None:
            return first_valid_payload
        raise last_error




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


def _extract_json_fragments(text: str) -> List[str]:
    fragments: List[str] = []
    for start, char in enumerate(text):
        if char not in "{[":
            continue
        fragment = _balanced_json_fragment(text, start)
        if fragment is not None:
            fragments.append(fragment)
    return fragments


def _balanced_json_fragment(text: str, start: int) -> Optional[str]:
    opening = text[start]
    if opening not in "{[":
        return None
    stack: List[str] = ["}" if opening == "{" else "]"]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "{[":
            stack.append("}" if char == "{" else "]")
            continue
        if char in "}]":
            if not stack or char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return text[start : index + 1]
    return None


def _looks_like_candidate_payload(payload: Any) -> bool:
    if isinstance(payload, dict):
        candidates = payload.get("candidates")
        return isinstance(candidates, list)
    if isinstance(payload, list):
        return all(isinstance(item, dict) for item in payload)
    return False




def _candidate_from_sentence(sentence: str) -> MemoryCandidateLite | None:
    cleaned = sentence.strip()
    if len(cleaned) < 8:
        return None
    lowered = cleaned.lower()
    if any(marker in lowered for marker in ('偏好', 'prefer', '喜欢', '希望', '鍋忓好')):
        memory_type = "preference"
        scope = "user"
        importance = 0.72
        stability = "stable"
    elif any(marker in lowered for marker in ('依赖', 'depends', '阻塞', 'blocks', '取代', 'supersedes', '渚濈禱', '闃诽墣', '鍏取代')):
        memory_type = "entity_relation"
        scope = "project"
        importance = 0.70
        stability = "evolving"
    elif any(marker in lowered for marker in ('不要', 'do not', '规则', 'rule', '瑰勫垥', '涓嶶渚')):
        memory_type = "project_rule"
        scope = "project"
        importance = 0.78
        stability = "stable"
    elif any(marker in lowered for marker in ('决定', 'decision', '设计', '架构', 'architecture', 'design', '鍀冲畝', '璁捐宸', '鏋舌瀝')):
        memory_type = "decision"
        scope = "project"
        importance = 0.74
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
    pieces = re.split(r"(?<=[\u3002\uff01\uff1f!?])\s+|(?<=[\u3002\uff01\uff1f!?])|(?<=\.)\s+|(?<=\u9286)", compact)
    return [piece.strip() for piece in pieces if piece.strip()]
