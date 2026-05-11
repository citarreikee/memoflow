from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.integration_llm import plan_memory_integration_with_llm
from services.memory.formation.integration_planner import plan_memory_integration
from services.memory.formation.integration_schemas import MemoryIntegrationPlan
from services.memory.formation.mutation_planner import build_write_plans
from services.memory.formation.neighborhood import MemoryNeighborhoodRepository
from services.memory.formation.pipeline import MemoryFormationResult, run_memory_formation_dry_run
from services.memory.formation.shape_planner import plan_storage_shape
from services.memory.jobs import MemoryJob, MemoryJobQueue
from services.memory.storage.applier import MemoryApplyResult, MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.stores.dry_run import DryRunWriteResult, write_dry_run_outputs


FORMATION_PIPELINE_CONTRACT_VERSION = "formation_job_pipeline_v1"


@dataclass(frozen=True)
class MemoryFormationStageTrace:
    name: str
    status: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    mode: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryFormationJobResult:
    formation: MemoryFormationResult
    write_result: Optional[DryRunWriteResult]
    storage_debug: Optional[Dict[str, Any]] = None
    apply_result: Optional[MemoryApplyResult] = None
    integration_debug: Optional[Dict[str, Any]] = None
    stage_trace: List[MemoryFormationStageTrace] = field(default_factory=list)

    def to_debug_dict(self) -> Dict[str, Any]:
        debug = self.formation.to_debug_dict()
        debug["pipeline_contract_version"] = FORMATION_PIPELINE_CONTRACT_VERSION
        debug["pipeline_stages"] = [stage.to_dict() for stage in self.stage_trace]
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
        storage_debug: Optional[Dict[str, Any]] = None
        apply_result: Optional[MemoryApplyResult] = None
        integration_debug: Optional[Dict[str, Any]] = None
        if settings.MEMORY_STORAGE_ENABLED:
            integration_debug, integration_plans = await self._plan_integrations(
                session_id=session_id,
                workspace_dir=workspace_dir,
                formation=formation,
            )
            formation.plans = build_write_plans(
                formation.candidates,
                evidence_episode_ids=formation.episode_ids,
                integration_plans=integration_plans,
            )
        write_result: Optional[DryRunWriteResult] = None
        if settings.MEMORY_FORMATION_DRY_RUN and formation.plans:
            write_result = write_dry_run_outputs(
                base_dir=self.log_dir,
                session_id=session_id,
                plans=formation.plans,
            )
        if settings.MEMORY_STORAGE_ENABLED:
            storage_debug, apply_result = self._persist_to_sqlite(
                session_id=session_id,
                episode_payload=episode_payload,
                workspace_dir=workspace_dir,
                formation=formation,
            )
        stage_trace = self._build_stage_trace(
            formation=formation,
            integration_debug=integration_debug,
            write_result=write_result,
            storage_debug=storage_debug,
            apply_result=apply_result,
        )
        return MemoryFormationJobResult(
            formation=formation,
            write_result=write_result,
            storage_debug=storage_debug,
            apply_result=apply_result,
            integration_debug=integration_debug,
            stage_trace=stage_trace,
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
                "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
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

    def _build_stage_trace(
        self,
        *,
        formation: MemoryFormationResult,
        integration_debug: Optional[Dict[str, Any]],
        write_result: Optional[DryRunWriteResult],
        storage_debug: Optional[Dict[str, Any]],
        apply_result: Optional[MemoryApplyResult],
    ) -> List[MemoryFormationStageTrace]:
        extractor = formation.extractor_debug or {}
        observation_debug = extractor.get("observation") if isinstance(extractor.get("observation"), dict) else {}
        candidate_debug = extractor.get("formation") if isinstance(extractor.get("formation"), dict) else {}
        observation_first = bool(extractor.get("observation_first"))
        legacy_fallback = bool(extractor.get("legacy_candidate_fallback"))
        stages = [
            MemoryFormationStageTrace(
                name="observation_extraction",
                status="succeeded" if formation.observations else "empty",
                inputs={"episode_count": len(formation.episode_ids)},
                outputs={"observation_count": len(formation.observations)},
                mode=str(observation_debug.get("mode") or "unknown"),
            ),
            MemoryFormationStageTrace(
                name="candidate_formation",
                status="succeeded" if formation.candidates else "empty",
                inputs={"observation_count": len(formation.observations)},
                outputs={"candidate_count": len(formation.candidates)},
                mode=str(candidate_debug.get("mode") or "unknown"),
                notes=[note for note, enabled in {
                    "observation_first": observation_first,
                    "legacy_candidate_fallback": legacy_fallback,
                }.items() if enabled],
            ),
        ]
        if integration_debug is None:
            stages.append(
                MemoryFormationStageTrace(
                    name="integration_routing",
                    status="skipped",
                    inputs={"candidate_count": len(formation.candidates)},
                    outputs={},
                    mode="storage_disabled",
                )
            )
        else:
            stages.append(
                MemoryFormationStageTrace(
                    name="integration_routing",
                    status="succeeded",
                    inputs={"candidate_count": integration_debug.get("candidate_count", 0)},
                    outputs={
                        "snapshot_count": integration_debug.get("snapshot_count", 0),
                        "action_counts": integration_debug.get("action_counts", {}),
                    },
                    mode="rule_or_llm_integration",
                )
            )
        stages.append(
            MemoryFormationStageTrace(
                name="write_planning",
                status="succeeded" if formation.plans else "empty",
                inputs={"candidate_count": len(formation.candidates)},
                outputs={
                    "plan_count": len(formation.plans),
                    "planned_count": len([plan for plan in formation.plans if plan.status == "planned"]),
                    "needs_review_count": len([plan for plan in formation.plans if plan.status == "needs_review"]),
                    "blocked_count": len([plan for plan in formation.plans if plan.status == "blocked"]),
                    "noop_count": len([plan for plan in formation.plans if plan.status == "noop"]),
                },
                mode="integration_aware" if integration_debug is not None else "preliminary",
            )
        )
        stages.append(
            MemoryFormationStageTrace(
                name="dry_run_write",
                status="succeeded" if write_result else "skipped",
                inputs={"plan_count": len(formation.plans)},
                outputs=write_result.to_dict() if write_result else {},
                mode="dry_run_log" if settings.MEMORY_FORMATION_DRY_RUN else "dry_run_disabled",
            )
        )
        stages.append(
            MemoryFormationStageTrace(
                name="sqlite_persistence",
                status="succeeded" if storage_debug else "skipped",
                inputs={
                    "observation_count": len(formation.observations),
                    "candidate_count": len(formation.candidates),
                    "plan_count": len(formation.plans),
                },
                outputs=storage_debug or {},
                mode="storage_enabled" if settings.MEMORY_STORAGE_ENABLED else "storage_disabled",
            )
        )
        stages.append(
            MemoryFormationStageTrace(
                name="safe_apply",
                status="succeeded" if apply_result else "skipped",
                inputs={"plan_count": len(formation.plans)},
                outputs=apply_result.to_dict() if apply_result else {},
                mode="apply_plans_enabled" if settings.MEMORY_STORAGE_APPLY_PLANS else "apply_plans_disabled",
            )
        )
        return stages


    async def _plan_integrations(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        formation: MemoryFormationResult,
    ) -> tuple[Dict[str, Any], List[MemoryIntegrationPlan]]:
        if not formation.candidates:
            return {"enabled": True, "candidate_count": 0, "plans": []}, []
        store = MemorySQLiteStore.from_settings()
        repository = MemoryNeighborhoodRepository(store)
        plans: List[Dict[str, Any]] = []
        integration_plans: List[MemoryIntegrationPlan] = []
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
            integration_plans.append(integration)
            routed_shape = plan_storage_shape(
                candidate,
                write_strategy=integration.write_strategy,
                memory_layers=integration.memory_layers,
            )
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
                "storage_route_preview": routed_shape.to_dict(),
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
        }, integration_plans

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
        observation_debug = extractor.get("observation") if isinstance(extractor.get("observation"), dict) else {}
        formation_debug = extractor.get("formation") if isinstance(extractor.get("formation"), dict) else {}
        observation_mode = str(observation_debug.get("mode") or settings.MEMORY_FORMATION_EXTRACTOR)
        observation_model = observation_debug.get("model") if isinstance(observation_debug.get("model"), str) else None
        extractor_mode = str(formation_debug.get("mode") or settings.MEMORY_FORMATION_EXTRACTOR)
        extractor_model = formation_debug.get("model") if isinstance(formation_debug.get("model"), str) else None
        store.persist_observations(
            session_id=session_id,
            observations=formation.observations,
            extractor_mode=observation_mode,
            extractor_model=observation_model,
            status="formed" if formation.candidates else "extracted",
        )
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
            "observation_count": len(formation.observations),
            "candidate_count": len(formation.candidates),
            "plan_count": len(formation.plans),
            "applied": bool(apply_result),
        }
        return storage_debug, apply_result
