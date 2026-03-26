import os
import unittest
from unittest.mock import patch

from aiteam.adapters import ApiAdapter, SubscriptionAdapter


class ProviderAvailabilityTests(unittest.TestCase):
    def test_subscription_unavailable_when_limit_reached(self) -> None:
        adapter = SubscriptionAdapter(name="openai_pro", provider="openai", model="gpt-5.3-codex")
        with patch.dict(os.environ, {"AITEAM_SUBSCRIPTION_OPENAI_LIMIT_REACHED": "1"}, clear=False):
            self.assertFalse(adapter.available())

    def test_subscription_unavailable_when_degraded(self) -> None:
        adapter = SubscriptionAdapter(name="gemini_pro", provider="google", model="gemini-3.1-pro")
        with patch.dict(os.environ, {"AITEAM_PROVIDER_GOOGLE_DEGRADED": "true"}, clear=False):
            self.assertFalse(adapter.available())

    def test_api_unavailable_when_provider_degraded(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict(os.environ, {"AITEAM_PROVIDER_OPENAI_DEGRADED": "1"}, clear=False):
            self.assertFalse(adapter.available())

    def test_api_can_require_keys_via_global_flag(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict(os.environ, {"AITEAM_REQUIRE_API_KEYS": "1"}, clear=False):
            self.assertFalse(adapter.available())


if __name__ == "__main__":
    unittest.main()
