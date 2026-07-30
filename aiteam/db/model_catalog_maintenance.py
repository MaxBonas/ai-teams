"""Histórico durable e idempotente del mantenimiento del catálogo de modelos."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "model_catalog_maintenance_v1"
DIMENSIONS = ("model", "cli", "price", "quota", "prompt", "tool", "contract")

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS model_catalog_maintenance_snapshots (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    period TEXT NOT NULL,
    source_observed_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    dimension_hashes_json TEXT NOT NULL,
    trigger_reasons_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    trend_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_catalog_maintenance_created
    ON model_catalog_maintenance_snapshots(created_at, id);
CREATE INDEX IF NOT EXISTS idx_model_catalog_maintenance_period
    ON model_catalog_maintenance_snapshots(period, created_at);
"""


def reconcile_model_catalog_maintenance(
    db_path: Path,
    read_model: Mapping[str, Any],
    *,
    observed_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Persiste solo cambios materiales o el primer snapshot de cada mes."""
    now = _coerce_datetime(observed_at) or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    period = now.strftime("%Y-%m")
    content_hash = str(read_model.get("content_hash") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("model catalog content_hash is required")
    dimensions = model_catalog_dimension_hashes(read_model)
    metrics = model_catalog_maintenance_metrics(read_model)

    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)
        conn.execute("BEGIN IMMEDIATE")
        try:
            latest_row = conn.execute(
                """
                SELECT * FROM model_catalog_maintenance_snapshots
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            monthly_exists = (
                conn.execute(
                    """
                    SELECT 1 FROM model_catalog_maintenance_snapshots
                    WHERE period = ?
                    LIMIT 1
                    """,
                    (period,),
                ).fetchone()
                is not None
            )
            latest = _decode_row(latest_row) if latest_row is not None else None
            changed = (
                list(DIMENSIONS)
                if latest is None
                else [
                    key
                    for key in DIMENSIONS
                    if latest["dimension_hashes"].get(key) != dimensions[key]
                ]
            )
            reasons = (
                ["initial"]
                if latest is None
                else [f"{key}_changed" for key in changed]
            )
            if not monthly_exists:
                reasons.append("monthly")
            if not reasons:
                conn.execute("COMMIT")
                return {
                    "schema_version": SCHEMA_VERSION,
                    "persisted": False,
                    "reason": "no_change",
                    "snapshot": latest,
                }

            prior_metrics = latest["metrics"] if latest is not None else {}
            trend = {
                key: int(metrics[key]) - int(prior_metrics.get(key, metrics[key]))
                for key in metrics
            }
            payload = {
                "schema_version": SCHEMA_VERSION,
                "period": period,
                "source_observed_at": str(
                    read_model.get("observed_at") or now_iso
                ),
                "content_hash": content_hash,
                "dimension_hashes": dimensions,
                "trigger_reasons": reasons,
                "metrics": metrics,
                "trend": trend,
            }
            snapshot_hash = _sha256(payload)
            snapshot_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO model_catalog_maintenance_snapshots (
                    id, schema_version, period, source_observed_at,
                    content_hash, dimension_hashes_json, trigger_reasons_json,
                    metrics_json, trend_json, snapshot_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_hash) DO NOTHING
                """,
                (
                    snapshot_id,
                    SCHEMA_VERSION,
                    period,
                    payload["source_observed_at"],
                    content_hash,
                    _canonical_json(dimensions),
                    _canonical_json(reasons),
                    _canonical_json(metrics),
                    _canonical_json(trend),
                    snapshot_hash,
                    now_iso,
                ),
            )
            row = conn.execute(
                """
                SELECT * FROM model_catalog_maintenance_snapshots
                WHERE snapshot_hash = ?
                """,
                (snapshot_hash,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "schema_version": SCHEMA_VERSION,
        "persisted": row["id"] == snapshot_id,
        "reason": "recorded",
        "snapshot": _decode_row(row),
    }


def list_model_catalog_maintenance(
    db_path: Path,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 120))
    with contextlib.closing(_connect(db_path)) as conn:
        conn.executescript(_ENSURE_SQL)
        rows = conn.execute(
            """
            SELECT * FROM model_catalog_maintenance_snapshots
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return [_decode_row(row) for row in rows]


def model_catalog_dimension_hashes(
    read_model: Mapping[str, Any],
) -> dict[str, str]:
    candidates = [
        item for item in read_model.get("candidates") or () if isinstance(item, Mapping)
    ]
    roles = [
        (candidate, role)
        for candidate in candidates
        for role in candidate.get("roles") or ()
        if isinstance(role, Mapping)
    ]
    projections: dict[str, Any] = {
        "model": [
            {
                "identity": candidate.get("identity"),
                "label": candidate.get("label"),
                "roles_declared": candidate.get("roles_declared"),
                "tier": (candidate.get("model_metadata") or {}).get("tier"),
                "capability_band": (candidate.get("model_metadata") or {}).get(
                    "capability_band"
                ),
            }
            for candidate in candidates
        ],
        "cli": {
            "evaluation_version_evidence": read_model.get(
                "evaluation_version_evidence"
            ),
            "rows": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "probe_version": (
                        candidate.get("model_metadata") or {}
                    ).get("probe_version"),
                    "adapter_type": (
                        candidate.get("provider_metadata") or {}
                    ).get("adapter_type"),
                    "configured": (candidate.get("states") or {}).get(
                        "configured"
                    ),
                    "adapter_green": (candidate.get("states") or {}).get(
                        "adapter_green"
                    ),
                }
                for candidate in candidates
            ],
        },
        "price": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "economy": (candidate.get("model_metadata") or {}).get(
                    "economy"
                ),
                "price_note": (candidate.get("model_metadata") or {}).get(
                    "price_note"
                ),
            }
            for candidate in candidates
        ],
        "quota": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "capacity_pool": (candidate.get("identity") or {}).get(
                    "capacity_pool"
                ),
                "role": role.get("canonical_role"),
                "runtime_metrics": role.get("runtime_metrics"),
                "quota_inputs": _matching_values(
                    role.get("score_inputs"),
                    ("quota", "capacity", "budget"),
                ),
            }
            for candidate, role in roles
        ],
        "prompt": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "role": role.get("canonical_role"),
                "prompt_evidence": _matching_values(
                    {
                        "prompt_evaluation": role.get("evaluation"),
                        "prompt_provenance": role.get("provenance"),
                    },
                    ("prompt", "rubric", "judge", "contract"),
                ),
            }
            for candidate, role in roles
        ],
        "tool": [
            {
                "candidate_id": candidate.get("candidate_id"),
                "capabilities": (candidate.get("model_metadata") or {}).get(
                    "capabilities"
                ),
                "mcp_transport": (
                    candidate.get("provider_metadata") or {}
                ).get("mcp_transport"),
                "structured_output": (
                    candidate.get("provider_metadata") or {}
                ).get("structured_output"),
                "role": role.get("canonical_role"),
                "compatibility": role.get("compatibility"),
                "tool_gates": _matching_values(
                    (role.get("score") or {}).get("hard_gates"),
                    ("tool", "mcp", "structured"),
                ),
            }
            for candidate, role in roles
        ],
        "contract": {
            "schema_version": read_model.get("schema_version"),
            "score_version": read_model.get("score_version"),
            "canonical_roles": read_model.get("canonical_roles"),
            "evidence_taxonomy": read_model.get("evidence_taxonomy"),
            "tier1_authority_contract": read_model.get(
                "tier1_authority_contract"
            ),
        },
    }
    return {key: _sha256(value) for key, value in projections.items()}


def model_catalog_maintenance_metrics(
    read_model: Mapping[str, Any],
) -> dict[str, int]:
    candidates = [
        item for item in read_model.get("candidates") or () if isinstance(item, Mapping)
    ]
    roles = [
        role
        for candidate in candidates
        for role in candidate.get("roles") or ()
        if isinstance(role, Mapping)
    ]
    return {
        "candidate_count": len(candidates),
        "role_cell_count": len(roles),
        "compatible_role_count": sum(
            (role.get("compatibility") or {}).get("allowed") is True
            for role in roles
        ),
        "scored_role_count": sum(
            isinstance(role.get("score"), Mapping) for role in roles
        ),
        "auto_eligible_role_count": sum(
            (role.get("score") or {}).get("auto_eligible") is True
            for role in roles
        ),
        "green_candidate_count": sum(
            ((candidate.get("states") or {}).get("adapter_green") or {}).get(
                "value"
            )
            is True
            for candidate in candidates
        ),
        "selectable_candidate_count": sum(
            ((candidate.get("states") or {}).get("selectable") or {}).get(
                "value"
            )
            is True
            for candidate in candidates
        ),
        "stale_candidate_count": sum(
            ((candidate.get("states") or {}).get("stale") or {}).get("value")
            is True
            for candidate in candidates
        ),
    }


def _matching_values(value: Any, needles: tuple[str, ...]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _matching_values(item, needles)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if any(needle in str(key).lower() for needle in needles)
        }
    if isinstance(value, (list, tuple)):
        return [_matching_values(item, needles) for item in value]
    return value


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in (
        "dimension_hashes",
        "trigger_reasons",
        "metrics",
        "trend",
    ):
        result[key] = json.loads(result.pop(f"{key}_json"))
    result["hash_valid"] = result["snapshot_hash"] == _sha256(
        {
            "schema_version": result["schema_version"],
            "period": result["period"],
            "source_observed_at": result["source_observed_at"],
            "content_hash": result["content_hash"],
            "dimension_hashes": result["dimension_hashes"],
            "trigger_reasons": result["trigger_reasons"],
            "metrics": result["metrics"],
            "trend": result["trend"],
        }
    )
    return result


def _coerce_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
