"""Audita drift entre catálogos CLI vivos y modelos declarados por AI Teams.

No consume inferencias. Ejecuta únicamente inventarios autenticados, compara
IDs exactos y añade la matriz hermética perfil+modelo+rol al recibo.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.model_flow_matrix import audit_builtin_model_flows  # noqa: E402
from aiteam.model_calibration import audit_promoted_model_calibrations  # noqa: E402
from aiteam.model_tiers import audit_model_tier_matrix  # noqa: E402
from aiteam.user_config import (  # noqa: E402
    DEFAULT_ADAPTER_PROFILES,
    MODEL_OPTIONS_BY_PROFILE,
    codex_catalog_snapshot,
    model_options,
)


CATALOG_COMMANDS: dict[str, dict[str, Any]] = {
    "antigravity_subscription": {
        "executables": ("agy", "agy.exe"),
        "args": ("models",),
        "source": "agy models",
        "excluded": {},
    },
    "opencode_zen_free": {
        "executables": ("opencode.cmd", "opencode"),
        "args": ("models", "opencode"),
        "source": "opencode models opencode",
        "excluded": {
            "opencode/big-pickle": {
                "disposition": "rejected",
                "reason": "identidad opaca; excluido por provenance",
            },
        },
    },
}


def compare_catalog(
    *,
    profile_id: str,
    source: str,
    cli_version: str,
    declared: list[str],
    discovered: list[str],
    excluded: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    excluded = excluded or {}
    declared_set = {item for item in declared if item}
    discovered_set = {item for item in discovered if item}
    excluded_present = {
        model: disposition
        for model, disposition in excluded.items()
        if model in discovered_set
    }
    missing = sorted(declared_set - discovered_set)
    unexpected = sorted(discovered_set - declared_set - set(excluded))
    duplicates = sorted({item for item in discovered if discovered.count(item) > 1})
    return {
        "profile_id": profile_id,
        "source": source,
        "cli_version": cli_version,
        "status": "current",
        "declared": sorted(declared_set),
        "discovered": sorted(discovered_set),
        "excluded_discovered": excluded_present,
        "missing_declared": missing,
        "unexpected_discovered": unexpected,
        "duplicate_discovered": duplicates,
        "coverage_ok": not missing and not unexpected and not duplicates,
    }


def build_report(
    *,
    catalog_rows: list[dict[str, Any]],
    flow_report: dict[str, Any],
    codex_catalog: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    codex_current = (
        codex_catalog.get("status") == "current"
        and bool(codex_catalog.get("installed_version"))
        and bool(codex_catalog.get("catalog_client_version"))
    )
    inventory_complete = bool(catalog_rows) and codex_current and all(
        row.get("status") == "current" and bool(row.get("cli_version"))
        for row in catalog_rows
    )
    coverage_ok = inventory_complete and codex_catalog.get("coverage_ok") is True and all(
        row.get("coverage_ok") is True for row in catalog_rows
    )
    flow_ok = flow_report.get("ok") is True
    tier_report = audit_model_tier_matrix(
        DEFAULT_ADAPTER_PROFILES, MODEL_OPTIONS_BY_PROFILE
    )
    tier_ok = tier_report.get("ok") is True
    observed_versions = {
        str(row.get("profile_id") or ""): str(row.get("cli_version") or "") or None
        for row in catalog_rows
    }
    observed_versions["codex_subscription"] = (
        str(codex_catalog.get("installed_version") or "") or None
    )
    calibration_report = audit_promoted_model_calibrations(
        observed_at=observed_at,
        observed_versions=observed_versions,
    )
    calibration_due_dates = [
        datetime.fromisoformat(entry["calibrated_at"]).date()
        + timedelta(days=int(entry["max_age_days"]))
        for entry in calibration_report["entries"]
    ]
    cadence_due = (observed_at + timedelta(days=30)).date()
    scheduled_calibration_due = min(calibration_due_dates, default=cadence_due)
    calibration_due = (
        observed_at.date()
        if calibration_report["all_fresh"] is not True
        else max(observed_at.date(), scheduled_calibration_due)
    )
    next_review_due = min(cadence_due, calibration_due)
    attention: list[dict[str, Any]] = []
    for row in catalog_rows:
        if row.get("status") != "current" or row.get("coverage_ok") is not True:
            attention.append(
                {
                    "profile_id": row.get("profile_id"),
                    "reason": "catalog_inventory_failed"
                    if row.get("status") != "current"
                    else "catalog_drift",
                }
            )
    if not codex_current or codex_catalog.get("coverage_ok") is not True:
        attention.append(
            {
                "profile_id": "codex_subscription",
                "reason": (
                    codex_catalog.get("reason")
                    or ("catalog_drift" if codex_current else codex_catalog.get("status"))
                ),
            }
        )
    for entry in calibration_report["entries"]:
        if entry["status"] != "fresh":
            attention.append(
                {
                    "profile_id": entry["profile_id"],
                    "model": entry["model"],
                    "role": entry["role"],
                    "reason": "model_calibration_stale",
                    "stale_reasons": entry["stale_reasons"],
                }
            )
    if not tier_ok:
        attention.append(
            {
                "profile_id": "builtin_model_catalog",
                "reason": "model_tier_matrix_incomplete",
                "failures": tier_report.get("failures") or [],
            }
        )
    return {
        "schema_version": 1,
        "benchmark": "model_catalog_drift_audit",
        "observed_at": observed_at.isoformat(),
        "policy": {
            "owner": "AI Teams maintainer",
            "cadence": "monthly_and_on_cli_or_provider_change",
            "next_review_due": next_review_due.isoformat(),
            "calibration_next_review_due": calibration_due.isoformat(),
            "triggers": [
                "cli_version_changed",
                "provider_catalog_changed",
                "model_id_added_or_removed",
                "preview_or_free_offer_changed",
            ],
            "promotion_rule": "discovery_never_authorizes_defaults",
            "calibration_rule": "fresh_exact_profile_model_role_evidence_required",
        },
        "catalogs": catalog_rows,
        "codex_catalog": codex_catalog,
        "model_flow_matrix": {
            key: flow_report.get(key)
            for key in (
                "ok",
                "profile_count",
                "model_count",
                "positive_cell_count",
                "negative_cell_count",
                "failures",
            )
        },
        "model_tier_matrix": {
            key: tier_report.get(key)
            for key in ("ok", "policy_version", "models_audited", "failures", "rows")
        },
        "model_calibration_freshness": calibration_report,
        "gates": {
            "authenticated_inventories_complete": inventory_complete,
            "declared_catalog_coverage": coverage_ok,
            "hermetic_model_flow_matrix": flow_ok,
            "model_tier_matrix_complete": tier_ok,
            "promoted_model_calibration_registry": calibration_report["registry_valid"],
            "promoted_model_calibrations_fresh": calibration_report["all_fresh"],
        },
        "attention_required": attention,
        "promotion_allowed": False,
        "ok": (
            inventory_complete
            and coverage_ok
            and flow_ok
            and tier_ok
            and calibration_report["registry_valid"]
            and calibration_report["all_fresh"]
        ),
    }


def _resolve_executable(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _collect_catalog(profile_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    executable = _resolve_executable(tuple(spec["executables"]))
    if not executable:
        return {
            "profile_id": profile_id,
            "source": spec["source"],
            "status": "unavailable",
            "coverage_ok": False,
            "reason": "cli_not_installed",
        }
    try:
        proc = subprocess.run(
            [executable, *spec["args"]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "profile_id": profile_id,
            "source": spec["source"],
            "status": "timeout",
            "coverage_ok": False,
        }
    if proc.returncode != 0:
        return {
            "profile_id": profile_id,
            "source": spec["source"],
            "status": "command_failed",
            "coverage_ok": False,
            "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[:1000],
        }
    try:
        version_proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "profile_id": profile_id,
            "source": spec["source"],
            "status": "version_timeout",
            "coverage_ok": False,
        }
    cli_version = (
        (version_proc.stdout or version_proc.stderr or "").strip().splitlines()[:1]
    )
    if version_proc.returncode != 0 or not cli_version:
        return {
            "profile_id": profile_id,
            "source": spec["source"],
            "status": "version_unavailable",
            "coverage_ok": False,
        }
    discovered = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    declared = [
        str(item.get("value") or "")
        for item in model_options().get(profile_id, [])
        if isinstance(item, dict)
    ]
    return compare_catalog(
        profile_id=profile_id,
        source=str(spec["source"]),
        cli_version=cli_version[0],
        declared=declared,
        discovered=discovered,
        excluded=dict(spec.get("excluded") or {}),
    )


def run_audit(*, observed_at: datetime | None = None) -> dict[str, Any]:
    timestamp = observed_at or datetime.now().astimezone()
    catalog_rows = [
        _collect_catalog(profile_id, spec)
        for profile_id, spec in CATALOG_COMMANDS.items()
    ]
    # Old completed-run health must not mask a model removed from the current
    # Codex cache, so this gate consumes the raw read-only catalog snapshot.
    codex_catalog = codex_catalog_snapshot()
    declared = [
        str(item.get("value") or "")
        for item in model_options().get("codex_subscription", [])
        if isinstance(item, dict) and str(item.get("value") or "")
    ]
    discovered = [str(item) for item in codex_catalog.pop("models", []) if str(item)]
    missing = sorted(set(declared) - set(discovered))
    duplicates = sorted({item for item in discovered if discovered.count(item) > 1})
    codex_catalog.update(
        {
            "declared": sorted(set(declared)),
            "discovered": sorted(set(discovered)),
            "missing_declared": missing,
            "duplicate_discovered": duplicates,
            "coverage_ok": not missing and not duplicates,
        }
    )
    return build_report(
        catalog_rows=catalog_rows,
        flow_report=audit_builtin_model_flows(),
        codex_catalog=codex_catalog,
        observed_at=timestamp,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run_audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"gates": report["gates"], "attention": report["attention_required"]}
        )
    )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
