# Memoflow Memory Harness Runner

The memory harness runner is the first productization step for memory quality. It turns scattered smoke scripts and golden cases into a single structured report.

## Command

Quick mode, skips the long compaction scenario:

```powershell
python tools\memory_harness_runner.py --quick
```

Full mode, includes the long runtime compaction scenario. This may take several minutes because sidecar compaction can call an online model:

```powershell
python tools\memory_harness_runner.py
```

Fail fast:

```powershell
python tools\memory_harness_runner.py --fail-fast
```

## Report Location

Reports are written to:

```text
data/harness_reports/memory_harness_report_<UTC_TIMESTAMP>.json
data/harness_reports/latest.json
```

The report includes:

- overall status;
- case counts by status;
- category counts;
- per-case duration;
- per-case command;
- stdout/stderr tails;
- extracted metrics for golden contract, formation scenario eval, compaction scenario eval, and long-task checkpoint coverage.

## Categories

- `formation_quality`: golden fixtures, candidate quality gates, extraction parsing, and scenario-level formation eval from raw episodes.
- `formation_integration`: candidate neighborhood fetch plus existing-memory integration planning, queued formation integration smoke, including ADD/NOOP/MERGE/UPDATE/SUPERSEDE/LINK/NEEDS_REVIEW contracts.
- `runtime_safety`: compile checks, feature flag/failure isolation, durable memory job queue, in-process worker dispatch/retry, worker CLI process smoke, model context policy.
- `formation`: candidate extraction -> write plan smoke.
- `storage`: durable candidate/plan/record/projection persistence smoke.
- `retrieval`: retrieval plan/search/injection smoke and scenario-level retrieval eval.
- `runtime_compaction`: deterministic compaction scenario eval, long-task checkpoint compaction, and recovery trace.




## Stale Job Recovery

The durable job queue can recover jobs left in `running` after a worker crash. `MemoryJobQueue.requeue_stale_running()` releases jobs whose `locked_at` is older than `MEMORY_JOB_STALE_AFTER_SECONDS`. Retryable jobs go back to `pending` with `stale_lock_requeued`; jobs that already reached `max_attempts` become `dead` with `stale_lock_dead`.

Workers call this recovery step before claiming a new job. The CLI exposes the threshold as:

```powershell
python tools\memory_worker.py --once --stale-after-seconds 1800 --compact
```

`tests/memory_job_queue_smoke.py` covers direct queue recovery, and `tests/memory_worker_smoke.py` verifies a worker can reclaim and finish a stale `memory_formation` job.

## Memory Worker CLI

Background memory workflows are intentionally verified outside the main chat request path. The CLI is the deployable worker entry point that claims durable SQLite jobs and writes terminal job status back to the same queue.

Run one ready job:

```powershell
python tools\memory_worker.py --once --compact
```

Drain the current queue until idle:

```powershell
python tools\memory_worker.py --until-idle --compact
```

Run as a polling loop:

```powershell
python tools\memory_worker.py --loop --poll-interval-seconds 2 --compact
```

The harness includes `tests/memory_worker_cli_smoke.py`, which verifies `--once`, `--until-idle`, and bounded `--loop` behavior against a real SQLite-backed queue. The smoke fixes memory formation to the deterministic rule extractor so the test validates worker infrastructure instead of sidecar model quality.
## Current Role

This runner is not a replacement for deeper evaluation. It is the top-level harness that tells us whether a memory upgrade preserved the core safety loop.

Future upgrades should add scenario fixtures and quality metrics here before changing long-term memory behavior.






