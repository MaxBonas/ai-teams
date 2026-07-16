"""P5 — dieta de contexto: cuerpos de archivo solo cuando el workspace cambió."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


class _CapturingEngineerRuntime:
    """Guarda el wake payload de cada run para inspección."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        raw = str(wake_context.get("wake_payload_json") or "{}")
        try:
            self.payloads.append(json.loads(raw))
        except ValueError:
            self.payloads.append({})
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(status="completed", output="revisado")


def _db(tmp_path: Path) -> Path:
    # Como en capa 2 real: la DB vive en .aiteam/ (oculto, fuera del snapshot
    # del workspace) — en la raíz cambiaría el digest en cada comentario.
    (tmp_path / ".aiteam").mkdir(exist_ok=True)
    db_path = tmp_path / ".aiteam" / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES "
            "('role:engineer', 'engineer', 'E', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('issue-1', 'g1', 'Revisar notas.py', 'in_progress', 'engineer', 'role:engineer')"
        )
        conn.commit()
    return db_path


def _one_run(db_path: Path, runtime: _CapturingEngineerRuntime) -> None:
    executor = RunExecutor(db_path, AdapterRegistry([runtime]))
    enqueue_wakeup(
        db_path, agent_id="role:engineer", source="manual", reason="manual",
        payload={"issue_id": "issue-1", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:engineer")
    assert dispatch is not None
    executor.execute(dispatch)


def _contents(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        f["path"]: f.get("content")
        for f in payload.get("workspace_files") or []
    }


def test_unchanged_workspace_omits_file_bodies_on_second_run(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    (tmp_path / "notas.py").write_text("print('v1')\n", encoding="utf-8")
    runtime = _CapturingEngineerRuntime()

    _one_run(db_path, runtime)   # run 1: primera vez → cuerpos completos
    _one_run(db_path, runtime)   # run 2: workspace idéntico → dieta

    first, second = runtime.payloads[0], runtime.payloads[1]
    assert _contents(first).get("notas.py"), "la primera run recibe el cuerpo del archivo"
    assert _contents(second).get("notas.py") is None, "workspace sin cambios: cuerpo omitido"
    assert "workspace_files_note" in second
    assert "notas.py" in _contents(second), "la LISTA de paths sigue completa"

    # run 3: el workspace CAMBIA → cuerpos de vuelta.
    (tmp_path / "notas.py").write_text("print('v2')\n", encoding="utf-8")
    _one_run(db_path, runtime)
    third = runtime.payloads[2]
    assert _contents(third).get("notas.py"), "workspace cambiado: los cuerpos vuelven"
    assert "workspace_files_note" not in third


def test_delta_disabled_by_env_always_sends_bodies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_PAYLOAD_DELTA", "0")
    db_path = _db(tmp_path)
    (tmp_path / "notas.py").write_text("print('v1')\n", encoding="utf-8")
    runtime = _CapturingEngineerRuntime()

    _one_run(db_path, runtime)
    _one_run(db_path, runtime)

    assert _contents(runtime.payloads[1]).get("notas.py"), "con el flag apagado no hay dieta"


def test_failed_previous_run_resends_full_bodies(tmp_path: Path) -> None:
    """Si la run anterior falló, el agente pudo no llegar a leer los archivos."""
    db_path = _db(tmp_path)
    (tmp_path / "notas.py").write_text("print('v1')\n", encoding="utf-8")

    class _FailingOnce(_CapturingEngineerRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
            self.calls += 1
            if self.calls == 1:
                return ExecutionResult(status="failed", error="boom", exit_code=1)
            return ExecutionResult(status="completed", output="revisado")

    runtime = _FailingOnce()
    _one_run(db_path, runtime)   # falla
    _one_run(db_path, runtime)   # workspace idéntico, pero la previa falló

    assert _contents(runtime.payloads[1]).get("notas.py"), (
        "tras un fallo, se re-envían los cuerpos aunque el workspace no cambiara"
    )
