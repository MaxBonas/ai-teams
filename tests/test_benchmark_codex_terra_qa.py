from scripts.benchmark_codex_terra_qa import (
    QA_CANARY_CRITICALITY,
    _dispatch_run_id,
    aggregate_reports,
    evaluate_adversarial_test,
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


def test_dispatch_run_id_uses_scheduler_run_mapping() -> None:
    dispatch = type("Dispatch", (), {"run": {"id": "run:qa-1"}})()

    assert _dispatch_run_id(dispatch) == "run:qa-1"


def test_canary_does_not_measure_the_human_approval_gate() -> None:
    assert QA_CANARY_CRITICALITY == "medium"


def test_aggregate_requires_three_complete_comparable_samples() -> None:
    reports = [
        {
            "model": "gpt-5.6-terra",
            "seed": seed,
            "ok": True,
            "checks": {"attack": True, "verify": True},
            "phases": {"attack": {"seconds": 10 + seed}, "verify_fix": {"seconds": 20}},
            "usage": {"input_tokens": 100, "output_tokens": 10},
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["checks_passed"] == result["checks_total"] == 6
    assert result["usage"]["input_tokens"] == 300
    assert result["conclusion"]["exact_pair_calibrated"] is True
