from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiteam.db.interactions import (
    create_interaction,
    decision_latency_stats,
    resolve_interaction,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.notifications import notify_escalation


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status) "
            "VALUES ('issue-1', 'g1', 'T', 'in_progress')"
        )
        conn.commit()
    return db


def test_notification_command_runs_without_shell(monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_NOTIFY_COMMAND", 'notify-tool --channel "equipo uno"')
    proc = MagicMock()
    proc.stdin = MagicMock()

    with patch("aiteam.notifications.subprocess.Popen", return_value=proc) as popen:
        assert notify_escalation({"kind": "approval", "summary": "acción ñ"}) is True

    args, kwargs = popen.call_args
    assert args[0] == ["notify-tool", "--channel", "equipo uno"]
    assert kwargs["shell"] is False
    assert kwargs["encoding"] == "utf-8"
    proc.stdin.write.assert_called_once()
    assert "acción ñ" in proc.stdin.write.call_args.args[0]
    proc.stdin.close.assert_called_once()


def test_notification_without_command_is_noop(monkeypatch) -> None:
    monkeypatch.delenv("AITEAM_NOTIFY_COMMAND", raising=False)

    assert notify_escalation({"kind": "approval"}) is False


def test_escalation_fires_configured_notify_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El operador recibe la escalación sin abrir el cockpit."""
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
        db,
        issue_id="issue-1",
        kind="request_confirmation",
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


def test_no_notify_command_is_silent_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AITEAM_NOTIFY_COMMAND", raising=False)
    db = _db(tmp_path)

    row = create_interaction(
        db,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "x"},
    )
    assert row["status"] == "pending"


def test_decision_latency_stats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AITEAM_NOTIFY_COMMAND", raising=False)
    db = _db(tmp_path)
    resolved = create_interaction(
        db,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "a"},
        idempotency_key="k1",
    )
    resolve_interaction(
        db,
        interaction_id=resolved["id"],
        action="accept",
        resolved_by_user_id="user",
    )
    create_interaction(
        db,
        issue_id="issue-1",
        kind="request_confirmation",
        payload={"reason": "b"},
        idempotency_key="k2",
    )

    stats = decision_latency_stats(db)

    assert stats["resolved_count"] == 1
    assert stats["avg_resolution_seconds"] is not None
    assert stats["avg_resolution_seconds"] >= 0
    assert stats["pending_count"] == 1
    assert stats["oldest_pending_seconds"] is not None
    assert stats["oldest_pending_seconds"] >= 0
