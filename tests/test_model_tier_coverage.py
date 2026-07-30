from aiteam.model_tier_coverage import (
    TIER_COVERAGE_POLICY_VERSION,
    audit_model_tier_coverage,
    tier1_authority_gate,
    tier1_authority_for_role,
)

PROFILES = [
    {
        "id": "codex",
        "provider": "openai",
        "channel": "subscription",
        "config": {"capacity_pool": "codex_subscription"},
    },
    {
        "id": "gemini",
        "provider": "google",
        "channel": "subscription",
        "config": {"capacity_pool": "gemini_subscription"},
    },
]


def _row(profile: str, model: str, roles: dict[str, str]) -> dict:
    return {
        "profile_id": profile,
        "model": model,
        "tier": "premium",
        "automatic": True,
        "executable": True,
        "maintenance_allowed": True,
        "owner_preference": {"state": "high"},
        "roles": [
            {"role": role, "status": status} for role, status in roles.items()
        ],
    }


def test_lead_and_quorum_readiness_are_independent() -> None:
    report = audit_model_tier_coverage(
        {
            "observed_at": "2026-07-24T00:00:00Z",
            "rows": [
                _row("codex", "gpt-5.6-sol", {"lead": "calibrated"}),
                _row(
                    "gemini",
                    "gemini-pro",
                    {"quorum_auditor": "calibrated", "lead": "partial"},
                ),
            ],
        },
        profiles=PROFILES,
    )

    lead = report["tier_1"]["lanes"]["lead_ready"]["roles"][0]
    quorum = report["tier_1"]["lanes"]["quorum_ready"]["roles"][0]
    assert [(item["profile_id"], item["model"]) for item in lead["candidates"]] == [
        ("codex", "gpt-5.6-sol")
    ]
    assert [(item["profile_id"], item["model"]) for item in quorum["candidates"]] == [
        ("gemini", "gemini-pro")
    ]
    assert report["policy"]["authority_invariant"] == (
        "quorum_ready_never_implies_lead_ready"
    )


def test_coverage_requires_two_candidates_perspectives_and_pools() -> None:
    report = audit_model_tier_coverage(
        {
            "rows": [
                _row(
                    "codex",
                    "gpt-5.6-sol",
                    {"lead": "calibrated", "quorum_auditor": "calibrated"},
                ),
                _row(
                    "gemini",
                    "gemini-pro",
                    {"lead": "calibrated", "quorum_auditor": "calibrated"},
                ),
            ]
        },
        profiles=PROFILES,
    )

    lead = report["tier_1"]["lanes"]["lead_ready"]["roles"][0]
    quorum = report["tier_1"]["lanes"]["quorum_ready"]["roles"][0]
    assert lead["status"] == "covered"
    assert quorum["status"] == "covered"


def test_manual_or_partial_candidates_do_not_fill_coverage() -> None:
    manual = _row("gemini", "gemini-pro", {"quorum_auditor": "calibrated"})
    manual["automatic"] = False
    report = audit_model_tier_coverage(
        {
            "rows": [
                _row(
                    "codex",
                    "gpt-5.6-sol",
                    {"quorum_auditor": "calibrated"},
                ),
                manual,
            ]
        },
        profiles=PROFILES,
    )

    quorum = report["tier_1"]["lanes"]["quorum_ready"]["roles"][0]
    assert quorum["eligible_count"] == 1
    assert quorum["status"] == "single_point"
    assert any("not_automatic" in item["reasons"] for item in report["excluded"])


def test_exact_tier1_authority_is_fail_closed_and_role_specific() -> None:
    evaluation = {
        "status": "calibrated",
        "stale_reasons": [],
        "evidence_receipts": ["quorum.json"],
        "provider_version": "1.1.6",
    }
    quorum = tier1_authority_for_role(
        role="quorum_auditor",
        model_tier="premium",
        evaluation=evaluation,
        compatibility={"allowed": True},
    )
    lead = tier1_authority_for_role(
        role="lead",
        model_tier="premium",
        evaluation={"status": "partial", "evidence_receipts": ["lead-old.json"]},
        compatibility={"allowed": True},
    )

    assert quorum == {
        "policy_version": TIER_COVERAGE_POLICY_VERSION,
        "lane": "quorum_ready",
        "calibration_contract": {
            "version": "tier1_quorum_authority_v1",
            "required_constructs": [
                "independent_critique",
                "causal_retention",
                "go_no_go_judgment",
                "verifiable_structured_output",
            ],
        },
        "status": "enabled",
        "enabled": True,
        "reason_code": "exact_role_calibration_verified",
        "scope": "exact_profile_model_role",
        "evaluation_status": "calibrated",
        "evaluated_at": None,
        "provider_version": "1.1.6",
        "prompt_version": None,
        "stale_reasons": [],
        "evidence_receipts": ["quorum.json"],
    }
    assert lead["lane"] == "lead_ready"
    assert lead["calibration_contract"]["version"] == "tier1_lead_authority_v1"
    assert lead["enabled"] is False
    assert lead["reason_code"] == "exact_role_calibration_required"


def test_tier1_consumer_gate_rejects_missing_wrong_lane_and_old_policy() -> None:
    assert tier1_authority_gate(role="reviewer", authority=None)["allowed"] is True
    assert tier1_authority_gate(role="lead", authority=None)["code"] == (
        "tier1_authority_missing"
    )
    wrong_lane = tier1_authority_gate(
        role="lead",
        authority={
            "policy_version": TIER_COVERAGE_POLICY_VERSION,
            "lane": "quorum_ready",
            "status": "enabled",
            "enabled": True,
        },
    )
    assert wrong_lane["allowed"] is False
    assert wrong_lane["code"] == "tier1_authority_lane_mismatch"
    old_policy = tier1_authority_gate(
        role="quorum_auditor",
        authority={
            "policy_version": "legacy",
            "lane": "quorum_ready",
            "status": "enabled",
            "enabled": True,
        },
    )
    assert old_policy["code"] == "tier1_authority_policy_mismatch"
