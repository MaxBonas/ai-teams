"""Extension proposals (self-extension PR2): the Lead formally requests an
MCP server via the existing create_interaction op with
reason='extension_install_requested'. Installing third-party code is ALWAYS
a product decision — Tier 2/3 cannot propose, autonomy cannot auto-accept,
an incomplete payload is rejected before it ever reaches the owner.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.adapters.registry import AdapterDescriptor, AdapterRegistry, ExecutionResult
from aiteam.db.interactions import resolve_interaction
from aiteam.db.migration import SCHEMA_PATH
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.extensions import list_mcp_servers
from aiteam.heartbeat.executor import RunExecutor
from aiteam.heartbeat.scheduler import HeartbeatScheduler
from aiteam.policies import EXTENSION_PROPOSAL_REASON, operational_interaction_default

_VALID_PAYLOAD = {
    "reason": EXTENSION_PROPOSAL_REASON,
    "name": "Unity MCP",
    "source": "npx -y unity-mcp@1.2.0",
    "version": "1.2.0",
    "justification": "Reviewer cannot verify Play Mode from static YAML — 6 blocked review rounds.",
    "applies_to_roles": ["engineer", "reviewer"],
}

_CATALOG_PAYLOAD = {
    "reason": EXTENSION_PROPOSAL_REASON,
    "catalog_id": "github-readonly",
    "justification": "El reviewer necesita contrastar issues y pull requests sin escribir en GitHub.",
}


def _init_db(db_path: Path, *, proposer_role: str = "lead", proposer_id: str = "role:lead") -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("INSERT INTO goals (id, title) VALUES ('goal-1', 'Goal')")
        conn.execute(
            "INSERT INTO agents (id, role, name, seniority, adapter_type) VALUES (?, ?, ?, ?, ?)",
            (proposer_id, proposer_role, proposer_role.title(), "standard", "openai_api"),
        )
        conn.execute(
            "INSERT INTO issues (id, goal_id, title, status, role, assignee_agent_id)"
            " VALUES ('issue:intake', 'goal-1', 'Build', 'in_progress', ?, ?)",
            (proposer_role, proposer_id),
        )
        conn.commit()


class _ProposeRuntime:
    descriptor = AdapterDescriptor(adapter_type="openai_api", channel="api", provider="openai")

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def build_env(self, *, run_id: str, wake_context: dict[str, object]) -> dict[str, str]:
        return {}

    def execute(self, run: dict[str, Any], env: dict[str, str]) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            output="propuesta",
            actions={"interactions": [{
                "kind": "request_confirmation",
                "payload": self._payload,
                "title": "Proponer MCP: unity",
                "summary": "Habilita verificación real de Play Mode.",
            }]},
        )


def _dispatch(db_path: Path, *, agent_id: str) -> Any:
    enqueue_wakeup(
        db_path, agent_id=agent_id, source="test", reason="manual",
        payload={"issue_id": "issue:intake", "wake_reason": "manual"},
    )
    return HeartbeatScheduler(db_path).dispatch_next(agent_id=agent_id)


def _pending_interactions(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM issue_thread_interactions WHERE status='pending'").fetchall()
    return [dict(r) for r in rows]


def test_reason_excluded_from_autonomy_defaults() -> None:
    """Installing third-party code must never auto-accept, in any mode."""
    assert operational_interaction_default(EXTENSION_PROPOSAL_REASON) is None


def test_lead_proposal_creates_pending_interaction(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))

    executor.execute(_dispatch(db_path, agent_id="role:lead"))

    pending = _pending_interactions(db_path)
    assert len(pending) == 1
    payload = json.loads(pending[0]["payload_json"])
    assert payload["reason"] == EXTENSION_PROPOSAL_REASON
    assert payload["name"] == "Unity MCP"


def test_catalog_proposal_expands_locked_descriptor_before_owner_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_CATALOG_PAYLOAD)]))

    executor.execute(_dispatch(db_path, agent_id="role:lead"))

    pending = _pending_interactions(db_path)
    assert len(pending) == 1
    payload = json.loads(pending[0]["payload_json"])
    assert payload["catalog_id"] == "github-readonly"
    assert payload["source"] == "github-mcp-server"
    assert payload["version"] == "1.6.0"
    assert payload["args"][0] == "stdio"


def test_catalog_proposal_cannot_substitute_reviewed_executable(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    payload = {**_CATALOG_PAYLOAD, "source": "other-mcp"}
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(payload)]))

    executor.execute(_dispatch(db_path, agent_id="role:lead"))

    assert _pending_interactions(db_path) == []
    with sqlite3.connect(str(db_path)) as conn:
        rejected = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='extension.catalog_proposal_rejected'"
        ).fetchone()[0]
    assert rejected == 1


def test_engineer_cannot_propose(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, proposer_role="engineer", proposer_id="role:engineer")
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))

    executor.execute(_dispatch(db_path, agent_id="role:engineer"))

    assert _pending_interactions(db_path) == []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        denied = conn.execute(
            "SELECT payload_json FROM activity_log WHERE action = 'role.op_denied'"
        ).fetchone()
    assert denied is not None
    assert json.loads(denied["payload_json"])["action_group"] == "extension_proposal"


def test_reviewer_cannot_propose(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path, proposer_role="reviewer", proposer_id="role:reviewer")
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))

    executor.execute(_dispatch(db_path, agent_id="role:reviewer"))

    assert _pending_interactions(db_path) == []


def test_missing_required_fields_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    incomplete = {"reason": EXTENSION_PROPOSAL_REASON, "name": "unity"}  # no source, no justification
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(incomplete)]))

    executor.execute(_dispatch(db_path, agent_id="role:lead"))

    assert _pending_interactions(db_path) == []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        comment = conn.execute(
            "SELECT body FROM issue_comments WHERE author_user_id = 'system'"
        ).fetchone()
    assert comment is not None
    assert "source" in comment["body"] and "justification" in comment["body"]


def test_accept_writes_approved_registry_entry(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))
    executor.execute(_dispatch(db_path, agent_id="role:lead"))
    interaction_id = _pending_interactions(db_path)[0]["id"]

    resolve_interaction(db_path, interaction_id=interaction_id, action="accept", resolved_by_user_id="user")
    # The approval commits on the NEXT wake (interaction_resolved), matching
    # every other resolved-interaction side effect in this codebase.
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="interaction", reason="interaction_resolved",
        payload={
            "issue_id": "issue:intake", "wake_reason": "interaction_resolved",
            "interaction_id": interaction_id, "action": "accept",
        },
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    servers = list_mcp_servers(db_path.parent)
    assert len(servers) == 1
    assert servers[0]["name"] == "unity-mcp"
    assert servers[0]["status"] == "approved"
    assert servers[0]["source"] == "npx -y unity-mcp@1.2.0"
    assert servers[0]["version"] == "1.2.0"
    assert servers[0]["approved_by"] == "user"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        audit = conn.execute(
            "SELECT payload_json FROM activity_log WHERE action = 'extension.approved'"
        ).fetchone()
    assert audit is not None
    assert json.loads(audit["payload_json"])["name"] == "Unity MCP"


def test_accept_catalog_proposal_preserves_review_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_CATALOG_PAYLOAD)]))
    executor.execute(_dispatch(db_path, agent_id="role:lead"))
    interaction_id = _pending_interactions(db_path)[0]["id"]

    resolve_interaction(db_path, interaction_id=interaction_id, action="accept", resolved_by_user_id="user")
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="interaction", reason="interaction_resolved",
        payload={
            "issue_id": "issue:intake", "wake_reason": "interaction_resolved",
            "interaction_id": interaction_id, "action": "accept",
        },
    )
    executor.execute(HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead"))

    server = list_mcp_servers(db_path.parent)[0]
    assert server["status"] == "approved"
    assert server["catalog_id"] == "github-readonly"
    assert server["catalog_artifact_version"] == "1.6.0"
    assert server["catalog_reviewed_at"] == "2026-07-20"


def test_reject_persists_rejection_without_granting(tmp_path: Path) -> None:
    """A rejection grants nothing and runs nothing — but it IS recorded, so
    the Lead has ground truth to avoid re-proposing what the owner declined
    (design §7: idempotency per extension)."""
    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))
    executor.execute(_dispatch(db_path, agent_id="role:lead"))
    interaction_id = _pending_interactions(db_path)[0]["id"]

    resolve_interaction(db_path, interaction_id=interaction_id, action="reject", resolved_by_user_id="user")
    enqueue_wakeup(
        db_path, agent_id="role:lead", source="interaction", reason="interaction_resolved",
        payload={
            "issue_id": "issue:intake", "wake_reason": "interaction_resolved",
            "interaction_id": interaction_id, "action": "reject",
        },
    )
    dispatch = HeartbeatScheduler(db_path).dispatch_next(agent_id="role:lead")
    executor.execute(dispatch)

    servers = list_mcp_servers(db_path.parent)
    assert len(servers) == 1
    assert servers[0]["status"] == "rejected"
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        audit = conn.execute(
            "SELECT action FROM activity_log WHERE action = 'extension.rejected'"
        ).fetchone()
    assert audit is not None


def test_approve_after_reject_overwrites(tmp_path: Path) -> None:
    """The owner can change their mind: approving the same name later wins."""
    from aiteam.extensions import approve_mcp_server, reject_mcp_server

    runtime_dir = tmp_path / ".aiteam"
    runtime_dir.mkdir()
    reject_mcp_server(runtime_dir, name="unity", justification="not yet")
    approve_mcp_server(
        runtime_dir, name="unity", source="npx -y unity-mcp@1.2.0",
        version="1.2.0",
        applies_to_roles=["engineer"], justification="now yes", approved_by="user",
    )

    servers = list_mcp_servers(runtime_dir)
    assert len(servers) == 1
    assert servers[0]["status"] == "approved"
    assert servers[0]["source"] == "npx -y unity-mcp@1.2.0"


def test_rejected_identical_contract_is_suppressed_during_cooldown(tmp_path: Path) -> None:
    from aiteam.extensions import reject_mcp_server

    db_path = tmp_path / "aiteam.db"
    _init_db(db_path)
    reject_mcp_server(
        db_path.parent,
        name="Unity MCP",
        source=_VALID_PAYLOAD["source"],
        version=_VALID_PAYLOAD["version"],
        justification="owner declined",
    )
    executor = RunExecutor(db_path, AdapterRegistry([_ProposeRuntime(_VALID_PAYLOAD)]))

    executor.execute(_dispatch(db_path, agent_id="role:lead"))

    assert _pending_interactions(db_path) == []
    with sqlite3.connect(str(db_path)) as conn:
        comment = conn.execute(
            "SELECT body FROM issue_comments WHERE author_user_id='system' ORDER BY created_at DESC"
        ).fetchone()[0]
        suppressed = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE action='extension.proposal_suppressed'"
        ).fetchone()[0]
    assert "cooldown" in comment
    assert suppressed == 1
