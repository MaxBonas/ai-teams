"""P1+P2: suite independiente del implementador + review sobre diffs.

La ventaja estructural del equipo sobre un agente único: quien verifica no es
quien implementa, y verifica sobre recibos (diffs), no releyendo el mundo.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.dependencies import sync_default_child_dependencies
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


def _lead_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES "
            "('role:lead', 'lead', 'L', 'lead', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id, description) VALUES "
            "('root', 'g1', 'CLI de notas', 'in_progress', 'lead', 'role:lead', "
            "'Construye una CLI de notas con comandos add y list. Los tests deben pasar.')"
        )
        conn.commit()
    return db_path


class _DelegatingLeadRuntime:
    """Lead que delega SOLO un engineer — sin acordarse de los tests."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Delego la implementación.",
            actions={
                "create_issues": [
                    {
                        "title": "Implementar CLI de notas",
                        "description": "Implementa add y list con persistencia JSON. Files to modify: notas.py",
                        "role": "engineer",
                        "complexity": "medium",
                    }
                ]
            },
        )


def _run_lead_delegation(db_path: Path) -> None:
    executor = RunExecutor(db_path, AdapterRegistry([_DelegatingLeadRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "root", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)


def test_engineering_delegation_materializes_independent_test_designer(tmp_path: Path) -> None:
    db_path = _lead_db(tmp_path)

    _run_lead_delegation(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        designer = conn.execute(
            "SELECT id, description FROM issues WHERE parent_id='root' AND role='test_designer'"
        ).fetchone()
        engineer = conn.execute(
            "SELECT id FROM issues WHERE parent_id='root' AND role='engineer'"
        ).fetchone()
    assert designer is not None, "delegar engineering debe materializar la suite independiente"
    assert "NO leas la implementación" in designer["description"]
    assert "CLI de notas" in designer["description"], "la spec del padre viaja en la descripción"

    # Dependencias: el designer corre en PARALELO al engineer (sin dependencia
    # entre ellos); reviewer/test_runner esperarían a ambos.
    with sqlite3.connect(str(db_path)) as conn:
        dep = conn.execute(
            "SELECT COUNT(*) FROM issue_dependencies WHERE issue_id=? AND depends_on_issue_id=?",
            (designer["id"], engineer["id"]),
        ).fetchone()[0]
    assert dep == 0, "el test_designer no debe ver (ni esperar) la implementación"


def test_designer_not_duplicated_and_flag_disables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _lead_db(tmp_path)
    _run_lead_delegation(db_path)
    _run_lead_delegation(db_path)  # segunda delegación del mismo padre

    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id='root' AND role='test_designer'"
        ).fetchone()[0]
    assert n == 1, "una suite independiente por padre, no una por delegación"

    monkeypatch.setenv("AITEAM_INDEPENDENT_TESTS", "0")
    (tmp_path / "b").mkdir(exist_ok=True)
    db2 = _lead_db(tmp_path / "b")
    _run_lead_delegation(db2)
    with sqlite3.connect(str(db2)) as conn:
        n2 = conn.execute(
            "SELECT COUNT(*) FROM issues WHERE parent_id='root' AND role='test_designer'"
        ).fetchone()[0]
    assert n2 == 0


def test_dependents_wait_for_designer_too(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('root', 'g1', 'R', 'in_progress')"
        )
        for iid, role in (("eng", "engineer"), ("des", "test_designer"), ("run", "test_runner")):
            conn.execute(
                "INSERT INTO issues (id, goal_id, parent_id, title, status, role) VALUES (?, 'g1', 'root', ?, 'todo', ?)",
                (iid, role, role),
            )
        conn.commit()

    created = sync_default_child_dependencies(db_path, parent_issue_id="root")

    pairs = {(c["issue_id"], c["depends_on_issue_id"]) for c in created}
    assert ("run", "eng") in pairs and ("run", "des") in pairs, "el runner ejecuta AMBAS suites"
    assert ("des", "eng") not in pairs and ("eng", "des") not in pairs, "designer y engineer en paralelo"


def test_reviewer_wake_payload_carries_sibling_diffs(tmp_path: Path) -> None:
    """P2: el reviewer recibe los recibos git de las hermanas para veredicto
    por-hunk, sin releer el workspace."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:engineer','engineer','E')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('root', 'g1', 'R', 'in_progress')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role) VALUES "
            "('eng', 'g1', 'root', 'Impl', 'done', 'engineer'),"
            "('rev', 'g1', 'root', 'Review', 'todo', 'reviewer')"
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES ('run:e1', 'role:engineer', 'eng', 'completed')"
        )
        conn.execute(
            "INSERT INTO run_events (id, run_id, event_type, stream, payload_json) VALUES "
            "('ev1', 'run:e1', 'git_commit', 'system', ?)",
            (json.dumps({
                "commit": "abc1234",
                "diffstat": "notas.py | 20 ++++",
                "patch": "diff --git a/notas.py b/notas.py\n+def add(): ...",
            }),),
        )
        conn.commit()

    payload = build_wake_payload(db_path, issue_id="rev")

    diffs = payload.get("implementation_diffs")
    assert diffs, "el reviewer debe recibir los recibos git de sus hermanas"
    assert diffs[0]["commit"] == "abc1234"
    assert "def add" in diffs[0]["patch"]

    # Y el engineer NO recibe diffs (solo roles de juicio).
    eng_payload = build_wake_payload(db_path, issue_id="eng")
    assert "implementation_diffs" not in eng_payload


# ── P4: pase adversarial post-verde ───────────────────────────────────────────

class _HighCritLeadRuntime:
    """Lead que delega engineering con criticidad alta."""

    descriptor = AdapterDescriptor(adapter_type="subscription_cli", channel="subscription")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {"AITEAM_RUN_ID": run_id}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="Delego implementación crítica.",
            actions={
                "create_issues": [
                    {
                        "title": "Implementar módulo de pagos",
                        "description": "Implementa el flujo de pagos con validación estricta. Files to modify: pagos.py",
                        "role": "engineer",
                        "complexity": "high",
                        "criticality": "high",
                    }
                ]
            },
        )


def _run_highcrit_delegation(db_path: Path) -> None:
    executor = RunExecutor(db_path, AdapterRegistry([_HighCritLeadRuntime()]))
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="manual", reason="manual",
        payload={"issue_id": "root", "wake_reason": "manual"},
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    assert dispatch is not None
    executor.execute(dispatch)


def test_high_criticality_delegation_materializes_adversarial_qa(tmp_path: Path) -> None:
    db_path = _lead_db(tmp_path)

    _run_highcrit_delegation(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        qa = conn.execute(
            "SELECT description FROM issues WHERE parent_id='root' AND role='qa'"
        ).fetchone()
        crit = conn.execute(
            "SELECT criticality FROM issues WHERE parent_id='root' AND role='engineer'"
        ).fetchone()
    assert crit["criticality"] == "high", "la criticality del spec debe PERSISTIR en la columna"
    assert qa is not None, "criticidad alta debe materializar el pase adversarial"
    assert "tests que FALLEN" in qa["description"] or "tests que fallen" in qa["description"]
    assert "test_adversarial_" in qa["description"]


def test_normal_criticality_skips_adversarial_by_default(tmp_path: Path) -> None:
    db_path = _lead_db(tmp_path)

    _run_lead_delegation(db_path)  # delegación sin criticality

    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM issues WHERE parent_id='root' AND role='qa'").fetchone()[0]
    assert n == 0, "modo 'high' por defecto: sin criticidad alta no hay pase adversarial"


def test_adversarial_mode_always_forces_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AITEAM_ADVERSARIAL_QA", "always")
    db_path = _lead_db(tmp_path)

    _run_lead_delegation(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM issues WHERE parent_id='root' AND role='qa'").fetchone()[0]
    assert n == 1


def test_adversarial_mode_off_disables_even_on_high(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AITEAM_ADVERSARIAL_QA", "off")
    db_path = _lead_db(tmp_path)

    _run_highcrit_delegation(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM issues WHERE parent_id='root' AND role='qa'").fetchone()[0]
    assert n == 0


def test_cross_provider_enforcement_covers_adversarial_qa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """El atacante no debe compartir familia con el implementador."""
    from aiteam.project_adapters import write_project_adapter_policy
    from aiteam.user_config import store_secret

    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    store_secret(provider="google", name="default", secret="gemini-key")
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) VALUES "
            "('role:engineer', 'engineer', 'E', 'openai_api', '{}'),"
            "('role:qa', 'qa', 'Q', 'openai_api', '{}')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role) VALUES ('root', 'g1', 'R', 'in_progress', 'lead')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) VALUES "
            "('i-eng', 'g1', 'root', 'Impl', 'done', 'engineer', 'role:engineer')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id, criticality) VALUES "
            "('i-qa', 'g1', 'root', 'Adversarial', 'in_progress', 'qa', 'role:qa', 'high')"
        )
        conn.commit()
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "gemini_api"])

    executor = RunExecutor(db_path, AdapterRegistry([]))
    moved = executor._enforce_cross_provider_review(
        issue_id="i-qa", agent_id="role:qa", agent_role="qa"
    )

    assert moved is True
    with sqlite3.connect(str(db_path)) as conn:
        adapter = conn.execute("SELECT adapter_type FROM agents WHERE id='role:qa'").fetchone()[0]
    assert adapter == "gemini_api"
