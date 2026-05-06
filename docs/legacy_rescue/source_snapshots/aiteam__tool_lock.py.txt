from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ToolLockManager:
    """Gestiona el versionado estricto de herramientas (NPM/Python) en runtime."""

    def __init__(self, runtime_dir: Path) -> None:
        # API interna plana (write_lock / read_lock)
        self.lock_path = runtime_dir / "tool_lock.json"
        # API versionada (generate_lockfile / read_lockfile / check_drift)
        self.lock_file = runtime_dir / "tools.lock.json"
        runtime_dir.mkdir(parents=True, exist_ok=True)

    # --- Entry creation ---

    def create_lock_entry(
        self, tool_name: str, version: str, source: str
    ) -> dict[str, Any]:
        """Create a lock entry dict with version, source, checksum and timestamp."""
        payload = {"tool_name": tool_name, "version": version, "source": source}
        payload_str = json.dumps(payload, sort_keys=True)
        checksum = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
        return {
            "version": version,
            "source": source,
            "checksum": checksum,
            "locked_at": datetime.now(timezone.utc).isoformat(),
        }

    # --- Read / Write ---

    def read_lock(self) -> dict[str, dict[str, Any]]:
        """Read lock file. Returns {} if file doesn't exist or is corrupted."""
        if not self.lock_path.exists():
            return {}
        try:
            raw = self.lock_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            logger.error("Error reading tool lock from %s", self.lock_path)
            return {}

    def write_lock(self, lock_data: dict[str, dict[str, Any]]) -> None:
        """Write lock data atomically via temp+rename."""
        from aiteam.persistence import AtomicFileWriter
        AtomicFileWriter.write_json_atomic(self.lock_path, lock_data)

    # --- Query ---

    def get_pinned_version(self, tool_name: str) -> str | None:
        """Return the pinned version string for a tool, or None if not locked."""
        entry = self.read_lock().get(tool_name)
        if isinstance(entry, dict):
            return entry.get("version")
        return None

    def list_locked_tools(self) -> dict[str, str]:
        """Return a mapping of tool_name → version for all locked tools."""
        return {
            name: entry["version"]
            for name, entry in self.read_lock().items()
            if isinstance(entry, dict) and "version" in entry
        }

    # --- Mutations ---

    def update_lock_entry(self, tool_name: str, version: str, source: str) -> None:
        """Update (or add) a lock entry and save."""
        lock = self.read_lock()
        lock[tool_name] = self.create_lock_entry(tool_name, version, source)
        self.write_lock(lock)

    def remove_lock_entry(self, tool_name: str) -> None:
        """Remove a tool from the lock file."""
        lock = self.read_lock()
        lock.pop(tool_name, None)
        self.write_lock(lock)

    # --- Integrity ---

    def verify_lock_integrity(self) -> tuple[bool, str]:
        """Verify checksums for all entries. Returns (is_valid, error_message)."""
        lock = self.read_lock()
        for tool_name, entry in lock.items():
            if not isinstance(entry, dict):
                return False, f"entry for '{tool_name}' is not a dict"
            stored_checksum = entry.get("checksum", "")
            version = entry.get("version", "")
            source = entry.get("source", "")
            payload = {"tool_name": tool_name, "version": version, "source": source}
            payload_str = json.dumps(payload, sort_keys=True)
            expected = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
            if stored_checksum != expected:
                return False, f"checksum mismatch for tool '{tool_name}'"
        return True, ""

    # --- Versioned lockfile API (CLI + test_tool_lock.py) ---

    def generate_lockfile(self, tools: list[dict]) -> None:
        """Genera un lockfile versionado a partir de una lista de tool dicts.

        Cada entrada del resultado tiene: version, source, source_type, integrity, locked_at.
        """
        lock_tools: dict[str, Any] = {}
        for tool in tools:
            name = tool.get("name", "")
            version = tool.get("version", "latest")
            source = tool.get("source", "")
            source_type = tool.get("source_type", "")
            payload = {
                "name": name,
                "source_type": source_type,
                "source": source,
                "version": version,
            }
            payload_str = json.dumps(payload, sort_keys=True)
            integrity = hashlib.sha256(payload_str.encode()).hexdigest()[:16]
            lock_tools[name] = {
                "version": version,
                "source": source,
                "source_type": source_type,
                "integrity": integrity,
                "locked_at": datetime.now(timezone.utc).isoformat(),
            }
        data: dict[str, Any] = {"version": "1.0", "tools": lock_tools}
        from aiteam.persistence import AtomicFileWriter
        AtomicFileWriter.write_json_atomic(self.lock_file, data)

    def read_lockfile(self) -> dict[str, Any]:
        """Lee el lockfile versionado. Retorna {'version':'1.0','tools':{}} si no existe."""
        if not self.lock_file.exists():
            return {"version": "1.0", "tools": {}}
        try:
            raw = self.lock_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "tools" in data:
                return data
            # formato plano legado: envolvemos
            return {"version": "1.0", "tools": data}
        except (json.JSONDecodeError, OSError):
            return {"version": "1.0", "tools": {}}

    def check_drift(self, requested: list[dict]) -> list[str]:
        """Detecta diferencias entre herramientas solicitadas y el lockfile."""
        locked_tools = self.read_lockfile().get("tools", {})
        drifts: list[str] = []
        for tool in requested:
            name = tool.get("name", "")
            source = tool.get("source", "")
            if name not in locked_tools:
                drifts.append(f"{name} is not in lockfile")
            elif locked_tools[name].get("source") != source:
                drifts.append(f"{name} source changed")
        return drifts

    # --- Legacy compat (mantiene compatibilidad con codigo existente) ---

    def load_locks(self) -> dict[str, str]:
        """Legacy: returns flat tool_name → version dict."""
        return self.list_locked_tools()

    def save_locks(self, locks: dict[str, str]) -> None:
        """Legacy: save flat tool_name → version dict (sin checksum)."""
        lock_data = {
            name: self.create_lock_entry(name, version, source="")
            for name, version in locks.items()
        }
        self.write_lock(lock_data)

    def get_locked_version(self, tool_name: str) -> str | None:
        """Legacy alias for get_pinned_version."""
        return self.get_pinned_version(tool_name)

    def lock_tool(self, tool_name: str, version: str) -> None:
        """Legacy: lock a tool by name and version."""
        self.update_lock_entry(tool_name, version, source="")

    def verify_tool(self, tool_name: str, actual_version: str) -> bool:
        """Legacy: verify or auto-lock a tool version."""
        pinned = self.get_pinned_version(tool_name)
        if pinned is None:
            self.lock_tool(tool_name, actual_version)
            return True
        return pinned == actual_version
