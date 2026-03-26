import io
import json
import unittest
from unittest.mock import patch

from aiteam.adapters import ApiAdapter


class _MockHttpResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ApiAdapterLiveTests(unittest.TestCase):
    def test_api_adapter_uses_simulation_when_live_mode_disabled(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
            response = adapter.invoke("hello")
        self.assertTrue(response.success)
        self.assertIn("Processed prompt", response.content)

    def test_api_adapter_calls_openai_compatible_endpoint_in_live_mode(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        payload = {
            "choices": [{"message": {"content": "real response"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        }
        with patch.dict(
            "os.environ",
            {
                "AITEAM_ENABLE_LIVE_API": "1",
                "OPENAI_API_KEY": "test-key",
            },
            clear=False,
        ), patch("urllib.request.urlopen", return_value=_MockHttpResponse(payload)):
            response = adapter.invoke("hello")
        self.assertTrue(response.success)
        self.assertEqual(response.content, "real response")
        self.assertEqual(response.input_tokens, 12)
        self.assertEqual(response.output_tokens, 6)

    def test_live_mode_fails_without_required_key(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "1", "OPENAI_API_KEY": ""}, clear=False):
            response = adapter.invoke("hello")
        self.assertFalse(response.success)
        self.assertIn("missing_api_key", str(response.error))


if __name__ == "__main__":
    unittest.main()
