from scripts.benchmark_antigravity_durable_review import (
    CODEX_MODELS,
    GEMINI_API_MODELS,
    LOCAL_MODELS,
    OPENCODE_MODELS,
    _model_transport,
    _sum_usage,
    aggregate_reports,
    cli_version_for_profile,
)


def _report(model: str, seed: int, seconds: float) -> dict:
    profile_id = (
        "gemini_api_free"
        if model == "gemini-3.6-flash"
        else "codex_subscription"
        if model == "gpt-5.6-terra"
        else "antigravity_subscription"
    )
    return {
        "profile_id": profile_id,
        "model": model,
        "cli_version": "0.146.0-alpha.6",
        "seed": seed,
        "ok": True,
        "reject": {"ok": True},
        "approve": {"ok": True},
        "runs": [{}, {}, {}, {}],
        "quota_observation": {
            "provider_calls": 2,
            "product_runs": 4,
            "wall_seconds": seconds,
            "tokens": None,
        },
        "_source_receipt": f"{profile_id}-{model}-{seed}.json",
        "_source_sha256": f"hash-{profile_id}-{model}-{seed}",
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


def test_durable_review_matrix_includes_codex_terra() -> None:
    assert CODEX_MODELS == ("gpt-5.6-terra",)


def test_codex_transport_observes_current_cli_version(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.benchmark_antigravity_durable_review.cli_version_for_profile",
        lambda profile_id: (
            "0.146.0-alpha.6" if profile_id == "codex_subscription" else ""
        ),
    )

    transport = _model_transport("gpt-5.6-terra")

    assert transport["cli_version"] == "0.146.0-alpha.6"


def test_cli_version_for_api_is_protocol_bound() -> None:
    assert cli_version_for_profile("gemini_api_free") == "api:google:v1beta"


def test_durable_review_supports_gemini_free_without_cli_transport() -> None:
    assert GEMINI_API_MODELS == ("gemini-3.6-flash",)
    transport = _model_transport("gemini-3.6-flash")
    assert transport["profile_id"] == "gemini_api_free"
    assert transport["adapter_type"] == "gemini_api"
    assert transport["channel"] == "api"
    assert transport["command"] == []


def test_local_durable_review_uses_ollama_without_external_quota() -> None:
    assert LOCAL_MODELS == ("gemma4:26b",)
    transport = _model_transport("gemma4:26b")
    assert transport["profile_id"] == "local_gemma4_ollama"
    assert transport["channel"] == "local"
    assert transport["extra_config"]["model_reasoning_effort"] == "none"


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


def test_sum_usage_preserves_gemini_token_fields() -> None:
    totals = _sum_usage([
        {
            "usage_json": (
                '{"promptTokenCount":100,"candidatesTokenCount":20,'
                '"thoughtsTokenCount":30,"totalTokenCount":150}'
            )
        },
        {
            "usage_json": (
                '{"promptTokenCount":80,"candidatesTokenCount":10,'
                '"thoughtsTokenCount":15,"totalTokenCount":105}'
            )
        },
    ])

    assert totals == {
        "promptTokenCount": 180,
        "candidatesTokenCount": 30,
        "thoughtsTokenCount": 45,
        "totalTokenCount": 255,
    }


def test_aggregate_preserves_subscription_token_telemetry_when_every_seed_has_it() -> None:
    reports = [_report("gpt-5.6-terra", seed, 50 + seed) for seed in (1, 2, 3)]
    for seed, report in enumerate(reports, start=1):
        report["quota_observation"]["tokens"] = {
            "input_tokens": 100 * seed,
            "output_tokens": 10 * seed,
        }

    aggregate = aggregate_reports(reports)

    assert aggregate["arms"][0]["token_totals"] == {
        "input_tokens": 600,
        "output_tokens": 60,
    }
    assert aggregate["conclusion"]["tokens_available"] is True
    assert aggregate["conclusion"]["exact_pair_calibrated"] is True
    assert aggregate["conclusion"]["decision"] == "calibrate_exact_pair"
    assert aggregate["cli_version"] == "0.146.0-alpha.6"
    assert aggregate["integrity"]["same_cli_version"] is True
    assert "presión de cuota" in aggregate["conclusion"]["quota_note"]


def test_exact_pair_rejects_mixed_or_missing_cli_versions() -> None:
    reports = [_report("gpt-5.6-terra", seed, 50 + seed) for seed in (1, 2, 3)]
    reports[-1]["cli_version"] = "0.145.0"

    mixed = aggregate_reports(reports)

    assert mixed["conclusion"]["exact_pair_calibrated"] is False
    assert mixed["integrity"]["same_cli_version"] is False
    reports[-1]["cli_version"] = ""

    missing = aggregate_reports(reports)

    assert missing["conclusion"]["exact_pair_calibrated"] is False
    assert missing["integrity"]["same_cli_version"] is False
