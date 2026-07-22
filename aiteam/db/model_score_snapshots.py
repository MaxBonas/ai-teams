"""Persistencia idempotente de decisiones de catálogo/scoring."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS model_role_score_snapshots (
    id TEXT PRIMARY KEY,
    selection_scope TEXT NOT NULL,
    issue_id TEXT REFERENCES issues(id) ON DELETE SET NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    canonical_role TEXT NOT NULL,
    score_version TEXT NOT NULL,
    read_model_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    winner_candidate_id TEXT,
    winner_reason TEXT NOT NULL DEFAULT '',
    auto_applied INTEGER NOT NULL DEFAULT 0 CHECK (auto_applied IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(selection_scope, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_model_score_snapshots_role
    ON model_role_score_snapshots(canonical_role, created_at);
"""


def persist_model_role_score_snapshot(
    db_path: Path,
    *,
    selection_scope: str,
    canonical_role: str,
    score_version: str,
    read_model_version: str,
    candidates: Sequence[Mapping[str, Any]],
    winner_candidate_id: str | None = None,
    winner_reason: str = "",
    auto_applied: bool = False,
    issue_id: str | None = None,
    agent_id: str | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Persiste una entrada canónica completa; el mismo input es idempotente."""
    scope = str(selection_scope or "").strip()
    role = str(canonical_role or "").strip()
    if not scope or not role or not score_version or not read_model_version:
        raise ValueError("scope, role and schema versions are required")
    canonical_candidates = sorted(
        (dict(item) for item in candidates),
        key=lambda item: str(item.get("candidate_id") or ""),
    )
    candidate_ids = {
        str(item.get("candidate_id") or "").strip() for item in canonical_candidates
    }
    if (
        not canonical_candidates
        or "" in candidate_ids
        or len(candidate_ids) != len(canonical_candidates)
    ):
        raise ValueError("at least one candidate with candidate_id is required")
    if winner_candidate_id and winner_candidate_id not in candidate_ids:
        raise ValueError("winner must belong to the persisted candidate set")
    if auto_applied:
        winner = next(
            (
                item
                for item in canonical_candidates
                if item["candidate_id"] == winner_candidate_id
            ),
            None,
        )
        if winner is None or winner.get("auto_eligible") is not True:
            raise ValueError("auto-applied winner must be explicitly auto-eligible")

    payload = {
        "selection_scope": scope,
        "issue_id": issue_id,
        "agent_id": agent_id,
        "canonical_role": role,
        "score_version": str(score_version),
        "read_model_version": str(read_model_version),
        "candidates": canonical_candidates,
        "winner_candidate_id": winner_candidate_id,
        "winner_reason": str(winner_reason or ""),
        "auto_applied": bool(auto_applied),
    }
    encoded = _canonical_json(payload)
    input_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)
        row = conn.execute(
            """
            INSERT INTO model_role_score_snapshots (
                id, selection_scope, issue_id, agent_id, canonical_role,
                score_version, read_model_version, input_hash, candidates_json,
                winner_candidate_id, winner_reason, auto_applied
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(selection_scope, input_hash) DO NOTHING
            RETURNING *
            """,
            (
                snapshot_id or str(uuid.uuid4()),
                scope,
                issue_id,
                agent_id,
                role,
                score_version,
                read_model_version,
                input_hash,
                _canonical_json(canonical_candidates),
                winner_candidate_id,
                str(winner_reason or ""),
                int(bool(auto_applied)),
            ),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT * FROM model_role_score_snapshots
                   WHERE selection_scope = ? AND input_hash = ?""",
                (scope, input_hash),
            ).fetchone()
        return _decode_row(row)


def list_model_role_score_snapshots(
    db_path: Path, *, canonical_role: str | None = None
) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)
        if canonical_role:
            rows = conn.execute(
                """SELECT * FROM model_role_score_snapshots
                   WHERE canonical_role = ? ORDER BY created_at, id""",
                (canonical_role,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM model_role_score_snapshots ORDER BY created_at, id"
            ).fetchall()
    return [_decode_row(row) for row in rows]


def model_role_score_snapshot_hash_valid(snapshot: Mapping[str, Any]) -> bool:
    """Recalcula el sello completo; nunca confía en un booleano del caller."""
    payload = {
        "selection_scope": snapshot.get("selection_scope"),
        "issue_id": snapshot.get("issue_id"),
        "agent_id": snapshot.get("agent_id"),
        "canonical_role": snapshot.get("canonical_role"),
        "score_version": snapshot.get("score_version"),
        "read_model_version": snapshot.get("read_model_version"),
        "candidates": snapshot.get("candidates"),
        "winner_candidate_id": snapshot.get("winner_candidate_id"),
        "winner_reason": snapshot.get("winner_reason"),
        "auto_applied": bool(snapshot.get("auto_applied")),
    }
    expected = str(snapshot.get("input_hash") or "")
    return bool(expected) and (
        hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest() == expected
    )


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["candidates"] = json.loads(result.pop("candidates_json"))
    result["auto_applied"] = bool(result["auto_applied"])
    result["hash_valid"] = model_role_score_snapshot_hash_valid(result)
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
