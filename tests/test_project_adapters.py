from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import (
    _profile_score, choose_adapter_for_role,
    ensure_quorum_agents, ensure_tier3_agents, reconcile_project_agent_policy,
)


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


def test_reconcile_project_agent_policy_repairs_builtin_agents_only(tmp_path: Path) -> None:
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
    assert json.loads(rows["role:lead"]["adapter_config_json"])["model"] == "gpt-4.1"
    assert "skill_run" in json.loads(rows["role:lead"]["capabilities_json"])
    assert rows["role:engineer"]["adapter_type"] == "openai_api"
    assert json.loads(rows["role:engineer"]["adapter_config_json"])["model"] == "o4-mini"
    assert rows["role:engineer"]["supervisor_agent_id"] == "role:lead"
    assert "repo_write" in json.loads(rows["role:engineer"]["capabilities_json"])
    assert rows["role:reviewer"]["adapter_type"] == "subscription_cli"
    assert rows["role:reviewer"]["supervisor_agent_id"] == "role:lead"


def test_reconcile_repairs_subscription_cli_missing_profile_id(tmp_path: Path) -> None:
    """Live bug: one agent's adapter_config was {"model": "gpt-5.4"} with no
    profile_id — the subscription_cli runtime fell back to its default binary
    ('claude', not installed) and racked up 89 straight failed runs while
    every sibling carried codex_subscription."""
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
    assert config.get("model") == "gpt-5.4"  # explicit model choice preserved
    assert lead_config.get("model") == "gpt-5.5"  # healthy config untouched


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
    def test_subscription_cli_scores_higher_than_openai_api_for_junior(self):
        cli = _make_profile("subscription_cli", channel="subscription")
        api = _make_profile("openai_api", channel="api")
        assert _profile_score(cli, needs_senior=False) > _profile_score(api, needs_senior=False)

    def test_openai_api_penalized_for_junior_roles(self):
        api = _make_profile("openai_api", channel="api")
        score = _profile_score(api, needs_senior=False)
        # With health="ok" (40 pts) but API-only penalty (-30) the score must be < 40
        assert score < 40

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

    def test_choose_adapter_for_role_prefers_cli_for_engineer(self):
        """choose_adapter_for_role picks subscription_cli over openai_api for engineer."""
        profiles = [
            _make_profile("openai_api", channel="api"),
            _make_profile("subscription_cli", channel="subscription"),
        ]
        result = choose_adapter_for_role("engineer", "standard", profiles)
        assert result is not None
        assert result["adapter_type"] == "subscription_cli"

    def test_choose_adapter_for_role_still_uses_openai_for_lead(self):
        """Lead (senior) still picks openai_api when it's available."""
        profiles = [
            _make_profile("openai_api", channel="api"),
            _make_profile("subscription_cli", channel="subscription"),
        ]
        result = choose_adapter_for_role("lead", "lead", profiles)
        assert result is not None
        assert result["adapter_type"] == "openai_api"


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
