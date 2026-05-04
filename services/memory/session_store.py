from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from chat_history import Message, SessionManager
from config import settings


class SessionStore:
    """Persistent store for sessions, messages, episodes, and checkpoints."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "memoflow_sessions.sqlite3"
        self._initialize()

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
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    thinking TEXT,
                    tool_calls_json TEXT,
                    tool_call_id TEXT,
                    name TEXT,
                    reasoning_content TEXT,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session_timestamp
                ON messages(session_id, timestamp);

                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    start_message_id TEXT NOT NULL,
                    end_message_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    token_estimate INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_episodes_session_turn
                ON episodes(session_id, turn_index);

                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    token_estimate INTEGER NOT NULL,
                    covered_episode_ids_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_checkpoints_session_created
                ON checkpoints(session_id, created_at);
                """
            )

    def upsert_session(self, session: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, model, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    model = excluded.model,
                    updated_at = excluded.updated_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    session.session_id,
                    session.model,
                    session.created_at,
                    session.updated_at,
                    json.dumps(session.metadata or {}, ensure_ascii=False),
                ),
            )

    def append_message(self, session: Any, message: Message) -> None:
        self.upsert_session(session)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages (
                    message_id, session_id, role, content, thinking, tool_calls_json,
                    tool_call_id, name, reasoning_content, timestamp, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.message_id,
                    session.session_id,
                    message.role,
                    message.content,
                    message.thinking,
                    json.dumps(message.tool_calls, ensure_ascii=False) if message.tool_calls is not None else None,
                    message.tool_call_id,
                    message.name,
                    message.reasoning_content,
                    message.timestamp,
                    json.dumps(message.metadata or {}, ensure_ascii=False),
                ),
            )

    def load_sessions_into(self, session_manager: SessionManager) -> None:
        with self._connect() as conn:
            session_rows = conn.execute("SELECT * FROM sessions ORDER BY created_at ASC").fetchall()
            for session_row in session_rows:
                message_rows = conn.execute(
                    "SELECT * FROM messages WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_row["session_id"],),
                ).fetchall()
                messages = [
                    Message(
                        message_id=row["message_id"],
                        role=row["role"],
                        content=row["content"],
                        thinking=row["thinking"],
                        tool_calls=json.loads(row["tool_calls_json"]) if row["tool_calls_json"] else None,
                        tool_call_id=row["tool_call_id"],
                        name=row["name"],
                        reasoning_content=row["reasoning_content"],
                        timestamp=row["timestamp"],
                        metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    )
                    for row in message_rows
                ]
                session_manager.restore_session(
                    session_id=session_row["session_id"],
                    model=session_row["model"],
                    created_at=session_row["created_at"],
                    updated_at=session_row["updated_at"],
                    metadata=json.loads(session_row["metadata_json"]) if session_row["metadata_json"] else {},
                    messages=messages,
                )

    def get_episode_count(self, session_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM episodes WHERE session_id = ?", (session_id,)).fetchone()
            return int(row["count"]) if row else 0

    def session_exists(self, session_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM sessions WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
            return row is not None

    def insert_episode(
        self,
        *,
        episode_id: str,
        session_id: str,
        start_message_id: str,
        end_message_id: str,
        turn_index: int,
        token_estimate: int,
        source: str,
        created_at: str,
        payload: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes (
                    episode_id, session_id, start_message_id, end_message_id, turn_index,
                    token_estimate, source, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode_id,
                    session_id,
                    start_message_id,
                    end_message_id,
                    turn_index,
                    token_estimate,
                    source,
                    created_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def list_episodes(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE session_id = ? ORDER BY turn_index ASC",
                (session_id,),
            ).fetchall()
            result: List[Dict[str, Any]] = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["episode_id"] = row["episode_id"]
                payload["turn_index"] = row["turn_index"]
                payload["created_at"] = row["created_at"]
                payload["token_estimate"] = row["token_estimate"]
                result.append(payload)
            return result

    def save_checkpoint(
        self,
        *,
        checkpoint_id: str,
        session_id: str,
        created_at: str,
        token_estimate: int,
        covered_episode_ids: List[str],
        summary: Dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints (
                    checkpoint_id, session_id, created_at, token_estimate,
                    covered_episode_ids_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    session_id,
                    created_at,
                    token_estimate,
                    json.dumps(covered_episode_ids, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False),
                ),
            )

    def get_latest_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "checkpoint_id": row["checkpoint_id"],
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "token_estimate": row["token_estimate"],
                "covered_episode_ids": json.loads(row["covered_episode_ids_json"]),
                "summary": json.loads(row["summary_json"]),
            }

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "checkpoint_id": row["checkpoint_id"],
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                "token_estimate": row["token_estimate"],
                "covered_episode_ids": json.loads(row["covered_episode_ids_json"]),
                "summary": json.loads(row["summary_json"]),
            }

    def delete_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM episodes WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


session_store = SessionStore(settings.MEMORY_DATA_DIR)
