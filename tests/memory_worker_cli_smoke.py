from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.formation.jobs import MemoryFormationJobRunner
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


def test_cli_once_consumes_queued_formation_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue, job_id = _enqueue_formation_job(tmp, episode_id="ep_cli_once", text="Decision: CLI worker consumes queued jobs.")

        payload = _run_worker_cli(["--once", "--data-dir", tmp, "--log-dir", tmp, "--worker-id", "cli-once", "--compact"], tmp)
        completed = queue.get(job_id)

        assert payload["mode"] == "once"
        assert payload["result"]["claimed"] is True
        assert payload["result"]["status"] == "succeeded"
        assert payload["summary"]["succeeded"] == 1
        assert completed is not None
        assert completed.status == SUCCEEDED
        assert completed.result.get("triggered") is True


def test_cli_until_idle_consumes_multiple_jobs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        queue, _first_id = _enqueue_formation_job(tmp, episode_id="ep_cli_idle_1", text="Decision: until-idle consumes job one.")
        _queue, _second_id = _enqueue_formation_job(tmp, episode_id="ep_cli_idle_2", text="Decision: until-idle consumes job two.")
        assert _queue.db_path == queue.db_path

        payload = _run_worker_cli(
            ["--until-idle", "--data-dir", tmp, "--log-dir", tmp, "--worker-id", "cli-until-idle", "--compact"],
            tmp,
        )

        assert payload["mode"] == "until_idle"
        assert payload["summary"]["claimed"] == 2
        assert payload["summary"]["succeeded"] == 2
        assert payload["summary"]["idle"] == 1
        assert len(queue.list_jobs(status=SUCCEEDED, job_type="memory_formation")) == 2


def test_cli_loop_can_idle_without_job() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        payload = _run_worker_cli(
            [
                "--loop",
                "--max-loops",
                "1",
                "--poll-interval-seconds",
                "0",
                "--data-dir",
                tmp,
                "--log-dir",
                tmp,
                "--worker-id",
                "cli-loop-idle",
                "--compact",
            ],
            tmp,
            parse_last_json_line=True,
        )

        assert payload["mode"] == "loop"
        assert payload["summary"]["claimed"] == 0
        assert payload["summary"]["idle"] == 1


def _enqueue_formation_job(tmp: str, *, episode_id: str, text: str) -> tuple[MemoryJobQueue, str]:
    with SettingsPatch(
        MEMORY_DATA_DIR=tmp,
        MEMORY_FORMATION_DRY_RUN=True,
        MEMORY_FORMATION_EXTRACTOR="rule",
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_STORAGE_APPLY_PLANS=False,
        MEMORY_WRITE_PLAN_LOG_DIR=tmp,
    ):
        queue = MemoryJobQueue(tmp)
        runner = MemoryFormationJobRunner(log_dir=tmp, queue=queue)
        job = runner.schedule(
            session_id="session-cli-worker",
            episode_payload={
                "episode_id": episode_id,
                "session_id": "session-cli-worker",
                "turn_index": 1,
                "messages": [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": "Acknowledged."},
                ],
            },
            workspace_dir=tmp,
        )
        return queue, job.job_id


def _run_worker_cli(args: List[str], tmp: str, *, parse_last_json_line: bool = False) -> Dict[str, Any]:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT_DIR),
            "MEMORY_DATA_DIR": tmp,
            "MEMORY_WRITE_PLAN_LOG_DIR": tmp,
            "MEMORY_FORMATION_DRY_RUN": "true",
            "MEMORY_FORMATION_EXTRACTOR": "rule",
            "MEMORY_STORAGE_ENABLED": "false",
            "MEMORY_STORAGE_APPLY_PLANS": "false",
        }
    )
    completed = subprocess.run(
        [sys.executable, "tools/memory_worker.py", *args],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    assert lines, "worker CLI produced no stdout"
    raw = lines[-1] if parse_last_json_line else completed.stdout.strip()
    return json.loads(raw)


def main() -> None:
    test_cli_once_consumes_queued_formation_job()
    test_cli_until_idle_consumes_multiple_jobs()
    test_cli_loop_can_idle_without_job()
    print("memory worker cli smoke ok")


if __name__ == "__main__":
    main()
