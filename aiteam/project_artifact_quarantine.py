from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.project_artifact_audit import AuditOptions, audit_project_root
from aiteam.project_artifact_remediation import (
    SCHEMA_VERSION as REMEDIATION_SCHEMA_VERSION,
)
from aiteam.project_artifact_remediation import (
    verify_remediation_manifest,
)

JOURNAL_SCHEMA_VERSION = "project_artifact_quarantine_journal_v1"


class QuarantineError(RuntimeError):
    """A quarantine or restore operation failed closed."""


def apply_quarantine(
    manifest_path: Path,
    quarantine_root: Path,
    *,
    approved_manifest_sha256: str,
    approved_batch_sha256: str,
    active_workspace: Path | None = None,
    registry_workspaces: tuple[Path, ...] = (),
    workers: int = 8,
    git_timeout_seconds: float = 4.0,
    max_files_per_folder: int = 200_000,
) -> dict[str, Any]:
    """Move an approved exact batch atomically per directory.

    This function never purges. Any failure after the first move triggers an
    immediate reverse-order rollback and leaves the journal in quarantine.
    """
    manifest_path = Path(manifest_path).expanduser().resolve(strict=True)
    manifest = _load_json(manifest_path)
    root, proposals, batch_hash = _validate_manifest_and_approval(
        manifest,
        manifest_path=manifest_path,
        approved_manifest_sha256=approved_manifest_sha256,
        approved_batch_sha256=approved_batch_sha256,
    )
    quarantine_root = _validate_quarantine_root(quarantine_root, project_root=root)
    batch_id = f"batch-{batch_hash[:16]}"
    batch_dir = quarantine_root / batch_id
    items_dir = batch_dir / "items"
    if batch_dir.exists():
        raise QuarantineError("El batch de cuarentena ya existe; no se sobrescribe")
    if not _same_filesystem(root, quarantine_root):
        raise QuarantineError(
            "Origen y cuarentena deben compartir filesystem para usar moves atómicos"
        )

    current_audit = audit_project_root(
        root,
        active_workspace=active_workspace,
        registry_workspaces=registry_workspaces,
        options=AuditOptions(
            git_timeout_seconds=git_timeout_seconds,
            workers=workers,
            max_files_per_folder=max_files_per_folder,
            probe_process_handles=True,
        ),
    )
    current_entries = {
        str(entry["relative_path"]): entry for entry in current_audit["entries"]
    }
    prepared = _prepare_items(
        root,
        quarantine_root,
        items_dir,
        proposals,
        current_entries=current_entries,
    )

    batch_dir.mkdir()
    items_dir.mkdir()
    stored_manifest = batch_dir / "approved-remediation-manifest.json"
    stored_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    journal_path = batch_dir / "journal.json"
    journal: dict[str, Any] = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "batch_id": batch_id,
        "state": "prepared",
        "created_at": _now(),
        "updated_at": _now(),
        "project_root": str(root),
        "quarantine_root": str(quarantine_root),
        "manifest_path": str(manifest_path),
        "manifest_sha256": approved_manifest_sha256,
        "target_batch_sha256": batch_hash,
        "stored_manifest_sha256": _file_sha256(stored_manifest),
        "purge_authorized": False,
        "purge_supported": False,
        "items": prepared,
        "events": [{"at": _now(), "type": "prepared"}],
    }
    _write_journal(journal_path, journal)

    moved_indices: list[int] = []
    try:
        for index, item in enumerate(journal["items"]):
            source = Path(item["source_path"])
            destination = Path(item["quarantine_path"])
            if _tree_digest(source) != item["tree_sha256"]:
                raise QuarantineError(
                    f"El target cambió después del preflight: {item['relative_name']}"
                )
            _atomic_move(source, destination)
            moved_indices.append(index)
            if _tree_digest(destination) != item["tree_sha256"]:
                raise QuarantineError(
                    f"Checksum post-move inválido: {item['relative_name']}"
                )
            item["state"] = "quarantined"
            item["moved_at"] = _now()
            journal["events"].append(
                {"at": _now(), "type": "item_quarantined", "index": index}
            )
            _write_journal(journal_path, journal)
    except Exception as exc:
        rollback_errors = _rollback_quarantine_moves(
            journal,
            journal_path,
            moved_indices=moved_indices,
        )
        if rollback_errors:
            raise QuarantineError(
                "Falló la cuarentena y el rollback quedó incompleto: "
                + "; ".join(rollback_errors)
            ) from exc
        raise QuarantineError(
            f"Falló la cuarentena; todos los moves aplicados se revirtieron: {type(exc).__name__}"
        ) from exc

    journal["state"] = "quarantined"
    journal["completed_at"] = _now()
    journal["events"].append({"at": _now(), "type": "batch_quarantined"})
    _write_journal(journal_path, journal)
    return _load_and_verify_journal(journal_path)


def restore_quarantine(
    batch_dir: Path,
    *,
    approved_batch_sha256: str,
) -> dict[str, Any]:
    """Restore an entire quarantined batch; never overwrites a source path."""
    batch_dir = Path(batch_dir).expanduser().resolve(strict=True)
    journal_path = batch_dir / "journal.json"
    journal = _load_and_verify_journal(journal_path)
    if journal.get("state") != "quarantined":
        raise QuarantineError("Solo se puede restaurar un batch quarantined")
    if str(journal.get("target_batch_sha256")) != approved_batch_sha256:
        raise QuarantineError("El hash aprobado de batch no coincide")
    if Path(journal["quarantine_root"]).resolve(strict=True) != batch_dir.parent:
        raise QuarantineError("El batch no pertenece a la quarantine_root sellada")
    root = Path(journal["project_root"]).resolve(strict=True)
    if not _same_filesystem(root, batch_dir.parent):
        raise QuarantineError("Restore cross-filesystem no permitido")

    for item in journal["items"]:
        source = Path(item["source_path"])
        quarantined = Path(item["quarantine_path"])
        if source.exists() or source.is_symlink():
            raise QuarantineError(
                f"Colisión en destino de restauración: {item['relative_name']}"
            )
        if not quarantined.is_dir() or quarantined.is_symlink():
            raise QuarantineError(
                f"Falta target válido en cuarentena: {item['relative_name']}"
            )
        if _tree_digest(quarantined) != item["tree_sha256"]:
            raise QuarantineError(
                f"Checksum de cuarentena inválido: {item['relative_name']}"
            )

    restored_indices: list[int] = []
    try:
        for index, item in enumerate(journal["items"]):
            source = Path(item["source_path"])
            quarantined = Path(item["quarantine_path"])
            _atomic_move(quarantined, source)
            restored_indices.append(index)
            if _tree_digest(source) != item["tree_sha256"]:
                raise QuarantineError(
                    f"Checksum post-restore inválido: {item['relative_name']}"
                )
            item["state"] = "restored"
            item["restored_at"] = _now()
            journal["events"].append(
                {"at": _now(), "type": "item_restored", "index": index}
            )
            _write_journal(journal_path, journal)
    except Exception as exc:
        rollback_errors = _rollback_restore_moves(
            journal,
            journal_path,
            restored_indices=restored_indices,
        )
        if rollback_errors:
            raise QuarantineError(
                "Falló el restore y su rollback quedó incompleto: "
                + "; ".join(rollback_errors)
            ) from exc
        raise QuarantineError(
            f"Falló el restore; los moves aplicados volvieron a cuarentena: {type(exc).__name__}"
        ) from exc

    journal["state"] = "restored"
    journal["restored_at"] = _now()
    journal["events"].append({"at": _now(), "type": "batch_restored"})
    _write_journal(journal_path, journal)
    return _load_and_verify_journal(journal_path)


def verify_quarantine_journal(batch_dir: Path) -> bool:
    try:
        _load_and_verify_journal(Path(batch_dir) / "journal.json")
    except (OSError, ValueError, QuarantineError):
        return False
    return True


def _validate_manifest_and_approval(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    approved_manifest_sha256: str,
    approved_batch_sha256: str,
) -> tuple[Path, list[dict[str, Any]], str]:
    if manifest.get("schema_version") != REMEDIATION_SCHEMA_VERSION:
        raise QuarantineError("Schema de manifiesto no soportado")
    if not verify_remediation_manifest(manifest):
        raise QuarantineError("Integridad del manifiesto inválida")
    manifest_hash = str(manifest.get("manifest_sha256") or "")
    batch_hash = str(manifest.get("approval", {}).get("target_batch_sha256") or "")
    if manifest_hash != approved_manifest_sha256:
        raise QuarantineError("El hash aprobado de manifiesto no coincide")
    if batch_hash != approved_batch_sha256:
        raise QuarantineError("El hash aprobado de batch no coincide")
    if manifest.get("denied"):
        raise QuarantineError("Un manifiesto con targets denegados no es ejecutable")
    if not manifest.get("summary", {}).get("ready_for_owner_review"):
        raise QuarantineError("El manifiesto no estaba listo para revisión owner")
    proposals = manifest.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise QuarantineError("El manifiesto no contiene propuestas")
    if int(manifest.get("summary", {}).get("proposed_count", -1)) != len(proposals):
        raise QuarantineError("El conteo de propuestas es contradictorio")
    expected_batch = _target_batch_hash(proposals)
    if expected_batch != batch_hash:
        raise QuarantineError("La lista exacta no coincide con el hash de batch")
    root = Path(str(manifest.get("resolved_root") or "")).resolve(strict=True)
    if not root.is_dir():
        raise QuarantineError("La raíz histórica ya no es válida")
    if _is_within(manifest_path, root):
        raise QuarantineError("El manifiesto no puede vivir dentro de la raíz histórica")
    return root, proposals, batch_hash


def _validate_quarantine_root(quarantine_root: Path, *, project_root: Path) -> Path:
    candidate = Path(quarantine_root).expanduser()
    if not candidate.is_absolute():
        raise QuarantineError("La quarantine_root debe ser absoluta")
    if candidate.is_symlink() or _is_reparse(candidate):
        raise QuarantineError("La quarantine_root no puede ser symlink/reparse point")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise QuarantineError("La quarantine_root debe existir y ser un directorio")
    if _is_within(resolved, project_root) or _is_within(project_root, resolved):
        raise QuarantineError("La quarantine_root debe estar fuera de la raíz histórica")
    return resolved


def _prepare_items(
    root: Path,
    quarantine_root: Path,
    items_dir: Path,
    proposals: list[dict[str, Any]],
    *,
    current_entries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for proposal in proposals:
        name = str(proposal.get("relative_name") or "")
        source = Path(str(proposal.get("resolved_path") or "")).resolve(strict=True)
        if source.parent != root or source.name != name:
            raise QuarantineError(f"Target fuera de la raíz exacta: {name}")
        if source.is_symlink() or _is_reparse(source):
            raise QuarantineError(f"Target reparse no permitido: {name}")
        current = current_entries.get(name)
        if not current or current.get("classification") != "aiteam_disposable_candidate":
            raise QuarantineError(f"El target ya no es candidato: {name}")
        evidence = current.get("evidence", {})
        if _canonical_hash(evidence) != proposal.get("evidence_sha256"):
            raise QuarantineError(f"La evidencia viva cambió: {name}")
        handles = evidence.get("handles", {})
        if handles.get("state") != "observed":
            raise QuarantineError(f"Handles no observados: {name}")
        if int(handles.get("open_file_handle_count", 0) or 0) != 0:
            raise QuarantineError(f"Handles abiertos observados: {name}")
        destination = items_dir / name
        if destination.exists() or destination.is_symlink():
            raise QuarantineError(f"Colisión de cuarentena: {name}")
        tree_hash, file_count, total_bytes = _tree_inventory(source)
        prepared.append(
            {
                "relative_name": name,
                "source_path": str(source),
                "quarantine_path": str(destination),
                "state": "prepared",
                "tree_sha256": tree_hash,
                "file_count": file_count,
                "bytes": total_bytes,
                "evidence_sha256": proposal["evidence_sha256"],
            }
        )
    if not prepared:
        raise QuarantineError("El batch exacto está vacío")
    if len({item["source_path"] for item in prepared}) != len(prepared):
        raise QuarantineError("El batch contiene paths duplicados")
    if not _same_filesystem(root, quarantine_root):
        raise QuarantineError("Preflight cross-filesystem rechazado")
    return prepared


def _rollback_quarantine_moves(
    journal: dict[str, Any],
    journal_path: Path,
    *,
    moved_indices: list[int],
) -> list[str]:
    errors: list[str] = []
    for index in reversed(moved_indices):
        item = journal["items"][index]
        source = Path(item["source_path"])
        destination = Path(item["quarantine_path"])
        try:
            if source.exists():
                raise QuarantineError("source_collision_during_rollback")
            _atomic_move(destination, source)
            if _tree_digest(source) != item["tree_sha256"]:
                raise QuarantineError("checksum_mismatch_after_rollback")
            item["state"] = "rolled_back"
            item["rolled_back_at"] = _now()
        except (OSError, QuarantineError) as exc:
            item["state"] = "rollback_failed"
            errors.append(f"{item['relative_name']}:{type(exc).__name__}")
    journal["state"] = "rollback_failed" if errors else "rolled_back"
    journal["events"].append(
        {"at": _now(), "type": journal["state"], "errors": errors}
    )
    _write_journal(journal_path, journal)
    return errors


def _rollback_restore_moves(
    journal: dict[str, Any],
    journal_path: Path,
    *,
    restored_indices: list[int],
) -> list[str]:
    errors: list[str] = []
    for index in reversed(restored_indices):
        item = journal["items"][index]
        source = Path(item["source_path"])
        destination = Path(item["quarantine_path"])
        try:
            if destination.exists():
                raise QuarantineError("quarantine_collision_during_restore_rollback")
            _atomic_move(source, destination)
            if _tree_digest(destination) != item["tree_sha256"]:
                raise QuarantineError("checksum_mismatch_after_restore_rollback")
            item["state"] = "quarantined"
        except (OSError, QuarantineError) as exc:
            item["state"] = "restore_rollback_failed"
            errors.append(f"{item['relative_name']}:{type(exc).__name__}")
    journal["state"] = "restore_rollback_failed" if errors else "quarantined"
    journal["events"].append(
        {"at": _now(), "type": "restore_rollback", "errors": errors}
    )
    _write_journal(journal_path, journal)
    return errors


def _tree_digest(root: Path) -> str:
    return _tree_inventory(root)[0]


def _tree_inventory(root: Path) -> tuple[str, int, int]:
    if not root.is_dir() or root.is_symlink() or _is_reparse(root):
        raise QuarantineError("El árbol a sellar no es un directorio real")
    records: list[dict[str, Any]] = []
    file_count = 0
    total_bytes = 0
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as iterator:
            entries = sorted(iterator, key=lambda item: item.name.casefold())
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink() or _stat_is_reparse(info):
                raise QuarantineError(f"Symlink/reparse interno no permitido: {relative}")
            if stat.S_ISDIR(info.st_mode):
                records.append({"path": relative, "type": "dir"})
                stack.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                digest = _file_sha256(Path(entry.path))
                size = int(info.st_size)
                records.append(
                    {"path": relative, "type": "file", "bytes": size, "sha256": digest}
                )
                file_count += 1
                total_bytes += size
            else:
                raise QuarantineError(f"Tipo de filesystem no soportado: {relative}")
    records.sort(key=lambda item: (str(item["path"]).casefold(), str(item["type"])))
    return _canonical_hash(records), file_count, total_bytes


def _load_and_verify_journal(journal_path: Path) -> dict[str, Any]:
    journal = _load_json(journal_path)
    if journal.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise QuarantineError("Schema de journal no soportado")
    expected = str(journal.get("journal_sha256") or "")
    payload = {key: value for key, value in journal.items() if key != "journal_sha256"}
    if not expected or expected != _canonical_hash(payload):
        raise QuarantineError("Integridad del journal inválida")
    stored_manifest = journal_path.parent / "approved-remediation-manifest.json"
    if (
        not stored_manifest.is_file()
        or _file_sha256(stored_manifest) != journal.get("stored_manifest_sha256")
    ):
        raise QuarantineError("Integridad de la copia aprobada del manifiesto inválida")
    manifest = _load_json(stored_manifest)
    if (
        not verify_remediation_manifest(manifest)
        or manifest.get("manifest_sha256") != journal.get("manifest_sha256")
    ):
        raise QuarantineError("El manifiesto aprobado almacenado no coincide")
    return journal


def _write_journal(journal_path: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _now()
    journal.pop("journal_sha256", None)
    journal["journal_sha256"] = _canonical_hash(journal)
    temporary = journal_path.with_name(f".{journal_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, journal_path)


def _target_batch_hash(proposals: list[dict[str, Any]]) -> str:
    batch = [
        {
            "resolved_path": item["resolved_path"],
            "evidence_sha256": item["evidence_sha256"],
            "proposed_action": item["proposed_action"],
        }
        for item in proposals
    ]
    return _canonical_hash(batch)


def _atomic_move(source: Path, destination: Path) -> None:
    os.rename(_extended_windows_path(source), _extended_windows_path(destination))


def _extended_windows_path(path: Path) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _same_filesystem(left: Path, right: Path) -> bool:
    return left.stat().st_dev == right.stat().st_dev


def _is_reparse(path: Path) -> bool:
    try:
        return _stat_is_reparse(path.lstat())
    except OSError:
        return False


def _stat_is_reparse(info: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(info, "st_file_attributes", 0) & flag)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QuarantineError(f"No se pudo leer JSON válido: {path.name}") from exc
    if not isinstance(payload, dict):
        raise QuarantineError(f"El JSON debe ser un objeto: {path.name}")
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
