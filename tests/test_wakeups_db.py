from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import claim_next_wakeup, enqueue_wakeup, finish_wakeup, reconcile_stale_wakeups


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name) VALUES (?, ?, ?)",
            [
                ("role:team_lead", "team_lead", "Team Lead"),
                ("role:engineer", "engineer", "Engineer"),
            ],
        )
        conn.execute(
            """
            INSERT INTO runs (id, agent_id, invocation_source, status)
            VALUES (?, ?, ?, ?)
            """,
            ("run-1", "role:team_lead", "manual", "queued"),
        )
        conn.commit()


def test_enqueue_wakeup_coalesces_by_agent_and_idempotency_key(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = enqueue_wakeup(
        db_path,
        agent_id="role:team_lead",
        source="timer",
        reason="timer",
        payload={"n": 1},
        idempotency_key="timer:lead",
    )
    second = enqueue_wakeup(
        db_path,
        agent_id="role:team_lead",
        source="timer",
        reason="timer",
        payload={"n": 2},
        idempotency_key="timer:lead",
    )

    assert first["id"] == second["id"]
    assert second["coalesced_count"] == 1
    assert json.loads(second["payload_json"]) == {"n": 2}
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 1


def test_enqueue_wakeup_requeues_terminal_idempotent_request(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    first = enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-repeat",
        agent_id="role:team_lead",
        source="assignment",
        reason="assignment",
        idempotency_key="assignment:issue-1:lead",
    )
    claim_next_wakeup(db_path, agent_id="role:team_lead")
    finish_wakeup(db_path, wakeup_id=first["id"], status="finished", run_id="run-1")

    second = enqueue_wakeup(
        db_path,
        agent_id="role:team_lead",
        source="assignment",
        reason="assignment",
        payload={"issue_id": "issue-1"},
        idempotency_key="assignment:issue-1:lead",
    )

    assert second["id"] == "wakeup-repeat"
    assert second["status"] == "queued"
    assert second["run_id"] is None
    assert second["finished_at"] is None


def test_claim_next_wakeup_claims_oldest_queued_request(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-b",
        agent_id="role:team_lead",
        source="manual",
        reason="comment",
    )
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-a",
        agent_id="role:engineer",
        source="manual",
        reason="assignment",
    )

    claimed = claim_next_wakeup(
        db_path,
        agent_id="role:engineer",
        claimed_at="2026-05-04T12:00:00+00:00",
    )

    assert claimed is not None
    assert claimed["id"] == "wakeup-a"
    assert claimed["status"] == "claimed"
    assert claimed["claimed_at"] == "2026-05-04T12:00:00+00:00"


def test_claim_next_wakeup_can_be_scoped_to_snapshot_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-old",
        agent_id="role:team_lead",
        source="manual",
        reason="manual",
    )
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-new",
        agent_id="role:team_lead",
        source="assignment",
        reason="new_issue",
    )

    claimed = claim_next_wakeup(db_path, wakeup_ids=["wakeup-new"])

    assert claimed is not None
    assert claimed["id"] == "wakeup-new"
    with sqlite3.connect(str(db_path)) as conn:
        old = conn.execute("SELECT status FROM wakeup_requests WHERE id = ?", ("wakeup-old",)).fetchone()[0]
    assert old == "queued"


def test_claim_next_wakeup_empty_snapshot_claims_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-1",
        agent_id="role:team_lead",
        source="manual",
        reason="manual",
    )

    assert claim_next_wakeup(db_path, wakeup_ids=[]) is None


def test_claim_next_wakeup_allows_only_one_concurrent_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-1",
        agent_id="role:team_lead",
        source="manual",
        reason="manual",
    )
    barrier = threading.Barrier(2)
    results: list[dict[str, object] | None] = []
    lock = threading.Lock()

    def _claim() -> None:
        barrier.wait()
        result = claim_next_wakeup(db_path, agent_id="role:team_lead")
        with lock:
            results.append(result)

    threads = [threading.Thread(target=_claim), threading.Thread(target=_claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert sum(result is not None for result in results) == 1
    assert sum(result is None for result in results) == 1


def test_finish_wakeup_records_run_and_terminal_status(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-1",
        agent_id="role:team_lead",
        source="manual",
        reason="manual",
    )
    claim_next_wakeup(db_path, agent_id="role:team_lead")

    finished = finish_wakeup(
        db_path,
        wakeup_id="wakeup-1",
        status="finished",
        run_id="run-1",
        finished_at="2026-05-04T12:01:00+00:00",
    )

    assert finished is not None
    assert finished["status"] == "finished"
    assert finished["run_id"] == "run-1"
    assert finished["finished_at"] == "2026-05-04T12:01:00+00:00"


def test_finish_wakeup_rejects_non_terminal_status(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    with pytest.raises(ValueError):
        finish_wakeup(db_path, wakeup_id="missing", status="claimed")


def test_reconcile_stale_wakeups_requeues_claim_without_run(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    enqueue_wakeup(
        db_path,
        wakeup_id="wakeup-stale",
        agent_id="role:team_lead",
        source="manual",
        reason="manual",
    )
    claim_next_wakeup(
        db_path,
        agent_id="role:team_lead",
        claimed_at="2026-05-04T12:00:00+00:00",
    )

    reconciled = reconcile_stale_wakeups(
        db_path,
        max_age_sec=60,
        now="2026-05-04T12:10:00+00:00",
    )

    assert reconciled == ["wakeup-stale"]
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM wakeup_requests WHERE id = ?", ("wakeup-stale",)).fetchone()
    assert row["status"] == "queued"
    assert row["claimed_at"] is None
    assert row["error"] == "requeued_stale_claim"


def test_enqueue_wakeup_coalesces_by_agent_and_issue(tmp_path: Path) -> None:
    """One live wakeup per (agent, issue) even with distinct idempotency keys."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = enqueue_wakeup(
        db_path,
        agent_id="role:engineer",
        source="reconcile",
        reason="assignment",
        payload={"issue_id": "i1", "wake_reason": "assignment"},
        idempotency_key="assignment:i1:role:engineer",
    )
    second = enqueue_wakeup(
        db_path,
        agent_id="role:engineer",
        source="unblock",
        reason="lead_directive",
        payload={"issue_id": "i1", "wake_reason": "lead_directive", "instruction": "haz X"},
        idempotency_key="unblock:i1:role:engineer",
    )

    assert first["id"] == second["id"]
    assert second["coalesced_count"] == 1
    assert second["reason"] == "lead_directive"
    assert json.loads(second["payload_json"])["instruction"] == "haz X"
    with sqlite3.connect(str(db_path)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM wakeup_requests").fetchone()[0]
    assert count == 1


def test_enqueue_wakeup_does_not_coalesce_across_issues(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = enqueue_wakeup(
        db_path, agent_id="role:engineer", source="a", reason="assignment",
        payload={"issue_id": "i1"},
    )
    second = enqueue_wakeup(
        db_path, agent_id="role:engineer", source="a", reason="assignment",
        payload={"issue_id": "i2"},
    )

    assert first["id"] != second["id"]


def test_enqueue_wakeup_does_not_coalesce_into_claimed(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)

    first = enqueue_wakeup(
        db_path, agent_id="role:engineer", source="a", reason="assignment",
        payload={"issue_id": "i1"},
    )
    claimed = claim_next_wakeup(db_path, agent_id="role:engineer")
    assert claimed is not None and claimed["id"] == first["id"]

    second = enqueue_wakeup(
        db_path, agent_id="role:engineer", source="a", reason="assignment",
        payload={"issue_id": "i1"},
    )

    assert second["id"] != first["id"]
    assert second["status"] == "queued"
