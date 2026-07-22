"""Provenance y frescura de promociones de modelo por contrato de rol."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
CALIBRATION_MAX_AGE_DAYS = 30

# Solo contiene cambios de selección autorizados por evidencia local. Los
# defaults iniciales del catálogo no se convierten retroactivamente en
# "promociones" ni se deshabilitan por no aparecer aquí.
PROMOTED_MODEL_CALIBRATIONS: tuple[dict[str, Any], ...] = (
    {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-luna",
        "role": "context_curator",
        "calibrated_at": "2026-07-22",
        "provider_version": "0.145.0",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/context-curator-gpt-tier3-cli-0.145.0-aggregate-v3.json",
            "benchmarks/results/model_calibration/context-curator-auth-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-1.json",
            "benchmarks/results/model_calibration/context-curator-auth-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-2.json",
            "benchmarks/results/model_calibration/context-curator-auth-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-3.json",
            "benchmarks/results/model_calibration/context-curator-queue-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-1.json",
            "benchmarks/results/model_calibration/context-curator-queue-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-2.json",
            "benchmarks/results/model_calibration/context-curator-queue-gpt-5.6-luna-cli-0.145.0-medium-v3-seed-3.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "claude-sonnet-4-6",
        "role": "engineer",
        "calibrated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-coding-cli-conversor-v4-aggregate-v3.json",
        ),
    },
    {
        "profile_id": "antigravity_subscription",
        "model": "claude-sonnet-4-6",
        "role": "software_engineer",
        "calibrated_at": "2026-07-21",
        "provider_version": "1.1.5",
        "evidence_receipts": (
            "benchmarks/results/model_calibration/antigravity-coding-cli-conversor-v4-aggregate-v3.json",
        ),
    },
)


def _observation_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def audit_promoted_model_calibrations(
    *,
    observed_at: datetime | date,
    observed_versions: dict[str, str | None] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Evalúa edad, versión y recibos sin modificar disponibilidad de modelos."""
    observation = _observation_date(observed_at)
    versions = observed_versions or {}
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    registry_errors: list[dict[str, str]] = []

    for calibration in PROMOTED_MODEL_CALIBRATIONS:
        profile_id = str(calibration.get("profile_id") or "").strip()
        model = str(calibration.get("model") or "").strip()
        role = str(calibration.get("role") or "").strip()
        key = (profile_id, model, role)
        if not all(key):
            registry_errors.append({"key": ":".join(key), "reason": "identity_missing"})
            continue
        if key in seen:
            registry_errors.append({"key": ":".join(key), "reason": "duplicate_pair"})
            continue
        seen.add(key)

        try:
            calibrated_on = date.fromisoformat(str(calibration.get("calibrated_at") or ""))
        except ValueError:
            registry_errors.append({"key": ":".join(key), "reason": "calibrated_at_invalid"})
            continue
        age_days = (observation - calibrated_on).days
        expected_version = str(calibration.get("provider_version") or "").strip()
        if not expected_version:
            registry_errors.append(
                {"key": ":".join(key), "reason": "provider_version_missing"}
            )
        observed_version = str(versions.get(profile_id) or "").strip()
        receipts = [str(item) for item in calibration.get("evidence_receipts") or ()]
        missing_receipts = [
            receipt for receipt in receipts if not (repo_root / receipt).is_file()
        ]
        stale_reasons: list[str] = []
        if age_days < 0:
            stale_reasons.append("calibration_date_in_future")
        elif age_days > CALIBRATION_MAX_AGE_DAYS:
            stale_reasons.append("calibration_age_exceeded")
        if expected_version and not observed_version:
            stale_reasons.append("provider_version_unobserved")
        elif expected_version and observed_version != expected_version:
            stale_reasons.append("provider_version_changed")
        if not receipts or missing_receipts:
            stale_reasons.append("evidence_receipt_missing")

        fresh = not stale_reasons
        entries.append(
            {
                "profile_id": profile_id,
                "model": model,
                "role": role,
                "calibrated_at": calibrated_on.isoformat(),
                "age_days": age_days,
                "max_age_days": CALIBRATION_MAX_AGE_DAYS,
                "provider_version": expected_version or None,
                "observed_provider_version": observed_version or None,
                "evidence_receipts": receipts,
                "missing_evidence_receipts": missing_receipts,
                "status": "fresh" if fresh else "stale",
                "stale_reasons": stale_reasons,
                "new_promotion_allowed": fresh,
                "existing_default_action": "unchanged",
            }
        )

    registry_valid = (
        not registry_errors
        and len(entries) == len(PROMOTED_MODEL_CALIBRATIONS)
        and all(
            entry["evidence_receipts"] and not entry["missing_evidence_receipts"]
            for entry in entries
        )
    )
    all_fresh = registry_valid and all(entry["status"] == "fresh" for entry in entries)
    return {
        "schema_version": 1,
        "observed_at": observation.isoformat(),
        "max_age_days": CALIBRATION_MAX_AGE_DAYS,
        "registry_valid": registry_valid,
        "all_fresh": all_fresh,
        "registered_promotions_fresh": all_fresh,
        "unregistered_promotions_allowed": False,
        "existing_defaults_changed": False,
        "entries": entries,
        "registry_errors": registry_errors,
    }


def model_promotion_allowed(
    profile_id: str,
    model: str,
    role: str,
    *,
    observed_at: datetime | date,
    observed_version: str | None,
    repo_root: Path = REPO_ROOT,
) -> bool:
    """Falla cerrado para pares no registrados, stale o sin versión comprobada."""
    report = audit_promoted_model_calibrations(
        observed_at=observed_at,
        observed_versions={profile_id: observed_version},
        repo_root=repo_root,
    )
    return report["registry_valid"] is True and any(
        entry["profile_id"] == profile_id
        and entry["model"] == model
        and entry["role"] == role
        and entry["new_promotion_allowed"] is True
        for entry in report["entries"]
    )
