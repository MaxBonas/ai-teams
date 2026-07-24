from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam import ecosystem_validation
from aiteam.ecosystem_registry import plan_ecosystem_command
from aiteam.ecosystem_validation import (
    load_ecosystem_fixtures,
    required_cases_satisfied,
    validate_ecosystem_fixtures,
    write_validation_receipt,
)


def test_runtime_discovery_uses_current_platform_contract(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok(): assert True\n",
        encoding="utf-8",
    )

    plan = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="python",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
    )

    assert plan["allowed"] is True
    assert Path(plan["argv"][0]).is_file()


def test_fixture_catalog_is_versioned_and_unique() -> None:
    fixtures = load_ecosystem_fixtures()
    case_ids = [case["id"] for fixture in fixtures for case in fixture["cases"]]

    assert {fixture["fixture_id"] for fixture in fixtures} == {
        "c_cpp_cmake_minimal",
        "dotnet_minimal",
        "go_minimal",
        "java_maven_minimal",
        "javascript_minimal",
        "polyglot_monorepo",
        "python_minimal",
        "rust_minimal",
    }
    assert len(case_ids) == len(set(case_ids))


def test_python_fixture_executes_from_unicode_space_path_without_leaking_paths() -> (
    None
):
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("python_pytest",),
        runtime_overrides={"python": {"python": sys.executable}},
    )

    assert receipt["summary"] == {
        "total": 1,
        "passed": 1,
        "failed": 0,
        "blocked": 0,
        "planned": 0,
        "duration_ms": receipt["summary"]["duration_ms"],
    }
    case = receipt["cases"][0]
    assert case["status"] == "passed"
    assert case["actions"][0]["eligible_for_promotion_review"] is True
    assert case["actions"][0]["runtime"]["id"] == "python"
    assert case["actions"][0]["runtime"]["healthy"] is True
    assert case["actions"][0]["runtime"]["version_excerpt"]
    assert case["support_claim"] is False
    serialized = json.dumps(receipt)
    assert "fixture python_pytest ñ" not in serialized
    assert str(Path.home()) not in serialized


def test_polyglot_monorepo_detects_and_executes_each_descriptor() -> None:
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("monorepo_python", "monorepo_javascript"),
        runtime_overrides={"python": {"python": sys.executable}},
    )

    assert receipt["summary"]["passed"] == 2
    assert {item["ecosystem_id"] for item in receipt["cases"]} == {
        "python",
        "javascript_typescript",
    }


def test_missing_runtime_becomes_owned_capability_gap() -> None:
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("python_pytest",),
        runtime_overrides={
            "python": {"python": str(Path("definitely-missing") / "python")}
        },
    )

    case = receipt["cases"][0]
    assert case["status"] == "blocked"
    gap = case["capability_gaps"][0]
    assert gap["schema_version"] == "capability_gap_v1"
    assert gap["owner"] == "machine_environment_owner"
    assert gap["descriptor_id"] == "pytest"
    assert gap["reason"] == "runtime_unavailable:python"
    assert "outside AI Teams" in gap["next_action"]


def test_present_dotnet_host_without_sdk_is_an_environment_gap() -> None:
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("dotnet_xunit",),
    )

    case = receipt["cases"][0]
    if case["status"] == "passed":
        pytest.skip("this machine has a healthy .NET SDK")
    assert case["status"] == "blocked"
    gap = case["capability_gaps"][0]
    assert gap["owner"] == "machine_environment_owner"
    assert gap["reason"] in {
        "runtime_unavailable:dotnet",
        "runtime_probe_failed:dotnet",
    }


@pytest.mark.parametrize(
    ("case_id", "runtime_id"),
    [
        ("go_builtin", "go"),
        ("rust_cargo", "cargo"),
    ],
)
def test_unavailable_go_rust_toolchains_are_owned_gaps(
    case_id: str,
    runtime_id: str,
) -> None:
    receipt = validate_ecosystem_fixtures(selected_case_ids=(case_id,))
    case = receipt["cases"][0]
    if case["status"] == "passed":
        pytest.skip(f"this machine has a healthy {runtime_id} toolchain")

    assert case["status"] == "blocked"
    assert case["capability_gaps"][0]["owner"] == "machine_environment_owner"
    assert case["capability_gaps"][0]["reason"] in {
        f"runtime_unavailable:{runtime_id}",
        f"runtime_probe_failed:{runtime_id}",
    }


def test_unavailable_cmake_toolchain_is_an_owned_gap() -> None:
    receipt = validate_ecosystem_fixtures(selected_case_ids=("c_cpp_cmake",))
    case = receipt["cases"][0]
    if case["status"] == "passed":
        pytest.skip("this machine has a healthy CMake toolchain")

    assert case["status"] == "blocked"
    assert case["actions"][0]["action"] == "configure"
    assert case["capability_gaps"][0]["owner"] == "machine_environment_owner"
    assert case["capability_gaps"][0]["reason"] in {
        "runtime_unavailable:cmake",
        "runtime_probe_failed:cmake",
    }


def test_timeout_is_a_failed_cell_not_a_partial_success(monkeypatch) -> None:
    call_count = 0

    def timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return subprocess.CompletedProcess(args[0], 0, "Python fixture", "")
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 1))

    monkeypatch.setattr(ecosystem_validation, "run_command", timeout)
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("python_pytest",),
        runtime_overrides={"python": {"python": sys.executable}},
    )

    action = receipt["cases"][0]["actions"][0]
    assert action["status"] == "failed"
    assert action["reason"] == "timeout"
    assert action["support_claim"] is False


def test_missing_expected_artifact_fails_even_with_exit_zero(monkeypatch) -> None:
    def succeeds_without_artifact(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(
        ecosystem_validation,
        "run_command",
        succeeds_without_artifact,
    )
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("javascript_npm",),
    )

    build = receipt["cases"][0]["actions"][0]
    assert build["status"] == "failed"
    assert build["reason"] == "missing_artifacts:dist/fixture.txt"
    assert receipt["cases"][0]["status"] == "failed"


def test_receipt_redaction_removes_windows_and_posix_absolute_paths(
    tmp_path: Path,
) -> None:
    output = (
        "Maven home: C:\\Program Files\\Apache\\Maven\n"
        "cache: /Users/example/.m2/repository/file.jar\n"
        f"workspace: {tmp_path}\\target"
    )

    redacted = ecosystem_validation._redact_output(output, workspace=tmp_path)

    assert "C:\\" not in redacted
    assert "/Users/" not in redacted
    assert str(tmp_path) not in redacted
    assert "<absolute-path>" in redacted


def test_dry_run_and_receipt_are_durable_but_never_support_claims(
    tmp_path: Path,
) -> None:
    receipt = validate_ecosystem_fixtures(
        selected_case_ids=("javascript_npm",),
        execute=False,
    )
    destination = tmp_path / "receipts" / "matrix.json"
    write_validation_receipt(receipt, destination)
    restored = json.loads(destination.read_text(encoding="utf-8"))
    satisfied, failures = required_cases_satisfied(restored, ("javascript_npm",))

    assert restored["summary"]["planned"] == 1
    assert restored["support_claim"] is False
    assert satisfied is False
    assert failures == ["javascript_npm"]


def test_unknown_required_case_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown ecosystem fixture cases"):
        validate_ecosystem_fixtures(selected_case_ids=("missing",), execute=False)


def test_ci_matrix_covers_three_os_and_uploads_sha_bound_receipts() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "polyglot-fixtures.yml"
    ).read_text(encoding="utf-8")

    assert "windows-latest" in workflow
    assert "ubuntu-latest" in workflow
    assert "macos-latest" in workflow
    assert "github.event.pull_request.head.sha || github.sha" in workflow
    assert "persist-credentials: false" in workflow
    assert "scripts/validate_ecosystem_fixtures.py" in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "scripts/audit_ecosystem_ci_receipts.py" in workflow
    assert "needs: [python-node, java-maven, dotnet, go, rust, c-cpp]" in workflow
    assert "actions/download-artifact@v8" in workflow
    assert "ecosystem-ci-evidence.json" in workflow
    assert "actions/setup-java@v5" in workflow
    assert "actions/setup-dotnet@v5" in workflow
    for case_id in (
        "python_pytest",
        "javascript_npm",
        "monorepo_python",
        "monorepo_javascript",
    ):
        assert f"--require {case_id}" in workflow
    assert "--require java_maven_junit" in workflow
    assert "--require dotnet_xunit" in workflow
    assert "actions/setup-go@v6" in workflow
    assert "--require go_builtin" in workflow
    assert "--require rust_cargo" in workflow
    assert "--require c_cpp_cmake" in workflow
