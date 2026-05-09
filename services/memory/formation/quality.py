from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Tuple

from services.memory.formation.schemas import MemoryCandidateLite


MAX_CANDIDATE_TEXT_CHARS = 600
MAX_CANDIDATE_REASON_CHARS = 240


@dataclass(frozen=True)
class CandidateQualityReport:
    input_count: int
    output_count: int
    duplicate_count: int = 0
    clipped_text_count: int = 0
    clipped_reason_count: int = 0
    dropped_empty_count: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "input_count": self.input_count,
            "output_count": self.output_count,
            "duplicate_count": self.duplicate_count,
            "clipped_text_count": self.clipped_text_count,
            "clipped_reason_count": self.clipped_reason_count,
            "dropped_empty_count": self.dropped_empty_count,
            "notes": self.notes,
        }


@dataclass
class _MutableQualityStats:
    duplicate_count: int = 0
    clipped_text_count: int = 0
    clipped_reason_count: int = 0
    dropped_empty_count: int = 0
    notes: List[str] = field(default_factory=list)


def postprocess_candidates(
    candidates: Iterable[MemoryCandidateLite],
    *,
    max_candidates: int,
) -> Tuple[List[MemoryCandidateLite], CandidateQualityReport]:
    """Normalize model/rule candidates before write planning.

    This is deliberately not a semantic classifier. Its job is to enforce the
    memory quality contract around bounded size, dedupe, and debuggability while
    leaving higher-level accept/block decisions to the evaluator and shape
    planner.
    """

    raw_candidates = list(candidates)
    stats = _MutableQualityStats()
    deduped: dict[tuple[str, str, str, str], MemoryCandidateLite] = {}
    order: List[tuple[str, str, str, str]] = []

    for candidate in raw_candidates:
        cleaned = _clean_candidate(candidate, stats)
        if not cleaned.text:
            stats.dropped_empty_count += 1
            continue
        key = _candidate_key(cleaned)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = cleaned
            order.append(key)
            continue
        stats.duplicate_count += 1
        if _candidate_rank(cleaned) > _candidate_rank(existing):
            deduped[key] = cleaned

    result = [deduped[key] for key in order[: max(0, max_candidates)]]
    if len(order) > len(result):
        stats.notes.append("max_candidates_applied")
    report = CandidateQualityReport(
        input_count=len(raw_candidates),
        output_count=len(result),
        duplicate_count=stats.duplicate_count,
        clipped_text_count=stats.clipped_text_count,
        clipped_reason_count=stats.clipped_reason_count,
        dropped_empty_count=stats.dropped_empty_count,
        notes=stats.notes,
    )
    return result, report


def _clean_candidate(candidate: MemoryCandidateLite, stats: _MutableQualityStats) -> MemoryCandidateLite:
    text, text_clipped = _clean_text(candidate.text, max_chars=MAX_CANDIDATE_TEXT_CHARS)
    reason, reason_clipped = _clean_text(candidate.reason, max_chars=MAX_CANDIDATE_REASON_CHARS)
    if text_clipped:
        stats.clipped_text_count += 1
    if reason_clipped:
        stats.clipped_reason_count += 1
    return MemoryCandidateLite(
        text=text,
        type=candidate.type,
        scope=candidate.scope,
        action=candidate.action,
        importance=candidate.importance,
        reason=reason,
        stability=candidate.stability,
        candidate_id=candidate.candidate_id,
    )


def _clean_text(value: str, *, max_chars: int) -> tuple[str, bool]:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if len(compact) <= max_chars:
        return compact, False
    clipped = compact[: max(0, max_chars - 3)].rstrip() + "..."
    return clipped, True


def _candidate_key(candidate: MemoryCandidateLite) -> tuple[str, str, str, str]:
    return (
        candidate.type,
        candidate.scope,
        candidate.action,
        re.sub(r"\s+", " ", candidate.text.lower()).strip(),
    )


def _candidate_rank(candidate: MemoryCandidateLite) -> tuple[float, int, int]:
    stability_rank = {"stable": 3, "evolving": 2, "temporary": 1, "unknown": 0}.get(candidate.stability, 0)
    reason_rank = 1 if candidate.reason else 0
    return (candidate.importance, stability_rank, reason_rank)
