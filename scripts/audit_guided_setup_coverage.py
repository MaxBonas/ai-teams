from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from aiteam.guided_setup_coverage import (
    PROFILE_REQUIREMENTS,
    SCHEMA_VERSION,
    build_guided_setup_coverage,
)

AUDIT_VERSION = "guided_setup_coverage_audit_v1"


def _candidate(
    role: str,
    name: str,
    profile: str,
    perspective: str,
    pool: str,
    *,
    eligible: bool = True,
    channel: str = "subscription",
) -> dict[str, Any]:
    return {
        "candidate_id": name,
        "identity": {
            "profile_id": profile,
            "model_id": f"model-{name}",
            "provider_org": perspective,
            "channel": channel,
            "perspective_key": perspective,
            "capacity_pool": pool,
        },
        "model_metadata": {
            "tier": "premium",
            "caps": ["reasoning"],
            "price_note": "fixture",
        },
        "owner_selectable": True,
        "rank": 1,
        "contextual_compatibility": {"allowed": True, "code": "compatible"},
        "selection_score": {
            "score": 90,
            "auto_eligible": eligible,
            "hard_gates": {"calibrated": {"passed": eligible}},
        },
    }


def _selection(role: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": rows,
    }


def _fixture() -> dict[str, dict[str, Any]]:
    return {
        "team_lead": _selection(
            "team_lead",
            [_candidate("team_lead", "lead", "codex", "openai", "chatgpt")],
        ),
        "quorum_auditor": _selection(
            "quorum_auditor",
            [
                _candidate(
                    "quorum_auditor",
                    "audit-a",
                    "codex",
                    "openai",
                    "chatgpt",
                ),
                _candidate(
                    "quorum_auditor",
                    "audit-b",
                    "antigravity",
                    "google",
                    "google-subscription",
                ),
            ],
        ),
        "engineer": _selection(
            "engineer",
            [_candidate("engineer", "engineer", "codex", "openai", "chatgpt")],
        ),
        "reviewer": _selection(
            "reviewer",
            [
                _candidate(
                    "reviewer",
                    "reviewer",
                    "antigravity",
                    "google",
                    "google-subscription",
                )
            ],
        ),
    }


def build_audit(_repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    base = build_guided_setup_coverage(_fixture())
    checks["canonical_profile_requirements"] = {
        key: [(row["role"], row["count"]) for row in value]
        for key, value in PROFILE_REQUIREMENTS.items()
    } == {
        "solo_lead": [("team_lead", 1)],
        "lead_quorum": [("team_lead", 1), ("quorum_auditor", 2)],
        "full_team": [("team_lead", 1), ("engineer", 1), ("reviewer", 1)],
    }
    checks["complete_fixture_covers_three_profiles"] = all(
        row["ready"] for row in base["profiles"].values()
    )
    uncalibrated = _fixture()
    uncalibrated["team_lead"]["candidates"][0]["selection_score"][
        "auto_eligible"
    ] = False
    blocked = build_guided_setup_coverage(uncalibrated)
    checks["manual_or_uncalibrated_never_covers"] = (
        blocked["roles"]["team_lead"]["candidate_count"] == 1
        and blocked["roles"]["team_lead"]["eligible_count"] == 0
    )
    same = _fixture()
    same["quorum_auditor"]["candidates"][1]["identity"].update(
        {"perspective_key": "openai", "capacity_pool": "chatgpt"}
    )
    diversity = build_guided_setup_coverage(same)
    checks["quorum_requires_real_diversity"] = (
        diversity["profiles"]["lead_quorum"]["requirements"][1]["status"]
        == "diversity_gap"
    )
    filtered = build_guided_setup_coverage(
        _fixture(),
        ready_profile_ids={"codex"},
    )
    checks["unprepared_adapters_do_not_cover"] = (
        filtered["profiles"]["solo_lead"]["ready"] is True
        and filtered["profiles"]["full_team"]["ready"] is False
    )
    local_fixture = _fixture()
    local_fixture["engineer"]["candidates"][0]["identity"]["channel"] = "local"
    local = build_guided_setup_coverage(local_fixture)
    checks["local_marginal_cost_is_zero"] = (
        local["roles"]["engineer"]["candidates"][0]["economics"][
            "marginal_cost"
        ]
        == "zero"
    )
    checks["subscription_marginal_cost_is_zero"] = (
        base["roles"]["team_lead"]["candidates"][0]["economics"][
            "marginal_cost"
        ]
        == "zero"
    )
    api_fixture = _fixture()
    api_fixture["engineer"]["candidates"][0]["identity"]["channel"] = "api"
    api = build_guided_setup_coverage(api_fixture)
    checks["api_cost_remains_metered"] = (
        api["roles"]["engineer"]["candidates"][0]["economics"]["class"]
        == "metered"
    )
    missing = deepcopy(_fixture())
    missing.pop("reviewer")
    gaps = build_guided_setup_coverage(missing)
    checks["missing_roles_are_explicit_blockers"] = (
        gaps["profiles"]["full_team"]["blockers"] == ["reviewer:missing"]
    )
    checks["canonical_selector_policy_is_explicit"] = base["policy"] == {
        "source": "model_selection_v1",
        "eligibility": "candidate_is_automation_eligible",
        "discovery_grants_coverage": False,
        "manual_selection_grants_coverage": False,
        "quorum_requires_distinct_perspectives_and_capacity_pools": True,
        "local_marginal_cost": "zero",
    }
    report = {
        "schema_version": AUDIT_VERSION,
        "contract_version": SCHEMA_VERSION,
        "scope": {
            "fixture_only": True,
            "defaults_mutated": False,
            "user_configuration_mutated": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "summary": {
            "coverage_contract_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup coverage audit schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup coverage audit coverage drift")
    expected = {
        "coverage_contract_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup coverage audit summary drift")


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
    return 0 if report["summary"]["coverage_contract_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
