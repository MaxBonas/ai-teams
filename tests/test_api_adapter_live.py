import io
import json
import urllib.error
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
    def test_api_adapter_fails_when_live_mode_disabled(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
            response = adapter.invoke("hello")
        self.assertFalse(response.success)
        self.assertIn("live_api_disabled", str(response.error))

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

    def test_api_adapter_retries_transient_http_error(self) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        payload = {
            "choices": [{"message": {"content": "real response"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        }
        calls = {"count": 0}

        def _flaky_urlopen(request, timeout=0):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "0"},
                    io.BytesIO(b'{"error":"rate_limited"}'),
                )
            return _MockHttpResponse(payload)

        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                    "AITEAM_LIVE_API_RETRY_ATTEMPTS": "1",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_flaky_urlopen),
            patch("time.sleep", return_value=None),
        ):
            response = adapter.invoke("hello")

        self.assertTrue(response.success)
        self.assertEqual(response.content, "real response")
        self.assertEqual(calls["count"], 2)

    def test_api_adapter_fails_on_quota_exhaustion_without_simulated_fallback(
        self,
    ) -> None:
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")

        def _always_limited(request, timeout=0):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"rate_limited"}'),
            )

        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                    "AITEAM_LIVE_API_RETRY_ATTEMPTS": "0",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_always_limited),
        ):
            response = adapter.invoke("hello")

        self.assertFalse(response.success)
        self.assertIn("http_error:429", str(response.error))


class GeminiConversationalTests(unittest.TestCase):
    """Verifica que _invoke_google usa estructura contents nativa de Gemini."""

    def _make_gemini_response(self, text: str = "respuesta gemini") -> dict:
        return {
            "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
        }

    def _capture_gemini_call(self, response_text: str = "ok"):
        """Devuelve (captured_dict, urlopen_mock)."""
        captured: dict = {}
        gemini_response = self._make_gemini_response(response_text)

        def _urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["url"] = request.full_url
            return _MockHttpResponse(gemini_response)

        return captured, _urlopen

    def test_gemini_single_prompt_sends_user_turn(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="gemini_pro", provider="google", model="gemini-2.0-flash"
        )
        captured, urlopen_mock = self._capture_gemini_call()
        env = {"AITEAM_ENABLE_LIVE_API": "1", "GOOGLE_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=urlopen_mock),
        ):
            response = adapter.invoke("hola gemini")

        self.assertTrue(response.success)
        body = captured.get("body", {})
        contents = body.get("contents", [])
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[0]["parts"][0]["text"], "hola gemini")

    def test_gemini_multi_turn_preserves_history(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="gemini_pro", provider="google", model="gemini-2.0-flash"
        )
        messages = [
            {"role": "user", "content": "primera pregunta"},
            {"role": "assistant", "content": "primera respuesta"},
            {"role": "user", "content": "segunda pregunta"},
        ]
        captured, urlopen_mock = self._capture_gemini_call()
        env = {"AITEAM_ENABLE_LIVE_API": "1", "GOOGLE_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=urlopen_mock),
        ):
            response = adapter.invoke("segunda pregunta", messages=messages)

        self.assertTrue(response.success)
        contents = captured["body"]["contents"]
        # Debe tener 3 turnos con roles correctos
        self.assertEqual(len(contents), 3)
        self.assertEqual(contents[0]["role"], "user")
        self.assertEqual(contents[1]["role"], "model")  # "assistant" → "model"
        self.assertEqual(contents[2]["role"], "user")
        self.assertEqual(contents[1]["parts"][0]["text"], "primera respuesta")

    def test_gemini_system_message_goes_to_system_instruction(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="gemini_pro", provider="google", model="gemini-2.0-flash"
        )
        messages = [
            {"role": "system", "content": "Eres un experto en Python."},
            {"role": "user", "content": "Como funciona asyncio?"},
        ]
        captured, urlopen_mock = self._capture_gemini_call()
        env = {"AITEAM_ENABLE_LIVE_API": "1", "GOOGLE_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=urlopen_mock),
        ):
            adapter.invoke("Como funciona asyncio?", messages=messages)

        body = captured["body"]
        # system → system_instruction, no en contents
        contents = body["contents"]
        roles_in_contents = [c["role"] for c in contents]
        self.assertNotIn("system", roles_in_contents)
        self.assertIn("system_instruction", body)
        self.assertIn("Eres un experto", body["system_instruction"]["parts"][0]["text"])

    def test_gemini_consecutive_same_role_turns_are_merged(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="gemini_pro", provider="google", model="gemini-2.0-flash"
        )
        # Dos turnos user consecutivos — Gemini los rechaza, deben fusionarse
        messages = [
            {"role": "user", "content": "parte uno"},
            {"role": "user", "content": "parte dos"},
            {"role": "assistant", "content": "respuesta"},
        ]
        captured, urlopen_mock = self._capture_gemini_call()
        env = {"AITEAM_ENABLE_LIVE_API": "1", "GOOGLE_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=urlopen_mock),
        ):
            adapter.invoke("extra", messages=messages)

        contents = captured["body"]["contents"]
        # user+user → 1 user; model → 1 model = 2 total
        self.assertEqual(len(contents), 2)
        self.assertEqual(contents[0]["role"], "user")
        self.assertIn("parte uno", contents[0]["parts"][0]["text"])
        self.assertIn("parte dos", contents[0]["parts"][0]["text"])
        self.assertEqual(contents[1]["role"], "model")

    def test_gemini_history_starting_with_model_gets_user_prefix(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="gemini_pro", provider="google", model="gemini-2.0-flash"
        )
        messages = [
            {"role": "assistant", "content": "respuesta huerfana"},
            {"role": "user", "content": "continuacion"},
        ]
        captured, urlopen_mock = self._capture_gemini_call()
        env = {"AITEAM_ENABLE_LIVE_API": "1", "GOOGLE_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=urlopen_mock),
        ):
            adapter.invoke("continuacion", messages=messages)

        contents = captured["body"]["contents"]
        # Debe empezar con user (insertado automaticamente)
        self.assertEqual(contents[0]["role"], "user")

    def test_subscription_adapter_retries_transient_http_error(self) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="openai_pro", provider="openai", model="gpt-4.1"
        )
        payload = {
            "choices": [{"message": {"content": "subscription response"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        }
        calls = {"count": 0}

        def _flaky_urlopen(request, timeout=0):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "0"},
                    io.BytesIO(b'{"error":"rate_limited"}'),
                )
            return _MockHttpResponse(payload)

        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                    "AITEAM_LIVE_API_RETRY_ATTEMPTS": "1",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_flaky_urlopen),
            patch("time.sleep", return_value=None),
        ):
            response = adapter.invoke("hello")

        self.assertTrue(response.success)
        self.assertEqual(response.content, "subscription response")
        self.assertEqual(calls["count"], 2)

    def test_subscription_adapter_fails_on_quota_exhaustion_without_simulated_fallback(
        self,
    ) -> None:
        from aiteam.adapters import SubscriptionAdapter

        adapter = SubscriptionAdapter(
            name="openai_pro", provider="openai", model="gpt-4.1"
        )

        def _always_limited(request, timeout=0):
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "0"},
                io.BytesIO(b'{"error":"rate_limited"}'),
            )

        with (
            patch.dict(
                "os.environ",
                {
                    "AITEAM_ENABLE_LIVE_API": "1",
                    "OPENAI_API_KEY": "test-key",
                    "AITEAM_LIVE_API_RETRY_ATTEMPTS": "0",
                },
                clear=False,
            ),
            patch("urllib.request.urlopen", side_effect=_always_limited),
        ):
            response = adapter.invoke("hello")

        self.assertFalse(response.success)
        self.assertIn("http_error:429", str(response.error))


class ThreadCompactionTests(unittest.TestCase):
    """Verifica compaction por caracteres y por numero de turnos."""

    def _make_thread(self) -> object:
        from aiteam.agent_session import ConversationThread

        import datetime as _dt

        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return ConversationThread(
            thread_id="test-01",
            agent_id="agent-test",
            project_key="proj-test",
            created_at=now,
            last_updated=now,
        )

    def test_compaction_triggers_on_turn_count(self) -> None:
        thread = self._make_thread()
        for i in range(12):
            thread.append_turn("user", f"mensaje corto {i}")
        # Despues de compaction: summary + keep_recent (8) = 9
        self.assertLessEqual(len(thread.turns), 9)
        self.assertEqual(thread.turns[0].source, "summary")

    def test_compaction_triggers_on_chars_overflow(self) -> None:
        thread = self._make_thread()
        # Añadir 5 turnos de 20k chars cada uno → 100k total, supera 60k
        big_content = "x" * 20_000
        for i in range(5):
            # Bypass deduplication alternando roles
            role = "user" if i % 2 == 0 else "assistant"
            thread.turns.append(
                __import__(
                    "aiteam.agent_session", fromlist=["ConversationTurn"]
                ).ConversationTurn(
                    ts="2026-01-01T00:00:00+00:00",
                    role=role,
                    content=big_content + f" {i}",
                    source="task",
                )
            )
        # Forzar compaction manualmente (normalmente se llama en append_turn)
        thread._compact_turns(max_turns=20, keep_recent=8, max_chars=60_000)
        total_chars = sum(len(t.content) for t in thread.turns)
        # Los turnos retenidos deben estar bajo el 70% del limite (42k)
        self.assertLess(total_chars, 60_000)
        # El primer turno debe ser un summary
        self.assertEqual(thread.turns[0].source, "summary")
        self.assertIn("chars", thread.turns[0].content)

    def test_no_compaction_when_under_limits(self) -> None:
        thread = self._make_thread()
        for i in range(5):
            role = "user" if i % 2 == 0 else "assistant"
            thread.append_turn(role, f"mensaje {i}")
        count_before = len(thread.turns)
        thread._compact_turns(max_turns=9, keep_recent=8, max_chars=60_000)
        self.assertEqual(len(thread.turns), count_before)


class EvidenceGateQualityTests(unittest.TestCase):
    """Verifica validacion de calidad de output en modo live."""

    def _make_orchestrator(self, tmp: str):
        from aiteam.adapters import SubscriptionAdapter
        from aiteam.config import build_default_router_policy
        from aiteam.orchestrator import AITeamOrchestrator
        from aiteam.router import HybridRouter
        from pathlib import Path

        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        project_root.mkdir(parents=True, exist_ok=True)
        adapters = [
            SubscriptionAdapter(
                name="openai_pro",
                provider="openai",
                model="gpt-pro",
                capabilities={"coding", "reasoning", "analysis", "review"},
            )
        ]
        router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
        return AITeamOrchestrator(
            router=router, runtime_dir=runtime_dir, project_root=project_root
        )

    def test_assess_output_quality_rejects_trivial_engineer_output(self) -> None:
        import tempfile
        from aiteam.types import Role
        from aiteam.orchestrator import AITeamOrchestrator

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            ok, reason = orch._assess_output_quality(
                "He completado la tarea.", Role.ENGINEER, "build"
            )
        self.assertFalse(ok)
        self.assertIn("trivial", reason)

    def test_assess_output_quality_accepts_substantial_engineer_output(self) -> None:
        import tempfile
        from aiteam.types import Role

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            big_output = "def foo():\n    pass\n" * 15  # >200 chars
            ok, reason = orch._assess_output_quality(big_output, Role.ENGINEER, "build")
        self.assertTrue(ok)
        self.assertIn("substantial", reason)

    def test_assess_output_quality_reviewer_requires_observations(self) -> None:
        import tempfile
        from aiteam.types import Role

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            ok, _ = orch._assess_output_quality(
                "El codigo esta bien.", Role.REVIEWER, "review"
            )
        self.assertFalse(ok)

    def test_assess_output_quality_reviewer_passes_with_issues(self) -> None:
        import tempfile
        from aiteam.types import Role

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            ok, reason = orch._assess_output_quality(
                "- Issue: falta manejo de error en linea 42\n- Sugerencia: extraer logica a funcion auxiliar",
                Role.REVIEWER,
                "review",
            )
        self.assertTrue(ok)
        self.assertIn("observaciones", reason)

    def test_assess_output_quality_qa_requires_test_signals(self) -> None:
        import tempfile
        from aiteam.types import Role

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            ok, _ = orch._assess_output_quality(
                "He revisado el codigo y parece correcto.", Role.QA, "qa"
            )
        self.assertFalse(ok)

    def test_assess_output_quality_qa_passes_with_test_results(self) -> None:
        import tempfile
        from aiteam.types import Role

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            ok, reason = orch._assess_output_quality(
                "Tests: 12 passed, 0 failed. Coverage: 87%.", Role.QA, "qa"
            )
        self.assertTrue(ok)
        self.assertIn("resultados", reason)

    def test_verify_task_evidence_uses_quality_check_in_live_mode(self) -> None:
        """En live mode sin git diff, un output trivial debe fallar el gate."""
        import tempfile
        from aiteam.types import Role, WorkTask
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            orch = self._make_orchestrator(tmp)
            task = WorkTask(
                task_id="QUALITY-TEST-1",
                title="Build feature",
                description="Implement something",
                role=Role.ENGINEER,
                metadata={"_last_agent_output": "He completado la tarea."},
            )
            workspace = Path(tmp) / "workspace"
            with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "1"}, clear=False):
                has_evidence, reason = orch._verify_task_evidence(task, workspace)
        # Output trivial en live mode → no debe pasar
        self.assertFalse(has_evidence)


class NativeFunctionCallingTests(unittest.TestCase):
    """Verifica function calling nativo en OpenAI y Anthropic."""

    def _openai_tool_response(
        self, tool_name: str = "read_file", tool_id: str = "call_abc"
    ) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps({"command": "src/main.py"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }

    def _anthropic_tool_response(
        self, tool_name: str = "search", tool_id: str = "toolu_abc"
    ) -> dict:
        return {
            "content": [
                {"type": "text", "text": "Voy a buscar informacion."},
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": {"command": "query"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 15, "output_tokens": 8},
        }

    def test_openai_adapter_returns_tool_calls_when_model_requests_tool(self) -> None:
        from aiteam.adapters import ApiAdapter
        from aiteam.adapters.base import NativeToolDefinition

        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        tools = [
            NativeToolDefinition(
                name="read_file",
                description="Lee un archivo del proyecto",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            )
        ]
        captured: dict = {}

        def _urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _MockHttpResponse(self._openai_tool_response())

        env = {"AITEAM_ENABLE_LIVE_API": "1", "OPENAI_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=_urlopen),
        ):
            response = adapter.invoke("Lee el archivo src/main.py", tools=tools)

        self.assertTrue(response.success)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "read_file")
        self.assertEqual(response.tool_calls[0].arguments, {"command": "src/main.py"})
        self.assertEqual(response.tool_calls[0].id, "call_abc")
        # tools se envian en el body con formato correcto
        body_tools = captured["body"].get("tools", [])
        self.assertEqual(len(body_tools), 1)
        self.assertEqual(body_tools[0]["type"], "function")
        self.assertEqual(body_tools[0]["function"]["name"], "read_file")
        self.assertEqual(captured["body"].get("tool_choice"), "auto")

    def test_anthropic_adapter_returns_tool_calls_on_tool_use_response(self) -> None:
        from aiteam.adapters import SubscriptionAdapter
        from aiteam.adapters.base import NativeToolDefinition

        adapter = SubscriptionAdapter(
            name="claude_pro", provider="anthropic", model="claude-3-5-sonnet-20241022"
        )
        tools = [
            NativeToolDefinition(
                name="search",
                description="Busca en internet",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            )
        ]
        captured: dict = {}

        def _urlopen(request, timeout=0):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _MockHttpResponse(self._anthropic_tool_response())

        env = {"AITEAM_ENABLE_LIVE_API": "1", "ANTHROPIC_API_KEY": "test-key"}
        with (
            patch.dict("os.environ", env, clear=False),
            patch("urllib.request.urlopen", side_effect=_urlopen),
        ):
            response = adapter.invoke("Busca informacion sobre asyncio", tools=tools)

        self.assertTrue(response.success)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "search")
        self.assertEqual(response.tool_calls[0].arguments, {"command": "query"})
        # tools en body tienen formato Anthropic (input_schema, no parameters)
        body_tools = captured["body"].get("tools", [])
        self.assertEqual(len(body_tools), 1)
        self.assertIn("input_schema", body_tools[0])
        self.assertNotIn("parameters", body_tools[0])
        # El texto previo al tool_use tambien se captura
        self.assertIn("Voy a buscar", response.content)

    def test_adapter_without_tools_param_gets_no_tools_injected(self) -> None:
        """Adapters que no declaran tools= no reciben tools (backward compat)."""
        from aiteam.adapters.base import ModelAdapter, NativeToolDefinition
        from aiteam.types import AdapterResponse, ChannelType

        class LegacyAdapter(ModelAdapter):
            def available(self):
                return True

            def invoke(self, prompt, messages=None):  # sin tools
                return AdapterResponse(success=True, content="legacy ok", latency_ms=0)

        legacy = LegacyAdapter(
            name="legacy", provider="test", model="m", channel=ChannelType.API
        )
        tools = [NativeToolDefinition("t", "desc", {})]
        # No debe fallar aunque tools no este en la firma
        import inspect

        params = inspect.signature(legacy.invoke).parameters
        self.assertNotIn("tools", params)
        result = legacy.invoke("hola")
        self.assertTrue(result.success)

    def test_router_passes_tools_to_adapter_when_supported(self) -> None:
        """El router pasa tools solo si el adapter declara el parametro."""
        from aiteam.adapters import ApiAdapter
        from aiteam.adapters.base import NativeToolDefinition
        from aiteam.config import build_default_router_policy
        from aiteam.router import HybridRouter
        from aiteam.types import Role, Complexity, Criticality, RoutingRequest

        received_tools = []

        class ToolAwareAdapter(ApiAdapter):
            def invoke(self, prompt, messages=None, tools=None):
                received_tools.extend(tools or [])
                return __import__(
                    "aiteam.types", fromlist=["AdapterResponse"]
                ).AdapterResponse(success=True, content="ok con tools", latency_ms=0)

        adapter = ToolAwareAdapter(
            name="openai_api",
            provider="openai",
            model="gpt-4.1-mini",
            capabilities={"coding"},
        )
        router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())
        tools = [NativeToolDefinition("read_file", "Lee archivo", {})]

        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
        )
        decision = router.route_and_invoke(request, "hola", tools=tools)
        self.assertTrue(decision.success)
        self.assertEqual(len(received_tools), 1)
        self.assertEqual(received_tools[0].name, "read_file")


class StreamingInvokeTests(unittest.TestCase):
    """Tests para invoke_stream en adapters."""

    def test_base_adapter_stream_returns_no_chunks_when_invoke_fails(self):
        """invoke_stream del base adapter no emite chunks si invoke() falla."""
        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")
        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
            chunks = list(adapter.invoke_stream("hello world"))
        self.assertEqual(chunks, [])

    def test_invoke_stream_openai_yields_chunks(self):
        """_stream_openai_compatible parsea SSE y hace yield de chunks."""
        from unittest.mock import MagicMock

        adapter = ApiAdapter(name="openai_api", provider="openai", model="gpt-4.1-mini")

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.__iter__ = lambda s: iter(
            [
                b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n',
                b"data: [DONE]\n",
            ]
        )

        with (
            patch.dict(
                "os.environ",
                {"AITEAM_ENABLE_LIVE_API": "1", "OPENAI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("urllib.request.urlopen", return_value=mock_response),
        ):
            chunks = list(adapter.invoke_stream("test prompt"))

        self.assertEqual(chunks, ["Hello"])

    def test_invoke_stream_anthropic_yields_chunks(self):
        """_stream_anthropic parsea SSE de Anthropic y hace yield de chunks."""
        from unittest.mock import MagicMock

        adapter = ApiAdapter(
            name="anthropic_api",
            provider="anthropic",
            model="claude-3-5-haiku-20241022",
        )

        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.__iter__ = lambda s: iter(
            [
                b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n',
                b'data: {"type":"message_stop"}\n',
            ]
        )

        with (
            patch.dict(
                "os.environ",
                {"AITEAM_ENABLE_LIVE_API": "1", "ANTHROPIC_API_KEY": "test-key"},
                clear=False,
            ),
            patch("urllib.request.urlopen", return_value=mock_response),
        ):
            chunks = list(adapter.invoke_stream("test prompt"))

        self.assertEqual(chunks, ["Hi"])

    def test_router_on_chunk_callback_receives_chunks(self):
        """route_and_invoke con on_chunk llama callback por chunk."""
        from aiteam.config import build_default_router_policy
        from aiteam.router import HybridRouter
        from aiteam.types import Complexity, Criticality, Role, RoutingRequest

        class _StreamingAdapter(ApiAdapter):
            def available(self):
                return True

            def invoke_stream(self, prompt, messages=None):
                yield "foo"
                yield "bar"

        adapter = _StreamingAdapter(
            name="openai_api",
            provider="openai",
            model="gpt-4.1-mini",
            capabilities={"coding"},
        )
        router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())

        received_chunks: list[str] = []
        request = RoutingRequest(
            role=Role.ENGINEER,
            complexity=Complexity.MEDIUM,
            criticality=Criticality.MEDIUM,
        )
        decision = router.route_and_invoke(
            request,
            "test prompt",
            on_chunk=lambda c: received_chunks.append(c),
        )

        self.assertTrue(decision.success)
        self.assertEqual(received_chunks, ["foo", "bar"])
        self.assertEqual(decision.response.content, "foobar")


if __name__ == "__main__":
    unittest.main()
