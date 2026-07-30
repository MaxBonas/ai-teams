"""Proyección canónica y fail-closed de evidencia de adapters para el wizard."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "guided_setup_provider_evidence_v1"
DEFAULT_HEALTH_MAX_AGE_SECONDS = 24 * 60 * 60
DEFAULT_PROBE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def build_canonical_provider_evidence(
    plan: Mapping[str, Any],
    inventory: Mapping[str, Any],
    profiles: list[dict[str, Any]],
    *,
    observed_at: datetime | None = None,
    health_max_age_seconds: int = DEFAULT_HEALTH_MAX_AGE_SECONDS,
    probe_max_age_seconds: int = DEFAULT_PROBE_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    if plan.get("schema_version") != "guided_setup_preparation_v1":
        raise ValueError("guided_setup_provider_evidence_plan_schema_mismatch")
    if inventory.get("schema_version") != "machine_doctor_v1":
        raise ValueError("guided_setup_provider_evidence_inventory_schema_mismatch")
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    by_profile = {
        str(row.get("id") or ""): row
        for row in profiles
        if str(row.get("id") or "")
    }
    observations = {
        str(row.get("id") or ""): row
        for row in inventory.get("adapters", [])
        if isinstance(row, Mapping) and str(row.get("id") or "")
    }
    evidence: dict[str, dict[str, Any]] = {}
    details: list[dict[str, Any]] = []
    for requested in plan.get("adapters", []):
        profile_id = str(requested.get("id") or "")
        if profile_id == "personal_api":
            evidence[profile_id] = {
                "authentication": "not_checked",
                "catalog": "not_checked",
                "health": "not_checked",
                "contract": "not_checked",
            }
            details.append(_detail(profile_id, evidence[profile_id], [], []))
            continue
        profile = by_profile.get(profile_id)
        observation = observations.get(profile_id, {})
        if profile is None:
            evidence[profile_id] = {
                "authentication": "not_checked",
                "catalog": "not_checked",
                "health": "not_checked",
                "contract": "not_checked",
            }
            details.append(_detail(profile_id, evidence[profile_id], [], []))
            continue
        health = profile.get("health") if isinstance(profile.get("health"), Mapping) else {}
        fresh = _fresh(
            health.get("checked_at"),
            now=now,
            max_age_seconds=health_max_age_seconds,
        )
        auth_status = str(
            observation.get("authentication_status") or "not_checked"
        )
        authentication = (
            "passed"
            if fresh and auth_status in {"authenticated", "not_applicable"}
            else "failed"
            if fresh and auth_status == "not_authenticated"
            else "not_checked"
        )
        health_stage = (
            "passed"
            if fresh and str(health.get("status") or "") == "ok"
            else "failed"
            if fresh
            and str(health.get("status") or "")
            in {"failed", "degraded", "unavailable"}
            else "not_checked"
        )
        catalog = (
            profile.get("model_catalog")
            if isinstance(profile.get("model_catalog"), Mapping)
            else {}
        )
        catalog_passed = (
            str(catalog.get("status") or "") == "current"
            and int(catalog.get("count") or 0) > 0
            and (
                not catalog.get("checked_at")
                or _fresh(
                    catalog.get("checked_at"),
                    now=now,
                    max_age_seconds=health_max_age_seconds,
                )
            )
        )
        options = [
            row
            for row in profile.get("model_options", [])
            if isinstance(row, Mapping)
        ]
        cli_version = _version(
            ((observation.get("cli") or {}).get("version"))
            if isinstance(observation.get("cli"), Mapping)
            else None
        )
        config = (
            profile.get("config")
            if isinstance(profile.get("config"), Mapping)
            else {}
        )
        transport_version = cli_version or _version(
            config.get("api_version") or config.get("transport_version")
        )
        exact_receipts: list[str] = []
        exact_models: list[str] = []
        for option in options:
            receipts = [
                str(item)
                for item in option.get("probe_receipts", [])
                if _safe_receipt_ref(item)
            ]
            probe_version = _version(option.get("probe_version"))
            structured_output = str(
                option.get("structured_output")
                or profile.get("structured_output")
                or ""
            )
            if (
                option.get("probe_status") == "completed"
                and receipts
                and transport_version
                and probe_version == transport_version
                and structured_output in {"json_object", "json_schema"}
                and _fresh(
                    option.get("probe_evaluated_at"),
                    now=now,
                    max_age_seconds=probe_max_age_seconds,
                )
            ):
                exact_receipts.extend(receipts)
                exact_models.append(str(option.get("value") or ""))
        stages = {
            "authentication": authentication,
            "catalog": "passed" if catalog_passed else "not_checked",
            "health": health_stage,
            "contract": "passed" if exact_receipts else "not_checked",
        }
        evidence[profile_id] = stages
        details.append(
            _detail(
                profile_id,
                stages,
                sorted(set(exact_models)),
                sorted(set(exact_receipts)),
                catalog_source=str(catalog.get("source") or ""),
                health_checked_at=str(health.get("checked_at") or ""),
                cli_version=cli_version,
                transport_version=transport_version,
            )
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "discovery_is_quality": False,
            "model_execution_is_structured_contract": False,
            "inference_attempted": False,
        },
        "observed_at": now.astimezone(timezone.utc).isoformat(),
        "stage_evidence": evidence,
        "details": details,
    }


def _detail(
    profile_id: str,
    stages: Mapping[str, str],
    exact_models: list[str],
    receipt_refs: list[str],
    *,
    catalog_source: str = "",
    health_checked_at: str = "",
    cli_version: str | None = None,
    transport_version: str | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "stages": dict(stages),
        "catalog_source": catalog_source or None,
        "health_checked_at": health_checked_at or None,
        "cli_version": cli_version,
        "transport_version": transport_version or cli_version,
        "exact_contract_models": exact_models,
        "receipt_refs": receipt_refs,
    }


def _fresh(value: Any, *, now: datetime, max_age_seconds: int) -> bool:
    try:
        checked = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age = (now.astimezone(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= max_age_seconds


def _version(value: Any) -> str | None:
    match = re.search(
        r"\d+(?:\.\d+)+(?:-[A-Za-z0-9.]+)?|\bv\d+(?:[a-z]+\d*)?\b",
        str(value or ""),
        re.IGNORECASE,
    )
    return match.group(0) if match else None


def _safe_receipt_ref(value: Any) -> bool:
    ref = str(value or "").replace("\\", "/")
    return bool(
        ref
        and len(ref) <= 500
        and not ref.startswith("/")
        and not re.match(r"^[A-Za-z]:", ref)
        and ".." not in ref.split("/")
    )
