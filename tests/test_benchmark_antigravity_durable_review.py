from scripts.benchmark_antigravity_durable_review import (
    OPENCODE_MODELS,
    _sum_usage,
    aggregate_reports,
)


def _report(model: str, seed: int, seconds: float) -> dict:
    return {
        "model": model,
        "seed": seed,
        "ok": True,
        "reject": {"ok": True},
        "approve": {"ok": True},
        "runs": [{}, {}, {}, {}],
        "quota_observation": {
            "provider_calls": 2,
            "product_runs": 4,
            "wall_seconds": seconds,
        },
    }


def test_aggregate_requires_balanced_behavioral_matrix_and_keeps_baseline() -> None:
    reports = []
    for seed in (1, 2, 3):
        reports.append(_report("gemini-3.5-flash-high", seed, 100 + seed))
        reports.append(_report("gemini-3.6-flash-medium", seed, 40 + seed))

    aggregate = aggregate_reports(reports)

    assert aggregate["matrix_balanced"] is True
    assert aggregate["conclusion"]["behavioral_contract_tied"] is True
    assert aggregate["conclusion"]["default_change_allowed"] is False
    assert aggregate["arms"][0]["provider_calls"] == 6
    assert aggregate["arms"][1]["product_runs"] == 12
    assert aggregate["conclusion"]["challengers"] == ["gemini-3.6-flash-medium"]
    assert aggregate["conclusion"]["median_wall_seconds_delta"] == {
        "gemini-3.6-flash-medium": -60.0,
    }


def test_aggregate_supports_multiple_opencode_challengers() -> None:
    reports = []
    for seed in (1, 2, 3):
        reports.append(_report("gemini-3.5-flash-high", seed, 100 + seed))
        reports.append(_report("opencode/nemotron-3-ultra-free", seed, 20 + seed))
        reports.append(_report("opencode/mimo-v2.5-free", seed, 30 + seed))

    aggregate = aggregate_reports(reports)

    assert aggregate["matrix_balanced"] is True
    assert aggregate["conclusion"]["default_change_allowed"] is False
    assert aggregate["conclusion"]["challengers"] == [
        "opencode/nemotron-3-ultra-free",
        "opencode/mimo-v2.5-free",
    ]


def test_opencode_matrix_includes_laguna() -> None:
    assert "opencode/laguna-s-2.1-free" in OPENCODE_MODELS


def test_aggregate_requires_unique_seeds_and_only_surfaces_stable_challenger() -> None:
    duplicate_seed_baseline = [
        _report("opencode/deepseek-v4-flash-free", seed, 10 + seed)
        for seed in (1, 1, 2)
    ]
    for row in duplicate_seed_baseline:
        row["ok"] = False
    challenger = [
        _report("opencode/laguna-s-2.1-free", seed, 20 + seed)
        for seed in (1, 2, 3)
    ]

    aggregate = aggregate_reports([*duplicate_seed_baseline, *challenger])

    assert aggregate["matrix_balanced"] is False
    assert aggregate["arms"][0]["seed_matrix_complete"] is False
    assert aggregate["conclusion"]["manual_catalog_candidates"] == [
        "opencode/laguna-s-2.1-free"
    ]


def test_sum_usage_counts_provider_calls_even_when_one_has_no_usage() -> None:
    totals = _sum_usage([
        {"usage_json": '{"input_tokens":10,"total_tokens":12}'},
        {"usage_json": "{}"},
        {"usage_json": '{"input_tokens":7,"total_tokens":9}'},
    ])

    assert totals == {"input_tokens": 17, "total_tokens": 21}
