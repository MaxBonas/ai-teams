"""P5 — autonomy policy: auto-resolution of OPERATIONAL escalations.

Covers: classification (operational vs product), the once-per-(issue, reason)
rule in autonomous mode, the supervised TTL, wake-on-resolve continuation,
and the project_config.json accessors.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.autonomy import AUTONOMY_RESOLVER_ID, auto_resolve_operational_interactions
from aiteam.db.interactions import create_interaction, get_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import PROJECT_CONFIG_NAME, project_autonomy, set_project_autonomy


def _init_db(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type)"
            " VALUES ('role:lead', 'lead', 'Lead', 'lead', 'manual')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:intake', 'goal-1', 'Build', 'in_progress', 'lead', 'role:lead')"
        )
        conn.commit()
    return db_path


def _set_autonomous(runtime_dir: Path) -> None:
    set_project_autonomy(runtime_dir, "autonomous")


def _create_escalation(db_path: Path, *, reason: str, interaction_id: str | None = None) -> dict:
    return create_interaction(
        db_path,
        issue_id="issue:intake",
        kind="request_confirmation",
        payload={"reason": reason},
        interaction_id=interaction_id,
    )


def _wakeups(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM wakeup_requests").fetchall()
        return [dict(row) for row in rows]


def test_supervised_default_leaves_everything_pending(tmp_path: Path) -> None:
    db_path = _init_db(tmp_path / ".aiteam")
    _create_escalation(db_path, reason="cost_breaker_tripped")

    assert auto_resolve_operational_interactions(db_path) == []
    rows = _pending(db_path)
    assert len(rows) == 1


def test_autonomous_resolves_operational_and_wakes_assignee(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    _set_autonomous(runtime_dir)
    created = _create_escalation(db_path, reason="cost_breaker_tripped")

    resolved = auto_resolve_operational_interactions(db_path)

    assert resolved == [created["id"]]
    row = get_interaction(db_path, interaction_id=created["id"])
    assert row is not None
    assert row["status"] == "accepted"
    assert row["resolved_by_user_id"] == AUTONOMY_RESOLVER_ID
    result = json.loads(row["result_json"])
    assert result["resolution_data"]["auto_resolved"] is True
    assert result["resolution_data"]["autonomy_trigger"] == "autonomous"
    # Continuation: the assignee wakeup fired exactly as if the user clicked.
    wakeups = _wakeups(db_path)
    assert any(w["agent_id"] == "role:lead" and w["reason"] == "interaction_resolved" for w in wakeups)


def test_autonomous_once_per_issue_and_reason(tmp_path: Path) -> None:
    """The second identical escalation means the safe default didn't work —
    it must stay pending for the user (no auto-resolve loop fuel)."""
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    _set_autonomous(runtime_dir)
    first = _create_escalation(db_path, reason="delegation_churn_limit", interaction_id="int-1")
    assert auto_resolve_operational_interactions(db_path) == [first["id"]]

    _create_escalation(db_path, reason="delegation_churn_limit", interaction_id="int-2")
    assert auto_resolve_operational_interactions(db_path) == []
    row = get_interaction(db_path, interaction_id="int-2")
    assert row is not None and row["status"] == "pending"


def test_autonomous_never_touches_product_decisions(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    _set_autonomous(runtime_dir)
    for reason in ("initial_cycle_ready", "criticality_requires_approval", "budget_exceeded"):
        _create_escalation(db_path, reason=reason, interaction_id=f"int-{reason}")

    assert auto_resolve_operational_interactions(db_path) == []
    assert len(_pending(db_path)) == 3


def test_escalation_reason_fallback_key(tmp_path: Path) -> None:
    """liveness writes 'escalation_reason' (subtree_stalled) instead of 'reason'."""
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    _set_autonomous(runtime_dir)
    created = create_interaction(
        db_path,
        issue_id="issue:intake",
        kind="request_confirmation",
        payload={"escalation_reason": "subtree_stalled", "blocked_child_ids": []},
    )

    assert auto_resolve_operational_interactions(db_path) == [created["id"]]


def test_supervised_ttl_resolves_only_expired(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    monkeypatch.setenv("AITEAM_INTERACTION_TTL_MINUTES", "30")
    _create_escalation(db_path, reason="cost_breaker_tripped", interaction_id="int-old")
    _create_escalation(db_path, reason="reviewer_fix_cycle_limit", interaction_id="int-fresh")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE issue_thread_interactions SET created_at = datetime('now', '-2 hours') WHERE id = 'int-old'"
        )
        conn.commit()

    resolved = auto_resolve_operational_interactions(db_path)

    assert resolved == ["int-old"]
    old = get_interaction(db_path, interaction_id="int-old")
    fresh = get_interaction(db_path, interaction_id="int-fresh")
    assert old is not None and old["status"] == "accepted"
    assert json.loads(old["result_json"])["resolution_data"]["autonomy_trigger"] == "ttl_expired"
    assert fresh is not None and fresh["status"] == "pending"


def test_supervised_ttl_never_touches_product(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / ".aiteam"
    db_path = _init_db(runtime_dir)
    monkeypatch.setenv("AITEAM_INTERACTION_TTL_MINUTES", "30")
    _create_escalation(db_path, reason="initial_cycle_ready", interaction_id="int-product")
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE issue_thread_interactions SET created_at = datetime('now', '-2 hours')")
        conn.commit()

    assert auto_resolve_operational_interactions(db_path) == []


def test_set_project_autonomy_preserves_config(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir(parents=True)
    config_path = runtime_dir / PROJECT_CONFIG_NAME
    config_path.write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription"], "adapter_policy": {"source": "project_creation"}}),
        encoding="utf-8",
    )

    assert project_autonomy(runtime_dir) == "supervised"
    set_project_autonomy(runtime_dir, "autonomous")
    assert project_autonomy(runtime_dir) == "autonomous"
    stored = json.loads(config_path.read_text(encoding="utf-8"))
    assert stored["adapter_profile_ids"] == ["codex_subscription"]
    assert stored["adapter_policy"] == {"source": "project_creation"}

    try:
        set_project_autonomy(runtime_dir, "yolo")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid mode must raise ValueError")


def _pending(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM issue_thread_interactions WHERE status = 'pending'"
        ).fetchall()
        return [dict(row) for row in rows]
