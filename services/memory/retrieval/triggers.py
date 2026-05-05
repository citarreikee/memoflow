from __future__ import annotations

import re
from typing import List

from services.memory.retrieval.lexical import contains_any
from services.memory.retrieval.schemas import RetrievalIntent, RetrievalPlan


PREFERENCE_TERMS = (
    "prefer",
    "preference",
    "like",
    "习惯",
    "偏好",
    "喜欢",
    "倾向",
)
PROFILE_TERMS = ("profile", "about me", "我的", "我是谁", "个人信息")
PROJECT_TERMS = ("project", "repo", "workspace", "spec", "文档", "项目", "规范", "依赖", "实现顺序")
CONTINUITY_TERMS = (
    "remember",
    "memory",
    "previous",
    "earlier",
    "last time",
    "记得",
    "记忆",
    "之前",
    "上次",
    "刚才",
    "历史",
)
DECISION_TERMS = ("decision", "decide", "rule", "决定", "规则", "约定")
TASK_TERMS = ("todo", "task", "state", "pending", "任务", "进度", "状态", "待办")
RELATION_TERMS = (
    "depends on",
    "dependency",
    "relation",
    "related",
    "依赖",
    "关系",
    "关联",
    "取代",
    "冲突",
)


def build_retrieval_plan(
    *,
    user_message: str,
    session_key: str,
    token_budget: int,
    enabled: bool,
) -> RetrievalPlan:
    intent = classify_intent(user_message, enabled=enabled, token_budget=token_budget)
    paths: List[str] = []
    if intent.kind != "none":
        paths.append("exact_record")
        paths.append("lexical_projection")
    if intent.needs_graph:
        paths.append("graph_one_hop")
    return RetrievalPlan(
        intent=intent,
        paths=paths,
        namespace=session_key,
        scopes=intent.scopes or ["session", "user", "workspace"],
        limit_per_path=8,
        max_pack_items=5,
        max_pack_chars=1600,
        min_score=0.15,
    )


def classify_intent(user_message: str, *, enabled: bool, token_budget: int) -> RetrievalIntent:
    query = (user_message or "").strip()
    if not enabled:
        return RetrievalIntent(kind="none", query=query)
    if token_budget < 1000:
        return RetrievalIntent(kind="none", query=query)
    if not query or _is_trivial(query):
        return RetrievalIntent(kind="none", query=query)

    memory_types: List[str] = []
    kind = "none"
    needs_preferences = False
    needs_graph = False
    scopes = ["session", "user", "workspace"]

    if contains_any(query, PREFERENCE_TERMS):
        kind = "preference_lookup"
        memory_types.extend(["preference", "profile_fact"])
        needs_preferences = True
    if contains_any(query, PROFILE_TERMS):
        kind = "profile_lookup" if kind == "none" else kind
        memory_types.append("profile_fact")
    if contains_any(query, PROJECT_TERMS):
        kind = "project_rule_lookup" if kind == "none" else kind
        memory_types.extend(["project_rule", "procedure", "decision"])
    if contains_any(query, TASK_TERMS):
        kind = "task_state_lookup" if kind == "none" else kind
        memory_types.append("task_state")
    if contains_any(query, DECISION_TERMS):
        kind = "decision_lookup" if kind == "none" else kind
        memory_types.append("decision")
    if contains_any(query, RELATION_TERMS):
        kind = "relation_lookup" if kind == "none" else kind
        memory_types.append("entity_relation")
        needs_graph = True
    if contains_any(query, CONTINUITY_TERMS):
        kind = "broad_recall" if kind == "none" else kind

    entities = _extract_entities(query)
    if entities and kind == "none":
        kind = "broad_recall"

    if kind == "none":
        return RetrievalIntent(kind="none", query=query)
    return RetrievalIntent(
        kind=kind,
        query=query,
        entities=entities,
        scopes=scopes,
        memory_types=_dedupe(memory_types),
        needs_graph=needs_graph,
        needs_preferences=needs_preferences,
    )


def _is_trivial(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized in {"hi", "hello", "hey", "你好", "嗨"}


def _extract_entities(text: str) -> List[str]:
    latin_entities = re.findall(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", text)
    quoted_entities = re.findall(r"[`'\"]([^`'\"]{2,40})[`'\"]", text)
    return _dedupe(latin_entities + quoted_entities)


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result

