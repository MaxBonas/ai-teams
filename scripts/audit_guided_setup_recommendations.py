from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiteam.guided_setup_recommendations import (
    SCHEMA_VERSION,
    build_progressive_recommendations,
)

AUDIT_VERSION = "guided_setup_recommendations_audit_v1"


def _coverage() -> dict[str, Any]:
    requirement = {
        "role": "quorum_auditor",
        "status": "diversity_gap",
        "missing_count": 0,
        "perspective_count": 1,
        "capacity_pool_count": 1,
    }
    return {
        "schema_version": "guided_setup_coverage_v1",
        "recommended_profile": "lead_quorum",
        "recommended_profile_ready": False,
        "profiles": {
            "solo_lead": {"ready": False, "requirements": []},
            "lead_quorum": {
                "ready": False,
                "requirements": [requirement],
            },
            "full_team": {
                "ready": False,
                "requirements": [
                    {
                        **requirement,
                        "role": "engineer",
                        "status": "missing",
                        "missing_count": 1,
                    }
                ],
            },
        },
        "roles": {
            "team_lead": {"candidates": []},
            "worker": {"candidates": []},
        },
    }


def _adapter(profile_id: str, state: str) -> dict[str, Any]:
    return {
        "id": profile_id,
        "state": state,
        "primary_candidate": True,
        "stages": {
            "installation": "passed",
            "version": "passed",
            "authentication": "failed" if state == "blocked" else "passed",
            "catalog": "not_checked",
            "health": "not_checked",
            "contract": "not_checked",
        },
    }


def _preparation(*adapters: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_preparation_v1",
        "adapters": list(adapters),
    }


def build_audit(_repo_root: Path) -> dict[str, Any]:
    base = build_progressive_recommendations(
        _coverage(),
        _preparation(
            _adapter("codex_subscription", "blocked"),
            _adapter("antigravity_subscription", "unverified"),
        ),
    )
    lead_action = base["next_action"]
    ready_adapter = build_progressive_recommendations(
        _coverage(),
        _preparation(_adapter("codex_subscription", "ready")),
    )
    no_channel = build_progressive_recommendations(
        _coverage(),
        _preparation(),
    )
    expanded = deepcopy(_coverage())
    expanded["profiles"]["solo_lead"]["ready"] = True
    expanded["roles"]["team_lead"]["candidates"] = [
        {
            "candidate_id": "codex:lead",
            "profile_id": "codex_subscription",
            "economics": {"class": "zero_marginal"},
        }
    ]
    expanded["roles"]["worker"]["candidates"] = [
        {
            "candidate_id": "local:worker",
            "profile_id": "ollama",
            "economics": {"class": "zero_marginal"},
        }
    ]
    later = build_progressive_recommendations(
        expanded,
        _preparation(_adapter("codex_subscription", "ready")),
    )
    checks = {
        "schema_is_versioned": base["schema_version"] == SCHEMA_VERSION,
        "phase_order_is_stable": base["policy"]["order"] == [
            "minimum_lead",
            "quorum_diversity",
            "full_team",
            "economic_workers",
        ],
        "one_minimum_adapter_is_actionable": (
            lead_action["profile_id"] == "codex_subscription"
            and lead_action["alternative_profile_ids"]
            == ["antigravity_subscription"]
        ),
        "passed_install_is_not_recommended": (
            "installation" not in lead_action["pending_stages"]
        ),
        "ready_adapter_is_never_reinstalled": (
            ready_adapter["next_action"]["code"]
            == "restore_lead_model_eligibility"
        ),
        "missing_channel_requires_owner_choice": (
            no_channel["next_action"]["code"] == "choose_lead_channel"
        ),
        "quorum_gap_follows_minimum_lead": (
            later["next_action"]["code"] == "expand_quorum_diversity"
        ),
        "recommended_quorum_is_required": (
            later["next_action"]["required"] is True
        ),
        "economic_worker_is_optional_and_last": (
            later["actions"][-1]["code"] == "consider_economic_worker"
            and later["actions"][-1]["required"] is False
        ),
        "no_silent_mutation_or_install": (
            base["policy"]["automatic_install"] is False
            and base["policy"]["automatic_default_change"] is False
            and base["policy"]["configure_everything"] is False
        ),
    }
    report = {
        "schema_version": AUDIT_VERSION,
        "contract_version": SCHEMA_VERSION,
        "scope": {
            "fixture_only": True,
            "defaults_mutated": False,
            "user_configuration_mutated": False,
            "installations_attempted": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "summary": {
            "recommendations_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup recommendations audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup recommendations audit coverage drift")
    expected = {
        "recommendations_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup recommendations audit summary drift")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = build_audit(Path(__file__).resolve().parents[1])
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    if args.json or not args.output:
        print(payload, end="")
    return 0 if report["summary"]["recommendations_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
