from __future__ import annotations

import hashlib
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any

from aiteam.project_artifact_audit import (
    KNOWN_LEGACY_FIXTURE_FAMILIES,
    project_name_identity,
)

SCHEMA_VERSION = "project_hygiene_v1"
_STAGING_PREFIXES = (".aiteam-project-staging-", ".aiteam-staging-")
_TOMBSTONE_PREFIX = ".aiteam-deleted-"


def observe_project_hygiene(
    root: Path | None,
    *,
    configured: bool,
) -> dict[str, Any]:
    """Return a fast, path-redacted and strictly read-only root observation."""
    counts = {
        "direct_directories": 0,
        "aiteam_projects": 0,
        "legacy_numbered": 0,
        "legacy_tombstones": 0,
        "staging_leftovers": 0,
        "reparse_points": 0,
        "scan_errors": 0,
    }
    families: Counter[str] = Counter()
    root_path = Path(root).expanduser() if root is not None else None
    root_exists = bool(root_path and root_path.is_dir())
    root_reparse = bool(root_path and _is_reparse_or_symlink(root_path))

    if root_path is None or not configured:
        status = "not_configured"
    elif not root_exists:
        status = "root_missing"
    elif root_reparse:
        status = "review_required"
        counts["reparse_points"] = 1
    else:
        _scan_root(root_path, counts=counts, families=families)
        unexpected = (
            counts["legacy_numbered"]
            + counts["legacy_tombstones"]
            + counts["staging_leftovers"]
        )
        status = (
            "review_required"
            if counts["reparse_points"] or counts["scan_errors"]
            else "legacy_artifacts_detected"
            if unexpected
            else "clean"
        )

    action = _recommended_action(status)
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "read_only": True,
            "paths_emitted": False,
            "symlinks_followed": False,
            "database_opened": False,
            "git_invoked": False,
        },
        "root": {
            "configured": configured,
            "exists": root_exists,
            "reparse_point": root_reparse,
            "fingerprint": _root_fingerprint(root_path) if root_path else None,
        },
        "status": status,
        "requires_attention": status != "clean",
        "counts": counts,
        "legacy_families": [
            {"family": family, "count": families[family]}
            for family in sorted(families)
        ],
        "ownership": {
            "aiteam_identity_is_not_cleanup_authority": True,
            "folders_without_aiteam_identity_are_personal_protected": True,
        },
        "lifecycle": {
            "automatic_cleanup_installed": False,
            "startup_cleanup_installed": False,
            "ttl_cleanup_installed": False,
            "doctor_can_mutate": False,
        },
        "recommended_action": action,
    }


def validate_project_hygiene(report: dict[str, Any]) -> None:
    expected = {
        "schema_version",
        "scope",
        "root",
        "status",
        "requires_attention",
        "counts",
        "legacy_families",
        "ownership",
        "lifecycle",
        "recommended_action",
    }
    if set(report) != expected or report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("project hygiene schema drift")
    if report["scope"] != {
        "read_only": True,
        "paths_emitted": False,
        "symlinks_followed": False,
        "database_opened": False,
        "git_invoked": False,
    }:
        raise ValueError("project hygiene scope drift")
    if report["status"] not in {
        "not_configured",
        "root_missing",
        "review_required",
        "legacy_artifacts_detected",
        "clean",
    }:
        raise ValueError("project hygiene status drift")
    if report["requires_attention"] is not (report["status"] != "clean"):
        raise ValueError("project hygiene attention drift")
    if any(not isinstance(value, int) or value < 0 for value in report["counts"].values()):
        raise ValueError("project hygiene counts drift")
    if report["recommended_action"].get("mutates_state") is not False:
        raise ValueError("project hygiene action must be read-only")


def _scan_root(
    root: Path,
    *,
    counts: dict[str, int],
    families: Counter[str],
) -> None:
    try:
        with os.scandir(root) as iterator:
            entries = list(iterator)
    except OSError:
        counts["scan_errors"] += 1
        return
    for entry in entries:
        try:
            is_link = entry.is_symlink()
            is_directory = entry.is_dir(follow_symlinks=False)
        except OSError:
            counts["scan_errors"] += 1
            continue
        if is_link or _entry_is_reparse(entry):
            counts["reparse_points"] += 1
            continue
        if not is_directory:
            continue
        counts["direct_directories"] += 1
        name = entry.name
        if name.startswith(_TOMBSTONE_PREFIX):
            counts["legacy_tombstones"] += 1
        if name.startswith(_STAGING_PREFIXES):
            counts["staging_leftovers"] += 1

        child = Path(entry.path)
        db_path = child / ".aiteam" / "aiteam.db"
        if not db_path.is_file():
            continue
        counts["aiteam_projects"] += 1
        identity = project_name_identity(name)
        family = str(identity["family"])
        if identity["numbered"] and family in KNOWN_LEGACY_FIXTURE_FAMILIES:
            counts["legacy_numbered"] += 1
            families[family] += 1
        try:
            with os.scandir(child) as child_iterator:
                for nested in child_iterator:
                    if nested.name.startswith(".aiteam-staging-"):
                        counts["staging_leftovers"] += 1
        except OSError:
            counts["scan_errors"] += 1


def _recommended_action(status: str) -> dict[str, Any]:
    by_status = {
        "not_configured": (
            "configure_projects_root",
            "Elige una raíz absoluta; la comprobación no la crea ni la modifica.",
            True,
        ),
        "root_missing": (
            "choose_existing_projects_root",
            "Elige una carpeta existente o créala fuera del doctor y vuelve a comprobar.",
            True,
        ),
        "review_required": (
            "review_project_root_hygiene",
            "Revisa enlaces/errores y ejecuta la auditoría completa antes de decidir.",
            True,
        ),
        "legacy_artifacts_detected": (
            "run_project_artifact_audit",
            "Ejecuta K.8.1 para clasificar; el doctor no mueve ni borra carpetas.",
            True,
        ),
        "clean": (
            "none",
            "No se detectaron artefactos inesperados en la observación ligera.",
            False,
        ),
    }
    code, description, requires_human = by_status[status]
    return {
        "code": code,
        "description": description,
        "requires_human": requires_human,
        "mutates_state": False,
    }


def _root_fingerprint(root: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(os.fspath(root)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or _stat_is_reparse(info)


def _entry_is_reparse(entry: os.DirEntry[str]) -> bool:
    try:
        return _stat_is_reparse(entry.stat(follow_symlinks=False))
    except OSError:
        return False


def _stat_is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)
