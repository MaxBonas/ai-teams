import shutil
import unittest
from pathlib import Path

from aiteam.context_curator import (
    ContextCuratorStore,
    PROJECT_CONTEXT_VERSION,
    estimate_context_compaction_value,
    estimate_context_pressure,
)
from api.utils import (
    _build_project_continuity_context,
    _load_chat_context_curator_insights,
    PROJECT_ROOT,
    resolve_runtime_dir,
)


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

    def test_remember_preplan_continuation_deduplicates_existing_facts(self) -> None:
        """Llamar dos veces con los mismos datos no debe doblar los items en durable_facts."""
        store = ContextCuratorStore(self.runtime_dir)
        kwargs = dict(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-dedup01",
            user_message="Implementar login seguro",
            surface_hints={"surfaces": ["security"], "recommended_delegate_intents": ["delegate_mcp_probe"]},
            curator_summary="- auth.py es el punto critico\n- semgrep encuentra CVEs",
            lead_summary="P0 seguridad en auth",
            source_task_ids=["CHAT-dedup01::lead_intake"],
        )
        store.remember_preplan(**kwargs)
        first_ctx = store.load_chat_context("CHAT-dedup01", project_key=str(self.workspace.resolve()))
        first_fact_count = len(first_ctx["durable_facts"])

        # Segunda llamada con los mismos datos (simulando continuation que reprocesa el mismo estado)
        store.remember_preplan(**kwargs)
        second_ctx = store.load_chat_context("CHAT-dedup01", project_key=str(self.workspace.resolve()))
        second_fact_count = len(second_ctx["durable_facts"])

        self.assertEqual(
            first_fact_count,
            second_fact_count,
            "Llamadas repetidas con mismos datos no deben duplicar durable_facts",
        )
        self.assertGreater(first_fact_count, 0)

    def test_remember_phase_summary_accumulates_long_run_correctly(self) -> None:
        """Una run larga con 4 fases debe producir un project_context_v1 con working_set no vacío
        y un build_summary() legible."""
        store = ContextCuratorStore(self.runtime_dir)
        project_key = str(self.workspace.resolve())
        chat_root = "CHAT-longrun01"

        # Simular cierre de 4 fases con outputs representativos
        phases = [
            ("discovery", "Repositorio auditado. auth.py es clave. 3 endpoints expuestos."),
            ("build", "Patch de seguridad aplicado en auth.py. Tests pasan."),
            ("review", "Revisión aprobada. Sin regresiones detectadas."),
            ("qa", "Suite QA completa. Cobertura 94%. 0 fallos."),
        ]
        for phase, output in phases:
            store.remember_phase_summary(
                project_key=project_key,
                chat_root=chat_root,
                phase=phase,
                output=output,
                source_task_ids=[f"{chat_root}::{phase}"],
            )

        final_ctx = store.load_project_context(project_key)

        # El contexto debe tener entradas en al menos durable_facts y working_set
        total_items = (
            len(final_ctx["durable_facts"])
            + len(final_ctx["working_set"])
            + len(final_ctx["decisions"])
        )
        self.assertGreater(total_items, 0, "project_context_v1 debe tener items tras 4 fases")

        # build_summary debe producir texto legible (no vacío)
        summary = store.build_summary(final_ctx)
        self.assertTrue(summary, "build_summary no debe ser vacío tras una run de 4 fases")
        self.assertIn("discovery", summary.lower())

    def test_curated_context_preferred_over_raw_in_continuity_output(self) -> None:
        """_build_project_continuity_context debe anteponer el bloque curator sobre historia cruda."""
        store = ContextCuratorStore(self.runtime_dir)
        store.remember_preplan(
            project_key=str(self.workspace.resolve()),
            chat_root="CHAT-priority01",
            user_message="Optimizar rendimiento de la API",
            surface_hints={"surfaces": ["research"], "recommended_delegate_intents": ["delegate_lsp"]},
            curator_summary="- endpoint /api/data es el cuello de botella\n- N+1 query en ORM detectado",
            lead_summary="P1 rendimiento: resolver N+1 antes del release",
            source_task_ids=["CHAT-priority01::lead_intake"],
        )

        continuity_text = _build_project_continuity_context(self.runtime_dir)

        # El bloque curado debe aparecer en el output
        self.assertIn("Context curator:", continuity_text)
        self.assertIn("durable_facts:", continuity_text)

        # El contenido curado debe aparecer antes que cualquier raw history marker
        curator_pos = continuity_text.find("Context curator:")
        self.assertGreater(curator_pos, -1, "Bloque 'Context curator:' debe estar presente")

        # Verificar que el contenido semántico real está presente
        self.assertIn("N+1", continuity_text)

    def test_context_curator_isolates_by_project_root(self) -> None:
        store = ContextCuratorStore(self.runtime_dir)
        project_a = str((self.workspace / "project-a").resolve())
        project_b = str((self.workspace / "project-b").resolve())

        store.remember_preplan(
            project_key=project_a,
            chat_root="CHAT-shared-root",
            user_message="Proyecto A: audita auth",
            surface_hints={"surfaces": ["security"]},
            curator_summary="- auth_a.py contiene el riesgo principal",
            lead_summary="P0 revisar auth_a",
            source_task_ids=["CHAT-shared-root::lead_intake"],
        )
        store.remember_preplan(
            project_key=project_b,
            chat_root="CHAT-shared-root",
            user_message="Proyecto B: audita billing",
            surface_hints={"surfaces": ["research"]},
            curator_summary="- billing_b.py concentra el problema",
            lead_summary="P0 revisar billing_b",
            source_task_ids=["CHAT-shared-root::lead_intake"],
        )

        chat_a = store.load_chat_context("CHAT-shared-root", project_key=project_a)
        chat_b = store.load_chat_context("CHAT-shared-root", project_key=project_b)

        self.assertEqual(chat_a["project_key"], project_a)
        self.assertEqual(chat_b["project_key"], project_b)
        self.assertIn("auth_a.py", store.build_summary(chat_a))
        self.assertNotIn("billing_b.py", store.build_summary(chat_a))
        self.assertIn("billing_b.py", store.build_summary(chat_b))
        self.assertNotIn("auth_a.py", store.build_summary(chat_b))

    def test_chat_context_insights_survive_external_runtime_migration(self) -> None:
        store = ContextCuratorStore(self.runtime_dir)
        project_key = str(self.workspace.resolve())

        project_ctx = store.load_project_context(project_key)
        project_ctx["durable_facts"] = [{"text": "login flow auditado", "confidence": 0.7}]
        store._write_project_context(project_key, project_ctx)

        chat_ctx = store.load_chat_context("CHAT-econ01", project_key=project_key)
        chat_ctx["working_set"] = [{"text": "build: revisar auth selector", "confidence": 0.7}]
        chat_ctx["invalidations"] = [{"text": "replan_partial", "confidence": 0.8}]
        store._write_chat_context("CHAT-econ01", chat_ctx)

        migrated_runtime = resolve_runtime_dir(self.workspace, PROJECT_ROOT)
        insights = _load_chat_context_curator_insights(migrated_runtime, "CHAT-econ01")
        summary = insights.get("context_curator_summary", {}) or {}

        self.assertEqual(int(summary.get("invalidation_count", 0)), 1)
        self.assertEqual(
            int((summary.get("chat_layer_counts", {}) or {}).get("working_set", 0)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
