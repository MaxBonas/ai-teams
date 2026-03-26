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
        # El mock incluye el marcador [SIMULADO | ...] para indicar que es una respuesta simulada
        self.assertIn("SIMULADO", response.content)

    def test_api_adapter_calls_openai_compatible_endpoint_in_live_mode(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        payload = {
            "choices": [{"message": {"content": "real response"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        }
        captured: dict[str, object] = {}

        def _capture_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _MockHttpResponse(payload)

        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_capture_urlopen),
        ):
            response = adapter.invoke("hello")
        self.assertTrue(response.success)
        self.assertEqual(response.content, "real response")
        self.assertEqual(response.input_tokens, 12)
        self.assertEqual(response.output_tokens, 6)
        body = captured.get("body", {})
        self.assertEqual(body.get("messages", [{}])[0].get("content"), "hello")

    def test_api_adapter_uses_messages_history_when_provided(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        payload = {
            "choices": [{"message": {"content": "real response"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6},
        }
        captured: dict[str, object] = {}

        def _capture_urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _MockHttpResponse(payload)

        messages = [
            {"role": "system", "content": "You are a senior engineer"},
            {"role": "user", "content": "Implement auth with JWT"},
        ]
        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_capture_urlopen),
        ):
            response = adapter.invoke("hello", messages=messages)

        self.assertTrue(response.success)
        body = captured.get("body", {})
        self.assertEqual(body.get("messages"), messages)

    def test_live_mode_fails_without_required_key(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict(
            "os.environ",
            {"AITEAM_ENABLE_LIVE_API": "1", "OPENAI_API_KEY": ""},
            clear=False,
        ):
            response = adapter.invoke("hello")
        self.assertFalse(response.success)
        self.assertIn("missing_api_key", str(response.error))


if __name__ == "__main__":
    unittest.main()
