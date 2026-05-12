from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.formation.jobs import FORMATION_APPLY_JOB_TYPE, MemoryFormationJobRunner
from services.memory.jobs import SUCCEEDED, MemoryJobQueue


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


async def test_main_startup_worker_consumes_memory_jobs() -> None:
    import main

    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
        MEMORY_STORAGE_DB_PATH=str(Path(tmp) / "memory.sqlite3"),
        MEMORY_WORKER_ENABLED=True,
        MEMORY_WORKER_ID="main-worker-smoke",
        MEMORY_WORKER_POLL_INTERVAL_SECONDS=0.01,
        MEMORY_WORKER_MAX_JOBS_PER_TICK=4,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_FORMATION_EXTRACTOR="rule",
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_STORAGE_APPLY_PLANS=False,
    ):
        await main.stop_memory_worker_loop()
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        runner.schedule_staged(
            session_id="session-main-worker",
            episode_payload={
                "episode_id": "ep_main_worker",
                "session_id": "session-main-worker",
                "turn_index": 1,
                "messages": [
                    {"role": "user", "content": "Decision: app startup worker consumes queued memory jobs."},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
            },
            workspace_dir=tmp,
        )
        try:
            main.start_memory_worker_loop()
            for _ in range(100):
                if queue.list_jobs(status=SUCCEEDED, job_type=FORMATION_APPLY_JOB_TYPE):
                    break
                await asyncio.sleep(0.02)
            assert queue.list_jobs(status=SUCCEEDED, job_type=FORMATION_APPLY_JOB_TYPE)
            task = getattr(main.app.state, "memory_worker_task", None)
            assert task is not None
            assert not task.done()
        finally:
            await main.stop_memory_worker_loop()


def main_test() -> None:
    asyncio.run(test_main_startup_worker_consumes_memory_jobs())
    print("memory app worker smoke ok")


if __name__ == "__main__":
    main_test()
