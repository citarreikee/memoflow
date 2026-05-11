from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.extractor import should_trigger_extraction
from services.memory.formation.integration_llm import plan_memory_integration_with_llm
from services.memory.formation.integration_planner import plan_memory_integration
from services.memory.formation.integration_schemas import MemoryIntegrationPlan
from services.memory.formation.mutation_planner import build_write_plans
from services.memory.formation.neighborhood import MemoryNeighborhoodRepository
from services.memory.formation.pipeline import MemoryFormationResult, run_memory_formation_dry_run
from services.memory.formation.pipeline import _extract_observations, _form_candidates
from services.memory.formation.schemas import MemoryCandidateLite, MemoryObservation, MemoryWritePlan, normalize_candidate, normalize_observation
from services.memory.formation.shape_planner import plan_storage_shape
from services.memory.jobs import MemoryJob, MemoryJobQueue
from services.memory.storage.applier import MemoryApplyResult, MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.stores.dry_run import DryRunWriteResult, write_dry_run_outputs


FORMATION_PIPELINE_CONTRACT_VERSION = "formation_job_pipeline_v1"
FORMATION_LEGACY_JOB_TYPE = "memory_formation"
FORMATION_OBSERVATION_JOB_TYPE = "memory_observation"
FORMATION_CANDIDATE_JOB_TYPE = "memory_candidate_formation"
FORMATION_INTEGRATION_JOB_TYPE = "memory_integration_routing"
FORMATION_WRITE_JOB_TYPE = "memory_write_planning"
FORMATION_APPLY_JOB_TYPE = "memory_safe_apply"
FORMATION_STAGED_JOB_TYPES = {
    FORMATION_OBSERVATION_JOB_TYPE,
    FORMATION_CANDIDATE_JOB_TYPE,
    FORMATION_INTEGRATION_JOB_TYPE,
    FORMATION_WRITE_JOB_TYPE,
    FORMATION_APPLY_JOB_TYPE,
}


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
    artifact_debug: Optional[Dict[str, Any]] = None

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
        if self.artifact_debug:
            debug["pipeline_artifacts"] = self.artifact_debug
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
        artifact_debug: Optional[Dict[str, Any]] = None
        if settings.MEMORY_STORAGE_ENABLED:
            artifact_debug = self._persist_stage_artifacts(
                session_id=session_id,
                episode_payload=episode_payload,
                stage_trace=stage_trace,
            )
        return MemoryFormationJobResult(
            formation=formation,
            write_result=write_result,
            storage_debug=storage_debug,
            apply_result=apply_result,
            integration_debug=integration_debug,
            stage_trace=stage_trace,
            artifact_debug=artifact_debug,
        )

    def schedule(self, *, session_id: str, episode_payload: Dict[str, Any], workspace_dir: Optional[str] = None) -> MemoryJob:
        return self.queue.enqueue(
            job_type=FORMATION_LEGACY_JOB_TYPE,
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

    def schedule_staged(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        workspace_dir: Optional[str] = None,
    ) -> MemoryJob:
        return self.queue.enqueue(
            job_type=FORMATION_OBSERVATION_JOB_TYPE,
            session_id=session_id,
            episode_id=str(episode_payload.get("episode_id") or "") or None,
            priority=40,
            payload={
                "session_id": session_id,
                "episode_payload": episode_payload,
                "workspace_dir": workspace_dir,
                "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
                "staged": True,
            },
        )

    def schedule_rerun_from_stage(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        completed_stage_name: str,
        workspace_dir: Optional[str] = None,
    ) -> MemoryJob:
        episode_id = str(episode_payload.get("episode_id") or "")
        if not self.load_stage_output(session_id=session_id, episode_id=episode_id, stage_name=completed_stage_name):
            raise ValueError(f"missing_stage_artifact:{completed_stage_name}")
        next_job_type = _next_job_type_after_stage(completed_stage_name, storage_enabled=settings.MEMORY_STORAGE_ENABLED)
        return self.queue.enqueue(
            job_type=next_job_type,
            session_id=session_id,
            episode_id=episode_id or None,
            priority=42,
            payload={
                "session_id": session_id,
                "episode_payload": episode_payload,
                "workspace_dir": workspace_dir,
                "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
                "staged": True,
                "rerun": True,
                "rerun_from_stage": completed_stage_name,
                "input_source": "pipeline_artifact",
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

    async def run_staged_job(self, job: MemoryJob) -> Dict[str, Any]:
        if job.job_type == FORMATION_OBSERVATION_JOB_TYPE:
            return await self._run_observation_job(job)
        if job.job_type == FORMATION_CANDIDATE_JOB_TYPE:
            return await self._run_candidate_job(job)
        if job.job_type == FORMATION_INTEGRATION_JOB_TYPE:
            return await self._run_integration_job(job)
        if job.job_type == FORMATION_WRITE_JOB_TYPE:
            return await self._run_write_job(job)
        if job.job_type == FORMATION_APPLY_JOB_TYPE:
            return await self._run_apply_job(job)
        raise ValueError(f"unsupported_formation_stage_job:{job.job_type}")

    def load_stage_output(
        self,
        *,
        session_id: str,
        stage_name: str,
        episode_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return MemorySQLiteStore.from_settings().get_pipeline_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            job_type="memory_formation",
            stage_name=stage_name,
            contract_version=FORMATION_PIPELINE_CONTRACT_VERSION,
        )

    async def _run_observation_job(self, job: MemoryJob) -> Dict[str, Any]:
        payload = job.payload or {}
        session_id = str(payload.get("session_id") or job.session_id or "")
        episode_payload = payload.get("episode_payload") or {}
        episode_id = str(episode_payload.get("episode_id") or job.episode_id or "")
        if not should_trigger_extraction(episode_payload):
            stage = MemoryFormationStageTrace(
                name="observation_extraction",
                status="skipped",
                inputs={"episode_count": 1 if episode_id else 0},
                outputs={"observation_count": 0, "observations": []},
                mode="trigger_policy_noop",
            )
            artifact_debug = self._persist_single_stage_artifact(session_id=session_id, episode_id=episode_id, stage=stage)
            result = {
                "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
                "stage": stage.to_dict(),
                "pipeline_artifacts": artifact_debug,
                "done": True,
                "skipped_reason": "trigger_policy_noop",
            }
            self.queue.complete(job.job_id, result=result)
            return result
        observations, observation_debug = await _extract_observations(episode_payload)
        stage = MemoryFormationStageTrace(
            name="observation_extraction",
            status="succeeded" if observations else "empty",
            inputs={"episode_count": 1 if episode_id else 0},
            outputs={"observation_count": len(observations), "observations": [item.to_dict() for item in observations]},
            mode=str(observation_debug.get("mode") or "unknown"),
        )
        store = MemorySQLiteStore.from_settings()
        if settings.MEMORY_STORAGE_ENABLED:
            store.persist_observations(
                session_id=session_id,
                observations=observations,
                extractor_mode=str(observation_debug.get("mode") or settings.MEMORY_FORMATION_EXTRACTOR),
                extractor_model=observation_debug.get("model") if isinstance(observation_debug.get("model"), str) else None,
                status="extracted",
            )
        artifact_debug = self._persist_single_stage_artifact(
            session_id=session_id,
            episode_id=episode_id,
            stage=stage,
        )
        next_job = self.queue.enqueue(
            job_type=FORMATION_CANDIDATE_JOB_TYPE,
            session_id=session_id,
            episode_id=episode_id or None,
            priority=45,
            payload=_stage_payload(payload),
        )
        result = {
            "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
            "stage": stage.to_dict(),
            "pipeline_artifacts": artifact_debug,
            "next_job_id": next_job.job_id,
            "next_job_type": next_job.job_type,
        }
        self.queue.complete(job.job_id, result=result)
        return result

    async def _run_candidate_job(self, job: MemoryJob) -> Dict[str, Any]:
        payload = job.payload or {}
        session_id = str(payload.get("session_id") or job.session_id or "")
        episode_payload = payload.get("episode_payload") or {}
        episode_id = str(episode_payload.get("episode_id") or job.episode_id or "")
        observation_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="observation_extraction",
        ) or {}
        raw_observations = observation_output.get("observations") if isinstance(observation_output.get("observations"), list) else []
        observations = [
            normalize_observation(raw, fallback_id=str(raw.get("observation_id") or f"obs_{index}"), episode_id=episode_id)
            for index, raw in enumerate(raw_observations)
            if isinstance(raw, dict)
        ]
        candidates, formation_debug = await _form_candidates(episode_payload, observations)
        observation_first = bool(observations)
        for candidate in candidates:
            if observation_first and not candidate.source_observation_ids:
                candidate.risk = "high"
        stage = MemoryFormationStageTrace(
            name="candidate_formation",
            status="succeeded" if candidates else "empty",
            inputs={"observation_count": len(observations)},
            outputs={"candidate_count": len(candidates), "candidates": [item.to_dict() for item in candidates]},
            mode=str(formation_debug.get("mode") or "unknown"),
            notes=[note for note, enabled in {"observation_first": observation_first, "legacy_candidate_fallback": not observation_first and bool(candidates)}.items() if enabled],
        )
        artifact_debug = self._persist_single_stage_artifact(session_id=session_id, episode_id=episode_id, stage=stage)
        next_job_type = FORMATION_INTEGRATION_JOB_TYPE if settings.MEMORY_STORAGE_ENABLED else FORMATION_WRITE_JOB_TYPE
        next_job = self.queue.enqueue(
            job_type=next_job_type,
            session_id=session_id,
            episode_id=episode_id or None,
            priority=50,
            payload=_stage_payload(payload),
        )
        result = {
            "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
            "stage": stage.to_dict(),
            "pipeline_artifacts": artifact_debug,
            "next_job_id": next_job.job_id,
            "next_job_type": next_job.job_type,
        }
        self.queue.complete(job.job_id, result=result)
        return result

    async def _run_integration_job(self, job: MemoryJob) -> Dict[str, Any]:
        payload = job.payload or {}
        session_id = str(payload.get("session_id") or job.session_id or "")
        episode_id = str((payload.get("episode_payload") or {}).get("episode_id") or job.episode_id or "")
        candidate_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="candidate_formation",
        ) or {}
        candidates = _candidates_from_payload(candidate_output.get("candidates"))
        formation = MemoryFormationResult(True, [episode_id] if episode_id else [], [], candidates, [])
        integration_debug, integration_plans = await self._plan_integrations(
            session_id=session_id,
            workspace_dir=payload.get("workspace_dir"),
            formation=formation,
        )
        stage = MemoryFormationStageTrace(
            name="integration_routing",
            status="succeeded",
            inputs={"candidate_count": integration_debug.get("candidate_count", 0)},
            outputs={"snapshot_count": integration_debug.get("snapshot_count", 0), "action_counts": integration_debug.get("action_counts", {}), "integration_plans": [item.to_dict() for item in integration_plans]},
            mode="rule_or_llm_integration",
        )
        artifact_debug = self._persist_single_stage_artifact(session_id=session_id, episode_id=episode_id, stage=stage)
        next_job = self.queue.enqueue(
            job_type=FORMATION_WRITE_JOB_TYPE,
            session_id=session_id,
            episode_id=episode_id or None,
            priority=55,
            payload=_stage_payload(payload),
        )
        result = {"pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION, "stage": stage.to_dict(), "memory_integration": integration_debug, "pipeline_artifacts": artifact_debug, "next_job_id": next_job.job_id, "next_job_type": next_job.job_type}
        self.queue.complete(job.job_id, result=result)
        return result

    async def _run_write_job(self, job: MemoryJob) -> Dict[str, Any]:
        payload = job.payload or {}
        session_id = str(payload.get("session_id") or job.session_id or "")
        episode_id = str((payload.get("episode_payload") or {}).get("episode_id") or job.episode_id or "")
        candidate_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="candidate_formation",
        ) or {}
        integration_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="integration_routing",
        ) or {}
        candidates = _candidates_from_payload(candidate_output.get("candidates"))
        integration_plans = _integration_plans_from_payload(integration_output.get("integration_plans"))
        plans = build_write_plans(candidates, evidence_episode_ids=[episode_id] if episode_id else [], integration_plans=integration_plans)
        write_result = None
        if settings.MEMORY_FORMATION_DRY_RUN and plans:
            write_result = write_dry_run_outputs(base_dir=self.log_dir, session_id=session_id, plans=plans)
        stage = MemoryFormationStageTrace(
            name="write_planning",
            status="succeeded" if plans else "empty",
            inputs={"candidate_count": len(candidates)},
            outputs={"plan_count": len(plans), "plans": [item.to_dict() for item in plans]},
            mode="integration_aware" if integration_plans else "preliminary",
        )
        artifact_debug = self._persist_single_stage_artifact(session_id=session_id, episode_id=episode_id, stage=stage)
        next_job = self.queue.enqueue(
            job_type=FORMATION_APPLY_JOB_TYPE,
            session_id=session_id,
            episode_id=episode_id or None,
            priority=60,
            payload=_stage_payload(payload),
        )
        result = {"pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION, "stage": stage.to_dict(), "dry_run_writes": write_result.to_dict() if write_result else {}, "pipeline_artifacts": artifact_debug, "next_job_id": next_job.job_id, "next_job_type": next_job.job_type}
        self.queue.complete(job.job_id, result=result)
        return result

    async def _run_apply_job(self, job: MemoryJob) -> Dict[str, Any]:
        payload = job.payload or {}
        session_id = str(payload.get("session_id") or job.session_id or "")
        episode_payload = payload.get("episode_payload") or {}
        episode_id = str(episode_payload.get("episode_id") or job.episode_id or "")
        observation_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="observation_extraction",
        ) or {}
        candidate_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="candidate_formation",
        ) or {}
        write_output = self.load_stage_output(
            session_id=session_id,
            episode_id=episode_id,
            stage_name="write_planning",
        ) or {}
        plans = _write_plans_from_payload(write_output.get("plans"))
        store = MemorySQLiteStore.from_settings()
        observations = [normalize_observation(raw, fallback_id=str(raw.get("observation_id") or f"obs_{index}"), episode_id=episode_id) for index, raw in enumerate(observation_output.get("observations") or []) if isinstance(raw, dict)]
        candidates = _candidates_from_payload(candidate_output.get("candidates"))
        if settings.MEMORY_STORAGE_ENABLED:
            store.persist_observations(session_id=session_id, observations=observations, extractor_mode="staged_observation", extractor_model=None, status="formed" if candidates else "extracted")
            for candidate in candidates:
                store.persist_candidate(session_id=session_id, episode_id=episode_id, candidate=candidate, extractor_mode="staged_candidate_formation", extractor_model=None, status="planned" if plans else "extracted")
            for plan in plans:
                store.persist_write_plan(session_id=session_id, plan=plan)
        apply_result = None
        if settings.MEMORY_STORAGE_ENABLED and settings.MEMORY_STORAGE_APPLY_PLANS and plans:
            apply_result = MemoryWriteApplier(store).apply_plans(session_id=session_id, workspace_dir=payload.get("workspace_dir"), plans=plans)
        storage_debug = {"enabled": settings.MEMORY_STORAGE_ENABLED, "observation_count": len(observations), "candidate_count": len(candidates), "plan_count": len(plans), "applied": bool(apply_result)}
        stage = MemoryFormationStageTrace(
            name="safe_apply",
            status="succeeded" if apply_result else "skipped",
            inputs={"plan_count": len(plans)},
            outputs=apply_result.to_dict() if apply_result else storage_debug,
            mode="apply_plans_enabled" if settings.MEMORY_STORAGE_APPLY_PLANS else "apply_plans_disabled",
        )
        artifact_debug = self._persist_single_stage_artifact(session_id=session_id, episode_id=episode_id, stage=stage)
        result = {"pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION, "stage": stage.to_dict(), "memory_storage": storage_debug, "pipeline_artifacts": artifact_debug, "done": True}
        self.queue.complete(job.job_id, result=result)
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

    def _persist_stage_artifacts(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        stage_trace: List[MemoryFormationStageTrace],
    ) -> Dict[str, Any]:
        store = MemorySQLiteStore.from_settings()
        episode_id = str(episode_payload.get("episode_id") or "") or None
        artifact_ids = store.persist_pipeline_artifacts(
            session_id=session_id,
            episode_id=episode_id,
            job_type="memory_formation",
            contract_version=FORMATION_PIPELINE_CONTRACT_VERSION,
            stages=[stage.to_dict() for stage in stage_trace],
        )
        return {
            "enabled": True,
            "contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
            "artifact_count": len(artifact_ids),
            "artifact_ids": artifact_ids,
        }

    def _persist_single_stage_artifact(
        self,
        *,
        session_id: str,
        episode_id: str,
        stage: MemoryFormationStageTrace,
    ) -> Dict[str, Any]:
        store = MemorySQLiteStore.from_settings()
        artifact_id = store.persist_pipeline_artifact(
            session_id=session_id,
            episode_id=episode_id or None,
            job_type=FORMATION_LEGACY_JOB_TYPE,
            contract_version=FORMATION_PIPELINE_CONTRACT_VERSION,
            stage_index=_stage_index(stage.name),
            stage=stage.to_dict(),
        )
        return {
            "enabled": True,
            "contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
            "artifact_count": 1,
            "artifact_ids": [artifact_id],
        }


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


def _candidates_from_payload(value: Any) -> List[MemoryCandidateLite]:
    raw_items = value if isinstance(value, list) else []
    candidates: List[MemoryCandidateLite] = []
    for index, raw in enumerate(raw_items):
        if isinstance(raw, dict):
            candidates.append(normalize_candidate(raw, fallback_id=str(raw.get("candidate_id") or f"cand_{index}")))
    return candidates


def _stage_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    staged_payload = {
        "session_id": payload.get("session_id"),
        "episode_payload": payload.get("episode_payload") or {},
        "workspace_dir": payload.get("workspace_dir"),
        "pipeline_contract_version": FORMATION_PIPELINE_CONTRACT_VERSION,
        "staged": True,
        "input_source": "pipeline_artifact",
    }
    if payload.get("rerun"):
        staged_payload["rerun"] = True
        staged_payload["rerun_from_stage"] = payload.get("rerun_from_stage")
    return staged_payload


def _next_job_type_after_stage(stage_name: str, *, storage_enabled: bool) -> str:
    if stage_name == "observation_extraction":
        return FORMATION_CANDIDATE_JOB_TYPE
    if stage_name == "candidate_formation":
        return FORMATION_INTEGRATION_JOB_TYPE if storage_enabled else FORMATION_WRITE_JOB_TYPE
    if stage_name == "integration_routing":
        return FORMATION_WRITE_JOB_TYPE
    if stage_name == "write_planning":
        return FORMATION_APPLY_JOB_TYPE
    raise ValueError(f"unsupported_rerun_stage:{stage_name}")


def _stage_index(stage_name: str) -> int:
    order = {
        "observation_extraction": 0,
        "candidate_formation": 1,
        "integration_routing": 2,
        "write_planning": 3,
        "dry_run_write": 4,
        "sqlite_persistence": 5,
        "safe_apply": 6,
    }
    return order.get(stage_name, 999)


def _integration_plans_from_payload(value: Any) -> List[MemoryIntegrationPlan]:
    raw_items = value if isinstance(value, list) else []
    plans: List[MemoryIntegrationPlan] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        plans.append(
            MemoryIntegrationPlan(
                candidate_id=str(raw.get("candidate_id") or ""),
                action=str(raw.get("action") or "NOOP"),
                confidence=float(raw.get("confidence") or 0.0),
                rationale=str(raw.get("rationale") or ""),
                target_memory_id=raw.get("target_memory_id") if isinstance(raw.get("target_memory_id"), str) else None,
                related_memory_ids=[str(item) for item in raw.get("related_memory_ids") or []],
                graph_relations=raw.get("graph_relations") if isinstance(raw.get("graph_relations"), list) else [],
                suggested_text=raw.get("suggested_text") if isinstance(raw.get("suggested_text"), str) else None,
                memory_layers=[str(item) for item in raw.get("memory_layers") or []],
                write_strategy=raw.get("write_strategy") if isinstance(raw.get("write_strategy"), str) else None,
                related_existing_indices=[int(item) for item in raw.get("related_existing_indices") or []],
                blocked_reasons=[str(item) for item in raw.get("blocked_reasons") or []],
                needs_review_reasons=[str(item) for item in raw.get("needs_review_reasons") or []],
            )
        )
    return plans


def _write_plans_from_payload(value: Any) -> List[MemoryWritePlan]:
    raw_items = value if isinstance(value, list) else []
    plans: List[MemoryWritePlan] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        plans.append(
            MemoryWritePlan(
                plan_id=str(raw.get("plan_id") or ""),
                candidate_id=str(raw.get("candidate_id") or ""),
                action=str(raw.get("action") or "NOOP"),
                canonical_store=raw.get("canonical_store") if isinstance(raw.get("canonical_store"), str) else None,
                projections=[str(item) for item in raw.get("projections") or []],
                scope=str(raw.get("scope") or "session"),
                evidence_episode_ids=[str(item) for item in raw.get("evidence_episode_ids") or []],
                confidence=float(raw.get("confidence") or 0.0),
                status=str(raw.get("status") or "blocked"),
                blocked_reasons=[str(item) for item in raw.get("blocked_reasons") or []],
                type=str(raw.get("type") or "non_memory"),
                text=str(raw.get("text") or ""),
                reason=str(raw.get("reason") or ""),
                integration_action=raw.get("integration_action") if isinstance(raw.get("integration_action"), str) else None,
                write_strategy=raw.get("write_strategy") if isinstance(raw.get("write_strategy"), str) else None,
                target_memory_id=raw.get("target_memory_id") if isinstance(raw.get("target_memory_id"), str) else None,
                related_memory_ids=[str(item) for item in raw.get("related_memory_ids") or []],
                graph_relations=raw.get("graph_relations") if isinstance(raw.get("graph_relations"), list) else [],
                memory_layers=[str(item) for item in raw.get("memory_layers") or []],
                needs_review_reasons=[str(item) for item in raw.get("needs_review_reasons") or []],
            )
        )
    return plans
