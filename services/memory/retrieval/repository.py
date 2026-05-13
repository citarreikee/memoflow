from __future__ import annotations

from typing import List

from services.memory.retrieval.lexical import lexical_score
from services.memory.retrieval.schemas import RetrievalCandidate, RetrievalPlan
from services.memory.storage.sqlite_store import MemorySQLiteStore


class MemoryRetrievalRepository:
    def __init__(self, store: MemorySQLiteStore) -> None:
        self.store = store

    def search(self, plan: RetrievalPlan) -> List[RetrievalCandidate]:
        candidates: List[RetrievalCandidate] = []
        if "exact_record" in plan.paths:
            candidates.extend(self._search_records(plan))
        if "lexical_projection" in plan.paths:
            candidates.extend(self._search_projections(plan))
        if "graph_one_hop" in plan.paths:
            candidates.extend(self._search_graph(plan))
        if "review_items" in plan.paths:
            candidates.extend(self._search_review_items(plan))
        return candidates

    def _search_records(self, plan: RetrievalPlan) -> List[RetrievalCandidate]:
        rows = self.store.search_active_records(
            query=plan.intent.query,
            namespace=plan.namespace,
            scopes=plan.scopes,
            memory_types=plan.intent.memory_types,
            limit=plan.limit_per_path,
        )
        candidates: List[RetrievalCandidate] = []
        for row in rows:
            text = str(row.get("value") or row.get("key") or "").strip()
            score = lexical_score(plan.intent.query, f"{row.get('key', '')} {text}") or 0.5
            candidates.append(
                RetrievalCandidate(
                    source="exact_record",
                    memory_id=str(row.get("memory_id") or ""),
                    projection_id=None,
                    edge_id=None,
                    scope=str(row.get("scope") or ""),
                    memory_type=str(row.get("type") or ""),
                    text=text,
                    score=score,
                    reason="active_record_match",
                    payload=dict(row),
                    authority="authoritative",
                    intent=_candidate_intent(plan),
                    source_priority=1.0,
                )
            )
        return candidates

    def _search_projections(self, plan: RetrievalPlan) -> List[RetrievalCandidate]:
        rows = self.store.search_vector_projections(
            query=plan.intent.query,
            namespace=plan.namespace,
            scopes=plan.scopes,
            memory_types=plan.intent.memory_types,
            limit=plan.limit_per_path,
        )
        candidates: List[RetrievalCandidate] = []
        for row in rows:
            text = str(row.get("text") or row.get("record_value") or "").strip()
            score = lexical_score(plan.intent.query, text)
            if score <= 0:
                continue
            candidates.append(
                RetrievalCandidate(
                    source="lexical_projection",
                    memory_id=row.get("memory_id"),
                    projection_id=str(row.get("projection_id") or ""),
                    edge_id=None,
                    scope=str(row.get("record_scope") or row.get("scope") or ""),
                    memory_type=str(row.get("record_type") or row.get("type") or ""),
                    text=text,
                    score=score,
                    reason="projection_lexical_match",
                    payload=dict(row),
                    authority="projection",
                    intent=_candidate_intent(plan),
                    source_priority=0.7,
                )
            )
        return candidates

    def _search_graph(self, plan: RetrievalPlan) -> List[RetrievalCandidate]:
        rows = self.store.search_graph_edges(
            query=plan.intent.query,
            scopes=plan.scopes,
            limit=plan.limit_per_path,
        )
        candidates: List[RetrievalCandidate] = []
        for row in rows:
            text = str(row.get("payload_json") or row.get("relation_type") or "").strip()
            score = lexical_score(plan.intent.query, text) or 0.35
            candidates.append(
                RetrievalCandidate(
                    source="graph_one_hop",
                    memory_id=row.get("source_memory_id") or row.get("target_memory_id"),
                    projection_id=None,
                    edge_id=str(row.get("edge_id") or ""),
                    scope=str(row.get("scope") or ""),
                    memory_type="entity_relation",
                    text=text,
                    score=score,
                    reason="graph_edge_match",
                    payload=dict(row),
                    authority="authoritative",
                    intent="dependency_relations",
                    source_priority=0.9,
                )
            )
        dag_rows = self.store.search_dag_edges(
            query=plan.intent.query,
            scopes=plan.scopes,
            limit=plan.limit_per_path,
        )
        for row in dag_rows:
            text = _dag_edge_text(row)
            score = lexical_score(plan.intent.query, text) or 0.4
            candidates.append(
                RetrievalCandidate(
                    source="dag_one_hop",
                    memory_id=row.get("source_ref_id") if row.get("source_node_kind") == "memory" else None,
                    projection_id=None,
                    edge_id=str(row.get("dag_edge_id") or ""),
                    scope=str(row.get("scope") or ""),
                    memory_type="dependency_dag",
                    text=text,
                    score=score,
                    reason="dag_edge_match",
                    payload=dict(row),
                    authority="authoritative",
                    intent="dependency_relations",
                    source_priority=0.95,
                )
            )
        return candidates

    def _search_review_items(self, plan: RetrievalPlan) -> List[RetrievalCandidate]:
        rows = self.store.search_review_items(
            query=plan.intent.query,
            session_id=plan.namespace,
            scopes=plan.scopes,
            limit=plan.limit_per_path,
        )
        candidates: List[RetrievalCandidate] = []
        for row in rows:
            text = str(row.get("reason") or row.get("payload_json") or "").strip()
            score = lexical_score(plan.intent.query, text) or 0.3
            candidates.append(
                RetrievalCandidate(
                    source="review_items",
                    memory_id=row.get("target_memory_id"),
                    projection_id=None,
                    edge_id=str(row.get("review_id") or ""),
                    scope=str(row.get("scope") or ""),
                    memory_type="review_item",
                    text=text,
                    score=score,
                    reason="review_item_match",
                    payload=dict(row),
                    authority="review_only",
                    intent="conflict_check",
                    source_priority=0.55,
                    conflict=True,
                )
            )
        return candidates


def _candidate_intent(plan: RetrievalPlan) -> str:
    if plan.intent.intents:
        return plan.intent.intents[0]
    return plan.intent.kind


def _dag_edge_text(row: dict) -> str:
    source = str(row.get("source_label") or row.get("source_ref_id") or "").strip()
    target = str(row.get("target_label") or row.get("target_ref_id") or "").strip()
    edge_type = str(row.get("edge_type") or "depends_on").strip()
    return f"{source} {edge_type} {target}".strip()
