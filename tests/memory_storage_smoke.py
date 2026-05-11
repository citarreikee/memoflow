from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chat_history import SessionManager
from config import settings
from services.memory.runtime import MemoryRuntime
from services.memory.session_store import SessionStore
from services.memory.storage.sqlite_store import MemorySQLiteStore
from services.memory.transcript_store import persist_session_message


async def run_storage_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous = {
            "formation_enabled": settings.MEMORY_FORMATION_ENABLED,
            "formation_dry_run": settings.MEMORY_FORMATION_DRY_RUN,
            "formation_background": settings.MEMORY_FORMATION_BACKGROUND,
            "storage_enabled": settings.MEMORY_STORAGE_ENABLED,
            "storage_apply": settings.MEMORY_STORAGE_APPLY_PLANS,
            "storage_db_path": settings.MEMORY_STORAGE_DB_PATH,
            "memory_data_dir": settings.MEMORY_DATA_DIR,
            "write_plan_log_dir": settings.MEMORY_WRITE_PLAN_LOG_DIR,
        }
        settings.MEMORY_FORMATION_ENABLED = True
        settings.MEMORY_FORMATION_DRY_RUN = False
        settings.MEMORY_FORMATION_BACKGROUND = False
        settings.MEMORY_STORAGE_ENABLED = True
        settings.MEMORY_STORAGE_APPLY_PLANS = True
        settings.MEMORY_DATA_DIR = tmp
        settings.MEMORY_WRITE_PLAN_LOG_DIR = tmp
        settings.MEMORY_STORAGE_DB_PATH = str(Path(tmp) / "memory.sqlite3")
        try:
            store = SessionStore(tmp)
            runtime = MemoryRuntime(store)
            manager = SessionManager()
            session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
            store.upsert_session(session)

            await _finalize_one(
                manager,
                store,
                runtime,
                session.session_id,
                "决定：v0.3 storage depends_on v0.2 write plans。规则：file memory 只能生成 suggestion，不要静默写文件。",
                "已记录为存储设计约束。",
            )

            memory_store = MemorySQLiteStore(tmp, db_path=settings.MEMORY_STORAGE_DB_PATH)
            assert memory_store.count_rows("memory_observations") >= 1
            assert memory_store.count_rows("memory_candidates") >= 1
            assert memory_store.count_rows("memory_write_plans") >= 1
            assert memory_store.count_rows("memory_records") >= 1
            assert memory_store.count_rows("memory_evidence_links") >= 1
            assert memory_store.count_rows("memory_reindex_jobs") >= 1
            assert memory_store.count_rows("memory_vector_projections") >= 1
            assert memory_store.count_rows("memory_file_suggestions") >= 1
        finally:
            settings.MEMORY_FORMATION_ENABLED = previous["formation_enabled"]
            settings.MEMORY_FORMATION_DRY_RUN = previous["formation_dry_run"]
            settings.MEMORY_FORMATION_BACKGROUND = previous["formation_background"]
            settings.MEMORY_STORAGE_ENABLED = previous["storage_enabled"]
            settings.MEMORY_STORAGE_APPLY_PLANS = previous["storage_apply"]
            settings.MEMORY_STORAGE_DB_PATH = previous["storage_db_path"]
            settings.MEMORY_DATA_DIR = previous["memory_data_dir"]
            settings.MEMORY_WRITE_PLAN_LOG_DIR = previous["write_plan_log_dir"]


async def _finalize_one(
    manager: SessionManager,
    store: SessionStore,
    runtime: MemoryRuntime,
    session_id: str,
    user_text: str,
    assistant_text: str,
) -> dict:
    session = manager.get_session(session_id)
    user = manager.add_message(session_id, "user", user_text)
    persist_session_message(store, session, user)
    assistant = manager.add_message(session_id, "assistant", assistant_text)
    persist_session_message(store, session, assistant)
    return await runtime.finalize_turn(
        manager,
        session_id=session_id,
        input_source="memory-storage-smoke",
        token_budget=8000,
        memory_debug={},
    )


def main() -> None:
    asyncio.run(run_storage_smoke())
    print("memory storage smoke ok")


if __name__ == "__main__":
    main()
