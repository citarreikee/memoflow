from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chat_history import SessionManager
from config import settings
from services.memory.formation.pipeline import MemoryFormationResult
from services.memory.formation.schemas import MemoryWritePlan
from services.memory.retrieval.pipeline import MemoryRetrievalPipeline
from services.memory.retrieval.ranker import build_retrieval_pack
from services.memory.retrieval.schemas import RetrievalCandidate, RetrievalIntent, RetrievalPlan
from services.memory.runtime import MemoryRuntime, RuntimeInput
from services.memory.session_store import SessionStore
from services.memory.storage.applier import MemoryWriteApplier
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.transcript_store import persist_session_message


class SettingsPatch:
    def __init__(self, **values: Any) -> None:
        self.values = values
        self.previous: Dict[str, Any] = {}

    def __enter__(self) -> "SettingsPatch":
        for key, value in self.values.items():
            self.previous[key] = getattr(settings, key)
            setattr(settings, key, value)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)


class FailingFormationJobs:
    async def run(self, **_: Any) -> MemoryFormationResult:
        raise RuntimeError("formation boom")

    def schedule(self, **_: Any) -> None:
        raise RuntimeError("formation schedule boom")


class FailingRetrievalStore:
    def search_active_records(self, **_: Any) -> List[Dict[str, Any]]:
        raise RuntimeError("retrieval db boom")


async def test_formation_disabled_persists_episode() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_FORMATION_ENABLED=False,
        MEMORY_FORMATION_BACKGROUND=False,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_RETRIEVAL_ENABLED=False,
        MEMORY_DATA_DIR=tmp,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)
        _add_turn(manager, store, session.session_id, "hello", "hi")

        debug = await runtime.finalize_turn(
            manager,
            session_id=session.session_id,
            input_source="stabilization",
            token_budget=8000,
            memory_debug={},
        )

        formation = debug.get("memory_formation") or {}
        assert store.get_episode_count(session.session_id) == 1
        assert formation.get("triggered") is False
        assert formation.get("skipped_reason") == "disabled"
        assert "memory_formation_skipped" in debug.get("events", [])


async def test_formation_failure_is_non_blocking() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_FORMATION_ENABLED=True,
        MEMORY_FORMATION_BACKGROUND=False,
        MEMORY_STORAGE_ENABLED=False,
        MEMORY_RETRIEVAL_ENABLED=False,
        MEMORY_DATA_DIR=tmp,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        runtime.formation_jobs = FailingFormationJobs()  # type: ignore[assignment]
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)
        _add_turn(manager, store, session.session_id, "remember project rule: keep memory features default off", "noted")

        debug = await runtime.finalize_turn(
            manager,
            session_id=session.session_id,
            input_source="stabilization",
            token_budget=8000,
            memory_debug={},
        )

        formation = debug.get("memory_formation") or {}
        assert store.get_episode_count(session.session_id) == 1
        assert formation.get("skipped_reason") == "formation_error"
        assert "formation boom" in formation.get("error", "")
        assert "memory_formation_failed" in debug.get("events", [])


async def test_retrieval_disabled_does_not_inject_message() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_RETRIEVAL_ENABLED=False,
        MEMORY_STORAGE_ENABLED=False,
        FILE_MEMORY_ENABLED=False,
        MEMORY_DATA_DIR=tmp,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)
        user = manager.add_message(session.session_id, "user", "remember the previous decision")
        persist_session_message(store, session, user)

        package = await runtime.prepare_turn(
            RuntimeInput(
                session_id=session.session_id,
                session_key=session.session_id,
                model="deepseek-v4-pro",
                provider="deepseek",
                input_source="stabilization",
                user_message="remember the previous decision",
                workspace_dir=tmp,
                history_messages=manager.get_history(session_id=session.session_id, limit=None),
                latest_checkpoint=None,
                token_budget=8000,
                context_policy={"model": "deepseek-v4-pro", "provider": "deepseek"},
            )
        )

        assert package.debug["retrieval"]["intent"]["kind"] == "none"
        assert not any("Retrieved long-term memory" in str(message.get("content", "")) for message in package.messages)


def test_retrieval_failure_is_empty_pack() -> None:
    with SettingsPatch(MEMORY_RETRIEVAL_ENABLED=True, MEMORY_STORAGE_ENABLED=True):
        pack = MemoryRetrievalPipeline().run(
            user_message="remember project decision",
            session_key="session-a",
            token_budget=8000,
            store=FailingRetrievalStore(),  # type: ignore[arg-type]
        )

        assert pack.items == []
        assert pack.intent.kind == "none"
        assert pack.trace[0]["decision"] == "failed"
        assert "retrieval db boom" in pack.trace[0]["reason"]


async def test_retrieval_empty_store_is_non_blocking() -> None:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        MEMORY_RETRIEVAL_ENABLED=True,
        MEMORY_STORAGE_ENABLED=True,
        MEMORY_STORAGE_DB_PATH=str(Path(tmp) / "memory.sqlite3"),
        FILE_MEMORY_ENABLED=False,
        MEMORY_DATA_DIR=tmp,
    ):
        store = SessionStore(tmp)
        runtime = MemoryRuntime(store)
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
        store.upsert_session(session)
        user = manager.add_message(session.session_id, "user", "remember the project decision")
        persist_session_message(store, session, user)

        package = await runtime.prepare_turn(
            RuntimeInput(
                session_id=session.session_id,
                session_key=session.session_id,
                model="deepseek-v4-pro",
                provider="deepseek",
                input_source="stabilization",
                user_message="remember the project decision",
                workspace_dir=tmp,
                history_messages=manager.get_history(session_id=session.session_id, limit=None),
                latest_checkpoint=None,
                token_budget=8000,
                context_policy={"model": "deepseek-v4-pro", "provider": "deepseek"},
            )
        )

        assert package.debug["retrieval"]["items"] == []
        assert package.debug["retrieval"]["trace"][0]["decision"] in {"planned", "failed"}
        assert package.messages[-1]["role"] == "user"


def test_retrieval_budget_omits_extra_items() -> None:
    plan = RetrievalPlan(
        intent=RetrievalIntent(kind="broad_recall", query="remember project"),
        paths=["exact_record"],
        namespace="session-a",
        scopes=["session"],
        limit_per_path=10,
        max_pack_items=1,
        max_pack_chars=30,
        min_score=0.0,
    )
    candidates = [
        RetrievalCandidate(
            source="exact_record",
            memory_id=f"mem_{index}",
            projection_id=None,
            edge_id=None,
            scope="session",
            memory_type="decision",
            text=f"short memory {index}",
            score=0.8 - index * 0.1,
            reason="test",
        )
        for index in range(3)
    ]

    pack = build_retrieval_pack(plan, candidates)

    assert len(pack.items) == 1
    assert pack.omitted_count == 2
    assert any(item.get("decision") == "excluded" for item in pack.trace)


def test_bad_write_plan_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = MemorySQLiteStore(tmp, db_path=str(Path(tmp) / "memory.sqlite3"))
        plan = MemoryWritePlan(
            plan_id="plan_bad",
            candidate_id="cand_bad",
            action="ADD",
            canonical_store="semantic_kv",
            projections=["vector_projection"],
            scope="project",
            evidence_episode_ids=[],
            confidence=0.9,
            status="planned",
            type="project_rule",
            text="Keep memory writes explicit.",
            reason="test",
        )

        result = MemoryWriteApplier(store).apply_plans(session_id="session-a", workspace_dir=tmp, plans=[plan])

        assert result.blocked_count == 1
        assert "evidence_missing" in result.blocked_reasons
        assert store.count_rows("memory_records") == 0
        assert store.count_rows("memory_vector_projections") == 0


def _add_turn(manager: SessionManager, store: SessionStore, session_id: str, user_text: str, assistant_text: str) -> None:
    session = manager.get_session(session_id)
    assert session is not None
    user = manager.add_message(session_id, "user", user_text)
    persist_session_message(store, session, user)
    assistant = manager.add_message(session_id, "assistant", assistant_text)
    persist_session_message(store, session, assistant)


def main() -> None:
    asyncio.run(test_formation_disabled_persists_episode())
    asyncio.run(test_formation_failure_is_non_blocking())
    asyncio.run(test_retrieval_disabled_does_not_inject_message())
    test_retrieval_failure_is_empty_pack()
    asyncio.run(test_retrieval_empty_store_is_non_blocking())
    test_retrieval_budget_omits_extra_items()
    test_bad_write_plan_is_blocked()
    print("memory stabilization smoke ok")


if __name__ == "__main__":
    main()
