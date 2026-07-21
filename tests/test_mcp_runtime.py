from __future__ import annotations

import json
import sys
from pathlib import Path

from aiteam.extensions import (
    approve_mcp_server,
    approve_mcp_server_tools,
    list_mcp_servers,
    set_mcp_server_health,
    set_mcp_server_status,
)
from aiteam.mcp_runtime import (
    check_and_activate_mcp_server,
    mcp_servers_for_run,
    refresh_due_mcp_servers,
)


def _fake_server(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    script = tmp_path / "fake_mcp.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        f"result = {{'protocolVersion':'2025-06-18','capabilities':{{}},'serverInfo':{{'name':'fake','version':'{version}'}}}}\n"
        "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)\n"
        "json.loads(sys.stdin.readline())\n"
        "request = json.loads(sys.stdin.readline())\n"
        "tools=[{'name':'read_docs','annotations':{'readOnlyHint':True}},{'name':'publish_docs'}]\n"
        "print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{'tools':tools}}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    return script


def _approve(runtime_dir: Path, script: Path, *, version: str = "1.2.3") -> None:
    approve_mcp_server(
        runtime_dir,
        name="fake",
        source=sys.executable,
        version=version,
        args=[str(script)],
        applies_to_roles=["engineer"],
        approved_by="user",
    )


def test_real_initialize_promotes_pinned_server_to_active(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    _approve(runtime_dir, _fake_server(tmp_path))

    result = check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)

    assert result["status"] == "active"
    assert result["health"]["status"] == "ok"
    assert result["health"]["server_version"] == "1.2.3"
    assert result["health"]["tools"] == [
        {"name": "read_docs", "read_only": True},
        {"name": "publish_docs", "read_only": False},
    ]
    assert list_mcp_servers(runtime_dir)[0]["health"]["status"] == "ok"


def test_version_mismatch_fails_closed(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    _approve(runtime_dir, _fake_server(tmp_path, version="2.0.0"))

    result = check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)

    assert result["status"] == "failed"
    assert "version mismatch" in result["health"]["detail"]


def test_shell_install_source_is_never_launched(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    approve_mcp_server(
        runtime_dir,
        name="unsafe",
        source="npx -y unsafe-mcp@1.0.0",
        version="1.0.0",
        approved_by="user",
    )

    result = check_and_activate_mcp_server(runtime_dir, name="unsafe", timeout_sec=1)

    assert result["status"] == "failed"
    assert "shell commands are forbidden" in result["health"]["detail"]


def test_run_grant_requires_role_capability_and_current_health(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    _approve(runtime_dir, _fake_server(tmp_path))
    check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="engineer", capabilities=["external_mcp"]
    )
    assert grants == []
    assert denials == [{"name": "fake", "reason": "mcp_tools_not_owner_approved"}]

    approve_mcp_server_tools(
        runtime_dir,
        name="fake",
        tools=[
            {"name": "read_docs", "access": "read"},
            {"name": "publish_docs", "access": "write"},
        ],
        approved_by="user",
    )

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="engineer", capabilities=["external_mcp"]
    )
    assert denials == []
    assert grants[0]["name"] == "fake"
    assert grants[0]["version"] == "1.2.3"
    assert grants[0]["enabled_tools"] == ["read_docs"]
    assert grants[0]["denied_tools"] == ["publish_docs"]
    assert json.dumps(grants)  # transport remains JSON-serializable

    grants, denials = mcp_servers_for_run(runtime_dir, role="engineer", capabilities=[])
    assert grants == []
    assert denials == [{"name": "fake", "reason": "capability_not_granted:external_mcp"}]

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="reviewer", capabilities=["external_mcp"]
    )
    assert grants == [] and denials == []

    grants, denials = mcp_servers_for_run(
        runtime_dir,
        role="engineer",
        capabilities=["external_mcp", "repo_write"],
    )
    assert denials == []
    assert grants[0]["enabled_tools"] == ["read_docs", "publish_docs"]
    assert grants[0]["denied_tools"] == []


def test_owner_policy_not_server_hint_controls_read_access(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    _approve(runtime_dir, _fake_server(tmp_path))
    check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)
    approve_mcp_server_tools(
        runtime_dir,
        name="fake",
        tools=[{"name": "read_docs", "access": "write"}],
        approved_by="user",
    )

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="engineer", capabilities=["external_mcp"]
    )

    assert grants == []
    assert denials[0]["reason"] == "mcp_no_authorized_tools"
    decisions = {item["name"]: item for item in denials[0]["tool_decisions"]}
    assert decisions["read_docs"]["reason"] == "mcp_tool_write_not_granted"
    assert decisions["publish_docs"]["reason"] == "mcp_tool_not_owner_approved"


def test_expired_health_denies_grant(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    _approve(runtime_dir, _fake_server(tmp_path))
    check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)
    approve_mcp_server_tools(
        runtime_dir,
        name="fake",
        tools=[{"name": "read_docs", "access": "read"}],
        approved_by="user",
    )
    registry_path = runtime_dir / "extensions.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["mcp_servers"]["fake"]["health"]["checked_at"] = "2000-01-01T00:00:00+00:00"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="engineer", capabilities=["external_mcp"]
    )

    assert grants == []
    assert denials == [{"name": "fake", "reason": "mcp_health_expired"}]


def test_changed_script_invalidates_health_and_grant(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    script = _fake_server(tmp_path)
    _approve(runtime_dir, script)
    check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)
    approve_mcp_server_tools(
        runtime_dir,
        name="fake",
        tools=[{"name": "read_docs", "access": "read"}],
        approved_by="user",
    )
    script.write_text(script.read_text(encoding="utf-8") + "\n# replaced\n", encoding="utf-8")

    grants, denials = mcp_servers_for_run(
        runtime_dir, role="engineer", capabilities=["external_mcp"]
    )

    assert grants == []
    assert denials == [{"name": "fake", "reason": "mcp_artifact_changed"}]


def test_periodic_health_backs_off_and_retires_after_three_failures(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    script = _fake_server(tmp_path)
    _approve(runtime_dir, script)
    check_and_activate_mcp_server(runtime_dir, name="fake", timeout_sec=2)
    script.write_text("raise SystemExit(2)\n", encoding="utf-8")
    registry_path = runtime_dir / "extensions.json"

    for expected_failures in (1, 2, 3):
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["mcp_servers"]["fake"]["health"]["next_check_at"] = "2000-01-01T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        results = refresh_due_mcp_servers(runtime_dir, max_checks=1, timeout_sec=1)
        assert len(results) == 1
        assert results[0]["health"]["consecutive_failures"] == expected_failures

    entry = list_mcp_servers(runtime_dir)[0]
    assert entry["status"] == "retired"
    assert entry["health"]["retired_after_failures"] is True
    assert refresh_due_mcp_servers(runtime_dir, max_checks=1, timeout_sec=1) == []


def test_health_process_receives_only_declared_environment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    monkeypatch.setenv("DECLARED_TOKEN", "allowed")
    script = tmp_path / "env_mcp.py"
    script.write_text(
        "import json,os,sys\n"
        "r=json.loads(sys.stdin.readline())\n"
        "name='clean' if 'UNRELATED_SECRET' not in os.environ and os.environ.get('DECLARED_TOKEN')=='allowed' else 'leaked'\n"
        "result={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':name,'version':'1.0.0'}}\n"
        "print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':result}),flush=True)\n"
        "json.loads(sys.stdin.readline())\n"
        "r=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc':'2.0','id':r['id'],'result':{'tools':[{'name':'read','annotations':{'readOnlyHint':True}}]}}),flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    runtime_dir = tmp_path / ".aiteam"
    approve_mcp_server(
        runtime_dir,
        name="env-check",
        source=sys.executable,
        version="1.0.0",
        args=[str(script)],
        env_required=["DECLARED_TOKEN"],
        approved_by="user",
    )

    result = check_and_activate_mcp_server(runtime_dir, name="env-check", timeout_sec=2)

    assert result["health"]["status"] == "ok"
    assert result["health"]["server_name"] == "clean"


def test_approval_is_idempotent_per_runtime_contract_and_version(tmp_path: Path) -> None:
    runtime_dir = tmp_path / ".aiteam"
    script = _fake_server(tmp_path)
    _approve(runtime_dir, script)
    set_mcp_server_status(runtime_dir, name="fake", status="active")
    set_mcp_server_health(
        runtime_dir,
        name="fake",
        health={"status": "ok", "server_version": "1.2.3", "tools": [{"name": "read", "read_only": True}]},
    )

    _approve(runtime_dir, script)
    same = list_mcp_servers(runtime_dir)
    assert len(same) == 1
    assert same[0]["status"] == "active"
    assert same[0]["health"]["server_version"] == "1.2.3"

    _approve(runtime_dir, script, version="2.0.0")
    changed = list_mcp_servers(runtime_dir)
    assert len(changed) == 1
    assert changed[0]["status"] == "approved"
    assert "health" not in changed[0]
