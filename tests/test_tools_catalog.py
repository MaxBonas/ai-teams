"""Tests for aiteam.tools.catalog — capability catalog and per-agent checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiteam.tools.catalog import (
    CAPABILITY_CATALOG,
    check_capability,
    default_capabilities_for_role,
    get_agent_capabilities,
)


# ── Catalog structure ────────────────────────────────────────────────────────


def test_catalog_has_expected_keys():
    expected = {
        "repo_read", "repo_write",
        "lsp_symbols", "lsp_references",
        "test_execute", "build_execute",
        "browser_nav", "browser_test",
        "external_mcp", "skill_run",
    }
    assert expected == set(CAPABILITY_CATALOG.keys())


def test_catalog_entries_have_required_fields():
    for key, entry in CAPABILITY_CATALOG.items():
        assert "description" in entry, key
        assert "tool_family" in entry, key
        assert "label" in entry, key


# ── get_agent_capabilities ───────────────────────────────────────────────────


def test_capabilities_from_json_string():
    agent = {"capabilities_json": '["repo_read", "test_execute"]'}
    assert get_agent_capabilities(agent) == ["repo_read", "test_execute"]


def test_capabilities_from_list():
    agent = {"capabilities": ["repo_read", "browser_nav"]}
    result = get_agent_capabilities(agent)
    assert "repo_read" in result
    assert "browser_nav" in result


def test_capabilities_unknown_filtered_out():
    agent = {"capabilities_json": '["repo_read", "fly_to_moon", "test_execute"]'}
    result = get_agent_capabilities(agent)
    assert result == ["repo_read", "test_execute"]


def test_capabilities_empty_agent():
    result = get_agent_capabilities({})
    assert result == []


def test_capabilities_invalid_json():
    agent = {"capabilities_json": "not json"}
    assert get_agent_capabilities(agent) == []


# ── check_capability ─────────────────────────────────────────────────────────


def test_check_capability_allowed():
    agent = {"capabilities_json": '["repo_read", "test_execute"]'}
    allowed, reason = check_capability(agent, "repo_read")
    assert allowed
    assert "granted" in reason


def test_check_capability_denied():
    agent = {"capabilities_json": '["repo_read"]'}
    allowed, reason = check_capability(agent, "browser_nav")
    assert not allowed
    assert "browser_nav" in reason


def test_check_capability_empty():
    allowed, reason = check_capability({}, "repo_read")
    assert not allowed


# ── default_capabilities_for_role ────────────────────────────────────────────


def test_default_engineer():
    caps = default_capabilities_for_role("engineer")
    assert "repo_read" in caps
    assert "repo_write" in caps
    assert "test_execute" in caps


def test_default_reviewer():
    caps = default_capabilities_for_role("reviewer")
    assert "repo_read" in caps
    assert "repo_write" not in caps


def test_default_worker_is_explicitly_read_only():
    assert default_capabilities_for_role("worker") == ["repo_read"]


def test_tier2_specialists_have_explicit_contract_capabilities():
    assert default_capabilities_for_role("test_designer") == [
        "repo_read", "repo_write", "lsp_symbols", "lsp_references",
    ]
    assert default_capabilities_for_role("mcp_operator") == [
        "repo_read", "external_mcp", "skill_run",
    ]


def test_default_qa():
    caps = default_capabilities_for_role("qa")
    assert "browser_test" in caps
    assert "repo_write" in caps


def test_default_unknown_role():
    caps = default_capabilities_for_role("unknown_role_xyz")
    # Falls back to ["repo_read"]
    assert caps == ["repo_read"]


# ── Integration: update_agent persists capabilities ──────────────────────────

import sqlite3


def _init_db(db_path: Path) -> None:
    from aiteam.db.migration import SCHEMA_PATH
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


def test_update_agent_capabilities(tmp_path):
    from aiteam.db.agents import create_agent, update_agent

    db = tmp_path / "test.db"
    _init_db(db)

    agent = create_agent(db, role="engineer", name="Eng", agent_id="role:engineer")
    assert agent["capabilities_json"] == "[]"

    updated = update_agent(db, agent_id="role:engineer", capabilities=["repo_read", "test_execute"])
    assert updated is not None
    caps = json.loads(updated["capabilities_json"])
    assert "repo_read" in caps
    assert "test_execute" in caps


def test_update_agent_capabilities_empty_list(tmp_path):
    from aiteam.db.agents import create_agent, update_agent

    db = tmp_path / "test.db"
    _init_db(db)

    create_agent(db, role="lead", name="Lead", agent_id="role:lead",
                 capabilities=["repo_read", "skill_run"])
    updated = update_agent(db, agent_id="role:lead", capabilities=[])
    assert json.loads(updated["capabilities_json"]) == []
