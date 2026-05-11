from __future__ import annotations

import tempfile
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from chat_history import SessionManager
from services.memory.formation.schemas import MemoryWritePlan
from services.memory.retrieval.pipeline import MemoryRetrievalPipeline
from services.memory.runtime import MemoryRuntime, RuntimeInput
from services.memory.session_store import SessionStore
from services.memory.transcript_store import persist_session_message
from services.memory.storage.sqlite_store import MemorySQLiteStore


def test_query_reconstruction_finds_prior_state() -> None:
    with _store() as store:
        store.insert_memory_record(
            scope="session",
            namespace="session:retrieval2",
            memory_type="task_state",
            key="state:open-loop-runtime-ready",
            value="Open loop: finish runtime retrieval integration before lifecycle work.",
            status="active",
            confidence=0.9,
            version=1,
            source_plan_id="seed_plan_state",
            payload={},
        )
        pack = MemoryRetrievalPipeline().run(
            user_message="上次那个问题解决了吗？",
            session_key="session:retrieval2",
            token_budget=8000,
            store=store,
        )
        assert pack.intent.kind == "task_state_lookup"
        assert "active_state" in pack.intent.intents
        assert any("runtime retrieval integration" in item.text for item in pack.items)
        assert pack.trace[0]["reconstructed_queries"]


def test_retrieval_dedupes_recent_context() -> None:
    with _store() as store:
        text = "Decision: use query reconstruction before retrieval intent planning."
        store.insert_memory_record(
            scope="session",
            namespace="session:retrieval2",
            memory_type="decision",
            key="decision:query-reconstruction-first",
            value=text,
            status="active",
            confidence=0.9,
            version=1,
            source_plan_id="seed_plan_decision",
            payload={},
        )
        pack = MemoryRetrievalPipeline().run(
            user_message="What was the decision about query reconstruction?",
            session_key="session:retrieval2",
            token_budget=8000,
            store=store,
            recent_context_text=f"User and assistant just discussed: {text}",
        )
        assert not pack.items
        assert any(item.get("reason") == "redundant_with_recent_context" for item in pack.trace)


def test_review_items_are_conflict_marked_not_authoritative() -> None:
    with _store() as store:
        plan = MemoryWritePlan(
            plan_id="seed_review_plan",
            candidate_id="seed_review_candidate",
            action="CONFLICT",
            canonical_store="semantic_kv",
            projections=[],
            scope="session",
            evidence_episode_ids=["episode_review"],
            confidence=0.5,
            status="needs_review",
            type="decision",
            text="Conflict: old plan says use local sidecar, new plan says use online sidecar.",
            reason="seed review conflict",
        )
        store.insert_review_item(
            session_id="session:retrieval2",
            plan=plan,
            reason="Conflict about sidecar provider choice needs review.",
        )
        pack = MemoryRetrievalPipeline().run(
            user_message="Is there any conflict about sidecar provider choice?",
            session_key="session:retrieval2",
            token_budget=8000,
            store=store,
        )
        assert any(item.source == "review_items" for item in pack.items)
        review_items = [item for item in pack.items if item.source == "review_items"]
        assert all(item.authority == "review_only" for item in review_items)
        assert all(item.conflict for item in review_items)


async def test_finalize_records_retrieval_usage_audit() -> None:
    with _store() as store:
        session_store = SessionStore(settings.MEMORY_DATA_DIR)
        runtime = MemoryRuntime(session_store)
        manager = SessionManager()
        session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": settings.MEMORY_DATA_DIR})
        session_store.upsert_session(session)
        session_key = f"session:{session.session_id}"
        store.insert_memory_record(
            scope="session",
            namespace=session_key,
            memory_type="decision",
            key="decision:query-reconstruction-first",
            value="Decision: use query reconstruction before retrieval intent planning.",
            status="active",
            confidence=0.9,
            version=1,
            source_plan_id="seed_plan_audit",
            payload={},
        )
        user = manager.add_message(session.session_id, "user", "What was the decision about query reconstruction?")
        assert user is not None
        persist_session_message(session_store, session, user)
        package = await runtime.prepare_turn(
            RuntimeInput(
                session_id=session.session_id,
                session_key=session_key,
                model="deepseek-v4-pro",
                provider="deepseek",
                input_source="retrieval-2-contract",
                user_message="What was the decision about query reconstruction?",
                workspace_dir=settings.MEMORY_DATA_DIR,
                history_messages=manager.get_history(session_id=session.session_id, limit=None) or [],
                latest_checkpoint=None,
                token_budget=8000,
                context_policy={"model": "deepseek-v4-pro", "provider": "deepseek"},
            )
        )
        assert package.debug["retrieval"]["items"]
        assistant = manager.add_message(
            session.session_id,
            "assistant",
            "The decision was to use query reconstruction before retrieval intent planning.",
        )
        assert assistant is not None
        persist_session_message(session_store, session, assistant)
        debug = await runtime.finalize_turn(
            manager,
            session_id=session.session_id,
            input_source="retrieval-2-contract",
            token_budget=8000,
            memory_debug=package.debug,
        )
        usage = debug["audit"]["retrieval_usage"]
        assert usage["items_retrieved"] >= 1
        assert usage["items_cited_by_model"] >= 1
        assert "retrieval_usage_audited" in debug["events"]


class _store:
    def __enter__(self) -> MemorySQLiteStore:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = {
            "MEMORY_DATA_DIR": settings.MEMORY_DATA_DIR,
            "MEMORY_STORAGE_DB_PATH": settings.MEMORY_STORAGE_DB_PATH,
            "MEMORY_STORAGE_ENABLED": settings.MEMORY_STORAGE_ENABLED,
            "MEMORY_RETRIEVAL_ENABLED": settings.MEMORY_RETRIEVAL_ENABLED,
        }
        settings.MEMORY_DATA_DIR = self.tmp.name
        settings.MEMORY_STORAGE_DB_PATH = str(Path(self.tmp.name) / "memory.sqlite3")
        settings.MEMORY_STORAGE_ENABLED = True
        settings.MEMORY_RETRIEVAL_ENABLED = True
        return MemorySQLiteStore(self.tmp.name, db_path=settings.MEMORY_STORAGE_DB_PATH)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        settings.MEMORY_DATA_DIR = self.previous["MEMORY_DATA_DIR"]
        settings.MEMORY_STORAGE_DB_PATH = self.previous["MEMORY_STORAGE_DB_PATH"]
        settings.MEMORY_STORAGE_ENABLED = self.previous["MEMORY_STORAGE_ENABLED"]
        settings.MEMORY_RETRIEVAL_ENABLED = self.previous["MEMORY_RETRIEVAL_ENABLED"]
        self.tmp.cleanup()


if __name__ == "__main__":
    import asyncio

    test_query_reconstruction_finds_prior_state()
    test_retrieval_dedupes_recent_context()
    test_review_items_are_conflict_marked_not_authoritative()
    asyncio.run(test_finalize_records_retrieval_usage_audit())
    print("memory retrieval 2 contract ok")
