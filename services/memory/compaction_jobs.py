from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from services.context_utils import estimate_messages_tokens, flatten_turns
from services.memory.checkpoint_audit import build_checkpoint_trace, sanitize_checkpoint_summary
from services.memory.checkpoints import get_latest_checkpoint, save_checkpoint
from services.memory.compaction import build_checkpoint_summary, estimate_checkpoint_tokens
from services.memory.jobs import MemoryJob, MemoryJobQueue
from services.memory.session_store import SessionStore


COMPACTION_JOB_TYPE = "memory_compaction"


@dataclass(frozen=True)
class MemoryCompactionJobResult:
    session_id: str
    triggered: bool
    saved: bool
    skipped_reason: Optional[str] = None
    checkpoint_id: Optional[str] = None
    covered_count: int = 0
    older_turn_count: int = 0
    token_estimate: int = 0
    compaction_debug: Dict[str, Any] | None = None
    quality: Dict[str, Any] | None = None
    trace: Dict[str, Any] | None = None

    def to_debug_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MemoryCompactionJobRunner:
    def __init__(self, *, store: SessionStore, queue: MemoryJobQueue | None = None) -> None:
        self.store = store
        self.queue = queue or MemoryJobQueue.from_settings()

    def has_active_job(self, *, session_id: str) -> bool:
        return self.queue.has_active_job(session_id=session_id, job_type=COMPACTION_JOB_TYPE)

    def schedule(
        self,
        *,
        session_id: str,
        episode_id: Optional[str],
        older_turns: List[List[Dict[str, Any]]],
        covered_episode_ids: List[str],
        previous_checkpoint: Optional[Dict[str, Any]],
        reasons: List[str],
        token_budget: int,
        post_turn_estimated_tokens: int,
    ) -> MemoryJob:
        return self.queue.enqueue(
            job_type=COMPACTION_JOB_TYPE,
            session_id=session_id,
            episode_id=episode_id,
            priority=60,
            payload={
                "session_id": session_id,
                "older_turns": older_turns,
                "covered_episode_ids": covered_episode_ids,
                "previous_checkpoint": previous_checkpoint,
                "reasons": reasons,
                "token_budget": token_budget,
                "post_turn_estimated_tokens": post_turn_estimated_tokens,
                "older_turn_count": len(older_turns),
                "older_turn_token_estimate": estimate_messages_tokens(flatten_turns(older_turns)),
            },
        )

    async def run_queued_job(self, job: MemoryJob) -> MemoryCompactionJobResult:
        payload = job.payload or {}
        result = await self.run(
            session_id=str(payload.get("session_id") or job.session_id or ""),
            older_turns=payload.get("older_turns") if isinstance(payload.get("older_turns"), list) else [],
            covered_episode_ids=[str(item) for item in payload.get("covered_episode_ids") or []],
            previous_checkpoint=payload.get("previous_checkpoint") if isinstance(payload.get("previous_checkpoint"), dict) else None,
        )
        self.queue.complete(job.job_id, result=result.to_debug_dict())
        return result

    async def run(
        self,
        *,
        session_id: str,
        older_turns: List[List[Dict[str, Any]]],
        covered_episode_ids: List[str],
        previous_checkpoint: Optional[Dict[str, Any]] = None,
    ) -> MemoryCompactionJobResult:
        if not session_id:
            return MemoryCompactionJobResult(session_id=session_id, triggered=False, saved=False, skipped_reason="session_missing")
        if not older_turns:
            return MemoryCompactionJobResult(session_id=session_id, triggered=False, saved=False, skipped_reason="older_turns_missing")
        if not covered_episode_ids:
            return MemoryCompactionJobResult(session_id=session_id, triggered=False, saved=False, skipped_reason="covered_episode_ids_missing")

        latest_checkpoint = get_latest_checkpoint(self.store, session_id)
        latest_covered = set(latest_checkpoint.get("covers_episode_ids", [])) if latest_checkpoint else set()
        target_covered = set(covered_episode_ids)
        if target_covered and target_covered.issubset(latest_covered):
            return MemoryCompactionJobResult(
                session_id=session_id,
                triggered=False,
                saved=False,
                skipped_reason="already_covered_by_latest_checkpoint",
                checkpoint_id=latest_checkpoint.get("checkpoint_id") if latest_checkpoint else None,
                covered_count=len(latest_covered),
            )

        summary, compaction_debug = await build_checkpoint_summary(
            older_turns,
            previous_checkpoint=latest_checkpoint or previous_checkpoint,
        )
        summary, quality_report = sanitize_checkpoint_summary(summary)
        if not quality_report.ok:
            return MemoryCompactionJobResult(
                session_id=session_id,
                triggered=True,
                saved=False,
                skipped_reason="quality_gate_failed",
                older_turn_count=len(older_turns),
                compaction_debug=compaction_debug,
                quality=quality_report.to_dict(),
            )

        checkpoint = save_checkpoint(
            self.store,
            session_id=session_id,
            covered_episode_ids=covered_episode_ids,
            summary=summary,
            token_estimate=estimate_checkpoint_tokens(summary),
        )
        return MemoryCompactionJobResult(
            session_id=session_id,
            triggered=True,
            saved=True,
            checkpoint_id=checkpoint["checkpoint_id"],
            covered_count=len(covered_episode_ids),
            older_turn_count=len(older_turns),
            token_estimate=checkpoint["token_estimate"],
            compaction_debug=compaction_debug,
            quality=quality_report.to_dict(),
            trace=build_checkpoint_trace(self.store, session_id=session_id, checkpoint=checkpoint),
        )
