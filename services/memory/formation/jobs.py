from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.integration_llm import plan_memory_integration_with_llm
from services.memory.formation.integration_planner import plan_memory_integration
from services.memory.formation.neighborhood import MemoryNeighborhoodRepository
from services.memory.formation.pipeline import MemoryFormationResult, run_memory_formation_dry_run
from services.memory.jobs import MemoryJob, MemoryJobQueue
from services.memory.storage.applier import MemoryApplyResult, MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.stores.dry_run import DryRunWriteResult, write_dry_run_outputs


@dataclass
class MemoryFormationJobResult:
    formation: MemoryFormationResult
    write_result: Optional[DryRunWriteResult]
    storage_debug: Optional[Dict[str, Any]] = None
    apply_result: Optional[MemoryApplyResult] = None
    integration_debug: Optional[Dict[str, Any]] = None

    def to_debug_dict(self) -> Dict[str, Any]:
        debug = self.formation.to_debug_dict()
        if self.write_result:
            debug["dry_run_writes"] = self.write_result.to_dict()
        if self.storage_debug:
            debug["memory_storage"] = self.storage_debug
        if self.apply_result:
            debug.setdefault("memory_storage", {})
            debug["memory_storage"].update(self.apply_result.to_dict())
        if self.integration_debug:
            debug["memory_integration"] = self.integration_debug
        return debug


class MemoryFormationJobRunner:
    def __init__(self, *, log_dir: str, queue: MemoryJobQueue | None = None) -> None:
        self.log_dir = log_dir
        self.queue = queue or MemoryJobQueue.from_settings()

    async def run(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        workspace_dir: Optional[str] = None,
    ) -> MemoryFormationJobResult:
        formation = await run_memory_formation_dry_run(episode_payload)
        write_result: Optional[DryRunWriteResult] = None
        if settings.MEMORY_FORMATION_DRY_RUN and formation.plans:
            write_result = write_dry_run_outputs(
                base_dir=self.log_dir,
                session_id=session_id,
                plans=formation.plans,
            )
        storage_debug: Optional[Dict[str, Any]] = None
        apply_result: Optional[MemoryApplyResult] = None
        integration_debug: Optional[Dict[str, Any]] = None
        if settings.MEMORY_STORAGE_ENABLED:
            integration_debug = await self._plan_integrations(
                session_id=session_id,
                workspace_dir=workspace_dir,
                formation=formation,
            )
            storage_debug, apply_result = self._persist_to_sqlite(
                session_id=session_id,
                episode_payload=episode_payload,
                workspace_dir=workspace_dir,
                formation=formation,
            )
        return MemoryFormationJobResult(
            formation=formation,
            write_result=write_result,
            storage_debug=storage_debug,
            apply_result=apply_result,
            integration_debug=integration_debug,
        )

    def schedule(self, *, session_id: str, episode_payload: Dict[str, Any], workspace_dir: Optional[str] = None) -> MemoryJob:
        return self.queue.enqueue(
            job_type="memory_formation",
            session_id=session_id,
            episode_id=str(episode_payload.get("episode_id") or "") or None,
            priority=50,
            payload={
                "session_id": session_id,
                "episode_payload": episode_payload,
                "workspace_dir": workspace_dir,
                "dry_run": settings.MEMORY_FORMATION_DRY_RUN,
                "storage_enabled": settings.MEMORY_STORAGE_ENABLED,
            },
        )

    async def run_queued_job(self, job: MemoryJob) -> MemoryFormationJobResult:
        payload = job.payload or {}
        result = await self.run(
            session_id=str(payload.get("session_id") or job.session_id or ""),
            episode_payload=payload.get("episode_payload") or {},
            workspace_dir=payload.get("workspace_dir"),
        )
        self.queue.complete(job.job_id, result=result.to_debug_dict())
        return result


    async def _plan_integrations(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        formation: MemoryFormationResult,
    ) -> Dict[str, Any]:
        if not formation.candidates:
            return {"enabled": True, "candidate_count": 0, "plans": []}
        store = MemorySQLiteStore.from_settings()
        repository = MemoryNeighborhoodRepository(store)
        plans: List[Dict[str, Any]] = []
        action_counts: Dict[str, int] = {}
        snapshot_count = 0
        for candidate in formation.candidates:
            snapshots = repository.fetch_for_candidate(
                candidate,
                session_id=session_id,
                workspace_dir=workspace_dir,
            )
            integration_mode = "rule_integration"
            llm_debug: Optional[Dict[str, Any]] = None
            rule_integration = plan_memory_integration(candidate, existing_memories=snapshots)
            if settings.MEMORY_FORMATION_EXTRACTOR == "llm":
                llm_integration, llm_debug = await plan_memory_integration_with_llm(
                    candidate,
                    existing_memories=snapshots,
                )
                if llm_debug.get("error"):
                    integration = rule_integration
                    integration_mode = "llm_minimal_with_rule_fallback"
                else:
                    integration = llm_integration
                    integration_mode = "llm_minimal_integration"
            else:
                integration = rule_integration
            action_counts[integration.action] = action_counts.get(integration.action, 0) + 1
            snapshot_count += len(snapshots)
            plan_debug = {
                "candidate_id": candidate.candidate_id,
                "candidate_type": candidate.type,
                "candidate_scope": candidate.scope,
                "integration_mode": integration_mode,
                "snapshot_count": len(snapshots),
                "snapshots": [snapshot.to_dict() for snapshot in snapshots],
                "rule_plan": rule_integration.to_dict(),
                "plan": integration.to_dict(),
            }
            if llm_debug:
                plan_debug["llm"] = llm_debug
            plans.append(plan_debug)
        return {
            "enabled": True,
            "candidate_count": len(formation.candidates),
            "snapshot_count": snapshot_count,
            "action_counts": action_counts,
            "plans": plans,
        }

    def _persist_to_sqlite(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        workspace_dir: Optional[str],
        formation: MemoryFormationResult,
    ) -> tuple[Dict[str, Any], Optional[MemoryApplyResult]]:
        store = MemorySQLiteStore.from_settings()
        episode_id = str(episode_payload.get("episode_id") or "")
        extractor = formation.extractor_debug or {}
        extractor_mode = str(extractor.get("mode") or settings.MEMORY_FORMATION_EXTRACTOR)
        extractor_model = extractor.get("model") if isinstance(extractor.get("model"), str) else None
        for candidate in formation.candidates:
            store.persist_candidate(
                session_id=session_id,
                episode_id=episode_id,
                candidate=candidate,
                extractor_mode=extractor_mode,
                extractor_model=extractor_model,
                status="planned" if formation.plans else "extracted",
            )
        for plan in formation.plans:
            store.persist_write_plan(session_id=session_id, plan=plan)
        apply_result = None
        if settings.MEMORY_STORAGE_APPLY_PLANS and formation.plans:
            apply_result = MemoryWriteApplier(store).apply_plans(
                session_id=session_id,
                workspace_dir=workspace_dir,
                plans=formation.plans,
            )
        storage_debug = {
            "enabled": True,
            "candidate_count": len(formation.candidates),
            "plan_count": len(formation.plans),
            "applied": bool(apply_result),
        }
        return storage_debug, apply_result
