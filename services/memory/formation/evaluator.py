from __future__ import annotations

from dataclasses import dataclass
from typing import List

from services.memory.formation.schemas import MemoryCandidateLite


LOW_IMPORTANCE_THRESHOLD = 0.45
REVIEW_IMPORTANCE_THRESHOLD = 0.65
PLANNED_IMPORTANCE_THRESHOLD = 0.65

SENSITIVE_MARKERS = (
    "password",
    "api key",
    "secret",
    "token",
    "密码",
    "密钥",
    "身份证",
    "银行卡",
)


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    confidence: float
    blocked_reasons: List[str]


def evaluate_candidate(candidate: MemoryCandidateLite) -> EvaluationResult:
    blocked_reasons: List[str] = []
    status = "planned"

    if candidate.action == "NOOP" or candidate.type == "non_memory":
        return EvaluationResult("noop", 0.0, ["candidate_noop"])

    if not candidate.text:
        return EvaluationResult("blocked", 0.0, ["empty_text"])

    if candidate.importance < LOW_IMPORTANCE_THRESHOLD:
        return EvaluationResult("noop", candidate.importance, ["low_importance"])

    if _contains_sensitive_marker(candidate.text):
        blocked_reasons.append("sensitive_content")

    if candidate.scope not in {"session", "user", "project", "workspace"}:
        blocked_reasons.append("invalid_scope")

    if candidate.stability == "temporary" and candidate.type not in {"task_state", "episodic_event"}:
        blocked_reasons.append("temporary_memory")

    confidence = _derive_confidence(candidate)

    if blocked_reasons:
        status = "blocked"
    elif candidate.importance < REVIEW_IMPORTANCE_THRESHOLD or candidate.stability == "unknown":
        status = "needs_review"

    return EvaluationResult(status, confidence, blocked_reasons)


def _derive_confidence(candidate: MemoryCandidateLite) -> float:
    confidence = candidate.importance
    if candidate.stability == "stable":
        confidence += 0.08
    elif candidate.stability == "evolving":
        confidence += 0.03
    elif candidate.stability == "temporary":
        confidence -= 0.10
    elif candidate.stability == "unknown":
        confidence -= 0.05
    if not candidate.reason:
        confidence -= 0.05
    return round(min(1.0, max(0.0, confidence)), 3)


def _contains_sensitive_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)

