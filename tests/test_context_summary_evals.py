from scripts.context_summary_evals import evaluate_summary


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
