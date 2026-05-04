from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chat_history import SessionManager
from services.memory.checkpoint_audit import build_checkpoint_recovery_payload
from services.memory.runtime import RuntimeInput, MemoryRuntime
from services.memory.session_store import SessionStore
from services.memory.transcript_store import persist_session_message


async def run_long_task_smoke(turn_count: int = 20, token_budget: int = 1400) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        manager = SessionManager()
        session = manager.create_session(
            model="deepseek-v4-pro",
            metadata={"workspace_dir": ".", "session_key": "runtime-long-task-smoke"},
        )
        store.upsert_session(session)
        last_debug = None

        for turn_index in range(turn_count):
            user = manager.add_message(
                session.session_id,
                "user",
                _user_turn(turn_index),
            )
            persist_session_message(store, session, user)
            history = manager.get_history(session.session_id, limit=None) or []
            package = await runtime.prepare_turn(
                RuntimeInput(
                    session_id=session.session_id,
                    session_key="runtime-long-task-smoke",
                    model="deepseek-v4-pro",
                    provider="deepseek",
                    input_source="long-task-smoke",
                    user_message=user.content or "",
                    workspace_dir=".",
                    history_messages=history,
                    latest_checkpoint=store.get_latest_checkpoint(session.session_id),
                    token_budget=token_budget,
                )
            )
            assistant = manager.add_message(
                session.session_id,
                "assistant",
                _assistant_turn(turn_index),
            )
            persist_session_message(store, session, assistant)
            last_debug = await runtime.finalize_turn(
                manager,
                session_id=session.session_id,
                input_source="long-task-smoke",
                token_budget=token_budget,
                memory_debug=package.debug,
            )

        checkpoint = store.get_latest_checkpoint(session.session_id)
        recovery = build_checkpoint_recovery_payload(store, session_id=session.session_id)
        result = {
            "session_id": session.session_id,
            "turn_count": turn_count,
            "episode_count": store.get_episode_count(session.session_id),
            "checkpoint_id": checkpoint["checkpoint_id"] if checkpoint else None,
            "checkpoint_covered_count": len(checkpoint.get("covered_episode_ids", [])) if checkpoint else 0,
            "checkpoint_token_estimate": checkpoint.get("token_estimate") if checkpoint else 0,
            "debug_events_tail": (last_debug or {}).get("events", [])[-12:],
            "quality": ((last_debug or {}).get("audit") or {}).get("checkpoint_quality"),
            "trace": recovery.get("trace"),
        }
        _assert_smoke_result(result)
        return result


def _assert_smoke_result(result: dict) -> None:
    if result["episode_count"] != result["turn_count"]:
        raise AssertionError(f"episode_count mismatch: {result}")
    if not result["checkpoint_id"]:
        raise AssertionError(f"checkpoint missing: {result}")
    if result["checkpoint_covered_count"] <= 0:
        raise AssertionError(f"checkpoint covers no episodes: {result}")
    trace = result.get("trace") or {}
    if trace.get("covered_count") != result["checkpoint_covered_count"]:
        raise AssertionError(f"checkpoint trace count mismatch: {result}")
    if not trace.get("coverage_complete"):
        raise AssertionError(f"checkpoint trace is incomplete: {result}")
    quality = result.get("quality") or {}
    if quality and quality.get("ok") is False:
        raise AssertionError(f"checkpoint quality gate failed: {result}")


def _user_turn(turn_index: int) -> str:
    return (
        f"第{turn_index}轮：继续实现 Memory Runtime 质量闭环。"
        "必须保持 prepare_turn 轻量，finalize_turn 后做 compaction。"
        "需要追踪 checkpoint 覆盖 episode、质量门控、debug 审计和恢复能力。"
        "当前开放问题是压测是否能证明长任务不丢主线。"
        + " 工程细节" * 45
    )


def _assistant_turn(turn_index: int) -> str:
    return (
        f"第{turn_index}轮完成：保留 recent raw turns，旧轮进入 checkpoint。"
        "决策：episode 是原始记录，checkpoint 是工作摘要，sidecar 失败不能阻断主链路。"
        "重要文件：services/memory/runtime.py, services/memory/checkpoint_audit.py。"
        "下一步：继续验证覆盖追溯和质量报告。"
        + " 实现说明" * 45
    )


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_long_task_smoke()), ensure_ascii=False, indent=2))
