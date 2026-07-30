"""Auditoría de calibración fresca para automatización de modelos."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aiteam.model_selection import (
    build_contextual_model_selection,
    candidate_is_automation_eligible,
    same_profile_fallback,
)
from aiteam.policies import CANONICAL_ROLES, role_status

AUTOMATION_ENFORCEMENT_VERSION = "model_automation_enforcement_v1"


def audit_model_automation_enforcement(
    read_model: Mapping[str, Any],
    *,
    profiles: Iterable[Mapping[str, Any]],
    options_by_profile: Mapping[str, Iterable[Mapping[str, Any]]],
    repo_root: Path,
) -> dict[str, Any]:
    """Comprueba defaults y fallback dinámicos más wiring de hiring/recovery."""
    profile_rows = [dict(row) for row in profiles]
    option_rows = {
        str(profile_id): [dict(option) for option in options]
        for profile_id, options in options_by_profile.items()
    }
    failures: list[dict[str, Any]] = []
    candidate_checks = 0
    ineligible_calibration_checks = 0
    default_checks = 0
    fallback_checks = 0

    for role in CANONICAL_ROLES:
        if role_status(role) == "deterministic":
            continue
        projection = build_contextual_model_selection(
            read_model,
            role=role,
            profiles=profile_rows,
            options_by_profile=option_rows,
        )
        rows = list(projection.get("candidates") or ())
        for row in rows:
            candidate_checks += 1
            score = row.get("selection_score") or {}
            gates = score.get("hard_gates") or {}
            calibrated = (gates.get("calibrated") or {}).get("passed") is True
            fresh = (gates.get("fresh") or {}).get("passed") is True
            automatic = candidate_is_automation_eligible(row)
            if automatic and not (calibrated and fresh):
                failures.append(
                    {
                        "surface": "selector",
                        "code": "automation_without_fresh_calibration",
                        "candidate_id": row.get("candidate_id"),
                        "role": role,
                    }
                )
            if not calibrated or not fresh:
                ineligible_calibration_checks += 1
                if automatic:
                    failures.append(
                        {
                            "surface": "selector",
                            "code": "failed_calibration_gate_bypassed",
                            "candidate_id": row.get("candidate_id"),
                            "role": role,
                        }
                    )

        default_id = str((projection.get("default") or {}).get("candidate_id") or "")
        if default_id:
            default_checks += 1
            winner = next(
                (
                    row
                    for row in rows
                    if str(row.get("candidate_id") or "") == default_id
                ),
                None,
            )
            if not candidate_is_automation_eligible(winner):
                failures.append(
                    {
                        "surface": "default",
                        "code": "default_not_automation_eligible",
                        "candidate_id": default_id,
                        "role": role,
                    }
                )

        profiles_in_projection = {
            str((row.get("identity") or {}).get("profile_id") or "")
            for row in rows
        }
        for profile_id in profiles_in_projection:
            fallback = same_profile_fallback(
                projection,
                profile_id=profile_id,
                failed_model="__audit_failed_model__",
            )
            if fallback is None:
                continue
            fallback_checks += 1
            selected = next(
                (
                    row
                    for row in rows
                    if str(row.get("candidate_id") or "")
                    == str(fallback.get("candidate_id") or "")
                ),
                None,
            )
            if not candidate_is_automation_eligible(selected):
                failures.append(
                    {
                        "surface": "fallback",
                        "code": "fallback_not_automation_eligible",
                        "candidate_id": fallback.get("candidate_id"),
                        "role": role,
                    }
                )

    wiring = _audit_consumer_wiring(repo_root, failures)
    hermetic_matrix = _audit_hermetic_matrix(failures)
    return {
        "schema_version": AUTOMATION_ENFORCEMENT_VERSION,
        "read_model_hash": read_model.get("content_hash"),
        "roles_checked": sum(
            role_status(role) != "deterministic" for role in CANONICAL_ROLES
        ),
        "candidate_checks": candidate_checks,
        "failed_calibration_gate_checks": ineligible_calibration_checks,
        "default_checks": default_checks,
        "fallback_checks": fallback_checks,
        "hermetic_matrix": hermetic_matrix,
        "consumer_wiring": wiring,
        "failure_count": len(failures),
        "failures": failures,
        "ok": not failures,
    }


def _audit_consumer_wiring(
    repo_root: Path, failures: list[dict[str, Any]]
) -> dict[str, Any]:
    paths = {
        "defaults": repo_root / "aiteam" / "model_default_rollout.py",
        "hiring": repo_root / "aiteam" / "project_adapters.py",
        "recovery": repo_root / "aiteam" / "heartbeat" / "executor.py",
        "fallback": repo_root / "aiteam" / "model_selection.py",
    }
    try:
        sources = {
            surface: path.read_text(encoding="utf-8")
            for surface, path in paths.items()
        }
    except OSError as exc:
        failures.append(
            {
                "surface": "wiring",
                "code": "consumer_source_unreadable",
                "error": str(exc),
            }
        )
        return {"checks": 0, "passed": 0}
    checks = {
        "defaults_require_projected_auto_eligible": (
            'get("auto_eligible") is True' in sources["defaults"]
            and "auto_applied" in sources["defaults"]
        ),
        "hiring_uses_automation_gate": "_selection_is_automation_eligible"
        in sources["hiring"],
        "recovery_uses_automation_gate": "candidate_is_automation_eligible"
        in sources["recovery"],
        "fallback_uses_automation_gate": "candidate_is_automation_eligible(item)"
        in sources["fallback"],
        "legacy_adapter_fallback_denied": (
            "legacy provider fallback lacks an exact calibrated"
            in sources["recovery"]
            and 'decision="denied"' in sources["recovery"]
        ),
    }
    for check, passed in checks.items():
        if not passed:
            failures.append(
                {
                    "surface": "wiring",
                    "code": "consumer_gate_missing",
                    "check": check,
                }
            )
    return {"checks": len(checks), "passed": sum(checks.values())}


def _audit_hermetic_matrix(failures: list[dict[str, Any]]) -> dict[str, Any]:
    calibrated = {
        "candidate_id": "fixture:calibrated",
        "identity": {"profile_id": "fixture", "model_id": "calibrated"},
        "owner_selectable": True,
        "rank": 2,
        "selection_score": {"auto_eligible": True},
    }
    uncalibrated = {
        "candidate_id": "fixture:uncalibrated",
        "identity": {"profile_id": "fixture", "model_id": "uncalibrated"},
        "owner_selectable": True,
        "rank": 1,
        "selection_score": {
            "auto_eligible": False,
            "auto_ineligible_reasons": ["gate:calibrated:no"],
        },
    }
    cases = {
        "manual_selection_does_not_imply_automation": (
            candidate_is_automation_eligible(uncalibrated) is False
        ),
        "calibrated_candidate_can_automate": (
            candidate_is_automation_eligible(calibrated) is True
        ),
        "fallback_skips_higher_ranked_uncalibrated": (
            (
                same_profile_fallback(
                    {"candidates": [uncalibrated, calibrated]},
                    profile_id="fixture",
                    failed_model="failed",
                )
                or {}
            ).get("candidate_id")
            == "fixture:calibrated"
        ),
        "fallback_fails_closed_without_calibrated_candidate": (
            same_profile_fallback(
                {"candidates": [uncalibrated]},
                profile_id="fixture",
                failed_model="failed",
            )
            is None
        ),
    }
    for case, passed in cases.items():
        if not passed:
            failures.append(
                {
                    "surface": "hermetic_matrix",
                    "code": "automation_gate_case_failed",
                    "case": case,
                }
            )
    return {
        "checks": len(cases),
        "passed": sum(cases.values()),
        "cases": [{"case": case, "passed": passed} for case, passed in cases.items()],
    }
