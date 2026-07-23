from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam.config_portability import (
    PortableConfigurationError,
    export_portable_configuration,
    import_portable_configuration,
    inspect_portable_configuration,
)


ROOT = Path(__file__).resolve().parents[1]


def test_export_excludes_secrets_health_state_and_absolute_paths(tmp_path: Path) -> None:
    source = tmp_path / "source-user"
    project = tmp_path / "source-project"
    runtime = project / ".aiteam"
    source.mkdir()
    runtime.mkdir(parents=True)
    (source / "settings.json").write_text(
        json.dumps({"projects_root": r"C:\private\projects", "theme": "dark"}),
        encoding="utf-8",
    )
    (source / "adapter_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "custom_api",
                        "label": "Custom",
                        "adapter_type": "openai_compatible_api",
                        "channel": "api",
                        "privacy_note": "accidental sk-supersecretvalue12345",
                        "config": {
                            "model": "vendor/model",
                            "api_key": "sk-never-export-this",
                            "api_key_ref": "secret:custom:default",
                            "command": [r"C:\private\bin\provider.exe"],
                            "cache": "runtime/provider-cache",
                            "headers": {"Authorization": "Bearer never"},
                        },
                        "health": {"status": "ok"},
                        "model_options": [
                            {
                                "value": "vendor/model",
                                "available": True,
                                "selectable": True,
                                "role_score": 99,
                                "recommended": True,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (source / "secrets.json").write_text(
        '{"marker": "SECRET_STORE_MUST_NOT_TRAVEL"}', encoding="utf-8"
    )
    (source / "adapter_health.json").write_text(
        '{"marker": "HEALTH_MUST_NOT_TRAVEL"}', encoding="utf-8"
    )
    (runtime / "project_config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "adapter_profile_ids": ["custom_api"],
                "autonomy": "supervised",
                "instructions": "FREEFORM_MUST_STAY_IN_PROJECT_GIT",
                "workspace_path": r"C:\private\projects\source-project",
            }
        ),
        encoding="utf-8",
    )
    (runtime / "aiteam.db").write_bytes(b"SQLITE_STATE_MUST_NOT_TRAVEL")

    package = export_portable_configuration(
        source_user_config_dir=source,
        project_dir=project,
    )
    encoded = json.dumps(package, ensure_ascii=False)

    assert inspect_portable_configuration(package)["valid"] is True
    assert package["user"]["settings"] == {"theme": "dark"}
    profile = package["user"]["adapter_profiles"][0]
    assert profile["config"] == {
        "api_key_ref": "secret:custom:default",
        "model": "vendor/model",
    }
    assert profile["model_options"] == [{"value": "vendor/model"}]
    assert "health" not in profile
    assert "privacy_note" not in profile
    assert package["project"]["config"] == {
        "version": 1,
        "adapter_profile_ids": ["custom_api"],
        "autonomy": "supervised",
    }
    assert "SECRET_STORE_MUST_NOT_TRAVEL" not in encoded
    assert "HEALTH_MUST_NOT_TRAVEL" not in encoded
    assert "SQLITE_STATE_MUST_NOT_TRAVEL" not in encoded
    assert "FREEFORM_MUST_STAY_IN_PROJECT_GIT" not in encoded
    assert r"C:\private" not in encoded
    assert {row["code"] for row in package["omissions"]} >= {
        "absolute_path_removed",
        "inline_secret_removed",
        "machine_setting_excluded",
        "project_field_excluded",
        "secret_container_removed",
        "secret_pattern_removed",
        "freeform_project_content_excluded",
        "runtime_state_removed",
    }
    assert package["requires_machine_setup"][0]["selectable_after_import"] is False


def test_import_is_dry_run_by_default_and_apply_merges_without_secrets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (source / "adapter_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "custom",
                        "label": "Imported",
                        "adapter_type": "openai_compatible_api",
                        "config": {
                            "model": "vendor/new",
                            "api_key_ref": "secret:vendor:default",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (target / "settings.json").write_text(
        json.dumps({"projects_root": r"D:\local\projects", "locale": "es"}),
        encoding="utf-8",
    )
    (target / "adapter_profiles.json").write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "custom",
                        "label": "Old",
                        "config": {"timeout": 90},
                    },
                    {"id": "keep", "label": "Keep", "config": {}},
                ]
            }
        ),
        encoding="utf-8",
    )
    (target / "adapter_health.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "custom": {"status": "ok"},
                    "keep": {"status": "ok"},
                }
            }
        ),
        encoding="utf-8",
    )
    secret_path = target / "secrets.json"
    secret_path.write_text('{"keep": "LOCAL_SECRET"}', encoding="utf-8")
    package = export_portable_configuration(source_user_config_dir=source)
    original_profiles = (target / "adapter_profiles.json").read_text(encoding="utf-8")

    preflight = import_portable_configuration(
        package,
        target_user_config_dir=target,
    )

    assert preflight["applied"] is False
    assert preflight["profile_collisions"] == ["custom"]
    assert (target / "adapter_profiles.json").read_text(encoding="utf-8") == original_profiles

    report = import_portable_configuration(
        package,
        target_user_config_dir=target,
        apply=True,
    )

    assert report["applied"] is True
    settings = json.loads((target / "settings.json").read_text(encoding="utf-8"))
    assert settings == {
        "projects_root": r"D:\local\projects",
        "locale": "es",
        "theme": "dark",
    }
    profiles = {
        row["id"]: row
        for row in json.loads(
            (target / "adapter_profiles.json").read_text(encoding="utf-8")
        )["profiles"]
    }
    assert profiles["custom"]["label"] == "Imported"
    assert profiles["custom"]["config"] == {
        "timeout": 90,
        "model": "vendor/new",
        "api_key_ref": "secret:vendor:default",
    }
    assert profiles["keep"]["label"] == "Keep"
    health = json.loads((target / "adapter_health.json").read_text(encoding="utf-8"))
    assert health["profiles"]["custom"] == {
        "status": "untested",
        "reason": "portable_configuration_imported_requires_retest",
    }
    assert health["profiles"]["keep"] == {"status": "ok"}
    assert secret_path.read_text(encoding="utf-8") == '{"keep": "LOCAL_SECRET"}'

    second = import_portable_configuration(
        package,
        target_user_config_dir=target,
        apply=True,
    )
    profiles_after_second = json.loads(
        (target / "adapter_profiles.json").read_text(encoding="utf-8")
    )["profiles"]
    assert second["applied"] is True
    assert [row["id"] for row in profiles_after_second] == ["custom", "keep"]


def test_project_import_requires_explicit_target_and_preserves_target_fields(
    tmp_path: Path,
) -> None:
    source_user = tmp_path / "source-user"
    source_project = tmp_path / "source-project"
    target_project = tmp_path / "target-project"
    source_user.mkdir()
    (source_project / ".aiteam").mkdir(parents=True)
    target_project.mkdir()
    (source_project / ".aiteam" / "project_config.json").write_text(
        '{"version": 1, "adapter_profile_ids": ["codex_subscription"], "autonomy": "autonomous"}',
        encoding="utf-8",
    )
    package = export_portable_configuration(
        source_user_config_dir=source_user,
        project_dir=source_project,
    )

    preflight = import_portable_configuration(package)
    assert preflight["blockers"] == ["project_target_required"]
    with pytest.raises(PortableConfigurationError, match="project_target_required"):
        import_portable_configuration(package, apply=True)

    runtime = target_project / ".aiteam"
    runtime.mkdir()
    (runtime / "project_config.json").write_text(
        '{"target_only": true, "autonomy": "supervised"}', encoding="utf-8"
    )
    report = import_portable_configuration(
        package,
        target_user_config_dir=tmp_path / "target-user",
        project_dir=target_project,
        apply=True,
    )

    assert report["applied"] is True
    project_config = json.loads(
        (runtime / "project_config.json").read_text(encoding="utf-8")
    )
    assert project_config == {
        "target_only": True,
        "version": 1,
        "adapter_profile_ids": ["codex_subscription"],
        "autonomy": "autonomous",
    }
    assert not (runtime / "aiteam.db").exists()


def test_integrity_and_portability_checks_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    package = export_portable_configuration(source_user_config_dir=source)

    tampered = json.loads(json.dumps(package))
    tampered["user"]["settings"]["theme"] = "tampered"
    with pytest.raises(PortableConfigurationError, match="integrity mismatch"):
        inspect_portable_configuration(tampered)

    path_injected = json.loads(json.dumps(package))
    path_injected["user"]["settings"]["theme"] = r"C:\foreign\theme"
    path_injected["integrity"]["payload_sha256"] = _hash_payload(path_injected)
    with pytest.raises(PortableConfigurationError, match="absolute machine path"):
        inspect_portable_configuration(path_injected)

    state_injected = json.loads(json.dumps(package))
    state_injected["user"]["adapter_profiles"] = [
        {"id": "bad", "health": {"status": "ok"}}
    ]
    state_injected["integrity"]["payload_sha256"] = _hash_payload(state_injected)
    with pytest.raises(PortableConfigurationError, match="runtime state field"):
        inspect_portable_configuration(state_injected)

    project_injected = json.loads(json.dumps(package))
    project_injected["project"] = {"config": {"team": {"lead": "copied"}}}
    project_injected["integrity"]["payload_sha256"] = _hash_payload(project_injected)
    with pytest.raises(PortableConfigurationError, match="forbidden fields"):
        inspect_portable_configuration(project_injected)


def test_preflight_validates_target_before_any_write(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    package = export_portable_configuration(source_user_config_dir=source)
    invalid = target / "adapter_profiles.json"
    invalid.write_text("{broken", encoding="utf-8")

    with pytest.raises(PortableConfigurationError, match="invalid configuration file"):
        import_portable_configuration(
            package,
            target_user_config_dir=target,
            apply=False,
        )

    assert invalid.read_text(encoding="utf-8") == "{broken"
    assert not (target / "settings.json").exists()
    assert not (target / "adapter_health.json").exists()


def test_cli_export_inspect_and_import_preflight(tmp_path: Path) -> None:
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    output = tmp_path / "portable.json"
    env = os.environ.copy()
    env["AITEAM_USER_CONFIG_DIR"] = str(user_dir)

    export_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "config_portability.py"),
            "export",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    inspect_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "config_portability.py"),
            "inspect",
            "--input",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    import_result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "config_portability.py"),
            "import",
            "--input",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert export_result.returncode == 0, export_result.stderr
    assert inspect_result.returncode == 0, inspect_result.stderr
    assert import_result.returncode == 0, import_result.stderr
    assert json.loads(inspect_result.stdout)["valid"] is True
    assert json.loads(import_result.stdout)["applied"] is False


def _hash_payload(package: dict) -> str:
    payload = {key: value for key, value in package.items() if key != "integrity"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
