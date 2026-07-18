from __future__ import annotations

import json

from aiteam.adapters.anthropic_adapter import _build_system, _build_user


def test_anthropic_quorum_system_carries_strict_bounded_contract() -> None:
    prompt = _build_system("skill base", "quorum_auditor")

    assert "QUORUM AUDITOR — CONTRATO ESTRICTO" in prompt
    assert "---QUORUM-AUDIT---" in prompt
    assert "1-3 findings" in prompt
    assert "---AGENT-REPORT---" in prompt
    assert "approved|changes_requested|blocked" in prompt


def test_anthropic_quorum_user_receives_full_frozen_review() -> None:
    plan = "Plan A completo " * 400
    payload = {
        "issue": {"title": "child"},
        "quorum_review": {
            "objective": {"title": "Autorizar tenants", "description": "Objetivo congelado"},
            "plan": {"body": plan},
            "instruction": "Audita de forma independiente",
        },
    }

    prompt = _build_user(json.dumps(payload, ensure_ascii=False), {"issue_id": "issue:q"})

    assert "Objetivo congelado" in prompt
    assert plan in prompt
    assert "Audita de forma independiente" in prompt
    assert "Context snapshot" not in prompt


def test_anthropic_context_curator_receives_full_durable_slice() -> None:
    body = "Decisión causal " * 600
    target = {
        "target_issue_id": "issue:parent",
        "start_comment_id": "comment:a",
        "end_comment_id": "comment:b",
        "start_char_offset": 0,
        "end_char_offset": len(body),
        "char_count_original": len(body),
        "comments": [{"id": "comment:b", "body": body}],
    }
    prompt = _build_user(
        json.dumps({"issue": {"title": "curator"}, "context_curation_target": target}),
        {"issue_id": "issue:curator"},
    )

    assert body in prompt
    assert "append_context_summary" in prompt
    assert "char_count_original" in prompt
    assert "Context snapshot" not in prompt
