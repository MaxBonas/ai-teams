from __future__ import annotations

import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from scripts.audit_project_db import report


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime = project / ".aiteam"
    runtime.mkdir(parents=True)
    db = runtime / "aiteam.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id,title) VALUES ('g','Goal')")
        conn.execute("INSERT INTO agents (id,role,name) VALUES ('lead','lead','Lead')")
        conn.execute(
            "INSERT INTO issues (id,goal_id,title,status,role,assignee_agent_id) "
            "VALUES ('root','g','Plan','in_progress','lead','lead')"
        )
        conn.commit()
    return project, db


def test_audit_flags_quorum_without_live_path_or_provenance(
    tmp_path: Path, capsys
) -> None:
    project, db = _project(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO quorum_sessions (id,issue_id,base_plan_revision_id,status) "
            "VALUES ('q','root','rev-a','reviewing')"
        )
        conn.execute("INSERT INTO agents (id,role,name) VALUES ('auditor','quorum_auditor','Auditor')")
        conn.execute(
            "INSERT INTO quorum_contributions "
            "(id,session_id,agent_id,ordinal,result,evidence,findings_json,valid) "
            "VALUES ('c','q','auditor',1,'approved','e','[{\"id\":\"f\"}]',1)"
        )
        conn.commit()

    report(project, excerpts=False)
    output = capsys.readouterr().out

    assert "== QUORUM ==" in output
    assert "!! quorum reviewing sin auditor/run/wakeup vivo: 1" in output
    assert "!! contribuciones quorum validas sin provenance completa: 1" in output


def test_audit_accepts_reviewing_quorum_with_queued_auditor(tmp_path: Path, capsys) -> None:
    project, db = _project(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO agents (id,role,name) VALUES ('auditor','quorum_auditor','Auditor')")
        conn.execute(
            "INSERT INTO quorum_sessions (id,issue_id,base_plan_revision_id,status) "
            "VALUES ('q','root','rev-a','reviewing')"
        )
        conn.execute(
            "INSERT INTO issues (id,parent_id,goal_id,title,status,role,assignee_agent_id,metadata_json) "
            "VALUES ('child','root','g','Audit','todo','quorum_auditor','auditor',"
            "'{\"quorum_session_id\":\"q\"}')"
        )
        conn.execute(
            "INSERT INTO wakeup_requests (id,agent_id,source,reason,status,payload_json) "
            "VALUES ('w','auditor','quorum','new_issue','queued','{\"issue_id\":\"child\"}')"
        )
        conn.commit()

    report(project, excerpts=False)
    output = capsys.readouterr().out

    assert "OK quorum reviewing sin auditor/run/wakeup vivo: 0" in output


def test_audit_flags_stranded_context_curator_recovery(tmp_path: Path, capsys) -> None:
    project, db = _project(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO agents (id,role,name) VALUES ('curator','context_curator','Curator')")
        conn.execute(
            "INSERT INTO issues (id,parent_id,goal_id,title,status,role,assignee_agent_id,metadata_json) "
            "VALUES ('curator-issue','root','g','Curate','in_progress','context_curator','curator',"
            "'{\"context_curator_recovery\":{\"state\":\"retry_queued\",\"corrective_attempts\":1}}')"
        )
        conn.commit()

    report(project, excerpts=False)
    output = capsys.readouterr().out

    assert "!! context curator retry sin continuacion durable: 1" in output
