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

from chat_history import SessionManager
from config import settings
from services.memory.compaction_jobs import COMPACTION_JOB_TYPE
from services.memory.formation.jobs import MemoryFormationJobRunner
from services.memory.jobs import DEAD, PENDING, RUNNING, SUCCEEDED, MemoryJobQueue
from services.memory.runtime import MemoryRuntime
from services.memory.session_store import SessionStore
from services.memory.transcript_store import persist_session_message


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


def test_job_queue_lifecycle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = MemoryJobQueue(tmp)
        job = queue.enqueue(
            job_type="memory_formation",
            session_id="session-a",
            episode_id="ep_1",
            payload={"hello": "world"},
            max_attempts=1,
        )

        assert job.status == PENDING
        claimed = queue.claim_next(worker_id="worker-a", job_types=["memory_formation"])
        assert claimed is not None
        assert claimed.status == RUNNING
        assert claimed.attempt_count == 1
        assert claimed.locked_by == "worker-a"
        queue.complete(claimed.job_id, result={"ok": True})
        completed = queue.get(claimed.job_id)
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.result == {"ok": True}

        failed = queue.enqueue(job_type="memory_formation", payload={}, max_attempts=1)
        claimed_failed = queue.claim_next(worker_id="worker-a")
        assert claimed_failed is not None
        queue.fail(claimed_failed.job_id, error="boom", retry_delay_seconds=0)
        dead = queue.get(claimed_failed.job_id)
        assert dead is not None
        assert dead.status == DEAD
        assert dead.error == "boom"


def test_stale_running_jobs_are_requeued_or_deaded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue = MemoryJobQueue(tmp)
        retryable = queue.enqueue(job_type="memory_formation", payload={}, max_attempts=2)
        claimed_retryable = queue.claim_next(worker_id="crashed-worker")
        assert claimed_retryable is not None
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with queue._connect() as conn:
            conn.execute("UPDATE memory_jobs SET locked_at = ? WHERE job_id = ?", (stale_time, retryable.job_id))

        reclaimed = queue.requeue_stale_running(stale_after_seconds=60)
        after_requeue = queue.get(retryable.job_id)
        assert reclaimed == {"requeued": 1, "dead": 0}
        assert after_requeue is not None
        assert after_requeue.status == PENDING
        assert after_requeue.error == "stale_lock_requeued"
        assert after_requeue.locked_by is None

        terminal = queue.enqueue(job_type="terminal_job", payload={}, max_attempts=1)
        claimed_terminal = queue.claim_next(worker_id="crashed-worker", job_types=["terminal_job"])
        assert claimed_terminal is not None
        with queue._connect() as conn:
            conn.execute("UPDATE memory_jobs SET locked_at = ? WHERE job_id = ?", (stale_time, terminal.job_id))

        deaded = queue.requeue_stale_running(stale_after_seconds=60)
        after_dead = queue.get(terminal.job_id)
        assert deaded == {"requeued": 0, "dead": 1}
        assert after_dead is not None
        assert after_dead.status == DEAD
        assert after_dead.error == "stale_lock_dead"


async def test_runtime_background_formation_enqueues_job() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_ENABLED=True,
        MEMORY_FORMATION_BACKGROUND=True,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        runtime.formation_jobs = MemoryFormationJobRunner(log_dir=tmp, queue=MemoryJobQueue(tmp))
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)
        user = manager.add_message(session.session_id, "user", "Decision: background memory formation should enqueue durable jobs.")
        persist_session_message(store, session, user)
        assistant = manager.add_message(session.session_id, "assistant", "Noted.")
        persist_session_message(store, session, assistant)

        debug = await runtime.finalize_turn(
            manager,
            session_id=session.session_id,
            input_source="memory-job-queue-smoke",
            token_budget=8000,
            memory_debug={},
        )

        formation = debug.get("memory_formation") or {}
        assert formation.get("queued") is True
        assert formation.get("job_id")
        assert "memory_formation_queued" in debug.get("events", [])
        jobs = runtime.formation_jobs.queue.list_jobs(job_type="memory_formation")
        assert len(jobs) == 1
        assert jobs[0].status == PENDING
        assert jobs[0].payload["session_id"] == session.session_id
        assert jobs[0].payload["episode_payload"]["episode_id"] == formation["episode_ids"][0]


async def test_queued_formation_job_can_be_consumed() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_ENABLED=True,
        MEMORY_FORMATION_BACKGROUND=True,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        episode_payload = {
            "episode_id": "ep_queued",
            "session_id": "session-a",
            "turn_index": 1,
            "messages": [
                {"role": "user", "content": "Decision: queued formation jobs should be recoverable."},
                {"role": "assistant", "content": "Acknowledged."},
            ],
        }
        job = runner.schedule(session_id="session-a", episode_payload=episode_payload, workspace_dir=tmp)
        claimed = queue.claim_next(worker_id="worker-a", job_types=["memory_formation"])
        assert claimed is not None
        assert claimed.job_id == job.job_id
        await runner.run_queued_job(claimed)
        completed = queue.get(job.job_id)
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.result.get("triggered") is True


async def test_runtime_post_turn_compaction_enqueues_without_saving_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_ENABLED=False,
        MEMORY_FORMATION_BACKGROUND=True,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
        CONTEXT_COMPACTION_MIN_USER_TURNS=3,
        CONTEXT_COMPACTION_KEEP_RECENT_TURNS=1,
        CONTEXT_COMPACTION_TRIGGER_RATIO=0.01,
        SIDECAR_COMPACTION_ENABLED=True,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)

        queued_debug = None
        for index in range(4):
            user = manager.add_message(session.session_id, "user", f"Decision: compaction queue test turn {index}.")
            persist_session_message(store, session, user)
            assistant = manager.add_message(session.session_id, "assistant", "Acknowledged.")
            persist_session_message(store, session, assistant)
            debug = await runtime.finalize_turn(
                manager,
                session_id=session.session_id,
                input_source="memory-compaction-queue-smoke",
                token_budget=1000,
                memory_debug={},
            )
            if "post_turn_compaction_queued" in debug.get("events", []):
                queued_debug = debug

        jobs = runtime.compaction_jobs.queue.list_jobs(job_type=COMPACTION_JOB_TYPE)
        latest_checkpoint = store.get_latest_checkpoint(session.session_id)

        assert queued_debug is not None
        assert queued_debug.get("maintenance", {}).get("compaction", {}).get("job_id")
        assert len(jobs) == 1
        assert jobs[0].status == PENDING
        assert jobs[0].payload["older_turn_count"] >= 1
        assert latest_checkpoint is None


def main() -> None:
    test_job_queue_lifecycle()
    test_stale_running_jobs_are_requeued_or_deaded()
    asyncio.run(test_runtime_background_formation_enqueues_job())
    asyncio.run(test_queued_formation_job_can_be_consumed())
    asyncio.run(test_runtime_post_turn_compaction_enqueues_without_saving_checkpoint())
    print("memory job queue smoke ok")


if __name__ == "__main__":
    main()
