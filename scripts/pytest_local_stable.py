from __future__ import annotations

import os
from pathlib import Path
import gc
import shutil
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _mkdir(path: Path) -> None:
    quoted = str(path).replace("'", "''")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"New-Item -ItemType Directory -Force -Path '{quoted}' | Out-Null",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def make_writeable(function, value, _exc_info) -> None:
        os.chmod(value, stat.S_IWRITE | stat.S_IREAD)
        function(value)

    shutil.rmtree(path, onerror=make_writeable)


def _prepare_workspace_temp(root: Path) -> tuple[Path, Path]:
    temp_root = root / ".tmp_pytest_runtime"
    base_temp = temp_root / f"basetemp_{uuid4().hex}"
    _mkdir(temp_root)
    _mkdir(base_temp)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMP"] = str(temp_root)
    tempfile.tempdir = str(temp_root)
    return temp_root, base_temp


def _patch_temporary_directory(temp_root: Path) -> None:
    def safe_mkdtemp(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | os.PathLike[str] | None = None,
    ) -> str:
        root = Path(dir) if dir else temp_root
        name = f"{prefix or 'tmp'}{uuid4().hex}{suffix or ''}"
        candidate = root / name
        _mkdir(candidate)
        return str(candidate)

    class WorkspaceTemporaryDirectory:
        def __init__(
            self,
            suffix: str | None = None,
            prefix: str | None = None,
            dir: str | os.PathLike[str] | None = None,
            ignore_cleanup_errors: bool = False,
        ) -> None:
            self._ignore_cleanup_errors = ignore_cleanup_errors
            self._root = Path(dir) if dir else temp_root
            self._prefix = prefix or "tmp"
            self._suffix = suffix or ""
            candidate = self._root / f"{self._prefix}{uuid4().hex}{self._suffix}"
            _mkdir(candidate)
            self.name = str(candidate)

        def __enter__(self) -> str:
            return self.name

        def __exit__(self, exc_type, exc, tb) -> bool:
            shutil.rmtree(self.name, ignore_errors=True)
            return False

        def cleanup(self) -> None:
            shutil.rmtree(self.name, ignore_errors=True)

    tempfile.mkdtemp = safe_mkdtemp
    tempfile.TemporaryDirectory = WorkspaceTemporaryDirectory


def _patch_pytest_cleanup() -> None:
    try:
        import _pytest.pathlib as pytest_pathlib
    except Exception:
        return

    original_cleanup = pytest_pathlib.cleanup_dead_symlinks

    def safe_cleanup_dead_symlinks(root: Path) -> None:
        try:
            original_cleanup(root)
        except PermissionError:
            return

    pytest_pathlib.cleanup_dead_symlinks = safe_cleanup_dead_symlinks


def _patch_pytest_tmp_path_factory(base_temp: Path) -> None:
    try:
        from _pytest.tmpdir import TempPathFactory
    except Exception:
        return

    original_mktemp = TempPathFactory.mktemp

    def safe_getbasetemp(self) -> Path:
        basetemp = getattr(self, "_basetemp", None)
        if basetemp is None:
            self._basetemp = base_temp
            _mkdir(base_temp)
            return base_temp
        return basetemp

    def safe_mktemp(self, basename: str, numbered: bool = True) -> Path:
        if not numbered:
            return original_mktemp(self, basename, numbered=numbered)
        root = safe_getbasetemp(self)
        candidate = root / f"{basename}{uuid4().hex}"
        _mkdir(candidate)
        return candidate

    TempPathFactory.getbasetemp = safe_getbasetemp
    TempPathFactory.mktemp = safe_mktemp


def main(argv: list[str]) -> int:
    repo_root = _repo_root()
    os.chdir(repo_root)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    temp_root, base_temp = _prepare_workspace_temp(repo_root)
    _patch_temporary_directory(temp_root)
    _patch_pytest_cleanup()
    _patch_pytest_tmp_path_factory(base_temp)

    try:
        import pytest

        final_args = [
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(base_temp),
            *argv,
        ]
        result = int(pytest.main(final_args))
        gc.collect()
        from scripts.cleanup_test_artifacts import cleanup

        for failure in cleanup(include_live=True):
            print(f"cleanup warning: {failure}", file=sys.stderr)
        return result
    finally:
        _remove_tree(temp_root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
