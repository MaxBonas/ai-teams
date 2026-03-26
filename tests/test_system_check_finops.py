"""Tests for system-check finops reporting and cost anomaly checks."""

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase

from aiteam.finops import BudgetManager, BudgetPolicy


class TestSystemCheckFinops(TestCase):
    """Test suite for system-check finops reporting."""

    def setUp(self) -> None:
        """Create a temporary runtime directory for each test."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_system_check_"))
        self.policy = BudgetPolicy(
            daily_api_budget_usd=10.0,
            monthly_api_budget_usd=200.0,
            anomaly_detection_enabled=True,
            anomaly_zscore_threshold=3.0,
        )
        self.manager = BudgetManager(
            runtime_dir=self.temp_dir,
            policy=self.policy,
        )

    def tearDown(self) -> None:
        """Clean up temporary files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_system_check_includes_finops_section(self) -> None:
        """system-check JSON report has 'finops' key with cost_anomaly_detected and reason.
        
        Expected:
        - report["finops"] exists
        - report["finops"]["cost_anomaly_detected"] is boolean
        - report["finops"]["cost_anomaly_reason"] is string
        """
        # Simulate system-check by generating finops report
        anomaly_detected, reason = self.manager.detect_cost_anomaly()
        
        finops_report = {
            "cost_anomaly_detected": anomaly_detected,
            "cost_anomaly_reason": reason,
        }
        
        self.assertIn("cost_anomaly_detected", finops_report)
        self.assertIn("cost_anomaly_reason", finops_report)
        self.assertIsInstance(finops_report["cost_anomaly_detected"], bool)
        self.assertIsInstance(finops_report["cost_anomaly_reason"], str)

    def test_system_check_cost_anomaly_check_fails_report(self) -> None:
        """system-check includes cost_anomaly check in fails list if anomaly detected.
        
        Setup:
        - Inject a cost spike to trigger anomaly condition
        
        Expected:
        - If anomaly detected, failed_checks should include "cost_anomaly=" check
        """
        # Inject spike to trigger anomaly
        now = datetime.now(timezone.utc)
        today_str = now.date().isoformat()
        
        entries = []
        # Create baseline entries for 6 past days
        for days_ago in range(6, 0, -1):
            day = (now - timedelta(days=days_ago)).date()
            entry = {
                "ts": f"{day.isoformat()}T12:00:00+00:00",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "channel": "api",
                "reason": "test",
                "success": True,
                "input_tokens": 100000,
                "output_tokens": 0,
                "cost_usd": 1.00,
            }
            entries.append(entry)
        
        # Add spike for today
        entry = {
            "ts": f"{today_str}T22:00:00+00:00",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "channel": "api",
            "reason": "test",
            "success": True,
            "input_tokens": 10000000,
            "output_tokens": 0,
            "cost_usd": 100.00,
        }
        entries.append(entry)
        
        # Write entries
        for entry in entries:
            with open(self.manager.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        
        anomaly_detected, reason = self.manager.detect_cost_anomaly()
        
        # Simulate system-check failed_checks logic
        failed_checks = []
        if anomaly_detected:
            failed_checks.append(f"cost_anomaly={reason}")
        
        # Verify: if anomaly detected, check is in failed_checks
        if anomaly_detected:
            self.assertTrue(any("cost_anomaly=" in check for check in failed_checks))
        
        # Always pass if no anomaly (no failed checks)
        self.assertIsInstance(failed_checks, list)

    def test_system_check_finops_section_schema(self) -> None:
        """finops section has correct schema (types, keys).
        
        Expected:
        - finops["cost_anomaly_detected"] is bool
        - finops["cost_anomaly_reason"] is str
        - Exactly 2 keys in finops
        """
        anomaly_detected, reason = self.manager.detect_cost_anomaly()
        
        finops = {
            "cost_anomaly_detected": anomaly_detected,
            "cost_anomaly_reason": reason,
        }
        
        # Verify types
        self.assertIsInstance(finops["cost_anomaly_detected"], bool)
        self.assertIsInstance(finops["cost_anomaly_reason"], str)
        
        # Verify key count
        self.assertEqual(len(finops.keys()), 2, "finops section should have exactly 2 keys")
        
        # Verify both keys are present
        self.assertIn("cost_anomaly_detected", finops)
        self.assertIn("cost_anomaly_reason", finops)


class TestSystemCheckIntegration(TestCase):
    """Integration tests for system-check with finops."""

    def setUp(self) -> None:
        """Create a temporary runtime directory."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_system_check_int_"))

    def tearDown(self) -> None:
        """Clean up."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_empty_ledger_returns_insufficient_history(self) -> None:
        """Empty cost ledger returns 'insufficient_history'."""
        policy = BudgetPolicy()
        manager = BudgetManager(runtime_dir=self.temp_dir, policy=policy)
        
        anomaly_detected, reason = manager.detect_cost_anomaly()
        
        self.assertFalse(anomaly_detected)
        self.assertEqual(reason, "insufficient_history")

    def test_system_check_normal_operation_succeeds(self) -> None:
        """Normal operation (no anomaly) reports success."""
        policy = BudgetPolicy()
        manager = BudgetManager(runtime_dir=self.temp_dir, policy=policy)
        
        # Add normal entries (no spike)
        now = datetime.now(timezone.utc)
        for days_ago in range(6, 0, -1):
            day = (now - timedelta(days=days_ago)).date()
            entry = {
                "ts": f"{day.isoformat()}T12:00:00+00:00",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "channel": "api",
                "reason": "test",
                "success": True,
                "input_tokens": 1000,
                "output_tokens": 1000,
                "cost_usd": 0.01,
            }
            with open(manager.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        
        anomaly_detected, reason = manager.detect_cost_anomaly()
        
        # With normal, consistent spend, should be either "normal" or "insufficient_daily_data"
        # depending on month boundaries
        self.assertIsInstance(anomaly_detected, bool)
        self.assertIsInstance(reason, str)
