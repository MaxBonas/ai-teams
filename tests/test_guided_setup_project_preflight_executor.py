from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from aiteam.guided_setup_project_preflight_execution import (
    SCHEMA_VERSION as PLAN_SCHEMA_VERSION,
)
from aiteam.guided_setup_project_preflight_executor import (
    execute_project_preflight_plan,
    validate_project_preflight_execution_receipt,
)


def _plan(
    *action_ids: str,
    fixture_case_id: str = "web_vite_react_typescript",
) -> dict:
    actions = []
    if "local_fixture" in action_ids:
        actions.append(
            {
                "id": "local_fixture",
                "kind": "software_toolchain_smoke",
                "runner": "ecosystem_validation",
                "case_id": fixture_case_id,
                "timeout_seconds": 300,
                "remote": False,
                "quota_possible": False,
                "workspace": "isolated_temporary_copy",
                "max_attempts": 1,
                "consent_requirements": ["confirm_local_fixture"],
            }
        )
    if "exact_adapter_probe" in action_ids:
        actions.append(
            {
                "id": "exact_adapter_probe",
                "kind": "structured_output_probe",
                "runner": "adapter_contract_probe",
                "profile_id": "codex_subscription",
                "model_id": "gpt-exact",
                "timeout_seconds": 90,
                "remote": True,
                "quota_possible": True,
                "workspace": "none",
                "max_attempts": 1,
                "consent_requirements": [
                    "confirm_remote_probe",
                    "acknowledge_possible_quota",
                ],
            }
        )
    result = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "scope": {
            "plan_only": True,
            "filesystem_mutated": False,
            "commands_executed": False,
            "tests_executed": False,
            "remote_calls": False,
            "secrets_read": False,
            "quota_consumed": False,
            "automatic_install": False,
        },
        "inputs": {
            "needs_hash": "a" * 64,
            "proposal_hash": "b" * 64,
            "preflight_hash": "c" * 64,
        },
        "policy": {
            "execution_order": [
                "local_fixture",
                "exact_adapter_probe",
            ],
            "fail_fast": True,
            "max_local_fixtures": 1,
            "max_remote_probes": 1,
            "max_attempts_per_action": 1,
            "automatic_install": False,
            "health_or_catalog_persisted": False,
        },
        "actions": actions,
        "planning_blockers": [],
        "summary": {
            "status": "ready" if actions else "nothing_to_run",
            "action_count": len(actions),
            "remote_action_count": sum(
                row["remote"] is True for row in actions
            ),
            "requires_consent": bool(actions),
            "next_action": (
                "confirm_preflight_execution"
                if actions
                else "persist_preflight_before_commit"
            ),
        },
    }
    import hashlib
    import json

    result["plan_hash"] = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return result


def _passed_fixture(case_id: str, timeout: int) -> dict:
    assert case_id == "web_vite_react_typescript"
    assert timeout == 300
    return {
        "schema_version": "ecosystem_validation_receipt_v1",
        "cases": [{"id": case_id, "status": "passed"}],
        "summary": {"total": 1, "passed": 1},
    }


def _execute(plan: dict, **overrides):
    values = {
        "plan_hash": plan["plan_hash"],
        "confirm_local_fixture": True,
        "confirm_remote_probe": True,
        "acknowledge_possible_quota": True,
        "fixture_runner": _passed_fixture,
        "observed_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return execute_project_preflight_plan(plan, **values)


def test_local_fixture_executes_once_and_normalizes_safe_evidence() -> None:
    calls = []

    def runner(case_id: str, timeout: int) -> dict:
        calls.append((case_id, timeout))
        return _passed_fixture(case_id, timeout)

    receipt = _execute(_plan("local_fixture"), fixture_runner=runner)

    assert calls == [("web_vite_react_typescript", 300)]
    assert receipt["summary"]["status"] == "passed"
    assert receipt["fixture_evidence"][0] == {
        "schema_version": "guided_setup_fixture_evidence_v1",
        "kind": "software_toolchain_smoke",
        "status": "passed",
        "receipt_ref": receipt["actions"][0]["receipt_ref"],
        "commands_executed": True,
        "tests_executed": True,
        "remote_calls": False,
        "quota_consumed": False,
        "workspace_mutated": False,
    }
    assert receipt["scope"]["user_workspace_mutated"] is False
    assert receipt["scope"]["automatic_install"] is False


def test_default_runner_executes_allowlisted_python_fixture_in_temp_copy() -> None:
    plan = _plan("local_fixture", fixture_case_id="python_pytest")
    receipt = execute_project_preflight_plan(
        plan,
        plan_hash=plan["plan_hash"],
        confirm_local_fixture=True,
        confirm_remote_probe=False,
        acknowledge_possible_quota=False,
        observed_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )

    assert receipt["summary"]["status"] == "passed"
    assert receipt["actions"][0]["reason"] == "allowlisted_fixture_passed"
    assert receipt["scope"]["temporary_workspace_mutated"] is True
    assert receipt["scope"]["user_workspace_mutated"] is False


def test_missing_consent_prevents_all_execution() -> None:
    calls = []
    plan = _plan("local_fixture")

    with pytest.raises(ValueError, match="local_fixture_consent_required"):
        _execute(
            plan,
            confirm_local_fixture=False,
            fixture_runner=lambda *_args: calls.append(True),
        )

    assert calls == []


def test_all_consents_and_runners_are_checked_before_local_execution() -> None:
    calls = []
    plan = _plan("local_fixture", "exact_adapter_probe")

    with pytest.raises(ValueError, match="remote_probe_consent_required"):
        _execute(
            plan,
            confirm_remote_probe=False,
            fixture_runner=lambda *_args: calls.append(True),
        )
    with pytest.raises(ValueError, match="runner_unavailable"):
        _execute(
            plan,
            fixture_runner=lambda *_args: calls.append(True),
        )

    assert calls == []


def test_failed_fixture_is_fail_fast_and_does_not_claim_pass() -> None:
    def failed(case_id: str, _timeout: int) -> dict:
        return {
            "cases": [{"id": case_id, "status": "failed"}],
            "summary": {"total": 1, "failed": 1},
        }

    receipt = _execute(_plan("local_fixture"), fixture_runner=failed)

    assert receipt["summary"]["status"] == "failed"
    assert receipt["fixture_evidence"][0]["status"] == "failed"
    assert receipt["actions"][0]["attempts"] == 1


def test_runner_exception_is_redacted_to_exception_class() -> None:
    def broken(_case_id: str, _timeout: int) -> dict:
        raise RuntimeError("secret value at C:\\Users\\owner")

    receipt = _execute(_plan("local_fixture"), fixture_runner=broken)

    assert receipt["actions"][0]["reason"] == "runner_error:RuntimeError"
    assert "secret" not in str(receipt)
    assert "Users" not in str(receipt)


def test_non_programming_nothing_to_run_never_calls_runner() -> None:
    receipt = _execute(
        _plan(),
        fixture_runner=lambda *_args: pytest.fail("runner must not execute"),
    )

    assert receipt["summary"]["status"] == "nothing_to_run"
    assert receipt["actions"] == []
    assert receipt["scope"]["temporary_workspace_mutated"] is False


def test_executor_rejects_stale_plan_and_receipt_tampering() -> None:
    plan = _plan("local_fixture")
    with pytest.raises(ValueError, match="execution_plan_stale"):
        _execute(plan, plan_hash="f" * 64)

    receipt = _execute(plan)
    tampered = deepcopy(receipt)
    tampered["actions"][0]["attempts"] = 2
    with pytest.raises(ValueError, match="action_state_drift"):
        validate_project_preflight_execution_receipt(tampered)
