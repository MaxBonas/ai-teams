"""Provenance y frescura de promociones de modelo por contrato de rol."""
from __future__ import annotations

import json
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


def _validate_evidence_content(
    calibration: dict[str, Any], *, repo_root: Path, receipts: list[str]
) -> list[str]:
    payloads: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for receipt in receipts:
        path = repo_root / receipt
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"invalid_json:{receipt}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"invalid_payload:{receipt}")
            continue
        payloads[receipt] = payload

    profile_id = str(calibration.get("profile_id") or "")
    model = str(calibration.get("model") or "")
    role = str(calibration.get("role") or "")
    provider_version = str(calibration.get("provider_version") or "")
    if profile_id == "codex_subscription":
        aggregates = [
            (receipt, payload) for receipt, payload in payloads.items()
            if payload.get("benchmark") == "context_curator_gpt_tier3_calibration_aggregate"
        ]
        if len(aggregates) != 1:
            return [*errors, "codex_aggregate_missing_or_duplicate"]
        aggregate_receipt, aggregate = aggregates[0]
        conclusion = aggregate.get("conclusion") or {}
        candidate = (aggregate.get("arms") or {}).get("luna_medium_prompt_v3") or {}
        checks = {
            "cli_version": aggregate.get("cli_version") == provider_version,
            "matrix_balanced": aggregate.get("matrix_balanced") is True,
            "promotion_allowed": conclusion.get("promotion_allowed") is True,
            "selected_model": conclusion.get("selected_model") == model,
            "selected_role": conclusion.get("selected_role") == role,
            "reasoning_effort": conclusion.get("reasoning_effort") == "medium",
            "candidate_model": candidate.get("model") == model,
            "candidate_effort": candidate.get("reasoning_effort_override") == "medium",
            "candidate_6_of_6": candidate.get("samples") == 6 and candidate.get("accepted") == 6,
            "candidate_valid": candidate.get("validation_errors") == [],
        }
        errors.extend(name for name, valid in checks.items() if not valid)
        expected_sources = set(receipts) - {aggregate_receipt}
        if set(candidate.get("source_receipts") or []) != expected_sources:
            errors.append("candidate_source_receipts_mismatch")
        for receipt in expected_sources:
            payload = payloads.get(receipt)
            if payload is None:
                continue
            config = payload.get("execution_config") or {}
            runtime = payload.get("runtime") or {}
            run = runtime.get("run") or {}
            individual_checks = {
                "accepted": payload.get("accepted") is True,
                "profile": payload.get("profile_id") == profile_id,
                "model": (payload.get("adapter") or {}).get("model") == model,
                "role": config.get("role") == role,
                "config_model": config.get("model") == model,
                "effort": config.get("reasoning_effort_override") == "medium",
                "run_status": run.get("status") == "completed",
                "agent": run.get("agent_id") == f"role:{role}",
                "issue_status": runtime.get("issue_status") == "done",
            }
            errors.extend(
                f"{name}:{receipt}" for name, valid in individual_checks.items() if not valid
            )
    elif profile_id == "antigravity_subscription":
        aggregates = [
            payload for payload in payloads.values()
            if payload.get("benchmark") == "antigravity_coding_behavioral_calibration"
        ]
        if len(aggregates) != 1:
            return [*errors, "antigravity_aggregate_missing_or_duplicate"]
        aggregate = aggregates[0]
        model_row = (aggregate.get("models") or {}).get(model) or {}
        integrity = aggregate.get("integrity") or {}
        conclusion = aggregate.get("conclusion") or {}
        hidden_total = int(model_row.get("hidden_total") or 0)
        hidden_passed = model_row.get("hidden_passed") or []
        checks = {
            "model_samples": model_row.get("samples") == 3,
            "model_done": model_row.get("done") == 3,
            "hidden_suite": hidden_total > 0 and hidden_passed == [hidden_total] * 3,
            "integrity": integrity.get("promotion_contract_complete") is True
            and integrity.get("promotion_allowed") is True
            and not integrity.get("missing_cells")
            and not integrity.get("duplicate_cells"),
            "promotion_allowed": conclusion.get("promotion_allowed") is True,
        }
        errors.extend(name for name, valid in checks.items() if not valid)
    return errors


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
        evidence_validation_errors = _validate_evidence_content(
            calibration, repo_root=repo_root, receipts=receipts
        )
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
        if evidence_validation_errors:
            stale_reasons.append("evidence_receipt_invalid")

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
                "evidence_validation_errors": evidence_validation_errors,
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
            and not entry["evidence_validation_errors"]
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
