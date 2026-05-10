from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.compaction_jobs import COMPACTION_JOB_TYPE, MemoryCompactionJobRunner
from services.memory.formation.jobs import MemoryFormationJobRunner
from services.memory.jobs import DEAD, PENDING, SUCCEEDED, MemoryJobQueue
from services.memory.session_store import SessionStore
from services.memory.transcript_store import persist_episode
from services.memory.worker import MemoryWorker


class SettingsPatch:
    def __init__(self, **values: Any) -> None:
        self.values = values
        self.previous: dict[str, Any] = {}

    def __enter__(self) -> "SettingsPatch":
        for key, value in self.values.items():
            self.previous[key] = getattr(settings, key)
            setattr(settings, key, value)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)


async def test_worker_consumes_formation_job() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        episode_payload = {
            "episode_id": "ep_worker_formation",
            "session_id": "session-worker",
            "turn_index": 1,
            "messages": [
                {"role": "user", "content": "Decision: memory workers should consume queued formation jobs."},
                {"role": "assistant", "content": "Acknowledged."},
            ],
        }
        job = runner.schedule(session_id="session-worker", episode_payload=episode_payload, workspace_dir=tmp)
        worker = MemoryWorker(queue=queue, formation_runner=runner, worker_id="worker-a", retry_delay_seconds=0)

        result = await worker.run_once(job_types=["memory_formation"])
        completed = queue.get(job.job_id)

        assert result.claimed is True
        assert result.status == "succeeded"
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.result.get("triggered") is True


async def test_worker_retries_then_deads_unknown_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = MemoryJobQueue(tmp)
        job = queue.enqueue(job_type="unknown_job", payload={}, max_attempts=2)
        worker = MemoryWorker(queue=queue, worker_id="worker-a", retry_delay_seconds=0)

        first = await worker.run_once()
        after_first = queue.get(job.job_id)
        second = await worker.run_once()
        after_second = queue.get(job.job_id)

        assert first.status == PENDING
        assert after_first is not None
        assert after_first.status == PENDING
        assert after_first.attempt_count == 1
        assert second.status == DEAD
        assert after_second is not None
        assert after_second.status == DEAD
        assert after_second.attempt_count == 2
        assert "unsupported_memory_job_type" in (after_second.error or "")


async def test_worker_run_until_idle_processes_multiple_jobs() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        for index in range(2):
            runner.schedule(
                session_id="session-worker",
                episode_payload={
                    "episode_id": f"ep_worker_{index}",
                    "session_id": "session-worker",
                    "turn_index": index + 1,
                    "messages": [
                        {"role": "user", "content": f"Decision: worker loop handles job {index}."},
                        {"role": "assistant", "content": "Acknowledged."},
                    ],
                },
                workspace_dir=tmp,
            )
        worker = MemoryWorker(queue=queue, formation_runner=runner, worker_id="worker-a", retry_delay_seconds=0)

        results = await worker.run_until_idle(job_types=["memory_formation"], max_jobs=5)
        succeeded = [result for result in results if result.status == "succeeded"]
        idle = results[-1]

        assert len(succeeded) == 2
        assert idle.status == "idle"
        assert len(queue.list_jobs(status=SUCCEEDED)) == 2


async def test_worker_requeues_stale_running_job_before_claim() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        job = runner.schedule(
            session_id="session-worker",
            episode_payload={
                "episode_id": "ep_worker_stale",
                "session_id": "session-worker",
                "turn_index": 1,
                "messages": [
                    {"role": "user", "content": "Decision: stale running jobs should be reclaimed by workers."},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
            },
            workspace_dir=tmp,
        )
        claimed = queue.claim_next(worker_id="crashed-worker", job_types=["memory_formation"])
        assert claimed is not None
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with queue._connect() as conn:
            conn.execute("UPDATE memory_jobs SET locked_at = ? WHERE job_id = ?", (stale_time, job.job_id))
        worker = MemoryWorker(
            queue=queue,
            formation_runner=runner,
            worker_id="recovery-worker",
            retry_delay_seconds=0,
            stale_after_seconds=60,
        )

        result = await worker.run_once(job_types=["memory_formation"])
        completed = queue.get(job.job_id)

        assert result.status == "succeeded"
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.attempt_count == 2


async def test_worker_consumes_compaction_job_and_saves_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        SIDECAR_COMPACTION_ENABLED=False,
        CONTEXT_COMPACTION_KEEP_RECENT_TURNS=1,
    ):
        queue = MemoryJobQueue(tmp)
        store = SessionStore(tmp)
        session_id = "session-compaction-worker"
        for index in range(3):
            episode = {
                "episode_id": f"ep_compact_{index}",
                "session_id": session_id,
                "turn_index": index + 1,
                "source": "worker-compaction-smoke",
                "message_ids": [f"msg_u_{index}", f"msg_a_{index}"],
                "messages": [
                    {"role": "user", "content": f"Decision: worker compaction preserves turn {index}."},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
                "token_estimate": 20,
                "created_at": "2026-05-11T00:00:00",
            }
            persist_episode(store, episode)
        runner = MemoryCompactionJobRunner(store=store, queue=queue)
        job = runner.schedule(
            session_id=session_id,
            episode_id="ep_compact_2",
            older_turns=[
                [
                    {"role": "user", "content": "Decision: worker compaction preserves turn zero."},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
                [
                    {"role": "user", "content": "Decision: worker compaction preserves turn one."},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
            ],
            covered_episode_ids=["ep_compact_0", "ep_compact_1"],
            previous_checkpoint=None,
            reasons=["worker_compaction_smoke"],
            token_budget=1000,
            post_turn_estimated_tokens=500,
        )
        worker = MemoryWorker(
            queue=queue,
            compaction_runner=runner,
            worker_id="compaction-worker",
            retry_delay_seconds=0,
        )

        result = await worker.run_once(job_types=[COMPACTION_JOB_TYPE])
        completed = queue.get(job.job_id)
        checkpoint = store.get_latest_checkpoint(session_id)

        assert result.status == "succeeded"
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.result.get("saved") is True
        assert checkpoint is not None
        assert checkpoint["covered_episode_ids"] == ["ep_compact_0", "ep_compact_1"]


def main() -> None:
    asyncio.run(test_worker_consumes_formation_job())
    asyncio.run(test_worker_retries_then_deads_unknown_job())
    asyncio.run(test_worker_run_until_idle_processes_multiple_jobs())
    asyncio.run(test_worker_requeues_stale_running_job_before_claim())
    asyncio.run(test_worker_consumes_compaction_job_and_saves_checkpoint())
    print("memory worker smoke ok")


if __name__ == "__main__":
    main()
