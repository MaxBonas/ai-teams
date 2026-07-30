from __future__ import annotations

import hashlib
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping, Sequence

UTF8_SUBPROCESS_OPTIONS: dict[str, Any] = {
    "text": True,
    "encoding": "utf-8",
    "errors": "replace",
}
PROVIDER_CLI_FINGERPRINT_DOMAIN = b"aiteam-provider-cli-identity-v1\0"


def configure_utf8_stdio() -> None:
    """Make CLI JSON/text output portable across Windows console encodings."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            continue


def platform_id(*, system: str | None = None) -> str:
    observed = (system or platform.system()).strip().lower()
    if observed.startswith("win"):
        return "windows"
    if observed in {"darwin", "mac", "macos"}:
        return "macos"
    return "linux"


def architecture_id(*, machine: str | None = None) -> str:
    observed = (machine or platform.machine()).strip().lower()
    if observed in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if observed in {"arm64", "aarch64"}:
        return "arm64"
    return observed or "unknown"


def path_comparison_key(value: str | Path, *, os_id: str | None = None) -> str:
    target_os = os_id or platform_id()
    text = str(value)
    if target_os == "windows":
        return str(PureWindowsPath(text)).replace("/", "\\").casefold()
    return str(PurePosixPath(text))


def paths_equivalent(
    left: str | Path,
    right: str | Path,
    *,
    os_id: str | None = None,
    resolve: bool = True,
) -> bool:
    if resolve:
        left = Path(left).resolve()
        right = Path(right).resolve()
    return path_comparison_key(left, os_id=os_id) == path_comparison_key(
        right, os_id=os_id
    )


def executable_candidates(command: str, *, os_id: str | None = None) -> list[str]:
    target_os = os_id or platform_id()
    clean = str(command or "").strip()
    if not clean:
        return []
    if target_os != "windows" or clean.lower().endswith((".exe", ".cmd", ".bat")):
        return [clean]
    return [f"{clean}.cmd", f"{clean}.exe", clean]


def is_usable_executable_path(value: str, *, os_id: str | None = None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    target_os = os_id or platform_id()
    if target_os != "windows":
        return True
    normalized = text.replace("/", "\\").casefold()
    if "\\windowsapps\\" in normalized:
        return False
    return PureWindowsPath(text).suffix.lower() in {".exe", ".cmd", ".bat"}


def resolve_executable(
    command: str,
    *,
    known_candidates: Sequence[str | Path] = (),
    os_id: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> str | None:
    target_os = os_id or platform_id()
    resolver = which or shutil.which
    for candidate in known_candidates:
        text = str(candidate)
        if Path(text).is_file() and is_usable_executable_path(text, os_id=target_os):
            return text
    for candidate in executable_candidates(command, os_id=target_os):
        resolved = resolver(candidate)
        if resolved and is_usable_executable_path(resolved, os_id=target_os):
            return resolved
    return None


def resolve_provider_cli(
    cli_id: str,
    commands: Sequence[str],
    *,
    os_id: str | None = None,
    which: Callable[[str], str | None] | None = None,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a provider CLI through one shared doctor/runtime boundary."""
    target_os = os_id or platform_id()
    env = os.environ if environment is None else environment
    clean_id = str(cli_id or "").strip().lower()
    known: list[Path] = []
    if target_os == "windows" and clean_id in {"agy", "antigravity"}:
        local_app_data = str(env.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            known.append(Path(local_app_data) / "agy" / "bin" / "agy.exe")
    for command in commands:
        resolved = resolve_executable(
            str(command),
            known_candidates=known,
            os_id=target_os,
            which=which,
        )
        if resolved:
            return resolved
    return None


def provider_cli_fingerprint(
    executable: str | Path | None,
    *,
    os_id: str | None = None,
) -> str | None:
    """Return a path-redacted digest bound to resolution and file content."""
    if not executable:
        return None
    try:
        resolved = Path(executable).resolve(strict=True)
        content_hash = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_hash.update(chunk)
    except (OSError, RuntimeError):
        return None
    identity_hash = hashlib.sha256(PROVIDER_CLI_FINGERPRINT_DOMAIN)
    identity_hash.update(
        path_comparison_key(resolved, os_id=os_id).encode("utf-8")
    )
    identity_hash.update(b"\0")
    identity_hash.update(content_hash.digest())
    return identity_hash.hexdigest()


def process_group_popen_options(*, os_id: str | None = None) -> dict[str, Any]:
    target_os = os_id or platform_id()
    if target_os == "windows":
        return {
            "creationflags": int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            )
        }
    return {"start_new_session": True}


def run_command(
    command: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    input: str | None = None,
    stdin: int | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a UTF-8 command in its own group and reap the whole group on timeout."""
    if input is not None and stdin is not None:
        raise ValueError("stdin and input may not be used together")
    child_env = dict(os.environ if env is None else env)
    child_env.setdefault("PYTHONUTF8", "1")
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.Popen(
        [str(item) for item in command],
        env=child_env,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.PIPE if input is not None else stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        **UTF8_SUBPROCESS_OPTIONS,
        **process_group_popen_options(),
    )
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(proc)
        stdout, stderr = proc.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=_coalesce_timeout_output(exc.output, stdout),
            stderr=_coalesce_timeout_output(exc.stderr, stderr),
        ) from exc
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=int(proc.returncode or 0),
        stdout=stdout or "",
        stderr=stderr or "",
    )


def terminate_process_tree(
    proc: subprocess.Popen[Any],
    *,
    grace_sec: float = 2.0,
    os_id: str | None = None,
) -> None:
    if proc.poll() is not None:
        return
    target_os = os_id or platform_id()
    if target_os == "windows":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1.0, grace_sec),
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=max(0.1, grace_sec))
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
    try:
        proc.wait(timeout=max(0.1, grace_sec))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=max(0.1, grace_sec))


def venv_python_candidates(root: Path, *, os_id: str | None = None) -> list[Path]:
    target_os = os_id or platform_id()
    if target_os == "windows":
        return [Path(root) / "venv" / "Scripts" / "python.exe"]
    return [
        Path(root) / "venv" / "bin" / "python",
        Path(root) / "venv" / "bin" / "python3",
    ]


def probe_filesystem_boundary(base_dir: Path | None = None) -> dict[str, Any]:
    parent = Path(base_dir).resolve() if base_dir is not None else None
    with tempfile.TemporaryDirectory(
        prefix="aiteam espacio ñ ",
        dir=str(parent) if parent is not None else None,
    ) as temporary:
        root = Path(temporary)
        unicode_file = root / "Árbol 日本語.txt"
        unicode_file.write_text("línea\r\nsegunda\n", encoding="utf-8", newline="\n")
        roundtrip = unicode_file.read_text(encoding="utf-8")
        alternate_case = unicode_file.with_name(unicode_file.name.swapcase())
        case_sensitive = not alternate_case.exists()
        executable = root / ("probe.cmd" if platform_id() == "windows" else "probe.sh")
        executable.write_text(
            "@echo off\r\necho ok\r\n"
            if platform_id() == "windows"
            else "#!/bin/sh\nprintf ok\n",
            encoding="utf-8",
            newline="",
        )
        if platform_id() != "windows":
            executable.chmod(0o700)
        return {
            "spaces_and_unicode_roundtrip": roundtrip == "línea\nsegunda\n",
            "case_sensitive_observed": case_sensitive,
            "utf8_roundtrip": "日本語" in unicode_file.name,
            "permission_probe": os.access(executable, os.X_OK),
        }


def probe_timeout_cleanup() -> dict[str, Any]:
    started = time.monotonic()
    try:
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.2,
        )
    except subprocess.TimeoutExpired:
        return {
            "timeout_observed": True,
            "elapsed_sec": round(time.monotonic() - started, 3),
            "process_group_strategy": (
                "windows_taskkill_tree"
                if platform_id() == "windows"
                else "posix_process_group"
            ),
        }
    return {
        "timeout_observed": False,
        "elapsed_sec": round(time.monotonic() - started, 3),
        "process_group_strategy": "none",
    }


def _coalesce_timeout_output(
    partial: str | bytes | None, final: str | bytes | None
) -> str:
    value = final if final not in (None, "") else partial
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
