from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class WorkspaceDelta(NamedTuple):
    created: list[str]
    modified: list[str]
    deleted: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.created or self.modified or self.deleted)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "created": self.created,
            "modified": self.modified,
            "deleted": self.deleted,
        }


_EXCLUDED_NAMES = {
    ".aiteam",
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}


def workspace_root_for_db(db_path: Path) -> Path:
    db_path = Path(db_path)
    if db_path.parent.name == ".aiteam":
        return db_path.parent.parent
    return db_path.parent


def snapshot_workspace(root: Path) -> dict[str, tuple[int, int]]:
    root = Path(root)
    if not root.exists() or not root.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if _is_excluded(path, root) or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        snapshot[rel] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def diff_snapshots(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
    *,
    limit: int = 50,
) -> WorkspaceDelta:
    created = sorted(path for path in after if path not in before)
    deleted = sorted(path for path in before if path not in after)
    modified = sorted(path for path in after if path in before and after[path] != before[path])
    return WorkspaceDelta(
        created=created[:limit],
        modified=modified[:limit],
        deleted=deleted[:limit],
    )


def _is_excluded(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _EXCLUDED_NAMES for part in rel.parts)
