from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

SCHEMA_VERSION = "release_descriptor_v1"
VERSION_PATTERN = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?"
)


class ReleaseDescriptorError(RuntimeError):
    """El candidato no cumple el contrato que autoriza una publicación."""


@dataclass(frozen=True)
class ReleaseDescriptor:
    version: str
    tag: str
    notes_path: Path
    upgrade_path: Path
    prerelease: bool
    publication_enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": self.version,
            "tag": self.tag,
            "notes_path": self.notes_path.as_posix(),
            "upgrade_path": self.upgrade_path.as_posix(),
            "prerelease": self.prerelease,
            "publication_enabled": self.publication_enabled,
        }


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise ReleaseDescriptorError(
            f"No se pudo consultar Git ({' '.join(args)}): {detail}"
        ) from exc
    return result.stdout.strip()


def _relative_file(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ReleaseDescriptorError(f"{field} debe ser una ruta relativa")
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or value != posix.as_posix():
        raise ReleaseDescriptorError(f"{field} contiene una ruta insegura: {value}")
    path = Path(*posix.parts)
    if not (root / path).is_file():
        raise ReleaseDescriptorError(f"{field} no existe: {value}")
    return path


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseDescriptorError(f"Descriptor de release inválido: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseDescriptorError("El descriptor de release debe ser un objeto")
    return value


def validate_release_descriptor(
    root: Path,
    version: str,
    *,
    require_tag: bool = False,
) -> ReleaseDescriptor:
    root = root.resolve()
    if VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseDescriptorError(
            "La versión publicada debe ser SemVer estable o prerelease, sin build metadata"
        )
    descriptor_path = root / "config" / "releases" / f"v{version}.json"
    data = _load_json(descriptor_path)
    expected_keys = {
        "schema_version",
        "version",
        "tag",
        "notes_path",
        "upgrade_path",
        "required_note_headings",
        "database",
        "publish",
    }
    if set(data) != expected_keys:
        raise ReleaseDescriptorError(
            "El descriptor contiene campos ausentes o desconocidos: "
            f"{sorted(set(data) ^ expected_keys)}"
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseDescriptorError("schema_version de descriptor no soportada")
    expected_tag = f"v{version}"
    if data.get("version") != version or data.get("tag") != expected_tag:
        raise ReleaseDescriptorError(
            f"Descriptor, versión y tag deben coincidir exactamente con {expected_tag}"
        )

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = project.get("project", {}).get("version")
    if project_version != version:
        raise ReleaseDescriptorError(
            f"pyproject.toml declara {project_version!r}, no {version!r}"
        )

    notes_path = _relative_file(root, data.get("notes_path"), "notes_path")
    upgrade_path = _relative_file(root, data.get("upgrade_path"), "upgrade_path")
    required_headings = data.get("required_note_headings")
    if not isinstance(required_headings, list) or not required_headings:
        raise ReleaseDescriptorError("required_note_headings debe ser una lista no vacía")
    notes = (root / notes_path).read_text(encoding="utf-8")
    for heading in required_headings:
        if not isinstance(heading, str) or heading not in notes.splitlines():
            raise ReleaseDescriptorError(
                f"Las notas no contienen el encabezado exacto: {heading!r}"
            )

    database = data.get("database")
    if not isinstance(database, dict):
        raise ReleaseDescriptorError("Falta el contrato database")
    if database.get("backup_required") is not True:
        raise ReleaseDescriptorError("La migración debe exigir backup")
    if database.get("rollback_requires_backup_restore") is not True:
        raise ReleaseDescriptorError("El rollback de DB debe restaurar un backup")
    migration_path = _relative_file(
        root, database.get("migration_command_path"), "migration_command_path"
    )
    if migration_path.as_posix() != "scripts/migrate_to_v2.py":
        raise ReleaseDescriptorError("El migrador declarado no es el canónico")

    publish = data.get("publish")
    if not isinstance(publish, dict) or publish.get("github_release") is not True:
        raise ReleaseDescriptorError("El descriptor no autoriza una GitHub Release")
    if not isinstance(publish.get("enabled"), bool):
        raise ReleaseDescriptorError("publish.enabled debe ser booleano")
    prerelease = bool(publish.get("prerelease", False))
    if prerelease != ("-" in version):
        raise ReleaseDescriptorError(
            "publish.prerelease debe coincidir con la versión SemVer"
        )

    if require_tag:
        if publish["enabled"] is not True:
            raise ReleaseDescriptorError(
                "La publicación está bloqueada hasta completar sus gates de aceptación"
            )
        for path in (
            descriptor_path.relative_to(root),
            notes_path,
            upgrade_path,
            Path("LICENSE"),
            Path("NOTICE"),
        ):
            _git(root, "ls-files", "--error-unmatch", path.as_posix())
        tags = _git(root, "tag", "--points-at", "HEAD").splitlines()
        if expected_tag not in tags:
            raise ReleaseDescriptorError(f"HEAD no contiene el tag exacto {expected_tag}")
        tag_object_type = _git(root, "cat-file", "-t", f"refs/tags/{expected_tag}")
        if tag_object_type != "tag":
            raise ReleaseDescriptorError(
                f"{expected_tag} debe ser un tag anotado, no un tag ligero"
            )
        if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ReleaseDescriptorError("El worktree debe estar limpio para publicar")

    return ReleaseDescriptor(
        version=version,
        tag=expected_tag,
        notes_path=notes_path,
        upgrade_path=upgrade_path,
        prerelease=prerelease,
        publication_enabled=publish["enabled"],
    )
