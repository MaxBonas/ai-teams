from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from aiteam.installation_support import load_installation_support_contract
from aiteam.machine_doctor import _probe_version_command
from aiteam.platform_runtime import provider_cli_fingerprint, resolve_provider_cli
from aiteam.user_config import codex_catalog_snapshot, load_adapter_profiles

SCHEMA_VERSION = "provider_cli_version_audit_v1"
_VERSION_PATTERN = re.compile(
    r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?![0-9A-Za-z.-])"
)


def parse_cli_version(value: Any) -> dict[str, Any] | None:
    match = _VERSION_PATTERN.search(str(value or ""))
    if not match:
        return None
    prerelease = match.group(4)
    normalized = ".".join(match.group(index) for index in range(1, 4))
    if prerelease:
        normalized = f"{normalized}-{prerelease}"
    return {
        "normalized": normalized,
        "core": tuple(int(match.group(index)) for index in range(1, 4)),
        "prerelease": prerelease,
        "channel": "prerelease" if prerelease else "stable",
    }


def build_runtime_cli_observations(
    *,
    support: Mapping[str, Any] | None = None,
    profiles: list[dict[str, Any]] | None = None,
    command_probe: Callable[[list[str]], tuple[bool, str | None]] | None = None,
    os_id: str | None = None,
) -> list[dict[str, Any]]:
    contract = dict(support or load_installation_support_contract())
    profile_rows = load_adapter_profiles() if profiles is None else profiles
    profiles_by_id = {str(row.get("id") or ""): row for row in profile_rows}
    adapters_by_id = {str(row["id"]): row for row in contract["adapters"]}
    probe = command_probe or _probe_version_command
    observations: list[dict[str, Any]] = []
    for policy in contract["cli_version_contract"]["entries"]:
        adapter_id = str(policy["adapter_id"])
        profile = profiles_by_id.get(adapter_id, {})
        config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
        configured = config.get("command")
        commands = (
            [str(item) for item in configured]
            if isinstance(configured, list) and configured
            else list(adapters_by_id[adapter_id]["commands"])
        )
        resolved = resolve_provider_cli(
            str(policy["id"]),
            commands,
            os_id=os_id,
        )
        installed, version = (
            probe([resolved, *adapters_by_id[adapter_id]["version_args"]])
            if resolved
            else (False, None)
        )
        observations.append(
            {
                "adapter_id": adapter_id,
                "cli_id": str(policy["id"]),
                "installed": installed,
                "version": version,
                "executable": Path(resolved).name if resolved else None,
                "fingerprint": provider_cli_fingerprint(resolved, os_id=os_id),
            }
        )
    return observations


def audit_provider_cli_versions(
    doctor_report: Mapping[str, Any],
    runtime_observations: list[dict[str, Any]],
    *,
    support: Mapping[str, Any] | None = None,
    catalog_guards: Mapping[str, Mapping[str, Any]] | None = None,
    documentation_guard: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract = dict(support or load_installation_support_contract())
    doctor_by_id = {
        str(row.get("id") or ""): row
        for row in doctor_report.get("adapters", [])
        if isinstance(row, dict)
    }
    runtime_by_id = {
        str(row.get("adapter_id") or ""): row
        for row in runtime_observations
        if isinstance(row, dict)
    }
    guards = dict(catalog_guards or {})
    rows: list[dict[str, Any]] = []
    for policy in contract["cli_version_contract"]["entries"]:
        adapter_id = str(policy["adapter_id"])
        doctor_adapter = doctor_by_id.get(adapter_id, {})
        doctor = doctor_adapter.get("cli")
        doctor = doctor if isinstance(doctor, dict) else {}
        runtime = runtime_by_id.get(adapter_id, {})
        checks = _identity_version_checks(policy, doctor, runtime)
        installed = bool(doctor.get("installed") or runtime.get("installed"))
        optional_absent = (
            not installed and str(policy["requirement"]).startswith("optional_")
        )
        primary_absent = (
            not installed and str(policy["requirement"]) == "primary_option"
        )
        absence_allowed = optional_absent or primary_absent
        catalog = dict(guards.get(adapter_id) or {})
        catalog_ready = catalog.get("ok") is True
        failures = [
            key
            for key, passed in checks.items()
            if passed is False and not absence_allowed
        ]
        rows.append(
            {
                "cli_id": str(policy["id"]),
                "adapter_id": adapter_id,
                "requirement": str(policy["requirement"]),
                "minimum_version": str(policy["minimum_version"]),
                "validated_version": str(policy["validated_version"]),
                "accepted_channels": list(policy["accepted_channels"]),
                "doctor": _redacted_observation(doctor),
                "runtime": _redacted_observation(runtime),
                "checks": checks,
                "identity_version_ok": absence_allowed or not failures,
                "status": (
                    "optional_absent"
                    if optional_absent
                    else "primary_alternative_absent"
                    if primary_absent
                    else "failed"
                    if failures
                    else "catalog_pending"
                    if not catalog_ready
                    else "ready"
                ),
                "failure_codes": failures,
                "catalog_guard": {
                    "contract": str(policy["catalog_guard"]),
                    "status": str(catalog.get("status") or "not_checked"),
                    "ok": catalog_ready,
                },
            }
        )
    primary_rows = [row for row in rows if row["requirement"] == "primary_option"]
    primary_ready = any(
        row["identity_version_ok"] and row["doctor"]["installed"]
        for row in primary_rows
    )
    identity_version_ok = primary_ready and all(
        row["identity_version_ok"] for row in rows
    )
    catalog_ready = all(
        row["catalog_guard"]["ok"]
        for row in rows
        if row["doctor"]["installed"]
    )
    documentation = dict(documentation_guard or {})
    documentation_ready = documentation.get("ok") is True
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract_schema_version": contract["cli_version_contract"][
            "schema_version"
        ],
        "contract_updated_at": contract["cli_version_contract"]["updated_at"],
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "paths_emitted": False,
        },
        "rows": rows,
        "documentation_guard": {
            "ok": documentation_ready,
            "status": str(documentation.get("status") or "not_checked"),
            "checks": dict(documentation.get("checks") or {}),
            "source_sha256": documentation.get("source_sha256"),
        },
        "summary": {
            "identity_version_ok": identity_version_ok,
            "primary_option_ready": primary_ready,
            "catalog_ready": catalog_ready,
            "documentation_ready": documentation_ready,
            "promotion_ready": (
                identity_version_ok and catalog_ready and documentation_ready
            ),
            "failure_count": sum(len(row["failure_codes"]) for row in rows),
        },
    }
    validate_provider_cli_version_audit(report)
    return report


def validate_provider_cli_version_audit(report: Mapping[str, Any]) -> None:
    if set(report) != {
        "schema_version",
        "contract_schema_version",
        "contract_updated_at",
        "scope",
        "rows",
        "documentation_guard",
        "summary",
    }:
        raise ValueError("provider CLI audit fields drift")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("provider CLI audit schema drift")
    if report.get("scope") != {
        "read_only": True,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "paths_emitted": False,
    }:
        raise ValueError("provider CLI audit privacy scope drift")
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("provider CLI audit rows must be non-empty")
    identities = [(row.get("cli_id"), row.get("adapter_id")) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("provider CLI audit identities drift")
    fingerprint_pattern = re.compile(r"^[0-9a-f]{64}$")
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("provider CLI audit row must be an object")
        for surface in ("doctor", "runtime"):
            observation = row.get(surface)
            if not isinstance(observation, dict):
                raise TypeError("provider CLI observation must be an object")
            executable = observation.get("executable")
            if executable and ("/" in str(executable) or "\\" in str(executable)):
                raise ValueError("provider CLI audit must not emit executable paths")
            fingerprint = observation.get("fingerprint")
            if fingerprint is not None and not fingerprint_pattern.fullmatch(
                str(fingerprint)
            ):
                raise ValueError("provider CLI fingerprint is invalid")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise TypeError("provider CLI audit summary must be an object")
    expected_promotion = all(
        summary.get(key) is True
        for key in (
            "identity_version_ok",
            "catalog_ready",
            "documentation_ready",
        )
    )
    if summary.get("promotion_ready") is not expected_promotion:
        raise ValueError("provider CLI promotion summary drift")


def build_documentation_guard(
    guide_text: str,
    *,
    support: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract = dict(support or load_installation_support_contract())
    policy = contract["cli_version_contract"]
    labels = {
        "codex": "Codex",
        "agy": "Antigravity",
        "opencode": "OpenCode",
    }
    checks = {
        "contract_schema_referenced": str(policy["schema_version"]) in guide_text,
        "contract_path_referenced": "config/installation_support.v1.json"
        in guide_text,
        "validated_floor_semantics": "suelos validados" in guide_text,
    }
    for entry in policy["entries"]:
        cli_id = str(entry["id"])
        checks[f"cli_documented:{cli_id}"] = labels.get(cli_id, cli_id) in guide_text
    ok = all(checks.values())
    return {
        "ok": ok,
        "status": "current" if ok else "failed",
        "checks": checks,
        "source_sha256": hashlib.sha256(guide_text.encode("utf-8")).hexdigest(),
    }


def build_catalog_guards(
    runtime_observations: list[dict[str, Any]],
    *,
    drift_receipt: Mapping[str, Any],
    codex_catalog: Mapping[str, Any] | None = None,
    observed_at: datetime | None = None,
    max_age_days: int = 31,
) -> dict[str, dict[str, Any]]:
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    observed_text = str(drift_receipt.get("observed_at") or "")
    try:
        drift_time = datetime.fromisoformat(observed_text.replace("Z", "+00:00"))
        if drift_time.tzinfo is None:
            drift_time = drift_time.replace(tzinfo=timezone.utc)
        age_days = (now - drift_time).total_seconds() / 86400
        fresh = 0 <= age_days <= max_age_days
    except ValueError:
        age_days = None
        fresh = False
    runtime_by_id = {
        str(row.get("adapter_id") or ""): row for row in runtime_observations
    }
    rows = {
        str(row.get("profile_id") or ""): row
        for row in drift_receipt.get("catalogs", [])
        if isinstance(row, dict)
    }
    guards: dict[str, dict[str, Any]] = {}
    for adapter_id in ("antigravity_subscription", "opencode_zen_free"):
        runtime = runtime_by_id.get(adapter_id, {})
        catalog = rows.get(adapter_id, {})
        runtime_version = parse_cli_version(runtime.get("version"))
        catalog_version = parse_cli_version(catalog.get("cli_version"))
        checks = {
            "evidence_fresh": fresh,
            "catalog_current": catalog.get("status") == "current",
            "coverage_ok": catalog.get("coverage_ok") is True,
            "cli_version_matches": runtime_version is not None
            and catalog_version is not None
            and runtime_version["normalized"] == catalog_version["normalized"],
        }
        guards[adapter_id] = {
            "ok": all(checks.values()),
            "status": "current" if all(checks.values()) else "failed",
            "checks": checks,
            "age_days": round(age_days, 3) if age_days is not None else None,
            "source_sha256": _mapping_sha256(drift_receipt),
        }
    codex = dict(codex_catalog or codex_catalog_snapshot())
    runtime_codex = parse_cli_version(
        runtime_by_id.get("codex_subscription", {}).get("version")
    )
    installed = parse_cli_version(codex.get("installed_version"))
    catalog_client = parse_cli_version(codex.get("catalog_client_version"))
    codex_checks = {
        "catalog_current": codex.get("status") == "current",
        "catalog_has_models": bool(codex.get("models")),
        "runtime_matches_installed_core": runtime_codex is not None
        and installed is not None
        and runtime_codex["core"] == installed["core"],
        "catalog_not_newer_than_runtime": runtime_codex is not None
        and catalog_client is not None
        and catalog_client["core"] <= runtime_codex["core"],
    }
    guards["codex_subscription"] = {
        "ok": all(codex_checks.values()),
        "status": "current" if all(codex_checks.values()) else "failed",
        "checks": codex_checks,
        "age_days": 0,
        "source_sha256": _mapping_sha256(codex),
    }
    return guards


def _identity_version_checks(
    policy: Mapping[str, Any],
    doctor: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, bool]:
    doctor_installed = bool(doctor.get("installed"))
    runtime_installed = bool(runtime.get("installed"))
    doctor_version = parse_cli_version(doctor.get("version"))
    runtime_version = parse_cli_version(runtime.get("version"))
    minimum = parse_cli_version(policy.get("minimum_version"))
    observed = runtime_version or doctor_version
    declared = set(str(item) for item in policy.get("declared_prereleases", []))
    channels = set(str(item) for item in policy.get("accepted_channels", []))
    return {
        "installation_matches": doctor_installed == runtime_installed,
        "version_observable": (not doctor_installed and not runtime_installed)
        or (doctor_version is not None and runtime_version is not None),
        "version_matches": (not doctor_installed and not runtime_installed)
        or (
            doctor_version is not None
            and runtime_version is not None
            and doctor_version["normalized"] == runtime_version["normalized"]
        ),
        "minimum_version": observed is not None
        and minimum is not None
        and _version_at_least(observed, minimum),
        "channel_allowed": observed is not None
        and observed["channel"] in channels,
        "prerelease_explicit": observed is not None
        and (
            observed["channel"] != "prerelease"
            or observed["normalized"] in declared
        ),
        "executable_matches": (not doctor_installed and not runtime_installed)
        or (
            bool(doctor.get("executable"))
            and doctor.get("executable") == runtime.get("executable")
        ),
        "fingerprint_observable": (not doctor_installed and not runtime_installed)
        or (
            bool(doctor.get("fingerprint"))
            and bool(runtime.get("fingerprint"))
        ),
        "fingerprint_matches": (not doctor_installed and not runtime_installed)
        or (
            bool(doctor.get("fingerprint"))
            and doctor.get("fingerprint") == runtime.get("fingerprint")
        ),
    }


def _version_at_least(
    observed: Mapping[str, Any],
    minimum: Mapping[str, Any],
) -> bool:
    if observed["core"] != minimum["core"]:
        return observed["core"] > minimum["core"]
    if minimum["prerelease"] is None:
        return observed["prerelease"] is None
    if observed["prerelease"] is None:
        return True
    return _prerelease_key(str(observed["prerelease"])) >= _prerelease_key(
        str(minimum["prerelease"])
    )


def _prerelease_key(value: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(item)) if item.isdigit() else (1, item.casefold())
        for item in value.split(".")
    )


def _redacted_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "installed": bool(value.get("installed")),
        "version": value.get("version"),
        "executable": value.get("executable"),
        "fingerprint": value.get("fingerprint"),
    }


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
