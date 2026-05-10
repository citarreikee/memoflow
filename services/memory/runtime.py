from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import settings
from services.context_utils import (
    estimate_messages_tokens,
    flatten_turns,
    group_messages_by_user_turn,
    history_message_to_dict,
    trim_tool_message_content,
)
from services.memory.checkpoint_audit import (
    build_checkpoint_trace,
)
from services.memory.checkpoints import get_latest_checkpoint, save_checkpoint
from services.memory.compaction_jobs import MemoryCompactionJobRunner
from services.memory.compaction import build_checkpoint_summary, estimate_checkpoint_tokens, split_turns_for_compaction
from services.memory.formation.jobs import MemoryFormationJobRunner
from services.memory.policy import (
    RuntimePolicyDecision,
    RuntimeState,
    classify_token_pressure,
    decide_post_turn_policy,
    decide_prepare_policy,
)
from services.memory.retrieval.assembler import render_retrieval_message
from services.memory.retrieval.pipeline import MemoryRetrievalPipeline
from services.memory.runtime_events import RuntimeEventLog
from services.memory.session_store import SessionStore, session_store
from services.memory.transcript_store import build_episode_payload, next_turn_index, persist_episode
from services.memory.working_set import assemble_working_set


RUNTIME_VERSION = "0.2"


@dataclass
class RuntimeInput:
    session_id: str
    session_key: str
    model: str
    provider: str
    input_source: str
    user_message: str
    workspace_dir: str
    history_messages: List[Any]
    latest_checkpoint: Optional[Dict[str, Any]]
    token_budget: int
    context_policy: Optional[Dict[str, Any]] = None


@dataclass
class ContextPackage:
    messages: List[Dict[str, Any]]
    token_budget: int
    estimated_tokens: int
    policy_decision: RuntimePolicyDecision
    pending_checkpoint: Optional[Dict[str, Any]] = None
    file_memories: List[Dict[str, str]] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)


class MemoryRuntime:
    """Context compiler and maintenance scheduler for Memory Runtime v0.1."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.formation_jobs = MemoryFormationJobRunner(log_dir=settings.MEMORY_WRITE_PLAN_LOG_DIR)
        self.compaction_jobs = MemoryCompactionJobRunner(store=store)
        self.retrieval = MemoryRetrievalPipeline()

    async def prepare_turn(self, runtime_input: RuntimeInput) -> ContextPackage:
        events = RuntimeEventLog()
        events.add("turn_started", session_id=runtime_input.session_id, source=runtime_input.input_source)

        history = self._history_to_provider_messages(runtime_input.history_messages)
        trimmed_history = trim_tool_message_content(history, settings.CONTEXT_TOOL_RESULT_MAX_CHARS)
        turns = group_messages_by_user_turn(trimmed_history)
        state = self._build_state(
            session_id=runtime_input.session_id,
            history=trimmed_history,
            turns=turns,
            token_budget=runtime_input.token_budget,
            latest_checkpoint=runtime_input.latest_checkpoint,
        )
        events.add("runtime_state_built", **state.to_dict())

        decision = decide_prepare_policy(
            state=state,
            latest_checkpoint=runtime_input.latest_checkpoint,
            user_message=runtime_input.user_message,
        )
        if decision.load_checkpoint:
            checkpoint_trace = build_checkpoint_trace(
                self.store,
                session_id=runtime_input.session_id,
                checkpoint=runtime_input.latest_checkpoint,
            )
            events.add(
                "checkpoint_loaded",
                checkpoint_id=runtime_input.latest_checkpoint.get("checkpoint_id") if runtime_input.latest_checkpoint else None,
                covered_count=len(runtime_input.latest_checkpoint.get("covers_episode_ids", []))
                if runtime_input.latest_checkpoint
                else 0,
                trace=checkpoint_trace,
            )

        retrieval_pack = self.retrieval.run(
            user_message=runtime_input.user_message,
            session_key=runtime_input.session_key,
            token_budget=runtime_input.token_budget,
        )
        retrieval_message = render_retrieval_message(retrieval_pack)
        if retrieval_pack.items:
            events.add("memory_retrieved", included_count=len(retrieval_pack.items), omitted_count=retrieval_pack.omitted_count)
        else:
            events.add("memory_retrieval_empty", trace=retrieval_pack.trace)

        package = assemble_working_set(
            history_messages=runtime_input.history_messages,
            token_budget=runtime_input.token_budget,
            trim_tool_result_chars=settings.CONTEXT_TOOL_RESULT_MAX_CHARS,
            existing_checkpoint=runtime_input.latest_checkpoint if decision.load_checkpoint else None,
            workspace_dir=runtime_input.workspace_dir,
            user_message=runtime_input.user_message,
            load_file_memory=decision.load_file_memory,
            retrieval_message=retrieval_message,
        )
        if package.file_memories:
            events.add("file_memory_loaded", files=[item["path"] for item in package.file_memories])

        messages = package.messages
        estimated_tokens = package.estimated_tokens
        pending_checkpoint = None
        if estimated_tokens > runtime_input.token_budget and len(turns) > settings.CONTEXT_COMPACTION_KEEP_RECENT_TURNS:
            decision.emergency_compaction = True
            decision.reason.append("assembled_context_over_budget")
            events.add("emergency_compaction_triggered", estimated_tokens=estimated_tokens)
            emergency = await self._build_checkpoint_from_turns(
                turns=turns,
                latest_checkpoint=runtime_input.latest_checkpoint,
            )
            if emergency:
                pending_checkpoint = emergency
                package = assemble_working_set(
                    history_messages=runtime_input.history_messages,
                    token_budget=runtime_input.token_budget,
                    trim_tool_result_chars=settings.CONTEXT_TOOL_RESULT_MAX_CHARS,
                    existing_checkpoint=emergency,
                    workspace_dir=runtime_input.workspace_dir,
                    user_message=runtime_input.user_message,
                    load_file_memory=decision.load_file_memory,
                    retrieval_message=retrieval_message,
                )
                messages = package.messages
                estimated_tokens = package.estimated_tokens

        events.add("context_compiled", estimated_tokens=estimated_tokens, message_count=len(messages))
        debug = self._build_debug(
            decision=decision,
            runtime_input=runtime_input,
            state=state,
            session_id=runtime_input.session_id,
            estimated_tokens=estimated_tokens,
            token_budget=runtime_input.token_budget,
            context_policy=runtime_input.context_policy,
            checkpoint=runtime_input.latest_checkpoint,
            recent_turn_count=package.recent_turn_count,
            file_memories=package.file_memories,
            retrieval_pack=retrieval_pack.to_dict(),
            events=events,
        )
        return ContextPackage(
            messages=messages,
            token_budget=runtime_input.token_budget,
            estimated_tokens=estimated_tokens,
            policy_decision=decision,
            pending_checkpoint=pending_checkpoint,
            file_memories=package.file_memories,
            debug=debug,
        )

    async def finalize_turn(
        self,
        session_manager: Any,
        *,
        session_id: str,
        input_source: str,
        token_budget: int,
        memory_debug: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        debug = memory_debug if memory_debug is not None else {}
        events = RuntimeEventLog()
        session = session_manager.get_session(session_id)
        if not session:
            events.add("finalize_skipped", reason="session_missing")
            return self._merge_finalize_debug(debug, events, None)

        history_messages = session_manager.get_history(session_id=session_id, limit=None) or []
        turn_messages = _collect_last_turn_messages(history_messages)
        if not turn_messages:
            events.add("finalize_skipped", reason="last_turn_missing")
            return self._merge_finalize_debug(debug, events, None)

        turn_index = next_turn_index(self.store, session_id)
        episode_payload = build_episode_payload(
            session_id=session_id,
            turn_index=turn_index,
            source=input_source,
            messages=turn_messages,
            token_estimate=estimate_messages_tokens([history_message_to_dict(message) for message in turn_messages]),
        )
        persist_episode(self.store, episode_payload)
        events.add("episode_persisted", episode_id=episode_payload["episode_id"], turn_index=turn_index)
        workspace_dir = (session.metadata or {}).get("workspace_dir") if hasattr(session, "metadata") else None
        formation_debug = await self._run_memory_formation(
            session_id=session_id,
            episode_payload=episode_payload,
            workspace_dir=workspace_dir,
            events=events,
        )

        latest_checkpoint = get_latest_checkpoint(self.store, session_id)
        history = self._history_to_provider_messages(history_messages)
        history = trim_tool_message_content(history, settings.CONTEXT_TOOL_RESULT_MAX_CHARS)
        turns = group_messages_by_user_turn(history)
        covered_count = len(latest_checkpoint.get("covers_episode_ids", [])) if latest_checkpoint else 0
        uncovered_turns = turns[covered_count:]
        older_turns, _recent_turns = split_turns_for_compaction(uncovered_turns)
        episodes = self.store.list_episodes(session_id)
        if len(episodes) < len(turns):
            older_turns = []
            events.add(
                "post_turn_compaction_skipped",
                reason="episode_turn_mapping_incomplete",
                episode_count=len(episodes),
                turn_count=len(turns),
            )
        post_turn_tokens = estimate_messages_tokens(history)
        post_policy = decide_post_turn_policy(
            post_turn_estimated_tokens=post_turn_tokens,
            token_budget=token_budget,
            uncovered_user_turn_count=len(uncovered_turns),
            uncovered_old_turn_count=len(older_turns),
            checkpoint_job_pending=self.compaction_jobs.has_active_job(session_id=session_id),
        )

        saved_checkpoint: Optional[Dict[str, Any]] = None
        if post_policy.post_turn_compaction and older_turns:
            events.add("post_turn_compaction_triggered", reasons=post_policy.reason)
            try:
                covered_episode_ids = self._covered_episode_ids_for_count(session_id, covered_count + len(older_turns))
                if covered_episode_ids:
                    job = self.compaction_jobs.schedule(
                        session_id=session_id,
                        episode_id=episode_payload["episode_id"],
                        older_turns=older_turns,
                        covered_episode_ids=covered_episode_ids,
                        previous_checkpoint=latest_checkpoint,
                        reasons=post_policy.reason,
                        token_budget=token_budget,
                        post_turn_estimated_tokens=post_turn_tokens,
                    )
                    events.add(
                        "post_turn_compaction_queued",
                        job_id=job.job_id,
                        job_type=job.job_type,
                        covered_count=len(covered_episode_ids),
                        older_turn_count=len(older_turns),
                        background=True,
                    )
                else:
                    events.add("post_turn_compaction_skipped", reason="covered_episode_ids_missing")
            except Exception as exc:
                events.add("post_turn_compaction_queue_failed", error=str(exc))

        return self._merge_finalize_debug(debug, events, post_policy, saved_checkpoint, formation_debug=formation_debug)

    def _build_state(
        self,
        *,
        session_id: str,
        history: List[Dict[str, Any]],
        turns: List[List[Dict[str, Any]]],
        token_budget: int,
        latest_checkpoint: Optional[Dict[str, Any]],
    ) -> RuntimeState:
        checkpoint_covered_count = len(latest_checkpoint.get("covers_episode_ids", [])) if latest_checkpoint else 0
        estimated_full_tokens = estimate_messages_tokens(history)
        estimated_checkpoint_tokens = int(latest_checkpoint.get("token_estimate", 0)) if latest_checkpoint else 0
        recent_turns = turns[checkpoint_covered_count:]
        estimated_recent_tokens = estimate_messages_tokens(flatten_turns(recent_turns))
        ratio = estimated_full_tokens / token_budget if token_budget > 0 else 0.0
        return RuntimeState(
            raw_message_count=len(history),
            user_turn_count=len(turns),
            uncovered_turn_count=len(recent_turns),
            episode_count=self.store.get_episode_count(session_id),
            checkpoint_covered_count=checkpoint_covered_count,
            estimated_full_tokens=estimated_full_tokens,
            estimated_checkpoint_tokens=estimated_checkpoint_tokens,
            estimated_recent_tokens=estimated_recent_tokens,
            token_budget=token_budget,
            token_pressure_ratio=ratio,
            risk_level=classify_token_pressure(estimated_full_tokens, token_budget),
        )

    async def _build_checkpoint_from_turns(
        self,
        *,
        turns: List[List[Dict[str, Any]]],
        latest_checkpoint: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        older_turns, _recent_turns = split_turns_for_compaction(turns)
        if not older_turns:
            return None
        summary, _debug = await build_checkpoint_summary(older_turns, previous_checkpoint=latest_checkpoint)
        return {
            "checkpoint_id": "pending_emergency",
            "summary": summary,
            "covers_episode_ids": [],
            "token_estimate": estimate_checkpoint_tokens(summary),
        }

    def _covered_episode_ids_for_count(self, session_id: str, covered_count: int) -> List[str]:
        if covered_count <= 0:
            return []
        episodes = self.store.list_episodes(session_id)
        return [episode["episode_id"] for episode in episodes[:covered_count]]

    def _history_to_provider_messages(self, history_messages: List[Any]) -> List[Dict[str, Any]]:
        return [history_message_to_dict(message) for message in history_messages]

    def _build_debug(
        self,
        *,
        decision: RuntimePolicyDecision,
        runtime_input: RuntimeInput,
        state: RuntimeState,
        session_id: str,
        estimated_tokens: int,
        token_budget: int,
        context_policy: Optional[Dict[str, Any]],
        checkpoint: Optional[Dict[str, Any]],
        recent_turn_count: int,
        file_memories: List[Dict[str, str]],
        retrieval_pack: Dict[str, Any],
        events: RuntimeEventLog,
    ) -> Dict[str, Any]:
        pressure_ratio = estimated_tokens / token_budget if token_budget > 0 else 0.0
        return {
            "runtime_version": RUNTIME_VERSION,
            "audit_version": "0.1",
            "policy": {
                "load_checkpoint": decision.load_checkpoint,
                "load_file_memory": decision.load_file_memory,
                "post_turn_compaction_scheduled": decision.post_turn_compaction,
                "emergency_compaction": decision.emergency_compaction,
                "retrieval_mode": decision.retrieval_mode,
                "reason": decision.reason,
            },
            "budget": {
                "model": context_policy.get("model") if context_policy else None,
                "provider": context_policy.get("provider") if context_policy else None,
                "context_window": context_policy.get("context_window") if context_policy else None,
                "context_window_source": context_policy.get("context_window_source") if context_policy else None,
                "budget_ratio": context_policy.get("budget_ratio") if context_policy else None,
                "threshold_source": context_policy.get("threshold_source") if context_policy else None,
                "thresholds": context_policy.get("thresholds") if context_policy else None,
                "token_budget": token_budget,
                "estimated_tokens": estimated_tokens,
                "pressure_ratio": round(pressure_ratio, 4),
                "risk_level": classify_token_pressure(estimated_tokens, token_budget),
            },
            "state": state.to_dict(),
            "context_compile": self._build_context_compile_debug(
                runtime_input=runtime_input,
                decision=decision,
                state=state,
                estimated_tokens=estimated_tokens,
                token_budget=token_budget,
                recent_turn_count=recent_turn_count,
                file_memories=file_memories,
                retrieval_pack=retrieval_pack,
                checkpoint=checkpoint,
            ),
            "sources": {
                "checkpoint_id": checkpoint.get("checkpoint_id") if checkpoint else None,
                "recent_turn_count": recent_turn_count,
                "covered_episode_count": len(checkpoint.get("covers_episode_ids", [])) if checkpoint else 0,
                "checkpoint_trace": build_checkpoint_trace(self.store, session_id=session_id, checkpoint=checkpoint),
                "file_memory": [item["path"] for item in file_memories],
                "retrieval_pack": retrieval_pack.get("items", []),
            },
            "retrieval": retrieval_pack,
            "audit": {
                "answering_questions": [
                    "what_context_was_loaded",
                    "why_this_policy_was_chosen",
                    "what_was_persisted_after_turn",
                ],
                "checkpoint_quality": None,
                "checkpoint_trace": None,
            },
            "events": events.names(),
            "event_details": events.to_dicts(),
        }

    def _build_context_compile_debug(
        self,
        *,
        runtime_input: RuntimeInput,
        decision: RuntimePolicyDecision,
        state: RuntimeState,
        estimated_tokens: int,
        token_budget: int,
        recent_turn_count: int,
        file_memories: List[Dict[str, str]],
        retrieval_pack: Dict[str, Any],
        checkpoint: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        retrieval_items = retrieval_pack.get("items") if isinstance(retrieval_pack, dict) else []
        retrieval_intent = retrieval_pack.get("intent") if isinstance(retrieval_pack, dict) else {}
        retrieval_kind = retrieval_intent.get("kind") if isinstance(retrieval_intent, dict) else None
        retrieval_sources = sorted(
            {str(item.get("source")) for item in retrieval_items or [] if isinstance(item, dict) and item.get("source")}
        )
        dirty_reasons = self._context_dirty_reasons(
            runtime_input=runtime_input,
            decision=decision,
            file_memories=file_memories,
            retrieval_items=retrieval_items if isinstance(retrieval_items, list) else [],
            checkpoint=checkpoint,
        )
        append_only_fast_path = (
            state.risk_level == "safe"
            and not dirty_reasons
            and not retrieval_items
            and not decision.emergency_compaction
            and not decision.load_checkpoint
            and not decision.load_file_memory
        )
        return {
            "version": "instrumentation_v0",
            "stable_frame_cache_enabled": False,
            "stable_frame_id": None,
            "reused_stable_frame": False,
            "dirty_reasons": dirty_reasons,
            "append_only_fast_path_eligible": append_only_fast_path,
            "append_only_fast_path_used": False,
            "retrieval_mode": retrieval_kind or "none",
            "retrieval_sources": retrieval_sources,
            "estimated_tokens": estimated_tokens,
            "token_budget": token_budget,
            "recent_turn_count": recent_turn_count,
            "note": "Instrumentation only; stable frame cache and turn-delta reuse are not implemented yet.",
        }

    def _context_dirty_reasons(
        self,
        *,
        runtime_input: RuntimeInput,
        decision: RuntimePolicyDecision,
        file_memories: List[Dict[str, str]],
        retrieval_items: List[Dict[str, Any]],
        checkpoint: Optional[Dict[str, Any]],
    ) -> List[str]:
        reasons: List[str] = []
        if checkpoint:
            reasons.append("checkpoint_loaded")
        if file_memories or decision.load_file_memory:
            reasons.append("file_memory_loaded")
        if retrieval_items:
            reasons.append("query_dependent_retrieval")
        if decision.emergency_compaction:
            reasons.append("emergency_compaction")
        if not runtime_input.model or not runtime_input.provider:
            reasons.append("model_context_missing")
        if runtime_input.token_budget <= 0:
            reasons.append("token_budget_missing")
        if not runtime_input.workspace_dir:
            reasons.append("workspace_missing")
        return reasons

    def _merge_finalize_debug(
        self,
        debug: Dict[str, Any],
        events: RuntimeEventLog,
        post_policy: Optional[RuntimePolicyDecision],
        saved_checkpoint: Optional[Dict[str, Any]] = None,
        formation_debug: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        debug.setdefault("events", [])
        debug["events"].extend(events.names())
        debug.setdefault("event_details", [])
        debug["event_details"].extend(events.to_dicts())
        if post_policy:
            debug.setdefault("policy", {})
            debug["policy"]["post_turn_compaction_scheduled"] = post_policy.post_turn_compaction
            debug["policy"]["post_turn_reason"] = post_policy.reason
        if saved_checkpoint:
            debug.setdefault("sources", {})
            debug["sources"]["checkpoint_saved"] = saved_checkpoint["checkpoint_id"]
            debug["sources"]["checkpoint_trace"] = build_checkpoint_trace(
                self.store,
                session_id=saved_checkpoint["session_id"],
                checkpoint=saved_checkpoint,
            )
        if formation_debug is not None:
            debug["memory_formation"] = formation_debug
        debug.setdefault("audit", {})
        for event in events.to_dicts():
            event_type = event.get("type")
            data = event.get("data") or {}
            if event_type == "checkpoint_quality_evaluated":
                debug["audit"]["checkpoint_quality"] = data
            if event_type in {"checkpoint_loaded", "checkpoint_saved"} and data.get("trace"):
                debug["audit"]["checkpoint_trace"] = data["trace"]
            if event_type == "post_turn_compaction_queued":
                debug.setdefault("maintenance", {})
                debug["maintenance"]["compaction"] = data
        return debug

    async def _run_memory_formation(
        self,
        *,
        session_id: str,
        episode_payload: Dict[str, Any],
        workspace_dir: Optional[str],
        events: RuntimeEventLog,
    ) -> Dict[str, Any]:
        if not settings.MEMORY_FORMATION_ENABLED:
            events.add("memory_formation_skipped", reason="disabled")
            return {"triggered": False, "episode_ids": [episode_payload["episode_id"]], "skipped_reason": "disabled"}
        try:
            if settings.MEMORY_FORMATION_BACKGROUND:
                job = self.formation_jobs.schedule(
                    session_id=session_id,
                    episode_payload=episode_payload,
                    workspace_dir=workspace_dir,
                )
                events.add(
                    "memory_formation_queued",
                    episode_id=episode_payload["episode_id"],
                    background=True,
                    job_id=job.job_id,
                )
                return {
                    "triggered": True,
                    "scheduled": True,
                    "queued": True,
                    "background": True,
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "episode_ids": [episode_payload["episode_id"]],
                }
            job_result = await self.formation_jobs.run(
                session_id=session_id,
                episode_payload=episode_payload,
                workspace_dir=workspace_dir,
            )
            debug = job_result.to_debug_dict()
            if not job_result.formation.triggered:
                events.add(
                    "memory_formation_skipped",
                    reason=job_result.formation.skipped_reason,
                    episode_id=episode_payload["episode_id"],
                )
                return debug
            write_debug = debug.get("dry_run_writes") or {}
            events.add(
                "memory_formation_planned",
                episode_id=episode_payload["episode_id"],
                candidate_count=len(job_result.formation.candidates),
                plan_count=len(job_result.formation.plans),
                written_count=write_debug.get("total_written", 0),
                dry_run=settings.MEMORY_FORMATION_DRY_RUN,
            )
            return debug
        except Exception as exc:
            events.add("memory_formation_failed", episode_id=episode_payload["episode_id"], error=str(exc))
            return {
                "triggered": False,
                "episode_ids": [episode_payload["episode_id"]],
                "skipped_reason": "formation_error",
                "error": str(exc),
            }


def _collect_last_turn_messages(history_messages: List[Any]) -> List[Any]:
    if not history_messages:
        return []
    collected: List[Any] = []
    for message in reversed(history_messages):
        collected.append(message)
        if message.role == "user" and len(collected) > 1:
            break
    collected.reverse()
    if collected and getattr(collected[0], "role", None) != "user":
        return []
    return collected


memory_runtime = MemoryRuntime(session_store)
