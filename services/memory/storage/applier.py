from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from services.memory.formation.schemas import MemoryWritePlan
from services.memory.formation.shape_planner import has_explicit_graph_relation
from services.memory.storage.keys import derive_memory_key, derive_namespace
from services.memory.storage.sqlite_store import MemorySQLiteStore


@dataclass
class MemoryApplyResult:
    canonical_writes: int = 0
    evidence_links: int = 0
    vector_projections: int = 0
    graph_edges: int = 0
    dag_nodes: int = 0
    dag_edges: int = 0
    file_suggestions: int = 0
    review_items: int = 0
    reindex_jobs: int = 0
    blocked_count: int = 0
    applied_plan_ids: List[str] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "canonical_writes": self.canonical_writes,
            "evidence_links": self.evidence_links,
            "vector_projections": self.vector_projections,
            "graph_edges": self.graph_edges,
            "dag_nodes": self.dag_nodes,
            "dag_edges": self.dag_edges,
            "file_suggestions": self.file_suggestions,
            "review_items": self.review_items,
            "reindex_jobs": self.reindex_jobs,
            "blocked_count": self.blocked_count,
            "applied_plan_ids": self.applied_plan_ids,
            "blocked_reasons": self.blocked_reasons,
        }


class MemoryWriteApplier:
    def __init__(self, store: MemorySQLiteStore) -> None:
        self.store = store

    def apply_plans(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plans: List[MemoryWritePlan],
    ) -> MemoryApplyResult:
        result = MemoryApplyResult()
        for plan in plans:
            self._apply_one(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        return result

    def _apply_one(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        if plan.status not in {"planned", "needs_review"}:
            result.blocked_count += 1
            result.blocked_reasons.extend(plan.blocked_reasons or [f"plan_status_{plan.status}"])
            return
        if not plan.evidence_episode_ids:
            result.blocked_count += 1
            result.blocked_reasons.append("evidence_missing")
            return
        if _has_review_write(plan) or plan.status == "needs_review" or plan.action in {"CONFLICT", "NEEDS_REVIEW"}:
            self._apply_review_item(session_id=session_id, plan=plan, result=result)
            return

        handlers = {
            "ADD": self._apply_add,
            "MERGE": self._apply_merge,
            "UPDATE": self._apply_update,
            "SUPERSEDE": self._apply_supersede,
            "LINK": self._apply_link,
            "DELETE": self._apply_delete,
        }
        handler = handlers.get(plan.action)
        if handler is None:
            result.blocked_count += 1
            result.blocked_reasons.append(f"unsupported_plan_action:{plan.action}")
            return
        blocked_before = result.blocked_count
        handler(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        if result.blocked_count > blocked_before:
            return
        self.store.mark_plan_applied(plan_id=plan.plan_id)
        result.applied_plan_ids.append(plan.plan_id)

    def _apply_add(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        memory_id = self._apply_canonical_or_file(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        self._apply_projections(memory_id=memory_id, plan=plan, result=result)

    def _apply_merge(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        if not plan.target_memory_id:
            self._block(result, "merge_target_missing")
            return
        memory_id = self._apply_canonical_or_file(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        self._apply_projections(memory_id=memory_id, plan=plan, result=result)

    def _apply_update(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        if not plan.target_memory_id:
            self._block(result, "update_target_missing")
            return
        memory_id = self._apply_canonical_or_file(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        self._apply_projections(memory_id=memory_id, plan=plan, result=result)

    def _apply_supersede(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        if not plan.target_memory_id:
            self._block(result, "supersede_target_missing")
            return
        memory_id = self._apply_canonical_or_file(session_id=session_id, workspace_dir=workspace_dir, plan=plan, result=result)
        self._apply_projections(memory_id=memory_id, plan=plan, result=result)

    def _apply_link(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        relations = _relations_for_plan(plan)
        if not relations:
            self._block(result, "link_relation_missing")
            return
        memory_id = None
        if _is_record_canonical_store(plan.canonical_store):
            memory_id = self._apply_canonical_record(
                session_id=session_id,
                workspace_dir=workspace_dir,
                plan=plan,
                result=result,
            )
        if plan.canonical_store == "file_memory":
            self.store.insert_file_suggestion(session_id=session_id, workspace_dir=workspace_dir, plan=plan)
            result.file_suggestions += 1
        if plan.canonical_store == "dag":
            self._apply_dag(memory_id=memory_id, plan=plan, result=result)
        self._apply_graph_relations(memory_id=memory_id, plan=plan, result=result, relations=relations)
        if "vector_projection" in plan.projections:
            self._apply_vector_projection(memory_id=memory_id, plan=plan, result=result)

    def _apply_delete(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> None:
        if not plan.target_memory_id:
            self._block(result, "delete_target_missing")
            return
        self.store.mark_record_status(memory_id=plan.target_memory_id, status="deleted")

    def _apply_review_item(self, *, session_id: str, plan: MemoryWritePlan, result: MemoryApplyResult) -> None:
        reason = ";".join(plan.needs_review_reasons or plan.blocked_reasons or ["needs_review"])
        if not self.store.has_review_item(source_plan_id=plan.plan_id):
            self.store.insert_review_item(session_id=session_id, plan=plan, reason=reason)
            result.review_items += 1
        self.store.mark_plan_applied(plan_id=plan.plan_id)
        result.applied_plan_ids.append(plan.plan_id)

    def _apply_canonical_or_file(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> Optional[str]:
        memory_id: Optional[str] = None
        if _is_record_canonical_store(plan.canonical_store):
            memory_id = self._apply_canonical_record(
                session_id=session_id,
                workspace_dir=workspace_dir,
                plan=plan,
                result=result,
            )

        if plan.canonical_store == "file_memory":
            self.store.insert_file_suggestion(session_id=session_id, workspace_dir=workspace_dir, plan=plan)
            result.file_suggestions += 1
        if plan.canonical_store == "dag":
            self._apply_dag(memory_id=memory_id, plan=plan, result=result)
        return memory_id

    def _apply_projections(self, *, memory_id: Optional[str], plan: MemoryWritePlan, result: MemoryApplyResult) -> None:
        projection_stores = _projection_stores(plan)
        if plan.canonical_store == "relation_graph" or "relation_graph" in projection_stores:
            self._apply_graph_relations(memory_id=memory_id, plan=plan, result=result, relations=_relations_for_plan(plan))

        if "dag" in projection_stores:
            self._apply_dag(memory_id=memory_id, plan=plan, result=result)

        if plan.canonical_store == "vector_projection" or "vector_projection" in projection_stores:
            self._apply_vector_projection(memory_id=memory_id, plan=plan, result=result)

    def _apply_dag(self, *, memory_id: Optional[str], plan: MemoryWritePlan, result: MemoryApplyResult) -> None:
        source_ref_id = memory_id or plan.plan_id
        source_kind = "memory" if memory_id else "plan"
        source_node = self.store.get_or_create_dag_node(
            node_kind=source_kind,
            ref_id=source_ref_id,
            scope=plan.scope,
            label=plan.text,
            source_plan_id=plan.plan_id,
            payload=plan.to_dict(),
        )
        if source_node.get("_created"):
            result.dag_nodes += 1
        relations = _relations_for_plan(plan) or [{"relation_type": "derived_from", "target_memory_id": episode_id} for episode_id in plan.evidence_episode_ids]
        for relation in relations:
            relation_type = relation.get("relation_type") or "derived_from"
            target_ref_id = relation.get("target_memory_id") or plan.target_memory_id or plan.evidence_episode_ids[0]
            target_kind = "memory" if relation.get("target_memory_id") or plan.target_memory_id else "episode"
            target_node = self.store.get_or_create_dag_node(
                node_kind=target_kind,
                ref_id=target_ref_id,
                scope=plan.scope,
                label=target_ref_id,
                source_plan_id=plan.plan_id,
                payload={"source_plan_id": plan.plan_id, "relation": relation},
            )
            if target_node.get("_created"):
                result.dag_nodes += 1
            if self.store.has_dag_edge(
                source_node_id=source_node["node_id"],
                target_node_id=target_node["node_id"],
                edge_type=relation_type,
                source_plan_id=plan.plan_id,
            ):
                continue
            if self.store.dag_edge_creates_cycle(source_node_id=source_node["node_id"], target_node_id=target_node["node_id"]):
                self._block(result, "dag_cycle_detected")
                continue
            self.store.insert_dag_edge(
                source_node_id=source_node["node_id"],
                target_node_id=target_node["node_id"],
                edge_type=relation_type,
                scope=plan.scope,
                confidence=plan.confidence,
                source_plan_id=plan.plan_id,
                payload=plan.to_dict(),
            )
            result.dag_edges += 1
            result.reindex_jobs += self._enqueue_job("refresh_dag", "dag_edge_created", memory_id=memory_id, plan=plan)

    def _apply_graph_relations(
        self,
        *,
        memory_id: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
        relations: List[Dict[str, Optional[str]]],
    ) -> None:
        for relation in relations:
            relation_type = relation["relation_type"] or "derived_from"
            target_memory_id = relation.get("target_memory_id")
            if self.store.has_graph_edge(
                memory_id=memory_id,
                episode_id=plan.evidence_episode_ids[0],
                relation_type=relation_type,
                source_plan_id=plan.plan_id,
                target_memory_id=target_memory_id,
            ):
                continue
            self.store.insert_graph_edge(
                memory_id=memory_id,
                episode_id=plan.evidence_episode_ids[0],
                relation_type=relation_type,
                target_memory_id=target_memory_id,
                plan=plan,
            )
            result.graph_edges += 1
            result.reindex_jobs += self._enqueue_job("refresh_graph", "graph_edge_created", memory_id=memory_id, plan=plan)

    def _apply_vector_projection(self, *, memory_id: Optional[str], plan: MemoryWritePlan, result: MemoryApplyResult) -> None:
        if self.store.has_vector_projection(
            memory_id=memory_id,
            episode_id=plan.evidence_episode_ids[0],
            source_plan_id=plan.plan_id,
        ):
            return
        projection_id = self.store.insert_vector_projection(
            memory_id=memory_id,
            episode_id=plan.evidence_episode_ids[0],
            plan=plan,
        )
        result.vector_projections += 1
        result.reindex_jobs += self._enqueue_job(
            "embed_vector",
            "vector_projection_created",
            memory_id=memory_id,
            projection_id=projection_id,
            plan=plan,
        )

    def _block(self, result: MemoryApplyResult, reason: str) -> None:
        result.blocked_count += 1
        result.blocked_reasons.append(reason)

    def _apply_canonical_record(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        result: MemoryApplyResult,
    ) -> str:
        namespace = derive_namespace(scope=plan.scope, session_id=session_id, workspace_dir=workspace_dir)
        key = derive_memory_key(plan)
        record = self.store.get_record_by_source_plan(source_plan_id=plan.plan_id)
        supersedes_memory_id = record.get("supersedes_memory_id") if record else None
        if not record:
            existing = self.store.find_active_record(scope=plan.scope, namespace=namespace, memory_type=plan.type, key=key)
            status = "active" if plan.status == "planned" else "needs_review"
            version = 1
            if plan.target_memory_id and plan.action in {"MERGE", "UPDATE", "SUPERSEDE"}:
                supersedes_memory_id = plan.target_memory_id
                target = self.store.get_record(memory_id=plan.target_memory_id)
                if target:
                    version = int(target["version"]) + 1
            elif existing and plan.action in {"ADD", "UPDATE", "MERGE", "SUPERSEDE"}:
                supersedes_memory_id = existing["memory_id"]
                version = int(existing["version"]) + 1
            record = self.store.insert_memory_record(
                scope=plan.scope,
                namespace=namespace,
                memory_type=plan.type,
                key=key,
                value=plan.text,
                status=status,
                confidence=plan.confidence,
                version=version,
                source_plan_id=plan.plan_id,
                supersedes_memory_id=supersedes_memory_id,
                payload=plan.to_dict(),
            )
            result.canonical_writes += 1
            result.reindex_jobs += self._enqueue_job("refresh_kv_shortcut", "canonical_record_mutated", memory_id=record["memory_id"], plan=plan)
        if supersedes_memory_id:
            self.store.mark_record_status(
                memory_id=supersedes_memory_id,
                status="superseded",
                superseded_by_memory_id=record["memory_id"],
            )
            if not self.store.has_graph_edge(
                memory_id=record["memory_id"],
                episode_id=plan.evidence_episode_ids[0],
                relation_type="supersedes",
                source_plan_id=plan.plan_id,
                target_memory_id=supersedes_memory_id,
            ):
                self.store.insert_graph_edge(
                    memory_id=record["memory_id"],
                    episode_id=plan.evidence_episode_ids[0],
                    relation_type="supersedes",
                    target_memory_id=supersedes_memory_id,
                    plan=plan,
                )
                result.graph_edges += 1
        if plan.action == "DELETE":
            self.store.mark_record_status(memory_id=record["memory_id"], status="deleted")
        for episode_id in plan.evidence_episode_ids:
            if self.store.has_evidence_link(memory_id=record["memory_id"], episode_id=episode_id, plan_id=plan.plan_id):
                continue
            self.store.insert_evidence_link(memory_id=record["memory_id"], episode_id=episode_id, plan_id=plan.plan_id)
            result.evidence_links += 1
        return record["memory_id"]

    def _enqueue_job(
        self,
        job_type: str,
        reason: str,
        *,
        plan: MemoryWritePlan,
        memory_id: Optional[str] = None,
        projection_id: Optional[str] = None,
    ) -> int:
        self.store.insert_reindex_job(
            job_type=job_type,
            reason=reason,
            memory_id=memory_id,
            projection_id=projection_id,
            payload=plan.to_dict(),
        )
        return 1


class _PlanCandidateAdapter:
    def __init__(self, plan: MemoryWritePlan) -> None:
        self.text = plan.text
        self.reason = plan.reason


def _relations_for_plan(plan: MemoryWritePlan) -> List[Dict[str, Optional[str]]]:
    relations = [
        {
            "relation_type": str(relation.get("relation_type") or "derived_from"),
            "target_memory_id": str(relation.get("target_memory_id") or "").strip() or None,
        }
        for relation in plan.graph_relations
        if isinstance(relation, dict)
    ]
    if relations:
        return relations
    if has_explicit_graph_relation(_PlanCandidateAdapter(plan)):
        return [{"relation_type": _infer_relation_type(plan.text), "target_memory_id": plan.target_memory_id}]
    return []


def _projection_stores(plan: MemoryWritePlan) -> List[str]:
    writes = plan.storage_route.get("projection_writes") if isinstance(plan.storage_route, dict) else None
    stores: List[str] = []
    if isinstance(writes, list):
        for write in writes:
            if not isinstance(write, dict):
                continue
            store = str(write.get("store") or "").strip()
            if store and store not in stores:
                stores.append(store)
    for store in plan.projections:
        if store not in stores:
            stores.append(store)
    return stores


def _has_review_write(plan: MemoryWritePlan) -> bool:
    if not isinstance(plan.storage_route, dict):
        return False
    review_write = plan.storage_route.get("review_write")
    return isinstance(review_write, dict) and review_write.get("store") == "review_queue"


def _is_record_canonical_store(store: Optional[str]) -> bool:
    return store in {"semantic_kv", "state_kv", "episode_log", "relation_graph", "vector_projection"}


def _infer_relation_type(text: str) -> str:
    lowered = (text or "").lower()
    markers = {
        "depends_on": ("depends_on", "depends on", "依赖"),
        "supersedes": ("supersedes", "取代", "替代"),
        "contradicts": ("contradicts", "冲突", "矛盾"),
        "derived_from": ("derived_from", "derived from", "来自"),
        "caused_by": ("caused_by", "caused by", "导致"),
        "part_of": ("part_of", "part of", "组成"),
        "blocks": ("blocks", "阻塞"),
    }
    for relation_type, values in markers.items():
        if any(value in lowered for value in values):
            return relation_type
    return "derived_from"
