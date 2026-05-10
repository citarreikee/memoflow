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
from services.memory.formation.jobs import MemoryFormationJobRunner
from services.memory.formation.schemas import MemoryWritePlan
from services.memory.jobs import MemoryJobQueue, SUCCEEDED
from services.memory.storage.applier import MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
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


async def test_queued_formation_emits_integration_plan() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_STORAGE_DB_PATH=str(Path(tmp) / "memory.sqlite3"),
        MEMORY_FORMATION_DRY_RUN=False,
        MEMORY_FORMATION_EXTRACTOR="rule",
        MEMORY_STORAGE_ENABLED=True,
        MEMORY_STORAGE_APPLY_PLANS=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        store = MemorySQLiteStore(tmp, db_path=settings.MEMORY_STORAGE_DB_PATH)
        seed_plan = MemoryWritePlan(
            plan_id="seed_sidecar_decision",
            candidate_id="seed_candidate",
            action="ADD",
            canonical_store="semantic_kv",
            projections=["episode_log", "vector_projection"],
            scope="project",
            evidence_episode_ids=["ep_seed"],
            confidence=0.9,
            status="planned",
            type="decision",
            text="Decision: use local qwen sidecar for memory compaction.",
            reason="seed existing memory",
        )
        MemoryWriteApplier(store).apply_plans(session_id="session-integration", workspace_dir=tmp, plans=[seed_plan])

        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        job = runner.schedule(
            session_id="session-integration",
            workspace_dir=tmp,
            episode_payload={
                "episode_id": "ep_replaces_sidecar",
                "session_id": "session-integration",
                "turn_index": 1,
                "messages": [
                    {
                        "role": "user",
                        "content": "Decision: deepseek sidecar replaces local qwen sidecar for memory compaction.",
                    },
                    {"role": "assistant", "content": "Acknowledged."},
                ],
            },
        )
        worker = MemoryWorker(queue=queue, formation_runner=runner, worker_id="integration-worker", retry_delay_seconds=0)

        result = await worker.run_once(job_types=["memory_formation"])
        completed = queue.get(job.job_id)
        assert result.status == "succeeded"
        assert completed is not None
        assert completed.status == SUCCEEDED
        integration = completed.result.get("memory_integration") or {}
        write_plans = completed.result.get("plans") or []
        plans = integration.get("plans") or []
        actions = [entry.get("plan", {}).get("action") for entry in plans]

        assert integration.get("enabled") is True
        assert integration.get("candidate_count", 0) >= 1
        assert integration.get("snapshot_count", 0) >= 1
        assert "SUPERSEDE" in actions
        supersede = next(entry for entry in plans if entry.get("plan", {}).get("action") == "SUPERSEDE")
        assert supersede.get("snapshot_count", 0) >= 1
        assert supersede["plan"].get("target_memory_id")
        supersede_write_plan = next(plan for plan in write_plans if plan.get("integration_action") == "SUPERSEDE")
        assert supersede_write_plan["action"] == "SUPERSEDE"
        assert supersede_write_plan["write_strategy"] == "supersede_existing"
        assert supersede_write_plan["target_memory_id"] == supersede["plan"].get("target_memory_id")
        assert "relation_graph" in supersede_write_plan["projections"]


def main() -> None:
    asyncio.run(test_queued_formation_emits_integration_plan())
    print("memory formation integration job smoke ok")


if __name__ == "__main__":
    main()
