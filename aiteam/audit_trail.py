from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.persistence import AtomicFileWriter


class AuditTrail:
    def __init__(self, runtime_dir: Path) -> None:
        self.log_path = runtime_dir / "audit_trail.jsonl"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("", encoding="utf-8")

    def audit_decision(
        self,
        decision_type: str,
        task_id: str,
        approver_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
        rule_applied: str = "unknown",
    ) -> None:
        if metadata is None:
            metadata = {}
            
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "decision_type": decision_type,
            "task_id": task_id,
            "approver_id": approver_id,
            "reason": reason,
            "metadata": metadata,
            "rule_applied": rule_applied,
        }
        AtomicFileWriter.append_jsonl_with_checksum(self.log_path, record)

    def records(self) -> list[dict[str, Any]]:
        return AtomicFileWriter.read_jsonl_with_dedup(self.log_path)

    def windowed_records(self, start_ts: str, end_ts: str) -> list[dict[str, Any]]:
        docs = self.records()
        valid = []
        for d in docs:
            t = d.get("ts", "")
            if start_ts <= t <= end_ts:
                valid.append(d)
        return valid
