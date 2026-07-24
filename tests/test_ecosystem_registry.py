from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from aiteam.ecosystem_registry import (
    ACTION_IDS,
    ECOSYSTEM_IDS,
    detect_project_ecosystems,
    load_ecosystem_registry,
    load_ecosystem_schema,
    plan_ecosystem_command,
    project_toolchain_projection,
    validate_ecosystem_registry,
)


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_registry_contract_is_versioned_complete_and_install_free() -> None:
    schema = load_ecosystem_schema()
    registry = load_ecosystem_registry()

    assert schema["title"] == registry["schema_version"]
    assert tuple(registry["action_ids"]) == ACTION_IDS
    assert tuple(item["id"] for item in registry["ecosystems"]) == ECOSYSTEM_IDS
    assert registry["execution_policy"] == {
        "detection_mutates": False,
        "automatic_install": False,
        "descriptor_only_commands": True,
        "requires_authorization": True,
        "cwd_within_workspace": True,
        "max_timeout_seconds": 1200,
        "allowed_env": ["CI", "NO_COLOR", "PYTHONUTF8"],
    }
    command_tokens = {
        str(token).casefold()
        for ecosystem in registry["ecosystems"]
        for action in ACTION_IDS
        for command in ecosystem["commands"][action]
        for token in command["argv"]
    }
    assert not command_tokens.intersection(
        {"install", "add", "latest", "curl", "wget", "sudo", "npx"}
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["execution_policy"].update(automatic_install=True), "cannot install"),
        (
            lambda value: value["ecosystems"][0]["commands"]["test"][0][
                "env"
            ].update(SECRET="x"),
            "env denied",
        ),
        (
            lambda value: value["ecosystems"][0]["commands"]["test"][0].update(
                timeout_seconds=1201
            ),
            "timeout invalid",
        ),
        (
            lambda value: value["ecosystems"][0]["commands"]["test"][0].update(
                runtime_id="missing"
            ),
            "runtime missing",
        ),
        (
            lambda value: value["ecosystems"][0]["commands"]["test"][0].update(
                surprise=True
            ),
            "fields drift",
        ),
        (
            lambda value: value["ecosystems"][5]["action_dependencies"].append(
                {"action": "configure", "requires": ["build"]}
            ),
            "dependency cycle",
        ),
    ],
)
def test_registry_validation_fails_closed(mutation, message: str) -> None:
    registry = copy.deepcopy(load_ecosystem_registry())
    mutation(registry)
    with pytest.raises(ValueError, match=message):
        validate_ecosystem_registry(registry)


def test_detection_handles_monorepo_noise_and_real_scan_limit(tmp_path: Path) -> None:
    _write(tmp_path / "services" / "api" / "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path / "services" / "api" / "tests" / "test_api.py", "def test_ok(): pass\n")
    _write(
        tmp_path / "apps" / "web" / "package.json",
        json.dumps({"scripts": {"test": "vitest"}}),
    )
    _write(tmp_path / "node_modules" / "hidden" / "Cargo.toml")

    observed = detect_project_ecosystems(tmp_path)

    assert {"python", "javascript_typescript"} <= set(observed["detected_ids"])
    assert "rust" not in observed["detected_ids"]
    assert observed["mutated"] is False
    assert observed["commands_executed"] is False
    assert observed["installation_performed"] is False
    assert observed["support_claims"] == []
    assert detect_project_ecosystems(tmp_path, max_files=1)["scan_truncated"] is True


def test_planner_denies_without_capability_or_authorization(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_unit.py", "raise RuntimeError('must not execute')\n")

    missing_capability = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="python",
        action_id="test",
        authorized=True,
        runtime_overrides={"python": sys.executable},
    )
    unauthorized = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="python",
        action_id="test",
        granted_capabilities=("test_execute",),
        runtime_overrides={"python": sys.executable},
    )

    assert missing_capability["reason"] == "capability_not_granted:test_execute"
    assert missing_capability["capability_gap"]["owner"] == "team_owner"
    assert missing_capability["capability_gap"]["descriptor_id"] == "pytest"
    assert unauthorized["reason"] == "execution_not_authorized"
    assert unauthorized["capability_gap"]["owner"] == "project_owner"
    assert not (tmp_path / ".pytest_cache").exists()


def test_python_plan_is_exact_bounded_and_does_not_execute(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "unit" / "test_nested.py", "raise RuntimeError('sentinel')\n")
    _write(tmp_path / "node_modules" / "test_hidden.py", "raise RuntimeError('hidden')\n")

    plan = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="python",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        runtime_overrides={"python": sys.executable},
    )

    assert plan["allowed"] is True
    assert plan["argv"] == [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/unit/test_nested.py",
    ]
    assert plan["cwd"] == "."
    assert plan["timeout_seconds"] == 600
    assert plan["support_claim"] is False
    assert not (tmp_path / ".pytest_cache").exists()


def test_npm_plan_requires_real_script_and_uses_manifest_directory(tmp_path: Path) -> None:
    package = tmp_path / "apps" / "web" / "package.json"
    _write(package, json.dumps({"scripts": {"test": "echo Error: no test specified"}}))
    runtime = tmp_path / "fake-npm.cmd"
    _write(runtime, "@exit /b 99\n")

    denied = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="javascript_typescript",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        runtime_overrides={"npm": str(runtime)},
    )
    _write(package, json.dumps({"scripts": {"test": "vitest run"}}))
    allowed = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="javascript_typescript",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        runtime_overrides={"npm": str(runtime)},
    )

    assert denied["reason"] == "no_applicable_descriptor"
    assert allowed["allowed"] is True
    assert allowed["cwd"] == "apps/web"
    assert allowed["argv"] == [str(runtime), "test", "--silent"]


def test_planned_commands_remain_locked_until_fixture_verification(tmp_path: Path) -> None:
    _write(tmp_path / "go.mod", "module example.test/project\n")
    runtime = tmp_path / "fake-go.exe"
    _write(runtime)

    locked = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="go",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        runtime_overrides={"go": str(runtime)},
    )
    explicitly_previewed = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="go",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        include_planned=True,
        runtime_overrides={"go": str(runtime)},
    )

    assert locked["reason"] == "verification_required"
    assert locked["capability_gap"]["owner"] == "ecosystem_registry_owner"
    assert explicitly_previewed["allowed"] is True
    assert explicitly_previewed["support_claim"] is False


def test_cmake_build_and_test_require_recorded_prerequisites(tmp_path: Path) -> None:
    _write(tmp_path / "CMakeLists.txt", "cmake_minimum_required(VERSION 3.16)\n")
    cmake = tmp_path / "cmake.exe"
    ctest = tmp_path / "ctest.exe"
    _write(cmake)
    _write(ctest)
    overrides = {"cmake": str(cmake), "ctest": str(ctest)}

    configure = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="c_cpp",
        action_id="configure",
        granted_capabilities=("build_execute",),
        authorized=True,
        include_planned=True,
        runtime_overrides=overrides,
    )
    build_denied = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="c_cpp",
        action_id="build",
        granted_capabilities=("build_execute",),
        authorized=True,
        include_planned=True,
        runtime_overrides=overrides,
    )
    build = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="c_cpp",
        action_id="build",
        granted_capabilities=("build_execute",),
        authorized=True,
        include_planned=True,
        completed_actions=("configure",),
        runtime_overrides=overrides,
    )
    test_denied = plan_ecosystem_command(
        tmp_path,
        ecosystem_id="c_cpp",
        action_id="test",
        granted_capabilities=("test_execute",),
        authorized=True,
        include_planned=True,
        completed_actions=("configure",),
        runtime_overrides=overrides,
    )

    assert configure["allowed"] is True
    assert configure["requires_actions"] == []
    assert build_denied["reason"] == "prerequisite_not_satisfied:configure"
    assert build_denied["capability_gap"]["owner"] == "workflow_owner"
    assert build["allowed"] is True
    assert build["requires_actions"] == ["configure"]
    assert test_denied["reason"] == "prerequisite_not_satisfied:build"


def test_projection_guides_lead_hiring_without_authorizing_execution(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(tmp_path / "tests" / "test_ok.py", "def test_ok(): pass\n")

    lead = project_toolchain_projection(tmp_path, role="lead")
    runner = project_toolchain_projection(
        tmp_path,
        role="test_runner",
        granted_capabilities=("repo_read", "test_execute"),
    )

    hiring = {item["role"]: item for item in lead["hiring_requirements"]}
    assert hiring["engineer"]["capabilities"] == ["build_execute"]
    assert hiring["test_runner"]["capabilities"] == ["test_execute"]
    assert lead["commands_executed"] is False
    assert lead["installation_performed"] is False
    assert lead["support_claim"] is False
    python = next(item for item in runner["ecosystems"] if item["id"] == "python")
    assert [item["action"] for item in python["action_contracts"]] == ["test"]
    assert python["action_contracts"][0]["execution_authorized"] is False
    assert runner["capability_gaps"] == []


def test_projection_does_not_invent_actions_from_extension_only(tmp_path: Path) -> None:
    _write(tmp_path / "script.py", "print('no suite or build manifest')\n")

    projection = project_toolchain_projection(tmp_path, role="lead")

    assert projection["detected_ids"] == ["python"]
    assert projection["ecosystems"][0]["available_actions"] == []
    assert projection["hiring_requirements"] == []
