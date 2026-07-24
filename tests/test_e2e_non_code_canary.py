from __future__ import annotations

import contextlib
import copy
import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.adapters.registry import AdapterRegistry
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wake_payload import build_wake_payload
from aiteam.heartbeat.executor import RunExecutor
from aiteam.lead_intake import apply_accepted_team_proposal, build_team_proposal
from aiteam.objective_classification import (
    PROGRAMMING_ROLES,
    classify_objective,
)


def _non_code_db(tmp_path: Path) -> Path:
    db_path = tmp_path / ".aiteam" / "aiteam.db"
    db_path.parent.mkdir(parents=True)
    classification = classify_objective(
        "Estudio para una empresa de limpieza",
        "Crear formularios para analizar necesidades, operaciones y clientes.",
    )
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO goals (id, title) VALUES ('goal:cleaning', 'Empresa de limpieza')"
        )
        conn.execute(
            """
            INSERT INTO agents (id, role, name, seniority, adapter_type)
            VALUES ('role:lead', 'lead', 'Lead', 'lead', 'manual')
            """
        )
        conn.execute(
            """
            INSERT INTO issues (
                id, goal_id, title, description, status, role,
                assignee_agent_id, metadata_json
            )
            VALUES (?, ?, ?, ?, 'todo', 'lead', 'role:lead', ?)
            """,
            (
                "issue:intake",
                "goal:cleaning",
                "Estudio para una empresa de limpieza",
                "Crear formularios para analizar necesidades, operaciones y clientes.",
                json.dumps(
                    {
                        "profile": "full_team",
                        "objective_classification": classification.to_metadata(),
                    }
                ),
            ),
        )
        conn.execute(
            """
            INSERT INTO runs (id, agent_id, issue_id, status)
            VALUES
              ('run:non-code-canary', 'role:lead', 'issue:intake', 'completed'),
              ('run:bad-delegation', 'role:lead', 'issue:intake', 'running')
            """
        )
        conn.commit()
    return db_path


def test_cleaning_study_converges_without_invented_programming_or_test_loop(
    tmp_path: Path,
) -> None:
    db_path = _non_code_db(tmp_path)
    legacy_test = tmp_path / "tests" / "test_unrelated.py"
    legacy_test.parent.mkdir()
    legacy_test.write_text("def test_old(): assert True\n", encoding="utf-8")
    baseline_tests = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("test_*.py"))

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        issue = dict(
            conn.execute("SELECT * FROM issues WHERE id='issue:intake'").fetchone()
        )
    proposal = build_team_proposal(issue, profile="full_team", adapter_profiles=[])
    tampered_proposal = copy.deepcopy(proposal)
    tampered_proposal["proposed_team"].append(
        {"id": "role:engineer", "role": "engineer", "name": "Engineer"}
    )
    with pytest.raises(
        ValueError,
        match="programming roles require hiring from a software sub-issue",
    ):
        apply_accepted_team_proposal(
            db_path,
            parent_issue_id="issue:intake",
            proposal=tampered_proposal,
            source_run_id="run:non-code-canary",
        )
    wake_payload = build_wake_payload(db_path, issue_id="issue:intake")
    assert wake_payload["objective_contract"] == {
        "schema_version": "objective_execution_contract_v1",
        "kind": "research",
        "programming_roles_allowed": False,
        "tests_required": False,
        "acceptance_evidence": [
            "source_coverage_and_dates",
            "questions_mapped_to_decisions",
            "assumptions_and_calculations",
            "decision_ready_document",
        ],
    }
    outcome = apply_accepted_team_proposal(
        db_path,
        parent_issue_id="issue:intake",
        proposal=proposal,
        source_run_id="run:non-code-canary",
    )

    assert outcome["created_issues"]
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        roles = {
            row[0]
            for row in conn.execute(
                "SELECT role FROM issues WHERE parent_id='issue:intake'"
            ).fetchall()
        }
        dependencies = {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT issue_id, depends_on_issue_id FROM issue_dependencies"
            ).fetchall()
        }
    assert roles == {"web_scout", "context_curator", "lead"}
    assert not roles.intersection(PROGRAMMING_ROLES)
    assert (
        "issue:intake:evidence_synthesis",
        "issue:intake:source_research",
    ) in dependencies
    assert (
        "issue:intake:accept_delivery",
        "issue:intake:evidence_synthesis",
    ) in dependencies

    executor = RunExecutor(db_path, AdapterRegistry([]))
    assert executor._quality_close_denied(issue_id="issue:intake") is None
    verification = executor._machine_close_verification("issue:intake")
    assert "Objetivo non-code" in verification
    assert "BLOQUEANTE" not in verification

    rejected_engineer = executor._create_delegated_issue(
        issue_id="issue:intake",
        agent_id="role:lead",
        run={"id": "run:bad-delegation"},
        spec={
            "title": "Implementar formularios",
            "description": (
                "Crear código y tests para unos formularios que en realidad son "
                "un instrumento documental del estudio de necesidades. Esta petición "
                "deliberadamente errónea no debe materializar ningún artefacto ejecutable."
            ),
            "role": "engineer",
        },
        metadata_source="canary",
        activity_source="canary",
    )
    assert rejected_engineer is None

    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "UPDATE issues SET status='done' WHERE parent_id='issue:intake'"
        )
        conn.execute("UPDATE issues SET status='done' WHERE id='issue:intake'")
        final_status = conn.execute(
            "SELECT status FROM issues WHERE id='issue:intake'"
        ).fetchone()[0]
        rejected = conn.execute(
            """
            SELECT COUNT(*) FROM activity_log
            WHERE action='objective.programming_delegation_rejected'
              AND target_id='issue:intake'
            """
        ).fetchone()[0]
        conn.commit()

    assert final_status == "done"
    assert rejected == 1
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("test_*.py")) == baseline_tests


def test_mixed_objective_only_allows_explicitly_executable_engineering(
    tmp_path: Path,
) -> None:
    db_path = _non_code_db(tmp_path)
    mixed = classify_objective(
        "Analizar necesidades y construir aplicación web",
        "Preparar formularios e implementar frontend y API.",
    )
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "UPDATE issues SET metadata_json=? WHERE id='issue:intake'",
            (
                json.dumps(
                    {
                        "profile": "full_team",
                        "objective_classification": mixed.to_metadata(),
                    }
                ),
            ),
        )
        conn.commit()

    executor = RunExecutor(db_path, AdapterRegistry([]))
    rejected = executor._create_delegated_issue(
        issue_id="issue:intake",
        agent_id="role:lead",
        run={"id": "run:bad-delegation"},
        spec={
            "title": "Analizar entrevistas",
            "description": (
                "Sintetizar entrevistas, cuestionarios y formularios para comprender "
                "necesidades operativas, riesgos, clientes y decisiones de la empresa."
            ),
            "role": "engineer",
        },
        metadata_source="canary",
        activity_source="canary",
    )
    accepted = executor._create_delegated_issue(
        issue_id="issue:intake",
        agent_id="role:lead",
        run={"id": "run:bad-delegation"},
        spec={
            "title": "Implementar API Python para formularios",
            "description": (
                "Implementar una API Python y una base de datos SQLite para almacenar "
                "respuestas del formulario. Añadir endpoint, validación y código fuente."
            ),
            "role": "engineer",
            "objective_kind": "software",
        },
        metadata_source="canary",
        activity_source="canary",
    )

    assert rejected is None
    assert accepted is not None
    assert accepted["role"] == "engineer"
    accepted_metadata = json.loads(accepted["metadata_json"])
    assert accepted_metadata["objective_classification"]["kind"] == "software"
