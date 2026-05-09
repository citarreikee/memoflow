from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from services.memory.formation.integration_schemas import ExistingMemorySnapshot, MemoryIntegrationPlan
from services.memory.formation.schemas import MemoryCandidateLite


HIGH_OVERLAP = 0.90
MEDIUM_OVERLAP = 0.50
LOW_CONFIDENCE_REVIEW = 0.62

RELATION_MARKERS = {
    "depends_on": ("depends_on", "depends on", "dependency", "requires"),
    "blocks": ("blocks", "blocked by", "blocking"),
    "supersedes": ("supersedes", "replaces", "instead of", "取代"),
    "contradicts": ("contradicts", "conflicts with", "opposite of"),
    "derived_from": ("derived_from", "derived from", "based on"),
}


def plan_memory_integration(
    candidate: MemoryCandidateLite,
    *,
    existing_memories: Iterable[ExistingMemorySnapshot],
) -> MemoryIntegrationPlan:
    """Plan how a candidate should integrate with existing memory.

    This dry-run planner is intentionally conservative. It establishes the data
    contract that a future LLM integration planner must satisfy, while avoiding
    direct writes or hidden mutation.
    """

    if candidate.action == "NOOP" or candidate.type == "non_memory":
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NOOP",
            confidence=1.0,
            rationale="Candidate is explicitly non-memory/noop.",
            blocked_reasons=["candidate_noop"],
        )

    existing = [memory for memory in existing_memories if memory.status == "active"]
    same_bucket = [memory for memory in existing if memory.type == candidate.type and memory.scope == candidate.scope]
    best_memory, best_score = _best_overlap(candidate.text, same_bucket)

    if candidate.action == "UPDATE":
        if best_memory:
            return MemoryIntegrationPlan(
                candidate_id=candidate.candidate_id or "",
                action="UPDATE",
                confidence=round(max(candidate.importance, best_score), 3),
                rationale="Candidate explicitly updates an existing memory in the same type/scope bucket.",
                target_memory_id=best_memory.memory_id,
                related_memory_ids=[best_memory.memory_id],
                suggested_text=candidate.text,
            )
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=round(candidate.importance, 3),
            rationale="Candidate requests an update but no matching active memory was found.",
            needs_review_reasons=["update_target_missing"],
        )

    if _looks_like_supersession(candidate.text, same_bucket):
        target = best_memory or (same_bucket[0] if same_bucket else None)
        if target:
            return MemoryIntegrationPlan(
                candidate_id=candidate.candidate_id or "",
                action="SUPERSEDE",
                confidence=round(max(candidate.importance, best_score), 3),
                rationale="Candidate explicitly supersedes an existing memory.",
                target_memory_id=target.memory_id,
                related_memory_ids=[target.memory_id],
                graph_relations=[{"relation_type": "supersedes", "target_memory_id": target.memory_id}],
                suggested_text=candidate.text,
            )
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=round(candidate.importance, 3),
            rationale="Candidate expresses supersession but no matching active memory was found.",
            needs_review_reasons=["supersession_target_missing"],
        )

    if _looks_like_conflict(candidate.text, same_bucket):
        target = same_bucket[0] if same_bucket else None
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=0.5,
            rationale="Candidate may conflict with an existing memory and needs review before update/supersession.",
            target_memory_id=target.memory_id if target else None,
            related_memory_ids=[target.memory_id] if target else [],
            needs_review_reasons=["possible_conflict"],
        )

    if best_memory and _covers(best_memory.text, candidate.text, best_score):
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NOOP",
            confidence=round(best_score, 3),
            rationale="Existing memory already covers this candidate.",
            target_memory_id=best_memory.memory_id,
            blocked_reasons=["duplicate_existing_memory"],
        )

    if best_memory and best_score >= MEDIUM_OVERLAP:
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="MERGE",
            confidence=round(best_score, 3),
            rationale="Candidate overlaps an existing memory and should be merged or used as extra evidence.",
            target_memory_id=best_memory.memory_id,
            suggested_text=_merge_text(best_memory.text, candidate.text),
        )

    relation_type, relation_target = _detect_relation(candidate.text, existing)
    if candidate.type == "entity_relation":
        if relation_type and relation_target:
            return MemoryIntegrationPlan(
                candidate_id=candidate.candidate_id or "",
                action="LINK",
                confidence=0.78,
                rationale="Candidate states an explicit relation to existing memory.",
                target_memory_id=relation_target.memory_id,
                related_memory_ids=[relation_target.memory_id],
                graph_relations=[{"relation_type": relation_type, "target_memory_id": relation_target.memory_id}],
            )
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=0.45,
            rationale="Relation candidate lacks an explicit target in existing memory.",
            needs_review_reasons=["relation_target_missing"],
        )

    if candidate.importance < LOW_CONFIDENCE_REVIEW or candidate.stability == "unknown":
        return MemoryIntegrationPlan(
            candidate_id=candidate.candidate_id or "",
            action="NEEDS_REVIEW",
            confidence=round(candidate.importance, 3),
            rationale="Candidate is plausible but not confident enough for automatic integration.",
            needs_review_reasons=["low_confidence_or_unknown_stability"],
        )

    return MemoryIntegrationPlan(
        candidate_id=candidate.candidate_id or "",
        action="ADD",
        confidence=round(candidate.importance, 3),
        rationale="No overlapping active memory found; candidate can be added if deterministic gates pass.",
        suggested_text=candidate.text,
    )


def plan_memory_integrations(
    candidates: Iterable[MemoryCandidateLite],
    *,
    existing_memories: Iterable[ExistingMemorySnapshot],
) -> List[MemoryIntegrationPlan]:
    snapshot = list(existing_memories)
    return [plan_memory_integration(candidate, existing_memories=snapshot) for candidate in candidates]


def _best_overlap(
    text: str,
    existing: Iterable[ExistingMemorySnapshot],
) -> Tuple[Optional[ExistingMemorySnapshot], float]:
    best: Optional[ExistingMemorySnapshot] = None
    best_score = 0.0
    for memory in existing:
        score = _jaccard(_tokens(text), _tokens(memory.text))
        if score > best_score:
            best = memory
            best_score = score
    return best, best_score


def _detect_relation(
    text: str,
    existing: List[ExistingMemorySnapshot],
) -> Tuple[Optional[str], Optional[ExistingMemorySnapshot]]:
    lowered = text.lower()
    relation_type = None
    for current_type, markers in RELATION_MARKERS.items():
        if any(marker in lowered for marker in markers):
            relation_type = current_type
            break
    if not relation_type:
        return None, None
    target, score = _best_overlap(text, existing)
    if target and score >= 0.12:
        return relation_type, target
    return relation_type, None


def _looks_like_conflict(text: str, same_bucket: List[ExistingMemorySnapshot]) -> bool:
    if not same_bucket:
        return False
    lowered = text.lower()
    conflict_markers = ("instead", "no longer", "not ", "do not", "conflict")
    return any(marker in lowered for marker in conflict_markers)


def _looks_like_supersession(text: str, same_bucket: List[ExistingMemorySnapshot]) -> bool:
    if not same_bucket:
        return False
    lowered = text.lower()
    supersession_markers = ("supersedes", "supersede", "replaces", "replace", "instead of")
    return any(marker in lowered for marker in supersession_markers)


def _covers(existing: str, candidate: str, overlap_score: float) -> bool:
    existing_norm = _normalize(existing)
    candidate_norm = _normalize(candidate)
    if existing_norm and existing_norm == candidate_norm:
        return True
    if existing_norm and candidate_norm in existing_norm:
        return True
    return overlap_score >= HIGH_OVERLAP


def _merge_text(existing: str, candidate: str) -> str:
    if _normalize(candidate) in _normalize(existing):
        return existing
    if _normalize(existing) in _normalize(candidate):
        return candidate
    return f"{existing} / Additional evidence: {candidate}"


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9_\-]+", (text or "").lower()) if len(token) >= 2}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())

