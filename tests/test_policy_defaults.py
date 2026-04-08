import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiteam.cli import build_default_orchestrator
from aiteam.config import build_default_router_policy
from aiteam.types import ChannelType


class PolicyDefaultsTests(unittest.TestCase):
    def test_default_policy_prefers_three_subscriptions_then_openai_and_groq_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = build_default_orchestrator(Path(tmp))
            policy = orchestrator.router.policy
            self.assertTrue(policy.pro_first)
            self.assertEqual(policy.max_subscription_attempts, 3)
            self.assertEqual(policy.preferred_subscription_providers[:3], ["openai", "google", "anthropic"])
            self.assertEqual(policy.preferred_api_providers, ["openai", "groq"])
            self.assertEqual(policy.role_provider_preferences["team_lead"], ["openai", "google", "anthropic"])
            self.assertEqual(policy.role_provider_preferences["engineer"], ["openai", "google", "groq"])
            self.assertEqual(policy.role_provider_preferences["reviewer"], ["openai", "google", "groq"])
            self.assertEqual(policy.role_provider_preferences["qa"], ["openai", "google", "groq"])

    def test_default_adapter_pool_includes_openai_and_groq_api_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = build_default_orchestrator(Path(tmp))
            adapters = orchestrator.router.adapters

            subscription_providers = {
                adapter.provider for adapter in adapters if adapter.channel == ChannelType.SUBSCRIPTION
            }
            self.assertTrue({"openai", "anthropic", "google"}.issubset(subscription_providers))

            api_adapters = [adapter for adapter in adapters if adapter.channel == ChannelType.API]
            self.assertTrue(api_adapters)
            api_providers = {adapter.provider for adapter in api_adapters}
            self.assertTrue({"openai", "groq"}.issubset(api_providers))
            api_models = {adapter.model for adapter in api_adapters}
            self.assertIn("gpt-4.1-mini", api_models)
            self.assertIn("gpt-4o-mini", api_models)
            self.assertIn("llama-3.3-70b-versatile", api_models)

    def test_default_anthropic_subscription_adapters_are_team_lead_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orchestrator = build_default_orchestrator(Path(tmp))
            adapters = orchestrator.router.adapters
            anthropic_subscriptions = [
                adapter
                for adapter in adapters
                if adapter.channel == ChannelType.SUBSCRIPTION and adapter.provider == "anthropic"
            ]
            self.assertTrue(anthropic_subscriptions)
            # The pool has both TL-only adapters (claude_pro) and worker adapters (claude_haiku).
            # Verify at least one is TL-exclusive and at least one is a worker (no role restriction).
            tl_only = [a for a in anthropic_subscriptions if a.role_targets == {"team_lead"}]
            worker = [a for a in anthropic_subscriptions if not a.role_targets]
            self.assertTrue(tl_only, "Expected at least one Anthropic TL-only subscription adapter")
            self.assertTrue(worker, "Expected at least one Anthropic worker subscription adapter")
            # No anthropic subscription adapter should have mixed or other-role targets
            for adapter in anthropic_subscriptions:
                self.assertIn(
                    adapter.role_targets,
                    ({"team_lead"}, set()),
                    f"Adapter {adapter.name} has unexpected role_targets: {adapter.role_targets}",
                )

    def test_policy_reads_strict_role_env_toggles(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "AITEAM_ENFORCE_ROLE_MODEL_PREFERENCES": "1",
                "AITEAM_STRICT_ROLE_POLICY_ENVS": "stage,prod",
            },
            clear=False,
        ):
            policy = build_default_router_policy()
            self.assertTrue(policy.enforce_role_model_preferences)
            self.assertEqual(policy.strict_role_policy_environments, ["stage", "prod"])


if __name__ == "__main__":
    unittest.main()
