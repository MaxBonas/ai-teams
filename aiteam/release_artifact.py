from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import tomllib

CONTRACT_SCHEMA_VERSION = "release_artifact_v1"
MANIFEST_SCHEMA_VERSION = "release_manifest_v1"
LICENSE_SCHEMA_VERSION = "release_licenses_v1"


class ReleaseArtifactError(RuntimeError):
    """El artefacto no puede construirse sin rebajar sus garantías."""


@dataclass(frozen=True)
class GitEntry:
    path: str
    executable: bool


@dataclass(frozen=True)
class ReleaseArtifactResult:
    archive: Path
    checksum: Path
    manifest: Path
    sbom: Path
    licenses: Path
    archive_sha256: str
    promotion_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "release_artifact_result_v1",
            "archive": str(self.archive),
            "checksum": str(self.checksum),
            "manifest": str(self.manifest),
            "sbom": str(self.sbom),
            "licenses": str(self.licenses),
            "archive_sha256": self.archive_sha256,
            "promotion_allowed": self.promotion_allowed,
        }


@dataclass(frozen=True)
class ReleaseVerificationResult:
    archive: Path
    archive_sha256: str
    root_directory: str
    version: str
    revision: str
    files_verified: int
    promotion_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "release_verification_result_v1",
            "archive": str(self.archive),
            "archive_sha256": self.archive_sha256,
            "root_directory": self.root_directory,
            "version": self.version,
            "revision": self.revision,
            "files_verified": self.files_verified,
            "promotion_allowed": self.promotion_allowed,
        }


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _run_git(root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ReleaseArtifactError(
            f"No se pudo consultar Git ({' '.join(args)}): {detail or exc}"
        ) from exc
    return completed.stdout


def load_release_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactError(f"Contrato de release inválido: {exc}") from exc
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ReleaseArtifactError("schema_version de release no soportada")
    if contract.get("source", {}).get("strategy") != "git_tracked":
        raise ReleaseArtifactError("La fuente de release debe ser git_tracked")
    if contract.get("archive", {}).get("compression") != "stored":
        raise ReleaseArtifactError(
            "La compresión debe ser stored para reproducibilidad entre plataformas"
        )
    for section in ("archive", "source", "secret_scan", "metadata", "sbom"):
        if not isinstance(contract.get(section), dict):
            raise ReleaseArtifactError(f"Falta la sección obligatoria {section}")
    return contract


def _git_entries(root: Path) -> list[GitEntry]:
    raw = _run_git(root, "ls-files", "--stage", "-z")
    entries: list[GitEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, _, stage = metadata.decode("ascii").split()
            path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReleaseArtifactError("Entrada de índice Git no interpretable") from exc
        if stage != "0":
            raise ReleaseArtifactError(f"El índice contiene un conflicto: {path}")
        if mode == "120000":
            raise ReleaseArtifactError(f"No se permiten symlinks en la release: {path}")
        entries.append(GitEntry(path=path.replace("\\", "/"), executable=mode == "100755"))
    return sorted(entries, key=lambda item: item.path)


def _git_state(root: Path, version: str) -> dict[str, Any]:
    revision = _run_git(root, "rev-parse", "HEAD").decode("ascii").strip()
    epoch_text = _run_git(root, "show", "-s", "--format=%ct", "HEAD").decode("ascii").strip()
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all").decode(
        "utf-8", errors="replace"
    )
    tags = [
        tag
        for tag in _run_git(root, "tag", "--points-at", "HEAD")
        .decode("utf-8", errors="replace")
        .splitlines()
        if tag
    ]
    expected_tags = {version, f"v{version}"}
    exact_release_tag = next((tag for tag in tags if tag in expected_tags), None)
    return {
        "revision": revision,
        "source_date_epoch": int(epoch_text),
        "dirty": bool(status.strip()),
        "dirty_entries": len(status.splitlines()),
        "tags": sorted(tags),
        "exact_release_tag": exact_release_tag,
    }


def _validate_path(path: str) -> PurePosixPath:
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or "\\" in path
        or path != parsed.as_posix()
    ):
        raise ReleaseArtifactError(f"Ruta insegura en el índice: {path}")
    return parsed


def _validate_sources(
    root: Path,
    entries: list[GitEntry],
    contract: dict[str, Any],
) -> list[tuple[GitEntry, bytes]]:
    source = contract["source"]
    present = {entry.path for entry in entries}
    missing = sorted(set(source["required_paths"]) - present)
    if missing:
        raise ReleaseArtifactError(
            "Faltan archivos obligatorios: " + ", ".join(missing)
        )
    forbidden_segments = {item.casefold() for item in source["forbidden_path_segments"]}
    forbidden_names = {item.casefold() for item in source["forbidden_names"]}
    forbidden_name_prefixes = tuple(
        item.casefold() for item in source["forbidden_name_prefixes"]
    )
    forbidden_suffixes = tuple(item.casefold() for item in source["forbidden_suffixes"])
    allowed_runtime = set(source["allowed_runtime_paths"])
    allowed_sensitive = set(source["allowed_sensitive_paths"])
    maximum = int(source["maximum_file_bytes"])
    patterns = [
        (item["id"], re.compile(item["regex"]))
        for item in contract["secret_scan"]["patterns"]
    ]
    allowed_test_literals = contract["secret_scan"].get("allowed_test_literals", [])
    collected: list[tuple[GitEntry, bytes]] = []
    for entry in entries:
        parsed = _validate_path(entry.path)
        folded_parts = [part.casefold() for part in parsed.parts]
        if parsed.parts and parsed.parts[0].casefold() == "runtime":
            if entry.path not in allowed_runtime:
                raise ReleaseArtifactError(
                    f"Estado runtime controlado por Git no permitido: {entry.path}"
                )
        elif forbidden_segments.intersection(folded_parts):
            raise ReleaseArtifactError(f"Ruta prohibida en release: {entry.path}")
        if entry.path not in allowed_sensitive and parsed.name.casefold() in forbidden_names:
            raise ReleaseArtifactError(f"Nombre sensible en release: {entry.path}")
        if (
            entry.path not in allowed_sensitive
            and parsed.name.casefold().startswith(forbidden_name_prefixes)
        ):
            raise ReleaseArtifactError(f"Nombre sensible en release: {entry.path}")
        if (
            entry.path not in allowed_sensitive
            and parsed.name.casefold().endswith(forbidden_suffixes)
        ):
            raise ReleaseArtifactError(f"Extensión sensible en release: {entry.path}")
        full_path = root / Path(*parsed.parts)
        if full_path.is_symlink():
            raise ReleaseArtifactError(f"No se permiten symlinks: {entry.path}")
        try:
            content = full_path.read_bytes()
        except OSError as exc:
            raise ReleaseArtifactError(f"No se pudo leer {entry.path}: {exc}") from exc
        if len(content) > maximum:
            raise ReleaseArtifactError(
                f"Archivo por encima del máximo ({maximum} B): {entry.path}"
            )
        candidate_texts = [content.decode("utf-8", errors="ignore")]
        if b"\0" in content:
            for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
                try:
                    candidate_texts.append(content.decode(encoding))
                except UnicodeDecodeError:
                    continue
        for text in candidate_texts:
            for literal in allowed_test_literals:
                text = text.replace(literal, "[ALLOWED_TEST_LITERAL]")
            for pattern_id, pattern in patterns:
                if pattern.search(text):
                    raise ReleaseArtifactError(
                        f"Posible secreto {pattern_id} en {entry.path}"
                    )
        collected.append((entry, content))
    return collected


def _dependency_name_from_npm_path(path: str, item: dict[str, Any]) -> str | None:
    if item.get("name"):
        return str(item["name"])
    parts = PurePosixPath(path).parts
    indexes = [index for index, part in enumerate(parts) if part == "node_modules"]
    if not indexes:
        return None
    start = indexes[-1] + 1
    if start >= len(parts):
        return None
    if parts[start].startswith("@") and start + 1 < len(parts):
        return f"{parts[start]}/{parts[start + 1]}"
    return parts[start]


def _reachable_dependencies(
    packages: dict[str, list[dict[str, Any]]], roots: set[str]
) -> set[str]:
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        for package in packages.get(name, []):
            pending.extend(
                str(item["name"])
                for item in package.get("dependencies", [])
                if item.get("name")
            )
    return reached


def _python_dependencies(
    root: Path, relative_path: str, lock_path: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    project_data = tomllib.loads((root / relative_path).read_text(encoding="utf-8"))
    project = project_data.get("project", {})
    root_component = {
        "type": "application",
        "name": str(project.get("name", "ai-teams")),
        "version": str(project.get("version", "unknown")),
    }
    lock = tomllib.loads((root / lock_path).read_text(encoding="utf-8"))
    packages_by_name: dict[str, list[dict[str, Any]]] = {}
    root_package: dict[str, Any] | None = None
    for package in lock.get("package", []):
        source = package.get("source", {})
        if source.get("editable") == ".":
            root_package = package
            continue
        packages_by_name.setdefault(str(package["name"]), []).append(package)
    if root_package is None:
        raise ReleaseArtifactError("uv.lock no contiene el proyecto editable raíz")
    runtime_roots = {
        str(item["name"]) for item in root_package.get("dependencies", [])
    }
    optional_roots = {
        str(item["name"])
        for values in root_package.get("optional-dependencies", {}).values()
        for item in values
    }
    runtime_reached = _reachable_dependencies(packages_by_name, runtime_roots)
    optional_reached = _reachable_dependencies(packages_by_name, optional_roots)
    dependencies: list[dict[str, Any]] = []
    for name in sorted(packages_by_name):
        for package in sorted(
            packages_by_name[name], key=lambda item: str(item["version"])
        ):
            version = str(package["version"])
            sdist = package.get("sdist") or {}
            wheel = next(iter(package.get("wheels", [])), {})
            integrity = sdist.get("hash") or wheel.get("hash")
            dependencies.append(
                {
                    "ecosystem": "pypi",
                    "name": name,
                    "version": version,
                    "constraint": None,
                    "license": None,
                    "scope": (
                        "runtime"
                        if name in runtime_reached
                        else (
                            "optional:dev"
                            if name in optional_reached
                            else "locked_transitive"
                        )
                    ),
                    "resolution": "locked",
                    "integrity": integrity,
                    "purl": (
                        f"pkg:pypi/{quote(name.casefold(), safe='')}"
                        f"@{quote(version, safe='')}"
                    ),
                }
            )
    return root_component, dependencies


def _npm_dependencies(root: Path, lockfiles: list[str]) -> list[dict[str, Any]]:
    dependencies: dict[tuple[str, str], dict[str, Any]] = {}
    for relative_path in lockfiles:
        try:
            lock = json.loads((root / relative_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseArtifactError(f"Lockfile npm inválido {relative_path}: {exc}") from exc
        if lock.get("lockfileVersion") not in {2, 3}:
            raise ReleaseArtifactError(f"Lockfile npm no soportado: {relative_path}")
        for package_path, item in sorted(lock.get("packages", {}).items()):
            if not package_path or not isinstance(item, dict) or not item.get("version"):
                continue
            name = _dependency_name_from_npm_path(package_path, item)
            if not name:
                raise ReleaseArtifactError(
                    f"No se pudo determinar el paquete npm: {package_path}"
                )
            version = str(item["version"])
            key = (name, version)
            scope = "development" if item.get("dev") else "runtime"
            existing = dependencies.get(key)
            if existing and existing["scope"] == "development" and scope == "runtime":
                existing["scope"] = "runtime"
                continue
            if existing:
                continue
            dependencies[key] = {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "constraint": None,
                "license": item.get("license"),
                "scope": scope,
                "resolution": "locked",
                "integrity": item.get("integrity"),
                "purl": f"pkg:npm/{quote(name, safe='/')}@{quote(version, safe='')}",
            }
    return [dependencies[key] for key in sorted(dependencies)]


def _build_inventory(
    root: Path,
    contract: dict[str, Any],
    version: str,
    revision: str,
    source_date_epoch: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_component, python_dependencies = _python_dependencies(
        root,
        contract["sbom"]["python_project"],
        contract["sbom"]["python_lockfile"],
    )
    npm_dependencies = _npm_dependencies(root, contract["sbom"]["node_lockfiles"])
    dependencies = python_dependencies + npm_dependencies
    root_component = {
        **root_component,
        "version": version,
        "bom-ref": f"pkg:generic/ai-teams@{quote(version, safe='')}",
    }
    components = []
    for dependency in dependencies:
        component: dict[str, Any] = {
            "type": "library",
            "name": dependency["name"],
            "bom-ref": dependency["purl"],
            "purl": dependency["purl"],
            "scope": (
                "optional"
                if dependency["scope"].startswith("optional:")
                else (
                    "excluded"
                    if dependency["scope"] == "development"
                    else "required"
                )
            ),
            "properties": [
                {"name": "aiteams:ecosystem", "value": dependency["ecosystem"]},
                {"name": "aiteams:resolution", "value": dependency["resolution"]},
                {"name": "aiteams:scope", "value": dependency["scope"]},
            ],
        }
        if dependency["version"]:
            component["version"] = dependency["version"]
        if dependency["constraint"]:
            component["properties"].append(
                {"name": "aiteams:declared_constraint", "value": dependency["constraint"]}
            )
        if dependency["license"]:
            component["licenses"] = [
                {"license": {"name": str(dependency["license"])}}
            ]
        if dependency.get("integrity"):
            component["properties"].append(
                {
                    "name": f"{dependency['ecosystem']}:integrity",
                    "value": dependency["integrity"],
                }
            )
        components.append(component)
    timestamp = datetime.fromtimestamp(source_date_epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": contract["sbom"]["spec_version"],
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'ai-teams:{revision}:{version}')}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "component": root_component,
            "properties": [
                {"name": "aiteams:source_revision", "value": revision},
                {
                    "name": "aiteams:python_inventory_limit",
                    "value": "uv_locked_unresolved_licenses",
                },
            ],
        },
        "components": sorted(components, key=lambda item: item["bom-ref"]),
    }
    unknown = [
        dependency
        for dependency in dependencies
        if not dependency["license"]
    ]
    project_license_present = (root / "LICENSE").is_file()
    blockers = []
    if not project_license_present:
        blockers.append("project_license_missing")
    licenses = {
        "schema_version": LICENSE_SCHEMA_VERSION,
        "source_revision": revision,
        "project_license": {
            "declared": project_license_present,
            "path": "LICENSE" if project_license_present else None,
        },
        "summary": {
            "dependencies": len(dependencies),
            "known_license": len(dependencies) - len(unknown),
            "unknown_license": len(unknown),
            "locked": sum(
                dependency["resolution"] == "locked" for dependency in dependencies
            ),
            "declared_unlocked": sum(
                dependency["resolution"] == "declared_unlocked"
                for dependency in dependencies
            ),
        },
        "promotion_note": (
            "Una licencia de proyecto ausente bloquea la publicación. Las licencias "
            "de terceros desconocidas permanecen visibles en este informe."
        ),
        "promotion_blockers": blockers,
        "dependencies": sorted(
            dependencies,
            key=lambda item: (
                item["ecosystem"],
                item["name"].casefold(),
                item["version"] or "",
            ),
        ),
    }
    return sbom, licenses


def _zip_datetime(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(source_date_epoch, tz=timezone.utc)
    if value.year < 1980:
        value = datetime(1980, 1, 1, tzinfo=timezone.utc)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)


def _write_zip_member(
    archive: zipfile.ZipFile,
    path: str,
    content: bytes,
    timestamp: tuple[int, int, int, int, int, int],
    executable: bool = False,
) -> None:
    info = zipfile.ZipInfo(path, date_time=timestamp)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    mode = 0o100755 if executable else 0o100644
    info.external_attr = mode << 16
    archive.writestr(info, content)


def _parse_checksum_lines(content: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in content.splitlines():
        parts = line.split("  ", 1)
        if (
            len(parts) != 2
            or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None
            or not parts[1]
            or parts[1] in checksums
        ):
            raise ReleaseArtifactError("SHA256SUMS contiene una entrada inválida")
        _validate_path(parts[1])
        checksums[parts[1]] = parts[0]
    if not checksums:
        raise ReleaseArtifactError("SHA256SUMS está vacío")
    return checksums


def verify_release_artifact(
    archive_path: Path,
    *,
    checksum_path: Path | None = None,
    require_promotable: bool = False,
) -> ReleaseVerificationResult:
    archive_path = archive_path.resolve()
    try:
        archive_bytes = archive_path.read_bytes()
    except OSError as exc:
        raise ReleaseArtifactError(f"No se pudo leer el artefacto: {exc}") from exc
    archive_hash = _sha256(archive_bytes)
    if checksum_path is not None:
        try:
            checksum_line = checksum_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ReleaseArtifactError(f"No se pudo leer el checksum externo: {exc}") from exc
        parts = checksum_line.split("  ", 1)
        if (
            len(parts) != 2
            or parts[0] != archive_hash
            or parts[1] != archive_path.name
        ):
            raise ReleaseArtifactError("El checksum externo no coincide con el ZIP")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or len(names) != len(
                {name.casefold() for name in names}
            ):
                raise ReleaseArtifactError("El ZIP contiene rutas duplicadas")
            if not names:
                raise ReleaseArtifactError("El ZIP está vacío")
            total_size = 0
            for info in infos:
                name = info.filename
                parsed = _validate_path(name)
                if len(parsed.parts) < 2 or name.endswith("/"):
                    raise ReleaseArtifactError(f"Miembro ZIP inesperado: {name}")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise ReleaseArtifactError("El ZIP no usa compresión reproducible")
                if info.flag_bits & 0x1:
                    raise ReleaseArtifactError("El ZIP contiene un miembro cifrado")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ReleaseArtifactError(f"El ZIP contiene un symlink: {name}")
                if info.file_size > 26_214_400:
                    raise ReleaseArtifactError(f"Miembro ZIP demasiado grande: {name}")
                total_size += info.file_size
            if total_size > 1_073_741_824:
                raise ReleaseArtifactError("El payload ZIP supera el límite total")
            roots = {PurePosixPath(name).parts[0] for name in names}
            if len(roots) != 1:
                raise ReleaseArtifactError("El ZIP debe tener un único directorio raíz")
            root_directory = next(iter(roots))
            checksum_member = (
                f"{root_directory}/RELEASE-METADATA/SHA256SUMS"
            )
            manifest_member = f"{root_directory}/RELEASE-METADATA/manifest.json"
            sbom_member = f"{root_directory}/RELEASE-METADATA/sbom.cdx.json"
            licenses_member = f"{root_directory}/RELEASE-METADATA/licenses.json"
            if not {
                checksum_member,
                manifest_member,
                sbom_member,
                licenses_member,
            }.issubset(names):
                raise ReleaseArtifactError("Faltan metadatos internos obligatorios")
            try:
                checksum_text = archive.read(checksum_member).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ReleaseArtifactError("SHA256SUMS no es UTF-8 válido") from exc
            checksums = _parse_checksum_lines(checksum_text)
            payload_names = [name for name in names if name != checksum_member]
            expected_paths = {
                name.removeprefix(f"{root_directory}/") for name in payload_names
            }
            if set(checksums) != expected_paths:
                raise ReleaseArtifactError(
                    "SHA256SUMS no cubre exactamente todo el payload"
                )
            for name in payload_names:
                relative = name.removeprefix(f"{root_directory}/")
                if _sha256(archive.read(name)) != checksums[relative]:
                    raise ReleaseArtifactError(
                        f"Checksum interno no válido: {relative}"
                    )
            try:
                embedded_sidecars = {
                    "manifest.json": archive.read(manifest_member),
                    "sbom.cdx.json": archive.read(sbom_member),
                    "licenses.json": archive.read(licenses_member),
                }
                manifest = json.loads(embedded_sidecars["manifest.json"])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ReleaseArtifactError("Manifiesto interno inválido") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseArtifactError(f"ZIP de release inválido: {exc}") from exc

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ReleaseArtifactError("Versión de manifiesto interno no soportada")
    version = manifest.get("version")
    if not isinstance(version, str) or root_directory != f"ai-teams-{version}":
        raise ReleaseArtifactError("Versión y directorio raíz no coinciden")
    promotion_allowed = manifest.get("promotion_allowed") is True
    if require_promotable and not promotion_allowed:
        raise ReleaseArtifactError("El manifiesto no autoriza promoción")
    revision = manifest.get("revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        raise ReleaseArtifactError("El manifiesto no contiene una revisión válida")
    archive_base = archive_path.name.removesuffix(".zip")
    sidecar_paths = {
        suffix: archive_path.with_name(f"{archive_base}.{suffix}")
        for suffix in embedded_sidecars
    }
    present_sidecars = {
        suffix for suffix, path in sidecar_paths.items() if path.is_file()
    }
    if present_sidecars and present_sidecars != set(sidecar_paths):
        raise ReleaseArtifactError("Los sidecars externos están incompletos")
    for suffix, path in sidecar_paths.items():
        if path.is_file():
            try:
                external_content = path.read_bytes()
            except OSError as exc:
                raise ReleaseArtifactError(
                    f"No se pudo leer el sidecar externo: {path.name}"
                ) from exc
            if external_content != embedded_sidecars[suffix]:
                raise ReleaseArtifactError(
                    f"El sidecar externo no coincide con el ZIP: {path.name}"
                )
    return ReleaseVerificationResult(
        archive=archive_path,
        archive_sha256=archive_hash,
        root_directory=root_directory,
        version=version,
        revision=revision,
        files_verified=len(payload_names),
        promotion_allowed=promotion_allowed,
    )


def build_release_artifact(
    root: Path,
    output_dir: Path,
    version: str,
    *,
    contract_path: Path | None = None,
    allow_dirty: bool = False,
    require_release_tag: bool = False,
) -> ReleaseArtifactResult:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ReleaseArtifactError(f"Versión no válida: {version}")
    contract_file = contract_path or root / "config" / "release_artifact.v1.json"
    contract = load_release_contract(contract_file)
    state = _git_state(root, version)
    if state["dirty"] and not allow_dirty:
        raise ReleaseArtifactError(
            "El worktree está sucio; confirmar primero los cambios o usar "
            "--allow-dirty únicamente para un preview no promocionable"
        )
    if require_release_tag and not state["exact_release_tag"]:
        raise ReleaseArtifactError(
            f"HEAD no tiene el tag exacto v{version} o {version}"
        )
    sources = _validate_sources(root, _git_entries(root), contract)
    sbom, licenses = _build_inventory(
        root,
        contract,
        version,
        state["revision"],
        state["source_date_epoch"],
    )
    promotion_allowed = bool(
        not state["dirty"]
        and state["exact_release_tag"]
        and not licenses["promotion_blockers"]
    )
    source_records = [
        {
            "path": entry.path,
            "bytes": len(content),
            "sha256": _sha256(content),
            "mode": "0755" if entry.executable else "0644",
        }
        for entry, content in sources
    ]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "contract_version": contract["schema_version"],
        "name": "ai-teams",
        "version": version,
        "revision": state["revision"],
        "source_date_epoch": state["source_date_epoch"],
        "dirty": state["dirty"],
        "dirty_entries": state["dirty_entries"],
        "exact_release_tag": state["exact_release_tag"],
        "promotion_allowed": promotion_allowed,
        "source_strategy": contract["source"]["strategy"],
        "archive_reproducibility": "stable_order_timestamp_mode_zip_stored",
        "files": source_records,
        "inventory": {
            "sbom_format": "CycloneDX 1.6",
            "npm": "locked",
            "python": "uv_locked",
            "license_unknown": licenses["summary"]["unknown_license"],
            "promotion_blockers": licenses["promotion_blockers"],
        },
    }
    metadata = contract["metadata"]
    metadata_members = {
        f"{metadata['directory']}/{metadata['manifest']}": _json_bytes(manifest),
        f"{metadata['directory']}/{metadata['sbom']}": _json_bytes(sbom),
        f"{metadata['directory']}/{metadata['licenses']}": _json_bytes(licenses),
    }
    root_directory = contract["archive"]["root_template"].format(version=version)
    members: list[tuple[str, bytes, bool]] = [
        (f"{root_directory}/{entry.path}", content, entry.executable)
        for entry, content in sources
    ]
    members.extend(
        (f"{root_directory}/{path}", content, False)
        for path, content in metadata_members.items()
    )
    members.sort(key=lambda item: item[0])
    checksum_lines = [
        f"{_sha256(content)}  {path.removeprefix(f'{root_directory}/')}"
        for path, content, _ in members
    ]
    checksums_path = f"{root_directory}/{metadata['directory']}/{metadata['checksums']}"
    checksums_content = ("\n".join(checksum_lines) + "\n").encode("utf-8")
    members.append((checksums_path, checksums_content, False))
    members.sort(key=lambda item: item[0])

    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"ai-teams-{version}"
    archive_path = output_dir / f"{base_name}.zip"
    temporary_archive = output_dir / f".{base_name}.{os.getpid()}.tmp"
    timestamp = _zip_datetime(state["source_date_epoch"])
    try:
        with zipfile.ZipFile(temporary_archive, "w", allowZip64=True) as archive:
            for path, content, executable in members:
                _write_zip_member(archive, path, content, timestamp, executable)
        os.replace(temporary_archive, archive_path)
    finally:
        temporary_archive.unlink(missing_ok=True)
    archive_hash = _sha256(archive_path.read_bytes())
    checksum_path = output_dir / f"{archive_path.name}.sha256"
    manifest_path = output_dir / f"{base_name}.manifest.json"
    sbom_path = output_dir / f"{base_name}.sbom.cdx.json"
    licenses_path = output_dir / f"{base_name}.licenses.json"
    checksum_path.write_text(
        f"{archive_hash}  {archive_path.name}\n", encoding="utf-8", newline="\n"
    )
    manifest_path.write_bytes(metadata_members[f"{metadata['directory']}/{metadata['manifest']}"])
    sbom_path.write_bytes(metadata_members[f"{metadata['directory']}/{metadata['sbom']}"])
    licenses_path.write_bytes(metadata_members[f"{metadata['directory']}/{metadata['licenses']}"])
    return ReleaseArtifactResult(
        archive=archive_path,
        checksum=checksum_path,
        manifest=manifest_path,
        sbom=sbom_path,
        licenses=licenses_path,
        archive_sha256=archive_hash,
        promotion_allowed=promotion_allowed,
    )
