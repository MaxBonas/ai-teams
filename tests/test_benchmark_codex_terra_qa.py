from scripts.benchmark_codex_terra_qa import (
    QA_CANARY_CRITICALITY,
    _dispatch_run_id,
    adapter_config,
    aggregate_diverse_family_reports,
    aggregate_reports,
    evaluate_adversarial_test,
    evaluate_webhook_test,
    reevaluate_report,
)


def test_adversarial_test_evaluator_requires_three_negative_boundaries() -> None:
    text = '''
from auth import can_access

def test_boundaries():
    assert not can_access({"tenant_id": "tenant-a", "active": True}, {"tenant_id": "tenant-b"})
    assert can_access({"tenant_id": "tenant-a", "active": False}, {"tenant_id": "tenant-a"}) is False
    assert not can_access({"tenant_id": "tenant-a", "active": True, "role": "member"}, {"tenant_id": "tenant-a", "private": True})
'''
    assert evaluate_adversarial_test(text)["contract_passed"] is True
    assert evaluate_adversarial_test(text.replace("tenant-b", "tenant-a"))["contract_passed"] is False


def test_webhook_evaluator_requires_signature_expiry_and_replay() -> None:
    text = '''
from webhook import accept_webhook

def test_boundaries():
    assert not accept_webhook({"id": "a", "signature": "forged", "timestamp": 1000}, set(), 1000)
    assert accept_webhook({"id": "b", "signature": "trusted", "timestamp": 1000}, set(), 1600) is False
    seen_ids = {"c"}
    assert not accept_webhook({"id": "c", "signature": "trusted", "timestamp": 1000}, seen_ids, 1000)  # replay
'''
    assert evaluate_webhook_test(text)["contract_passed"] is True
    assert evaluate_webhook_test(text.replace("forged", "trusted"))["contract_passed"] is False


def test_dispatch_run_id_uses_scheduler_run_mapping() -> None:
    dispatch = type("Dispatch", (), {"run": {"id": "run:qa-1"}})()

    assert _dispatch_run_id(dispatch) == "run:qa-1"


def test_canary_does_not_measure_the_human_approval_gate() -> None:
    assert QA_CANARY_CRITICALITY == "medium"


def test_antigravity_config_preserves_profile_and_does_not_invent_effort() -> None:
    config = adapter_config("antigravity_subscription", "gemini-3.5-flash-high")
    assert config["cli_kind"] == "antigravity"
    assert config["command"] == ["agy"]
    assert config["profile_id"] == "antigravity_subscription"
    assert "model_reasoning_effort" not in config


def test_aggregate_requires_three_complete_comparable_samples() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "role": "qa",
            "contract_version": "adversarial_qa_fix_cycle_v2",
            "seed": seed,
            "ok": True,
            "checks": {"attack": True, "verify": True},
            "phases": {"attack": {"seconds": 10 + seed}, "verify_fix": {"seconds": 20}},
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "attack_evaluation": {"seed": seed},
            "failing_test_run": {"exit_code": 1},
            "_source_receipt": f"qa-seed-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["checks_passed"] == result["checks_total"] == 6
    assert result["usage"]["input_tokens"] == 300
    assert result["integrity"]["sources_bound"] is True
    assert result["conclusion"]["exact_pair_calibrated"] is True
    reports[-1]["profile_id"] = "antigravity_subscription"
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is False


def test_diversity_aggregate_requires_two_exact_calibrated_families() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "case_family": family,
            "matrix_complete": True,
            "samples_passed": 3,
            "conclusion": {"exact_pair_calibrated": True},
            "_source_receipt": f"{family}.json",
        }
        for family in ("authorization_boundary", "webhook_replay_boundary")
    ]
    result = aggregate_diverse_family_reports(
        reports, model="gpt-5.6-terra", profile_id="codex_subscription"
    )
    assert result["case_family_count"] == 2
    assert result["samples_total"] == 6
    assert result["conclusion"]["case_diversity_passed"] is True
    reports[-1]["case_family"] = "authorization_boundary"
    assert (
        aggregate_diverse_family_reports(
            reports, model="gpt-5.6-terra", profile_id="codex_subscription"
        )["conclusion"]["case_diversity_passed"]
        is False
    )


def test_reevaluation_versions_generalized_transport_without_rerun() -> None:
    updated = reevaluate_report({"checks": {"all": True}, "ok": True})
    assert updated["contract_version"] == "adversarial_qa_fix_cycle_v2"
    assert updated["reevaluation"]["provider_rerun"] is False


def test_reevaluation_accepts_inactive_constructor_from_persisted_pytest() -> None:
    report = {
        "checks": {"adversarial_test_contract": False, "other": True},
        "attack_evaluation": {
            "anchors": {
                "imports_target": True,
                "cross_tenant": True,
                "inactive_actor": False,
                "private_non_admin": True,
                "negative_assertion": True,
            },
        },
        "failing_test_run": {
            "stdout": "actor = Actor(tenant='tenant-a', active=False, role='admin')"
        },
        "ok": False,
    }
    updated = reevaluate_report(report)
    assert updated["attack_evaluation"]["contract_passed"] is True
    assert updated["checks"]["adversarial_test_contract"] is True
    assert updated["ok"] is True
