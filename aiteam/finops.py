from __future__ import annotations

import json
import calendar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiteam.persistence import AtomicFileWriter
from aiteam.types import RoutingDecision


@dataclass
class BudgetPolicy:
    daily_api_budget_usd: float = 10.0
    monthly_api_budget_usd: float = 200.0
    per_model_daily_cap_usd: dict[str, float] | None = None
    anomaly_detection_enabled: bool = True
    anomaly_zscore_threshold: float = 3.0


@dataclass
class ApiBudgetSignal:
    can_use_api: bool
    reason: str
    daily_utilization_ratio: float
    monthly_utilization_ratio: float
    max_api_cost_tier: int
    suggested_max_api_attempts: int


class BudgetManager:
    """Gestiona gasto API y aplica guardrails de presupuesto."""

    def __init__(
        self,
        runtime_dir: Path,
        policy: BudgetPolicy,
        model_cost_per_1k_tokens: dict[str, float] | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.policy = policy
        self.ledger_path = runtime_dir / "cost_ledger.jsonl"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.ledger_path.exists():
            self.ledger_path.write_text("", encoding="utf-8")
        self.model_cost_per_1k_tokens = model_cost_per_1k_tokens or {
            "gpt-5.3-codex": 0.0,
            "gpt-4.1-mini": 0.008,
            "gpt-4o-mini": 0.01,
            "gpt-api-premium": 0.03,
            "claude-api-premium": 0.03,
            "gemini-api-pro": 0.02,
            "claude-code": 0.0,
            "gemini-3.1-pro": 0.0,
        }

    def can_use_api(self) -> tuple[bool, str]:
        snapshot = self.snapshot()
        if snapshot["daily_api_spend_usd"] >= self.policy.daily_api_budget_usd:
            return False, "daily_api_budget_exceeded"
        if snapshot["monthly_api_spend_usd"] >= self.policy.monthly_api_budget_usd:
            return False, "monthly_api_budget_exceeded"
        return True, "ok"

    def api_signal(self) -> ApiBudgetSignal:
        snapshot = self.snapshot()
        can_use, reason = self.can_use_api()

        daily_ratio = float(snapshot.get("daily_utilization_ratio", 0.0))
        monthly_ratio = float(snapshot.get("monthly_utilization_ratio", 0.0))
        pressure = max(daily_ratio, monthly_ratio)

        max_tier = 2
        max_attempts = 2
        if pressure >= 0.9:
            max_tier = 1
            max_attempts = 1
        elif pressure >= 0.75:
            max_tier = 1
            max_attempts = 1
        elif pressure >= 0.5:
            max_tier = 2
            max_attempts = 1

        if not can_use:
            max_tier = 0
            max_attempts = 0

        return ApiBudgetSignal(
            can_use_api=can_use,
            reason=reason,
            daily_utilization_ratio=round(daily_ratio, 4),
            monthly_utilization_ratio=round(monthly_ratio, 4),
            max_api_cost_tier=max_tier,
            suggested_max_api_attempts=max_attempts,
        )

    def record_routing_decision(self, decision: RoutingDecision) -> float:
        now = datetime.now(timezone.utc)
        cost = self._estimate_cost_usd(decision)
        resp = decision.response
        payload = {
            "ts": now.isoformat(),
            "provider": decision.provider,
            "model": decision.model,
            "channel": decision.channel.value,
            "reason": decision.reason,
            "success": decision.success,
            "input_tokens": resp.input_tokens if resp else 0,
            "output_tokens": resp.output_tokens if resp else 0,
            "cost_usd": round(cost, 8),
        }
        AtomicFileWriter.append_jsonl_with_checksum(self.ledger_path, payload)
        return cost

    def snapshot(self) -> dict[str, float]:
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        month = now.strftime("%Y-%m")
        daily = 0.0
        monthly = 0.0
        entries = 0

        for record in self._records():
            ts = record.get("ts", "")
            if not ts:
                continue
            entries += 1
            cost = float(record.get("cost_usd", 0.0))
            if ts.startswith(day):
                daily += cost
            if ts.startswith(month):
                monthly += cost

        # Forecasting
        current_day = now.day
        _, days_in_month = calendar.monthrange(now.year, now.month)
        forecast_monthly = 0.0
        if current_day > 0:
            forecast_monthly = (monthly / current_day) * days_in_month

        return {
            "entries": float(entries),
            "daily_api_spend_usd": round(daily, 6),
            "monthly_api_spend_usd": round(monthly, 6),
            "forecast_monthly_spend_usd": round(forecast_monthly, 6),
            "daily_api_budget_usd": self.policy.daily_api_budget_usd,
            "monthly_api_budget_usd": self.policy.monthly_api_budget_usd,
            "daily_utilization_ratio": self._ratio(daily, self.policy.daily_api_budget_usd),
            "monthly_utilization_ratio": self._ratio(monthly, self.policy.monthly_api_budget_usd),
            "forecast_utilization_ratio": self._ratio(forecast_monthly, self.policy.monthly_api_budget_usd),
        }

    def _records(self) -> list[dict]:
        return AtomicFileWriter.read_jsonl_with_dedup(self.ledger_path)

    def list_ledger_records(self) -> list[dict]:
        """Return deduplicated ledger records for reporting/routing consumers."""
        return self._records()

    def daily_spend_by_model(self, day_iso: str | None = None) -> dict[str, float]:
        """Aggregate API spend by model for a specific day (UTC date string)."""
        target_day = day_iso or datetime.now(timezone.utc).date().isoformat()
        output: dict[str, float] = {}
        for record in self._records():
            ts = str(record.get("ts", ""))
            if not ts.startswith(target_day):
                continue
            model = str(record.get("model", "unknown"))
            cost = float(record.get("cost_usd", 0.0))
            output[model] = output.get(model, 0.0) + cost
        return output

    def _estimate_cost_usd(self, decision: RoutingDecision) -> float:
        if decision.channel.value != "api":
            return 0.0
        if not decision.response:
            return 0.0

        input_toks = decision.response.input_tokens or 0
        output_toks = decision.response.output_tokens or 0
        total_tokens = max(1, input_toks + output_toks)
        per_1k = self.model_cost_per_1k_tokens.get(decision.model, 0.04)
        return (total_tokens / 1000.0) * per_1k

    def detect_cost_anomaly(self) -> tuple[bool, str]:
        """Detect cost spike using z-score on daily spend."""
        if not self.policy.anomaly_detection_enabled:
            return False, "anomaly_detection_disabled"

        records = self._records()
        if len(records) < 7:
            return False, "insufficient_history"

        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")

        daily_costs: dict[str, float] = {}
        for record in records:
            ts = record.get("ts", "")
            if not ts.startswith(current_month):
                continue
            date_key = ts[:10]
            cost = float(record.get("cost_usd", 0.0))
            daily_costs[date_key] = daily_costs.get(date_key, 0.0) + cost

        if len(daily_costs) < 2:
            return False, "insufficient_daily_data"

        costs_list = sorted(daily_costs.values())
        mean = sum(costs_list) / len(costs_list)
        variance = sum((x - mean) ** 2 for x in costs_list) / len(costs_list)
        stddev = variance ** 0.5

        if stddev < 1e-9:
            return False, "no_variance"

        today_key = now.date().isoformat()
        today_cost = daily_costs.get(today_key, 0.0)
        zscore = (today_cost - mean) / stddev

        if zscore >= self.policy.anomaly_zscore_threshold:
            return True, f"cost_spike_zscore_{zscore:.2f}"
        return False, "normal"

    @staticmethod
    def _ratio(value: float, budget: float) -> float:
        if budget <= 0:
            return 0.0
        return round(value / budget, 6)
