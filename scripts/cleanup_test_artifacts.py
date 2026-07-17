"""Retira temporales de pytest cuando el proceso que los creó ya terminó."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import stat


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMP_PARENT = REPO_ROOT / ".pytest-workspace-tmp"
USER_CONFIG_TEMP = REPO_ROOT / ".pytest-user-config-tmp"


def _pid_is_running(pid: int) -> bool:
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


def cleanup(*, include_live: bool = False) -> list[str]:
    failures: list[str] = []
    if TEMP_PARENT.exists():
        for candidate in TEMP_PARENT.iterdir():
            match = re.fullmatch(r"session-(\d+)-[0-9a-f]+", candidate.name)
            if not include_live and match and _pid_is_running(int(match.group(1))):
                continue
            try:
                _remove_tree(candidate)
            except OSError as exc:
                failures.append(f"{candidate}: {exc}")
        if TEMP_PARENT.exists() and not any(TEMP_PARENT.iterdir()):
            TEMP_PARENT.rmdir()
    try:
        _remove_tree(USER_CONFIG_TEMP)
    except OSError as exc:
        failures.append(f"{USER_CONFIG_TEMP}: {exc}")
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
