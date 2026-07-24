from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aiteam.installation_support import load_installation_support_contract
from aiteam.machine_doctor import (
    SCHEMA_VERSION,
    build_machine_inventory,
    load_machine_doctor_schema,
    render_machine_inventory,
    validate_machine_inventory,
)


ROOT = Path(__file__).resolve().parents[1]


def _fake_command(command: list[str]) -> tuple[bool, str | None]:
    executable = Path(command[0]).stem.lower()
    versions = {
        "node": "v24.0.0",
        "npm": "11.0.0",
        "git": "git version 2.49.0",
        "pwsh": "7.6.0",
        "powershell": "7.6.0",
    }
    return True, versions.get(executable, "1.0.0")


def test_schema_is_fail_closed_and_versioned() -> None:
    schema = load_machine_doctor_schema()

    assert schema["title"] == SCHEMA_VERSION
    assert schema["additionalProperties"] is False
    assert schema["properties"]["scope"]["properties"]["secrets_read"]["const"] is False
    assert {"toolchains", "adapters"} <= set(schema["required"])


def test_inventory_has_stable_base_shape_without_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aiteam.machine_doctor._resolve_runtime",
        lambda commands, os_id: f"C:\\Tools\\{commands[0]}.exe",
    )
    report = build_machine_inventory(
        root=tmp_path,
        support_contract=load_installation_support_contract(),
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("windows", "x86_64", "test-release"),
        adapter_profiles=[],
    )

    assert report["schema_version"] == SCHEMA_VERSION
    assert report["scope"] == {
        "read_only": True,
        "secrets_read": False,
        "credentials_probed": False,
        "personal_paths_emitted": False,
    }
    assert [item["id"] for item in report["runtimes"]] == [
        "python",
        "node",
        "npm",
        "git",
        "powershell",
        "sqlite",
    ]
    assert report["summary"]["inventory_complete"] is True
    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized
    assert "C:\\\\Tools" not in serialized


def test_missing_required_runtime_is_visible_but_inventory_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(commands: list[str], os_id: str) -> str | None:
        return None if commands[0] == "node" else f"/usr/bin/{commands[0]}"

    monkeypatch.setattr("aiteam.machine_doctor._resolve_runtime", resolve)
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )

    assert report["summary"]["inventory_complete"] is True
    assert report["summary"]["required_runtimes_ready"] is False
    assert report["summary"]["missing_required"] == ["node"]
    assert report["summary"]["strict_pass"] is False
    assert any(
        item["code"] == "required_runtime_absent"
        and item["subject_id"] == "node"
        for item in report["diagnostics"]
    )


def test_permission_probe_does_not_create_files(tmp_path: Path) -> None:
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert after == before


def test_validation_rejects_executable_paths(tmp_path: Path) -> None:
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )
    report["runtimes"][0]["executable"] = "/private/python"

    with pytest.raises(ValueError, match="must not expose a path"):
        validate_machine_inventory(report)


def test_validation_rejects_diagnostic_summary_drift(tmp_path: Path) -> None:
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )
    report["summary"]["strict_pass"] = True

    with pytest.raises(ValueError, match="strict status drift"):
        validate_machine_inventory(report)


def test_version_probe_timeout_degrades_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiteam.machine_doctor import _probe_version_command

    monkeypatch.setattr(
        "aiteam.machine_doctor.run_command",
        lambda command, env, timeout: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(command, timeout)
        ),
    )

    assert _probe_version_command(["tool", "--version"]) == (False, None)


def test_version_probe_passes_only_allowlisted_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aiteam.machine_doctor import _probe_version_command

    captured: dict[str, str] = {}
    monkeypatch.setenv("AITEAM_TEST_SUPER_SECRET", "must-not-leave-parent")

    def fake_run(command, *, env, timeout):
        captured.update(env)
        return subprocess.CompletedProcess(command, 0, "tool 1.2.3\n", "")

    monkeypatch.setattr("aiteam.machine_doctor.run_command", fake_run)

    assert _probe_version_command(["tool", "--version"]) == (True, "tool 1.2.3")
    assert "AITEAM_TEST_SUPER_SECRET" not in captured


def test_human_output_states_privacy_scope(tmp_path: Path) -> None:
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )

    summary = render_machine_inventory(report)

    assert "No se leyeron secretos, credenciales ni paths personales." in summary
    assert "[blocker] primary_adapter_not_ready" in summary
    assert "Siguiente acción:" in summary
    assert "Resultado: blocked" in summary


def test_toolchains_separate_manifest_binary_and_support_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "go.mod").write_text("module example.test\n", encoding="utf-8")
    monkeypatch.setattr(
        "aiteam.machine_doctor._resolve_runtime",
        lambda commands, os_id: None if commands[0] == "go" else f"/tools/{commands[0]}",
    )

    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )
    go = next(item for item in report["toolchains"] if item["id"] == "go")

    assert go == {
        "id": "go",
        "manifest_detected": True,
        "manifests": ["go.mod"],
        "binary_installed": False,
        "version": None,
        "executable": None,
        "source": "ecosystem_registry_v1_and_path_lookup",
        "support_claim": False,
        "diagnostic_state": "absent",
    }


def test_adapter_states_are_exact_and_do_not_infer_auth_from_installation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "aiteam.machine_doctor._resolve_runtime",
        lambda commands, os_id: f"C:\\Tools\\{commands[0]}",
    )
    profiles = [
        {
            "id": "codex_subscription",
            "provider": "openai-codex",
            "channel": "subscription",
            "adapter_type": "subscription_cli",
            "config": {"command": ["codex"], "api_key": "never-emit-me"},
            "health": {"status": "installed"},
        },
        {
            "id": "local_test",
            "provider": "ollama",
            "channel": "local",
            "adapter_type": "subscription_cli",
            "config": {"command": ["codex"]},
            "health": {"status": "ok"},
        },
    ]

    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("windows", "x86_64", "test-release"),
        adapter_profiles=profiles,
    )
    by_id = {item["id"]: item for item in report["adapters"]}

    assert by_id["codex_subscription"]["cli"]["installed"] is True
    assert by_id["codex_subscription"]["authentication_status"] == "not_checked"
    assert by_id["codex_subscription"]["health_status"] == "installed"
    assert by_id["codex_subscription"]["diagnostic_state"] == "unverified"
    assert by_id["local_test"]["authentication_status"] == "not_applicable"
    assert by_id["local_test"]["provider_runtime"]["id"] == "ollama"
    assert "never-emit-me" not in json.dumps(report)


def test_adapter_explicit_durable_auth_evidence_is_preserved(
    tmp_path: Path,
) -> None:
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=lambda command: (False, None),
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[
            {
                "id": "api-test",
                "provider": "example",
                "channel": "api",
                "adapter_type": "example_api",
                "health": {
                    "status": "failed",
                    "auth_status": "not_authenticated",
                    "detail": "C:\\Users\\private\\token.txt",
                },
            }
        ],
    )

    adapter = report["adapters"][0]
    assert adapter["authentication_status"] == "not_authenticated"
    assert adapter["health_status"] == "failed"
    assert adapter["diagnostic_state"] == "not_authenticated"
    assert "private" not in json.dumps(report)


def test_doctor_source_has_no_secret_or_inference_entrypoints() -> None:
    source = (ROOT / "aiteam" / "machine_doctor.py").read_text(encoding="utf-8")

    assert "read_secret(" not in source
    assert "profile_is_connected(" not in source
    assert "run_inference" not in source


def test_diagnostics_distinguish_adapter_states_and_primary_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def resolve(commands: list[str], os_id: str) -> str | None:
        return None if commands[0] == "missing-cli" else f"/tools/{commands[0]}"

    monkeypatch.setattr("aiteam.machine_doctor._resolve_runtime", resolve)
    profiles = [
        {
            "id": "codex_subscription",
            "provider": "openai-codex",
            "channel": "subscription",
            "adapter_type": "subscription_cli",
            "setup_class": "primary_option",
            "config": {"command": ["codex"]},
            "health": {"status": "ok", "reason": "auth_present"},
        },
        {
            "id": "missing",
            "provider": "example",
            "channel": "subscription",
            "adapter_type": "subscription_cli",
            "config": {"command": ["missing-cli"]},
            "health": {"status": "untested"},
        },
        {
            "id": "no-auth",
            "provider": "example",
            "channel": "api",
            "adapter_type": "example_api",
            "health": {"status": "failed", "auth_status": "not_authenticated"},
        },
        {
            "id": "incompatible",
            "provider": "example",
            "channel": "api",
            "adapter_type": "example_api",
            "status": "blocked_by_provider",
            "health": {"status": "untested"},
        },
        {
            "id": "degraded",
            "provider": "example",
            "channel": "api",
            "adapter_type": "example_api",
            "health": {"status": "degraded"},
        },
        {
            "id": "unverified",
            "provider": "example",
            "channel": "api",
            "adapter_type": "example_api",
            "health": {"status": "untested"},
        },
    ]

    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=profiles,
    )
    states = {item["id"]: item["diagnostic_state"] for item in report["adapters"]}

    assert states == {
        "codex_subscription": "ready",
        "degraded": "degraded",
        "incompatible": "incompatible",
        "missing": "absent",
        "no-auth": "not_authenticated",
        "unverified": "unverified",
    }
    assert report["summary"]["strict_pass"] is True
    assert report["summary"]["status"] == "degraded"
    assert "primary_adapter_not_ready" not in {
        item["code"] for item in report["diagnostics"]
    }


def test_no_verified_primary_adapter_is_a_blocker(tmp_path: Path) -> None:
    report = build_machine_inventory(
        root=tmp_path,
        command_probe=_fake_command,
        port_probe=lambda port: "not_listening",
        host=("linux", "x86_64", "test-release"),
        adapter_profiles=[],
    )

    assert report["summary"]["status"] == "blocked"
    assert report["summary"]["strict_pass"] is False
    blocker = next(
        item
        for item in report["diagnostics"]
        if item["code"] == "primary_adapter_not_ready"
    )
    assert blocker["state"] == "unverified"
    assert blocker["next_action"]["requires_human"] is True
    assert blocker["next_action"]["mutates_state"] is False
