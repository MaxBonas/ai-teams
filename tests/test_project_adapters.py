from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import (
    _profile_score, choose_adapter_for_role,
    ensure_quorum_agents, ensure_tier3_agents, reconcile_project_agent_policy,
)
from aiteam.user_config import record_model_health


def _init_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, capabilities_json) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("role:lead", "lead", "Team Lead", "lead", "lead_builtin", "[]"),
                ("role:engineer", "engineer", "Engineer", "standard", "role_builtin", "[]"),
                ("role:reviewer", "reviewer", "Reviewer", "senior", "subscription_cli", '["repo_read"]'),
            ],
        )
        conn.commit()


def test_reconcile_project_agent_policy_repairs_builtin_agents_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        record_model_health("openai_api", model, available=True, reason="test fixture")
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["openai_api"]}),
        encoding="utf-8",
    )

    repaired = reconcile_project_agent_policy(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["id"]: dict(row)
            for row in conn.execute(
                "SELECT id, role, adapter_type, adapter_config_json, capabilities_json, supervisor_agent_id FROM agents"
            )
        }

    assert set(repaired) == {
        "role:lead", "role:engineer", "role:reviewer",
        "role:file_scout", "role:web_scout", "role:context_curator",
    }
    assert rows["role:lead"]["adapter_type"] == "openai_api"
    assert json.loads(rows["role:lead"]["adapter_config_json"])["model"] == "gpt-5.6-sol"
    assert "skill_run" in json.loads(rows["role:lead"]["capabilities_json"])
    assert rows["role:engineer"]["adapter_type"] == "openai_api"
    assert json.loads(rows["role:engineer"]["adapter_config_json"])["model"] == "gpt-5.6-terra"
    assert rows["role:engineer"]["supervisor_agent_id"] == "role:lead"
    assert "repo_write" in json.loads(rows["role:engineer"]["capabilities_json"])
    assert rows["role:reviewer"]["adapter_type"] == "subscription_cli"
    assert rows["role:reviewer"]["supervisor_agent_id"] == "role:lead"


def test_reconcile_repairs_missing_profile_with_governed_model(tmp_path: Path, monkeypatch) -> None:
    """Live bug: one agent's adapter_config was {"model": "gpt-5.4"} with no
    profile_id — the subscription_cli runtime fell back to its default binary
    ('claude', not installed) and racked up 89 straight failed runs while
    every sibling carried codex_subscription."""
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health("codex_subscription", "gpt-5.5", available=True, reason="test")
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("role:lead", "lead", "Lead", "lead", "subscription_cli",
                 json.dumps({"profile_id": "codex_subscription", "model": "gpt-5.5"}), '["skill_run"]'),
                ("role:engineer", "engineer", "Engineer", "standard", "subscription_cli",
                 json.dumps({"model": "gpt-5.4"}), '["repo_write"]'),
            ],
        )
        conn.commit()
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription"]}),
        encoding="utf-8",
    )

    repaired = reconcile_project_agent_policy(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        config = json.loads(conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id = 'role:engineer'"
        ).fetchone()["adapter_config_json"])
        lead_config = json.loads(conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id = 'role:lead'"
        ).fetchone()["adapter_config_json"])

    assert "role:engineer" in repaired
    assert config.get("profile_id") == "codex_subscription"
    assert config.get("model") == "gpt-5.5"  # modelo legacy fuera de catálogo sustituido por uno verificado
    assert lead_config.get("model") == "gpt-5.5"  # healthy config untouched


def test_reconcile_does_not_restore_profile_outside_drifted_allowlist(tmp_path: Path) -> None:
    """The live variant that the selection-based repair missed: the project
    allowlist only lists openai_api (drift) while the whole team actually runs
    subscription_cli/codex. Reintroducing Codex would bypass project policy;
    preserve the invalid row so the runtime preflight can block and diagnose."""
    db_path = tmp_path / "aiteam.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("role:lead", "lead", "Lead", "lead", "subscription_cli",
                 json.dumps({"profile_id": "codex_subscription", "model": "gpt-5.5"}), '["skill_run"]'),
                ("role:engineer", "engineer", "Engineer", "standard", "subscription_cli",
                 json.dumps({"model": "gpt-5.4"}), '["repo_write"]'),
            ],
        )
        conn.commit()
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["openai_api"]}),  # drifted
        encoding="utf-8",
    )

    reconcile_project_agent_policy(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT adapter_type, adapter_config_json FROM agents WHERE id = 'role:engineer'"
        ).fetchone()
    config = json.loads(row["adapter_config_json"])
    assert row["adapter_type"] == "subscription_cli"
    assert config.get("profile_id") is None
    assert config.get("model") == "gpt-5.4"


# ---------------------------------------------------------------------------
# _profile_score — junior role scoring prefers CLI over API-only
# ---------------------------------------------------------------------------


def _make_profile(adapter_type: str, channel: str = "api", health_status: str = "ok") -> dict:
    return {
        "id": adapter_type,
        "adapter_type": adapter_type,
        "provider": adapter_type.split("_")[0],
        "channel": channel,
        "health": {"status": health_status},
    }


class TestProfileScore:
    def test_transport_does_not_change_junior_score_by_itself(self):
        cli = _make_profile("subscription_cli", channel="subscription")
        api = _make_profile("openai_api", channel="api")
        assert _profile_score(cli, needs_senior=False) == _profile_score(api, needs_senior=False)

    def test_openai_api_is_not_penalized_for_junior_roles(self):
        api = _make_profile("openai_api", channel="api")
        score = _profile_score(api, needs_senior=False)
        assert score == 40

    def test_subscription_cli_not_penalized_for_senior(self):
        """CLI adapter should still work for senior roles (no penalty there)."""
        cli = _make_profile("subscription_cli", channel="subscription")
        score = _profile_score(cli, needs_senior=True)
        # health=ok gives 40; no penalty for senior
        assert score >= 40

    def test_openai_api_preferred_for_senior_roles(self):
        """Senior roles still prefer API adapters."""
        cli = _make_profile("subscription_cli", channel="subscription")
        api = _make_profile("openai_api", channel="api")
        assert _profile_score(api, needs_senior=True) > _profile_score(cli, needs_senior=True)

    def test_choose_adapter_for_role_preserves_rank_when_both_can_engineer(self):
        profiles = [
            _make_profile("openai_api", channel="api"),
            _make_profile("subscription_cli", channel="subscription"),
        ]
        result = choose_adapter_for_role("engineer", "standard", profiles)
        assert result is not None
        assert result["adapter_type"] == "openai_api"

    def test_choose_adapter_for_role_still_uses_openai_for_lead(self):
        """Lead (senior) still picks openai_api when it's available."""
        profiles = [
            _make_profile("openai_api", channel="api"),
            _make_profile("subscription_cli", channel="subscription"),
        ]
        result = choose_adapter_for_role("lead", "lead", profiles)
        assert result is not None
        assert result["adapter_type"] == "openai_api"

    def test_codex_context_curator_uses_calibrated_premium_model(self):
        profile = {
            **_make_profile("subscription_cli", channel="subscription"),
            "id": "codex_subscription",
            "provider": "openai-codex",
        }
        result = choose_adapter_for_role("context_curator", "cheap", [profile])
        assert result is not None
        assert result["model"] == "gpt-5.5"

    @pytest.mark.parametrize(
        ("profile_id", "role", "expected"),
        [
            ("codex_subscription", "lead", "gpt-5.6-sol"),
            ("codex_subscription", "engineer", "gpt-5.6-terra"),
            ("codex_subscription", "file_scout", "gpt-5.6-luna"),
            ("openai_api", "lead", "gpt-5.6-sol"),
            ("openai_api", "reviewer", "gpt-5.6-terra"),
            ("openai_api", "web_scout", "gpt-5.6-luna"),
            ("anthropic_api", "lead", "claude-opus-4-8"),
            ("anthropic_api", "architect", "claude-opus-4-8"),
            ("anthropic_api", "engineer", "claude-sonnet-5"),
            ("anthropic_api", "context_curator", "claude-haiku-4-5"),
            ("gemini_api", "lead", "gemini-3.1-pro-preview"),
            ("gemini_api", "engineer", "gemini-3.5-flash"),
            ("gemini_api", "file_scout", "gemini-3.1-flash-lite"),
            ("antigravity_subscription", "lead", "gemini-3.1-pro-high"),
            ("antigravity_subscription", "engineer", "claude-sonnet-4-6"),
            ("antigravity_subscription", "reviewer", "gemini-3.5-flash-high"),
            ("antigravity_subscription", "web_scout", "gemini-3.5-flash-low"),
        ],
    )
    def test_role_tier_selects_current_model(self, profile_id, role, expected):
        profile = {
            **_make_profile("subscription_cli" if "subscription" in profile_id else profile_id),
            "id": profile_id,
            "channel": "subscription" if "subscription" in profile_id else "api",
        }
        result = choose_adapter_for_role(role, None, [profile])
        assert result is not None
        assert result["model"] == expected

    def test_local_profile_keeps_owner_configured_installed_model(self):
        profile = {
            **_make_profile("subscription_cli", channel="local"),
            "id": "local_qwen_ollama",
            "provider": "ollama",
            "config": {"model": "qwen2.5-coder:32b"},
        }
        result = choose_adapter_for_role("engineer", "standard", [profile])
        assert result is not None
        assert result["model"] == "qwen2.5-coder:32b"

    def test_hiring_skips_profile_with_no_executable_models(self):
        unavailable_local = {
            **_make_profile("subscription_cli", channel="local"),
            "id": "local_gem4_lmstudio",
            "provider": "lmstudio",
            "config": {"model": "google/gemma-4-26b-a4b"},
            "model_options": [{
                "value": "google/gemma-4-26b-a4b",
                "available": False,
                "availability_reason": "runtime no verificado",
            }],
        }
        available_api = {
            **_make_profile("openai_api", channel="api"),
            "id": "openai_api",
            "model_options": [{
                "value": "gpt-5.6-terra",
                "available": True,
            }],
        }

        result = choose_adapter_for_role("engineer", "standard", [unavailable_local, available_api])

        assert result is not None
        assert result["adapter_profile_id"] == "openai_api"
        assert result["model"] == "gpt-5.6-terra"

    def test_hiring_returns_none_when_every_profile_model_is_unavailable(self):
        profile = {
            **_make_profile("subscription_cli", channel="local"),
            "id": "local_gem4_lmstudio",
            "model_options": [{"value": "gemma-3-4b-it", "available": False}],
        }

        assert choose_adapter_for_role("engineer", "standard", [profile]) is None


# ── ensure_quorum_agents ──────────────────────────────────────────────────────

def _init_quorum_db(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, capabilities_json) "
            "VALUES ('role:lead', 'lead', 'Lead', 'lead', 'openai_api', '[]')"
        )
        conn.commit()


class TestEnsureQuorumAgents:
    def test_assigns_distinct_providers_when_available(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [
            {**_make_profile("subscription_cli", channel="subscription"),
             "id": "codex_subscription", "provider": "openai-codex"},
            {**_make_profile("anthropic_sonnet", channel="api"),
             "id": "anthropic_api", "provider": "anthropic"},
        ]

        ensure_quorum_agents(db, profiles=profiles)

        with sqlite3.connect(str(db)) as conn:
            configs = [
                json.loads(row[0]) for row in conn.execute(
                    "SELECT adapter_config_json FROM agents "
                    "WHERE role='quorum_auditor' ORDER BY id"
                ).fetchall()
            ]
        assert {config["profile_id"] for config in configs} == {
            "codex_subscription", "anthropic_api"
        }

    def test_creates_both_auditors_when_absent(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [_make_profile("openai_api", channel="api")]

        created = ensure_quorum_agents(db, profiles=profiles)

        assert "role:quorum_auditor_1" in created
        assert "role:quorum_auditor_2" in created
        with sqlite3.connect(str(db)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert "role:quorum_auditor_1" in ids
        assert "role:quorum_auditor_2" in ids

    def test_idempotent_on_second_call(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [_make_profile("openai_api", channel="api")]

        ensure_quorum_agents(db, profiles=profiles)
        created_second = ensure_quorum_agents(db, profiles=profiles)

        assert created_second == []  # nothing created the second time

    def test_auditors_assigned_to_lead_supervisor(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [_make_profile("openai_api", channel="api")]

        ensure_quorum_agents(db, profiles=profiles)

        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                r["id"]: dict(r)
                for r in conn.execute(
                    "SELECT id, supervisor_agent_id, role, seniority FROM agents"
                ).fetchall()
            }
        assert rows["role:quorum_auditor_1"]["supervisor_agent_id"] == "role:lead"
        assert rows["role:quorum_auditor_2"]["supervisor_agent_id"] == "role:lead"
        assert rows["role:quorum_auditor_1"]["seniority"] == "senior"

    def test_works_with_no_profiles(self, tmp_path: Path):
        """When no adapter profiles exist, still creates agents with openai_api default."""
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)

        created = ensure_quorum_agents(db, profiles=[])

        assert "role:quorum_auditor_1" in created
        assert "role:quorum_auditor_2" in created


# ── POST /api/issues triggers quorum bootstrap ────────────────────────────────

class TestIssueApiQuorumBootstrap:
    """When a lead_quorum issue is created via the API, quorum agents are auto-created."""

    def _setup(self, tmp_path: Path) -> Path:
        import api.utils as utils
        # resolve_runtime_dir returns workspace/.aiteam (not workspace/runtime)
        # when the workspace differs from project_root — use .aiteam directly
        # so the path is stable and no directory rename is triggered.
        db_path = tmp_path / ".aiteam" / "aiteam.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO agents (id, role, name, seniority, adapter_type, capabilities_json) "
                "VALUES ('role:lead', 'lead', 'Lead', 'lead', 'openai_api', '[]')"
            )
            conn.execute(
                "INSERT INTO goals (id, title) VALUES ('g1', 'Goal')"
            )
            conn.commit()
        utils.set_current_workspace(tmp_path)
        return db_path

    def test_quorum_agents_created_on_lead_quorum_issue(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from api.main import app
        db_path = self._setup(tmp_path)

        client = TestClient(app)
        resp = client.post("/api/issues", json={
            "title": "Plan the project",
            "role": "lead",
            "status": "todo",
            "assignee_agent_id": "role:lead",
            "goal_id": "g1",
            "metadata": {"profile": "lead_quorum"},
        })

        assert resp.status_code == 200
        with sqlite3.connect(str(db_path)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert "role:quorum_auditor_1" in ids
        assert "role:quorum_auditor_2" in ids

    def test_no_quorum_agents_for_full_team_issue(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from api.main import app
        db_path = self._setup(tmp_path)

        client = TestClient(app)
        client.post("/api/issues", json={
            "title": "Plan the project",
            "role": "lead",
            "status": "todo",
            "assignee_agent_id": "role:lead",
            "goal_id": "g1",
            "metadata": {"profile": "full_team"},
        })

        with sqlite3.connect(str(db_path)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert "role:quorum_auditor_1" not in ids

    def test_team_panel_quorum_reconcile_creates_canonical_agents_idempotently(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from api.main import app
        self._setup(tmp_path)
        client = TestClient(app)

        first = client.post("/api/agents/quorum/reconcile")
        second = client.post("/api/agents/quorum/reconcile")

        assert first.status_code == 200
        assert first.json()["created_agent_ids"] == [
            "role:quorum_auditor_1", "role:quorum_auditor_2"
        ]
        assert {agent["id"] for agent in first.json()["agents"]} == {
            "role:quorum_auditor_1", "role:quorum_auditor_2"
        }
        assert second.status_code == 200
        assert second.json()["created_agent_ids"] == []


# ── ensure_tier3_agents ───────────────────────────────────────────────────────

class TestEnsureTier3Agents:
    def test_creates_all_three_scouts_when_absent(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)  # reuse helper — creates schema + lead agent
        profiles = [_make_profile("openai_api", channel="api")]

        created = ensure_tier3_agents(db, profiles=profiles)

        assert "role:file_scout" in created
        assert "role:web_scout" in created
        assert "role:context_curator" in created
        with sqlite3.connect(str(db)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert "role:file_scout" in ids
        assert "role:web_scout" in ids
        assert "role:context_curator" in ids

    def test_idempotent_on_second_call(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [_make_profile("openai_api", channel="api")]

        ensure_tier3_agents(db, profiles=profiles)
        created_second = ensure_tier3_agents(db, profiles=profiles)

        assert created_second == []

    def test_scouts_assigned_to_lead_supervisor(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [_make_profile("openai_api", channel="api")]

        ensure_tier3_agents(db, profiles=profiles)

        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                r["id"]: dict(r)
                for r in conn.execute(
                    "SELECT id, supervisor_agent_id, role, seniority FROM agents"
                ).fetchall()
            }
        assert rows["role:file_scout"]["supervisor_agent_id"] == "role:lead"
        assert rows["role:web_scout"]["supervisor_agent_id"] == "role:lead"
        assert rows["role:context_curator"]["supervisor_agent_id"] == "role:lead"
        assert rows["role:file_scout"]["seniority"] == "cheap"

    def test_reconcile_creates_tier3_agents_automatically(self, tmp_path: Path):
        """reconcile_project_agent_policy calls ensure_tier3_agents internally."""
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        (tmp_path / "project_config.json").write_text(
            json.dumps({"version": 1, "adapter_profile_ids": ["openai_api"]}),
            encoding="utf-8",
        )

        reconcile_project_agent_policy(db)

        with sqlite3.connect(str(db)) as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM agents").fetchall()}
        assert "role:file_scout" in ids
        assert "role:web_scout" in ids
        assert "role:context_curator" in ids
