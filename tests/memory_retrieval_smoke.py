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
from services.memory.runtime import MemoryRuntime, RuntimeInput
from services.memory.session_store import SessionStore
from services.memory.storage.sqlite_store import MemorySQLiteStore


async def run_retrieval_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous = {
            "storage_enabled": settings.MEMORY_STORAGE_ENABLED,
            "retrieval_enabled": settings.MEMORY_RETRIEVAL_ENABLED,
            "storage_db_path": settings.MEMORY_STORAGE_DB_PATH,
            "memory_data_dir": settings.MEMORY_DATA_DIR,
        }
        settings.MEMORY_STORAGE_ENABLED = True
        settings.MEMORY_RETRIEVAL_ENABLED = True
        settings.MEMORY_DATA_DIR = tmp
        settings.MEMORY_STORAGE_DB_PATH = str(Path(tmp) / "memory.sqlite3")
        try:
            session_store = SessionStore(tmp)
            runtime = MemoryRuntime(session_store)
            manager = SessionManager()
            session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": tmp})
            session_store.upsert_session(session)

            memory_store = MemorySQLiteStore(tmp, db_path=settings.MEMORY_STORAGE_DB_PATH)
            memory_store.insert_memory_record(
                scope="session",
                namespace=f"session:{session.session_id}",
                memory_type="preference",
                key="preference:concise-plans",
                value="User prefers concise implementation plans for backend memory work.",
                status="active",
                confidence=0.92,
                version=1,
                source_plan_id="plan_smoke",
                payload={"test": True},
            )

            package = await runtime.prepare_turn(
                RuntimeInput(
                    session_id=session.session_id,
                    session_key=f"session:{session.session_id}",
                    model="deepseek-v4-pro",
                    provider="deepseek",
                    input_source="memory-retrieval-smoke",
                    user_message="Remember my preference for backend memory work plans?",
                    workspace_dir=tmp,
                    history_messages=manager.get_history(session_id=session.session_id, limit=None) or [],
                    latest_checkpoint=None,
                    token_budget=8000,
                    context_policy={"model": "deepseek-v4-pro", "provider": "deepseek"},
                )
            )

            rendered = "\n".join(message.get("content", "") for message in package.messages)
            assert "Relevant memory retrieved for this turn" in rendered
            assert "concise implementation plans" in rendered
            assert package.debug["retrieval"]["items"]
        finally:
            settings.MEMORY_STORAGE_ENABLED = previous["storage_enabled"]
            settings.MEMORY_RETRIEVAL_ENABLED = previous["retrieval_enabled"]
            settings.MEMORY_STORAGE_DB_PATH = previous["storage_db_path"]
            settings.MEMORY_DATA_DIR = previous["memory_data_dir"]


def main() -> None:
    asyncio.run(run_retrieval_smoke())
    print("memory retrieval smoke ok")


if __name__ == "__main__":
    main()

