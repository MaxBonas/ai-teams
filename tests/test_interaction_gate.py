"""Tests for the interaction creation gate in RunExecutor._apply_actions.

The gate enforces two rules:
  Rule 1 — per-run limit: at most ONE interaction may be created in a single
            heartbeat run.  A result that requests two interactions must have the
            second one silently dropped.
  Rule 2 — pre-existing gate: if there is already a pending (non-terminal)
            interaction for the issue, no new interaction is created.

Both rules prevent popup floods and cascading duplicate wakes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aiteam.adapters.registry import (
    AdapterDescriptor,
    AdapterRegistry,
    ExecutionResult,
)
from aiteam.db.interactions import create_interaction, list_interactions
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler


# ── Shared DB setup ──────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES (?, ?)", ("goal-1", "Goal"))
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type) VALUES (?, ?, ?, ?)",
            ("agent-1", "engineer", "Engineer", "openai_api"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES (?, ?, ?, ?, ?)",
            ("issue-1", "goal-1", "Implement feature", "in_progress", "agent-1"),
        )
        conn.commit()


def _dispatch(db_path: Path) -> Any:
    enqueue_wakeup(
        db_path,
        agent_id="agent-1",
        source="manual",
        reason="manual",
        payload={"issue_id": "issue-1"},
    )
    return HeartbeatScheduler(db_path).dispatch_next()


def _count_pending(db_path: Path, issue_id: str = "issue-1") -> int:
    _TERMINAL = {"accepted", "rejected", "answered", "cancelled", "expired"}
    interactions = list_interactions(db_path, issue_id=issue_id)
    return sum(1 for i in interactions if str(i.get("status") or "") not in _TERMINAL)


def _all_interactions(db_path: Path, issue_id: str = "issue-1") -> list[dict[str, Any]]:
    return list_interactions(db_path, issue_id=issue_id)


# ── Runtime helpers ──────────────────────────────────────────────────────────

def _make_interaction_payload(
    reason: str,
    title: str,
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "kind": "request_confirmation",
        "title": title,
        "summary": f"Summary for {reason}",
        "idempotency_key": idempotency_key,
        "payload": {"version": 1, "reason": reason},
        "continuation_policy": "wake_assignee",
    }


class _TwoInteractionsRuntime:
    """Returns a result that requests TWO interactions in a single run."""

    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="two interactions requested",
            actions={
                "interactions": [
                    _make_interaction_payload(
                        reason="first_question",
                        title="First question for user",
                        idempotency_key="gate-test:issue-1:first",
                    ),
                    _make_interaction_payload(
                        reason="second_question",
                        title="Second question for user",
                        idempotency_key="gate-test:issue-1:second",
                    ),
                ],
            },
        )


class _OneInteractionRuntime:
    """Returns a result that requests ONE interaction — used for the pre-existing gate test."""

    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def __init__(self, reason: str = "new_question", idempotency_key: str = "gate-test:issue-1:new") -> None:
        self._reason = reason
        self._idempotency_key = idempotency_key

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="one interaction requested",
            actions={
                "interactions": [
                    _make_interaction_payload(
                        reason=self._reason,
                        title="New question for user",
                        idempotency_key=self._idempotency_key,
                    ),
                ],
            },
        )


# ── Rule 1: per-run limit ────────────────────────────────────────────────────

class TestPerRunLimit:
    """At most one interaction may be created per heartbeat run."""

    def test_only_one_interaction_created_from_two_requested(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        registry = AdapterRegistry([_TwoInteractionsRuntime()])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        interactions = _all_interactions(db_path)
        assert len(interactions) == 1, (
            f"Only 1 interaction should be created when 2 are requested; got {len(interactions)}"
        )

    def test_first_interaction_is_the_one_created(self, tmp_path: Path) -> None:
        """The first interaction in the list wins; the second is dropped."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        registry = AdapterRegistry([_TwoInteractionsRuntime()])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        interactions = _all_interactions(db_path)
        assert len(interactions) == 1
        assert interactions[0]["idempotency_key"] == "gate-test:issue-1:first", (
            f"Expected first interaction to survive, got key={interactions[0]['idempotency_key']!r}"
        )

    def test_created_interaction_is_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        registry = AdapterRegistry([_TwoInteractionsRuntime()])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        interactions = _all_interactions(db_path)
        assert interactions[0]["status"] == "pending"

    def test_second_interaction_not_stored_at_all(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        registry = AdapterRegistry([_TwoInteractionsRuntime()])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE idempotency_key = 'gate-test:issue-1:second'",
            ).fetchone()[0]
        assert count == 0, "Second interaction should not be persisted at all"


# ── Rule 2: pre-existing gate ────────────────────────────────────────────────

class TestPreExistingGate:
    """If a pending interaction already exists for the issue, new ones are skipped."""

    def test_no_new_interaction_when_one_already_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        # Create a pending interaction BEFORE the run
        create_interaction(
            db_path,
            issue_id="issue-1",
            kind="request_confirmation",
            payload={"reason": "pre_existing"},
            idempotency_key="gate-test:issue-1:pre-existing",
            created_by_agent_id="agent-1",
        )
        registry = AdapterRegistry([_OneInteractionRuntime(
            reason="new_question",
            idempotency_key="gate-test:issue-1:new",
        )])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        # Only the pre-existing interaction should be present
        interactions = _all_interactions(db_path)
        assert len(interactions) == 1, (
            f"Expected only the pre-existing interaction; got {len(interactions)}"
        )

    def test_pre_existing_interaction_is_unchanged(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        pre = create_interaction(
            db_path,
            issue_id="issue-1",
            kind="request_confirmation",
            payload={"reason": "pre_existing"},
            idempotency_key="gate-test:issue-1:pre-existing",
            created_by_agent_id="agent-1",
        )
        registry = AdapterRegistry([_OneInteractionRuntime()])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status FROM issue_thread_interactions WHERE id = ?", (pre["id"],)
            ).fetchone()
        assert row["status"] == "pending", "Pre-existing interaction should stay pending"

    def test_new_interaction_not_created_when_pre_existing_pending(self, tmp_path: Path) -> None:
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        create_interaction(
            db_path,
            issue_id="issue-1",
            kind="request_confirmation",
            payload={"reason": "pre_existing"},
            idempotency_key="gate-test:issue-1:pre-existing",
            created_by_agent_id="agent-1",
        )
        registry = AdapterRegistry([_OneInteractionRuntime(
            reason="new_question",
            idempotency_key="gate-test:issue-1:new",
        )])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE idempotency_key = 'gate-test:issue-1:new'",
            ).fetchone()[0]
        assert count == 0, "New interaction should not be stored when a pending one already exists"

    def test_gate_does_not_block_when_prior_is_terminal(self, tmp_path: Path) -> None:
        """A resolved (accepted) interaction is terminal — the gate should let new ones through."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        from aiteam.db.interactions import resolve_interaction
        pre = create_interaction(
            db_path,
            issue_id="issue-1",
            kind="request_confirmation",
            payload={"reason": "pre_existing"},
            idempotency_key="gate-test:issue-1:pre-existing",
            created_by_agent_id="agent-1",
        )
        resolve_interaction(db_path, interaction_id=pre["id"], action="accept")

        registry = AdapterRegistry([_OneInteractionRuntime(
            reason="new_question",
            idempotency_key="gate-test:issue-1:new",
        )])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE idempotency_key = 'gate-test:issue-1:new'",
            ).fetchone()[0]
        assert count == 1, (
            "New interaction should be created when the pre-existing one is already accepted"
        )

    def test_gate_does_not_block_when_prior_is_rejected(self, tmp_path: Path) -> None:
        """A rejected interaction is terminal — gate should allow new interactions."""
        db_path = tmp_path / "aiteam.db"
        _init_db(db_path)
        from aiteam.db.interactions import resolve_interaction
        pre = create_interaction(
            db_path,
            issue_id="issue-1",
            kind="request_confirmation",
            payload={"reason": "pre_existing"},
            idempotency_key="gate-test:issue-1:pre-existing",
            created_by_agent_id="agent-1",
        )
        resolve_interaction(db_path, interaction_id=pre["id"], action="reject")

        registry = AdapterRegistry([_OneInteractionRuntime(
            reason="new_question",
            idempotency_key="gate-test:issue-1:new",
        )])
        dispatch = _dispatch(db_path)
        RunExecutor(db_path, registry).execute(dispatch)

        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM issue_thread_interactions"
                " WHERE idempotency_key = 'gate-test:issue-1:new'",
            ).fetchone()[0]
        assert count == 1, "New interaction should be created when the pre-existing one is rejected"
