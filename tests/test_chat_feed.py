from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.utils as utils
from api.main import app
from api.routers.chat import _load_chat, _quorum_message_block_reason
from aiteam.db.migration import SCHEMA_PATH


def _init(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('role:lead', 'lead', 'Lead')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('issue:intake', 'g1', 'T', 'in_progress', 'lead', 'role:lead')"
        )
        conn.commit()


def _add_comment(db: Path, cid: str, body: str, *, created_at: str, user: bool = False) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, author_agent_id, author_user_id, body, created_at) "
            "VALUES (?, 'issue:intake', ?, ?, ?, ?)",
            (cid, None if user else "role:lead", "user" if user else None, body, created_at),
        )
        conn.commit()


def test_chat_returns_newest_when_thread_exceeds_limit(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init(db)
    # 150 messages, chronological ids so created_at ordering is deterministic.
    for i in range(150):
        _add_comment(db, f"c{i:03d}", f"msg {i}", created_at=f"2026-07-07 00:{i//60:02d}:{i%60:02d}")

    items = _load_chat(db, limit=120)

    assert len(items) == 120
    # Presented chronologically ascending…
    assert [it["body"] for it in items] == [f"msg {i}" for i in range(30, 150)]
    # …and crucially the NEWEST message is included (the bug froze it out).
    assert items[-1]["body"] == "msg 149"


def test_chat_short_thread_unaffected(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init(db)
    _add_comment(db, "c1", "hola", created_at="2026-07-07 00:00:01", user=True)
    _add_comment(db, "c2", "respuesta del lead", created_at="2026-07-07 00:00:02")

    items = _load_chat(db, limit=120)

    assert [it["body"] for it in items] == ["hola", "respuesta del lead"]
    assert items[0]["sender"] == "user"
    assert items[1]["sender"] == "agent"


def test_chat_cannot_silently_mutate_frozen_quorum_objective(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init(db)
    assert _quorum_message_block_reason(db, issue_id="issue:intake") is None
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO quorum_sessions (id, issue_id, base_plan_revision_id) VALUES ('q1', 'issue:intake', 'rev:a')"
        )
        conn.commit()
    reason = _quorum_message_block_reason(db, issue_id="issue:intake")
    assert reason is not None
    assert "Nueva tarea" in reason


# ── Endpoint contracts (recibos HTTP, no solo el helper) ─────────────────────

@pytest.fixture
def chat_client(tmp_path: Path):
    """TestClient con workspace configurado y DB sembrada (mismo patrón que test_crud_api)."""
    utils.set_current_workspace(tmp_path)
    # resolve_runtime_dir renombra runtime/ → .aiteam/ en workspaces externos;
    # sembramos directamente el destino final para poder reabrir la DB después.
    db = tmp_path / ".aiteam" / "aiteam.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    _init(db)
    return TestClient(app, raise_server_exceptions=True), db


def _freeze_intake(db: Path) -> None:
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "INSERT INTO quorum_sessions (id, issue_id, base_plan_revision_id) VALUES ('q1', 'issue:intake', 'rev:a')"
        )
        conn.commit()


def test_get_chat_endpoint_serves_feed_even_with_frozen_session(chat_client) -> None:
    """Regresión: el gate de freeze en el GET referenciaba `body` inexistente → 500."""
    client, db = chat_client
    _add_comment(db, "c1", "hola", created_at="2026-07-18 00:00:01", user=True)
    _freeze_intake(db)

    response = client.get("/api/chat?limit=10")

    assert response.status_code == 200
    bodies = [item["body"] for item in response.json()["messages"]]
    assert "hola" in bodies


def test_post_chat_message_rejected_409_when_objective_frozen(chat_client) -> None:
    client, db = chat_client
    _freeze_intake(db)

    response = client.post("/api/chat/message", json={"body": "cambia el objetivo", "issue_id": "issue:intake"})

    assert response.status_code == 409
    assert "Nueva tarea" in response.json()["detail"]
    # El recibo importante: la directiva NO quedó registrada como comentario.
    with sqlite3.connect(str(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM issue_comments").fetchone()[0]
    assert count == 0


def test_post_chat_message_accepted_when_not_frozen(chat_client) -> None:
    client, db = chat_client

    response = client.post("/api/chat/message", json={"body": "sigue con el plan", "issue_id": "issue:intake"})

    assert response.status_code == 200
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT body FROM issue_comments").fetchone()
    assert row is not None and row[0] == "sigue con el plan"
