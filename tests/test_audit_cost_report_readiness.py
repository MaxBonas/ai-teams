from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from scripts.audit_cost_report_readiness import audit_database, main


def _database(tmp_path: Path, *, deliveries: int = 5, with_quality: bool = True) -> Path:
    db = tmp_path / "project" / ".aiteam" / "aiteam.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id,title,status) VALUES ('g','Goal','active')")
        conn.execute(
            "INSERT INTO agents (id,role,name,status) VALUES ('reviewer','reviewer','Reviewer','active')"
        )
        for index in range(deliveries):
            root = f"root-{index}"
            child = f"review-{index}"
            run = f"run-{index}"
            conn.execute(
                "INSERT INTO issues (id,goal_id,title,status,metadata_json) VALUES (?, 'g', ?, 'done', ?)",
                (root, root, json.dumps({"profile": "full_team"})),
            )
            conn.execute(
                "INSERT INTO issues (id,parent_id,goal_id,title,status,role,assignee_agent_id) "
                "VALUES (?, ?, 'g', ?, 'done', 'reviewer', 'reviewer')",
                (child, root, child),
            )
            conn.execute(
                "INSERT INTO runs (id,agent_id,issue_id,status,started_at,finished_at,actual_cost_cents) "
                "VALUES (?, 'reviewer', ?, 'completed', '2026-07-01T10:00:00+00:00', "
                "'2026-07-01T10:01:00+00:00', 2)",
                (run, child),
            )
            conn.execute(
                "INSERT INTO cost_events (id,run_id,agent_id,issue_id,cost_cents,period) "
                "VALUES (?, ?, 'reviewer', ?, 2, '2026-07')",
                (f"cost-{index}", run, child),
            )
            if with_quality:
                conn.execute(
                    "INSERT INTO agent_reports "
                    "(id,issue_id,agent_id,run_id,agent_role,result,valid,is_assignee) "
                    "VALUES (?, ?, 'reviewer', ?, 'reviewer', 'approved', 1, 1)",
                    (f"report-{index}", child, run),
                )
    return db


def test_ready_database_requires_recursive_delivery_evidence(tmp_path: Path) -> None:
    report = audit_database(_database(tmp_path))

    assert report["report_ready"] is True
    assert report["terminal_delivery_count"] == 5
    assert report["terminal_run_count"] == 5
    assert report["profiles"] == [{
        "profile": "full_team",
        "delivery_count": 5,
        "terminal_delivery_count": 5,
        "run_count": 5,
        "timed_run_count": 5,
        "timed_run_coverage": 1.0,
        "cost_provenance_run_count": 5,
        "cost_provenance_coverage": 1.0,
        "quality_delivery_count": 5,
        "quality_delivery_coverage": 1.0,
        "quality_signal_count": 5,
        "quality_pass_count": 5,
        "ready": True,
        "exclusions": [],
    }]


def test_database_fails_closed_on_volume_and_quality(tmp_path: Path) -> None:
    report = audit_database(_database(tmp_path, deliveries=4, with_quality=False))

    assert report["report_ready"] is False
    assert report["profiles"][0]["exclusions"] == [
        "insufficient_terminal_deliveries",
        "insufficient_quality_coverage",
    ]


def test_require_ready_cli_fails_without_a_ready_project(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", [
        "audit_cost_report_readiness.py",
        "--root",
        str(tmp_path),
        "--output",
        str(output),
        "--require-ready",
    ])

    assert main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["conclusion"]["cost_report_allowed"] is False
