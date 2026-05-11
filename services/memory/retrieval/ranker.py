from __future__ import annotations

from typing import Dict, List, Tuple

from services.memory.retrieval.schemas import RetrievalCandidate, RetrievalPack, RetrievalPackItem, RetrievalPlan


PATH_PRIOR = {
    "exact_record": 0.35,
    "graph_one_hop": 0.25,
    "lexical_projection": 0.15,
    "recent_evidence": 0.05,
    "review_items": 0.1,
}

CONFLICT_MARKERS = (
    "conflict",
    "contradict",
    "supersede",
    "replace",
    "instead",
    "no longer",
    "冲突",
    "矛盾",
    "取代",
    "不再",
)


def build_retrieval_pack(plan: RetrievalPlan, candidates: List[RetrievalCandidate]) -> RetrievalPack:
    ranked = sorted(_dedupe(candidates), key=_rank_key, reverse=True)
    items: List[RetrievalPackItem] = []
    trace: List[Dict[str, object]] = []
    used_chars = 0

    for candidate in ranked:
        score = _combined_score(candidate)
        if score < plan.min_score:
            trace.append(_trace(candidate, score, "excluded", "below_min_score"))
            continue
        if _is_redundant_with_context(candidate.text, plan.recent_context_text):
            trace.append(_trace(candidate, score, "excluded", "redundant_with_recent_context"))
            continue
        item_chars = len(candidate.text)
        if len(items) >= plan.max_pack_items:
            trace.append(_trace(candidate, score, "excluded", "item_budget_exhausted"))
            continue
        if used_chars + item_chars > plan.max_pack_chars:
            trace.append(_trace(candidate, score, "excluded", "char_budget_exhausted"))
            continue
        items.append(
            RetrievalPackItem(
                memory_id=candidate.memory_id,
                source=candidate.source,
                memory_type=candidate.memory_type,
                scope=candidate.scope,
                text=candidate.text,
                reason=candidate.reason,
                score=round(score, 4),
                authority=candidate.authority,
                intent=candidate.intent,
                evidence_episode_ids=candidate.evidence_episode_ids,
                load_mode=candidate.load_mode,
                conflict=candidate.conflict or _looks_conflicting(candidate.text),
            )
        )
        used_chars += item_chars
        trace.append(_trace(candidate, score, "included", candidate.reason))

    return RetrievalPack(
        intent=plan.intent,
        items=items,
        omitted_count=max(0, len(ranked) - len(items)),
        estimated_chars=used_chars,
        trace=trace,
    )


def _dedupe(candidates: List[RetrievalCandidate]) -> List[RetrievalCandidate]:
    seen = set()
    result: List[RetrievalCandidate] = []
    for candidate in candidates:
        key = candidate.memory_id or candidate.edge_id or candidate.projection_id or _normalize_text(candidate.text)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _rank_key(candidate: RetrievalCandidate) -> Tuple[float, float, str]:
    combined = _combined_score(candidate)
    source_prior = PATH_PRIOR.get(candidate.source, 0.0)
    return combined, source_prior, candidate.text


def _combined_score(candidate: RetrievalCandidate) -> float:
    priority_bonus = max(0.0, min(candidate.source_priority, 1.0)) * 0.1
    return min(1.0, candidate.score + PATH_PRIOR.get(candidate.source, 0.0) + priority_bonus)


def _trace(candidate: RetrievalCandidate, score: float, decision: str, reason: str) -> Dict[str, object]:
    return {
        "source": candidate.source,
        "memory_id": candidate.memory_id,
        "projection_id": candidate.projection_id,
        "edge_id": candidate.edge_id,
        "score": round(score, 4),
        "decision": decision,
        "reason": reason,
        "authority": candidate.authority,
        "intent": candidate.intent,
        "conflict": candidate.conflict or _looks_conflicting(candidate.text),
    }


def _normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())


def _is_redundant_with_context(text: str, recent_context_text: str) -> bool:
    normalized_text = _normalize_text(text)
    normalized_context = _normalize_text(recent_context_text)
    if not normalized_text or not normalized_context:
        return False
    if len(normalized_text) >= 24 and normalized_text in normalized_context:
        return True
    text_terms = {term for term in normalized_text.split() if len(term) >= 4}
    if len(text_terms) < 4:
        return False
    context_terms = set(normalized_context.split())
    overlap = len(text_terms & context_terms) / max(1, len(text_terms))
    return overlap >= 0.75


def _looks_conflicting(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in CONFLICT_MARKERS)
