"""Retira temporales de pytest cuando el proceso que los creó ya terminó."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import shutil
import stat


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMP_PARENT = REPO_ROOT / ".pytest-workspace-tmp"
USER_CONFIG_TEMP = REPO_ROOT / ".pytest-user-config-tmp"


def pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
                exit_code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def make_writeable(function, value, _exc_info) -> None:
        os.chmod(value, stat.S_IWRITE | stat.S_IREAD)
        function(value)

    shutil.rmtree(path, onerror=make_writeable)


def _cleanup_session_root(
    root: Path, *, include_live: bool, failures: list[str]
) -> None:
    if root.exists():
        for candidate in root.iterdir():
            match = re.fullmatch(r"session-(\d+)-[0-9a-f]+", candidate.name)
            if not include_live and match and pid_is_running(int(match.group(1))):
                continue
            try:
                _remove_tree(candidate)
            except OSError as exc:
                failures.append(f"{candidate}: {exc}")
        if root.exists() and not any(root.iterdir()):
            root.rmdir()


def cleanup(*, include_live: bool = False) -> list[str]:
    failures: list[str] = []
    _cleanup_session_root(TEMP_PARENT, include_live=include_live, failures=failures)
    _cleanup_session_root(USER_CONFIG_TEMP, include_live=include_live, failures=failures)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-live", action="store_true")
    args = parser.parse_args()
    failures = cleanup(include_live=args.include_live)
    for failure in failures:
        print(f"cleanup warning: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
