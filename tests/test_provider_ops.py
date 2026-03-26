from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aiteam.provider_ops import build_provider_ops_view, sync_provider_ops_alerts


class ProviderOpsTests(unittest.TestCase):
    def test_build_provider_ops_view_marks_degraded_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            (runtime_dir / "provider_doctor.json").write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "name": "claude_pro_cli",
                                "provider": "anthropic",
                                "healthy": True,
                                "details": "claude_logged_in:unknown",
                            }
                        ],
                        "api_keys": {},
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "provider_smoke.json").write_text(
                json.dumps(
                    {
                        "smoke": [
                            {
                                "name": "claude_pro_cli",
                                "healthy": False,
                                "details": "smoke_failed:credit balance is too low",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "provider_accounts.json").write_text(
                json.dumps({"subscription_accounts": [], "api_accounts": []}),
                encoding="utf-8",
            )
            (runtime_dir / "model_catalog.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "adapter_name": "claude_pro_cli",
                                "provider": "anthropic",
                                "model": "Claude Sonnet/Opus class",
                                "tier": "senior_cloud",
                                "intelligence_rank": 98,
                                "coding_rank": 97,
                                "reasoning_rank": 99,
                                "trust_rank": 70,
                                "local_allowed_for_team_lead": False,
                                "api_allowed_for_team_lead": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = build_provider_ops_view(runtime_dir)
            self.assertEqual(payload["summary"]["degraded_count"], 1)
            self.assertEqual(payload["summary"]["operational_count"], 0)
            row = next(
                item
                for item in payload["providers"]
                if item["adapter_name"] == "claude_pro_cli"
            )
            self.assertTrue(row["degraded"])
            self.assertFalse(row["operational"])
            self.assertFalse(row["team_lead_eligible"])
            self.assertTrue(
                any("Senior cloud degraded" in alert for alert in payload["alerts"])
            )

    def test_sync_provider_ops_alerts_emits_event_and_mailbox_on_state_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            previous = {
                "providers": [
                    {
                        "adapter_name": "claude_pro_cli",
                        "provider": "anthropic",
                        "tier": "senior_cloud",
                        "operational": True,
                        "degraded": False,
                    }
                ]
            }
            (runtime_dir / "provider_ops.json").write_text(
                json.dumps(previous), encoding="utf-8"
            )
            (runtime_dir / "provider_doctor.json").write_text(
                json.dumps(
                    {
                        "providers": [
                            {
                                "name": "claude_pro_cli",
                                "provider": "anthropic",
                                "healthy": True,
                                "details": "claude_logged_in:unknown",
                            }
                        ],
                        "api_keys": {},
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "provider_smoke.json").write_text(
                json.dumps(
                    {
                        "smoke": [
                            {
                                "name": "claude_pro_cli",
                                "healthy": False,
                                "details": "smoke_failed:credit balance is too low",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime_dir / "provider_accounts.json").write_text(
                json.dumps({"subscription_accounts": [], "api_accounts": []}),
                encoding="utf-8",
            )
            (runtime_dir / "model_catalog.json").write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "adapter_name": "claude_pro_cli",
                                "provider": "anthropic",
                                "model": "Claude Sonnet/Opus class",
                                "tier": "senior_cloud",
                                "intelligence_rank": 98,
                                "coding_rank": 97,
                                "reasoning_rank": 99,
                                "trust_rank": 70,
                                "local_allowed_for_team_lead": False,
                                "api_allowed_for_team_lead": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = sync_provider_ops_alerts(runtime_dir)
            self.assertTrue(result["emitted"])
            self.assertEqual(len(result["changes"]), 1)
            self.assertEqual(result["changes"][0]["from_state"], "operational")
            self.assertEqual(result["changes"][0]["to_state"], "degraded")

            mailbox_path = runtime_dir / "mailbox.jsonl"
            self.assertTrue(mailbox_path.exists())
            mailbox_blob = mailbox_path.read_text(encoding="utf-8")
            self.assertIn("Provider ops alert", mailbox_blob)

            events_path = runtime_dir / "events.jsonl"
            self.assertTrue(events_path.exists())
            events_blob = events_path.read_text(encoding="utf-8")
            self.assertIn("provider_ops_alert", events_blob)


if __name__ == "__main__":
    unittest.main()
