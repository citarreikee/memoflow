from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.formation.jobs import MemoryFormationJobRunner
from services.memory.jobs import MemoryJobQueue
from services.memory.worker import MemoryWorker


async def main_async() -> None:
    args = parse_args()
    queue = MemoryJobQueue(args.data_dir or settings.MEMORY_DATA_DIR)
    formation_runner = MemoryFormationJobRunner(log_dir=args.log_dir or settings.MEMORY_WRITE_PLAN_LOG_DIR, queue=queue)
    worker = MemoryWorker(
        queue=queue,
        formation_runner=formation_runner,
        worker_id=args.worker_id,
        retry_delay_seconds=args.retry_delay_seconds,
        stale_after_seconds=args.stale_after_seconds,
    )
    job_types = args.job_type or None

    if args.until_idle:
        results = await worker.run_until_idle(job_types=job_types, max_jobs=args.max_jobs)
        emit({"mode": "until_idle", "results": [result.to_dict() for result in results], "summary": summarize(results)}, compact=args.compact)
        return

    if args.loop:
        all_results = []
        loops = 0
        while args.max_loops <= 0 or loops < args.max_loops:
            result = await worker.run_once(job_types=job_types)
            all_results.append(result)
            emit({"mode": "loop_tick", "result": result.to_dict()}, compact=True)
            loops += 1
            if not result.claimed:
                await asyncio.sleep(args.poll_interval_seconds)
        emit({"mode": "loop", "results": [result.to_dict() for result in all_results], "summary": summarize(all_results)}, compact=args.compact)
        return

    result = await worker.run_once(job_types=job_types)
    emit({"mode": "once", "result": result.to_dict(), "summary": summarize([result])}, compact=args.compact)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Memoflow memory background worker.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process at most one ready job. This is the default.")
    mode.add_argument("--until-idle", action="store_true", help="Process ready jobs until the queue is idle or max-jobs is reached.")
    mode.add_argument("--loop", action="store_true", help="Continuously poll for jobs. Use max-loops for smoke tests.")
    parser.add_argument("--data-dir", default="", help="Memory data directory. Defaults to MEMORY_DATA_DIR.")
    parser.add_argument("--log-dir", default="", help="Formation dry-run log directory. Defaults to MEMORY_WRITE_PLAN_LOG_DIR.")
    parser.add_argument("--worker-id", default="", help="Stable worker id. Generated if omitted.")
    parser.add_argument("--job-type", action="append", help="Restrict worker to one or more job types. Can be repeated.")
    parser.add_argument("--max-jobs", type=int, default=50, help="Max jobs for --until-idle.")
    parser.add_argument("--retry-delay-seconds", type=int, default=60, help="Delay before retrying failed jobs.")
    parser.add_argument("--stale-after-seconds", type=int, default=settings.MEMORY_JOB_STALE_AFTER_SECONDS, help="Requeue running jobs locked longer than this. 0 disables stale recovery.")
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0, help="Polling interval for --loop when idle.")
    parser.add_argument("--max-loops", type=int, default=0, help="Max loop ticks for --loop. 0 means forever.")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON.")
    args = parser.parse_args()
    if not args.once and not args.until_idle and not args.loop:
        args.once = True
    return args


def summarize(results: List[Any]) -> Dict[str, int]:
    summary = {"claimed": 0, "succeeded": 0, "failed": 0, "dead": 0, "idle": 0}
    for result in results:
        if result.claimed:
            summary["claimed"] += 1
        if result.status == "succeeded":
            summary["succeeded"] += 1
        elif result.status == "dead":
            summary["dead"] += 1
            summary["failed"] += 1
        elif result.status in {"failed", "pending", "running"}:
            summary["failed"] += 1
        elif result.status == "idle":
            summary["idle"] += 1
    return summary


def emit(payload: Dict[str, Any], *, compact: bool = False) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 2))


if __name__ == "__main__":
    asyncio.run(main_async())
