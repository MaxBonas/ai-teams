"""Cierra OpenCode por no-cambio sin repetir inferencias del proveedor."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_VERSION = "1.18.4"
DECLARED_MODELS = {
    "opencode/deepseek-v4-flash-free",
    "opencode/laguna-s-2.1-free",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/north-mini-code-free",
}
REJECTED_MODELS = {"opencode/big-pickle"}
EVIDENCE_RECEIPTS = (
    "benchmarks/results/model_calibration/opencode-session-isolation-v1.json",
    "benchmarks/results/model_calibration/"
    "opencode-durable-review-v1-laguna-vs-deepseek-aggregate.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report(
    *,
    repo_root: Path,
    cli_version: str,
    discovered_models: set[str],
    observed_at: str,
) -> dict[str, Any]:
    isolation = json.loads((repo_root / EVIDENCE_RECEIPTS[0]).read_text("utf-8"))
    review = json.loads((repo_root / EVIDENCE_RECEIPTS[1]).read_text("utf-8"))
    screens = isolation.get("schema_screen") or []
    screened_models = {str(row.get("model") or "") for row in screens}
    structured_output_blocked = all(
        row.get("structured") is None
        and (row.get("provider_error") or {}).get("name") == "StructuredOutputError"
        for row in screens
    )
    deepseek = next(
        (
            arm
            for arm in review.get("arms") or []
            if arm.get("model") == "opencode/deepseek-v4-flash-free"
        ),
        {},
    )
    checks = {
        "cli_version_unchanged": cli_version == EXPECTED_VERSION,
        "declared_catalog_present": DECLARED_MODELS <= discovered_models,
        "opaque_discovery_still_rejected": REJECTED_MODELS <= discovered_models,
        "schema_screen_complete": screened_models == DECLARED_MODELS,
        "structured_output_still_blocked": structured_output_blocked,
        "deepseek_reviewer_remains_partial_1_of_3": (
            deepseek.get("samples") == 3 and deepseek.get("passed") == 1
        ),
        "default_change_forbidden": (
            (review.get("conclusion") or {}).get("default_change_allowed") is False
        ),
    }
    manifest = [
        {"receipt": relative, "sha256": _sha256(repo_root / relative)}
        for relative in EVIDENCE_RECEIPTS
    ]
    return {
        "schema_version": 1,
        "benchmark": "opencode_negative_pool_closure",
        "observed_at": observed_at,
        "profile_id": "opencode_zen_free",
        "cli_version": cli_version,
        "declared_models": sorted(DECLARED_MODELS),
        "discovered_models": sorted(discovered_models),
        "rejected_discoveries": sorted(REJECTED_MODELS),
        "checks": checks,
        "evidence_manifest": manifest,
        "inference_runs": 0,
        "decision": {
            "status": "closed_by_no_change" if all(checks.values()) else "reopen",
            "deepseek_reviewer": "partial",
            "other_pairs_promoted": False,
            "repeat_inference": False,
            "reopen_when": (
                "cambio de versión, catálogo, transporte o contrato structured output"
            ),
        },
        "ok": all(checks.values()),
    }


def _command_output(command: list[str]) -> str:
    executable = (
        shutil.which(f"{command[0]}.cmd")
        if command[0] == "opencode"
        else shutil.which(command[0])
    )
    if not executable:
        raise FileNotFoundError(f"executable not found: {command[0]}")
    completed = subprocess.run(
        [executable, *command[1:]],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "benchmarks/results/model_calibration/"
            "opencode-1.18.4-negative-closure-v1.json"
        ),
    )
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    report = build_report(
        repo_root=repo_root,
        cli_version=_command_output(["opencode", "--version"]),
        discovered_models=set(
            _command_output(["opencode", "models", "opencode"]).splitlines()
        ),
        observed_at=datetime.now(timezone.utc).astimezone().isoformat(),
    )
    output = args.output if args.output.is_absolute() else repo_root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
