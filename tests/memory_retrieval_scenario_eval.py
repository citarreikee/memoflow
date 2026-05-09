from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.formation.schemas import MemoryWritePlan
from services.memory.retrieval.pipeline import MemoryRetrievalPipeline
from services.memory.storage.sqlite_store import MemorySQLiteStore


FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "memory_retrieval_scenarios.json"


@dataclass
class SettingsPatch:
    values: Dict[str, Any]
    previous: Dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> "SettingsPatch":
        for key, value in self.values.items():
            self.previous[key] = getattr(settings, key)
            setattr(settings, key, value)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)


@dataclass
class RetrievalScenarioResult:
    scenario_id: str
    passed: bool
    errors: List[str] = field(default_factory=list)
    intent_kind: str = "none"
    item_count: int = 0
    sources: List[str] = field(default_factory=list)
    memory_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "errors": self.errors,
            "intent_kind": self.intent_kind,
            "item_count": self.item_count,
            "sources": self.sources,
            "memory_types": self.memory_types,
        }


def load_scenarios() -> List[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))


def evaluate_scenario(scenario: Dict[str, Any]) -> RetrievalScenarioResult:
    with tempfile.TemporaryDirectory() as tmp, SettingsPatch(
        {
            "MEMORY_DATA_DIR": tmp,
            "MEMORY_STORAGE_DB_PATH": str(Path(tmp) / "memory.sqlite3"),
            "MEMORY_STORAGE_ENABLED": True,
            "MEMORY_RETRIEVAL_ENABLED": True,
        }
    ):
        store = MemorySQLiteStore(tmp, db_path=settings.MEMORY_STORAGE_DB_PATH)
        _seed_records(store, scenario.get("seed_records") or [])
        _seed_graph_edges(store, scenario.get("seed_graph_edges") or [])
        pack = MemoryRetrievalPipeline().run(
            user_message=str(scenario.get("query") or ""),
            session_key=str(scenario.get("session_key") or ""),
            token_budget=int(scenario.get("token_budget") or 8000),
            store=store,
        )
    expected = scenario.get("expected") or {}
    errors: List[str] = []
    items_text = "\n".join(item.text for item in pack.items).lower()
    sources = [item.source for item in pack.items]
    memory_types = [item.memory_type for item in pack.items]

    expected_intent = expected.get("intent_kind")
    if expected_intent and pack.intent.kind != expected_intent:
        errors.append(f"intent_kind expected {expected_intent!r}, got {pack.intent.kind!r}")

    min_items = int(expected.get("min_items", 0))
    max_items = expected.get("max_items")
    if len(pack.items) < min_items:
        errors.append(f"item_count expected >= {min_items}, got {len(pack.items)}")
    if max_items is not None and len(pack.items) > int(max_items):
        errors.append(f"item_count expected <= {max_items}, got {len(pack.items)}")

    for fragment in expected.get("must_include_text") or []:
        if str(fragment).lower() not in items_text:
            errors.append(f"missing text fragment {fragment!r}")
    for fragment in expected.get("must_not_include_text") or []:
        if str(fragment).lower() in items_text:
            errors.append(f"forbidden text fragment present {fragment!r}")
    for memory_type in expected.get("include_memory_types") or []:
        if memory_type not in memory_types:
            errors.append(f"missing memory type {memory_type!r}; got {memory_types!r}")
    for source in expected.get("include_sources") or []:
        if source not in sources:
            errors.append(f"missing source {source!r}; got {sources!r}")

    return RetrievalScenarioResult(
        scenario_id=str(scenario.get("id") or ""),
        passed=not errors,
        errors=errors,
        intent_kind=pack.intent.kind,
        item_count=len(pack.items),
        sources=sources,
        memory_types=memory_types,
    )


def _seed_records(store: MemorySQLiteStore, records: List[Dict[str, Any]]) -> None:
    for index, record in enumerate(records):
        store.insert_memory_record(
            scope=str(record.get("scope") or "session"),
            namespace=str(record.get("namespace") or ""),
            memory_type=str(record.get("type") or "non_memory"),
            key=str(record.get("key") or f"seed:{index}"),
            value=str(record.get("value") or ""),
            status=str(record.get("status") or "active"),
            confidence=float(record.get("confidence", 0.8)),
            version=int(record.get("version", 1)),
            source_plan_id=str(record.get("source_plan_id") or f"seed_plan_{index}"),
            payload=record.get("payload") if isinstance(record.get("payload"), dict) else {},
        )


def _seed_graph_edges(store: MemorySQLiteStore, edges: List[Dict[str, Any]]) -> None:
    for index, edge in enumerate(edges):
        payload = edge.get("payload") if isinstance(edge.get("payload"), dict) else {}
        plan = MemoryWritePlan(
            plan_id=str(edge.get("source_plan_id") or f"seed_edge_plan_{index}"),
            candidate_id=f"seed_edge_candidate_{index}",
            action="ADD",
            canonical_store="relation_graph",
            projections=[],
            scope=str(edge.get("scope") or "session"),
            evidence_episode_ids=[str(edge.get("episode_id") or f"seed_edge_episode_{index}")],
            confidence=float(edge.get("confidence", 0.82)),
            status="planned",
            type="entity_relation",
            text=str(payload.get("text") or edge.get("relation_type") or ""),
            reason="retrieval scenario seed edge",
        )
        store.insert_graph_edge(
            memory_id=str(edge.get("source_memory_id") or "") or None,
            episode_id=plan.evidence_episode_ids[0],
            relation_type=str(edge.get("relation_type") or "derived_from"),
            plan=plan,
        )


def run_eval() -> Dict[str, Any]:
    scenarios = load_scenarios()
    results = [evaluate_scenario(scenario) for scenario in scenarios]
    failed = [result for result in results if not result.passed]
    return {
        "scenario_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "results": [result.to_dict() for result in results],
    }


def main() -> None:
    report = run_eval()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
