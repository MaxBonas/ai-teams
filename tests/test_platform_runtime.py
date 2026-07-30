from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aiteam.adapters.registry import AdapterDescriptor
from aiteam.adapters.subprocess_adapter import SubprocessAdapterRuntime
from aiteam.platform_runtime import (
    architecture_id,
    configure_utf8_stdio,
    executable_candidates,
    path_comparison_key,
    probe_filesystem_boundary,
    probe_timeout_cleanup,
    process_group_popen_options,
    provider_cli_fingerprint,
    resolve_executable,
    resolve_provider_cli,
    run_command,
    venv_python_candidates,
)


def test_platform_identifiers_normalize_common_architectures() -> None:
    assert architecture_id(machine="AMD64") == "x86_64"
    assert architecture_id(machine="aarch64") == "arm64"


def test_path_comparison_respects_target_os_semantics() -> None:
    assert path_comparison_key(r"C:\Work\AI", os_id="windows") == path_comparison_key(
        r"c:/work/ai", os_id="windows"
    )
    assert path_comparison_key("/Work/AI", os_id="linux") != path_comparison_key(
        "/work/ai", os_id="linux"
    )


def test_executable_resolution_uses_real_windows_shims() -> None:
    available = {
        "codex.cmd": r"C:\Tools\codex.cmd",
        "codex": r"C:\Users\demo\AppData\Local\Microsoft\WindowsApps\codex",
    }

    assert executable_candidates("codex", os_id="windows") == [
        "codex.cmd",
        "codex.exe",
        "codex",
    ]
    assert (
        resolve_executable(
            "codex",
            os_id="windows",
            which=lambda name: available.get(name),
        )
        == r"C:\Tools\codex.cmd"
    )


def test_provider_cli_resolution_shares_antigravity_known_location(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "agy" / "bin" / "agy.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture")

    assert resolve_provider_cli(
        "agy",
        ["agy.exe", "agy"],
        os_id="windows",
        which=lambda _name: None,
        environment={"LOCALAPPDATA": str(tmp_path)},
    ) == str(binary)


def test_provider_cli_fingerprint_is_stable_and_binds_path_and_content(
    tmp_path: Path,
) -> None:
    first = tmp_path / "one" / "tool.cmd"
    second = tmp_path / "two" / "tool.cmd"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"same-version-output")
    second.write_bytes(b"same-version-output")

    first_digest = provider_cli_fingerprint(first, os_id="windows")

    assert first_digest == provider_cli_fingerprint(first, os_id="windows")
    assert first_digest != provider_cli_fingerprint(second, os_id="windows")
    second.write_bytes(b"different-content")
    assert first_digest != provider_cli_fingerprint(second, os_id="windows")
    assert first_digest is not None
    assert str(tmp_path).casefold() not in first_digest.casefold()


def test_provider_cli_fingerprint_fails_closed_for_missing_file(
    tmp_path: Path,
) -> None:
    assert provider_cli_fingerprint(tmp_path / "missing.exe") is None


def test_virtualenv_layout_is_platform_specific(tmp_path: Path) -> None:
    assert venv_python_candidates(tmp_path, os_id="windows") == [
        tmp_path / "venv" / "Scripts" / "python.exe"
    ]
    assert venv_python_candidates(tmp_path, os_id="linux") == [
        tmp_path / "venv" / "bin" / "python",
        tmp_path / "venv" / "bin" / "python3",
    ]


def test_process_group_options_are_explicit() -> None:
    assert process_group_popen_options(os_id="windows")["creationflags"] > 0
    assert process_group_popen_options(os_id="linux") == {"start_new_session": True}


def test_utf8_command_roundtrip_in_unicode_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "espacio ñ 日本語"
    workspace.mkdir()

    completed = run_command(
        [sys.executable, "-c", "print('salida ñ 日本語')"],
        cwd=workspace,
        timeout=5,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "salida ñ 日本語"
    assert completed.stderr == ""


def test_utf8_stdio_configuration_is_explicit_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class Stream:
        def reconfigure(self, *, encoding: str, errors: str) -> None:
            calls.append((encoding, errors))

    monkeypatch.setattr(
        "aiteam.platform_runtime.sys",
        SimpleNamespace(platform="win32", stdout=Stream(), stderr=Stream()),
    )

    configure_utf8_stdio()

    assert calls == [("utf-8", "replace"), ("utf-8", "replace")]


def test_timeout_is_reported_and_process_group_is_reaped() -> None:
    result = probe_timeout_cleanup()

    assert result["timeout_observed"] is True
    assert result["elapsed_sec"] < 8
    assert result["process_group_strategy"] != "none"


def test_filesystem_probe_covers_unicode_case_and_permissions(tmp_path: Path) -> None:
    result = probe_filesystem_boundary(tmp_path)

    assert result["spaces_and_unicode_roundtrip"] is True
    assert result["utf8_roundtrip"] is True
    assert isinstance(result["case_sensitive_observed"], bool)
    assert result["permission_probe"] is True


def test_subprocess_adapter_uses_utf8_boundary() -> None:
    runtime = SubprocessAdapterRuntime(
        descriptor=AdapterDescriptor(
            adapter_type="test_subprocess",
            channel="subscription",
        ),
        command=[sys.executable, "-c", "print('acción correcta 日本語')"],
    )

    result = runtime.execute({}, {})

    assert result.status == "completed"
    assert result.output is not None
    assert "acción correcta 日本語" in result.output


def test_run_command_rejects_conflicting_stdin_modes() -> None:
    with pytest.raises(ValueError, match="stdin and input"):
        run_command(
            [sys.executable, "-c", "pass"],
            input="payload",
            stdin=subprocess.DEVNULL,
        )
