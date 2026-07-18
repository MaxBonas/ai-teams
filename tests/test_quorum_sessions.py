from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.quorum_sessions import (
    accept_quorum_synthesis,
    create_quorum_session,
    degrade_quorum_session,
    evaluate_quorum_session,
    record_quorum_contribution,
)


def _init(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal:q', 'Quorum')")
        conn.execute(
            "INSERT INTO agents (id, role, name) VALUES "
            "('role:lead', 'lead', 'Lead'),"
            "('role:q1', 'reviewer', 'Q1'),"
            "('role:q2', 'reviewer', 'Q2')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES ('issue:q', 'goal:q', 'Decidir arquitectura', 'in_progress', 'lead', 'role:lead')"
        )
        conn.commit()


def _contribute(db_path: Path, session_id: str, agent: str, ordinal: int, provider: str) -> None:
    record_quorum_contribution(
        db_path,
        session_id=session_id,
        agent_id=agent,
        ordinal=ordinal,
        provider=provider,
        model=f"{provider}-model",
        channel="api",
        result="approved",
        evidence="Revisión del plan base rev-a con riesgos enumerados.",
        findings=[{"id": f"finding-{ordinal}", "severity": "medium", "summary": "riesgo"}],
    )


def test_quorum_requires_two_valid_provider_diverse_contributions(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    session = create_quorum_session(
        db_path, issue_id="issue:q", base_plan_revision_id="rev-a"
    )

    _contribute(db_path, session["id"], "role:q1", 1, "openai")
    first = evaluate_quorum_session(db_path, session_id=session["id"])
    assert first["ready"] is False
    assert first["missing_valid"] == 1

    _contribute(db_path, session["id"], "role:q2", 2, "openai")
    same_provider = evaluate_quorum_session(db_path, session_id=session["id"])
    assert same_provider["ready"] is False
    assert same_provider["diversity_satisfied"] is False

    _contribute(db_path, session["id"], "role:q2", 2, "google")
    ready = evaluate_quorum_session(db_path, session_id=session["id"])
    assert ready == {
        "ready": True,
        "status": "ready",
        "requested_contributions": 2,
        "min_valid_contributions": 2,
        "reduced_quorum": False,
        "valid_contributions": 2,
        "total_contributions": 2,
        "distinct_providers": 2,
        "missing_valid": 0,
        "diversity_satisfied": True,
    }


def test_reduced_quorum_accepts_one_available_senior(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    session = create_quorum_session(
        db_path, issue_id="issue:q", base_plan_revision_id="rev:single",
        requested_contributions=1,
    )
    assert session["requested_contributions"] == 1
    assert session["min_valid_contributions"] == 1

    _contribute(db_path, session["id"], "role:q1", 1, "openai")

    gate = evaluate_quorum_session(db_path, session_id=session["id"])
    assert gate["ready"] is True
    assert gate["diversity_satisfied"] is True
    assert gate["reduced_quorum"] is True


def test_quorum_rejects_narration_without_structured_findings(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    session = create_quorum_session(
        db_path, issue_id="issue:q", base_plan_revision_id="rev-a"
    )
    row = record_quorum_contribution(
        db_path,
        session_id=session["id"],
        agent_id="role:q1",
        ordinal=1,
        provider="openai",
        result="approved",
        evidence="Todo parece correcto.",
        findings=[],
    )
    assert row["valid"] == 0
    assert evaluate_quorum_session(db_path, session_id=session["id"])["valid_contributions"] == 0


@pytest.mark.parametrize("terminal_status", ["degraded", "failed"])
def test_terminal_quorum_cannot_be_revived_by_evaluation_or_late_contribution(
    tmp_path: Path, terminal_status: str
) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    session = create_quorum_session(db_path, issue_id="issue:q", base_plan_revision_id="rev-a")
    _contribute(db_path, session["id"], "role:q1", 1, "openai")
    if terminal_status == "degraded":
        degrade_quorum_session(db_path, session_id=session["id"], skipped_reason="test")
    else:
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE quorum_sessions SET status='failed' WHERE id=?", (session["id"],)
            )
            conn.commit()

    gate = evaluate_quorum_session(db_path, session_id=session["id"])

    assert gate["status"] == terminal_status
    assert gate["ready"] is False
    with pytest.raises(ValueError, match="is terminal"):
        _contribute(db_path, session["id"], "role:q2", 2, "google")
    assert evaluate_quorum_session(db_path, session_id=session["id"])["status"] == terminal_status


def test_accepted_synthesis_finishes_planning_without_starting_execution(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE issues SET metadata_json = '{\"profile\":\"lead_quorum\"}' WHERE id='issue:q'")
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) "
            "VALUES ('run:synthesis', 'role:lead', 'issue:q', 'completed')"
        )
        conn.execute(
            """
            INSERT INTO issue_documents (
                id, issue_id, key, title, body, current_revision_id, revision_number
            ) VALUES ('doc:plan', 'issue:q', 'plan', 'Plan', 'Plan B', 'rev-b', 2)
            """
        )
        conn.execute(
            """
            INSERT INTO issue_document_revisions (
                id, document_id, issue_id, key, title, body, revision_number, created_by_run_id
            ) VALUES ('rev-b', 'doc:plan', 'issue:q', 'plan', 'Plan', 'Plan B', 2, 'run:synthesis')
            """
        )
        conn.commit()
    session = create_quorum_session(
        db_path, issue_id="issue:q", base_plan_revision_id="rev-a"
    )
    _contribute(db_path, session["id"], "role:q1", 1, "openai")
    _contribute(db_path, session["id"], "role:q2", 2, "google")

    accepted = accept_quorum_synthesis(
        db_path,
        session_id=session["id"],
        synthesis_run_id="run:synthesis",
        final_plan_revision_id="rev-b",
        dispositions=[
            {"finding_id": "finding-1", "decision": "accept", "rationale": "Reduce el riesgo con evidencia verificable suficiente."},
            {"finding_id": "finding-2", "decision": "qualify", "rationale": "Se matiza para conservar el enfoque y limitar el coste."},
        ],
    )
    assert accepted["status"] == "accepted"
    with sqlite3.connect(str(db_path)) as conn:
        issue = conn.execute("SELECT status, metadata_json FROM issues WHERE id='issue:q'").fetchone()
        wakeups = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE reason='quorum_accepted' AND status='queued'"
        ).fetchone()[0]
        activity = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='quorum.accepted'"
        ).fetchone()[0]
    metadata = json.loads(issue[1])
    assert issue[0] == "done"
    assert metadata["profile"] == "lead_quorum"
    assert metadata["planning_status"] == "accepted_plan"
    assert wakeups == 0
    assert activity == 1

    repeated = accept_quorum_synthesis(
        db_path,
        session_id=session["id"],
        synthesis_run_id="run:synthesis",
        final_plan_revision_id="rev-b",
        dispositions=[
            {"finding_id": "finding-1", "decision": "accept", "rationale": "Reduce el riesgo con evidencia verificable suficiente."},
            {"finding_id": "finding-2", "decision": "qualify", "rationale": "Se matiza para conservar el enfoque y limitar el coste."},
        ],
    )
    assert repeated["status"] == "accepted"
    assert evaluate_quorum_session(db_path, session_id=session["id"]) == {
        "ready": False,
        "status": "accepted",
        "requested_contributions": 2,
        "min_valid_contributions": 2,
        "reduced_quorum": False,
        "valid_contributions": 2,
        "total_contributions": 2,
        "distinct_providers": 2,
        "missing_valid": 0,
        "diversity_satisfied": True,
    }
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='quorum.accepted'"
        ).fetchone()[0] == 1


def test_synthesis_requires_disposition_for_every_finding(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES ('run:s', 'role:lead', 'issue:q', 'completed')"
        )
        conn.execute(
            "INSERT INTO issue_documents (id, issue_id, key, title, body, current_revision_id) "
            "VALUES ('doc:p', 'issue:q', 'plan', 'Plan', 'B', 'rev:b')"
        )
        conn.execute(
            "INSERT INTO issue_document_revisions "
            "(id, document_id, issue_id, key, title, body, revision_number, created_by_run_id) "
            "VALUES ('rev:b', 'doc:p', 'issue:q', 'plan', 'Plan', 'B', 1, 'run:s')"
        )
        conn.commit()
    session = create_quorum_session(db_path, issue_id="issue:q", base_plan_revision_id="rev:a")
    _contribute(db_path, session["id"], "role:q1", 1, "openai")
    _contribute(db_path, session["id"], "role:q2", 2, "google")
    with pytest.raises(ValueError, match="missing dispositions"):
        accept_quorum_synthesis(
            db_path,
            session_id=session["id"],
            synthesis_run_id="run:s",
            final_plan_revision_id="rev:b",
            dispositions=[{"finding_id": "finding-1", "decision": "accept", "rationale": "Se acepta por su impacto causal claramente justificado."}],
        )


def test_persistence_rejects_synthesis_not_owned_by_configured_lead(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO runs (id, agent_id, issue_id, status) VALUES ('run:q1:synthesis', 'role:q1', 'issue:q', 'completed')"
        )
        conn.execute(
            "INSERT INTO issue_documents (id, issue_id, key, title, body, current_revision_id) "
            "VALUES ('doc:foreign', 'issue:q', 'plan', 'Plan', 'B', 'rev:foreign')"
        )
        conn.execute(
            "INSERT INTO issue_document_revisions "
            "(id, document_id, issue_id, key, title, body, revision_number, created_by_run_id) "
            "VALUES ('rev:foreign', 'doc:foreign', 'issue:q', 'plan', 'Plan', 'B', 1, 'run:q1:synthesis')"
        )
        conn.commit()
    session = create_quorum_session(db_path, issue_id="issue:q", base_plan_revision_id="rev:a")
    _contribute(db_path, session["id"], "role:q1", 1, "openai")
    _contribute(db_path, session["id"], "role:q2", 2, "google")

    with pytest.raises(ValueError, match="configured Lead"):
        accept_quorum_synthesis(
            db_path,
            session_id=session["id"],
            synthesis_run_id="run:q1:synthesis",
            final_plan_revision_id="rev:foreign",
            dispositions=[
                {"finding_id": "finding-1", "decision": "accept", "rationale": "Se acepta con una justificación causal suficiente."},
                {"finding_id": "finding-2", "decision": "accept", "rationale": "Se acepta con una justificación causal suficiente."},
            ],
        )
