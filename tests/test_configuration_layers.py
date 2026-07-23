from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from aiteam.configuration_layers import (
    deep_merge,
    load_configuration_contract,
    resolve_configuration,
)
from aiteam.user_config import (
    get_effective_app_settings,
    load_adapter_profiles,
    upsert_adapter_profile,
    update_app_settings,
)


ROOT = Path(__file__).resolve().parents[1]


def test_contract_fixes_precedence_ownership_and_secret_boundary() -> None:
    contract = load_configuration_contract()

    assert [row["id"] for row in contract["precedence_low_to_high"]] == [
        "versioned_defaults",
        "user_machine",
        "environment",
        "project",
        "run_override",
    ]
    assert all(row["owner"] for row in contract["precedence_low_to_high"])
    assert all(row["may_contain_secrets"] is False for row in contract["precedence_low_to_high"])
    assert contract["secrets"]["transport"] == "reference_only"
    assert "*.db" in contract["state_not_configuration"]
    assert contract["upgrade_contract"]["entrypoint_windows"] == "scripts/update_windows.bat"


def test_resolver_deep_merges_and_tracks_leaf_provenance() -> None:
    result = resolve_configuration(
        [
            (
                "versioned_defaults",
                {"autonomy": "supervised", "adapter": {"model": "default", "timeout": 30}},
            ),
            ("user_machine", {"adapter": {"timeout": 60}}),
            ("environment", {"autonomy": "autonomous"}),
            ("project", {"adapter": {"model": "project"}}),
            ("run_override", {"adapter": {"effort": "low"}}),
        ]
    )

    assert result.values == {
        "autonomy": "autonomous",
        "adapter": {"model": "project", "timeout": 60, "effort": "low"},
    }
    assert result.source_for("autonomy") == "environment"
    assert result.source_for("adapter.timeout") == "user_machine"
    assert result.source_for("adapter.model") == "project"
    assert result.source_for("adapter.effort") == "run_override"


def test_resolver_rejects_inline_secrets_but_accepts_references() -> None:
    with pytest.raises(ValueError, match="inline secret"):
        resolve_configuration([("user_machine", {"api_key": "do-not-store"})])

    result = resolve_configuration(
        [("user_machine", {"api_key_ref": "secret:openai:default"})]
    )
    assert result.values["api_key_ref"] == "secret:openai:default"


def test_deep_merge_treats_lists_as_atomic_overrides() -> None:
    assert deep_merge(
        {"nested": {"left": 1, "items": [1, 2]}},
        {"nested": {"right": 2, "items": [3]}},
    ) == {"nested": {"left": 1, "right": 2, "items": [3]}}


def test_existing_builtin_profile_override_inherits_new_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    upsert_adapter_profile(
        {
            "id": "codex_subscription",
            "label": "Codex de esta maquina",
            "config": {"sandbox": "read-only"},
        }
    )

    profile = next(
        row for row in load_adapter_profiles() if row["id"] == "codex_subscription"
    )

    assert profile["label"] == "Codex de esta maquina"
    assert profile["adapter_type"] == "subscription_cli"
    assert profile["channel"] == "subscription"
    assert profile["config"]["sandbox"] == "read-only"
    assert profile["config"]["approval_policy"] == "never"


def test_environment_overrides_machine_setting_with_visible_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    update_app_settings({"projects_root": str(tmp_path / "from-user")})
    monkeypatch.setenv("AITEAM_PROJECTS_ROOT", str(tmp_path / "from-env"))

    effective = get_effective_app_settings()

    assert Path(effective["values"]["projects_root"]) == tmp_path / "from-env"
    assert effective["provenance"]["projects_root"] == "environment"


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap contract")
def test_legacy_runtime_json_is_merged_without_losing_local_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "legacy-install"
    config = root / "config"
    runtime = root / "runtime"
    config.mkdir(parents=True)
    runtime.mkdir()
    control_template = {
        "heartbeat_interval_ms": 2000,
        "noise_policy": {"default_gate_strength": "light"},
    }
    local_control = {
        "heartbeat_interval_ms": 9000,
        "local_only": True,
    }
    (config / "control_plane.example.json").write_text(
        json.dumps(control_template), encoding="utf-8"
    )
    (config / "agents.example.json").write_text(
        json.dumps({"profiles": {}}), encoding="utf-8"
    )
    control_path = runtime / "control_plane.json"
    control_path.write_text(json.dumps(local_control), encoding="utf-8")
    (runtime / "agents.json").write_text(json.dumps({"profiles": {}}), encoding="utf-8")

    _run_runtime_sync(root)
    first_hash = control_path.read_bytes()
    merged = json.loads(control_path.read_text(encoding="utf-8-sig"))

    assert merged["heartbeat_interval_ms"] == 9000
    assert merged["local_only"] is True
    assert merged["noise_policy"] == {"default_gate_strength": "light"}
    assert json.loads(
        (runtime / "control_plane.json.pre_template_sync.bak").read_text(encoding="utf-8")
    ) == local_control
    state = json.loads(
        (runtime / ".template_sync_state.json").read_text(encoding="utf-8-sig")
    )
    assert state["control_plane.json"]["mode"] == "local_override"

    _run_runtime_sync(root)
    assert control_path.read_bytes() == first_hash

    control_template["new_default"] = {"enabled": True}
    (config / "control_plane.example.json").write_text(
        json.dumps(control_template), encoding="utf-8"
    )
    _run_runtime_sync(root)
    upgraded = json.loads(control_path.read_text(encoding="utf-8-sig"))
    assert upgraded["heartbeat_interval_ms"] == 9000
    assert upgraded["new_default"] == {"enabled": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap contract")
def test_invalid_legacy_runtime_json_fails_closed_and_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "invalid-install"
    config = root / "config"
    runtime = root / "runtime"
    config.mkdir(parents=True)
    runtime.mkdir()
    (config / "control_plane.example.json").write_text("{}", encoding="utf-8")
    (config / "agents.example.json").write_text("{}", encoding="utf-8")
    target = runtime / "control_plane.json"
    target.write_text("{broken", encoding="utf-8")
    (runtime / "agents.json").write_text("{}", encoding="utf-8")

    result = _run_runtime_sync(root, check=False)

    assert result.returncode == 1
    assert target.read_text(encoding="utf-8") == "{broken"
    assert not (runtime / "control_plane.json.pre_template_sync.bak").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap contract")
def test_template_upgrade_only_preserves_values_changed_from_baseline(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tracked-install"
    config = root / "config"
    config.mkdir(parents=True)
    template_path = config / "control_plane.example.json"
    template_path.write_text(
        json.dumps({"heartbeat": 2, "nested": {"gate": "light"}}), encoding="utf-8"
    )
    (config / "agents.example.json").write_text("{}", encoding="utf-8")
    _run_runtime_sync(root)

    target = root / "runtime" / "control_plane.json"
    local = json.loads(target.read_text(encoding="utf-8-sig"))
    local["local_only"] = True
    target.write_text(json.dumps(local), encoding="utf-8")
    template_path.write_text(
        json.dumps({"heartbeat": 3, "nested": {"gate": "strict"}}), encoding="utf-8"
    )

    _run_runtime_sync(root)
    upgraded = json.loads(target.read_text(encoding="utf-8-sig"))

    assert upgraded == {
        "heartbeat": 3,
        "nested": {"gate": "strict"},
        "local_only": True,
    }


def _run_runtime_sync(root: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "ensure_local_runtime.ps1"),
            "-RootDir",
            str(root),
            "-Quiet",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result
