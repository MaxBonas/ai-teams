import json
from pathlib import Path

import pytest

from scripts.context_summary_evals import evaluate_summary


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_FIXTURES = ROOT / "benchmarks" / "context_quality"


RUBRIC = {
    "id": "fixture",
    "max_compression_ratio": 0.30,
    "criteria": [
        {"id": "decision", "patterns": [r"RS256"], "required": True},
        {"id": "owner", "patterns": [r"Engineer.{0,20}rollback"], "required": True},
        {"id": "noise", "patterns": [r"saludo"], "required": False},
    ],
}


def test_accepts_dense_summary_that_retains_required_causality() -> None:
    source = ("Se decide RS256. Engineer hará rollback. Mucho ruido. " * 30)
    summary = "RS256 confirmado; Engineer hará rollback."
    result = evaluate_summary(source, summary, RUBRIC)
    assert result["accepted"] is True
    assert result["required_retention_rate"] == 1.0
    assert result["within_budget"] is True


def test_rejects_short_summary_that_loses_required_decision() -> None:
    source = ("Se decide RS256. Engineer hará rollback. Mucho ruido. " * 30)
    result = evaluate_summary(source, "Engineer hará rollback.", RUBRIC)
    assert result["accepted"] is False
    assert result["within_budget"] is True
    assert result["required_retention_rate"] == 0.5


def test_rejects_complete_summary_that_exceeds_context_budget() -> None:
    source = "RS256. Engineer hará rollback. " * 20
    summary = "RS256. Engineer hará rollback. " * 10
    result = evaluate_summary(source, summary, RUBRIC)
    assert result["semantic_gate_passed"] is True
    assert result["within_budget"] is False
    assert result["accepted"] is False


def test_auth_rubric_accepts_compact_units_and_reversed_acceptance_order() -> None:
    rubric = json.loads(
        (CONTEXT_FIXTURES / "auth_migration_rubric.json").read_text(encoding="utf-8")
    )
    summary = (
        "JWT RS256; doble validación durante 24h. `legacy_kid_hits` debe ser 0 durante 2h. "
        "42 tests passed. Engineer creará rollback_keys.py; dry-run por Reviewer. "
        "Cachés regionales retienen JWKS 15 min. Si 401 supera 0.5% durante 5 min, escalar. "
        "Fuera de alcance: sesiones y proveedor de identidad."
    )

    report = evaluate_summary("x" * 2_000, summary, rubric)

    assert report["required_retained"] == 9
    assert report["semantic_gate_passed"] is True


@pytest.mark.parametrize(
    ("fixture", "expected_rubric_id"),
    [
        ("auth_migration", "auth_migration_causal_v5"),
        ("queue_rollout", "queue_rollout_causal_v4"),
    ],
)
def test_reference_context_summaries_pass_current_rubrics(
    fixture: str,
    expected_rubric_id: str,
) -> None:
    source = (CONTEXT_FIXTURES / f"{fixture}_thread.md").read_text(encoding="utf-8")
    summary = (CONTEXT_FIXTURES / f"{fixture}_reference_summary.md").read_text(encoding="utf-8")
    rubric = json.loads((CONTEXT_FIXTURES / f"{fixture}_rubric.json").read_text(encoding="utf-8"))

    report = evaluate_summary(source, summary, rubric)

    assert rubric["id"] == expected_rubric_id
    assert report["required_retained"] == report["required_total"] == 9
    assert report["within_budget"] is True
    assert report["accepted"] is True


def test_auth_rubric_accepts_equivalent_english_causal_anchors() -> None:
    rubric = json.loads((CONTEXT_FIXTURES / "auth_migration_rubric.json").read_text(encoding="utf-8"))
    summary = (
        "JWT RS256 with a 24-hour dual-key validation. Retain legacy_kid_hits until metric = 0 "
        "for 2 consecutive hours. 42 passed. Engineer owns rollback_keys.py; Reviewer accepts "
        "the dry-run. Regional JWKS caches retain keys for 15 minutes. At 0.5% 401 responses "
        "within a 5-minute window, pause and escalate. Scope exclusions: session migration and "
        "identity provider swap."
    )
    report = evaluate_summary("x" * 4_000, summary, rubric)
    assert report["required_retained"] == report["required_total"] == 9
    assert report["accepted"] is True
