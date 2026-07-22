from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.audit_parallel_channels import (
    _parallel_eligible,
    audit_database,
    audit_databases,
)


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "aiteam" / "db" / "schema.sql"


def _database(tmp_path: Path, *, eligible: bool, overlap: bool = False) -> Path:
    path = tmp_path / f"parallel-{eligible}-{overlap}.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g', 'Goal')")
        conn.executemany(
            "INSERT INTO agents (id, role, name, adapter_type, status) VALUES (?, ?, ?, ?, 'active')",
            [
                ("a1", "lead", "Lead", "subscription_cli"),
                ("a2", "file_scout", "Scout", "gemini_api" if eligible else "subscription_cli"),
            ],
        )
        second_parent = None if eligible else "root-1"
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) VALUES "
            "('root-1', 'g', 'One', 'in_progress', 'lead', 'a1')"
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, parent_id, title, status, role, assignee_agent_id) "
            "VALUES ('root-2', 'g', ?, 'Two', 'in_progress', 'file_scout', 'a2')",
            (second_parent,),
        )
        conn.executemany(
            "INSERT INTO wakeup_requests "
            "(id, agent_id, source, status, payload_json, requested_at) VALUES (?, ?, 'test', 'finished', ?, ?)",
            [
                ("w1", "a1", '{"issue_id":"root-1"}', "2026-01-01T00:00:00+00:00"),
                ("w2", "a2", '{"issue_id":"root-2"}', "2026-01-01T00:00:02+00:00"),
            ],
        )
        second_start = "2026-01-01T00:00:05+00:00" if overlap else "2026-01-01T00:00:10+00:00"
        conn.executemany(
            "INSERT INTO runs "
            "(id, agent_id, issue_id, wakeup_request_id, status, adapter_type, provider, started_at, finished_at) "
            "VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?)",
            [
                ("r1", "a1", "root-1", "w1", "subscription_cli", "openai-codex", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:10+00:00"),
                ("r2", "a2", "root-2", "w2", "gemini_api", "google", second_start, "2026-01-01T00:00:15+00:00"),
            ],
        )
        conn.commit()
    return path


def test_audit_finds_cross_root_cross_provider_serial_wait(tmp_path: Path) -> None:
    database = _database(tmp_path, eligible=True)
    report = audit_database(database)
    aggregate = audit_databases([database])

    assert report["recorded_runs"] == 2
    assert report["excluded_untimed_runs"] == 0
    assert report["eligible_serial_wait_runs"] == 1
    assert report["eligible_serial_wait_seconds"] == 8.0
    assert report["eligible_overlap_pairs"] == 0
    assert report["evidence_quality"] == "approximate"
    assert aggregate["conclusion"]["contention_trigger_observed"] is False
    assert aggregate["conclusion"]["approximate_contention_signal_observed"] is True
    assert aggregate["conclusion"]["evidence_sufficient_for_default_enable"] is False
    assert aggregate["conclusion"]["default_change_allowed"] is False


def test_same_root_and_provider_are_not_parallel_opportunity(tmp_path: Path) -> None:
    report = audit_databases([_database(tmp_path, eligible=False)])

    assert report["aggregate"]["eligible_serial_wait_runs"] == 0
    assert report["conclusion"]["evidence_sufficient_for_default_enable"] is False
    assert report["conclusion"]["default_change_allowed"] is False


def test_audit_observes_existing_eligible_overlap(tmp_path: Path) -> None:
    report = audit_database(_database(tmp_path, eligible=True, overlap=True))

    assert report["eligible_overlap_pairs"] == 1


def test_parallel_eligibility_matches_scheduler_invariants() -> None:
    left = {
        "agent_id": "lead-a",
        "root_issue_id": "root-a",
        "role": "lead",
        "provider": "openai",
        "adapter_type": "subscription_cli",
    }
    right = {
        "agent_id": "scout-b",
        "root_issue_id": "root-b",
        "role": "file_scout",
        "provider": "google",
        "adapter_type": "gemini_api",
    }

    assert _parallel_eligible(left, right) is True
    for field in ("agent_id", "root_issue_id", "provider"):
        conflicting = {**right, field: left[field]}
        assert _parallel_eligible(left, conflicting) is False
    assert _parallel_eligible(
        {**left, "role": "engineer"},
        {**right, "role": "reviewer"},
    ) is False
    assert _parallel_eligible(
        {**left, "capacity_pool": "shared"},
        {**right, "capacity_pool": "shared"},
    ) is False


def _add_decision(
    conn: sqlite3.Connection,
    *,
    decision_id: str,
    batch_id: str,
    wakeup_id: str,
    agent_id: str,
    issue_id: str,
    pool: str,
    considered_at: str,
    decision: str,
    reason: str,
    ready_at: str | None,
    snapshot: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO dispatch_candidate_decisions (
            id, batch_id, dispatch_mode, wakeup_request_id, agent_id,
            issue_id, root_issue_id, role, capacity_pool, is_work_slot,
            requested_at, ready_at, considered_at, decision, reason, details_json
        ) VALUES (?, ?, 'sequential', ?, ?, ?, ?, 'file_scout', ?, 0,
                  '2026-01-01T00:00:00+00:00', ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            batch_id,
            wakeup_id,
            agent_id,
            issue_id,
            issue_id,
            pool,
            ready_at,
            considered_at,
            decision,
            reason,
            '{"snapshot_contract":"candidate_queue_prefix_v1","snapshot_limit":25}' if snapshot else "{}",
        ),
    )


def test_exact_provenance_separates_wait_and_excludes_not_ready(tmp_path: Path) -> None:
    database = _database(tmp_path, eligible=True)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE runs SET started_at = '2026-01-01T00:00:02+00:00' WHERE id = 'r1'"
        )
        conn.executemany(
            "INSERT INTO agents (id, role, name, adapter_type, status) "
            "VALUES (?, 'file_scout', ?, 'gemini_api', 'active')",
            [("a3", "Blocked"), ("a4", "Checkout")],
        )
        conn.executemany(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id) "
            "VALUES (?, 'g', ?, 'in_progress', 'file_scout', ?)",
            [("root-3", "Blocked", "a3"), ("root-4", "Checkout", "a4")],
        )
        conn.executemany(
            "INSERT INTO wakeup_requests "
            "(id, agent_id, source, status, payload_json, requested_at) "
            "VALUES (?, ?, 'test', 'skipped', ?, '2026-01-01T00:00:00+00:00')",
            [
                ("w3", "a3", '{"issue_id":"root-3"}'),
                ("w4", "a4", '{"issue_id":"root-4"}'),
            ],
        )
        first = "2026-01-01T00:00:02+00:00"
        _add_decision(
            conn, decision_id="d1", batch_id="b1", wakeup_id="w1",
            agent_id="a1", issue_id="root-1", pool="pool-a",
            considered_at=first, decision="selected", reason="selected", ready_at=first,
        )
        _add_decision(
            conn, decision_id="d2", batch_id="b1", wakeup_id="w2",
            agent_id="a2", issue_id="root-2", pool="pool-b",
            considered_at=first, decision="rejected", reason="sequential_mode", ready_at=first,
        )
        _add_decision(
            conn, decision_id="d3", batch_id="b1", wakeup_id="w3",
            agent_id="a3", issue_id="root-3", pool="pool-c",
            considered_at=first, decision="rejected", reason="dependency_blocked", ready_at=None,
        )
        _add_decision(
            conn, decision_id="d4", batch_id="b1", wakeup_id="w4",
            agent_id="a4", issue_id="root-4", pool="pool-d",
            considered_at=first, decision="rejected", reason="checkout_active", ready_at=None,
        )
        _add_decision(
            conn, decision_id="d5", batch_id="b2", wakeup_id="w2",
            agent_id="a2", issue_id="root-2", pool="pool-b",
            considered_at="2026-01-01T00:00:10+00:00", decision="selected",
            reason="selected", ready_at=first,
        )
        conn.commit()

    source = audit_database(database)
    aggregate = audit_databases([database])

    assert source["evidence_quality"] == "exact"
    assert source["capacity_pool_count"] == 4
    assert source["total_queue_wait_seconds"] == 10.0
    assert source["ready_wait_seconds"] == 8.0
    assert source["parallelizable_wait_runs"] == 1
    assert source["parallelizable_wait_seconds"] == 8.0
    assert source["dispatch_evidence"]["selected_run_coverage_ratio"] == 1.0
    assert source["dispatch_evidence"]["excluded_by_reason"] == {
        "checkout_active": 1,
        "dependency_blocked": 1,
    }
    assert aggregate["conclusion"]["contention_trigger_observed"] is True


def test_singleton_provenance_is_partial_not_exact_trigger(tmp_path: Path) -> None:
    database = _database(tmp_path, eligible=True)
    with sqlite3.connect(database) as conn:
        _add_decision(
            conn, decision_id="only", batch_id="singleton", wakeup_id="w1",
            agent_id="a1", issue_id="root-1", pool="pool-a",
            considered_at="2026-01-01T00:00:00+00:00", decision="selected",
            reason="selected", ready_at="2026-01-01T00:00:00+00:00", snapshot=False,
        )
        conn.commit()

    source = audit_database(database)
    aggregate = audit_databases([database])

    assert source["evidence_quality"] == "partial_exact"
    assert source["dispatch_evidence"]["full_queue_batches"] == 0
    assert aggregate["conclusion"]["contention_trigger_observed"] is False
