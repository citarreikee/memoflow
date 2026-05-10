from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT_DIR / "data" / "harness_reports"


@dataclass
class HarnessCaseResult:
    name: str
    category: str
    status: str
    duration_ms: int
    command: Optional[List[str]] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class HarnessReport:
    harness_version: str
    started_at: str
    finished_at: str
    status: str
    duration_ms: int
    case_count: int
    passed_count: int
    failed_count: int
    skipped_count: int
    cases: List[HarnessCaseResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "harness_version": self.harness_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "case_count": self.case_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "cases": [asdict(case) for case in self.cases],
        }


QUICK_SCRIPTS = [
    ("compileall", "runtime_safety", [sys.executable, "-m", "compileall", "chat_history.py", "config.py", "main.py", "api", "providers", "services", "tests"]),
    ("memory_quality_contract", "formation_quality", [sys.executable, "tests/memory_quality_contract.py"]),
    ("memory_formation_quality_smoke", "formation_quality", [sys.executable, "tests/memory_formation_quality_smoke.py"]),
    ("memory_formation_scenario_eval", "formation_quality", [sys.executable, "tests/memory_formation_scenario_eval.py"]),
    ("memory_integration_planner_contract", "formation_integration", [sys.executable, "tests/memory_integration_planner_contract.py"]),
    ("memory_neighborhood_contract", "formation_integration", [sys.executable, "tests/memory_neighborhood_contract.py"]),
    ("memory_stabilization_smoke", "runtime_safety", [sys.executable, "tests/memory_stabilization_smoke.py"]),
    ("memory_job_queue_smoke", "runtime_safety", [sys.executable, "tests/memory_job_queue_smoke.py"]),
    ("memory_worker_smoke", "runtime_safety", [sys.executable, "tests/memory_worker_smoke.py"]),
    ("memory_worker_cli_smoke", "runtime_safety", [sys.executable, "tests/memory_worker_cli_smoke.py"]),
    ("memory_formation_smoke", "formation", [sys.executable, "tests/memory_formation_smoke.py"]),
    ("memory_formation_integration_job_smoke", "formation_integration", [sys.executable, "tests/memory_formation_integration_job_smoke.py"]),
    ("memory_storage_smoke", "storage", [sys.executable, "tests/memory_storage_smoke.py"]),
    ("memory_mutation_contract", "storage", [sys.executable, "tests/memory_mutation_contract.py"]),
    ("memory_retrieval_smoke", "retrieval", [sys.executable, "tests/memory_retrieval_smoke.py"]),
    ("memory_retrieval_scenario_eval", "retrieval", [sys.executable, "tests/memory_retrieval_scenario_eval.py"]),
    ("memory_compaction_scenario_eval", "runtime_compaction", [sys.executable, "tests/memory_compaction_scenario_eval.py"]),
    ("model_context_smoke", "runtime_safety", [sys.executable, "tests/model_context_smoke.py"]),
]

LONG_SCRIPTS = [
    ("runtime_long_task_smoke", "runtime_compaction", [sys.executable, "tests/runtime_long_task_smoke.py"]),
]


def main() -> None:
    args = parse_args()
    started = _utc_now()
    start_time = time.perf_counter()
    cases: List[HarnessCaseResult] = []

    if args.include_golden_summary:
        cases.append(_golden_case_summary())

    for name, category, command in QUICK_SCRIPTS:
        cases.append(_run_script_case(name=name, category=category, command=command, timeout_seconds=args.timeout_seconds))
        if args.fail_fast and cases[-1].status == "failed":
            break

    if not args.quick and not (args.fail_fast and any(case.status == "failed" for case in cases)):
        for name, category, command in LONG_SCRIPTS:
            cases.append(_run_script_case(name=name, category=category, command=command, timeout_seconds=args.long_timeout_seconds))
            if args.fail_fast and cases[-1].status == "failed":
                break
    elif args.quick:
        for name, category, command in LONG_SCRIPTS:
            cases.append(
                HarnessCaseResult(
                    name=name,
                    category=category,
                    status="skipped",
                    duration_ms=0,
                    command=command,
                    error="quick_mode",
                )
            )

    finished = _utc_now()
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    failed_count = len([case for case in cases if case.status == "failed"])
    passed_count = len([case for case in cases if case.status == "passed"])
    skipped_count = len([case for case in cases if case.status == "skipped"])
    report = HarnessReport(
        harness_version="0.1",
        started_at=started,
        finished_at=finished,
        status="failed" if failed_count else "passed",
        duration_ms=duration_ms,
        case_count=len(cases),
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        cases=cases,
    )

    report_path = _write_report(report, args.report_dir)
    print(json.dumps({"status": report.status, "report_path": str(report_path), "summary": _summary(report)}, ensure_ascii=False, indent=2))
    if failed_count:
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Memoflow memory harness scenarios and emit a JSON report.")
    parser.add_argument("--quick", action="store_true", help="Skip long runtime compaction scenario.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed case.")
    parser.add_argument("--timeout-seconds", type=int, default=90, help="Timeout for normal cases.")
    parser.add_argument("--long-timeout-seconds", type=int, default=600, help="Timeout for long cases.")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help="Directory for JSON reports.")
    parser.add_argument("--include-golden-summary", action="store_true", default=True, help="Include golden fixture summary.")
    return parser.parse_args()


def _run_script_case(*, name: str, category: str, command: List[str], timeout_seconds: int) -> HarnessCaseResult:
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        status = "passed" if completed.returncode == 0 else "failed"
        return HarnessCaseResult(
            name=name,
            category=category,
            status=status,
            duration_ms=duration_ms,
            command=command,
            stdout_tail=_tail(completed.stdout),
            stderr_tail=_tail(completed.stderr),
            metrics=_extract_metrics(name, completed.stdout),
            error=None if status == "passed" else f"exit_code_{completed.returncode}",
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return HarnessCaseResult(
            name=name,
            category=category,
            status="failed",
            duration_ms=duration_ms,
            command=command,
            stdout_tail=_tail(exc.stdout if isinstance(exc.stdout, str) else ""),
            stderr_tail=_tail(exc.stderr if isinstance(exc.stderr, str) else ""),
            error=f"timeout_after_{timeout_seconds}s",
        )
    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return HarnessCaseResult(
            name=name,
            category=category,
            status="failed",
            duration_ms=duration_ms,
            command=command,
            error=str(exc),
        )


def _golden_case_summary() -> HarnessCaseResult:
    start = time.perf_counter()
    fixture = ROOT_DIR / "tests" / "fixtures" / "memory_golden_cases.json"
    try:
        cases = json.loads(fixture.read_text(encoding="utf-8-sig"))
        by_status: Dict[str, int] = {}
        by_type: Dict[str, int] = {}
        for case in cases:
            expected = case.get("expected") or {}
            candidate = case.get("candidate") or {}
            by_status[str(expected.get("status"))] = by_status.get(str(expected.get("status")), 0) + 1
            by_type[str(candidate.get("type"))] = by_type.get(str(candidate.get("type")), 0) + 1
        return HarnessCaseResult(
            name="golden_fixture_summary",
            category="formation_quality",
            status="passed",
            duration_ms=int((time.perf_counter() - start) * 1000),
            metrics={"golden_case_count": len(cases), "by_expected_status": by_status, "by_candidate_type": by_type},
        )
    except Exception as exc:
        return HarnessCaseResult(
            name="golden_fixture_summary",
            category="formation_quality",
            status="failed",
            duration_ms=int((time.perf_counter() - start) * 1000),
            error=str(exc),
        )


def _extract_metrics(name: str, stdout: str) -> Dict[str, Any]:
    if name == "memory_quality_contract":
        return {"golden_contract": "ok" if "memory quality contract ok" in stdout else "unknown"}
    if name == "memory_formation_quality_smoke":
        return {"formation_quality_smoke": "ok" if "memory formation quality smoke ok" in stdout else "unknown"}
    if name == "memory_formation_scenario_eval":
        parsed = _extract_first_json(stdout)
        if isinstance(parsed, dict):
            return {
                "scenario_count": parsed.get("scenario_count"),
                "passed_count": parsed.get("passed_count"),
                "failed_count": parsed.get("failed_count"),
            }
    if name == "memory_compaction_scenario_eval":
        parsed = _extract_first_json(stdout)
        if isinstance(parsed, dict):
            return {
                "scenario_count": parsed.get("scenario_count"),
                "passed_count": parsed.get("passed_count"),
                "failed_count": parsed.get("failed_count"),
            }
    if name == "memory_retrieval_scenario_eval":
        parsed = _extract_first_json(stdout)
        if isinstance(parsed, dict):
            return {
                "scenario_count": parsed.get("scenario_count"),
                "passed_count": parsed.get("passed_count"),
                "failed_count": parsed.get("failed_count"),
            }
    if name == "runtime_long_task_smoke":
        parsed = _extract_first_json(stdout)
        if isinstance(parsed, dict):
            quality = parsed.get("quality") or {}
            trace = parsed.get("trace") or {}
            return {
                "turn_count": parsed.get("turn_count"),
                "episode_count": parsed.get("episode_count"),
                "checkpoint_covered_count": parsed.get("checkpoint_covered_count"),
                "checkpoint_quality_ok": quality.get("ok"),
                "checkpoint_coverage_complete": trace.get("coverage_complete"),
            }
    return {}


def _extract_first_json(text: str) -> Optional[Any]:
    stripped = (text or "").strip()
    if not stripped:
        return None
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None


def _write_report(report: HarnessReport, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = report_dir / f"memory_harness_report_{stamp}.json"
    path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    latest = report_dir / "latest.json"
    latest.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _summary(report: HarnessReport) -> Dict[str, Any]:
    by_category: Dict[str, Dict[str, int]] = {}
    for case in report.cases:
        bucket = by_category.setdefault(case.category, {"passed": 0, "failed": 0, "skipped": 0})
        bucket[case.status] = bucket.get(case.status, 0) + 1
    return {
        "case_count": report.case_count,
        "passed_count": report.passed_count,
        "failed_count": report.failed_count,
        "skipped_count": report.skipped_count,
        "by_category": by_category,
    }


def _tail(value: str, *, max_chars: int = 2500) -> str:
    if not value:
        return ""
    return value[-max_chars:]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()





