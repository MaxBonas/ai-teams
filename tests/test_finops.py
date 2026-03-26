import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from aiteam.finops import BudgetManager, BudgetPolicy


class FinOpsTests(unittest.TestCase):
    def test_snapshot_includes_utilization_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(daily_api_budget_usd=2.0, monthly_api_budget_usd=20.0),
            )
            self._write_cost_entry(manager, cost_usd=1.0)
            snapshot = manager.snapshot()

            self.assertEqual(snapshot["daily_api_spend_usd"], 1.0)
            self.assertEqual(snapshot["daily_utilization_ratio"], 0.5)
            self.assertEqual(snapshot["monthly_utilization_ratio"], 0.05)

    def test_api_signal_applies_cost_controls_under_pressure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(daily_api_budget_usd=1.0, monthly_api_budget_usd=100.0),
            )
            self._write_cost_entry(manager, cost_usd=0.8)

            signal = manager.api_signal()
            self.assertTrue(signal.can_use_api)
            self.assertEqual(signal.max_api_cost_tier, 1)
            self.assertEqual(signal.suggested_max_api_attempts, 1)

    def test_api_signal_blocks_api_after_budget_exceeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(daily_api_budget_usd=1.0, monthly_api_budget_usd=100.0),
            )
            self._write_cost_entry(manager, cost_usd=1.1)

            signal = manager.api_signal()
            self.assertFalse(signal.can_use_api)
            self.assertEqual(signal.max_api_cost_tier, 0)
            self.assertEqual(signal.suggested_max_api_attempts, 0)

    def test_daily_spend_by_model_returns_aggregated_amounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BudgetManager(
                runtime_dir=Path(tmp),
                policy=BudgetPolicy(daily_api_budget_usd=10.0, monthly_api_budget_usd=100.0),
            )
            now = datetime.now(timezone.utc).isoformat()
            manager.ledger_path.write_text(
                "\n".join(
                    [
                        json.dumps({"ts": now, "model": "gpt-4.1-mini", "cost_usd": 0.2}),
                        json.dumps({"ts": now, "model": "gpt-4.1-mini", "cost_usd": 0.3}),
                        json.dumps({"ts": now, "model": "gpt-4o-mini", "cost_usd": 0.5}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            by_model = manager.daily_spend_by_model(now[:10])
            self.assertAlmostEqual(by_model.get("gpt-4.1-mini", 0.0), 0.5, places=6)
            self.assertAlmostEqual(by_model.get("gpt-4o-mini", 0.0), 0.5, places=6)

            records = manager.list_ledger_records()
            self.assertEqual(len(records), 3)

    @staticmethod
    def _write_cost_entry(manager: BudgetManager, cost_usd: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "ts": now,
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "channel": "api",
            "reason": "test",
            "success": True,
            "input_tokens": 100,
            "output_tokens": 100,
            "cost_usd": cost_usd,
        }
        manager.ledger_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
