from __future__ import annotations

from typing import Any


class MetricsAggregator:
    def __init__(self, event_logger: Any) -> None:
        self.logger = event_logger

    def percentile_latency(self, p: int, window_hours: int) -> float:
        """Compute p-th percentile latency (p=50/95/99)."""
        events = self.logger.events_windowed(hours=window_hours)
        latencies = []
        for e in events:
            if e.get("event_type") == "task_execution":
                payload = e.get("payload", {})
                if "duration_ms" in payload:
                    latencies.append(payload["duration_ms"])
                elif "latency_ms" in payload:
                    latencies.append(payload["latency_ms"])
        
        if not latencies:
            return 0.0
            
        latencies.sort()
        k = (len(latencies) - 1) * (p / 100.0)
        f = int(k)
        c = f + 1
        
        if c >= len(latencies):
            return float(latencies[-1])
            
        d0 = latencies[f]
        d1 = latencies[c]
        return float(d0 + (d1 - d0) * (k - f))

    def event_type_breakdown(self, window_hours: int) -> dict[str, int]:
        events = self.logger.events_windowed(hours=window_hours)
        breakdown = {}
        for e in events:
            t = e.get("event_type", "unknown")
            breakdown[t] = breakdown.get(t, 0) + 1
        return breakdown

    def error_categorization(self, window_hours: int | None = None) -> dict[str, int]:
        if window_hours is None:
            events = self.logger._records()
        else:
            events = self.logger.events_windowed(hours=window_hours)
        errors = {}
        for e in events:
            if e.get("event_type") == "task_failed":
                payload = e.get("payload", {})
                # Podría haber reason en nivel raíz o dentro de payload
                reason = payload.get("reason", e.get("reason", "unknown"))
                r_lower = reason.lower() if isinstance(reason, str) else str(reason).lower()
                
                cat = "unknown"
                if "timeout" in r_lower:
                    cat = "timeout"
                elif "budget" in r_lower or "limit" in r_lower or "exceeded" in r_lower:
                    cat = "budget_block"
                elif "api" in r_lower or "connection" in r_lower or "network" in r_lower:
                    cat = "api_error"
                else:
                    cat = reason
                
                errors[cat] = errors.get(cat, 0) + 1
        return errors
