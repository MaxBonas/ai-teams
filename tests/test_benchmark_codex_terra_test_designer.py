from pathlib import Path

from scripts.benchmark_codex_terra_test_designer import (
    MUTANTS,
    PRODUCTION,
    STATE_MACHINE_MUTANTS,
    STATE_MACHINE_PRODUCTION,
    adapter_config,
    aggregate_diverse_family_reports,
    aggregate_reports,
    bootstrap_profile_ids,
    durable_authored_files,
    evaluate_mutation_suite,
    reevaluate_report,
)


def test_mutation_evaluator_kills_every_frozen_mutant(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text(PRODUCTION, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_acceptance_pricing.py"
    test_file.write_text(
        '''import pytest
from pricing import quote

def test_happy_path_and_discount():
    assert quote(10, 3, 20) == 24

@pytest.mark.parametrize("args", [(-0.01, 1, 0), (1, 0, 0), (1, 1, 101)])
def test_invalid_inputs(args):
    with pytest.raises(ValueError):
        quote(*args)
''',
        encoding="utf-8",
    )

    result = evaluate_mutation_suite(tmp_path, test_file)

    assert result["baseline"]["exit_code"] == 0
    assert result["mutants_killed"] == result["mutants_total"] == len(MUTANTS)
    assert (tmp_path / "pricing.py").read_text(encoding="utf-8") == PRODUCTION


def test_state_machine_evaluator_kills_every_frozen_mutant(tmp_path: Path) -> None:
    (tmp_path / "job_state.py").write_text(
        STATE_MACHINE_PRODUCTION, encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_acceptance_job_state.py"
    test_file.write_text(
        '''import pytest
from job_state import transition

@pytest.mark.parametrize("status,event,expected", [
    ("pending", "start", "running"),
    ("running", "succeed", "succeeded"),
    ("running", "fail", "failed"),
])
def test_valid(status, event, expected):
    original = {"status": status, "id": "j1"}
    result = transition(original, event)
    assert result == {"status": expected, "id": "j1"}
    assert result is not original
    assert original["status"] == status

@pytest.mark.parametrize("status,event", [
    ("pending", "succeed"), ("succeeded", "start"), ("failed", "start"),
    ("running", "unknown"), ("unknown", "start"),
])
def test_invalid(status, event):
    with pytest.raises(ValueError):
        transition({"status": status}, event)
''',
        encoding="utf-8",
    )
    result = evaluate_mutation_suite(
        tmp_path,
        test_file,
        production_filename="job_state.py",
        production_source=STATE_MACHINE_PRODUCTION,
        mutants=STATE_MACHINE_MUTANTS,
    )
    assert result["baseline"]["exit_code"] == 0
    assert result["mutants_killed"] == result["mutants_total"] == len(
        STATE_MACHINE_MUTANTS
    )
    assert (
        (tmp_path / "job_state.py").read_text(encoding="utf-8")
        == STATE_MACHINE_PRODUCTION
    )


def test_aggregate_requires_three_passing_comparable_samples() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "role": "test_designer",
            "contract_version": "independent_test_designer_mutation_v2",
            "seed": seed,
            "ok": True,
            "seconds": 10 + seed,
            "checks": {"suite": True},
            "usage": {"input_tokens": 100},
            "mutation_evaluation": {"seed": seed},
            "authored_files": ["tests/test_acceptance_pricing.py"],
            "report": {"result": "done"},
            "_source_receipt": f"test-designer-seed-{seed}.json",
        }
        for seed in (1, 2, 3)
    ]

    result = aggregate_reports(reports)

    assert result["samples_passed"] == 3
    assert result["checks_passed"] == result["checks_total"] == 3
    assert result["usage"]["input_tokens"] == 300
    assert result["integrity"]["sources_bound"] is True
    assert result["conclusion"]["exact_pair_calibrated"] is True
    reports[-1]["profile_id"] = "antigravity_subscription"
    assert aggregate_reports(reports)["conclusion"]["exact_pair_calibrated"] is False


def test_diversity_aggregate_requires_two_exact_mutation_families() -> None:
    reports = [
        {
            "profile_id": "codex_subscription",
            "model": "gpt-5.6-terra",
            "case_family": family,
            "matrix_complete": True,
            "samples_passed": 3,
            "conclusion": {"exact_pair_calibrated": True},
            "_source_receipt": f"{family}.json",
        }
        for family in ("pricing_boundary_mutation", "job_state_machine_mutation")
    ]
    result = aggregate_diverse_family_reports(
        reports, model="gpt-5.6-terra", profile_id="codex_subscription"
    )
    assert result["samples_total"] == 6
    assert result["conclusion"]["case_diversity_passed"] is True
    reports[-1]["case_family"] = "pricing_boundary_mutation"
    assert (
        aggregate_diverse_family_reports(
            reports, model="gpt-5.6-terra", profile_id="codex_subscription"
        )["conclusion"]["case_diversity_passed"]
        is False
    )


def test_antigravity_config_uses_plan_transport_without_fake_usage_settings() -> None:
    config = adapter_config("antigravity_subscription", "gemini-3.5-flash-high")
    assert config["cli_kind"] == "antigravity"
    assert config["command"] == ["agy"]
    assert config["sandbox"] == "workspace-write"
    assert "model_reasoning_effort" not in config


def test_local_test_designer_uses_ollama_without_external_quota() -> None:
    config = adapter_config("local_gemma4_ollama", "gemma4:26b")
    assert config["oss"] is True
    assert config["local_provider"] == "ollama"
    assert config["model_reasoning_effort"] == "none"
    assert bootstrap_profile_ids("local_gemma4_ollama") == [
        "local_gemma4_ollama",
        "codex_subscription",
    ]


def test_authored_surface_ignores_interpreter_cache(tmp_path: Path) -> None:
    (tmp_path / "pricing.py").write_text("production", encoding="utf-8")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "test_acceptance_pricing.py").write_text("test", encoding="utf-8")
    cache = test_dir / "__pycache__"
    cache.mkdir()
    (cache / "test_acceptance_pricing.pyc").write_bytes(b"cache")
    assert durable_authored_files(tmp_path) == ["tests/test_acceptance_pricing.py"]


def test_reevaluation_excludes_cache_without_provider_rerun() -> None:
    report = {
        "authored_files": [
            "__pycache__/pricing.pyc",
            "tests/__pycache__/test_acceptance_pricing.pyc",
            "tests/test_acceptance_pricing.py",
        ],
        "checks": {
            "only_expected_test_authored": False,
            "all_hidden_mutants_killed": True,
        },
        "ok": False,
    }
    updated = reevaluate_report(report)
    assert updated["ok"] is True
    assert updated["authored_files"] == ["tests/test_acceptance_pricing.py"]
    assert updated["reevaluation"]["provider_rerun"] is False
