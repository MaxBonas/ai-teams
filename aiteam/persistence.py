"""Atomic file operations with checksums and deduplication."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
from pathlib import Path
from typing import Any


class AtomicFileWriter:
    """Write JSON/JSONL files atomically with CRC32 validation."""

    _locks_guard = threading.RLock()
    _path_locks: dict[str, threading.RLock] = {}

    @classmethod
    def _lock_for(cls, path: Path) -> threading.RLock:
        key = str(path.resolve())
        with cls._locks_guard:
            lock = cls._path_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                cls._path_locks[key] = lock
            return lock

    @staticmethod
    def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        """Write JSON file atomically (write-to-temp + rename)."""
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                try:
                    json.dump(payload, tmp, indent=2, ensure_ascii=True)
                    tmp.flush()
                except Exception:
                    tmp_path.unlink(missing_ok=True)
                    raise

            try:
                tmp_path.replace(path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    @staticmethod
    def write_jsonl_atomic(path: Path, lines: list[dict[str, Any]]) -> None:
        """Write JSONL file atomically."""
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                try:
                    for line_obj in lines:
                        tmp.write(json.dumps(line_obj, ensure_ascii=True) + "\n")
                    tmp.flush()
                except Exception:
                    tmp_path.unlink(missing_ok=True)
                    raise

            try:
                tmp_path.replace(path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    @staticmethod
    def rewrite_jsonl_with_checksums(path: Path, records: list[dict[str, Any]]) -> None:
        """Write JSONL file atomically, injecting checksums into every record."""
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                tmp_path = Path(tmp.name)
                try:
                    for record in records:
                        payload = dict(record)
                        payload.pop("_checksum", None)
                        payload_str = json.dumps(payload, ensure_ascii=True, sort_keys=True)
                        checksum = hashlib.md5(payload_str.encode()).hexdigest()
                        payload["_checksum"] = checksum
                        tmp.write(json.dumps(payload, ensure_ascii=True) + "\n")
                    tmp.flush()
                except Exception:
                    tmp_path.unlink(missing_ok=True)
                    raise

            try:
                tmp_path.replace(path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    @staticmethod
    def append_jsonl_with_checksum(path: Path, record: dict[str, Any]) -> None:
        """Append single JSONL record with CRC32 checksum."""
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")

            payload = dict(record)
            payload_str = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            checksum = hashlib.md5(payload_str.encode()).hexdigest()
            payload["_checksum"] = checksum

            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")
                f.flush()

    @staticmethod
    def read_jsonl_with_dedup(path: Path) -> list[dict[str, Any]]:
        """Read JSONL file, skip corrupted lines and dedup by content."""
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            if not path.exists():
                return []

            raw = path.read_text(encoding="utf-8")
            items: list[dict[str, Any]] = []
            seen_checksums: set[str] = set()

            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue

                checksum = record.get("_checksum", None)
                if checksum and checksum in seen_checksums:
                    continue
                if checksum:
                    seen_checksums.add(checksum)

                clean = {k: v for k, v in record.items() if k != "_checksum"}
                items.append(clean)
            return items

    @staticmethod
    def read_jsonl_tail(path: Path, tail: int) -> list[dict[str, Any]]:
        """Read the last *tail* records from a JSONL file without loading the
        entire file.  Uses a backwards seek so only a small chunk of bytes is
        read from disk.  Dedup within the returned window is preserved.
        """
        if tail <= 0:
            return []
        lock = AtomicFileWriter._lock_for(path)
        with lock:
            if not path.exists():
                return []
            # Conservative estimate: 2 KB per line.  We read enough bytes from
            # the end to contain at least `tail` records, then discard the
            # (possibly partial) first line if we did not start at offset 0.
            bytes_to_read = tail * 2048
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                file_size = fh.tell()
                offset = max(0, file_size - bytes_to_read)
                fh.seek(offset)
                raw_bytes = fh.read()

            raw = raw_bytes.decode("utf-8", errors="replace")

            # If we started mid-file, drop the first (potentially partial) line.
            if offset > 0:
                newline_pos = raw.find("\n")
                if newline_pos >= 0:
                    raw = raw[newline_pos + 1 :]
                else:
                    # No newline found — we're inside a single huge line; bail.
                    return []

            items: list[dict[str, Any]] = []
            seen_checksums: set[str] = set()
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                checksum = record.get("_checksum", None)
                if checksum and checksum in seen_checksums:
                    continue
                if checksum:
                    seen_checksums.add(checksum)
                clean = {k: v for k, v in record.items() if k != "_checksum"}
                items.append(clean)

            # Return only the last `tail` records from the window we read.
            return items[-tail:]
