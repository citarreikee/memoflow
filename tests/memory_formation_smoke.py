from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chat_history import SessionManager
from config import settings
from services.memory.formation.mutation_planner import build_write_plan
from services.memory.formation.extractor import parse_candidate_json, parse_candidate_payload
from services.memory.formation.schemas import MemoryCandidateLite
from services.memory.runtime import MemoryRuntime
from services.memory.session_store import SessionStore
from services.memory.stores.write_plan_log import WritePlanLog
from services.memory.stores.vector_projection import VectorProjectionMockStore
from services.memory.stores.file_projection import FileProjectionMockStore
from services.memory.stores.relation_graph import RelationGraphMockStore
from services.memory.transcript_store import persist_session_message


def run_policy_smoke() -> None:
    payload = parse_candidate_json('{"candidates":[{"text":"Use concise Chinese replies","type":"preference","scope":"user","action":"ADD","importance":0.8,"reason":"Future response style","stability":"stable"}]}')
    parsed = parse_candidate_payload(payload)
    assert len(parsed) == 1
    assert parsed[0].type == "preference"

    project_rule = MemoryCandidateLite(
        text="v0.2 should plan storage shape before real persistence.",
        type="project_rule",
        scope="project",
        action="ADD",
        importance=0.82,
        reason="Affects implementation order.",
        stability="stable",
        candidate_id="cand_project_rule",
    )
    plan = build_write_plan(project_rule, evidence_episode_ids=["ep_1"])
    assert plan.status == "planned"
    assert plan.canonical_store == "file_memory"
    assert "vector_projection" in plan.projections

    weak = MemoryCandidateLite(
        text="random one-off detail",
        type="embedding_hint",
        scope="session",
        action="ADD",
        importance=0.2,
        reason="",
    )
    weak_plan = build_write_plan(weak, evidence_episode_ids=["ep_1"])
    assert weak_plan.status == "noop"
    assert weak_plan.canonical_store is None

    relation = MemoryCandidateLite(
        text="Module A and Module B are related.",
        type="entity_relation",
        scope="project",
        action="ADD",
        importance=0.9,
        reason="No explicit relation type.",
        stability="stable",
    )
    relation_plan = build_write_plan(relation, evidence_episode_ids=["ep_1"])
    assert relation_plan.status == "blocked"
    assert "graph_relation_missing" in relation_plan.blocked_reasons

    explicit_relation = MemoryCandidateLite(
        text="v0.2 depends_on v0.1 runtime events.",
        type="entity_relation",
        scope="project",
        action="ADD",
        importance=0.9,
        reason="Explicit dependency relation.",
        stability="stable",
    )
    explicit_plan = build_write_plan(explicit_relation, evidence_episode_ids=["ep_1"])
    assert explicit_plan.status == "planned"
    assert explicit_plan.canonical_store == "relation_graph"


async def run_runtime_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous_enabled = settings.MEMORY_FORMATION_ENABLED
        previous_dry_run = settings.MEMORY_FORMATION_DRY_RUN
        previous_log_dir = settings.MEMORY_WRITE_PLAN_LOG_DIR
        previous_background = settings.MEMORY_FORMATION_BACKGROUND
        settings.MEMORY_FORMATION_ENABLED = True
        settings.MEMORY_FORMATION_DRY_RUN = True
        settings.MEMORY_WRITE_PLAN_LOG_DIR = tmp
        settings.MEMORY_FORMATION_BACKGROUND = False
        try:
            store = SessionStore(tmp)
            runtime = MemoryRuntime(store)
            manager = SessionManager()
            session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": "."})
            store.upsert_session(session)

            user = manager.add_message(
                session.session_id,
                "user",
                "决定：v0.2 阶段要先设计 storage shape，不要默认所有记忆都进 DAG。",
            )
            persist_session_message(store, session, user)
            assistant = manager.add_message(session.session_id, "assistant", "收到，会按这个设计实现。")
            persist_session_message(store, session, assistant)

            debug = await runtime.finalize_turn(
                manager,
                session_id=session.session_id,
                input_source="memory-formation-smoke",
                token_budget=8000,
                memory_debug={},
            )

            formation = debug.get("memory_formation") or {}
            assert formation.get("triggered") is True
            assert formation.get("candidate_count", 0) >= 1
            assert formation.get("planned_count", 0) + formation.get("needs_review_count", 0) >= 1
            records = WritePlanLog(tmp).read_all()
            assert records
            assert records[0]["session_id"] == session.session_id
            vector_records = VectorProjectionMockStore(tmp).read_all()
            file_records = FileProjectionMockStore(tmp).read_all()
            graph_records = RelationGraphMockStore(tmp).read_all()
            assert vector_records or file_records or graph_records
            for record in vector_records:
                assert record["source_of_truth"] is False
            for record in file_records:
                assert record["auto_apply"] is False
        finally:
            settings.MEMORY_FORMATION_ENABLED = previous_enabled
            settings.MEMORY_FORMATION_DRY_RUN = previous_dry_run
            settings.MEMORY_WRITE_PLAN_LOG_DIR = previous_log_dir
            settings.MEMORY_FORMATION_BACKGROUND = previous_background


async def run_background_schedule_smoke() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous_enabled = settings.MEMORY_FORMATION_ENABLED
        previous_dry_run = settings.MEMORY_FORMATION_DRY_RUN
        previous_log_dir = settings.MEMORY_WRITE_PLAN_LOG_DIR
        previous_background = settings.MEMORY_FORMATION_BACKGROUND
        settings.MEMORY_FORMATION_ENABLED = True
        settings.MEMORY_FORMATION_DRY_RUN = True
        settings.MEMORY_WRITE_PLAN_LOG_DIR = tmp
        settings.MEMORY_FORMATION_BACKGROUND = True
        try:
            store = SessionStore(tmp)
            runtime = MemoryRuntime(store)
            runtime.formation_jobs.log_dir = tmp
            manager = SessionManager()
            session = manager.create_session(model="deepseek-v4-pro", metadata={"workspace_dir": "."})
            store.upsert_session(session)
            user = manager.add_message(session.session_id, "user", "规则：v0.2 不要静默改 file memory。")
            persist_session_message(store, session, user)
            assistant = manager.add_message(session.session_id, "assistant", "确认。")
            persist_session_message(store, session, assistant)

            debug = await runtime.finalize_turn(
                manager,
                session_id=session.session_id,
                input_source="memory-formation-background-smoke",
                token_budget=8000,
                memory_debug={},
            )
            formation = debug.get("memory_formation") or {}
            assert formation.get("scheduled") is True
            assert "memory_formation_scheduled" in debug.get("events", [])
        finally:
            settings.MEMORY_FORMATION_ENABLED = previous_enabled
            settings.MEMORY_FORMATION_DRY_RUN = previous_dry_run
            settings.MEMORY_WRITE_PLAN_LOG_DIR = previous_log_dir
            settings.MEMORY_FORMATION_BACKGROUND = previous_background


def main() -> None:
    run_policy_smoke()
    asyncio.run(run_runtime_smoke())
    asyncio.run(run_background_schedule_smoke())
    print("memory formation smoke ok")


if __name__ == "__main__":
    main()
