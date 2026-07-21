from scripts.benchmark_antigravity_role_models import MATRIX, aggregate_reports, parse_json_output, score_response


def test_screening_matrix_covers_inventory_and_live_role_baselines() -> None:
    models = [item.model for item in MATRIX]
    assert len(models) == 13
    assert len(set(models)) == 11
    assert {item.role for item in MATRIX} == {"lead", "coding", "review", "scout"}
    assert sum(item.role == "review" for item in MATRIX) == 4
    assert sum(item.role == "lead" for item in MATRIX) == 3
    assert sum(item.role == "coding" for item in MATRIX) == 3
    assert sum(item.role == "scout" for item in MATRIX) == 3
    assert {(item.model, item.role) for item in MATRIX if item.model.startswith("gemini-3.6")} == {
        ("gemini-3.6-flash-high", "lead"),
        ("gemini-3.6-flash-high", "coding"),
        ("gemini-3.6-flash-medium", "review"),
        ("gemini-3.6-flash-low", "scout"),
    }
    baselines = {(item.role, item.model) for item in MATRIX if item.baseline}
    assert baselines == {
        ("lead", "gemini-3.1-pro-high"),
        ("coding", "gemini-3.5-flash-high"),
        ("review", "gemini-3.5-flash-high"),
        ("scout", "gemini-3.5-flash-low"),
    }


def test_json_parser_accepts_clean_or_fenced_noise() -> None:
    assert parse_json_output('{"objective":"ok"}') == {"objective": "ok"}
    assert parse_json_output('```json\n{"objective":"ok"}\n```') == {"objective": "ok"}


def test_scout_score_penalizes_distractor_hallucination() -> None:
    base = {
        "decisions": [{"id": "D1", "fact": "SQLite", "owner": "Lead", "accepted_by": "Reviewer"}],
        "constraints": [{"id": "R1", "fact": "wakeup"}],
        "risks": [{"id": "K1", "fact": "checkout", "mitigation": "atomic"}],
        "gates": [{"id": "G1", "metric": "restart", "threshold": "100", "window": "24 hours", "action": "rollback"}],
        "dependencies": [{"id": "P1", "fact": "JSONL"}],
        "blockers": [{"id": "B1", "fact": "maintenance", "escalate_to": "human"}],
        "evidence": [{"id": "E1", "fact": "hidden suite"}],
    }
    clean = score_response("scout", base)
    noisy = score_response("scout", {**base, "noise": "Proyecto Aurora, dos horas"})
    assert clean["contract_pass"] is True
    assert noisy["contract_pass"] is False
    assert noisy["score"] < clean["score"]


def test_aggregate_never_authorizes_default_change_from_structural_score() -> None:
    results = []
    for seed in range(3):
        results.extend([
            {
                "role": "coding", "model": "gemini-3.5-flash-high", "status": "completed",
                "wall_seconds": 10 + seed, "evaluation": {"score_percent": 70.0, "contract_pass": True},
            },
            {
                "role": "coding", "model": "claude-sonnet-4-6", "status": "completed",
                "wall_seconds": 20 + seed, "evaluation": {"score_percent": 90.0, "contract_pass": True},
            },
        ])
    aggregate = aggregate_reports([{"results": results}])
    coding = aggregate["decisions"]["coding"]
    sonnet = next(item for item in coding["challengers"] if item["model"] == "claude-sonnet-4-6")
    assert sonnet["disposition"] == "candidate_for_behavioral_validation"
    assert aggregate["conclusion"]["default_change_allowed"] is False
