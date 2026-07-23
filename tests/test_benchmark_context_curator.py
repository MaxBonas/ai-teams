from scripts.benchmark_context_curator import aggregate_reports, bootstrap_profile_ids


def _report(case_id: str, seed: int) -> dict:
    return {
        "profile_id": "antigravity_subscription",
        "execution_config": {
            "model": "gemini-3.5-flash-low",
            "reasoning_effort_override": None,
        },
        "contract_version": "two_causal_slices_three_seeds_each_v1",
        "case_id": case_id,
        "seed": seed,
        "source_sha256": f"source-{case_id}",
        "rubric_sha256": f"rubric-{case_id}",
        "accepted": True,
        "summary": f"summary-{case_id}-{seed}",
        "causal_units": [],
        "criteria": [],
        "runtime": {
            "issue_status": "done",
            "run": {"status": "completed"},
            "attempts": 1,
            "wall_seconds": 10 + seed,
            "input_tokens": 0,
            "output_tokens": 0,
            "telemetry_status": "unknown",
        },
        "_source_receipt": f"{case_id}-seed-{seed}.json",
    }


def test_local_context_bootstrap_keeps_target_profile_first() -> None:
    assert bootstrap_profile_ids("local_qwen_ollama") == [
        "local_qwen_ollama",
        "codex_subscription",
    ]


def test_aggregate_requires_exact_two_case_three_seed_matrix() -> None:
    reports = [
        _report(case_id, seed)
        for case_id in ("auth_migration", "queue_rollout")
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["matrix_complete"] is True
    assert result["samples_passed"] == 6
    assert result["usage"]["telemetry_status"] == "unknown"
    assert result["conclusion"]["exact_pair_calibrated"] is True


def test_aggregate_fails_closed_on_duplicate_cell_or_retry() -> None:
    reports = [
        _report(case_id, seed)
        for case_id in ("auth_migration", "queue_rollout")
        for seed in (1, 2, 3)
    ]
    reports[-1]["seed"] = 2
    assert aggregate_reports(reports)["matrix_complete"] is False

    reports[-1]["seed"] = 3
    reports[-1]["runtime"]["attempts"] = 2
    result = aggregate_reports(reports)
    assert result["matrix_complete"] is True
    assert result["conclusion"]["exact_pair_calibrated"] is False
