from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from aiteam.persistence import AtomicFileWriter


class EventLogger:
    def __init__(self, runtime_dir: Path, alert_policy: Any | None = None) -> None:
        self.log_path = runtime_dir / "events.jsonl"
        self.alert_policy = alert_policy
        runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_id": str(uuid.uuid4()),  # garantiza checksum unico por evento
            "event_type": event_type,
            "payload": payload,
        }
        AtomicFileWriter.append_jsonl_with_checksum(self.log_path, record)

    def _safe_parse_ts(self, ts_str: str) -> datetime | None:
        try:
            return datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            return None

    def events_windowed(self, hours: int | None = None) -> list[dict[str, Any]]:
        records = self._records()
        if hours is None:
            return records
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            r for r in records
            if r.get("ts") and (dt := self._safe_parse_ts(r["ts"])) is not None and dt >= cutoff
        ]

    def summary(self, window_hours: int | None = None) -> dict[str, Any]:
        from aiteam.metrics import MetricsAggregator
        
        event_types = Counter()
        provider_counts = Counter()
        channel_counts = Counter()
        task_execution_total = 0
        task_execution_success = 0

        records = self.events_windowed(hours=window_hours)

        for record in records:
            event_types[str(record.get("event_type", "unknown"))] += 1
            if str(record.get("event_type", "")) != "task_execution":
                continue

            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                continue
            task_execution_total += 1
            success_val = payload.get("success", False)
            if success_val is True or success_val == 1:
                task_execution_success += 1
            provider_counts[str(payload.get("provider", "unknown"))] += 1
            channel_counts[str(payload.get("channel", "unknown"))] += 1

        success_rate = 0.0
        if task_execution_total > 0:
            success_rate = round((task_execution_success / task_execution_total) * 100.0, 2)

        api_share = 0.0
        if task_execution_total > 0:
            api_share = round((channel_counts.get("api", 0) / task_execution_total) * 100.0, 2)

        compliance_violations = int(event_types.get("compliance_violation", 0))
        quality_gates_opened = int(event_types.get("quality_gates_opened", 0))
        task_failed_events = int(event_types.get("task_failed", 0))
        
        agg = MetricsAggregator(self)
        error_breakdown = agg.error_categorization(window_hours=window_hours)
        
        # New Latency Percentiles (1.1)
        p50 = agg.percentile_latency(50, window_hours=window_hours or 24)
        p95 = agg.percentile_latency(95, window_hours=window_hours or 24)
        p99 = agg.percentile_latency(99, window_hours=window_hours or 24)

        min_exec = getattr(self.alert_policy, "min_execution_count_for_alert", 5) if self.alert_policy else 5
        min_succ = getattr(self.alert_policy, "min_success_rate_percent", 85.0) if self.alert_policy else 85.0
        max_api = getattr(self.alert_policy, "max_api_dependency_percent", 40.0) if self.alert_policy else 40.0
        max_fail = getattr(self.alert_policy, "max_recurrent_failures", 3) if self.alert_policy else 3

        alerts: list[str] = []
        if compliance_violations > 0:
            alerts.append(f"compliance_violations_detected:{compliance_violations}")
        if task_execution_total >= min_exec and success_rate < min_succ:
            alerts.append(f"low_task_execution_success_rate:{success_rate}")
        if task_execution_total >= min_exec and api_share > max_api:
            alerts.append(f"high_api_dependency:{api_share}")
        if task_failed_events >= max_fail:
            alerts.append(f"recurrent_task_failures:{task_failed_events}")

        return {
            "total_events": sum(event_types.values()),
            "event_types": dict(event_types),
            "error_breakdown": error_breakdown,
            "latency_p50": p50,
            "latency_p95": p95,
            "latency_p99": p99,
            "task_execution_total": task_execution_total,
            "task_execution_success": task_execution_success,
            "task_execution_success_rate": success_rate,
            "providers": dict(provider_counts),
            "channels": dict(channel_counts),
            "api_share_percent": api_share,
            "compliance_violations": compliance_violations,
            "quality_gates_opened": quality_gates_opened,
            "alerts": alerts,
            "alert_count": len(alerts),
        }

    def recent_events(self, hours: int = 1) -> list[dict[str, Any]]:
        """Get events from last N hours only."""
        return self.events_windowed(hours=hours)



    def prune_events(self, max_days: int = 30, archive_dir: Path | None = None) -> int:
        """Remove events older than max_days and optionally archive them."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
        records = self._records()
        valid_records = []
        archived_records = []
        
        for r in records:
            ts = r.get("ts")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
                
            if dt >= cutoff:
                valid_records.append(r)
            else:
                archived_records.append(r)
                
        if len(valid_records) == len(records):
            return 0
            
        AtomicFileWriter.rewrite_jsonl_with_checksums(self.log_path, valid_records)
        
        if archive_dir and archived_records:
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_path = archive_dir / f"events_archive_{stamp}.jsonl"
            AtomicFileWriter.rewrite_jsonl_with_checksums(archive_path, archived_records)
            
        return len(archived_records)

    def _records(self) -> list[dict[str, Any]]:
        return AtomicFileWriter.read_jsonl_with_dedup(self.log_path)
