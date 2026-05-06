from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


def create_agent(
    db_path: Path,
    *,
    role: str,
    name: str,
    seniority: str = "standard",
    adapter_type: str | None = None,
    adapter_config: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    budget_monthly_cents: int = 0,
    heartbeat_interval_sec: int = 0,
    supervisor_agent_id: str | None = None,
    agent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            INSERT INTO agents (
                id, role, name, seniority, adapter_type,
                adapter_config_json, capabilities_json,
                budget_monthly_cents, heartbeat_interval_sec,
                supervisor_agent_id, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                agent_id or str(uuid.uuid4()),
                role.strip(),
                name.strip(),
                seniority,
                adapter_type,
                _json(adapter_config),
                json.dumps(capabilities or [], ensure_ascii=False),
                int(budget_monthly_cents),
                int(heartbeat_interval_sec),
                supervisor_agent_id,
                _json(metadata),
            ),
        ).fetchone()
        return dict(row)


def list_agents(
    db_path: Path,
    *,
    status: str | None = None,
    role: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if status:
        filters.append("status = ?")
        params.append(status)
    if role:
        filters.append("role = ?")
        params.append(role)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with contextlib.closing(_connect(db_path)) as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM agents {where} ORDER BY created_at ASC LIMIT ?", params
        ).fetchall()]


def get_agent(db_path: Path, *, agent_id: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return dict(row) if row else None


def update_agent(
    db_path: Path,
    *,
    agent_id: str,
    status: str | None = None,
    name: str | None = None,
    seniority: str | None = None,
    heartbeat_interval_sec: int | None = None,
    adapter_type: str | None = None,
    adapter_config: dict[str, Any] | None = None,
    capabilities: list[str] | None = None,
    budget_monthly_cents: int | None = None,
    supervisor_agent_id: str | None = None,
) -> dict[str, Any] | None:
    sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if name is not None:
        sets.append("name = ?")
        params.append(name.strip())
    if seniority is not None:
        sets.append("seniority = ?")
        params.append(seniority)
    if heartbeat_interval_sec is not None:
        sets.append("heartbeat_interval_sec = ?")
        params.append(int(heartbeat_interval_sec))
    if adapter_type is not None:
        sets.append("adapter_type = ?")
        params.append(adapter_type)
    if adapter_config is not None:
        sets.append("adapter_config_json = ?")
        params.append(_json(adapter_config))
    if capabilities is not None:
        sets.append("capabilities_json = ?")
        params.append(json.dumps(capabilities, ensure_ascii=False))
    if budget_monthly_cents is not None:
        sets.append("budget_monthly_cents = ?")
        params.append(int(budget_monthly_cents))
    if supervisor_agent_id is not None:
        sets.append("supervisor_agent_id = ?")
        params.append(supervisor_agent_id or None)
    if len(sets) == 1:
        return get_agent(db_path, agent_id=agent_id)
    params.append(agent_id)
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            f"UPDATE agents SET {', '.join(sets)} WHERE id = ? RETURNING *",
            params,
        ).fetchone()
        return dict(row) if row else None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _json(v: Any) -> str:
    return json.dumps(v or {}, ensure_ascii=False, sort_keys=True)
