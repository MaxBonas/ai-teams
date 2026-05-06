from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile


class SnapshotManager:
    def __init__(
        self,
        project_root: Path,
        storage_dir: Path | None = None,
        exclude_sensitive: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.storage_dir = (storage_dir or (self.project_root / ".aiteam_snapshots")).resolve()
        self.manifest_path = self.storage_dir / "manifest.json"
        self.exclude_sensitive = exclude_sensitive

    def create_snapshot(
        self,
        *,
        label: str | None = None,
        max_keep: int = 30,
        include_sensitive: bool = False,
    ) -> dict[str, Any]:
        snapshot_id = self._snapshot_id()
        archive_path = self.storage_dir / f"{snapshot_id}.zip"

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        file_count = 0
        with ZipFile(archive_path, mode="w", compression=ZIP_DEFLATED) as archive:
            for path in self._iter_project_files(include_sensitive=include_sensitive):
                rel = path.relative_to(self.project_root)
                archive.write(path, rel.as_posix())
                file_count += 1

        entry = {
            "id": snapshot_id,
            "label": (label or "").strip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive": archive_path.name,
            "file_count": file_count,
            "size_bytes": archive_path.stat().st_size,
        }

        manifest = self._load_manifest()
        entries = manifest.get("snapshots", [])
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        entries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

        if max_keep > 0 and len(entries) > max_keep:
            keep = entries[:max_keep]
            drop = entries[max_keep:]
            for item in drop:
                archive_name = str(item.get("archive", "")).strip()
                if not archive_name:
                    continue
                path = self.storage_dir / archive_name
                if path.exists():
                    path.unlink()
            entries = keep

        manifest["snapshots"] = entries
        self._write_manifest(manifest)
        return entry

    def list_snapshots(self) -> list[dict[str, Any]]:
        manifest = self._load_manifest()
        entries = manifest.get("snapshots", [])
        if not isinstance(entries, list):
            return []
        output = [item for item in entries if isinstance(item, dict)]
        output.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return output

    def restore_snapshot(self, snapshot_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        snapshot = self._find_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        archive = self.storage_dir / str(snapshot.get("archive", ""))
        if not archive.exists():
            raise ValueError(f"Snapshot archive missing: {archive}")

        restored_files = 0
        with ZipFile(archive, mode="r") as zip_file:
            members = [item for item in zip_file.infolist() if not item.is_dir()]
            if dry_run:
                return {
                    "snapshot_id": snapshot_id,
                    "restored_files": len(members),
                    "dry_run": True,
                }

            for member in members:
                relative = Path(member.filename)
                target = (self.project_root / relative).resolve()
                if target.is_symlink() or not self._is_within_project(target):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zip_file.read(member.filename))
                restored_files += 1

        return {
            "snapshot_id": snapshot_id,
            "restored_files": restored_files,
            "dry_run": False,
        }

    def _iter_project_files(self, *, include_sensitive: bool):
        for path in self.project_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.project_root)
            if self._skip_path(relative):
                continue
            if self.exclude_sensitive and not include_sensitive and self._is_sensitive_path(relative):
                continue
            yield path

    def _skip_path(self, relative: Path) -> bool:
        parts = {part.lower() for part in relative.parts}
        excluded_dirs = {
            ".aiteam_snapshots",
            ".git",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            "node_modules",
            ".venv",
            "venv",
        }
        if parts.intersection(excluded_dirs):
            return True
        if relative.name.lower().endswith((".pyc", ".pyo", ".tmp")):
            return True
        return False

    @staticmethod
    def _is_sensitive_path(relative: Path) -> bool:
        name = relative.name.lower()
        if name in {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}:
            return True
        if name.startswith(".env."):
            return True
        if name.endswith((".pem", ".key", ".p12", ".pfx")):
            return True

        indicators = ["credential", "credentials", "secret", "secrets", "token", "api_key", "apikey"]
        if any(marker in name for marker in indicators):
            if name.endswith((".json", ".txt", ".yaml", ".yml", ".env", ".ini")):
                return True
        return False

    def _find_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        for item in self.list_snapshots():
            if str(item.get("id", "")).strip() == snapshot_id:
                return item
        return None

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"snapshots": []}
        raw = self.manifest_path.read_text(encoding="utf-8")
        if not raw.strip():
            return {"snapshots": []}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {"snapshots": []}
        if not isinstance(payload, dict):
            return {"snapshots": []}
        return payload

    def _write_manifest(self, payload: dict[str, Any]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def _snapshot_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        token = secrets.token_hex(3)
        return f"{ts}-{token}"

    def _is_within_project(self, path: Path) -> bool:
        try:
            path.relative_to(self.project_root)
        except ValueError:
            return False
        return True
