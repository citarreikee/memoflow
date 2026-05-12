from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import settings
from services.memory.formation.schemas import MemoryCandidateLite, MemoryObservation, MemoryWritePlan


def utc_now() -> str:
    return datetime.utcnow().isoformat()


class MemorySQLiteStore:
    def __init__(self, base_dir: str, db_path: str | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.base_dir / "memoflow_memory.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_settings(cls) -> "MemorySQLiteStore":
        return cls(settings.MEMORY_DATA_DIR, db_path=settings.MEMORY_STORAGE_DB_PATH or None)

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
                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    action TEXT NOT NULL,
                    importance REAL NOT NULL,
                    stability TEXT NOT NULL,
                    text TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    extractor_mode TEXT NOT NULL,
                    extractor_model TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_candidates_session_episode
                ON memory_candidates(session_id, episode_id);

                CREATE TABLE IF NOT EXISTS memory_observations (
                    observation_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    scope_hint TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    negative INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    extractor_mode TEXT NOT NULL,
                    extractor_model TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_observations_session_episode
                ON memory_observations(session_id, episode_id);

                CREATE TABLE IF NOT EXISTS memory_pipeline_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    episode_id TEXT,
                    job_type TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    stage_index INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    notes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_pipeline_artifacts_lookup
                ON memory_pipeline_artifacts(session_id, episode_id, job_type, stage_index);

                CREATE TABLE IF NOT EXISTS memory_write_plans (
                    plan_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    canonical_store TEXT,
                    scope TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    blocked_reasons_json TEXT NOT NULL,
                    projections_json TEXT NOT NULL,
                    evidence_episode_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_write_plans_session_status
                ON memory_write_plans(session_id, status);

                CREATE TABLE IF NOT EXISTS memory_records (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    version INTEGER NOT NULL,
                    source_plan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    supersedes_memory_id TEXT,
                    superseded_by_memory_id TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_records_lookup
                ON memory_records(scope, namespace, type, key, status);

                CREATE TABLE IF NOT EXISTS memory_evidence_links (
                    link_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    evidence_role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory
                ON memory_evidence_links(memory_id);

                CREATE TABLE IF NOT EXISTS memory_vector_projections (
                    projection_id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    episode_id TEXT,
                    source_plan_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding_model TEXT,
                    embedding_status TEXT NOT NULL,
                    embedding_ref TEXT,
                    source_of_truth INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_vector_status
                ON memory_vector_projections(embedding_status);

                CREATE TABLE IF NOT EXISTS memory_graph_edges (
                    edge_id TEXT PRIMARY KEY,
                    source_memory_id TEXT,
                    target_memory_id TEXT,
                    source_episode_id TEXT,
                    target_episode_id TEXT,
                    relation_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    source_plan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_graph_relation
                ON memory_graph_edges(scope, relation_type, status);

                CREATE TABLE IF NOT EXISTS memory_file_suggestions (
                    suggestion_id TEXT PRIMARY KEY,
                    source_plan_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    workspace_dir TEXT,
                    target_path TEXT,
                    suggested_patch TEXT NOT NULL,
                    status TEXT NOT NULL,
                    auto_apply INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_file_suggestions_status
                ON memory_file_suggestions(session_id, status);

                CREATE TABLE IF NOT EXISTS memory_review_items (
                    review_id TEXT PRIMARY KEY,
                    source_plan_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_memory_id TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_review_items_status
                ON memory_review_items(session_id, status, action);

                CREATE TABLE IF NOT EXISTS memory_reindex_jobs (
                    job_id TEXT PRIMARY KEY,
                    memory_id TEXT,
                    projection_id TEXT,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_reindex_jobs_status
                ON memory_reindex_jobs(status, job_type);
                """
            )

    def persist_pipeline_artifact(
        self,
        *,
        session_id: str,
        episode_id: Optional[str],
        job_type: str,
        contract_version: str,
        stage_index: int,
        stage: Dict[str, Any],
    ) -> str:
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        input_payload = stage.get("inputs") if isinstance(stage.get("inputs"), dict) else {}
        output_payload = stage.get("outputs") if isinstance(stage.get("outputs"), dict) else {}
        notes_payload = stage.get("notes") if isinstance(stage.get("notes"), list) else []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_pipeline_artifacts (
                    artifact_id, session_id, episode_id, job_type, contract_version, stage_name,
                    stage_index, status, mode, input_json, output_json, notes_json, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    session_id,
                    episode_id,
                    job_type,
                    contract_version,
                    str(stage.get("name") or ""),
                    stage_index,
                    str(stage.get("status") or "unknown"),
                    str(stage.get("mode") or "") or None,
                    json.dumps(input_payload, ensure_ascii=False),
                    json.dumps(output_payload, ensure_ascii=False),
                    json.dumps(notes_payload, ensure_ascii=False),
                    utc_now(),
                    json.dumps(stage, ensure_ascii=False),
                ),
            )
        return artifact_id

    def persist_pipeline_artifacts(
        self,
        *,
        session_id: str,
        episode_id: Optional[str],
        job_type: str,
        contract_version: str,
        stages: List[Dict[str, Any]],
    ) -> List[str]:
        artifact_ids: List[str] = []
        for index, stage in enumerate(stages):
            artifact_ids.append(
                self.persist_pipeline_artifact(
                    session_id=session_id,
                    episode_id=episode_id,
                    job_type=job_type,
                    contract_version=contract_version,
                    stage_index=index,
                    stage=stage,
                )
            )
        return artifact_ids

    def list_pipeline_artifacts(
        self,
        *,
        session_id: str,
        episode_id: Optional[str] = None,
        job_type: Optional[str] = None,
        hydrate: bool = False,
    ) -> List[Dict[str, Any]]:
        clauses = ["session_id = ?"]
        params: List[Any] = [session_id]
        if episode_id is not None:
            clauses.append("episode_id = ?")
            params.append(episode_id)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_pipeline_artifacts
                WHERE {' AND '.join(clauses)}
                ORDER BY stage_index ASC, created_at ASC
                """,
                params,
            ).fetchall()
            artifacts = [_row_to_dict(row) for row in rows]
            return [_hydrate_pipeline_artifact(artifact) for artifact in artifacts] if hydrate else artifacts

    def get_pipeline_artifact(
        self,
        *,
        session_id: str,
        stage_name: str,
        episode_id: Optional[str] = None,
        job_type: Optional[str] = None,
        contract_version: Optional[str] = None,
        hydrate: bool = True,
    ) -> Optional[Dict[str, Any]]:
        clauses = ["session_id = ?", "stage_name = ?"]
        params: List[Any] = [session_id, stage_name]
        if episode_id is not None:
            clauses.append("episode_id = ?")
            params.append(episode_id)
        if job_type is not None:
            clauses.append("job_type = ?")
            params.append(job_type)
        if contract_version is not None:
            clauses.append("contract_version = ?")
            params.append(contract_version)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM memory_pipeline_artifacts
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, stage_index DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            artifact = _row_to_dict(row)
            return _hydrate_pipeline_artifact(artifact) if hydrate else artifact

    def get_pipeline_stage_output(
        self,
        *,
        session_id: str,
        stage_name: str,
        episode_id: Optional[str] = None,
        job_type: Optional[str] = None,
        contract_version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        artifact = self.get_pipeline_artifact(
            session_id=session_id,
            episode_id=episode_id,
            job_type=job_type,
            stage_name=stage_name,
            contract_version=contract_version,
            hydrate=True,
        )
        if artifact is None:
            return None
        output_payload = artifact.get("output")
        return output_payload if isinstance(output_payload, dict) else {}

    def persist_observation(
        self,
        *,
        session_id: str,
        observation: MemoryObservation,
        extractor_mode: str,
        extractor_model: Optional[str],
        status: str = "extracted",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_observations (
                    observation_id, session_id, episode_id, type, scope_hint, confidence,
                    negative, text, reason, extractor_mode, extractor_model, status, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    session_id,
                    observation.episode_id,
                    observation.type,
                    observation.scope_hint,
                    observation.confidence,
                    1 if observation.negative else 0,
                    observation.text,
                    observation.reason,
                    extractor_mode,
                    extractor_model,
                    status,
                    utc_now(),
                    json.dumps(observation.to_dict(), ensure_ascii=False),
                ),
            )

    def persist_observations(
        self,
        *,
        session_id: str,
        observations: List[MemoryObservation],
        extractor_mode: str,
        extractor_model: Optional[str],
        status: str = "extracted",
    ) -> None:
        for observation in observations:
            self.persist_observation(
                session_id=session_id,
                observation=observation,
                extractor_mode=extractor_mode,
                extractor_model=extractor_model,
                status=status,
            )

    def persist_candidate(
        self,
        *,
        session_id: str,
        episode_id: str,
        candidate: MemoryCandidateLite,
        extractor_mode: str,
        extractor_model: Optional[str],
        status: str = "extracted",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_candidates (
                    candidate_id, session_id, episode_id, type, scope, action, importance,
                    stability, text, reason, extractor_mode, extractor_model, status, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    session_id,
                    episode_id,
                    candidate.type,
                    candidate.scope,
                    candidate.action,
                    candidate.importance,
                    candidate.stability,
                    candidate.text,
                    candidate.reason,
                    extractor_mode,
                    extractor_model,
                    status,
                    utc_now(),
                    json.dumps(candidate.to_dict(), ensure_ascii=False),
                ),
            )

    def persist_write_plan(self, *, session_id: str, plan: MemoryWritePlan) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_write_plans (
                    plan_id, candidate_id, session_id, action, canonical_store, scope, confidence, status,
                    blocked_reasons_json, projections_json, evidence_episode_ids_json, created_at, applied_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.plan_id,
                    plan.candidate_id,
                    session_id,
                    plan.action,
                    plan.canonical_store,
                    plan.scope,
                    plan.confidence,
                    plan.status,
                    json.dumps(plan.blocked_reasons, ensure_ascii=False),
                    json.dumps(plan.projections, ensure_ascii=False),
                    json.dumps(plan.evidence_episode_ids, ensure_ascii=False),
                    utc_now(),
                    None,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                ),
            )

    def find_active_record(self, *, scope: str, namespace: str, memory_type: str, key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_records
                WHERE scope = ? AND namespace = ? AND type = ? AND key = ? AND status = 'active'
                ORDER BY version DESC LIMIT 1
                """,
                (scope, namespace, memory_type, key),
            ).fetchone()
            return _row_to_dict(row) if row else None

    def get_record(self, *, memory_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_records WHERE memory_id = ?", (memory_id,)).fetchone()
            return _row_to_dict(row) if row else None

    def get_record_by_source_plan(self, *, source_plan_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_records
                WHERE source_plan_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (source_plan_id,),
            ).fetchone()
            return _row_to_dict(row) if row else None

    def has_evidence_link(self, *, memory_id: str, episode_id: str, plan_id: str, evidence_role: str = "source") -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM memory_evidence_links
                WHERE memory_id = ? AND episode_id = ? AND plan_id = ? AND evidence_role = ?
                LIMIT 1
                """,
                (memory_id, episode_id, plan_id, evidence_role),
            ).fetchone()
            return row is not None

    def has_vector_projection(self, *, memory_id: Optional[str], episode_id: Optional[str], source_plan_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM memory_vector_projections
                WHERE memory_id IS ? AND episode_id IS ? AND source_plan_id = ?
                LIMIT 1
                """,
                (memory_id, episode_id, source_plan_id),
            ).fetchone()
            return row is not None

    def has_graph_edge(
        self,
        *,
        memory_id: Optional[str],
        episode_id: Optional[str],
        relation_type: str,
        source_plan_id: str,
        target_memory_id: Optional[str] = None,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM memory_graph_edges
                WHERE source_memory_id IS ?
                  AND target_memory_id IS ?
                  AND source_episode_id IS ?
                  AND relation_type = ?
                  AND source_plan_id = ?
                  AND status = 'active'
                LIMIT 1
                """,
                (memory_id, target_memory_id, episode_id, relation_type, source_plan_id),
            ).fetchone()
            return row is not None

    def has_review_item(self, *, source_plan_id: str, status: str = "pending_review") -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM memory_review_items
                WHERE source_plan_id = ? AND status = ?
                LIMIT 1
                """,
                (source_plan_id, status),
            ).fetchone()
            return row is not None

    def search_active_records(
        self,
        *,
        query: str,
        namespace: str,
        scopes: List[str],
        memory_types: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        terms = _query_terms(query)
        clauses = ["status = 'active'"]
        params: List[Any] = []
        if scopes:
            clauses.append(f"scope IN ({','.join(['?'] * len(scopes))})")
            params.extend(scopes)
        if namespace:
            clauses.append("(namespace = ? OR scope IN ('user', 'workspace'))")
            params.append(namespace)
        if memory_types:
            clauses.append(f"type IN ({','.join(['?'] * len(memory_types))})")
            params.extend(memory_types)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(LOWER(key) LIKE ? OR LOWER(value) LIKE ?)")
                pattern = f"%{term}%"
                params.extend([pattern, pattern])
            clauses.append(f"({' OR '.join(term_clauses)})")
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_records
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    def search_vector_projections(
        self,
        *,
        query: str,
        namespace: str,
        scopes: List[str],
        memory_types: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        terms = _query_terms(query)
        clauses = ["(p.embedding_status = 'pending' OR p.embedding_status = 'active')"]
        params: List[Any] = []
        if scopes:
            clauses.append("(r.scope IS NULL OR r.scope IN (" + ",".join(["?"] * len(scopes)) + "))")
            params.extend(scopes)
        if namespace:
            clauses.append("(r.namespace IS NULL OR r.namespace = ? OR r.scope IN ('user', 'workspace'))")
            params.append(namespace)
        if memory_types:
            clauses.append("(r.type IS NULL OR r.type IN (" + ",".join(["?"] * len(memory_types)) + "))")
            params.extend(memory_types)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(LOWER(p.text) LIKE ? OR LOWER(COALESCE(r.value, '')) LIKE ?)")
                pattern = f"%{term}%"
                params.extend([pattern, pattern])
            clauses.append(f"({' OR '.join(term_clauses)})")
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    p.*,
                    r.scope AS record_scope,
                    r.type AS record_type,
                    r.value AS record_value
                FROM memory_vector_projections p
                LEFT JOIN memory_records r ON r.memory_id = p.memory_id
                WHERE {' AND '.join(clauses)}
                ORDER BY p.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    def search_graph_edges(self, *, query: str, scopes: List[str], limit: int) -> List[Dict[str, Any]]:
        terms = _query_terms(query)
        clauses = ["status = 'active'"]
        params: List[Any] = []
        if scopes:
            clauses.append(f"scope IN ({','.join(['?'] * len(scopes))})")
            params.extend(scopes)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(LOWER(relation_type) LIKE ? OR LOWER(payload_json) LIKE ?)")
                pattern = f"%{term}%"
                params.extend([pattern, pattern])
            clauses.append(f"({' OR '.join(term_clauses)})")
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_graph_edges
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    def search_review_items(
        self,
        *,
        query: str,
        session_id: str,
        scopes: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        terms = _query_terms(query)
        clauses = ["status = 'pending_review'"]
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if scopes:
            clauses.append(f"scope IN ({','.join(['?'] * len(scopes))})")
            params.extend(scopes)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(LOWER(action) LIKE ? OR LOWER(reason) LIKE ? OR LOWER(payload_json) LIKE ?)")
                pattern = f"%{term}%"
                params.extend([pattern, pattern, pattern])
            clauses.append(f"({' OR '.join(term_clauses)})")
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_review_items
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [_row_to_dict(row) for row in rows]

    def insert_memory_record(
        self,
        *,
        scope: str,
        namespace: str,
        memory_type: str,
        key: str,
        value: str,
        status: str,
        confidence: float,
        version: int,
        source_plan_id: str,
        supersedes_memory_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        memory_id = f"mem_{uuid.uuid4().hex}"
        now = utc_now()
        record = {
            "memory_id": memory_id,
            "scope": scope,
            "namespace": namespace,
            "type": memory_type,
            "key": key,
            "value": value,
            "status": status,
            "confidence": confidence,
            "version": version,
            "source_plan_id": source_plan_id,
            "created_at": now,
            "updated_at": now,
            "valid_from": None,
            "valid_until": None,
            "supersedes_memory_id": supersedes_memory_id,
            "superseded_by_memory_id": None,
            "payload_json": json.dumps(payload or {}, ensure_ascii=False),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_records (
                    memory_id, scope, namespace, type, key, value, status, confidence, version,
                    source_plan_id, created_at, updated_at, valid_from, valid_until,
                    supersedes_memory_id, superseded_by_memory_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(record.values()),
            )
        return record

    def mark_record_status(self, *, memory_id: str, status: str, superseded_by_memory_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE memory_records
                SET status = ?, updated_at = ?, superseded_by_memory_id = COALESCE(?, superseded_by_memory_id)
                WHERE memory_id = ?
                """,
                (status, utc_now(), superseded_by_memory_id, memory_id),
            )

    def mark_plan_applied(self, *, plan_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_write_plans SET status = 'applied', applied_at = ? WHERE plan_id = ?",
                (utc_now(), plan_id),
            )

    def insert_evidence_link(self, *, memory_id: str, episode_id: str, plan_id: str, evidence_role: str = "source") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_evidence_links (link_id, memory_id, episode_id, plan_id, evidence_role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"link_{uuid.uuid4().hex}", memory_id, episode_id, plan_id, evidence_role, utc_now()),
            )

    def insert_vector_projection(self, *, memory_id: Optional[str], episode_id: Optional[str], plan: MemoryWritePlan) -> str:
        projection_id = f"vproj_{uuid.uuid4().hex}"
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_vector_projections (
                    projection_id, memory_id, episode_id, source_plan_id, scope, text, embedding_model,
                    embedding_status, embedding_ref, source_of_truth, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (projection_id, memory_id, episode_id, plan.plan_id, plan.scope, plan.text, None, "pending", None, 0, now, now),
            )
        return projection_id

    def insert_graph_edge(
        self,
        *,
        memory_id: Optional[str],
        episode_id: Optional[str],
        relation_type: str,
        plan: MemoryWritePlan,
        target_memory_id: Optional[str] = None,
    ) -> str:
        edge_id = f"edge_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_graph_edges (
                    edge_id, source_memory_id, target_memory_id, source_episode_id, target_episode_id,
                    relation_type, scope, confidence, status, source_plan_id, created_at, valid_from, valid_until, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge_id,
                    memory_id,
                    target_memory_id,
                    episode_id,
                    None,
                    relation_type,
                    plan.scope,
                    plan.confidence,
                    "active",
                    plan.plan_id,
                    utc_now(),
                    None,
                    None,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                ),
            )
        return edge_id

    def insert_file_suggestion(
        self,
        *,
        session_id: str,
        workspace_dir: Optional[str],
        plan: MemoryWritePlan,
        target_path: Optional[str] = None,
    ) -> str:
        suggestion_id = f"fsug_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_file_suggestions (
                    suggestion_id, source_plan_id, session_id, workspace_dir, target_path, suggested_patch,
                    status, auto_apply, created_at, reviewed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    plan.plan_id,
                    session_id,
                    workspace_dir,
                    target_path,
                    f"- {plan.text}",
                    "pending_review",
                    0,
                    utc_now(),
                    None,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                ),
            )
        return suggestion_id

    def insert_review_item(
        self,
        *,
        session_id: str,
        plan: MemoryWritePlan,
        reason: str,
        status: str = "pending_review",
    ) -> str:
        review_id = f"rev_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_review_items (
                    review_id, source_plan_id, session_id, scope, action, status, target_memory_id,
                    reason, created_at, reviewed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    plan.plan_id,
                    session_id,
                    plan.scope,
                    plan.action,
                    status,
                    plan.target_memory_id,
                    reason,
                    utc_now(),
                    None,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                ),
            )
        return review_id

    def insert_reindex_job(
        self,
        *,
        job_type: str,
        reason: str,
        memory_id: Optional[str] = None,
        projection_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        job_id = f"ridx_{uuid.uuid4().hex}"
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_reindex_jobs (
                    job_id, memory_id, projection_id, job_type, status, reason, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    memory_id,
                    projection_id,
                    job_type,
                    "pending",
                    reason,
                    now,
                    now,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
        return job_id

    def count_rows(self, table: str) -> int:
        if table not in {
            "memory_observations",
            "memory_pipeline_artifacts",
            "memory_candidates",
            "memory_write_plans",
            "memory_records",
            "memory_evidence_links",
            "memory_vector_projections",
            "memory_graph_edges",
            "memory_file_suggestions",
            "memory_review_items",
            "memory_reindex_jobs",
        }:
            raise ValueError(f"unsupported_table:{table}")
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            return int(row["count"] if row else 0)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _hydrate_pipeline_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    hydrated = dict(artifact)
    hydrated["input"] = _loads_json_dict(hydrated.get("input_json"))
    hydrated["output"] = _loads_json_dict(hydrated.get("output_json"))
    hydrated["notes"] = _loads_json_list(hydrated.get("notes_json"))
    payload = _loads_json_dict(hydrated.get("payload_json"))
    if payload:
        hydrated["payload"] = payload
    return hydrated


def _loads_json_dict(value: Any) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _loads_json_list(value: Any) -> List[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _query_terms(query: str) -> List[str]:
    raw_terms = [term.strip().lower() for term in (query or "").replace("，", " ").replace("。", " ").split()]
    terms: List[str] = []
    for term in raw_terms:
        cleaned = term.strip("`'\".,:;!?()[]{}<>/\\")
        if len(cleaned) < 2:
            continue
        if cleaned in terms:
            continue
        terms.append(cleaned)
    return terms[:8]
