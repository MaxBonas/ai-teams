from __future__ import annotations

import json

from aiteam.session_continuity import (
    SessionScope,
    audit_session_experiment,
    extract_codex_session_id,
    validate_resume_scope,
)


def _scope(**overrides: str) -> SessionScope:
    values = {
        "agent_id": "role:engineer",
        "issue_id": "issue:one",
        "adapter_type": "subscription_cli",
        "profile_id": "codex_subscription",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "channel": "subscription",
        "workspace_id": "project:one",
    }
    values.update(overrides)
    return SessionScope(**values)


def test_resume_scope_requires_explicit_opt_in_and_exact_identity() -> None:
    allowed = validate_resume_scope(
        previous=_scope(),
        current=_scope(),
        session_id="019f7899-3002-7b91-bc6e-1e75d44c460f",
        previous_status="completed",
        explicit_opt_in=True,
    )
    assert allowed["allowed"] is True
    assert allowed["selector"] == "explicit_id_only"

    denied = validate_resume_scope(
        previous=_scope(),
        current=_scope(issue_id="issue:two", model="gpt-5.6-terra"),
        session_id="019f7899-3002-7b91-bc6e-1e75d44c460f",
        previous_status="completed",
        explicit_opt_in=False,
    )
    assert denied["allowed"] is False
    assert denied["session_id"] is None
    assert "experiment_not_enabled" in denied["reasons"]
    assert "scope_mismatch_issue_id" in denied["reasons"]
    assert "scope_mismatch_model" in denied["reasons"]


def test_codex_session_id_only_comes_from_thread_started() -> None:
    payload = "\n".join(
        [
            json.dumps({"type": "item.completed", "session_id": "wrong-session"}),
            "not-json",
            json.dumps({"type": "thread.started", "thread_id": "019f7899-3002-7b91-bc6e-1e75d44c460f"}),
        ]
    )
    assert extract_codex_session_id(payload) == "019f7899-3002-7b91-bc6e-1e75d44c460f"
    assert extract_codex_session_id('{"type":"thread.started","thread_id":"--last"}') is None


def _report(seed: int, arm: str, *, tokens: int, seconds: int) -> dict[str, object]:
    return {
        "seed": seed,
        "arm": arm,
        "provider": "openai-codex",
        "status": "completed",
        "input_tokens": tokens,
        "duration_seconds": seconds,
        "explicit_session_id": arm == "resumed",
        "scope_match": arm == "resumed",
        "gates": {
            "retains_initial_fact": True,
            "applies_new_instruction": True,
            "revoked_instruction_absent": True,
        },
    }


def test_session_audit_allows_only_complete_beneficial_quality_equal_ab() -> None:
    reports = [
        _report(1, "stateless", tokens=100, seconds=20),
        _report(1, "resumed", tokens=70, seconds=15),
        _report(2, "stateless", tokens=120, seconds=22),
        _report(2, "resumed", tokens=72, seconds=16),
    ]
    audit = audit_session_experiment(reports)
    assert audit["quality_equal"] is True
    assert audit["beneficial"] is True
    assert audit["production_activation_allowed"] is True
    assert audit["median_input_token_savings_ratio"] == 0.35


def test_session_audit_rejects_contamination_and_missing_arm() -> None:
    stateless = _report(1, "stateless", tokens=100, seconds=20)
    resumed = _report(1, "resumed", tokens=60, seconds=12)
    resumed["gates"] = {
        "retains_initial_fact": True,
        "applies_new_instruction": False,
        "revoked_instruction_absent": False,
    }
    audit = audit_session_experiment([stateless, resumed], min_seeds=2)
    assert audit["production_activation_allowed"] is False
    assert "quality_or_isolation_gate_failed" in audit["issues"]
    assert "insufficient_seeds:1<2" in audit["issues"]
