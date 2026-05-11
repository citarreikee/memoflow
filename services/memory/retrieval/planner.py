from __future__ import annotations

import re
from typing import Dict, Iterable, List

from services.memory.retrieval.lexical import contains_any
from services.memory.retrieval.schemas import ReconstructedQuery, RetrievalIntent, RetrievalPlan, SourceStrategy


PREFERENCE_TERMS = ("prefer", "preference", "like", "style", "偏好", "喜欢", "习惯", "倾向")
PROFILE_TERMS = ("profile", "about me", "my ", "我是", "我的", "个人信息")
PROJECT_TERMS = ("project", "repo", "workspace", "spec", "rule", "项目", "规范", "工程", "实现")
DECISION_TERMS = ("decision", "decide", "strategy", "rule", "决定", "规则", "策略", "方案")
TASK_TERMS = ("todo", "task", "state", "pending", "progress", "任务", "进度", "状态", "待办", "未解决")
RELATION_TERMS = ("depends", "dependency", "relation", "related", "conflict", "依赖", "关系", "关联", "冲突", "取代")
CONTINUITY_TERMS = ("remember", "memory", "previous", "earlier", "last time", "上次", "之前", "刚才", "历史", "那个")

LEGACY_KIND_BY_INTENT = {
    "user_preferences": "preference_lookup",
    "profile": "profile_lookup",
    "project_rules": "project_rule_lookup",
    "active_state": "task_state_lookup",
    "prior_decisions": "decision_lookup",
    "dependency_relations": "relation_lookup",
    "conflict_check": "relation_lookup",
    "broad_recall": "broad_recall",
}

MEMORY_TYPES_BY_INTENT = {
    "user_preferences": ["preference", "profile_fact"],
    "profile": ["profile_fact"],
    "project_rules": ["project_rule", "procedure", "decision"],
    "active_state": ["task_state", "decision"],
    "prior_decisions": ["decision"],
    "dependency_relations": ["entity_relation"],
    "conflict_check": ["decision", "project_rule", "entity_relation"],
    "broad_recall": [],
}

INTENT_SOURCE_STRATEGIES: Dict[str, SourceStrategy] = {
    "user_preferences": SourceStrategy(
        intent="user_preferences",
        primary_sources=["semantic_kv"],
        secondary_sources=["vector_projection"],
        budget_ratio={"semantic_kv": 0.8, "vector_projection": 0.2},
        vector_fallback=True,
    ),
    "project_rules": SourceStrategy(
        intent="project_rules",
        primary_sources=["semantic_kv"],
        secondary_sources=["relation_graph", "vector_projection"],
        budget_ratio={"semantic_kv": 0.65, "relation_graph": 0.2, "vector_projection": 0.15},
        vector_fallback=True,
    ),
    "prior_decisions": SourceStrategy(
        intent="prior_decisions",
        primary_sources=["semantic_kv"],
        secondary_sources=["relation_graph", "vector_projection"],
        budget_ratio={"semantic_kv": 0.7, "relation_graph": 0.2, "vector_projection": 0.1},
        vector_fallback=True,
    ),
    "active_state": SourceStrategy(
        intent="active_state",
        primary_sources=["semantic_kv"],
        secondary_sources=["vector_projection"],
        budget_ratio={"semantic_kv": 0.85, "vector_projection": 0.15},
        vector_fallback=True,
    ),
    "dependency_relations": SourceStrategy(
        intent="dependency_relations",
        primary_sources=["relation_graph"],
        secondary_sources=["semantic_kv", "review_items"],
        budget_ratio={"relation_graph": 0.6, "semantic_kv": 0.25, "review_items": 0.15},
        vector_fallback=False,
    ),
    "conflict_check": SourceStrategy(
        intent="conflict_check",
        primary_sources=["review_items"],
        secondary_sources=["semantic_kv", "relation_graph"],
        budget_ratio={"review_items": 0.45, "semantic_kv": 0.35, "relation_graph": 0.2},
        vector_fallback=False,
    ),
    "broad_recall": SourceStrategy(
        intent="broad_recall",
        primary_sources=["semantic_kv"],
        secondary_sources=["vector_projection", "relation_graph"],
        budget_ratio={"semantic_kv": 0.55, "vector_projection": 0.3, "relation_graph": 0.15},
        vector_fallback=True,
    ),
}


class RetrievalIntentPlanner:
    def build_plan(
        self,
        *,
        user_message: str,
        reconstructed_queries: List[ReconstructedQuery],
        session_key: str,
        token_budget: int,
        enabled: bool,
        recent_context_text: str = "",
    ) -> RetrievalPlan:
        raise NotImplementedError


class RuleRetrievalIntentPlanner(RetrievalIntentPlanner):
    def build_plan(
        self,
        *,
        user_message: str,
        reconstructed_queries: List[ReconstructedQuery],
        session_key: str,
        token_budget: int,
        enabled: bool,
        recent_context_text: str = "",
    ) -> RetrievalPlan:
        query = _joined_query(user_message, reconstructed_queries)
        if not enabled or token_budget < 1000 or not query or _is_trivial(user_message):
            intent = RetrievalIntent(kind="none", query=(user_message or "").strip())
            return _empty_plan(intent, session_key, recent_context_text)

        intents = self._classify_intents(user_message=user_message, reconstructed_queries=reconstructed_queries)
        if not intents:
            intent = RetrievalIntent(kind="none", query=(user_message or "").strip())
            return _empty_plan(intent, session_key, recent_context_text)

        strategies = [INTENT_SOURCE_STRATEGIES[name] for name in intents if name in INTENT_SOURCE_STRATEGIES]
        paths = _paths_from_strategies(strategies)
        memory_types = _dedupe([item for name in intents for item in MEMORY_TYPES_BY_INTENT.get(name, [])])
        kind = LEGACY_KIND_BY_INTENT.get(intents[0], "broad_recall")
        reconstructed_payload = [item.to_dict() for item in reconstructed_queries]
        intent = RetrievalIntent(
            kind=kind,
            query=query,
            entities=_extract_entities(query),
            scopes=["session", "user", "workspace"],
            memory_types=memory_types,
            needs_graph=any("relation_graph" in (s.primary_sources + s.secondary_sources) for s in strategies),
            needs_preferences="user_preferences" in intents,
            intents=intents,
            reconstructed_queries=reconstructed_payload,
            sufficiency_threshold=_sufficiency_threshold(intents),
        )
        return RetrievalPlan(
            intent=intent,
            paths=paths,
            namespace=session_key,
            scopes=intent.scopes,
            limit_per_path=8,
            max_pack_items=6,
            max_pack_chars=1600,
            min_score=0.15,
            source_strategies=strategies,
            budget_allocation=_budget_allocation(strategies, 1600),
            recent_context_text=recent_context_text,
        )

    def _classify_intents(self, *, user_message: str, reconstructed_queries: List[ReconstructedQuery]) -> List[str]:
        intents: List[str] = []
        for reconstructed in reconstructed_queries:
            intents.extend(reconstructed.target_intents)
        text = _joined_query(user_message, reconstructed_queries)
        if contains_any(text, PREFERENCE_TERMS):
            intents.append("user_preferences")
        if contains_any(text, PROFILE_TERMS):
            intents.append("profile")
        if contains_any(text, PROJECT_TERMS):
            intents.append("project_rules")
        if contains_any(text, TASK_TERMS):
            intents.append("active_state")
        if contains_any(text, DECISION_TERMS):
            intents.append("prior_decisions")
        if contains_any(text, RELATION_TERMS):
            intents.append("dependency_relations")
        if contains_any(text, CONTINUITY_TERMS):
            intents.append("broad_recall")
        if _extract_entities(text) and not intents:
            intents.append("broad_recall")
        return _dedupe([intent for intent in intents if intent in INTENT_SOURCE_STRATEGIES])


class LLMRetrievalIntentPlanner(RetrievalIntentPlanner):
    """Future sidecar slot: LLM decides retrieval intent and source strategy, code executes it."""

    def build_plan(
        self,
        *,
        user_message: str,
        reconstructed_queries: List[ReconstructedQuery],
        session_key: str,
        token_budget: int,
        enabled: bool,
        recent_context_text: str = "",
    ) -> RetrievalPlan:
        return RuleRetrievalIntentPlanner().build_plan(
            user_message=user_message,
            reconstructed_queries=reconstructed_queries,
            session_key=session_key,
            token_budget=token_budget,
            enabled=enabled,
            recent_context_text=recent_context_text,
        )


def _empty_plan(intent: RetrievalIntent, session_key: str, recent_context_text: str) -> RetrievalPlan:
    return RetrievalPlan(
        intent=intent,
        paths=[],
        namespace=session_key,
        scopes=[],
        limit_per_path=0,
        max_pack_items=0,
        max_pack_chars=0,
        min_score=1.0,
        recent_context_text=recent_context_text,
    )


def _joined_query(user_message: str, reconstructed_queries: List[ReconstructedQuery]) -> str:
    parts = [query.query for query in reconstructed_queries if query.query]
    if not parts and user_message:
        parts.append(user_message)
    return "\n".join(_dedupe(parts)).strip()


def _paths_from_strategies(strategies: List[SourceStrategy]) -> List[str]:
    source_to_path = {
        "semantic_kv": "exact_record",
        "vector_projection": "lexical_projection",
        "relation_graph": "graph_one_hop",
        "review_items": "review_items",
        "checkpoint": "checkpoint",
    }
    sources: List[str] = []
    for strategy in strategies:
        sources.extend(strategy.primary_sources)
        sources.extend(strategy.secondary_sources)
        if strategy.vector_fallback:
            sources.append("vector_projection")
    return _dedupe([source_to_path[source] for source in sources if source in source_to_path and source != "checkpoint"])


def _budget_allocation(strategies: List[SourceStrategy], total_chars: int) -> Dict[str, int]:
    weights: Dict[str, float] = {}
    for strategy in strategies:
        for source, ratio in strategy.budget_ratio.items():
            weights[source] = weights.get(source, 0.0) + ratio
    total_weight = sum(weights.values()) or 1.0
    return {source: max(80, int(total_chars * weight / total_weight)) for source, weight in weights.items()}


def _sufficiency_threshold(intents: List[str]) -> str:
    if any(intent in {"dependency_relations", "conflict_check"} for intent in intents):
        return "high"
    if any(intent in {"prior_decisions", "project_rules"} for intent in intents):
        return "medium"
    return "low"


def _is_trivial(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return normalized in {"hi", "hello", "hey", "你好", "嗨"}


def _extract_entities(text: str) -> List[str]:
    latin_entities = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text or "")
    quoted_entities = re.findall(r"[`'\"]([^`'\"]{2,40})[`'\"]", text or "")
    return _dedupe(latin_entities + quoted_entities)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
