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


class BudgetAtomicityTests(unittest.TestCase):
    """M2: Dos agentes escriben al ledger en paralelo sin pérdida de registros."""

    def _make_routing_decision(self, cost_usd: float):
        from unittest.mock import MagicMock
        from aiteam.types import RoutingDecision, AdapterResponse, RoutingChannel

        resp = MagicMock(spec=AdapterResponse)
        resp.input_tokens = 100
        resp.output_tokens = 100

        decision = MagicMock(spec=RoutingDecision)
        decision.provider = "openai"
        decision.model = "gpt-4o-mini"
        decision.channel = RoutingChannel.API
        decision.reason = "test"
        decision.success = True
        decision.response = resp
        return decision

    def test_concurrent_ledger_writes_no_loss(self) -> None:
        """Dos hilos escriben 50 registros cada uno → 100 en el ledger, sin corrupción."""
        import threading
        from aiteam.finops import BudgetManager, BudgetPolicy
        from aiteam.persistence import AtomicFileWriter

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "cost_ledger.jsonl"
            errors: list[Exception] = []
            n_writes = 50

            def write_records():
                try:
                    for i in range(n_writes):
                        AtomicFileWriter.append_jsonl_with_checksum(
                            ledger_path,
                            {"ts": f"2026-01-01T00:00:{i:02d}Z", "cost_usd": 0.001, "thread": threading.current_thread().name},
                        )
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=write_records, name="agent-1")
            t2 = threading.Thread(target=write_records, name="agent-2")
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            self.assertFalse(errors, f"Errores durante escritura concurrente: {errors}")

            records = AtomicFileWriter.read_jsonl_with_dedup(ledger_path)
            # Cada hilo escribió n_writes registros; pueden deduplicarse por checksum
            # si tienen el mismo contenido, pero los ts únicos evitan eso.
            self.assertEqual(len(records), n_writes * 2,
                             f"Se esperaban {n_writes * 2} registros, se leyeron {len(records)}")

            # Cada registro debe tener sus campos originales (read_jsonl_with_dedup
            # ya elimina el campo _checksum interno de validación — eso es correcto)
            for rec in records:
                self.assertIn("cost_usd", rec, "Registro incompleto indica corrupción")


if __name__ == "__main__":
    unittest.main()
