"""Genera el backlog durable de calibración por perfil+modelo+rol."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_evaluation_coverage import audit_model_evaluation_coverage  # noqa: E402
from aiteam.user_config import (  # noqa: E402
    DEFAULT_ADAPTER_PROFILES,
    executable_model_options,
    model_is_selectable,
    observed_profile_cli_version,
)


def _versions_from_drift(path: Path) -> dict[str, str | None]:
    report = json.loads(path.read_text(encoding="utf-8"))
    versions = {
        str(row.get("profile_id") or ""): str(row.get("cli_version") or "") or None
        for row in report.get("catalogs") or []
    }
    codex = report.get("codex_catalog") or {}
    versions["codex_subscription"] = str(codex.get("installed_version") or "") or None
    for profile in DEFAULT_ADAPTER_PROFILES:
        live_version = observed_profile_cli_version(profile)
        if live_version:
            versions[str(profile.get("id") or "")] = live_version
    return versions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drift-receipt",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "model_catalog_drift" / "model-catalog-drift-2026-07-22.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "model_evaluation_coverage" / "model-evaluation-coverage-2026-07-22.json",
    )
    args = parser.parse_args()
    executable: dict[str, set[str]] = {}
    for profile in DEFAULT_ADAPTER_PROFILES:
        profile_id = str(profile.get("id") or "")
        options, _catalog = executable_model_options(profile_id, profile=profile)
        executable[profile_id] = {
            str(option.get("value") or "")
            for option in options
            if model_is_selectable(option)
        }
    report = audit_model_evaluation_coverage(
        observed_at=datetime.now().astimezone(),
        observed_versions=_versions_from_drift(args.drift_receipt),
        executable_models_by_profile=executable,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"models": report["models"], "role_pairs": report["role_pairs"], "pair_counts": report["pair_counts"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
