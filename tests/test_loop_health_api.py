from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers.issues import router
from api.utils import get_current_workspace, set_current_workspace
from aiteam.db.migration import SCHEMA_PATH


def test_loop_health_exposes_offline_eval_summary_additively(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime = workspace / ".aiteam"
    runtime.mkdir(parents=True)
    db = runtime / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES (?, ?, ?, ?)",
            ("issue-1", "goal-1", "Pending root", "todo"),
        )
        conn.commit()

    app = FastAPI()
    app.include_router(router)
    previous = get_current_workspace()
    set_current_workspace(workspace)
    try:
        response = TestClient(app).get("/api/loop-health")
    finally:
        set_current_workspace(previous)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["orchestrator_evals"]["liveness"] == {
        "nonterminal_runs": 0,
        "claimed_or_running_wakeups": 0,
        "stranded_nonterminal_roots": 1,
        "healthy": False,
    }
    assert payload["orchestrator_evals"]["economy"]["total_tokens"] == 0
    assert payload["orchestrator_evals"]["quorum"]["available"] is True
