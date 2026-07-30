from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from aiteam.guided_setup_coverage import build_guided_setup_coverage
from aiteam.guided_setup_recommendations import (
    build_progressive_recommendations,
)
from aiteam.model_selection import candidate_is_automation_eligible

AUDIT_VERSION = "guided_setup_coverage_acceptance_v1"


def _candidate(
    name: str,
    *,
    profile: str,
    perspective: str,
    pool: str,
    channel: str = "subscription",
    automatic: bool = True,
    owner_selectable: bool = True,
    reason: str | None = None,
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
            "caps": ["reasoning", "structured_output"],
        },
        "owner_selectable": owner_selectable,
        "disabled_reason": reason,
        "rank": 1,
        "contextual_compatibility": {
            "allowed": True,
            "code": "compatible",
        },
        "selection_score": {
            "score": 90,
            "auto_eligible": automatic,
            "auto_ineligible_reasons": [reason] if reason else [],
            "hard_gates": {
                "adapter_green": {"passed": True},
                "calibrated": {"passed": automatic},
            },
        },
    }


def _selection(role: str, *candidates: dict[str, Any]) -> dict[str, Any]:
    return {
        "selection_version": "model_selection_v1",
        "canonical_role": role,
        "candidates": list(candidates),
    }


def _fixture() -> dict[str, dict[str, Any]]:
    return {
        "team_lead": _selection(
            "team_lead",
            _candidate(
                "lead",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
        ),
        "quorum_auditor": _selection(
            "quorum_auditor",
            _candidate(
                "audit-openai",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
            _candidate(
                "audit-google",
                profile="antigravity",
                perspective="google",
                pool="antigravity",
            ),
        ),
        "engineer": _selection(
            "engineer",
            _candidate(
                "engineer",
                profile="codex",
                perspective="openai",
                pool="codex",
            ),
        ),
        "reviewer": _selection(
            "reviewer",
            _candidate(
                "reviewer",
                profile="antigravity",
                perspective="google",
                pool="antigravity",
            ),
        ),
        "worker": _selection(
            "worker",
            _candidate(
                "worker-local",
                profile="ollama",
                perspective="local",
                pool="local",
                channel="local",
            ),
        ),
    }


def _preparation(*ready_ids: str) -> dict[str, Any]:
    return {
        "schema_version": "guided_setup_preparation_v1",
        "adapters": [
            {
                "id": profile_id,
                "state": "ready",
                "primary_candidate": profile_id == "codex",
                "stages": {},
            }
            for profile_id in ready_ids
        ],
    }


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_audit(_repo_root: Path) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    no_lead = _fixture()
    no_lead.pop("team_lead")
    no_lead_result = build_guided_setup_coverage(no_lead)
    checks["no_lead_blocks_minimum_route"] = (
        no_lead_result["profiles"]["solo_lead"]["ready"] is False
    )

    lead_only = build_guided_setup_coverage(
        {"team_lead": _fixture()["team_lead"]},
        ready_profile_ids={"codex"},
    )
    checks["single_lead_covers_only_minimum"] = (
        lead_only["profiles"]["solo_lead"]["ready"] is True
        and lead_only["profiles"]["lead_quorum"]["ready"] is False
        and lead_only["profiles"]["full_team"]["ready"] is False
    )

    same_quorum = _fixture()
    same_quorum["quorum_auditor"]["candidates"][1]["identity"].update(
        {"perspective_key": "openai", "capacity_pool": "codex"}
    )
    quorum_result = build_guided_setup_coverage(same_quorum)
    checks["quorum_without_diversity_is_blocked"] = (
        quorum_result["profiles"]["lead_quorum"]["requirements"][1]["status"]
        == "diversity_gap"
    )

    partial_team = _fixture()
    partial_team.pop("reviewer")
    partial_result = build_guided_setup_coverage(partial_team)
    checks["partial_full_team_names_missing_role"] = (
        partial_result["profiles"]["full_team"]["blockers"]
        == ["reviewer:missing"]
    )

    complete_result = build_guided_setup_coverage(_fixture())
    checks["complete_full_team_is_covered"] = (
        complete_result["profiles"]["full_team"]["ready"] is True
    )

    local_worker = complete_result["roles"]["worker"]["candidates"][0]
    checks["eligible_local_worker_preserves_zero_cost"] = (
        local_worker["economics"]["class"] == "zero_marginal"
        and complete_result["profiles"]["solo_lead"]["ready"] is True
    )

    limited = _fixture()
    limited["reviewer"] = _selection(
        "reviewer",
        _candidate(
            "api-limited",
            profile="gemini_api",
            perspective="google",
            pool="gemini-api",
            channel="api",
            automatic=False,
            reason="capacity_limit_reached",
        ),
    )
    limited_result = build_guided_setup_coverage(limited)
    checks["rate_limited_api_stays_visible_but_excluded"] = (
        limited_result["roles"]["reviewer"]["eligible_count"] == 0
        and limited_result["roles"]["reviewer"]["excluded_candidates"][0][
            "exclusion_reasons"
        ]
        == ["capacity_limit_reached"]
    )

    override = _fixture()
    override["team_lead"] = _selection(
        "team_lead",
        _candidate(
            "owner-override",
            profile="manual",
            perspective="owner",
            pool="manual",
            automatic=False,
            owner_selectable=True,
            reason="calibration_missing",
        ),
    )
    override_result = build_guided_setup_coverage(override)
    checks["owner_override_never_becomes_automatic_coverage"] = (
        override_result["roles"]["team_lead"]["excluded_candidates"][0][
            "owner_selectable"
        ]
        is True
        and override_result["profiles"]["solo_lead"]["ready"] is False
    )

    mixed = _fixture()
    mixed["team_lead"]["candidates"].append(
        _candidate(
            "manual-lead",
            profile="manual",
            perspective="owner",
            pool="manual",
            automatic=False,
        )
    )
    ready_ids = {"codex", "antigravity", "ollama"}
    parity = build_guided_setup_coverage(
        mixed,
        ready_profile_ids=ready_ids,
    )
    expected_ids = {
        row["candidate_id"]
        for row in mixed["team_lead"]["candidates"]
        if candidate_is_automation_eligible(row)
        and row["identity"]["profile_id"] in ready_ids
    }
    actual_ids = {
        row["candidate_id"]
        for row in parity["roles"]["team_lead"]["candidates"]
    }
    checks["coverage_matches_canonical_selector_gate"] = (
        expected_ids == actual_ids
    )

    immutable_fixture = _fixture()
    immutable_preparation = _preparation(
        "codex",
        "antigravity",
        "ollama",
    )
    before = (_hash(immutable_fixture), _hash(immutable_preparation))
    immutable_coverage = build_guided_setup_coverage(immutable_fixture)
    build_progressive_recommendations(
        immutable_coverage,
        immutable_preparation,
    )
    after = (_hash(immutable_fixture), _hash(immutable_preparation))
    checks["coverage_and_recommendations_do_not_mutate_inputs"] = (
        before == after
    )

    report = {
        "schema_version": AUDIT_VERSION,
        "scope": {
            "fixture_only": True,
            "defaults_mutated": False,
            "projects_created": False,
            "user_configuration_mutated": False,
            "installations_attempted": False,
            "secrets_read": False,
            "inference_attempted": False,
            "remote_quota_consumed": False,
        },
        "checks": checks,
        "summary": {
            "coverage_acceptance_ready": all(checks.values()),
            "check_count": len(checks),
            "passed_count": sum(value is True for value in checks.values()),
        },
    }
    validate_audit(report)
    return report


def validate_audit(report: dict[str, Any]) -> None:
    if report.get("schema_version") != AUDIT_VERSION:
        raise ValueError("guided setup coverage acceptance schema drift")
    checks = report.get("checks")
    if not isinstance(checks, dict) or len(checks) != 10:
        raise ValueError("guided setup coverage acceptance matrix drift")
    expected = {
        "coverage_acceptance_ready": all(checks.values()),
        "check_count": len(checks),
        "passed_count": sum(value is True for value in checks.values()),
    }
    if report.get("summary") != expected:
        raise ValueError("guided setup coverage acceptance summary drift")


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
    return 0 if report["summary"]["coverage_acceptance_ready"] or not args.strict else 1


if __name__ == "__main__":
    raise SystemExit(main())
