from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.memory.schemas import Episode, MemoryAtom, ShortTermState, utc_now_iso


class SQLiteMemoryStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.is_absolute():
            self.db_path = Path.cwd() / self.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  session_key TEXT,
                  harness TEXT NOT NULL,
                  source TEXT NOT NULL,
                  user_message TEXT NOT NULL,
                  assistant_answer TEXT,
                  tool_trace_json TEXT,
                  metadata_json TEXT,
                  created_at TEXT NOT NULL,
                  completed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS short_term_states (
                  session_id TEXT PRIMARY KEY,
                  harness TEXT NOT NULL,
                  recent_turn_ids_json TEXT NOT NULL,
                  conversation_summary TEXT,
                  task_state_summary TEXT,
                  open_issues_summary TEXT,
                  covered_message_ids_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_atoms (
                  id TEXT PRIMARY KEY,
                  type TEXT NOT NULL,
                  scope_type TEXT NOT NULL,
                  scope_id TEXT NOT NULL,
                  content TEXT NOT NULL,
                  normalized_content TEXT NOT NULL,
                  evidence_episode_ids_json TEXT NOT NULL,
                  entities_json TEXT,
                  keywords_json TEXT,
                  confidence REAL NOT NULL,
                  importance REAL NOT NULL,
                  status TEXT NOT NULL,
                  hash TEXT NOT NULL,
                  embedding_json TEXT,
                  metadata_json TEXT,
                  created_at TEXT NOT NULL,
                  observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS retrieval_events (
                  id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  query TEXT NOT NULL,
                  harness TEXT NOT NULL,
                  retrieved_atom_ids_json TEXT NOT NULL,
                  scores_json TEXT NOT NULL,
                  assembled_context_preview TEXT,
                  created_at TEXT NOT NULL
                );
                """
            )

    def save_episode(self, episode: Episode) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes (
                  id, session_id, session_key, harness, source, user_message,
                  assistant_answer, tool_trace_json, metadata_json, created_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.id,
                    episode.session_id,
                    episode.session_key,
                    episode.harness,
                    episode.source,
                    episode.user_message,
                    episode.assistant_answer,
                    json.dumps(episode.tool_trace, ensure_ascii=False),
                    json.dumps(episode.metadata, ensure_ascii=False),
                    episode.created_at,
                    episode.completed_at,
                ),
            )

    def list_episodes(self, session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM episodes WHERE session_id = ? ORDER BY completed_at DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM episodes ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._episode_row_to_dict(row) for row in rows]

    def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        return self._episode_row_to_dict(row) if row else None

    def get_short_term_state(self, session_id: str) -> Optional[ShortTermState]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM short_term_states WHERE session_id = ?", (session_id,)).fetchone()
        if not row:
            return None
        return ShortTermState(
            session_id=row["session_id"],
            harness=row["harness"],
            recent_turn_ids=json.loads(row["recent_turn_ids_json"] or "[]"),
            conversation_summary=row["conversation_summary"] or "",
            task_state_summary=row["task_state_summary"] or "",
            open_issues_summary=row["open_issues_summary"] or "",
            covered_message_ids=json.loads(row["covered_message_ids_json"] or "[]"),
            updated_at=row["updated_at"],
        )

    def save_short_term_state(self, state: ShortTermState) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO short_term_states (
                  session_id, harness, recent_turn_ids_json, conversation_summary,
                  task_state_summary, open_issues_summary, covered_message_ids_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.session_id,
                    state.harness,
                    json.dumps(state.recent_turn_ids, ensure_ascii=False),
                    state.conversation_summary,
                    state.task_state_summary,
                    state.open_issues_summary,
                    json.dumps(state.covered_message_ids, ensure_ascii=False),
                    state.updated_at,
                ),
            )

    def list_short_term_states(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM short_term_states ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        states = []
        for row in rows:
            state = self.get_short_term_state(row["session_id"])
            if state:
                states.append(state.to_dict())
        return states

    def save_memory_atom(self, atom: MemoryAtom) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_atoms (
                  id, type, scope_type, scope_id, content, normalized_content,
                  evidence_episode_ids_json, entities_json, keywords_json, confidence,
                  importance, status, hash, embedding_json, metadata_json, created_at, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    atom.id,
                    atom.type,
                    atom.scope_type,
                    atom.scope_id,
                    atom.content,
                    atom.normalized_content,
                    json.dumps(atom.evidence_episode_ids, ensure_ascii=False),
                    json.dumps(atom.entities, ensure_ascii=False),
                    json.dumps(atom.keywords, ensure_ascii=False),
                    atom.confidence,
                    atom.importance,
                    atom.status,
                    atom.hash,
                    json.dumps(atom.embedding, ensure_ascii=False) if atom.embedding is not None else None,
                    json.dumps(atom.metadata, ensure_ascii=False),
                    atom.created_at,
                    atom.observed_at,
                ),
            )

    def list_memory_atoms(self, scope_type: Optional[str] = None, scope_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 500))
        query = "SELECT * FROM memory_atoms"
        params: List[Any] = []
        clauses = []
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._atom_row_to_dict(row) for row in rows]

    def get_memory_atom(self, atom_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_atoms WHERE id = ?", (atom_id,)).fetchone()
        return self._atom_row_to_dict(row) if row else None

    def get_memory_atom_by_hash(self, memory_hash: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_atoms WHERE hash = ?", (memory_hash,)).fetchone()
        return self._atom_row_to_dict(row) if row else None

    def _episode_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "session_key": row["session_key"],
            "harness": row["harness"],
            "source": row["source"],
            "user_message": row["user_message"],
            "assistant_answer": row["assistant_answer"] or "",
            "tool_trace": json.loads(row["tool_trace_json"] or "[]"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def _atom_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "type": row["type"],
            "scope_type": row["scope_type"],
            "scope_id": row["scope_id"],
            "content": row["content"],
            "normalized_content": row["normalized_content"],
            "evidence_episode_ids": json.loads(row["evidence_episode_ids_json"] or "[]"),
            "entities": json.loads(row["entities_json"] or "[]"),
            "keywords": json.loads(row["keywords_json"] or "[]"),
            "confidence": row["confidence"],
            "importance": row["importance"],
            "status": row["status"],
            "hash": row["hash"],
            "embedding": json.loads(row["embedding_json"]) if row["embedding_json"] else None,
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "observed_at": row["observed_at"],
        }
