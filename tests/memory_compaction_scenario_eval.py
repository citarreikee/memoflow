from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings
from services.memory.compaction import build_checkpoint_summary


FIXTURE_PATH = ROOT_DIR / "tests" / "fixtures" / "memory_compaction_scenarios.json"


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
class CompactionScenarioResult:
    scenario_id: str
    passed: bool
    errors: List[str] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "errors": self.errors,
            "debug": self.debug,
        }


def load_scenarios() -> List[Dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))


async def evaluate_scenario(scenario: Dict[str, Any]) -> CompactionScenarioResult:
    # Force fallback compaction so quick harness checks deterministic checkpoint semantics,
    # not live sidecar model quality or network availability.
    with SettingsPatch({"SIDECAR_COMPACTION_ENABLED": False}):
        summary, debug = await build_checkpoint_summary(
            scenario.get("turns") or [],
            previous_checkpoint=scenario.get("previous_checkpoint"),
        )
    errors: List[str] = []
    expected = scenario.get("expected") or {}

    for key, expected_value in (expected.get("debug") or {}).items():
        if debug.get(key) is not expected_value:
            errors.append(f"debug.{key} expected {expected_value!r}, got {debug.get(key)!r}")

    for field_name, field_expectation in (expected.get("fields") or {}).items():
        haystack = _field_text(summary.get(field_name)).lower()
        for fragment in field_expectation.get("must_include") or []:
            if str(fragment).lower() not in haystack:
                errors.append(f"{field_name} missing fragment {fragment!r}")

    all_text = json.dumps(summary, ensure_ascii=False).lower()
    for fragment in expected.get("must_not_include") or []:
        if str(fragment).lower() in all_text:
            errors.append(f"summary contains forbidden fragment {fragment!r}")

    return CompactionScenarioResult(
        scenario_id=str(scenario.get("id") or ""),
        passed=not errors,
        errors=errors,
        debug={
            "used_sidecar": debug.get("used_sidecar"),
            "used_fallback": debug.get("used_fallback"),
            "folded": debug.get("folded"),
        },
    )


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value or "")


async def run_eval() -> Dict[str, Any]:
    scenarios = load_scenarios()
    results = [await evaluate_scenario(scenario) for scenario in scenarios]
    failed = [result for result in results if not result.passed]
    return {
        "scenario_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "results": [result.to_dict() for result in results],
    }


def main() -> None:
    report = asyncio.run(run_eval())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["failed_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
