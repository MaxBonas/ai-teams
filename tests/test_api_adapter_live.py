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


class GeminiConversationalTests(unittest.TestCase):
    """Verifica que _invoke_google usa estructura contents nativa de Gemini."""

    def _make_gemini_response(self, text: str = "respuesta gemini") -> dict:
        return {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": text}]}}
            ],
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
        self.assertEqual(contents[1]["role"], "model")   # "assistant" → "model"
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
                __import__("aiteam.agent_session", fromlist=["ConversationTurn"]).ConversationTurn(
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
                name="openai_pro", provider="openai", model="gpt-pro",
                capabilities={"coding", "reasoning", "analysis", "review"},
            )
        ]
        router = HybridRouter(adapters=adapters, policy=build_default_router_policy())
        return AITeamOrchestrator(router=router, runtime_dir=runtime_dir, project_root=project_root)

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
                Role.REVIEWER, "review",
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


if __name__ == "__main__":
    unittest.main()
