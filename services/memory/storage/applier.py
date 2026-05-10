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
    file_suggestions: int = 0
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
            "file_suggestions": self.file_suggestions,
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
        if plan.status == "needs_review" and plan.canonical_store != "file_memory":
            result.blocked_count += 1
            result.blocked_reasons.append("needs_review_not_applied")
            return

        memory_id: Optional[str] = None
        if plan.canonical_store in {"semantic_kv", "episode_log", "relation_graph", "vector_projection"}:
            memory_id = self._apply_canonical_record(
                session_id=session_id,
                workspace_dir=workspace_dir,
                plan=plan,
                result=result,
            )

        if plan.canonical_store == "file_memory":
            self.store.insert_file_suggestion(session_id=session_id, workspace_dir=workspace_dir, plan=plan)
            result.file_suggestions += 1

        if plan.canonical_store == "relation_graph" or "relation_graph" in plan.projections:
            for relation in _relations_for_plan(plan):
                self.store.insert_graph_edge(
                    memory_id=memory_id,
                    episode_id=plan.evidence_episode_ids[0],
                    relation_type=relation["relation_type"],
                    target_memory_id=relation.get("target_memory_id"),
                    plan=plan,
                )
                result.graph_edges += 1
                result.reindex_jobs += self._enqueue_job("refresh_graph", "graph_edge_created", memory_id=memory_id, plan=plan)

        if plan.canonical_store == "vector_projection" or "vector_projection" in plan.projections:
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

        self.store.mark_plan_applied(plan_id=plan.plan_id)
        result.applied_plan_ids.append(plan.plan_id)

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
        existing = self.store.find_active_record(scope=plan.scope, namespace=namespace, memory_type=plan.type, key=key)
        status = "active" if plan.status == "planned" else "needs_review"
        supersedes_memory_id = None
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
        if supersedes_memory_id:
            self.store.mark_record_status(
                memory_id=supersedes_memory_id,
                status="superseded",
                superseded_by_memory_id=record["memory_id"],
            )
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
            self.store.insert_evidence_link(memory_id=record["memory_id"], episode_id=episode_id, plan_id=plan.plan_id)
            result.evidence_links += 1
        result.canonical_writes += 1
        result.reindex_jobs += self._enqueue_job("refresh_kv_shortcut", "canonical_record_mutated", memory_id=record["memory_id"], plan=plan)
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
