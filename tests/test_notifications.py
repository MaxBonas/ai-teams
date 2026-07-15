"""Notificación de escalaciones + métrica de latencia de decisión."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from aiteam.db.interactions import create_interaction, decision_latency_stats, resolve_interaction
from aiteam.db.migration import SCHEMA_PATH


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) VALUES ('issue-1', 'g1', 'T', 'in_progress')"
        )
        conn.commit()
    return db


def test_escalation_fires_configured_notify_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AITEAM_NOTIFY_COMMAND recibe el payload por stdin — el operador se
    entera de la escalación sin abrir el cockpit."""
    sink = tmp_path / "notified.json"
    receiver = tmp_path / "receiver.py"
    receiver.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(r'{sink}').write_text(sys.stdin.read(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AITEAM_NOTIFY_COMMAND", f'"{sys.executable}" "{receiver}"')
    db = _db(tmp_path)

    create_interaction(
        db, issue_id="issue-1", kind="request_confirmation",
        payload={"reason": "daily_cost_cap_reached"},
        title="Cap de coste diario alcanzado",
        summary="prueba",
    )

    deadline = time.time() + 10
    while time.time() < deadline and not sink.exists():
        time.sleep(0.1)
    assert sink.exists(), "el comando de notificación nunca recibió el payload"
    payload = json.loads(sink.read_text(encoding="utf-8"))
    assert payload["title"] == "Cap de coste diario alcanzado"
    assert payload["issue_id"] == "issue-1"


def test_no_notify_command_is_silent_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AITEAM_NOTIFY_COMMAND", raising=False)
    db = _db(tmp_path)

    row = create_interaction(
        db, issue_id="issue-1", kind="request_confirmation", payload={"reason": "x"}
    )
    assert row["status"] == "pending"


def test_decision_latency_stats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AITEAM_NOTIFY_COMMAND", raising=False)
    db = _db(tmp_path)
    resolved = create_interaction(
        db, issue_id="issue-1", kind="request_confirmation", payload={"reason": "a"},
        idempotency_key="k1",
    )
    resolve_interaction(db, interaction_id=resolved["id"], action="accept", resolved_by_user_id="user")
    create_interaction(
        db, issue_id="issue-1", kind="request_confirmation", payload={"reason": "b"},
        idempotency_key="k2",
    )

    stats = decision_latency_stats(db)

    assert stats["resolved_count"] == 1
    assert stats["avg_resolution_seconds"] is not None and stats["avg_resolution_seconds"] >= 0
    assert stats["pending_count"] == 1
    assert stats["oldest_pending_seconds"] is not None and stats["oldest_pending_seconds"] >= 0
