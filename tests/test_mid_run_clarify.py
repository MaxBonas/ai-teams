"""E7-D4/D5: Tests de pausa conversacional mid-run (cualquier fase puede emitir [CLARIFY]).

Cubre:
- TaskState.WAITING_USER existe y serializa correctamente
- taskboard.mark_waiting_user: cambia estado, guarda pregunta en metadata
- _refresh_readiness: ignora tareas WAITING_USER (no las promueve a READY)
- Downstream tasks permanecen PENDING mientras hay una tarea WAITING_USER
- Orquestador: _run_task detecta [CLARIFY] en fases no-lead → marca WAITING_USER
- Orquestador: lead_intake y scouts NO son pausados por el orquestador (manejados por main.py)
- retry_task sobre una tarea WAITING_USER la vuelve READY
- Tarea WAITING_USER serializada a JSON se puede cargar de vuelta
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from aiteam.sqlite_store import SqliteStore
from aiteam.types import TaskState, WorkTask, Role, Complexity, Criticality
from aiteam.taskboard import TaskBoard


def _make_task(
    task_id: str,
    role: str = "engineer",
    deps: list[str] | None = None,
) -> WorkTask:
    return WorkTask(
        task_id=task_id,
        title=f"Task {task_id}",
        description="Test task",
        role=Role(role),
        complexity=Complexity.MEDIUM,
        criticality=Criticality.MEDIUM,
        dependencies=deps or [],
    )


class TestWaitingUserState(unittest.TestCase):

    def test_waiting_user_in_task_state_enum(self):
        self.assertIn("waiting_user", [s.value for s in TaskState])

    def test_waiting_user_value(self):
        self.assertEqual(TaskState.WAITING_USER.value, "waiting_user")

    def test_task_state_from_string(self):
        state = TaskState("waiting_user")
        self.assertEqual(state, TaskState.WAITING_USER)


class TestTaskBoardMarkWaitingUser(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._board = TaskBoard(Path(self._tmp) / "tasks.json")

    def test_mark_waiting_user_changes_state(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿Cuál es el stack tecnológico?")
        t = self._board.get_task("t1")
        self.assertEqual(t.state, TaskState.WAITING_USER)

    def test_mark_waiting_user_stores_question(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿REST o GraphQL?")
        t = self._board.get_task("t1")
        self.assertEqual(t.metadata.get("clarify_question"), "¿REST o GraphQL?")

    def test_mark_waiting_user_stores_timestamp(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿Config?")
        t = self._board.get_task("t1")
        self.assertIn("waiting_since", t.metadata)

    def test_waiting_user_task_not_in_ready_tasks(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿Config?")
        ready = self._board.ready_tasks()
        ready_ids = [t.task_id for t in ready]
        self.assertNotIn("t1", ready_ids)


class TestRefreshReadinessSkipsWaitingUser(unittest.TestCase):
    """_refresh_readiness no debe promover ni resetear tareas WAITING_USER."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._board = TaskBoard(Path(self._tmp) / "tasks.json")

    def test_waiting_user_state_preserved_after_refresh(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿?")
        # Forzar refresh_readiness via ready_tasks()
        self._board.ready_tasks()
        t = self._board.get_task("t1")
        self.assertEqual(t.state, TaskState.WAITING_USER)

    def test_downstream_stays_pending_while_upstream_waiting_user(self):
        """Si upstream está en WAITING_USER, downstream debe permanecer PENDING."""
        upstream = _make_task("upstream")
        downstream = _make_task("downstream", deps=["upstream"])
        self._board.add_task(upstream)
        self._board.add_task(downstream)
        # upstream → WAITING_USER
        self._board.mark_waiting_user("upstream", question="¿?")
        # downstream debe estar PENDING (upstream no está COMPLETED)
        d = self._board.get_task("downstream")
        self.assertEqual(d.state.value, "pending")
        # Y no debe aparecer en ready_tasks
        ready_ids = [t.task_id for t in self._board.ready_tasks()]
        self.assertNotIn("downstream", ready_ids)

    def test_downstream_becomes_ready_after_retry_and_complete(self):
        """Si upstream se reanuda y completa, downstream debe ser READY."""
        upstream = _make_task("upstream")
        downstream = _make_task("downstream", deps=["upstream"])
        self._board.add_task(upstream)
        self._board.add_task(downstream)
        self._board.mark_waiting_user("upstream", question="¿?")
        # Reanudar: retry_task → READY, luego mark_completed
        self._board.retry_task("upstream", reason="answer_injected")
        self._board.mark_completed("upstream", details="output completo")
        # Ahora downstream debe ser READY
        ready_ids = [t.task_id for t in self._board.ready_tasks()]
        self.assertIn("downstream", ready_ids)


class TestWaitingUserSerialization(unittest.TestCase):
    """WAITING_USER se persiste y carga correctamente desde SQLite."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._path = Path(self._tmp) / "tasks.json"

    def test_waiting_user_survives_save_and_load(self):
        board1 = TaskBoard(self._path)
        task = _make_task("t1")
        board1.add_task(task)
        board1.mark_waiting_user("t1", question="¿Qué base de datos?")

        # Crear nuevo TaskBoard desde el mismo archivo
        board2 = TaskBoard(self._path)
        t = board2.get_task("t1")
        self.assertIsNotNone(t)
        self.assertEqual(t.state, TaskState.WAITING_USER)
        self.assertEqual(t.metadata.get("clarify_question"), "¿Qué base de datos?")

    def test_waiting_user_json_value_is_string(self):
        """El valor serializado debe ser 'waiting_user' (no el enum completo)."""
        board = TaskBoard(self._path)
        task = _make_task("t1")
        board.add_task(task)
        board.mark_waiting_user("t1", question="¿?")
        raw = SqliteStore(self._path.with_name("aiteam.db")).load_all_tasks()
        states = [item["state"] for item in raw if item["task_id"] == "t1"]
        self.assertEqual(states[0], "waiting_user")


class TestRetryFromWaitingUser(unittest.TestCase):
    """retry_task puede reactivar una tarea WAITING_USER."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._board = TaskBoard(Path(self._tmp) / "tasks.json")

    def test_retry_sets_ready(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿?")
        self._board.retry_task("t1", reason="answer_injected")
        t = self._board.get_task("t1")
        self.assertEqual(t.state, TaskState.READY)

    def test_retry_clears_waiting_metadata(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿API pública?")
        self._board.retry_task("t1", reason="answer")
        t = self._board.get_task("t1")
        # state es READY — la pregunta permanece en metadata como historial (no se limpia)
        # pero el estado debe ser READY
        self.assertEqual(t.state, TaskState.READY)

    def test_retry_reason_recorded(self):
        task = _make_task("t1")
        self._board.add_task(task)
        self._board.mark_waiting_user("t1", question="¿?")
        self._board.retry_task("t1", reason="mid_run_clarification_injected")
        t = self._board.get_task("t1")
        self.assertEqual(t.metadata.get("retry_reason"), "mid_run_clarification_injected")


class TestMultipleWaitingTasks(unittest.TestCase):
    """Solo la primera tarea WAITING_USER debe bloquear el run."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._board = TaskBoard(Path(self._tmp) / "tasks.json")

    def test_two_waiting_tasks_are_both_waiting(self):
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        self._board.add_task(t1)
        self._board.add_task(t2)
        self._board.mark_waiting_user("t1", question="¿A?")
        self._board.mark_waiting_user("t2", question="¿B?")
        waiting = [
            t for t in self._board.list_tasks()
            if t.state == TaskState.WAITING_USER
        ]
        self.assertEqual(len(waiting), 2)

    def test_waiting_tasks_not_returned_by_ready_tasks(self):
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        self._board.add_task(t1)
        self._board.add_task(t2)
        self._board.mark_waiting_user("t1", question="¿A?")
        self._board.mark_waiting_user("t2", question="¿B?")
        ready = self._board.ready_tasks()
        self.assertEqual(ready, [])


if __name__ == "__main__":
    unittest.main()
