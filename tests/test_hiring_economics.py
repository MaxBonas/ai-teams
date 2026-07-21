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
from aiteam.user_config import record_model_health, store_secret


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

    # default typical tokens on Terra: 8000 in * 250 + 1000 out * 1500 → 3 cents
    assert cost == 3
    assert savings == 0  # the premium alternative IS the chosen adapter


def test_local_agent_estimates_savings_vs_premium(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="subscription_cli", adapter_config={"profile_id": "local_qwen_ollama"})
    store_secret(provider="openai", name="default", secret="sk-test")
    record_model_health(
        "openai_api", "gpt-5.6-sol", available=True, reason="economics fixture"
    )
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
    assert dispatch.run["estimated_cost_cents"] == 3
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


# ── A3: deviations scan + enforcement ─────────────────────────────────────────

def test_detect_policy_deviations_lists_premium_workers(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    store_secret(provider="openai", name="default", secret="sk-test")
    write_project_adapter_policy(tmp_path, profile_ids=["openai_api"])

    deviations = hiring_economics.detect_policy_deviations(db)

    assert len(deviations) == 1
    assert deviations[0]["agent_id"] == "agent-1"
    assert deviations[0]["role"] == "engineer"
    assert deviations[0]["estimated_cost_cents_per_run"] > 0
    assert deviations[0]["reason"] == "no_zero_cost_channel_connected"


def test_detect_policy_deviations_ignores_zero_cost_workers(tmp_path: Path, isolated_user_config: Path) -> None:
    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="subscription_cli", adapter_config={"profile_id": "local_qwen_ollama"})

    assert hiring_economics.detect_policy_deviations(db) == []


def _profile(profile_id: str, *, channel: str, adapter_type: str, health: str = "ok") -> dict:
    return {
        "id": profile_id,
        "adapter_type": adapter_type,
        "channel": channel,
        "provider": "openai" if channel == "api" else "ollama",
        "config": {},
        "health": {"status": health},
    }


def test_cost_policy_enforcement_reorders_tier3(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiteam.project_adapters import _apply_cost_policy

    monkeypatch.setenv("AITEAM_ENFORCE_COST_POLICY", "1")
    premium = _profile("openai_api", channel="api", adapter_type="openai_api")
    local = _profile("local_qwen", channel="local", adapter_type="subscription_cli")

    reordered = _apply_cost_policy("file_scout", [premium, local])
    assert reordered[0]["id"] == "local_qwen"

    # Non-Tier-3 roles keep the scoring order even under enforcement.
    assert _apply_cost_policy("engineer", [premium, local])[0]["id"] == "openai_api"


def test_cost_policy_enforcement_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiteam.project_adapters import _apply_cost_policy

    monkeypatch.delenv("AITEAM_ENFORCE_COST_POLICY", raising=False)
    premium = _profile("openai_api", channel="api", adapter_type="openai_api")
    local = _profile("local_qwen", channel="local", adapter_type="subscription_cli")

    assert _apply_cost_policy("file_scout", [premium, local])[0]["id"] == "openai_api"


def test_cost_policy_enforcement_requires_connected_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiteam.project_adapters import _apply_cost_policy

    monkeypatch.setenv("AITEAM_ENFORCE_COST_POLICY", "1")
    premium = _profile("openai_api", channel="api", adapter_type="openai_api")
    local = _profile("local_qwen", channel="local", adapter_type="subscription_cli", health="untested")

    assert _apply_cost_policy("file_scout", [premium, local])[0]["id"] == "openai_api"


def test_cost_policy_warning_comment_is_idempotent(tmp_path: Path, isolated_user_config: Path) -> None:
    from aiteam.lead_intake import _warn_cost_policy_deviation

    db = tmp_path / "aiteam.db"
    _init_db(db, agent_adapter="openai_api")
    decisions = [{
        "role": "engineer", "model": "gpt-4.1",
        "estimated_cost_cents": 2, "policy_deviation": "no_zero_cost_channel_connected",
    }]

    _warn_cost_policy_deviation(db, parent_issue_id="i1", decisions=decisions)
    _warn_cost_policy_deviation(db, parent_issue_id="i1", decisions=decisions)

    with sqlite3.connect(str(db)) as conn:
        comments = conn.execute(
            "SELECT COUNT(*) FROM issue_comments WHERE issue_id = 'i1' AND body LIKE '%Política de costes%'"
        ).fetchone()
        events = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action = 'cost_policy.warning' AND target_id = 'i1'"
        ).fetchone()
    assert comments[0] == 1
    assert events[0] == 1


def _seed_runs(db_path: Path, rows: list[tuple[str, str, str | None]]) -> None:
    """rows: (provider, status, error_code) — inserta runs mínimos."""
    import uuid
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("INSERT OR IGNORE INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute(
            "INSERT OR IGNORE INTO agents (id, role, name, adapter_type) VALUES ('agent-1','engineer','E','subscription_cli')"
        )
        for provider, status, error_code in rows:
            conn.execute(
                "INSERT INTO runs (id, agent_id, provider, status, error_code) VALUES (?,?,?,?,?)",
                (f"run-{uuid.uuid4()}", "agent-1", provider, status, error_code),
            )
        conn.commit()


def test_provider_router_health_flags_high_infra_failure_rate(tmp_path: Path) -> None:
    """Patrón del proyecto Unity: claude-code con muchos
    subscription_cli_not_found debe marcarse unhealthy."""
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    rows = [("claude-code", "completed", None)] * 6 + [
        ("claude-code", "failed", "subscription_cli_not_found")
    ] * 4  # 4/10 = 40% infra
    _seed_runs(db, rows)

    health = hiring_economics.provider_router_health(db)

    assert len(health) == 1
    entry = health[0]
    assert entry["provider"] == "claude-code"
    assert entry["total_runs"] == 10
    assert entry["infra_failures"] == 4
    assert entry["escalation_rate"] == 0.4
    assert entry["unhealthy"] is True


def test_provider_router_health_ignores_product_failures(tmp_path: Path) -> None:
    """Un fallo que NO es de infra (p.ej. sin error_code) no cuenta como
    escalación — no penaliza al proveedor por malas decisiones del agente."""
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    rows = [("openai", "completed", None)] * 8 + [("openai", "failed", None)] * 2
    _seed_runs(db, rows)

    health = hiring_economics.provider_router_health(db)

    assert health[0]["infra_failures"] == 0
    assert health[0]["escalation_rate"] == 0.0
    assert health[0]["unhealthy"] is False


def test_provider_router_health_skips_low_sample_providers(tmp_path: Path) -> None:
    """Menos de min_runs runs = ruido estadístico, no se reporta."""
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _seed_runs(db, [("gemini", "failed", "api_error")] * 3)  # solo 3 runs

    assert hiring_economics.provider_router_health(db) == []


def test_provider_escalation_threshold_env_tunable(monkeypatch: pytest.MonkeyPatch) -> None:
    from aiteam.policies import provider_escalation_threshold

    monkeypatch.setenv("AITEAM_PROVIDER_ESCALATION_THRESHOLD", "0.1")
    assert provider_escalation_threshold() == 0.1
    monkeypatch.setenv("AITEAM_PROVIDER_ESCALATION_THRESHOLD", "bogus")
    assert provider_escalation_threshold() == 0.25


def test_router_health_window_excludes_old_failures(tmp_path: Path) -> None:
    """La ventana temporal: fallos de hace días no cuentan en la vista 24h."""
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _seed_runs(db, [("openai", "completed", None)] * 5)
    with sqlite3.connect(str(db)) as conn:
        # 5 fallos de infra ANTIGUOS (hace 3 días)
        for i in range(5):
            conn.execute(
                "INSERT INTO runs (id, agent_id, provider, status, error_code, created_at) "
                "VALUES (?, 'agent-1', 'openai', 'failed', 'api_error', datetime('now', '-3 days'))",
                (f"old-{i}",),
            )
        conn.commit()

    historic = hiring_economics.provider_router_health(db)
    recent = hiring_economics.provider_router_health(db, window_hours=24)

    assert historic[0]["infra_failures"] == 5
    assert historic[0]["unhealthy"] is True
    assert recent[0]["infra_failures"] == 0, "los fallos viejos no deben gritar en la ventana reciente"
    assert recent[0]["unhealthy"] is False


def test_demoted_profile_ids_maps_unhealthy_adapter_types(tmp_path: Path) -> None:
    db = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO goals (id, title) VALUES ('g1', 'G')")
        conn.execute("INSERT INTO agents (id, role, name, adapter_type) VALUES ('agent-1','engineer','E','subscription_cli')")
        for i in range(6):
            conn.execute(
                "INSERT INTO runs (id, agent_id, provider, adapter_type, status, error_code) "
                "VALUES (?, 'agent-1', 'claude-code', 'subscription_cli', 'failed', 'subscription_cli_not_found')",
                (f"r-{i}",),
            )
        conn.commit()

    profiles = [
        {"id": "codex_subscription", "adapter_type": "subscription_cli"},
        {"id": "openai_api", "adapter_type": "openai_api"},
    ]
    demoted = hiring_economics.demoted_profile_ids(db, profiles)

    assert demoted == {"codex_subscription"}


def test_choose_adapter_demotes_but_never_excludes() -> None:
    from aiteam.project_adapters import choose_adapter_for_role

    profiles = [
        {"id": "codex_subscription", "adapter_type": "subscription_cli", "channel": "subscription", "status": "connected"},
        {"id": "openai_api", "adapter_type": "openai_api", "channel": "api", "status": "connected"},
    ]

    # Sin demote: el scoring normal decide (subscription suele ganar para engineer).
    baseline = choose_adapter_for_role("engineer", None, list(profiles))
    assert baseline is not None

    # Con el ganador demotado: el otro pasa delante.
    demoted = choose_adapter_for_role(
        "engineer", None, list(profiles),
        demoted_profile_ids={str(baseline.get("adapter_profile_id") or baseline["adapter_config"].get("profile_id"))},
    )
    assert demoted is not None
    assert demoted["adapter_type"] != baseline["adapter_type"]

    # Si TODOS están demotados, se sigue eligiendo uno (nunca excluir).
    all_demoted = choose_adapter_for_role(
        "engineer", None, list(profiles),
        demoted_profile_ids={"codex_subscription", "openai_api"},
    )
    assert all_demoted is not None
