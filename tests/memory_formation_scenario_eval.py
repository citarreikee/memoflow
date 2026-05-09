from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from services.memory.formation.extractor import extract_candidates_rule_based, should_trigger_extraction


FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "memory_formation_scenarios.json"


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    errors: List[str] = field(default_factory=list)
    candidate_count: int = 0
    candidate_types: List[str] = field(default_factory=list)
    candidate_scopes: List[str] = field(default_factory=list)
    candidate_layers: List[str] = field(default_factory=list)
    candidate_storage_intents: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "errors": self.errors,
            "candidate_count": self.candidate_count,
            "candidate_types": self.candidate_types,
            "candidate_scopes": self.candidate_scopes,
            "candidate_layers": self.candidate_layers,
            "candidate_storage_intents": self.candidate_storage_intents,
        }


def load_scenarios() -> List[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))


def evaluate_scenario(scenario: Dict[str, Any]) -> ScenarioResult:
    episode = scenario["episode"]
    expected = scenario["expected"]
    scenario_id = str(scenario["id"])
    triggered = should_trigger_extraction(episode)
    candidates = extract_candidates_rule_based(episode) if triggered else []
    candidate_text = "\n".join(candidate.text for candidate in candidates).lower()
    candidate_types = [candidate.type for candidate in candidates]
    candidate_scopes = [candidate.scope for candidate in candidates]
    candidate_layers = [candidate.memory_layer for candidate in candidates]
    candidate_storage_intents = [candidate.storage_intent for candidate in candidates]
    errors: List[str] = []

    if triggered is not bool(expected.get("triggered")):
        errors.append(f"triggered expected {expected.get('triggered')!r}, got {triggered!r}")

    min_candidates = int(expected.get("min_candidates", 0))
    max_candidates = int(expected.get("max_candidates", 999))
    if len(candidates) < min_candidates:
        errors.append(f"candidate_count expected >= {min_candidates}, got {len(candidates)}")
    if len(candidates) > max_candidates:
        errors.append(f"candidate_count expected <= {max_candidates}, got {len(candidates)}")

    for memory_type in expected.get("include_types") or []:
        if memory_type not in candidate_types:
            errors.append(f"missing candidate type {memory_type!r}; got {candidate_types!r}")
    for scope in expected.get("include_scopes") or []:
        if scope not in candidate_scopes:
            errors.append(f"missing candidate scope {scope!r}; got {candidate_scopes!r}")
    for layer in expected.get("include_layers") or []:
        if layer not in candidate_layers:
            errors.append(f"missing candidate layer {layer!r}; got {candidate_layers!r}")
    for intent in expected.get("include_storage_intents") or []:
        if intent not in candidate_storage_intents:
            errors.append(f"missing storage intent {intent!r}; got {candidate_storage_intents!r}")
    for text in expected.get("must_include_text") or []:
        if str(text).lower() not in candidate_text:
            errors.append(f"missing text fragment {text!r}")
    for text in expected.get("must_not_include_text") or []:
        if str(text).lower() in candidate_text:
            errors.append(f"forbidden text fragment present {text!r}")

    return ScenarioResult(
        scenario_id=scenario_id,
        passed=not errors,
        errors=errors,
        candidate_count=len(candidates),
        candidate_types=candidate_types,
        candidate_scopes=candidate_scopes,
        candidate_layers=candidate_layers,
        candidate_storage_intents=candidate_storage_intents,
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
