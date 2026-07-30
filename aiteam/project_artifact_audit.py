from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "project_artifact_audit_v1"
CLASSIFICATIONS = (
    "active_current_project",
    "aiteam_preserve_or_migrate",
    "aiteam_disposable_candidate",
    "ambiguous_owner_review_required",
    "personal_protected",
)
KNOWN_LEGACY_FIXTURE_FAMILIES = frozenset({
    "AnthropicLead",
    "Demo",
    "OrgChart",
    "Quorum",
    "Reconcile",
    "Solo",
})
_NUMBERED_NAME = re.compile(r"^(?P<family>.+?) (?P<number>\d+)$")
_REDACTED_ROOT = "<selected-projects-root>"


@dataclass(frozen=True)
class AuditOptions:
    git_timeout_seconds: float = 4.0
    workers: int = 8
    max_files_per_folder: int = 200_000
    probe_process_handles: bool = False


def audit_project_root(
    root: Path,
    *,
    active_workspace: Path | None = None,
    registry_workspaces: Iterable[Path] = (),
    options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Inventory direct children of *root* without mutating or following links.

    The result intentionally cannot authorize cleanup. Even the
    ``aiteam_disposable_candidate`` class is only input for a later, separately
    approved dry-run.
    """
    options = options or AuditOptions()
    root = _validate_root(root)
    active_key = _path_key(active_workspace) if active_workspace else None
    registry_keys = {_path_key(path) for path in registry_workspaces}

    children = _direct_children(root)
    handle_counts, handle_probe = _probe_handles(root, children, options.probe_process_handles)

    def inspect(child: Path) -> dict[str, Any]:
        return _inspect_folder(
            child,
            active_key=active_key,
            registry_keys=registry_keys,
            handle_count=handle_counts.get(child.name),
            handle_probe=handle_probe,
            options=options,
        )

    with ThreadPoolExecutor(max_workers=max(1, min(options.workers, 32))) as executor:
        entries = list(executor.map(inspect, children))
    entries.sort(key=lambda item: str(item["relative_path"]).casefold())

    counts = Counter(str(item["classification"]) for item in entries)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": _REDACTED_ROOT,
        "mode": "read_only_inventory",
        "safety": {
            "cleanup_authorized": False,
            "moves_performed": 0,
            "deletions_performed": 0,
            "renames_performed": 0,
            "project_writes_performed": 0,
            "symlinks_followed": False,
        },
        "limitations": [
            "Una clasificación disposable_candidate no autoriza mover ni borrar.",
            "La inspección SQLite usa una conexión immutable de solo lectura y puede omitir WAL activo.",
            "Los handles solo son exhaustivos cuando el probe está habilitado y el proceso tiene permisos.",
        ],
        "summary": {
            "folder_count": len(entries),
            "classification_counts": {
                classification: counts.get(classification, 0)
                for classification in CLASSIFICATIONS
            },
        },
        "entries": entries,
    }
    payload["content_sha256"] = _payload_hash(payload)
    return payload


def write_audit_receipt(report: dict[str, Any], output: Path, *, audited_root: Path) -> None:
    """Write a receipt outside the audited tree; never creates state inside it."""
    root = _validate_root(audited_root)
    output = Path(output).expanduser().resolve()
    if _is_within(output, root):
        raise ValueError("El receipt no puede escribirse dentro de la raíz auditada")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validate_root(root: Path) -> Path:
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        raise ValueError("La raíz de auditoría debe ser absoluta y explícita")
    if _is_reparse_or_symlink(candidate):
        raise ValueError("La raíz de auditoría no puede ser symlink/reparse point")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("La raíz de auditoría debe ser un directorio existente")
    return resolved


def _direct_children(root: Path) -> list[Path]:
    children: list[Path] = []
    with os.scandir(root) as iterator:
        for item in iterator:
            try:
                if item.is_dir(follow_symlinks=False) or item.is_symlink():
                    children.append(Path(item.path))
            except OSError:
                children.append(Path(item.path))
    return sorted(children, key=lambda path: path.name.casefold())


def _inspect_folder(
    path: Path,
    *,
    active_key: str | None,
    registry_keys: set[str],
    handle_count: int | None,
    handle_probe: dict[str, Any],
    options: AuditOptions,
) -> dict[str, Any]:
    reparse = _is_reparse_or_symlink(path)
    name_info = project_name_identity(path.name)
    timestamps = _timestamps(path)
    if reparse:
        evidence = {
            "aiteam": {"state": "not_inspected_reparse_point"},
            "database": {"state": "not_inspected_reparse_point"},
            "references": {"active_workspace": False, "registry_workspace": False},
            "name": name_info,
            "timestamps": timestamps,
            "size": {"state": "not_inspected_reparse_point"},
            "git": {"state": "not_inspected_reparse_point"},
            "handles": {"state": "not_inspected_reparse_point"},
            "reparse_point": True,
        }
        classification, confidence, reasons = (
            "ambiguous_owner_review_required",
            "high",
            ["symlink_or_reparse_point_not_followed"],
        )
    else:
        path_key = _path_key(path)
        active = path_key == active_key
        registered = path_key in registry_keys
        aiteam_dir = path / ".aiteam"
        db_path = aiteam_dir / "aiteam.db"
        aiteam_state = (
            "present"
            if aiteam_dir.is_dir()
            else "missing"
            if not aiteam_dir.exists()
            else "invalid_not_directory"
        )
        database = _inspect_database(db_path) if aiteam_state == "present" else {"state": "missing"}
        git = _inspect_git(path, timeout=options.git_timeout_seconds)
        size = _inspect_size(path, max_files=options.max_files_per_folder)
        handles = dict(handle_probe)
        if handle_count is not None:
            handles = {"state": "observed", "open_file_handle_count": handle_count}
        evidence = {
            "aiteam": {"state": aiteam_state},
            "database": database,
            "references": {
                "active_workspace": active,
                "registry_workspace": registered,
            },
            "name": name_info,
            "timestamps": timestamps,
            "size": size,
            "git": git,
            "handles": handles,
            "reparse_point": False,
        }
        classification, confidence, reasons = _classify(evidence)

    return {
        "relative_path": path.name,
        "classification": classification,
        "confidence": confidence,
        "reasons": reasons,
        "evidence": evidence,
    }


def _classify(evidence: dict[str, Any]) -> tuple[str, str, list[str]]:
    refs = evidence["references"]
    database = evidence["database"]
    git = evidence["git"]
    size = evidence["size"]
    name = evidence["name"]

    if refs["active_workspace"]:
        return "active_current_project", "high", ["matches_active_workspace_reference"]
    if evidence["aiteam"]["state"] == "missing":
        return "personal_protected", "high", ["no_aiteam_identity"]
    if evidence["aiteam"]["state"] != "present":
        return "ambiguous_owner_review_required", "high", ["invalid_aiteam_identity"]
    if database.get("state") != "valid":
        return (
            "ambiguous_owner_review_required",
            "high",
            [f"database_{database.get('state', 'unknown')}"],
        )
    if refs["registry_workspace"]:
        return "aiteam_preserve_or_migrate", "high", ["referenced_by_workspace_registry"]
    if git.get("state") not in {"not_a_git_repository", "observed"}:
        return "ambiguous_owner_review_required", "high", ["git_state_not_observed"]
    if git.get("state") == "observed":
        if git.get("dirty") or git.get("untracked"):
            return "aiteam_preserve_or_migrate", "high", ["git_has_local_work"]
        if int(git.get("remote_count", 0)) > 0:
            return "aiteam_preserve_or_migrate", "high", ["git_has_remote"]
    if size.get("state") != "complete":
        return "ambiguous_owner_review_required", "high", ["filesystem_inventory_incomplete"]

    strong_fixture_name = (
        bool(name.get("numbered"))
        and name.get("family") in KNOWN_LEGACY_FIXTURE_FAMILIES
    )
    if strong_fixture_name and database.get("schema_family") in {"paperclip_v2", "legacy_aiteam"}:
        return (
            "aiteam_disposable_candidate",
            "medium",
            [
                "known_numbered_aiteam_fixture_family",
                "valid_database",
                "no_observed_git_work_or_remote",
                "candidate_only_no_cleanup_authority",
            ],
        )
    return (
        "aiteam_preserve_or_migrate",
        "medium",
        ["valid_aiteam_project_without_sufficient_disposable_evidence"],
    )


def _inspect_database(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"state": "missing"}
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro&immutable=1"
        with contextlib.closing(
            sqlite3.connect(uri, uri=True, timeout=1.0)
        ) as conn:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                return {"state": "corrupt", "quick_check": "failed"}
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            counts = {
                table: _safe_count(conn, table)
                for table in ("goals", "agents", "issues", "runs")
                if table in tables
            }
            identity = _database_identity(conn, tables)
            current = {"goals", "agents", "issues", "runs"}
            legacy = {"tasks", "workflow_state"}
            schema_family = (
                "paperclip_v2"
                if current.issubset(tables)
                else "legacy_aiteam"
                if tables & legacy
                else "unknown"
            )
            return {
                "state": "valid",
                "user_version": user_version,
                "schema_family": schema_family,
                "table_count": len(tables),
                "entity_counts": counts,
                "project_identity": identity,
            }
    except (OSError, sqlite3.Error) as exc:
        return {"state": "corrupt_or_unreadable", "error_type": type(exc).__name__}


def _database_identity(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "goals" not in tables:
        return {"state": "not_available"}
    try:
        rows = conn.execute(
            "SELECT id, source FROM goals ORDER BY created_at, id LIMIT 2"
        ).fetchall()
    except sqlite3.Error:
        return {"state": "unreadable"}
    if not rows:
        return {"state": "empty"}
    raw_id = str(rows[0][0])
    return {
        "state": "observed",
        "primary_goal_id_sha256": hashlib.sha256(raw_id.encode("utf-8")).hexdigest(),
        "source": str(rows[0][1] or "")[:80],
        "multiple_goals_observed": len(rows) > 1,
    }


def _safe_count(conn: sqlite3.Connection, table: str) -> int | None:
    try:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    except sqlite3.Error:
        return None


def _inspect_git(path: Path, *, timeout: float) -> dict[str, Any]:
    git_entry = path / ".git"
    if not git_entry.exists():
        return {"state": "not_a_git_repository"}
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "status",
                "--porcelain=v1",
                "--branch",
                "--untracked-files=normal",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.2, timeout),
            check=False,
        )
    except FileNotFoundError:
        return {"state": "git_cli_missing"}
    except subprocess.TimeoutExpired:
        return {"state": "status_timeout"}
    except OSError as exc:
        return {"state": "status_error", "error_type": type(exc).__name__}
    if result.returncode != 0:
        return {"state": "status_error", "returncode": result.returncode}

    lines = result.stdout.splitlines()
    status_lines = [line for line in lines if not line.startswith("## ")]
    branch = _branch_from_status(lines[0] if lines and lines[0].startswith("## ") else "")
    untracked = sum(1 for line in status_lines if line.startswith("??"))
    dirty = sum(1 for line in status_lines if not line.startswith("??"))
    remotes = _remote_hosts(path, timeout=timeout)
    return {
        "state": "observed",
        "branch": branch,
        "dirty": dirty > 0,
        "dirty_entry_count": dirty,
        "untracked": untracked > 0,
        "untracked_entry_count": untracked,
        "remote_count": remotes["count"],
        "remote_hosts": remotes["hosts"],
        "remote_probe_state": remotes["state"],
    }


def _branch_from_status(header: str) -> dict[str, str]:
    value = header.removeprefix("## ").split("...", 1)[0].strip()
    if not value:
        return {"state": "unknown", "ref_sha256": ""}
    if value.startswith("No commits yet on "):
        state = "unborn"
        value = value.removeprefix("No commits yet on ")
    elif value == "HEAD (no branch)":
        state = "detached"
    else:
        state = "named"
    return {
        "state": state,
        "ref_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _remote_hosts(path: Path, *, timeout: float) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "config",
                "--get-regexp",
                r"^remote\..*\.url$",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.2, timeout),
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"state": type(exc).__name__, "count": 0, "hosts": []}
    if result.returncode not in {0, 1}:
        return {"state": "error", "count": 0, "hosts": []}
    urls = [
        line.split(None, 1)[1].strip()
        for line in result.stdout.splitlines()
        if len(line.split(None, 1)) == 2
    ]
    hosts = sorted({_remote_host(url) for url in urls})
    return {"state": "observed", "count": len(urls), "hosts": hosts}


def _remote_host(url: str) -> str:
    if "://" in url:
        parsed = urlparse(url)
        return (parsed.hostname or "redacted-remote").lower()
    match = re.match(r"^(?:[^@/\s]+@)?([^:/\s]+):", url)
    if match:
        return match.group(1).lower()
    return "local-or-redacted-remote"


def _inspect_size(path: Path, *, max_files: int) -> dict[str, Any]:
    total = 0
    files = 0
    dirs = 0
    stack = [path]
    errors = 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for item in iterator:
                    try:
                        info = item.stat(follow_symlinks=False)
                        if item.is_symlink() or _stat_is_reparse(info):
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            dirs += 1
                            stack.append(Path(item.path))
                        elif stat.S_ISREG(info.st_mode):
                            files += 1
                            total += int(info.st_size)
                            if files >= max_files:
                                return {
                                    "state": "truncated",
                                    "bytes_observed": total,
                                    "files_observed": files,
                                    "directories_observed": dirs,
                                }
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return {
        "state": "complete" if errors == 0 else "partial",
        "bytes": total,
        "files": files,
        "directories": dirs,
        "errors": errors,
    }


def _probe_handles(
    root: Path,
    children: list[Path],
    enabled: bool,
) -> tuple[dict[str, int], dict[str, Any]]:
    if not enabled:
        return {}, {"state": "not_probed", "reason": "explicit_opt_in_required"}
    try:
        import psutil
    except ImportError:
        return {}, {"state": "unavailable", "reason": "psutil_missing"}

    child_roots = {child.name: _path_key(child) + os.sep for child in children}
    counts = {child.name: 0 for child in children}
    denied = 0
    for process in psutil.process_iter():
        try:
            open_files = process.open_files()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied += 1
            continue
        for opened in open_files:
            opened_key = _path_key(Path(opened.path))
            for name, prefix in child_roots.items():
                if opened_key == prefix[:-1] or opened_key.startswith(prefix):
                    counts[name] += 1
                    break
    return counts, {"state": "observed", "processes_denied": denied}


def project_name_identity(name: str) -> dict[str, Any]:
    match = _NUMBERED_NAME.fullmatch(name)
    if not match or int(match.group("number")) < 2:
        return {"numbered": False, "family": name, "sequence": None}
    return {
        "numbered": True,
        "family": match.group("family"),
        "sequence": int(match.group("number")),
    }


def _timestamps(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        return {"state": "unreadable", "error_type": type(exc).__name__}
    return {
        "state": "observed",
        "created_at": datetime.fromtimestamp(info.st_ctime, timezone.utc).isoformat(),
        "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
    }


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or _stat_is_reparse(info)


def _stat_is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("content_sha256", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
