from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Mapping

from aiteam.installation_support import load_installation_support_contract
from aiteam.provider_cli_version_audit import (
    audit_provider_cli_versions,
    validate_provider_cli_version_audit,
)

SCHEMA_VERSION = "provider_cli_update_acceptance_v1"

_CURRENT = {
    "codex_subscription": ("codex", "codex-cli 0.146.0-alpha.6", "codex.cmd"),
    "antigravity_subscription": ("agy", "1.1.8", "agy.cmd"),
    "opencode_zen_free": ("opencode", "1.18.4", "opencode.cmd"),
}


def build_provider_cli_update_acceptance(
    *,
    support: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Exercise clean/update equivalence and fail-closed upgrade edge cases."""
    contract = deepcopy(dict(support or load_installation_support_contract()))
    doctor, runtime = _observations()
    clean = _audit(contract, doctor, runtime)
    updated = _audit(contract, deepcopy(doctor), deepcopy(runtime))
    clean_matrix = _canonical_matrix(clean)
    updated_matrix = _canonical_matrix(updated)

    pre_update_doctor, pre_update_runtime = _observations()
    pre_update_doctor["adapters"][1]["cli"].update(
        version="1.1.7",
        fingerprint="f" * 64,
    )
    pre_update_runtime[1]["version"] = "1.1.7"
    pre_update = _audit(contract, pre_update_doctor, pre_update_runtime)
    pre_update_codes = sorted(
        {
            code
            for row in pre_update["rows"]
            for code in row["failure_codes"]
        }
    )

    duplicate_doctor, duplicate_runtime = _observations()
    duplicate_doctor["adapters"][1]["cli"]["fingerprint"] = "f" * 64
    duplicate = _audit(contract, duplicate_doctor, duplicate_runtime)

    old_doctor, old_runtime = _observations()
    old_doctor["adapters"][1]["cli"]["version"] = "1.1.7"
    old_runtime[1]["version"] = "1.1.7"
    upgrade_required = _audit(contract, old_doctor, old_runtime)

    implicit_doctor, implicit_runtime = _observations()
    implicit_doctor["adapters"][0]["cli"]["version"] = (
        "codex-cli 0.146.0-alpha.7"
    )
    implicit_runtime[0]["version"] = "codex-cli 0.146.0-alpha.7"
    implicit_prerelease = _audit(contract, implicit_doctor, implicit_runtime)

    optional_doctor, optional_runtime = _observations()
    optional_doctor["adapters"][2]["cli"].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )
    optional_runtime[2].update(
        installed=False,
        version=None,
        executable=None,
        fingerprint=None,
    )
    optional_absent = _audit(contract, optional_doctor, optional_runtime)

    documentation_drift = _audit(
        contract,
        deepcopy(doctor),
        deepcopy(runtime),
        documentation_ok=False,
    )
    catalog_drift = _audit(
        contract,
        deepcopy(doctor),
        deepcopy(runtime),
        catalog_ok=False,
    )

    negative_cases = [
        _case(
            "duplicate_binary",
            duplicate,
            expected_codes={"fingerprint_matches"},
        ),
        _case(
            "upgrade_required",
            upgrade_required,
            expected_codes={"minimum_version"},
        ),
        _case(
            "implicit_prerelease",
            implicit_prerelease,
            expected_codes={"prerelease_explicit"},
        ),
        _case(
            "documentation_drift",
            documentation_drift,
            expected_summary_gate="documentation_ready",
        ),
        _case(
            "catalog_matrix_drift",
            catalog_drift,
            expected_summary_gate="catalog_ready",
        ),
    ]
    optional_row = next(
        row
        for row in optional_absent["rows"]
        if row["adapter_id"] == "opencode_zen_free"
    )
    optional_case = {
        "id": "optional_absent",
        "accepted": (
            optional_row["status"] == "optional_absent"
            and optional_absent["summary"]["promotion_ready"] is True
        ),
        "status": optional_row["status"],
        "promotion_ready": optional_absent["summary"]["promotion_ready"],
    }
    equivalent = (
        clean["summary"]["promotion_ready"] is True
        and updated["summary"]["promotion_ready"] is True
        and clean_matrix == updated_matrix
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract_schema_version": contract["cli_version_contract"][
            "schema_version"
        ],
        "scope": {
            "fixtures_only": True,
            "global_installations_mutated": False,
            "secrets_read": False,
            "login_attempted": False,
            "inference_attempted": False,
            "paths_emitted": False,
        },
        "sources": {
            "clean_clone": {
                "state": "current_contract",
                "matrix_sha256": _mapping_sha256(clean_matrix),
                "promotion_ready": clean["summary"]["promotion_ready"],
            },
            "existing_checkout_after_fast_forward": {
                "state": "current_contract",
                "matrix_sha256": _mapping_sha256(updated_matrix),
                "promotion_ready": updated["summary"]["promotion_ready"],
                "pre_update": {
                    "promotion_ready": pre_update["summary"][
                        "promotion_ready"
                    ],
                    "failure_codes": pre_update_codes,
                    "upgrade_required": "minimum_version" in pre_update_codes,
                    "duplicate_detected": (
                        "fingerprint_matches" in pre_update_codes
                    ),
                },
            },
        },
        "equivalence": {
            "matrix_equal": clean_matrix == updated_matrix,
            "promotion_equal": (
                clean["summary"]["promotion_ready"]
                == updated["summary"]["promotion_ready"]
            ),
            "ok": equivalent,
        },
        "negative_cases": negative_cases,
        "optional_case": optional_case,
        "summary": {
            "equivalence_ready": equivalent,
            "negative_cases_ready": all(
                row["blocked_as_expected"] for row in negative_cases
            ),
            "optional_absence_ready": optional_case["accepted"],
            "promotion_ready": (
                equivalent
                and all(row["blocked_as_expected"] for row in negative_cases)
                and optional_case["accepted"]
            ),
        },
    }
    validate_provider_cli_update_acceptance(report)
    return report


def validate_provider_cli_update_acceptance(report: Mapping[str, Any]) -> None:
    if set(report) != {
        "schema_version",
        "contract_schema_version",
        "scope",
        "sources",
        "equivalence",
        "negative_cases",
        "optional_case",
        "summary",
    }:
        raise ValueError("provider CLI update acceptance fields drift")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("provider CLI update acceptance schema drift")
    if report.get("scope") != {
        "fixtures_only": True,
        "global_installations_mutated": False,
        "secrets_read": False,
        "login_attempted": False,
        "inference_attempted": False,
        "paths_emitted": False,
    }:
        raise ValueError("provider CLI update acceptance scope drift")
    sources = report.get("sources")
    if not isinstance(sources, dict) or set(sources) != {
        "clean_clone",
        "existing_checkout_after_fast_forward",
    }:
        raise ValueError("provider CLI update acceptance sources drift")
    for source in sources.values():
        digest = source.get("matrix_sha256")
        if not isinstance(digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}",
            digest,
        ):
            raise ValueError("provider CLI update matrix digest invalid")
    pre_update = sources["existing_checkout_after_fast_forward"].get(
        "pre_update"
    )
    if not isinstance(pre_update, dict) or not (
        pre_update.get("promotion_ready") is False
        and pre_update.get("upgrade_required") is True
        and pre_update.get("duplicate_detected") is True
    ):
        raise ValueError("provider CLI pre-update evidence drift")
    cases = report.get("negative_cases")
    if not isinstance(cases, list) or {row.get("id") for row in cases} != {
        "duplicate_binary",
        "upgrade_required",
        "implicit_prerelease",
        "documentation_drift",
        "catalog_matrix_drift",
    }:
        raise ValueError("provider CLI update negative cases drift")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise TypeError("provider CLI update summary must be an object")
    expected = all(
        summary.get(key) is True
        for key in (
            "equivalence_ready",
            "negative_cases_ready",
            "optional_absence_ready",
        )
    )
    if summary.get("promotion_ready") is not expected:
        raise ValueError("provider CLI update promotion summary drift")


def _observations() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doctor_rows: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    for index, (adapter_id, values) in enumerate(_CURRENT.items(), start=1):
        cli_id, version, executable = values
        fingerprint = f"{index:064x}"
        observation = {
            "installed": True,
            "version": version,
            "executable": executable,
            "fingerprint": fingerprint,
        }
        doctor_rows.append(
            {
                "id": adapter_id,
                "cli": {
                    "id": cli_id,
                    **observation,
                    "source": "fixture",
                },
            }
        )
        runtime_rows.append(
            {
                "adapter_id": adapter_id,
                "cli_id": cli_id,
                **observation,
            }
        )
    return {"adapters": doctor_rows}, runtime_rows


def _audit(
    support: Mapping[str, Any],
    doctor: dict[str, Any],
    runtime: list[dict[str, Any]],
    *,
    documentation_ok: bool = True,
    catalog_ok: bool = True,
) -> dict[str, Any]:
    guards = {
        adapter_id: {
            "ok": catalog_ok,
            "status": "current" if catalog_ok else "failed",
        }
        for adapter_id in _CURRENT
    }
    report = audit_provider_cli_versions(
        doctor,
        runtime,
        support=support,
        catalog_guards=guards,
        documentation_guard={
            "ok": documentation_ok,
            "status": "current" if documentation_ok else "failed",
            "checks": {"fixture_contract_current": documentation_ok},
            "source_sha256": "d" * 64,
        },
    )
    validate_provider_cli_version_audit(report)
    return report


def _canonical_matrix(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rows": [
            {
                "cli_id": row["cli_id"],
                "adapter_id": row["adapter_id"],
                "requirement": row["requirement"],
                "version": row["runtime"]["version"],
                "executable": row["runtime"]["executable"],
                "fingerprint": row["runtime"]["fingerprint"],
                "status": row["status"],
            }
            for row in report["rows"]
        ],
        "summary": dict(report["summary"]),
    }


def _case(
    case_id: str,
    report: Mapping[str, Any],
    *,
    expected_codes: set[str] | None = None,
    expected_summary_gate: str | None = None,
) -> dict[str, Any]:
    observed_codes = sorted(
        {
            code
            for row in report["rows"]
            for code in row["failure_codes"]
        }
    )
    code_match = not expected_codes or expected_codes.issubset(observed_codes)
    gate_match = (
        expected_summary_gate is None
        or report["summary"].get(expected_summary_gate) is False
    )
    return {
        "id": case_id,
        "blocked_as_expected": (
            report["summary"]["promotion_ready"] is False
            and code_match
            and gate_match
        ),
        "failure_codes": observed_codes,
        "failed_summary_gate": expected_summary_gate,
        "promotion_ready": report["summary"]["promotion_ready"],
    }


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
