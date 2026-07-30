from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.project_artifact_audit import AuditOptions, audit_project_root

SCHEMA_VERSION = "project_artifact_remediation_manifest_v1"
_GLOB_CHARS = frozenset("*?[]")


class RemediationPlanError(ValueError):
    """The requested dry-run cannot be represented safely."""


def build_remediation_manifest(
    root: Path,
    *,
    target_names: Iterable[str] = (),
    include_all_candidates: bool = False,
    active_workspace: Path | None = None,
    registry_workspaces: Iterable[Path] = (),
    audit_options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Revalidate a legacy root and build a non-executable exact-path plan."""
    root = _validate_explicit_root(root)
    names = tuple(_validate_target_name(name) for name in target_names)
    if include_all_candidates == bool(names):
        raise RemediationPlanError(
            "Usa exactamente uno: --include-all-candidates o uno/más --target-name"
        )
    if len(set(names)) != len(names):
        raise RemediationPlanError("Los targets exactos no pueden repetirse")

    audit = audit_project_root(
        root,
        active_workspace=active_workspace,
        registry_workspaces=registry_workspaces,
        options=audit_options,
    )
    entries = {str(entry["relative_path"]): entry for entry in audit["entries"]}
    if include_all_candidates:
        names = tuple(
            sorted(
                (
                    name
                    for name, entry in entries.items()
                    if entry["classification"] == "aiteam_disposable_candidate"
                ),
                key=str.casefold,
            )
        )
        if not names:
            raise RemediationPlanError(
                "La raíz no contiene candidatos legacy; no se genera un plan en un árbol limpio"
            )
    else:
        missing = sorted(set(names) - entries.keys(), key=str.casefold)
        if missing:
            raise RemediationPlanError(
                "Cada target debe existir como hijo directo exacto; faltan: "
                + ", ".join(missing)
            )

    proposals: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    for name in names:
        entry = entries[name]
        evidence = entry["evidence"]
        path = (
            root / name
            if evidence.get("reparse_point")
            else _resolve_exact_child(root, name)
        )
        evidence_hash = _canonical_hash(evidence)
        common = {
            "resolved_path": str(path),
            "relative_name": name,
            "classification": entry["classification"],
            "confidence": entry["confidence"],
            "evidence_sha256": evidence_hash,
            "evidence": evidence,
            "size_bytes": evidence.get("size", {}).get("bytes"),
        }
        denial_reasons = _denial_reasons(entry)
        if denial_reasons:
            denied.append(
                {
                    **common,
                    "proposed_action": "none",
                    "denial_reasons": denial_reasons,
                }
            )
            continue
        proposals.append(
            {
                **common,
                "proposed_action": "quarantine_in_k8_4_after_separate_approval",
                "risks": [
                    "classification_is_not_ownership",
                    "project_may_contain_user_files",
                    "sqlite_database_present",
                    "filesystem_can_change_after_manifest",
                ],
                "recoverability": {
                    "required": True,
                    "mechanism": "byte_exact_external_quarantine",
                    "implemented_in_this_step": False,
                    "restore_test_required": True,
                    "permanent_purge_requires_second_approval": True,
                },
            }
        )

    target_batch = [
        {
            "resolved_path": item["resolved_path"],
            "evidence_sha256": item["evidence_sha256"],
            "proposed_action": item["proposed_action"],
        }
        for item in proposals
    ]
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "manual_dry_run_only",
        "resolved_root": str(root),
        "source_audit": {
            "schema_version": audit["schema_version"],
            "content_sha256": audit["content_sha256"],
            "summary": audit["summary"],
        },
        "selection": {
            "mode": "all_revalidated_candidates" if include_all_candidates else "exact_names",
            "requested_count": len(names),
            "exact_targets_only": True,
            "globs_allowed": False,
            "prefixes_allowed": False,
            "root_target_allowed": False,
        },
        "safety": {
            "execution_authorized": False,
            "quarantine_authorized": False,
            "cleanup_authorized": False,
            "moves_performed": 0,
            "deletions_performed": 0,
            "renames_performed": 0,
            "project_writes_performed": 0,
        },
        "summary": {
            "proposed_count": len(proposals),
            "denied_count": len(denied),
            "proposed_bytes": sum(int(item["size_bytes"] or 0) for item in proposals),
            "ready_for_owner_review": bool(proposals) and not denied,
        },
        "proposals": proposals,
        "denied": denied,
        "approval": {
            "status": "not_approved",
            "owner_must_approve_manifest_sha256": True,
            "owner_must_review_every_exact_path": True,
            "approval_is_not_execution": True,
            "target_batch_sha256": _canonical_hash(target_batch),
        },
        "next_step": {
            "component": "P0.K.8.4",
            "available": False,
            "reason": "quarantine_and_rollback_not_implemented_in_this_manifest",
        },
    }
    manifest["manifest_sha256"] = _canonical_hash(manifest)
    return manifest


def write_remediation_manifest(
    manifest: dict[str, Any],
    output: Path,
    *,
    audited_root: Path,
) -> None:
    """Create, never replace, a manifest outside the historical root."""
    root = _validate_explicit_root(audited_root)
    output = Path(output).expanduser().resolve()
    if _is_within(output, root):
        raise RemediationPlanError(
            "El manifiesto debe escribirse fuera de la raíz histórica"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except FileExistsError as exc:
        raise RemediationPlanError(
            "El manifiesto es inmutable y no puede sobrescribir un archivo existente"
        ) from exc


def verify_remediation_manifest(manifest: dict[str, Any]) -> bool:
    expected = str(manifest.get("manifest_sha256") or "")
    return bool(expected) and expected == _canonical_hash(
        {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    )


def _denial_reasons(entry: dict[str, Any]) -> list[str]:
    evidence = entry.get("evidence", {})
    git = evidence.get("git", {})
    refs = evidence.get("references", {})
    reasons: list[str] = []
    if entry.get("classification") != "aiteam_disposable_candidate":
        reasons.append(f"classification_{entry.get('classification', 'unknown')}")
    if evidence.get("reparse_point"):
        reasons.append("symlink_or_reparse_point")
    if refs.get("active_workspace"):
        reasons.append("active_current_project")
    if refs.get("registry_workspace"):
        reasons.append("referenced_by_workspace_registry")
    if evidence.get("database", {}).get("state") != "valid":
        reasons.append("database_not_valid")
    if git.get("state") not in {"observed", "not_a_git_repository"}:
        reasons.append("git_not_observed")
    if git.get("dirty"):
        reasons.append("git_dirty")
    if git.get("untracked"):
        reasons.append("git_untracked")
    if int(git.get("remote_count", 0) or 0) > 0:
        reasons.append("git_remote_present")
    if evidence.get("size", {}).get("state") != "complete":
        reasons.append("filesystem_inventory_incomplete")
    handles = evidence.get("handles", {})
    if (
        handles.get("state") == "observed"
        and int(handles.get("open_file_handle_count", 0) or 0) > 0
    ):
        reasons.append("open_file_handles_observed")
    return sorted(set(reasons))


def _validate_explicit_root(root: Path) -> Path:
    candidate = Path(root).expanduser()
    if not candidate.is_absolute():
        raise RemediationPlanError("La raíz histórica debe ser absoluta y explícita")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise RemediationPlanError("La raíz histórica debe ser un directorio")
    return resolved


def _validate_target_name(name: str) -> str:
    value = str(name)
    if not value or value in {".", ".."}:
        raise RemediationPlanError("Cada target debe ser un nombre exacto no vacío")
    if Path(value).is_absolute():
        raise RemediationPlanError("Usa nombres hijos exactos, no paths absolutos")
    separators = {os.sep, "/", "\\"}
    if os.altsep:
        separators.add(os.altsep)
    if any(char in value for char in separators):
        raise RemediationPlanError("Los targets no pueden contener separadores")
    if any(char in value for char in _GLOB_CHARS):
        raise RemediationPlanError("Globs y patrones no están permitidos")
    return value


def _resolve_exact_child(root: Path, name: str) -> Path:
    candidate = root / name
    if candidate.is_symlink():
        raise RemediationPlanError(f"El target exacto es un symlink: {name}")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != root:
        raise RemediationPlanError(f"El target no es un hijo directo exacto: {name}")
    if not resolved.is_dir():
        raise RemediationPlanError(f"El target no es un directorio: {name}")
    return resolved


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
