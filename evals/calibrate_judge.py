"""Compare LLM judge labels with independent human review labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.grade import load_jsonl
from evals.judge import DIMENSIONS


def calibration_report(reference_rows: list[dict], judge_rows: list[dict]) -> dict:
    references = {row["scenario_id"]: row["labels"] for row in reference_rows}
    if len(references) != len(reference_rows):
        raise ValueError("Duplicate scenario_id in reference labels")
    judged = {row["scenario_id"]: row["judgments"] for row in judge_rows}
    if len(judged) != len(judge_rows):
        raise ValueError("Duplicate scenario_id in judge results")

    report = {}
    for dimension in DIMENSIONS:
        compared = agreements = false_passes = abstentions = 0
        for scenario_id in references.keys() & judged.keys():
            human_label = references[scenario_id].get(dimension)
            if human_label not in {"pass", "fail"}:
                continue
            judge_label = judged[scenario_id].get(dimension, {}).get("label")
            if judge_label not in {"pass", "fail"}:
                abstentions += 1
                continue
            compared += 1
            agreements += int(judge_label == human_label)
            false_passes += int(judge_label == "pass" and human_label == "fail")
        report[dimension] = {
            "compared": compared,
            "agreement": round(agreements / compared, 3) if compared else None,
            "false_passes": false_passes,
            "abstentions": abstentions,
        }
    return {"matched_cases": len(references.keys() & judged.keys()), "dimensions": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare judge labels against human review")
    parser.add_argument("--reference", type=Path, required=True,
                        help="JSONL: scenario_id and labels by dimension")
    parser.add_argument("--judgments", type=Path, required=True,
                        help="JSONL output from evals.judge")
    args = parser.parse_args()
    print(json.dumps(calibration_report(load_jsonl(args.reference),
                                        load_jsonl(args.judgments)), indent=2))


if __name__ == "__main__":
    main()
