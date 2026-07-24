from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import aiteam.project_adapters as project_adapters
from aiteam.db.migration import SCHEMA_PATH
from aiteam.project_adapters import (
    _profile_score, choose_adapter_for_role,
    choose_adapter_for_new_slot,
    ensure_quorum_agents, ensure_tier3_agents, reconcile_project_agent_policy,
)
from aiteam.user_config import (
    DEFAULT_ADAPTER_PROFILES,
    MODEL_OPTIONS_BY_PROFILE,
    record_model_health,
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
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    record_model_health("codex_subscription", "gpt-5.6-terra", available=True, reason="test")
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
    assert config.get("model") == "gpt-5.6-terra"  # modelo legacy fuera de catálogo sustituido por uno verificado
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


def test_reconcile_preserves_explicit_owner_selection_intent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health("codex_subscription", "gpt-5.6-terra", available=True, reason="test")
    db_path = tmp_path / "aiteam.db"
    explicit = {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "selection_intent": {
            "schema_version": "model_selection_intent_v1",
            "mode": "owner_explicit",
            "source": "model_role_selector",
            "candidate_id": "model-candidate:fixture",
        },
    }
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("role:lead", "lead", "Lead", "lead", "subscription_cli",
                 json.dumps(explicit), '["skill_run"]', None),
                ("role:reviewer", "reviewer", "Reviewer", "senior", "subscription_cli",
                 json.dumps(explicit), '["repo_read"]', "role:lead"),
            ],
        )
        conn.commit()
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription"]}),
        encoding="utf-8",
    )

    reconcile_project_agent_policy(db_path, include_tier3=False)

    with sqlite3.connect(str(db_path)) as conn:
        stored = json.loads(conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id = 'role:reviewer'"
        ).fetchone()[0])
    assert stored == explicit


def test_reconcile_preserves_governed_default_selection_intent_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_USER_CONFIG_DIR", str(tmp_path / "user-config"))
    record_model_health("codex_subscription", "gpt-5.6-terra", available=True, reason="test")
    db_path = tmp_path / "aiteam.db"
    governed = {
        "profile_id": "codex_subscription",
        "model": "gpt-5.6-terra",
        "selection_intent": {
            "schema_version": "model_selection_intent_v1",
            "mode": "default",
            "source": "model_default_rollout_v1",
            "candidate_id": "model-candidate:fixture",
            "snapshot_id": "snapshot:fixture",
            "snapshot_hash": "hash:fixture",
        },
    }
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executemany(
            "INSERT INTO agents (id, role, name, seniority, adapter_type, adapter_config_json, capabilities_json, supervisor_agent_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("role:lead", "lead", "Lead", "lead", "subscription_cli",
                 json.dumps(governed), '["skill_run"]', None),
                ("role:reviewer", "reviewer", "Reviewer", "senior", "subscription_cli",
                 json.dumps(governed), '["repo_read"]', "role:lead"),
            ],
        )
        conn.commit()
    (tmp_path / "project_config.json").write_text(
        json.dumps({"version": 1, "adapter_profile_ids": ["codex_subscription"]}),
        encoding="utf-8",
    )

    reconcile_project_agent_policy(db_path, include_tier3=False)

    with sqlite3.connect(db_path) as conn:
        stored = json.loads(conn.execute(
            "SELECT adapter_config_json FROM agents WHERE id = 'role:reviewer'"
        ).fetchone()[0])
    assert stored == governed


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

    def test_codex_context_curator_uses_calibrated_budget_model(self):
        profile = {
            **_make_profile("subscription_cli", channel="subscription"),
            "id": "codex_subscription",
            "provider": "openai-codex",
        }
        result = choose_adapter_for_role("context_curator", "cheap", [profile])
        assert result is not None
        assert result["model"] == "gpt-5.6-luna"
        assert result["adapter_config"]["model_reasoning_effort"] == "medium"

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
            ("gemini_api", "engineer", "gemini-3.6-flash"),
            ("gemini_api", "file_scout", "gemini-3.5-flash-lite"),
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

    @pytest.mark.parametrize(
        "profile_id",
        [
            "antigravity_subscription",
            "openai_api",
            "gemini_api",
            "gemini_api_free",
            "groq_api_free",
            "anthropic_api",
        ],
    )
    def test_web_scout_rejects_builtin_profiles_without_governed_mcp(
        self, profile_id
    ):
        profile = next(
            item for item in DEFAULT_ADAPTER_PROFILES
            if item["id"] == profile_id
        )
        hydrated = {
            **profile,
            "model_options": [
                {**option, "available": True}
                for option in MODEL_OPTIONS_BY_PROFILE[profile_id]
            ],
        }

        assert choose_adapter_for_role(
            "web_scout", "cheap", [hydrated], data_class="public"
        ) is None

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


def _rollout_projection(profiles: list[dict], *, role: str, winner: bool) -> dict:
    candidates = []
    for rank, profile in enumerate(profiles, start=1):
        profile_id = str(profile["id"])
        candidate_id = f"candidate:{profile_id}:{role}"
        candidates.append({
            "candidate_id": candidate_id,
            "identity": {
                "profile_id": profile_id,
                "model_id": f"{profile_id}-{role}-model",
            },
            "rank": rank,
            "selection_reason": "hermetic_rollout_canary",
            "selection_score": {
                "score_version": "model_role_score_v1",
                "score": 90 - rank,
                "auto_eligible": winner and rank == 1,
                "hard_gates": {"calibrated": {"passed": winner and rank == 1}},
            },
        })
    winner_id = candidates[0]["candidate_id"] if winner and candidates else None
    return {
        "schema_version": "model_catalog_read_model_v1",
        "score_version": "model_role_score_v1",
        "canonical_role": role,
        "default": {"candidate_id": winner_id},
        "candidates": candidates,
    }


class TestEnsureQuorumAgents:
    def test_automatic_quorum_selection_inherits_issue_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO goals (id, title) VALUES ('goal:q', 'Quorum')")
            conn.execute(
                """
                INSERT INTO issues (
                    id, goal_id, title, status, criticality, metadata_json
                ) VALUES ('issue:q', 'goal:q', 'Auditar', 'todo', 'high', ?)
                """,
                (json.dumps({
                    "profile": "lead_quorum",
                    "data_class": "internal",
                    "required_capabilities": ["external_mcp"],
                }),),
            )
        observed: list[dict] = []

        def choose(*args, **kwargs):
            observed.append(kwargs)
            return project_adapters._unresolved_model_default(
                "no_auto_eligible_candidate"
            )

        monkeypatch.setattr(project_adapters, "choose_adapter_for_new_slot", choose)

        ensure_quorum_agents(db, profiles=[], issue_id="issue:q")

        assert len(observed) == 2
        assert {item["issue_id"] for item in observed} == {"issue:q"}
        assert {item["run_profile"] for item in observed} == {"lead_quorum"}
        assert {item["criticality"] for item in observed} == {"high"}
        assert {item["data_class"] for item in observed} == {"internal"}
        assert {
            tuple(item["required_capabilities"]) for item in observed
        } == {("external_mcp",)}

    def test_auto_quorum_canary_persists_two_diverse_applied_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [
            {**_make_profile("subscription_cli", channel="subscription"),
             "id": "codex_subscription", "provider": "openai-codex"},
            {**_make_profile("anthropic_sonnet", channel="api"),
             "id": "anthropic_api", "provider": "anthropic"},
        ]
        monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "auto")
        monkeypatch.setattr(
            "aiteam.model_selection_context.contextual_model_selection",
            lambda *args, **kwargs: _rollout_projection(
                kwargs["profiles"], role=str(kwargs["role"]), winner=True
            ),
        )

        ensure_quorum_agents(db, profiles=profiles)

        with sqlite3.connect(db) as conn:
            configs = [json.loads(row[0]) for row in conn.execute(
                "SELECT adapter_config_json FROM agents WHERE role='quorum_auditor' ORDER BY id"
            )]
            snapshots = conn.execute(
                "SELECT selection_scope, auto_applied FROM model_role_score_snapshots ORDER BY selection_scope"
            ).fetchall()
        assert {item["profile_id"] for item in configs} == {
            "codex_subscription", "anthropic_api"
        }
        assert all(item["selection_intent"]["mode"] == "default" for item in configs)
        assert snapshots == [
            ("quorum:new-agent:role:quorum_auditor_1", 1),
            ("quorum:new-agent:role:quorum_auditor_2", 1),
        ]

    def test_governed_defaults_preserve_perspective_diversity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [
            {
                **_make_profile("subscription_cli", channel="subscription"),
                "id": "codex_subscription",
                "provider": "openai-codex",
            },
            {
                **_make_profile("anthropic_sonnet", channel="api"),
                "id": "anthropic_api",
                "provider": "anthropic",
            },
        ]
        observed_profile_sets: list[list[str]] = []

        def governed(*args, **kwargs):
            candidates = kwargs["profiles"]
            observed_profile_sets.append([str(item["id"]) for item in candidates])
            profile = candidates[0]
            profile_id = str(profile["id"])
            model = f"{profile_id}-model"
            return {
                "adapter_type": profile["adapter_type"],
                "adapter_profile_id": profile_id,
                "adapter_config": {
                    "profile_id": profile_id,
                    "model": model,
                    "selection_intent": {
                        "schema_version": "model_selection_intent_v1",
                        "mode": "default",
                        "candidate_id": f"candidate:{profile_id}",
                    },
                },
                "model": model,
            }

        monkeypatch.setattr(project_adapters, "choose_adapter_for_new_slot", governed)

        ensure_quorum_agents(db, profiles=profiles)

        assert observed_profile_sets == [
            ["codex_subscription", "anthropic_api"],
            ["anthropic_api"],
        ]
        with sqlite3.connect(db) as conn:
            configs = [
                json.loads(row[0])
                for row in conn.execute(
                    "SELECT adapter_config_json FROM agents WHERE role='quorum_auditor' ORDER BY id"
                )
            ]
        assert {item["profile_id"] for item in configs} == {
            "codex_subscription",
            "anthropic_api",
        }
        assert {item["selection_intent"]["mode"] for item in configs} == {"default"}

    def test_auto_quorum_without_winner_never_invents_openai_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        monkeypatch.setattr(
            project_adapters,
            "choose_adapter_for_new_slot",
            lambda *args, **kwargs: project_adapters._unresolved_model_default(
                "no_auto_eligible_candidate"
            ),
        )

        ensure_quorum_agents(db, profiles=[])

        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents "
                "WHERE role='quorum_auditor' ORDER BY id"
            ).fetchall()
        assert [row[0] for row in rows] == ["role_builtin", "role_builtin"]
        assert all(
            json.loads(row[1])["model_default_rollout"]["state"] == "default_unresolved"
            for row in rows
        )

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

    def test_explicit_quorum_selection_persists_owner_intent_for_only_target(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profile = {
            **_make_profile("subscription_cli", channel="subscription"),
            "id": "codex_subscription",
            "provider": "openai-codex",
        }

        created = ensure_quorum_agents(
            db,
            profiles=[profile],
            explicit_selections={
                "role:quorum_auditor_1": {
                    "profile_id": "codex_subscription",
                    "model": "gpt-5.6-sol",
                    "candidate_id": "codex_subscription::gpt-5.6-sol",
                }
            },
            target_agent_ids=["role:quorum_auditor_1"],
        )

        assert created == ["role:quorum_auditor_1"]
        with sqlite3.connect(str(db)) as conn:
            raw = conn.execute(
                "SELECT adapter_config_json FROM agents WHERE id='role:quorum_auditor_1'"
            ).fetchone()[0]
            auditor_2 = conn.execute(
                "SELECT 1 FROM agents WHERE id='role:quorum_auditor_2'"
            ).fetchone()
        config = json.loads(raw)
        assert config["model"] == "gpt-5.6-sol"
        assert config["selection_intent"] == {
            "schema_version": "model_selection_intent_v1",
            "mode": "owner_explicit",
            "source": "model_role_selector",
            "candidate_id": "codex_subscription::gpt-5.6-sol",
        }
        assert auditor_2 is None

    def test_explicit_quorum_selection_rejects_duplicate_perspective(self, tmp_path: Path):
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [
            {
                **_make_profile("subscription_cli", channel="subscription"),
                "id": "codex_a",
                "provider": "openai-codex",
            },
            {
                **_make_profile("subscription_cli", channel="subscription"),
                "id": "codex_b",
                "provider": "openai-codex",
            },
        ]
        ensure_quorum_agents(
            db,
            profiles=profiles,
            explicit_selections={
                "role:quorum_auditor_1": {
                    "profile_id": "codex_a", "model": "gpt-a", "candidate_id": "a"
                }
            },
            target_agent_ids=["role:quorum_auditor_1"],
        )

        with pytest.raises(ValueError, match="perspective diversity"):
            ensure_quorum_agents(
                db,
                profiles=profiles,
                explicit_selections={
                    "role:quorum_auditor_2": {
                        "profile_id": "codex_b", "model": "gpt-b", "candidate_id": "b"
                    }
                },
                target_agent_ids=["role:quorum_auditor_2"],
            )

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
    def test_auto_tier3_no_winner_canary_persists_three_denied_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        profiles = [{
            **_make_profile("subscription_cli", channel="subscription"),
            "id": "codex_subscription",
            "provider": "openai-codex",
        }]
        monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "auto")
        monkeypatch.setattr(
            "aiteam.model_selection_context.contextual_model_selection",
            lambda *args, **kwargs: _rollout_projection(
                kwargs["profiles"], role=str(kwargs["role"]), winner=False
            ),
        )

        ensure_tier3_agents(db, profiles=profiles)

        with sqlite3.connect(db) as conn:
            agents = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents "
                "WHERE role IN ('file_scout', 'web_scout', 'context_curator') ORDER BY id"
            ).fetchall()
            snapshots = conn.execute(
                "SELECT selection_scope, auto_applied FROM model_role_score_snapshots ORDER BY selection_scope"
            ).fetchall()
        assert all(row[0] == "role_builtin" for row in agents)
        assert all(
            json.loads(row[1])["model_default_rollout"]["state"] == "default_unresolved"
            for row in agents
        )
        assert len(snapshots) == 3
        assert all(row[1] == 0 for row in snapshots)

    def test_auto_tier3_without_winner_stays_builtin_and_explained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = tmp_path / "aiteam.db"
        _init_quorum_db(db)
        scopes: list[str] = []

        def unresolved(*args, **kwargs):
            scopes.append(str(kwargs["selection_scope"]))
            return project_adapters._unresolved_model_default(
                "no_auto_eligible_candidate"
            )

        monkeypatch.setattr(project_adapters, "choose_adapter_for_new_slot", unresolved)

        ensure_tier3_agents(db, profiles=[])

        assert scopes == [
            "tier3:new-agent:role:file_scout",
            "tier3:new-agent:role:web_scout",
            "tier3:new-agent:role:context_curator",
        ]
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT adapter_type, adapter_config_json FROM agents "
                "WHERE role IN ('file_scout', 'web_scout', 'context_curator') ORDER BY id"
            ).fetchall()
        assert len(rows) == 3
        assert all(row[0] == "role_builtin" for row in rows)
        assert all(
            json.loads(row[1])["model_default_rollout"]["state"] == "default_unresolved"
            for row in rows
        )

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


def test_new_slot_recommend_records_but_preserves_legacy_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "recommend")
    monkeypatch.setattr(
        "aiteam.model_selection_context.contextual_model_selection",
        lambda *args, **kwargs: {"candidates": []},
    )
    observed: dict[str, str] = {}

    def no_assignment(*args, **kwargs):
        observed["rollout"] = kwargs["rollout"]
        return None

    monkeypatch.setattr(project_adapters, "select_model_default_for_new_slot", no_assignment)
    profile = {"id": "profile-a", "adapter_type": "subscription_cli"}

    selected = choose_adapter_for_new_slot(
        tmp_path / "aiteam.db",
        role="reviewer",
        seniority="senior",
        profiles=[profile],
        selection_scope="test:recommend",
    )

    assert observed == {"rollout": "recommend"}
    assert selected is not None
    assert selected["adapter_profile_id"] == "profile-a"


def test_new_slot_auto_uses_only_governed_winner_and_never_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "auto")
    monkeypatch.setattr(
        "aiteam.model_selection_context.contextual_model_selection",
        lambda *args, **kwargs: {"candidates": []},
    )
    governed = {
        "adapter_type": "subscription_cli",
        "adapter_profile_id": "profile-a",
        "adapter_config": {"profile_id": "profile-a", "model": "model-a"},
        "model": "model-a",
    }
    monkeypatch.setattr(
        project_adapters,
        "select_model_default_for_new_slot",
        lambda *args, **kwargs: governed,
    )
    profile = {"id": "profile-a", "adapter_type": "subscription_cli"}
    kwargs = {
        "db_path": tmp_path / "aiteam.db",
        "role": "reviewer",
        "seniority": "senior",
        "profiles": [profile],
        "selection_scope": "test:auto",
    }

    assert choose_adapter_for_new_slot(**kwargs) is governed
    monkeypatch.setattr(
        project_adapters,
        "select_model_default_for_new_slot",
        lambda *args, **kwargs: None,
    )
    unresolved = choose_adapter_for_new_slot(**kwargs)
    assert unresolved is not None
    assert unresolved["adapter_type"] == "role_builtin"
    assert unresolved["model"] is None
    assert unresolved["adapter_config"]["model_default_rollout"]["state"] == "default_unresolved"


def test_invalid_new_slot_rollout_rolls_back_to_shadow_without_new_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AITEAM_MODEL_DEFAULT_ROLLOUT", "invalid")
    monkeypatch.setattr(
        project_adapters,
        "select_model_default_for_new_slot",
        lambda *args, **kwargs: pytest.fail("shadow rollback must not apply a default"),
    )

    selected = choose_adapter_for_new_slot(
        tmp_path / "aiteam.db",
        role="reviewer",
        seniority="senior",
        profiles=[{"id": "profile-a", "adapter_type": "subscription_cli"}],
        selection_scope="test:rollback",
    )

    assert selected is not None
    assert selected["adapter_profile_id"] == "profile-a"


def test_reconcile_preserves_unresolved_auto_default_without_legacy_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    marker = {
        "model_default_rollout": {
            "schema_version": "model_default_rollout_v1",
            "mode": "auto",
            "state": "default_unresolved",
            "reason": "no_auto_eligible_candidate",
        }
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE agents SET adapter_config_json = ? WHERE id = 'role:engineer'",
            (json.dumps(marker, sort_keys=True),),
        )
        conn.commit()
    monkeypatch.setattr(
        project_adapters,
        "project_profiles",
        lambda runtime_dir: [{"id": "profile-a", "adapter_type": "subscription_cli"}],
    )

    reconcile_project_agent_policy(db_path, include_tier3=False)

    with sqlite3.connect(db_path) as conn:
        adapter_type, raw_config = conn.execute(
            "SELECT adapter_type, adapter_config_json FROM agents WHERE id = 'role:engineer'"
        ).fetchone()
    assert adapter_type == "role_builtin"
    assert json.loads(raw_config) == marker
