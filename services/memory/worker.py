from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from config import settings
from services.memory.compaction_jobs import COMPACTION_JOB_TYPE, MemoryCompactionJobRunner
from services.memory.formation.jobs import FORMATION_LEGACY_JOB_TYPE, FORMATION_STAGED_JOB_TYPES, MemoryFormationJobRunner
from services.memory.jobs import MemoryJob, MemoryJobQueue
from services.memory.session_store import SessionStore


@dataclass(frozen=True)
class WorkerRunResult:
    worker_id: str
    claimed: bool
    job_id: Optional[str] = None
    job_type: Optional[str] = None
    status: str = "idle"
    error: Optional[str] = None
    result: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoryWorker:
    def __init__(
        self,
        *,
        queue: MemoryJobQueue,
        formation_runner: Optional[MemoryFormationJobRunner] = None,
        compaction_runner: Optional[MemoryCompactionJobRunner] = None,
        worker_id: Optional[str] = None,
        retry_delay_seconds: int = 60,
        stale_after_seconds: Optional[int] = None,
    ) -> None:
        self.queue = queue
        self.worker_id = worker_id or f"memory-worker-{uuid.uuid4().hex[:8]}"
        self.formation_runner = formation_runner or MemoryFormationJobRunner(log_dir=str(queue.base_dir), queue=queue)
        self.compaction_runner = compaction_runner or MemoryCompactionJobRunner(
            store=SessionStore(str(queue.base_dir)),
            queue=queue,
        )
        self.retry_delay_seconds = retry_delay_seconds
        self.stale_after_seconds = stale_after_seconds if stale_after_seconds is not None else settings.MEMORY_JOB_STALE_AFTER_SECONDS

    async def run_once(self, *, job_types: Optional[List[str]] = None) -> WorkerRunResult:
        self.queue.requeue_stale_running(stale_after_seconds=self.stale_after_seconds, job_types=job_types)
        job = self.queue.claim_next(worker_id=self.worker_id, job_types=job_types)
        if job is None:
            return WorkerRunResult(worker_id=self.worker_id, claimed=False, status="idle")
        try:
            result = await self._dispatch(job)
            return WorkerRunResult(
                worker_id=self.worker_id,
                claimed=True,
                job_id=job.job_id,
                job_type=job.job_type,
                status="succeeded",
                result=result,
            )
        except Exception as exc:
            self.queue.fail(job.job_id, error=str(exc), retry_delay_seconds=self.retry_delay_seconds)
            failed_job = self.queue.get(job.job_id)
            return WorkerRunResult(
                worker_id=self.worker_id,
                claimed=True,
                job_id=job.job_id,
                job_type=job.job_type,
                status=failed_job.status if failed_job else "failed",
                error=str(exc),
            )

    async def run_until_idle(self, *, job_types: Optional[List[str]] = None, max_jobs: int = 50) -> List[WorkerRunResult]:
        results: List[WorkerRunResult] = []
        for _ in range(max(0, max_jobs)):
            result = await self.run_once(job_types=job_types)
            results.append(result)
            if not result.claimed:
                break
            await asyncio.sleep(0)
        return results

    async def _dispatch(self, job: MemoryJob) -> Dict[str, Any]:
        if job.job_type == FORMATION_LEGACY_JOB_TYPE:
            result = await self.formation_runner.run_queued_job(job)
            return result.to_debug_dict()
        if job.job_type in FORMATION_STAGED_JOB_TYPES:
            return await self.formation_runner.run_staged_job(job)
        if job.job_type == COMPACTION_JOB_TYPE:
            result = await self.compaction_runner.run_queued_job(job)
            return result.to_debug_dict()
        raise ValueError(f"unsupported_memory_job_type:{job.job_type}")
