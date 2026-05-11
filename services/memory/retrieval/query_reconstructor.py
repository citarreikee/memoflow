from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from services.memory.retrieval.schemas import ReconstructedQuery


CONTINUITY_TERMS = (
    "previous",
    "earlier",
    "last time",
    "that issue",
    "that problem",
    "progress",
    "上次",
    "之前",
    "刚才",
    "那个",
    "问题",
    "方案",
    "决定",
    "进度",
)

STATE_TERMS = ("todo", "pending", "progress", "state", "open loop", "待办", "进度", "状态", "未解决")
DECISION_TERMS = ("decision", "decide", "rule", "strategy", "决定", "规则", "策略", "方案")
RELATION_TERMS = ("depends", "dependency", "relation", "related", "冲突", "依赖", "关系", "关联", "取代")


class QueryReconstructor:
    def reconstruct(
        self,
        *,
        user_message: str,
        checkpoint: Optional[Dict[str, Any]] = None,
        recent_turns: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> List[ReconstructedQuery]:
        raise NotImplementedError


class RuleQueryReconstructor(QueryReconstructor):
    def reconstruct(
        self,
        *,
        user_message: str,
        checkpoint: Optional[Dict[str, Any]] = None,
        recent_turns: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> List[ReconstructedQuery]:
        raw = (user_message or "").strip()
        if not raw:
            return []

        context_text = _context_text(checkpoint=checkpoint, recent_turns=recent_turns)
        context_terms = _keywords(context_text)
        raw_terms = _keywords(raw)
        queries: List[ReconstructedQuery] = []

        if _contains(raw, CONTINUITY_TERMS):
            anchor = " ".join((context_terms or raw_terms)[:8]) or raw
            queries.append(
                ReconstructedQuery(
                    query=f"active open loops and current task state about {anchor}",
                    target_intents=["active_state"],
                    target_source_hints=["checkpoint", "semantic_kv"],
                    reason="continuity_reference_to_state",
                )
            )
            queries.append(
                ReconstructedQuery(
                    query=f"prior decisions and rules about {anchor}",
                    target_intents=["prior_decisions", "project_rules"],
                    target_source_hints=["semantic_kv", "relation_graph"],
                    reason="continuity_reference_to_decisions",
                )
            )

        if _contains(raw, STATE_TERMS):
            queries.append(
                ReconstructedQuery(
                    query=raw,
                    target_intents=["active_state"],
                    target_source_hints=["semantic_kv", "checkpoint"],
                    reason="state_terms_in_user_message",
                )
            )
        if _contains(raw, DECISION_TERMS):
            queries.append(
                ReconstructedQuery(
                    query=raw,
                    target_intents=["prior_decisions", "project_rules"],
                    target_source_hints=["semantic_kv"],
                    reason="decision_terms_in_user_message",
                )
            )
        if _contains(raw, RELATION_TERMS):
            queries.append(
                ReconstructedQuery(
                    query=raw,
                    target_intents=["dependency_relations", "conflict_check"],
                    target_source_hints=["relation_graph", "review_items"],
                    reason="relation_terms_in_user_message",
                )
            )

        queries.append(
            ReconstructedQuery(
                query=raw,
                target_intents=[],
                target_source_hints=["semantic_kv", "vector_projection"],
                reason="raw_query_fallback",
            )
        )
        return _dedupe_queries(queries)[:3]


class LLMQueryReconstructor(QueryReconstructor):
    """Future sidecar slot: LLM rewrites vague user language into executable retrieval queries."""

    def reconstruct(
        self,
        *,
        user_message: str,
        checkpoint: Optional[Dict[str, Any]] = None,
        recent_turns: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> List[ReconstructedQuery]:
        return RuleQueryReconstructor().reconstruct(
            user_message=user_message,
            checkpoint=checkpoint,
            recent_turns=recent_turns,
        )


def _context_text(*, checkpoint: Optional[Dict[str, Any]], recent_turns: Optional[Iterable[Dict[str, Any]]]) -> str:
    parts: List[str] = []
    if checkpoint:
        for key in ("task_kernel", "conversation", "task_state"):
            value = checkpoint.get(key)
            if value:
                parts.append(str(value))
        for key in ("open_loops", "decisions", "important_artifacts", "user_constraints"):
            value = checkpoint.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
    if recent_turns:
        for turn in recent_turns:
            if not isinstance(turn, dict):
                continue
            content = turn.get("content") or turn.get("text")
            if content:
                parts.append(str(content))
    return "\n".join(parts)


def _contains(text: str, terms: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in terms)


def _keywords(text: str) -> List[str]:
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text or "")
    stop = {"the", "and", "for", "that", "this", "with", "about", "what", "怎么", "什么", "这个", "那个"}
    result: List[str] = []
    for term in terms:
        normalized = term.lower() if term.isascii() else term
        if normalized in stop or normalized in result:
            continue
        result.append(normalized)
    return result[:12]


def _dedupe_queries(queries: List[ReconstructedQuery]) -> List[ReconstructedQuery]:
    seen = set()
    result: List[ReconstructedQuery] = []
    for query in queries:
        key = " ".join(query.query.lower().split())
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
    return result
