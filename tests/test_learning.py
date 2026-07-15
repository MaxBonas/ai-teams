"""Memoria entre proyectos: destilado al cierre + inyección en el intake."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload
from aiteam.learning import distill_learning_facts, global_learning_facts


@pytest.fixture()
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "user-config"
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(cfg))
    return cfg


def _project_db(tmp_path: Path) -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'Proyecto test')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES ('a1', 'engineer', 'E', 'subscription_cli')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES "
            "('issue:intake', 'g1', 'Root', 'done', 'a1')"
        )
        conn.commit()
    return db


def test_distills_recurrent_infra_failures(tmp_path: Path, isolated_config: Path) -> None:
    db = _project_db(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        for i in range(4):
            conn.execute(
                "INSERT INTO runs (id, agent_id, provider, status, error_code) VALUES (?, 'a1', 'claude-code', 'failed', 'subscription_cli_not_found')",
                (f"run-{i}",),
            )
        conn.commit()

    facts = distill_learning_facts(db)

    assert any("claude-code" in f["fact"] and "subscription_cli_not_found" in f["fact"] for f in facts)
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("SELECT fact FROM learning_facts").fetchall()
    assert len(rows) == len(facts), "los hechos deben persistirse en la tabla del proyecto"

    # Idempotencia: re-destilar no duplica.
    distill_learning_facts(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM learning_facts").fetchone()[0] == len(facts)


def test_distills_accepted_runtime_waiver(tmp_path: Path, isolated_config: Path) -> None:
    from aiteam.db.interactions import create_interaction, resolve_interaction
    from aiteam.policies import RUNTIME_VERIFICATION_WAIVER_REASON

    db = _project_db(tmp_path)
    interaction = create_interaction(
        db, issue_id="issue:intake", kind="request_confirmation",
        payload={"reason": RUNTIME_VERIFICATION_WAIVER_REASON},
    )
    resolve_interaction(db, interaction_id=interaction["id"], action="accept", resolved_by_user_id="user")

    facts = distill_learning_facts(db)

    assert any("test_runner" in f["fact"] for f in facts)


def test_global_mirror_feeds_next_project_intake(tmp_path: Path, isolated_config: Path) -> None:
    """El ciclo completo: proyecto A destila → el intake del proyecto B recibe
    las lecciones en su wake payload."""
    (tmp_path / "proyecto-a").mkdir(exist_ok=True)
    db_a = _project_db(tmp_path / "proyecto-a")
    with sqlite3.connect(str(db_a)) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO runs (id, agent_id, provider, status, error_code) VALUES (?, 'a1', 'openai', 'failed', 'api_error')",
                (f"run-{i}",),
            )
        conn.commit()
    distill_learning_facts(db_a)

    lessons = global_learning_facts()
    assert any("openai" in lesson for lesson in lessons)

    # Proyecto B: el wake payload de su issue RAÍZ lleva las lecciones.
    (tmp_path / "proyecto-b").mkdir(exist_ok=True)
    db_b = _project_db(tmp_path / "proyecto-b")
    payload = build_wake_payload(db_b, issue_id="issue:intake")
    assert "lessons_from_previous_projects" in payload
    assert any("openai" in lesson for lesson in payload["lessons_from_previous_projects"])

    # Y una issue hija NO las lleva (solo planifica el Lead en la raíz).
    with sqlite3.connect(str(db_b)) as conn:
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status) VALUES "
            "('child', 'g1', 'issue:intake', 'Sub', 'todo')"
        )
        conn.commit()
    child_payload = build_wake_payload(db_b, issue_id="child")
    assert "lessons_from_previous_projects" not in child_payload


def test_no_facts_no_noise(tmp_path: Path, isolated_config: Path) -> None:
    db = _project_db(tmp_path)

    assert distill_learning_facts(db) == []
    assert global_learning_facts() == []
    payload = build_wake_payload(db, issue_id="issue:intake")
    assert "lessons_from_previous_projects" not in payload
