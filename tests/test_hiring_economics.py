from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam import hiring_economics
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.project_adapters import write_project_adapter_policy
from aiteam.user_config import store_secret


def _init_db(db_path: Path, *, agent_adapter: str, adapter_config: dict | None = None) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT INTO agents (id, role, name, adapter_type, adapter_config_json) VALUES (?, ?, ?, ?, ?)",
            ("agent-1", "engineer", "E", agent_adapter, json.dumps(adapter_config or {})),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, assignee_agent_id) VALUES ('i1', 'g1', 'T', 'todo', 'agent-1')"
        )
        conn.commit()


@pytest.fixture()
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "user-config"
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(cfg))
    return cfg


def test_premium_agent_estimates_cost_and_zero_savings(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    cost, savings = hiring_economics.estimate_run_economics(db, "agent-1")

    # default typical tokens: 8000 in * 200 + 1000 out * 800 per 1M → 2 cents
    assert cost == 2
    assert savings == 0  # the premium alternative IS the chosen adapter


def test_local_agent_estimates_savings_vs_premium(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="subscription_cli", adapter_config={"profile_id": "local_qwen_ollama"})
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "local_qwen_ollama"])

    cost, savings = hiring_economics.estimate_run_economics(db, "agent-1")

    assert cost == 0  # local channel: zero marginal cost
    assert savings >= 1  # vs the connected premium alternative


def test_dispatch_fills_run_economics(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    enqueue_wakeup(
        db, agent_id="agent-1", source="test", reason="assignment",
        payload={"issue_id": "i1", "wake_reason": "assignment"},
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id="agent-1")

    assert dispatch is not None
    assert dispatch.run["estimated_cost_cents"] == 2
    assert dispatch.run["estimated_savings_cents"] == 0


def test_dispatch_respects_payload_economics(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")

    enqueue_wakeup(
        db, agent_id="agent-1", source="test", reason="assignment",
        payload={"issue_id": "i1", "wake_reason": "assignment", "estimated_cost_cents": 42, "estimated_savings_cents": 7},
    )
    dispatch = HeartbeatScheduler(db).dispatch_next(agent_id="agent-1")

    assert dispatch is not None
    assert dispatch.run["estimated_cost_cents"] == 42
    assert dispatch.run["estimated_savings_cents"] == 7


def test_hiring_decision_flags_deviation_without_zero_cost_channel(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    payload = hiring_economics.log_hiring_decision(
        db,
        agent_id="agent-1",
        role="engineer",
        adapter_type="openai_api",
        adapter_config={},
        source="test",
    )

    assert payload["estimated_cost_cents"] > 0
    assert payload["policy_deviation"] == "no_zero_cost_channel_connected"
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'hiring.decision' AND target_id = 'agent-1'"
        ).fetchone()
    assert row[0] == 1


def test_hiring_decision_flags_scoring_deviation_with_local_available(
    tmp_path: Path, isolated_user_config: Path
) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    # Mark the local profile healthy so it counts as a connected zero-cost channel.
    isolated_user_config.mkdir(parents=True, exist_ok=True)
    (isolated_user_config / "adapter_health.json").write_text(
        json.dumps({"profiles": {"local_qwen_ollama": {"status": "ok"}}}), encoding="utf-8"
    )
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api", "local_qwen_ollama"])

    payload = hiring_economics.log_hiring_decision(
        db,
        agent_id="agent-1",
        role="engineer",
        adapter_type="openai_api",
        adapter_config={},
        source="test",
    )

    assert payload["policy_deviation"] == "scoring_preferred_premium"


def test_hiring_decision_no_deviation_for_senior_roles(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    payload = hiring_economics.log_hiring_decision(
        db,
        agent_id="agent-1",
        role="lead",
        adapter_type="openai_api",
        adapter_config={},
        source="test",
    )

    assert payload["policy_deviation"] is None
