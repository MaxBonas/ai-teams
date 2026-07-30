"""Audita el contrato puro y proporcional del preflight de proyecto."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aiteam.guided_setup_needs import build_needs_submission
from aiteam.guided_setup_project_preflight import (
    SCHEMA_VERSION,
    build_project_preflight,
    validate_project_preflight,
)

AUDIT_VERSION = "guided_setup_project_preflight_contract_audit_v1"
EXPECTED_CHECKS = frozenset(
    {
        "research_avoids_software_tests",
        "operations_avoids_software_tests",
        "software_requires_exact_smoke",
        "mixed_without_software_avoids_tests",
        "mixed_with_software_requires_smoke",
        "adapter_contract_is_a_hard_gate",
        "detected_toolchain_is_a_hard_gate",
        "unsafe_path_is_a_hard_gate",
        "remote_or_quota_fixture_is_rejected",
        "preflight_tampering_is_rejected",
    }
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _needs(kind: str) -> dict[str, Any]:
    return build_needs_submission(
        "project_setup",
        {
            "goal": f"Fixture hermético para objetivo {kind}",
            "objective_kind": kind,
            "languages": ["TypeScript"],
            "data_sensitivity": "internal",
            "budget_priority": "balanced",
            "subscriptions": ["codex"],
            "api_access": "not_willing",
            "local_models": "not_wanted",
            "autonomy": "supervised",
            "criticality": "medium",
            "team_preference": "solo_lead",
            "external_tools": "optional",
        },
    )


def _proposal(
    needs: dict[str, Any],
    *,
    detected: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_project_proposal_v1",
        "proposal_hash": "a" * 64,
        "project": {
            "mode": "create",
            "objective": needs["answers"]["goal"],
            "objective_kind": needs["answers"]["objective_kind"],
        },
        "ecosystems": {
            "detected_ids": ["javascript_typescript"] if detected else [],
            "scan_truncated": False,
        },
        "team": {
            "assignments": [
                {
                    "candidate": {
                        "profile_id": "codex_subscription",
                        "model_id": "gpt-fixture",
                    }
                }
            ]
        },
        "save_gate": {"allowed": True},
    }


def _preparation(*, ready: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_preparation_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
            "installations_attempted": False,
            "terms_accepted": False,
        },
        "runtimes": [{"id": "python", "state": "ready"}],
        "adapters": [
            {
                "id": "codex_subscription",
                "state": "ready" if ready else "unverified",
                "stages": {"contract": "passed" if ready else "not_checked"},
            }
        ],
    }


def _inventory(*, toolchain_ready: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "machine_doctor_v1",
        "scope": {
            "read_only": True,
            "secrets_read": False,
            "credentials_probed": False,
        },
        "toolchains": [
            {
                "id": "javascript_typescript",
                "binary_installed": toolchain_ready,
            }
        ],
    }


def _path(*, confined: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_project_path_observation_v1",
        "mode": "create",
        "target_exists": False,
        "target_is_dir": False,
        "target_readable": False,
        "target_writable": False,
        "parent_exists": True,
        "parent_writable": True,
        "confined_to_projects_root": confined,
    }


def _smoke(**overrides: Any) -> dict[str, Any]:
    value = {
        "schema_version": "guided_setup_fixture_evidence_v1",
        "kind": "software_toolchain_smoke",
        "status": "passed",
        "receipt_ref": "benchmarks/results/guided_setup/software-smoke.json",
        "commands_executed": True,
        "tests_executed": True,
        "remote_calls": False,
        "quota_consumed": False,
        "workspace_mutated": False,
    }
    value.update(overrides)
    return value


def _build(
    kind: str,
    *,
    detected: bool = False,
    adapter_ready: bool = True,
    toolchain_ready: bool = True,
    confined: bool = True,
    fixtures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    needs = _needs(kind)
    return build_project_preflight(
        needs,
        _proposal(needs, detected=detected),
        _preparation(ready=adapter_ready),
        _inventory(toolchain_ready=toolchain_ready),
        _path(confined=confined),
        fixture_evidence=fixtures or [],
    )


def _rejects(call: Any, code: str) -> bool:
    try:
        call()
    except (TypeError, ValueError) as exc:
        return code in str(exc)
    return False


def build_audit(_repo_root: Path) -> dict[str, Any]:
    research = _build("research")
    operations = _build("operations")
    software_blocked = _build("software")
    software_ready = _build("software", fixtures=[_smoke()])
    mixed_without_surface = _build("mixed")
    mixed_with_surface = _build("mixed", detected=True)
    adapter_blocked = _build("research", adapter_ready=False)
    toolchain_blocked = _build(
        "software",
        detected=True,
        toolchain_ready=False,
        fixtures=[_smoke()],
    )
    path_blocked = _build("research", confined=False)

    tampered = deepcopy(research)
    tampered["gates"][0]["status"] = "blocked"
    checks = {
        "research_avoids_software_tests": (
            research["summary"]["go"]
            and research["fixture_policy"]["kind"]
            == "research_evidence_contract"
            and research["scope"]["tests_executed"] is False
        ),
        "operations_avoids_software_tests": (
            operations["summary"]["go"]
            and operations["fixture_policy"]["kind"]
            == "operations_receipt_contract"
            and operations["scope"]["tests_executed"] is False
        ),
        "software_requires_exact_smoke": (
            software_blocked["summary"]["next_action"]
            == "run_proportional_fixture"
            and software_ready["summary"]["go"]
        ),
        "mixed_without_software_avoids_tests": (
            mixed_without_surface["summary"]["go"]
            and mixed_without_surface["scope"]["tests_executed"] is False
        ),
        "mixed_with_software_requires_smoke": (
            mixed_with_surface["summary"]["go"] is False
            and mixed_with_surface["fixture_policy"][
                "software_fixture_required"
            ]
            is True
        ),
        "adapter_contract_is_a_hard_gate": (
            adapter_blocked["summary"]["blockers"][0]["gate"]
            == "selected_adapters"
        ),
        "detected_toolchain_is_a_hard_gate": (
            toolchain_blocked["summary"]["blockers"][0]["gate"]
            == "project_toolchains"
        ),
        "unsafe_path_is_a_hard_gate": (
            path_blocked["summary"]["blockers"][0]["gate"] == "project_path"
        ),
        "remote_or_quota_fixture_is_rejected": _rejects(
            lambda: _build(
                "software",
                fixtures=[_smoke(remote_calls=True, quota_consumed=True)],
            ),
            "fixture_remote_side_effect",
        ),
        "preflight_tampering_is_rejected": _rejects(
            lambda: validate_project_preflight(tampered),
            "guided_setup_preflight_summary_drift",
        ),
    }
    evidence = {
        "contract_version": SCHEMA_VERSION,
        "objective_kinds": ["mixed", "operations", "research", "software"],
        "gate_ids": [row["id"] for row in research["gates"]],
        "research_hash": research["preflight_hash"],
        "software_blocked_hash": software_blocked["preflight_hash"],
        "software_ready_hash": software_ready["preflight_hash"],
        "scope": research["scope"],
    }
    report = {
        "schema_version": AUDIT_VERSION,
        "scope": {
            "fixture_only": True,
            "user_projects_mutated": False,
            "database_mutated": False,
            "commands_executed": False,
            "project_tests_executed": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "evidence": evidence,
        "evidence_hash": _hash(evidence),
        "summary": {
            "preflight_contract_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup project preflight audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or set(checks) != EXPECTED_CHECKS:
        raise ValueError("guided setup project preflight audit matrix drift")
    evidence = report.get("evidence")
    if (
        not isinstance(evidence, dict)
        or report.get("evidence_hash") != _hash(evidence)
    ):
        raise ValueError("guided setup project preflight audit evidence drift")
    expected_summary = {
        "preflight_contract_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected_summary:
        raise ValueError("guided setup project preflight audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(REPO_ROOT)
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    ready = report["summary"]["preflight_contract_ready"]
    return 0 if ready or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
