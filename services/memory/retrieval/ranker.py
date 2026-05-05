from __future__ import annotations

from typing import Dict, List, Tuple

from services.memory.retrieval.schemas import RetrievalCandidate, RetrievalPack, RetrievalPackItem, RetrievalPlan


PATH_PRIOR = {
    "exact_record": 0.35,
    "graph_one_hop": 0.25,
    "lexical_projection": 0.15,
    "recent_evidence": 0.05,
}


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
    return min(1.0, candidate.score + PATH_PRIOR.get(candidate.source, 0.0))


def _trace(candidate: RetrievalCandidate, score: float, decision: str, reason: str) -> Dict[str, object]:
    return {
        "source": candidate.source,
        "memory_id": candidate.memory_id,
        "projection_id": candidate.projection_id,
        "edge_id": candidate.edge_id,
        "score": round(score, 4),
        "decision": decision,
        "reason": reason,
    }


def _normalize_text(text: str) -> str:
    return " ".join((text or "").lower().split())

