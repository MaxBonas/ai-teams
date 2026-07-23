"""Agrega el A/B durable de Context Curator sin consumir inferencias."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "model_calibration"
ARM_SPECS = {
    "gpt_5_5_control_unpinned": {
        "pattern": "context-curator-*-gpt-5.5-cli-0.145.0-seed-*.json",
        "model": "gpt-5.5", "effort": None,
    },
    "luna_unpinned_original": {
        "pattern": "context-curator-*-gpt-5.6-luna-cli-0.145.0-seed-*.json",
        "model": "gpt-5.6-luna", "effort": None,
    },
    "luna_unpinned_prompt_v2": {
        "pattern": "context-curator-*-gpt-5.6-luna-cli-0.145.0-prompt-v2-seed-*.json",
        "model": "gpt-5.6-luna", "effort": None,
    },
    "luna_medium_prompt_v3": {
        "pattern": "context-curator-*-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-*.json",
        "model": "gpt-5.6-luna", "effort": "medium",
    },
    "terra_unpinned_diagnostic": {
        "pattern": "context-curator-*-gpt-5.6-terra-cli-0.145.0-seed-*.json",
        "model": "gpt-5.6-terra", "effort": None,
    },
}
EXPECTED_CELLS = {(case, seed) for case in ("auth", "queue") for seed in (1, 2, 3)}
CELL_RE = re.compile(r"^context-curator-(auth|queue)-.*-seed-([123])$")


def _median(values: list[int | float]) -> float:
    return round(float(statistics.median(values)), 3)


def summarize_arm(paths: list[Path], *, model: str, effort: str | None) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    errors: list[str] = []
    cells: list[tuple[str, int]] = []
    for path, report in zip(paths, reports, strict=True):
        match = CELL_RE.match(path.stem)
        if not match:
            errors.append(f"invalid_filename:{path.name}")
        else:
            cells.append((match.group(1), int(match.group(2))))
        config = report.get("execution_config") or {}
        checks = {
            "profile": report.get("profile_id") == "codex_subscription",
            "role": config.get("role") == "context_curator",
            "config_model": config.get("model") == model,
            "effort": config.get("reasoning_effort_override") == effort,
            "adapter_model": (report.get("adapter") or {}).get("model") == model,
            "adapter_type": (report.get("adapter") or {}).get("type") == "subscription_cli",
            "channel": (report.get("adapter") or {}).get("channel") == "subscription",
            "run_status": ((report.get("runtime") or {}).get("run") or {}).get("status") == "completed",
            "agent": ((report.get("runtime") or {}).get("run") or {}).get("agent_id") == "role:context_curator",
            "issue_status": (report.get("runtime") or {}).get("issue_status") == "done",
        }
        errors.extend(f"{name}:{path.name}" for name, valid in checks.items() if not valid)
    if set(cells) != EXPECTED_CELLS or len(cells) != len(EXPECTED_CELLS):
        errors.append("cells_not_exactly_auth_queue_seeds_1_2_3")
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
        "reasoning_effort_override": effort,
        "validation_errors": errors,
        "source_receipts": [path.relative_to(REPO_ROOT).as_posix() for path in paths],
    }


def backfill_runtime_provenance(results_dir: Path = RESULTS_DIR) -> int:
    """Recupera configuración exacta de las DB originales sin ejecutar inferencias."""
    changed = 0
    for spec in ARM_SPECS.values():
        for path in sorted(results_dir.glob(str(spec["pattern"]))):
            report = json.loads(path.read_text(encoding="utf-8"))
            db_path = Path(str((report.get("runtime") or {}).get("db") or ""))
            if not db_path.is_file():
                raise RuntimeError(f"runtime DB unavailable for {path.name}")
            with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    "SELECT role, adapter_config_json FROM agents WHERE id='role:context_curator'"
                ).fetchone()
            if row is None:
                raise RuntimeError(f"context curator agent unavailable for {path.name}")
            config = json.loads(row[1])
            report["execution_config"] = {
                "role": row[0],
                "profile_id": config.get("profile_id"),
                "model": config.get("model"),
                "reasoning_effort_override": config.get("model_reasoning_effort"),
                "source": "recovered_read_only_from_original_runtime_db",
            }
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            changed += 1
    return changed


def build_report(results_dir: Path = RESULTS_DIR) -> dict[str, Any]:
    arms = {
        arm: summarize_arm(
            sorted(results_dir.glob(str(spec["pattern"]))),
            model=str(spec["model"]),
            effort=spec["effort"],
        )
        for arm, spec in ARM_SPECS.items()
    }
    candidate = arms["luna_medium_prompt_v3"]
    control = arms["gpt_5_5_control_unpinned"]
    matrix_balanced = all(not arm["validation_errors"] for arm in arms.values())
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
            "unpinned_effort_disposition": "historical_only_not_valid_for_effort_comparison",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_DIR / "context-curator-gpt-tier3-cli-0.145.0-aggregate-v3.json",
    )
    parser.add_argument(
        "--backfill-runtime-provenance",
        action="store_true",
        help="persiste en recibos la configuración recuperada de sus DB originales",
    )
    args = parser.parse_args()
    if args.backfill_runtime_provenance:
        print(json.dumps({"backfilled": backfill_runtime_provenance()}, ensure_ascii=False))
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["conclusion"], ensure_ascii=False))
    return 0 if report["conclusion"]["promotion_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
