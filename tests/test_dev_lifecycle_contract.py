from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aiteam.dev_lifecycle_contract import (
    ACTION_IDS,
    RECOVERY_CASE_IDS,
    lifecycle_command,
    lifecycle_manifest,
    load_dev_lifecycle_contract,
    validate_dev_lifecycle_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def test_contract_has_one_ordered_action_surface_for_windows_and_posix() -> None:
    contract = load_dev_lifecycle_contract()

    assert tuple(item["id"] for item in contract["actions"]) == ACTION_IDS
    assert contract["contract_status"] == "preview"
    for action in contract["actions"]:
        assert action["windows"]
        assert action["posix"]
        assert action["detached"] is False


def test_lifecycle_manifest_is_deterministic_and_posix_shared() -> None:
    first = lifecycle_manifest(target_platform="linux")
    second = lifecycle_manifest(target_platform="linux")
    macos = lifecycle_manifest(target_platform="macos")

    assert first == second
    assert first["commands"] == macos["commands"]
    assert first["commands"]["prepare"] == ["sh", "scripts/prepare_dev_env.sh"]
    assert first["commands"]["migrate"][-1] == "--json"
    assert tuple(item["id"] for item in first["recovery_matrix"]) == RECOVERY_CASE_IDS
    assert all(item["posix"] != "verified" for item in first["recovery_matrix"])


def test_windows_commands_preserve_existing_entrypoints() -> None:
    assert lifecycle_command("prepare", target_platform="windows") == [
        "scripts\\prepare_dev_env.bat"
    ]
    assert lifecycle_command("start", target_platform="windows") == [
        "start_ide.bat"
    ]
    assert lifecycle_command("stop", target_platform="windows") == [
        "stop_ide.bat"
    ]
    assert lifecycle_command("test", target_platform="windows") == [
        "scripts\\pytest_local.bat"
    ]


def test_contract_rejects_missing_action_or_authority_drift() -> None:
    contract = load_dev_lifecycle_contract()
    missing = copy.deepcopy(contract)
    missing["actions"].pop()
    with pytest.raises(ValueError, match="coverage drift"):
        validate_dev_lifecycle_contract(missing)

    global_install = copy.deepcopy(contract)
    global_install["invariants"]["global_dependency_install"] = True
    with pytest.raises(ValueError, match="invariants drift"):
        validate_dev_lifecycle_contract(global_install)

    missing_recovery = copy.deepcopy(contract)
    missing_recovery["recovery_matrix"].pop()
    with pytest.raises(ValueError, match="recovery matrix coverage drift"):
        validate_dev_lifecycle_contract(missing_recovery)


def test_contract_rejects_frontend_outside_checkout() -> None:
    contract = load_dev_lifecycle_contract()
    contract["actions"][0]["posix"] = ["sh", "../prepare.sh"]

    with pytest.raises(ValueError, match="inside checkout"):
        validate_dev_lifecycle_contract(contract)


def test_posix_frontends_are_local_only_and_do_not_depend_on_powershell() -> None:
    paths = [
        ROOT / "scripts" / "prepare_dev_env.sh",
        ROOT / "scripts" / "python_local.sh",
        ROOT / "scripts" / "pytest_local.sh",
        ROOT / "start_ide.sh",
        ROOT / "stop_ide.sh",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert all(path.is_file() for path in paths)
    assert "powershell" not in combined.lower()
    assert "sudo " not in combined
    assert "npm install -g" not in combined
    assert "pip install --user" not in combined
    assert "curl " not in combined
    assert "venv/bin/python" in combined


def test_start_stop_use_checkout_venv_and_owned_process_registry() -> None:
    launcher = (ROOT / "scripts" / "dev.mjs").read_text(encoding="utf-8")
    start = (ROOT / "start_ide.sh").read_text(encoding="utf-8")
    stop = (ROOT / "stop_ide.sh").read_text(encoding="utf-8")
    windows_start = (ROOT / "start_ide.bat").read_text(encoding="utf-8")
    windows_stop = (ROOT / "stop_ide.bat").read_text(encoding="utf-8")

    assert "join(ROOT, 'venv', 'bin', 'python')" in launcher
    assert 'exec node "$ROOT_DIR/scripts/dev.mjs"' in start
    assert "ide_processes.py" in launcher
    assert "ide_processes.py" in stop
    assert "ide_processes.py" in windows_start
    assert "ide_processes.py" in windows_stop
    assert "kill -9" not in stop
    assert "kill_port" not in windows_start
    assert "kill_port" not in windows_stop
    assert "kill_signature" not in windows_stop


def test_bootstrap_requires_versioned_locks_and_has_concurrency_guards() -> None:
    windows = (ROOT / "scripts" / "prepare_dev_env.ps1").read_text(encoding="utf-8")
    posix = (ROOT / "scripts" / "prepare_dev_env.sh").read_text(encoding="utf-8")
    venv = (ROOT / "scripts" / "ensure_local_venv.ps1").read_text(encoding="utf-8")
    frontend = (ROOT / "scripts" / "ensure_frontend_deps.ps1").read_text(
        encoding="utf-8"
    )

    assert (ROOT / "requirements-dev.lock").is_file()
    assert "FileShare]::None" in windows
    assert "if ($ownsLock)" in windows
    assert ".bootstrap.lock.d" in posix
    assert "owner aun no observable" in posix
    assert "requirements-dev.lock" in venv
    assert "--no-deps" in venv
    assert '"--upgrade", "pip"' not in venv
    assert '@("ci"' in frontend
    assert '@("install"' not in frontend


@pytest.mark.skipif(sys.platform != "win32", reason="frontend PowerShell de Windows")
def test_windows_bootstrap_missing_lock_fails_before_runtime_mutation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout con espacio ñ 日本語"
    scripts = root / "scripts"
    frontend = root / "ide-frontend"
    scripts.mkdir(parents=True)
    frontend.mkdir()
    shutil.copy2(ROOT / "scripts" / "prepare_dev_env.ps1", scripts)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (frontend / "package.json").write_text("{}\n", encoding="utf-8")
    (frontend / "package-lock.json").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "prepare_dev_env.ps1"),
            "-LockTimeoutSeconds",
            "1",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert "faltan inputs versionados" in (result.stdout + result.stderr)
    assert not (root / "runtime").exists()


def test_unknown_action_and_platform_fail_closed() -> None:
    with pytest.raises(ValueError, match="unsupported lifecycle action"):
        lifecycle_command("deploy", target_platform="linux")
    with pytest.raises(ValueError, match="unsupported lifecycle platform"):
        lifecycle_command("prepare", target_platform="plan9")


def test_contract_frontends_resolve_inside_unicode_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "espacio ñ 日本語"
    checkout.mkdir()
    contract = load_dev_lifecycle_contract()
    for action in contract["actions"]:
        for target in ("windows", "posix"):
            for item in action[target]:
                normalized = item.replace("\\", "/")
                if not normalized.endswith((".bat", ".sh", ".py")):
                    continue
                path = checkout / normalized
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

    validate_dev_lifecycle_contract(contract, root=checkout)
