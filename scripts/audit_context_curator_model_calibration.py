"""Agrega el A/B durable de Context Curator sin consumir inferencias."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "model_calibration"
ARM_PATTERNS = {
    "gpt_5_5_control_low": "context-curator-*-gpt-5.5-cli-0.145.0-seed-*.json",
    "luna_low_original": "context-curator-*-gpt-5.6-luna-cli-0.145.0-seed-*.json",
    "luna_low_prompt_v2": "context-curator-*-gpt-5.6-luna-cli-0.145.0-prompt-v2-seed-*.json",
    "luna_medium_prompt_v3": "context-curator-*-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-*.json",
    "terra_low_diagnostic": "context-curator-*-gpt-5.6-terra-cli-0.145.0-seed-*.json",
}


def _median(values: list[int | float]) -> float:
    return round(float(statistics.median(values)), 3)


def summarize_arm(paths: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    seconds = [float(report["runtime"]["wall_seconds"]) for report in reports]
    inputs = [int(report["runtime"]["input_tokens"]) for report in reports]
    outputs = [int(report["runtime"]["output_tokens"]) for report in reports]
    cases = sorted(
        {
            str(report.get("rubric_id") or "").split("_causal_")[0]
            for report in reports
        }
    )
    return {
        "model": str(reports[0]["adapter"]["model"]),
        "samples": len(reports),
        "accepted": sum(report.get("accepted") is True for report in reports),
        "cases": cases,
        "wall_seconds_median": _median(seconds),
        "wall_seconds_range": [min(seconds), max(seconds)],
        "input_tokens_median": _median(inputs),
        "output_tokens_median": _median(outputs),
        "source_receipts": [path.relative_to(REPO_ROOT).as_posix() for path in paths],
    }


def build_report(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    arms = {
        arm: summarize_arm(sorted(results_dir.glob(pattern)))
        for arm, pattern in ARM_PATTERNS.items()
    }
    candidate = arms["luna_medium_prompt_v3"]
    control = arms["gpt_5_5_control_low"]
    matrix_balanced = all(arm["samples"] == 6 and len(arm["cases"]) == 2 for arm in arms.values())
    promotion_allowed = (
        matrix_balanced
        and candidate["accepted"] == candidate["samples"]
        and control["accepted"] == control["samples"]
    )
    return {
        "schema_version": 1,
        "benchmark": "context_curator_gpt_tier3_calibration_aggregate",
        "channel": "codex_subscription",
        "cli_version": "0.145.0",
        "contract": "two_causal_slices_three_seeds_each",
        "arms": arms,
        "matrix_balanced": matrix_balanced,
        "conclusion": {
            "promotion_allowed": promotion_allowed,
            "selected_model": "gpt-5.6-luna" if promotion_allowed else None,
            "selected_tier": "budget" if promotion_allowed else None,
            "selected_role": "context_curator" if promotion_allowed else None,
            "reasoning_effort": "medium" if promotion_allowed else None,
            "quality_gate": "candidate_and_control_6_of_6",
            "gpt_5_5_disposition": "historical_control_not_active_default",
            "terra_disposition": "diagnostic_not_promoted_outside_budget_tier",
            "low_effort_disposition": "rejected_due_to_silent_causal_anchor_loss",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_DIR / "context-curator-gpt-tier3-cli-0.145.0-aggregate-v3.json",
    )
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return 0 if report["conclusion"]["promotion_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
