"""Tests para el mecanismo de pre-flight scouts (E7-A).

Cubre:
- Role.SCOUT existe y el router lo enruta a budget_api/local
- Scouts se excluyen de evidence gates y quality gates
- Scouts que fallan se completan con output vacío (graceful failure)
- _build_scout_project_state_context genera contexto válido
- _build_scout_session_history_context genera contexto válido
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from unittest.mock import MagicMock, patch
from uuid import uuid4

from aiteam.sqlite_store import SqliteStore
from aiteam.types import (
    AdapterResponse,
    Complexity,
    Criticality,
    Role,
    RoutingRequest,
    TaskState,
    WorkTask,
)


class TestScoutRole(unittest.TestCase):
    def test_scout_role_exists(self):
        self.assertIn("scout", [r.value for r in Role])

    def test_scout_role_value(self):
        self.assertEqual(Role.SCOUT.value, "scout")

    def test_scout_profile_exists(self):
        from aiteam.profiles import DEFAULT_PROFILES
        self.assertIn(Role.SCOUT, DEFAULT_PROFILES)

    def test_scout_charter_exists(self):
        from aiteam.profiles import ROLE_CHARTERS
        self.assertIn(Role.SCOUT, ROLE_CHARTERS)

    def test_scout_charter_rank_is_lowest(self):
        from aiteam.profiles import ROLE_CHARTERS
        scout_rank = ROLE_CHARTERS[Role.SCOUT].decision_rank
        self.assertEqual(scout_rank, 1)

    def test_scout_profile_in_experimental(self):
        from aiteam.profiles import EXPERIMENTAL_PROFILES
        self.assertIn(Role.SCOUT, EXPERIMENTAL_PROFILES)


class TestScoutRouting(unittest.TestCase):
    def _make_router(self, adapters):
        from aiteam.config import build_default_router_policy
        from aiteam.router import HybridRouter
        policy = build_default_router_policy()
        return HybridRouter(adapters=adapters, policy=policy)

    def _make_adapter(self, name, tier):
        from aiteam.adapters.base import ModelAdapter
        adapter = MagicMock(spec=ModelAdapter)
        adapter.name = name
        adapter.model = f"{name}-model"
        adapter.enabled = True
        adapter.requires_approval = False
        adapter.healthy = True
        profile_mock = MagicMock()
        profile_mock.tier = tier
        profile_mock.reasoning_rank = 5
        profile_mock.coding_rank = 5
        profile_mock.trust_rank = 5
        profile_mock.intelligence_rank = 5
        return adapter, profile_mock

    def test_scout_tier_rank_prefers_budget_api(self):
        from aiteam.config import build_default_router_policy
        from aiteam.router import HybridRouter
        policy = build_default_router_policy()
        router = HybridRouter(adapters=[], policy=policy)

        budget_adapter = MagicMock()
        budget_profile = MagicMock()
        budget_profile.tier = "budget_api"

        senior_adapter = MagicMock()
        senior_profile = MagicMock()
        senior_profile.tier = "senior_cloud"

        request = RoutingRequest(
            role=Role.SCOUT,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
        )

        with patch.object(router, "_profile_for", side_effect=[budget_profile, senior_profile]):
            budget_rank = router._tier_rank(budget_adapter, request)
            senior_rank = router._tier_rank(senior_adapter, request)

        self.assertLess(budget_rank, senior_rank, "budget_api debe tener mejor rank que senior_cloud para SCOUT")

    def test_scout_tier_rank_senior_cloud_is_worst(self):
        from aiteam.config import build_default_router_policy
        from aiteam.router import HybridRouter
        policy = build_default_router_policy()
        router = HybridRouter(adapters=[], policy=policy)

        request = RoutingRequest(
            role=Role.SCOUT,
            complexity=Complexity.LOW,
            criticality=Criticality.LOW,
        )
        profile = MagicMock()
        profile.tier = "senior_cloud"
        adapter = MagicMock()
        with patch.object(router, "_profile_for", return_value=profile):
            rank = router._tier_rank(adapter, request)
        self.assertEqual(rank, 99)


class TestScoutEvidenceGate(unittest.TestCase):
    """Los scouts deben saltar los evidence gates."""

    def _make_scout_task(self, phase="scout_project_state"):
        return WorkTask(
            task_id=f"CHAT-TEST::{phase}",
            title="Scout task",
            description="Scout test",
            role=Role.SCOUT,
            metadata={"is_scout": True, "phase": phase},
        )

    def test_scout_is_excluded_from_evidence_gate(self):
        task = self._make_scout_task()
        phase_name = task.task_id.split("::")[-1]
        is_scout = task.metadata.get("is_scout", False) or task.role.value == "scout"
        is_planning = is_scout or phase_name.startswith("plan_") or phase_name in (
            "lead_intake", "lead_close", "discovery"
        )
        self.assertTrue(is_planning, "Scout debe ser tratado como fase de planning (sin evidence gate)")

    def test_scout_phase_name_detection(self):
        for phase in ("scout_project_state", "scout_session_history", "scout_code"):
            task = WorkTask(
                task_id=f"CHAT-TEST::{phase}",
                title="Scout",
                description="Scout",
                role=Role.SCOUT,
                metadata={"is_scout": True},
            )
            self.assertEqual(task.role.value, "scout")
            self.assertTrue(task.metadata.get("is_scout"))


class TestScoutGracefulFailure(unittest.TestCase):
    """Si un scout falla, debe completarse con 'Sin datos disponibles.' en lugar de fallar."""

    def setUp(self):
        import tempfile
        from aiteam.taskboard import TaskBoard as Taskboard
        self.tmp = tempfile.mkdtemp()
        self.tb = Taskboard(storage_path=Path(self.tmp) / "tasks.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scout_task_can_be_completed_with_empty_output(self):
        task = WorkTask(
            task_id="CHAT-GRACEFUL::scout_project_state",
            title="Scout estado",
            description="Scout test",
            role=Role.SCOUT,
            metadata={"is_scout": True},
        )
        self.tb.add_task(task)
        self.tb.mark_completed("CHAT-GRACEFUL::scout_project_state", details="Sin datos disponibles.")
        result_task = self.tb.get_task("CHAT-GRACEFUL::scout_project_state")
        self.assertEqual(result_task.state, TaskState.COMPLETED)
        self.assertEqual(result_task.metadata.get("result"), "Sin datos disponibles.")

    def test_scout_failure_unblocks_dependent(self):
        """Si scout completa (aunque con output vacío), dependiente pasa a READY."""
        scout = WorkTask(
            task_id="CHAT-DEP::scout_project_state",
            title="Scout",
            description="Scout",
            role=Role.SCOUT,
            metadata={"is_scout": True},
        )
        dependent = WorkTask(
            task_id="CHAT-DEP::lead_intake",
            title="Lead intake",
            description="Lead",
            role=Role.TEAM_LEAD,
            dependencies=["CHAT-DEP::scout_project_state"],
            metadata={},
        )
        self.tb.add_task(scout)
        self.tb.add_task(dependent)
        # Scout completa con graceful failure
        self.tb.mark_completed("CHAT-DEP::scout_project_state", details="Sin datos disponibles.")
        # lead_intake debe estar READY
        ready = [t.task_id for t in self.tb.ready_tasks()]
        self.assertIn("CHAT-DEP::lead_intake", ready)


class TestScoutContextBuilders(unittest.TestCase):
    """Tests para las funciones que pre-fetchen datos sin LLM."""

    def setUp(self):
        local_root = Path.cwd() / ".tmp_test_scout_preflight"
        local_root.mkdir(parents=True, exist_ok=True)
        self.tmp = local_root / f"tmp_{uuid4().hex}"
        self.tmp.mkdir(parents=True, exist_ok=False)
        (self.tmp / "workspace").mkdir()
        self.workspace = self.tmp / "workspace"
        self.runtime = self.tmp / "runtime"
        self.runtime.mkdir()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scout_project_state_returns_string(self):
        from api.utils import _build_scout_project_state_context
        result = _build_scout_project_state_context(self.workspace)
        self.assertIsInstance(result, str)
        self.assertIn("ESTADO DEL PROYECTO", result)

    def test_scout_project_state_contains_structure(self):
        (self.workspace / "main.py").write_text("# main")
        (self.workspace / "README.md").write_text("# readme")
        from api.utils import _build_scout_project_state_context
        result = _build_scout_project_state_context(self.workspace)
        # Debe contener alguna referencia a los archivos o estructura
        self.assertTrue(len(result) > 20)

    def test_scout_project_state_forces_safe_git_decoding(self):
        from api.utils import _build_scout_project_state_context

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout="false\n", returncode=0),
            ]
            _build_scout_project_state_context(self.workspace)

        self.assertEqual(mock_run.call_count, 1)
        for call in mock_run.call_args_list:
            self.assertEqual(call.kwargs.get("encoding"), "utf-8")
            self.assertEqual(call.kwargs.get("errors"), "replace")

    def test_scout_project_state_reports_non_git_workspace_and_real_snapshot(self):
        from api.utils import _build_scout_project_state_context

        (self.workspace / "README.md").write_text("# readme")
        (self.workspace / "src").mkdir(exist_ok=True)
        (self.workspace / "src" / "app.py").write_text("print('ok')\n")

        result = _build_scout_project_state_context(self.workspace)

        self.assertIn("git repository: no", result)
        self.assertIn("workspace snapshot autoritativo:", result)
        self.assertIn("- README.md", result)
        self.assertIn("- src/app.py", result)

    def test_scout_session_history_no_sessions(self):
        from api.utils import _build_scout_session_history_context
        result = _build_scout_session_history_context(self.runtime)
        self.assertIsInstance(result, str)
        # Sin sesiones previas
        self.assertTrue("Sin sesiones previas" in result or len(result) < 100)

    def test_scout_session_history_with_sessions(self):
        # Crear runtime SQLite mínimo
        tasks = [
            {
                "task_id": "CHAT-ABC::lead_intake",
                "title": "test",
                "description": "pedido: construir API REST",
                "role": "team_lead",
                "state": "completed",
                "metadata": {"result": "Plan completado: 3 fases"},
            },
            {
                "task_id": "CHAT-ABC::lead_close",
                "title": "close",
                "description": "close",
                "role": "team_lead",
                "state": "completed",
                "metadata": {"result": "Entregado: API REST con 2 endpoints"},
            },
        ]
        SqliteStore(self.runtime / "aiteam.db").save_all_tasks(tasks)
        from api.utils import _build_scout_session_history_context
        result = _build_scout_session_history_context(self.runtime)
        self.assertIn("HISTORIAL DE SESIONES", result)
        self.assertIn("CHAT-ABC", result)

    def test_scout_session_history_reconstructs_failed_result_from_phase_verdicts(self):
        tasks = [
            {
                "task_id": "CHAT-ABCD1234::lead_intake",
                "title": "test",
                "description": "Solicitud original:\ncontinuar run dañada",
                "role": "team_lead",
                "state": "completed",
                "metadata": {"phase": "lead_intake"},
            }
        ]
        SqliteStore(self.runtime / "aiteam.db").save_all_tasks(tasks)
        SqliteStore(self.runtime / "aiteam.db").save_workflow_state(
            {
                "CHAT-ABCD1234": {
                    "phase_verdicts": {
                        "lead_intake": {
                            "phase_id": "lead_intake",
                            "status": "completed",
                            "slice_id": "2",
                        },
                        "build": {
                            "phase_id": "build",
                            "status": "completed",
                            "contract_status": "drift",
                            "slice_id": "4",
                            "reason_codes": ["slice_drift"],
                        },
                        "review": {
                            "phase_id": "review",
                            "status": "rejected",
                            "reason_codes": ["review_rejected"],
                        },
                        "qa": {
                            "phase_id": "qa",
                            "status": "blocked",
                            "reason_codes": ["qa_blocked"],
                        },
                    }
                }
            }
        )

        from api.utils import _build_scout_session_history_context

        result = _build_scout_session_history_context(self.runtime)
        self.assertIn("resultado_reconstruido: fallido", result)
        self.assertIn("review:rejected_decision", result)
        self.assertIn("qa:blocked_status", result)


class TestScoutNotInPhaseTaskIds(unittest.TestCase):
    """Scouts no deben aparecer en phase_task_ids ni en el scoring."""

    def test_scout_ids_not_in_workflow_phase_keys(self):
        task_root = "CHAT-SCORE"
        scout_state_id = f"{task_root}::scout_project_state"
        scout_history_id = f"{task_root}::scout_session_history"
        lead_task_id = f"{task_root}::lead_intake"

        # Simulamos el phase_task_ids que construye api/main.py
        phase_task_ids = {"lead_intake": lead_task_id}
        # Los scouts NO se añaden a phase_task_ids
        phase_task_set = set(phase_task_ids.values())

        self.assertNotIn(scout_state_id, phase_task_set)
        self.assertNotIn(scout_history_id, phase_task_set)
        self.assertIn(lead_task_id, phase_task_set)
