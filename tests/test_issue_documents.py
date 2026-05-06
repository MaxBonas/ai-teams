from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from aiteam.db.documents import DocumentConflict, get_document, list_documents, list_revisions, put_document
from api.main import app

SCHEMA_PATH = Path(__file__).parent.parent / "aiteam" / "db" / "schema.sql"


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO agents (id, role, name) VALUES (?, ?, ?)", ("role:lead", "lead", "Lead"))
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Build", "todo", "role:lead"),
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status) VALUES (?, ?, ?, ?, ?)",
            ("run-1", "role:lead", "issue-1", "manual", "running"),
        )


def test_put_document_creates_and_revises_plan(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = put_document(
        db_path,
        issue_id="issue-1",
        key="plan",
        title="Plan",
        body="# Plan\n\nPrimera version.",
        run_id="run-1",
        metadata={"source": "test"},
    )
    second = put_document(
        db_path,
        issue_id="issue-1",
        key="plan",
        title="Plan revisado",
        body="# Plan\n\nSegunda version.",
        base_revision_id=first["current_revision_id"],
        run_id="run-1",
    )

    assert first["revision_number"] == 1
    assert second["revision_number"] == 2
    assert second["body"].endswith("Segunda version.")
    assert get_document(db_path, issue_id="issue-1", key="plan")["current_revision_id"] == second["current_revision_id"]
    assert [row["revision_number"] for row in list_revisions(db_path, issue_id="issue-1", key="plan")] == [1, 2]
    assert list_documents(db_path, issue_id="issue-1")[0]["key"] == "plan"


def test_put_document_rejects_stale_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = put_document(db_path, issue_id="issue-1", key="plan", title="Plan", body="v1")
    put_document(
        db_path,
        issue_id="issue-1",
        key="plan",
        title="Plan",
        body="v2",
        base_revision_id=first["current_revision_id"],
    )

    try:
        put_document(
            db_path,
            issue_id="issue-1",
            key="plan",
            title="Plan",
            body="stale",
            base_revision_id=first["current_revision_id"],
        )
    except DocumentConflict as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("expected stale revision conflict")


def test_document_api_roundtrip(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    db_path = runtime_dir / "aiteam.db"
    _init_db(db_path)
    client = TestClient(app)
    headers = {"x-aiteam-workspace": str(tmp_path)}

    created = client.put(
        "/api/issues/issue-1/documents/plan",
        json={"title": "Plan", "body": "# Plan\n\nDetalle", "run_id": "run-1"},
        headers=headers,
    )
    assert created.status_code == 200
    document = created.json()["document"]
    assert document["key"] == "plan"

    fetched = client.get("/api/issues/issue-1/documents/plan", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["document"]["current_revision_id"] == document["current_revision_id"]

    issue = client.get("/api/issues/issue-1", headers=headers)
    assert issue.status_code == 200
    assert issue.json()["plan_document"]["key"] == "plan"

    stale = client.put(
        "/api/issues/issue-1/documents/plan",
        json={"title": "Plan", "body": "stale", "base_revision_id": "old"},
        headers=headers,
    )
    assert stale.status_code == 409


def test_plan_document_api_recovers_plan_comment(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    db_path = runtime_dir / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO issue_comments (id, issue_id, author_agent_id, source_run_id, body)
            VALUES ('comment-plan', 'issue-1', 'role:lead', 'run-1', ?)
            """,
            (
                "Plan inicial con accountability\n\n"
                "**Objetivo**: entregar MVP.\n\n"
                "Sub-issues: build/review/qa.\n\n"
                "Riesgos: cierre sin evidencia.",
            ),
        )
    client = TestClient(app)
    headers = {"x-aiteam-workspace": str(tmp_path)}

    fetched = client.get("/api/issues/issue-1/documents/plan", headers=headers)

    assert fetched.status_code == 200
    document = fetched.json()["document"]
    assert document["key"] == "plan"
    assert document["metadata"]["source"] == "recovered_from_plan_comment"
    assert "Plan inicial" in document["body"]
