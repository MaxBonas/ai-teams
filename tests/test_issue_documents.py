from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from aiteam.db.documents import (
    DocumentConflict,
    get_context_summary,
    get_document,
    list_documents,
    list_revisions,
    put_document,
)
from aiteam.context_curator import build_context_curation_target
from aiteam.plan_contract import PLAN_FORMAT, validate_plan_contract
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
        "/api/issues/issue-1/documents/spec",
        json={"title": "Especificación", "body": "# Especificación\n\nDetalle", "run_id": "run-1"},
        headers=headers,
    )
    assert created.status_code == 200
    document = created.json()["document"]
    assert document["key"] == "spec"

    fetched = client.get("/api/issues/issue-1/documents/spec", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["document"]["current_revision_id"] == document["current_revision_id"]

    stale = client.put(
        "/api/issues/issue-1/documents/spec",
        json={"title": "Especificación", "body": "stale", "base_revision_id": "old"},
        headers=headers,
    )
    assert stale.status_code == 409


def test_plan_comment_is_not_a_second_plan_source(tmp_path: Path) -> None:
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

    assert fetched.status_code == 404
    assert get_document(db_path, issue_id="issue-1", key="plan") is None

    legacy_write = client.put(
        "/api/issues/issue-1/documents/plan",
        json={"title": "Plan", "body": "# Plan legacy"},
        headers=headers,
    )
    assert legacy_write.status_code == 422


def _structured_plan() -> dict:
    return {
        "schema_version": 1,
        "objective": "Entregar un MVP verificable.",
        "scope": ["API y persistencia"],
        "assumptions": ["SQLite es la fuente de verdad"],
        "architecture": "Contrato versionado sobre issue_documents; ningún adapter conserva autoridad propia.",
        "work_items": [{
            "id": "api",
            "title": "Implementar contrato",
            "owner_role": "engineer",
            "reports_to": "lead",
            "deliverable": "API funcional",
            "evidence": ["pruebas API"],
            "accepted_by": "reviewer",
            "dependencies": [],
        }],
        "risks": [{"risk": "Regresión", "mitigation": "Pruebas", "rollback": "Revertir revisión"}],
        "verification": [{"criterion": "Roundtrip", "evidence": "pytest", "owner_role": "reviewer"}],
        "escalation_conditions": ["Conflicto de revisión sin resolución"],
        "next_run_risks": ["Un consumidor legacy puede esperar Markdown"],
        "narrative_markdown": "# Plan\n\nContrato durable.",
    }


def test_structured_plan_api_roundtrip_and_lead_provenance(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    db_path = runtime_dir / "aiteam.db"
    _init_db(db_path)
    client = TestClient(app)
    headers = {"x-aiteam-workspace": str(tmp_path)}

    created = client.put(
        "/api/issues/issue-1/documents/plan",
        json={"title": "Plan", "body": "", "plan": _structured_plan(), "run_id": "run-1"},
        headers=headers,
    )

    assert created.status_code == 200
    document = created.json()["document"]
    assert document["format"] == PLAN_FORMAT
    assert document["plan"]["work_items"][0]["reports_to"] == "lead"
    assert document["contract_validation"] == {"valid": True, "errors": []}
    assert validate_plan_contract(document["plan"])["valid"] is True


def test_plan_api_rejects_run_not_owned_by_assigned_lead(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    db_path = runtime_dir / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('agent-eng', 'engineer', 'Engineer')")
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status) VALUES ('run-eng', 'agent-eng', 'issue-1', 'manual', 'running')"
        )
    client = TestClient(app)

    response = client.put(
        "/api/issues/issue-1/documents/plan",
        json={"title": "Plan", "body": "", "plan": _structured_plan(), "run_id": "run-eng"},
        headers={"x-aiteam-workspace": str(tmp_path)},
    )

    assert response.status_code == 403


def test_context_summary_api_requires_causal_contract_and_curator_provenance(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    db_path = runtime_dir / "aiteam.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT INTO agents (id, role, name) VALUES ('curator', 'context_curator', 'Curator')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) "
            "VALUES ('curator-issue', 'goal-1', 'issue-1', 'Curate', 'in_progress', 'context_curator', 'curator')"
        )
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, invocation_source, status) "
            "VALUES ('curator-run', 'curator', 'curator-issue', 'manual', 'running')"
        )
        conn.execute(
            "INSERT INTO issue_comments (id, issue_id, author_user_id, body) "
            "VALUES ('source-comment', 'issue-1', 'user', ?)",
            ("Decisión con suficiente contexto. " * 80,),
        )
    target = build_context_curation_target(db_path, issue_id="issue-1")
    assert target is not None
    client = TestClient(app)
    headers = {"x-aiteam-workspace": str(tmp_path)}
    payload = {
        "summary_markdown": "Se conserva la decisión.",
        "start_comment_id": target["start_comment_id"],
        "end_comment_id": target["end_comment_id"],
        "char_count_original": target["char_count_original"],
        "start_char_offset": target["start_char_offset"],
        "end_char_offset": target["end_char_offset"],
        "causal_units": [{
            "id": "decision-1",
            "kind": "decision",
            "statement": "Se conserva la decisión.",
            "links": [],
            "source_comment_ids": ["source-comment"],
        }],
        "run_id": "curator-run",
    }

    denied = client.post(
        "/api/issues/issue-1/context-summary/blocks",
        json={**payload, "run_id": "run-1"},
        headers=headers,
    )
    accepted = client.post(
        "/api/issues/issue-1/context-summary/blocks",
        json=payload,
        headers=headers,
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    block = get_context_summary(db_path, issue_id="issue-1")["blocks"][0]
    assert block["causal_units"][0]["source_comment_ids"] == ["source-comment"]
