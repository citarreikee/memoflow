from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.formation.pipeline import MemoryFormationResult, run_memory_formation_dry_run
from services.memory.storage.applier import MemoryApplyResult, MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.stores.dry_run import DryRunWriteResult, write_dry_run_outputs


@dataclass
class MemoryFormationJobResult:
    formation: MemoryFormationResult
    write_result: Optional[DryRunWriteResult]
    storage_debug: Optional[Dict[str, Any]] = None
    apply_result: Optional[MemoryApplyResult] = None

    def to_debug_dict(self) -> Dict[str, Any]:
        debug = self.formation.to_debug_dict()
        if self.write_result:
            debug["dry_run_writes"] = self.write_result.to_dict()
        if self.storage_debug:
            debug["memory_storage"] = self.storage_debug
        if self.apply_result:
            debug.setdefault("memory_storage", {})
            debug["memory_storage"].update(self.apply_result.to_dict())
        return debug


class MemoryFormationJobRunner:
    def __init__(self, *, log_dir: str) -> None:
        self.log_dir = log_dir
        self._tasks: List[asyncio.Task] = []

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
        if settings.MEMORY_STORAGE_ENABLED:
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
        )

    def schedule(self, *, session_id: str, episode_payload: Dict[str, Any], workspace_dir: Optional[str] = None) -> asyncio.Task:
        task = asyncio.create_task(
            self.run(session_id=session_id, episode_payload=episode_payload, workspace_dir=workspace_dir)
        )
        self._tasks.append(task)
        task.add_done_callback(self._discard_done_task)
        return task

    def _discard_done_task(self, task: asyncio.Task) -> None:
        self._tasks = [item for item in self._tasks if item is not task]

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
