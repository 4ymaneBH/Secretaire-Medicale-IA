"""Deterministic graders for captured call traces.

Run with: python -m evals.grade --runs evals/fixtures/runs.jsonl
The input is JSONL with one exported call per line. No model API is needed.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_SCENARIOS = Path(__file__).with_name("scenarios.jsonl")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        rows.append(row)
    return rows


def _check(name: str, passed: bool, actual: Any, expected: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "actual": actual, "expected": expected}


def grade_case(scenario: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Grade observable actions and state, never infer consent from prose."""
    if scenario["scenario_id"] != run["scenario_id"]:
        raise ValueError("scenario_id mismatch")

    expected = scenario["expected"]
    events = run.get("events", [])
    calls = [event for event in events if event.get("type") == "tool_call"]
    successful = [
        event for event in calls
        if event.get("result", {}).get("status") not in (None, "error")
    ]
    successful_names = [event.get("name") for event in successful]
    all_names = [event.get("name") for event in calls]
    state = run.get("final_state", {})
    checks = []

    for name in expected.get("required_tools", []):
        checks.append(_check(f"required_tool:{name}", name in successful_names,
                             successful_names, name))
    for name in expected.get("forbidden_tools", []):
        checks.append(_check(f"forbidden_tool:{name}", name not in all_names,
                             all_names, f"no {name}"))

    sequence = expected.get("required_sequence")
    if sequence:
        cursor = iter(successful_names)
        ordered = all(any(name == item for item in cursor) for name in sequence)
        checks.append(_check("required_sequence", ordered, successful_names, sequence))

    for key in ("appointment_confirmed", "transfer_redirect_accepted", "appointment_scheduled_at"):
        if key in expected:
            actual = state.get(key)
            checks.append(_check(key, actual == expected[key], actual, expected[key]))

    if expected.get("appointment_confirmed") is True:
        persisted = state.get("appointment_id") is not None and bool(
            state.get("appointment_scheduled_at")
        )
        checks.append(_check("appointment_persisted", persisted,
                             state.get("appointment_scheduled_at"), "saved appointment row"))
        proposals = [event.get("arguments", {}).get("slot") for event in successful
                     if event.get("name") == "propose_appointment_slot"]
        proposed_slot = proposals[-1] if proposals else None
        scheduled_slot = state.get("appointment_scheduled_at")
        try:
            slot_matches = bool(proposed_slot and scheduled_slot) and (
                datetime.fromisoformat(proposed_slot) == datetime.fromisoformat(scheduled_slot)
            )
        except ValueError:
            slot_matches = False
        checks.append(_check("saved_slot_matches_proposal", slot_matches,
                             scheduled_slot, proposed_slot))

    if expected.get("urgent_transfer"):
        urgent = any(
            event.get("name") == "transfer_to_human"
            and event.get("arguments", {}).get("urgent") is True
            and event.get("result", {}).get("status") == "transfer"
            for event in calls
        )
        checks.append(_check("urgent_transfer", urgent, urgent, True))

    if expected.get("forbid_urgent_transfer"):
        urgent = any(
            event.get("name") == "transfer_to_human"
            and event.get("arguments", {}).get("urgent") is True
            for event in calls
        )
        checks.append(_check("forbid_urgent_transfer", not urgent, urgent, False))

    if "final_language" in expected:
        assistant_turns = [turn for turn in run.get("turns", [])
                           if turn.get("speaker") == "assistant"]
        actual = assistant_turns[-1].get("language") if assistant_turns else None
        checks.append(_check("final_language", actual == expected["final_language"],
                             actual, expected["final_language"]))

    return {
        "scenario_id": scenario["scenario_id"],
        "category": scenario["category"],
        "passed": bool(checks) and all(check["passed"] for check in checks),
        "checks": checks,
    }


def grade_runs(scenarios: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        raise ValueError("No captured runs to grade")
    by_id = {row["scenario_id"]: row for row in scenarios}
    if len(by_id) != len(scenarios):
        raise ValueError("Duplicate scenario_id in scenario set")
    seen = set()
    results = []
    for run in runs:
        scenario_id = run["scenario_id"]
        if scenario_id not in by_id:
            raise ValueError(f"Unknown scenario_id: {scenario_id}")
        if scenario_id in seen:
            raise ValueError(f"Duplicate run for scenario_id: {scenario_id}")
        seen.add(scenario_id)
        results.append(grade_case(by_id[scenario_id], run))

    categories: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = categories.setdefault(result["category"], {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += int(result["passed"])

    def latency_summary(event_type: str) -> dict[str, int | None]:
        samples = sorted(
            event["latency_ms"] for run in runs for event in run.get("events", [])
            if event.get("type") == event_type
            and isinstance(event.get("latency_ms"), (int, float))
        )
        if not samples:
            return {"samples": 0, "p50": None, "p95": None}
        return {
            "samples": len(samples),
            "p50": samples[math.ceil(0.50 * len(samples)) - 1],
            "p95": samples[math.ceil(0.95 * len(samples)) - 1],
        }

    return {
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "scenario_coverage": f"{len(seen)}/{len(scenarios)}",
        "categories": categories,
        "voice": {
            "speech_end_to_audio_sent_ms": latency_summary("response_audio_sent"),
            "stream_start_to_first_audio_sent_ms": latency_summary("first_stream_audio_sent"),
            "tts_errors": sum(event.get("type") == "tts_error"
                              for run in runs for event in run.get("events", [])),
        },
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade captured call traces against labeled scenarios")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Write JSON report to this file")
    args = parser.parse_args()
    report = grade_runs(load_jsonl(args.scenarios), load_jsonl(args.runs))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
