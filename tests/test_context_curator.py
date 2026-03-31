import shutil
import unittest
from pathlib import Path

from aiteam.context_curator import (
    ContextCuratorStore,
    PROJECT_CONTEXT_VERSION,
    estimate_context_compaction_value,
    estimate_context_pressure,
)
from api.utils import _build_project_continuity_context


class ContextCuratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(".tmp_test_context_curator")
        shutil.rmtree(self.workspace, ignore_errors=True)
        self.runtime_dir = self.workspace / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_remember_preplan_persists_project_and_chat_context(self) -> None:
        store = ContextCuratorStore(self.runtime_dir)

        project_ctx, chat_ctx = store.remember_preplan(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-ctx001",
            user_message="Research docs and audit security before fixing browser login",
            surface_hints={
                "surfaces": ["research", "security", "browser"],
                "recommended_delegate_intents": ["delegate_mcp_probe", "delegate_browser_repro"],
            },
            curator_summary="- auth.py es clave\n- hay flujo browser afectado",
            lead_summary="P0 investigar auth y validar browser. Riesgo de seguridad en login.",
            source_task_ids=["CHAT-ctx001::scout_context_curator", "CHAT-ctx001::lead_intake"],
        )

        self.assertEqual(project_ctx["version"], PROJECT_CONTEXT_VERSION)
        self.assertEqual(chat_ctx["version"], PROJECT_CONTEXT_VERSION)
        self.assertTrue((self.runtime_dir / "context" / "projects").exists())
        self.assertTrue((self.runtime_dir / "context" / "chats").exists())
        self.assertTrue(project_ctx["durable_facts"])
        self.assertTrue(chat_ctx["decisions"])
        self.assertIn("delegate:delegate_mcp_probe", [row["text"] for row in chat_ctx["next_actions"]])

        loaded_project = store.load_project_context(str(self.workspace.resolve()))
        loaded_chat = store.load_chat_context("CHAT-ctx001", project_key=str(self.workspace.resolve()))
        self.assertEqual(loaded_project["project_key"], str(self.workspace.resolve()))
        self.assertEqual(loaded_chat["chat_root"], "CHAT-ctx001")
        self.assertTrue(store.build_summary(loaded_project))

    def test_project_continuity_context_includes_context_curator_summary(self) -> None:
        store = ContextCuratorStore(self.runtime_dir)
        store.remember_preplan(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-ctx002",
            user_message="Audit security",
            surface_hints={"surfaces": ["security"], "recommended_delegate_intents": ["delegate_mcp_probe"]},
            curator_summary="- auth.py concentra riesgo\n- usar semgrep skill",
            lead_summary="P0 auditar seguridad",
            source_task_ids=["CHAT-ctx002::lead_intake"],
        )

        continuity = _build_project_continuity_context(self.runtime_dir)

        self.assertIn("Context curator:", continuity)
        self.assertIn("durable_facts:", continuity)
        self.assertIn("auth.py concentra riesgo", continuity)

    def test_remember_invalidation_records_replan_or_force_gate(self) -> None:
        store = ContextCuratorStore(self.runtime_dir)
        store.remember_preplan(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-ctx003",
            user_message="Implementa login",
            surface_hints={"surfaces": ["browser"], "recommended_delegate_intents": ["delegate_browser_repro"]},
            curator_summary="- login flow afectado",
            lead_summary="P0 reparar build",
            source_task_ids=["CHAT-ctx003::lead_intake"],
        )

        project_ctx, chat_ctx = store.remember_invalidation(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-ctx003",
            reason="replan_partial",
            affected_phases=["build"],
            source_task_ids=["CHAT-ctx003::lead_report_build"],
        )

        self.assertTrue(project_ctx["invalidations"])
        self.assertTrue(chat_ctx["invalidations"])
        self.assertIn("replan_partial", chat_ctx["invalidations"][0]["text"])
        self.assertIn("revisar_de_nuevo:build", [row["text"] for row in chat_ctx["open_questions"]])

    def test_estimate_context_pressure_escalates_with_continuation_and_history(self) -> None:
        pressure = estimate_context_pressure(
            continuation_requested=True,
            continuation_snapshot="build:failed, review:pending",
            phase_summary_count=5,
            delegate_batch_count=3,
            specialist_report_count=4,
            invalidation_count=1,
            open_question_count=3,
        )

        self.assertEqual(pressure["level"], "high")
        self.assertTrue(pressure["recommend_context_curator"])
        self.assertIn("continuation_requested", pressure["signals"])
        self.assertIn("delegate_batches_accumulated", pressure["signals"])

    def test_estimate_context_compaction_value_detects_material_savings(self) -> None:
        value = estimate_context_compaction_value(
            phase_outputs={
                "discovery": "A" * 900,
                "build": "B" * 1100,
                "review": "C" * 700,
            },
            project_context_summary="Proyecto compacto",
            chat_context_summary="Chat compacto",
            phase_context_summaries={
                "discovery": "Resumen discovery",
                "build": "Resumen build",
            },
        )

        self.assertEqual(value["level"], "high")
        self.assertTrue(value["priority_boost"])
        self.assertGreater(int(value["estimated_context_tokens_saved"]), 300)
        self.assertIn("context_savings_material", value["signals"])


if __name__ == "__main__":
    unittest.main()
