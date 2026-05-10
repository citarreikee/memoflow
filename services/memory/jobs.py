from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import settings


PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
DEAD = "dead"


@dataclass(frozen=True)
class MemoryJob:
    job_id: str
    job_type: str
    status: str
    priority: int
    session_id: Optional[str]
    episode_id: Optional[str]
    candidate_id: Optional[str]
    plan_id: Optional[str]
    attempt_count: int
    max_attempts: int
    run_after: str
    locked_by: Optional[str]
    locked_at: Optional[str]
    created_at: str
    updated_at: str
    payload: Dict[str, Any]
    result: Dict[str, Any]
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoryJobQueue:
    def __init__(self, base_dir: str, *, db_path: Optional[str] = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.base_dir / "memory_jobs.sqlite3"
        self._initialize()

    @classmethod
    def from_settings(cls) -> "MemoryJobQueue":
        return cls(settings.MEMORY_DATA_DIR)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_jobs (
                    job_id TEXT PRIMARY KEY,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    session_id TEXT,
                    episode_id TEXT,
                    candidate_id TEXT,
                    plan_id TEXT,
                    attempt_count INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    run_after TEXT NOT NULL,
                    locked_by TEXT,
                    locked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_memory_jobs_ready
                ON memory_jobs(status, run_after, priority, created_at);

                CREATE INDEX IF NOT EXISTS idx_memory_jobs_session
                ON memory_jobs(session_id, episode_id, job_type, status);
                """
            )

    def enqueue(
        self,
        *,
        job_type: str,
        payload: Dict[str, Any],
        session_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        priority: int = 100,
        max_attempts: int = 3,
        run_after: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> MemoryJob:
        now = utc_now()
        job_id = job_id or f"mjob_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_jobs (
                    job_id, job_type, status, priority, session_id, episode_id, candidate_id, plan_id,
                    attempt_count, max_attempts, run_after, locked_by, locked_at, created_at, updated_at,
                    payload_json, result_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job_type,
                    PENDING,
                    priority,
                    session_id,
                    episode_id,
                    candidate_id,
                    plan_id,
                    0,
                    max_attempts,
                    run_after or now,
                    None,
                    None,
                    now,
                    now,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps({}, ensure_ascii=False),
                    None,
                ),
            )
        job = self.get(job_id)
        if job is None:
            raise RuntimeError(f"memory_job_enqueue_failed:{job_id}")
        return job

    def claim_next(self, *, worker_id: str, job_types: Optional[List[str]] = None) -> Optional[MemoryJob]:
        now = utc_now()
        with self._connect() as conn:
            clauses = ["status = ?", "run_after <= ?"]
            params: List[Any] = [PENDING, now]
            if job_types:
                clauses.append("job_type IN (" + ",".join(["?"] * len(job_types)) + ")")
                params.extend(job_types)
            row = conn.execute(
                f"""
                SELECT * FROM memory_jobs
                WHERE {' AND '.join(clauses)}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not row:
                return None
            job_id = row["job_id"]
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?, locked_by = ?, locked_at = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (RUNNING, worker_id, now, now, job_id, PENDING),
            )
        return self.get(job_id)

    def complete(self, job_id: str, *, result: Optional[Dict[str, Any]] = None) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?, result_json = ?, error = NULL, locked_by = NULL, locked_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (SUCCEEDED, json.dumps(result or {}, ensure_ascii=False), now, job_id),
            )

    def fail(self, job_id: str, *, error: str, retry_delay_seconds: int = 60) -> None:
        job = self.get(job_id)
        if job is None:
            return
        now = utc_now()
        if job.attempt_count >= job.max_attempts:
            status = DEAD
            run_after = now
        else:
            status = PENDING
            run_after = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = ?, run_after = ?, error = ?, locked_by = NULL, locked_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (status, run_after, error, now, job_id),
            )

    def requeue_stale_running(
        self,
        *,
        stale_after_seconds: int,
        job_types: Optional[List[str]] = None,
    ) -> Dict[str, int]:
        if stale_after_seconds <= 0:
            return {"requeued": 0, "dead": 0}
        now_dt = datetime.now(timezone.utc)
        cutoff = (now_dt - timedelta(seconds=stale_after_seconds)).isoformat()
        now = now_dt.isoformat()
        clauses = ["status = ?", "locked_at IS NOT NULL", "locked_at <= ?"]
        params: List[Any] = [RUNNING, cutoff]
        if job_types:
            clauses.append("job_type IN (" + ",".join(["?"] * len(job_types)) + ")")
            params.extend(job_types)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT job_id, attempt_count, max_attempts
                FROM memory_jobs
                WHERE {' AND '.join(clauses)}
                """,
                params,
            ).fetchall()
            requeued = 0
            dead = 0
            for row in rows:
                if int(row["attempt_count"]) >= int(row["max_attempts"]):
                    conn.execute(
                        """
                        UPDATE memory_jobs
                        SET status = ?, run_after = ?, locked_by = NULL, locked_at = NULL, updated_at = ?, error = ?
                        WHERE job_id = ? AND status = ?
                        """,
                        (DEAD, now, now, "stale_lock_dead", row["job_id"], RUNNING),
                    )
                    dead += 1
                else:
                    conn.execute(
                        """
                        UPDATE memory_jobs
                        SET status = ?, run_after = ?, locked_by = NULL, locked_at = NULL, updated_at = ?, error = ?
                        WHERE job_id = ? AND status = ?
                        """,
                        (PENDING, now, now, "stale_lock_requeued", row["job_id"], RUNNING),
                    )
                    requeued += 1
        return {"requeued": requeued, "dead": dead}

    def get(self, job_id: str) -> Optional[MemoryJob]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return _row_to_job(row) if row else None

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[MemoryJob]:
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if job_type:
            clauses.append("job_type = ?")
            params.append(job_type)
        params.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_jobs
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_job(row) for row in rows]

    def has_active_job(self, *, session_id: str, job_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM memory_jobs
                WHERE session_id = ?
                  AND job_type = ?
                  AND status IN (?, ?)
                LIMIT 1
                """,
                (session_id, job_type, PENDING, RUNNING),
            ).fetchone()
            return row is not None


def _row_to_job(row: sqlite3.Row) -> MemoryJob:
    return MemoryJob(
        job_id=row["job_id"],
        job_type=row["job_type"],
        status=row["status"],
        priority=int(row["priority"]),
        session_id=row["session_id"],
        episode_id=row["episode_id"],
        candidate_id=row["candidate_id"],
        plan_id=row["plan_id"],
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        run_after=row["run_after"],
        locked_by=row["locked_by"],
        locked_at=row["locked_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        payload=_decode_json(row["payload_json"]),
        result=_decode_json(row["result_json"]),
        error=row["error"],
    )


def _decode_json(value: str) -> Dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {}
    except json.JSONDecodeError:
        return {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
