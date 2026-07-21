"""Contrato durable del context curator, independiente del executor y proveedor."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiteam.context_budget import LEGACY_UNSYNTHESIZED_CHAR_THRESHOLD, evaluate_context_budget
from aiteam.db.activity_log import log_activity
from aiteam.db.documents import append_summary_block, get_context_summary, get_document
from aiteam.db.issues import get_issue, update_issue
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.user_config import resolve_adapter_config


CONTEXT_CURATION_SLICE_MAX_CHARS = 24_000
MAX_COMPRESSION_RATIO = 0.30
CONTEXT_CURATOR_CHAR_THRESHOLD = LEGACY_UNSYNTHESIZED_CHAR_THRESHOLD
MAX_CAUSAL_UNITS = 32
MAX_CAUSAL_INDEX_CHARS = 4_096
CAUSAL_KINDS = frozenset({
    "objective", "decision", "constraint", "evidence", "accountability",
    "risk", "escalation", "open_item", "scope", "rejected_option",
})
REQUIRED_LINKS_BY_KIND: dict[str, frozenset[str]] = {
    "accountability": frozenset({"owner", "deliverable", "accepted_by"}),
    "escalation": frozenset({"metric", "threshold", "window", "action"}),
    "rejected_option": frozenset({"reason"}),
}


@dataclass(frozen=True)
class CuratorTrigger:
    from_comment_id: str | None
    unsynthesized_chars: int
    budget: Any


def build_context_curation_target(
    db_path: Path,
    *,
    issue_id: str,
    max_chars: int = CONTEXT_CURATION_SLICE_MAX_CHARS,
) -> dict[str, Any] | None:
    """Construye el rango exacto y acotado que el curador puede sintetizar."""
    with contextlib.closing(_connect(db_path)) as conn:
        marker: str | None = None
        row = conn.execute(
            "SELECT body FROM issue_documents WHERE issue_id=? AND key='context_summary'",
            (issue_id,),
        ).fetchone()
        if row:
            try:
                state = json.loads(str(row["body"] or "{}"))
                marker = str(state.get("synthesized_through_comment_id") or "") or None
                partial_comment_id = str(state.get("partial_comment_id") or "") or None
                partial_char_offset = int(state.get("partial_char_offset") or 0)
            except (TypeError, ValueError):
                marker = None
                partial_comment_id = None
                partial_char_offset = 0
        else:
            partial_comment_id = None
            partial_char_offset = 0
        marker_rowid = 0
        if marker:
            marker_row = conn.execute(
                "SELECT rowid FROM issue_comments WHERE issue_id=? AND id=?",
                (issue_id, marker),
            ).fetchone()
            marker_rowid = int(marker_row["rowid"]) if marker_row else 0
        rows = conn.execute(
            """
            SELECT id, body, author_agent_id, author_user_id, created_at
            FROM issue_comments
            WHERE issue_id=? AND rowid>?
            ORDER BY rowid ASC
            """,
            (issue_id, marker_rowid),
        ).fetchall()

    selected: list[dict[str, Any]] = []
    char_count = 0
    truncated_comment = False
    for row in rows:
        body = str(row["body"] or "")
        start_offset = partial_char_offset if str(row["id"]) == partial_comment_id else 0
        remaining = body[start_offset:]
        available = max_chars - char_count
        if available <= 0:
            break
        chunk = remaining[:available]
        if not chunk:
            continue
        selected.append({
            "id": row["id"],
            "author": row["author_user_id"] or row["author_agent_id"] or "system",
            "created_at": row["created_at"],
            "body": chunk,
        })
        char_count += len(chunk)
        if len(chunk) < len(remaining):
            truncated_comment = True
            break
    if not selected or char_count <= 0:
        return None
    return {
        "target_issue_id": issue_id,
        "start_comment_id": selected[0]["id"],
        "end_comment_id": selected[-1]["id"],
        "start_char_offset": partial_char_offset if selected[0]["id"] == partial_comment_id else 0,
        "end_char_offset": (
            (partial_char_offset if selected[-1]["id"] == partial_comment_id else 0)
            + len(selected[-1]["body"])
        ),
        "char_count_original": char_count,
        "max_compression_ratio": MAX_COMPRESSION_RATIO,
        "semantic_contract": {
            "version": 1,
            "max_units": MAX_CAUSAL_UNITS,
            "max_index_chars": MAX_CAUSAL_INDEX_CHARS,
            "kinds": sorted(CAUSAL_KINDS),
            "required_links_by_kind": {
                kind: sorted(links) for kind, links in REQUIRED_LINKS_BY_KIND.items()
            },
            "link_format": "relation:value",
            "provenance_policy": "use the smallest source_comment_ids set that supports each unit",
        },
        "has_more_unsynthesized": truncated_comment or len(selected) < len(rows),
        "comments": selected,
    }


def evaluate_curator_trigger(
    db_path: Path,
    *,
    issue_id: str,
    agent_id: str,
    parent_payload: dict[str, Any],
) -> CuratorTrigger | None:
    """Decide de forma determinista si hace falta una nueva compactación."""
    summary = get_context_summary(db_path, issue_id=issue_id) or {}
    marker = str(summary.get("synthesized_through_comment_id") or "") or None
    with contextlib.closing(_connect(db_path)) as conn:
        marker_row = conn.execute(
            "SELECT rowid FROM issue_comments WHERE issue_id=? AND id=?",
            (issue_id, marker),
        ).fetchone() if marker else None
        marker_rowid = int(marker_row["rowid"]) if marker_row else 0
        unsynthesized_chars = int(conn.execute(
            "SELECT COALESCE(SUM(LENGTH(body)), 0) FROM issue_comments WHERE issue_id=? AND rowid>?",
            (issue_id, marker_rowid),
        ).fetchone()[0])
        first_row = conn.execute(
            "SELECT id FROM issue_comments WHERE issue_id=? AND rowid>? ORDER BY rowid ASC LIMIT 1",
            (issue_id, marker_rowid),
        ).fetchone()
        agent = conn.execute(
            "SELECT adapter_type, adapter_config_json FROM agents WHERE id=?",
            (agent_id,),
        ).fetchone()
        active = conn.execute(
            """
            SELECT 1 FROM issues
            WHERE parent_id=? AND lower(role)='context_curator'
              AND status IN ('todo','in_progress','blocked')
            LIMIT 1
            """,
            (issue_id,),
        ).fetchone()
    if active or get_document(db_path, issue_id=issue_id, key="plan") is not None:
        return None

    shown_chars = sum(
        len(str(comment.get("body") or ""))
        for comment in (parent_payload.get("comments") or [])
        if isinstance(comment, dict)
    )
    base_chars = max(
        0,
        len(json.dumps(parent_payload, ensure_ascii=False, default=str)) - shown_chars,
    )
    config = resolve_adapter_config(
        str(agent["adapter_type"] or "") if agent else "",
        _decode_json(agent["adapter_config_json"] if agent else "{}"),
    )
    budget = evaluate_context_budget(
        unsynthesized_chars=unsynthesized_chars,
        base_payload_chars=base_chars,
        adapter_config=config,
    )
    if not budget.should_compact:
        return None
    return CuratorTrigger(
        from_comment_id=(
            None if marker and marker_row is None
            else (str(first_row["id"]) if first_row else None)
        ),
        unsynthesized_chars=unsynthesized_chars,
        budget=budget,
    )


def apply_curator_actions(
    db_path: Path,
    *,
    issue_id: str,
    agent_id: str,
    run_id: str,
    actions: dict[str, Any],
) -> dict[str, Any]:
    """Persiste el artefacto o aplica exactamente un retry y luego escalado."""
    updated_actions = actions
    persisted = False
    error = "missing append_context_summary operation"
    action = actions.get("append_context_summary")
    if isinstance(action, dict):
        try:
            parent_id = _parent_issue_id(db_path, issue_id=issue_id)
            receipt = append_verified_context_summary(
                db_path,
                issue_id=parent_id,
                action=action,
                run_id=run_id,
            )
            expected = receipt["target"]
            summary = str(action.get("summary_markdown") or "").strip()
            persisted = True
            log_activity(
                db_path,
                action="context_summary.appended",
                target_type="issue",
                target_id=parent_id,
                actor_agent_id=agent_id,
                run_id=run_id,
                payload={
                    "start_comment_id": expected["start_comment_id"],
                    "end_comment_id": expected["end_comment_id"],
                    "char_count_original": expected["char_count_original"],
                    "char_count_summary": len(summary),
                },
            )
        except Exception as exc:
            error = str(exc)

    if actions.get("issue_status") != "done" or persisted:
        return updated_actions
    updated_actions = dict(actions)
    issue = get_issue(db_path, issue_id=issue_id) or {}
    metadata = _decode_json(issue.get("metadata_json") or "{}")
    recovery = metadata.get("context_curator_recovery")
    attempts = int(recovery.get("corrective_attempts") or 0) if isinstance(recovery, dict) else 0
    diagnostic = (
        f"El bloque append_context_summary fue rechazado: {error}. Reutiliza exactamente "
        "payload.context_curation_target y vuelve a emitir el artefacto."
    )
    metadata["context_curator_recovery"] = {
        "corrective_attempts": min(attempts + 1, 2),
        "last_error": error,
        "last_run_id": run_id,
        "state": "retry_queued" if attempts == 0 else "escalated",
    }
    update_issue(db_path, issue_id=issue_id, metadata=metadata)
    updated_actions.setdefault("add_comments", []).append(f"⚙ Sistema: {diagnostic}")
    if attempts == 0:
        updated_actions.pop("issue_status", None)
        updated_actions.pop("notify_supervisor", None)
        enqueue_wakeup(
            db_path,
            agent_id=agent_id,
            source="context_curator_recovery",
            reason="context_summary_corrective_retry",
            payload={
                "issue_id": issue_id,
                "diagnostic": diagnostic,
                "corrective_attempt": 1,
                "source_run_id": run_id,
            },
            idempotency_key=f"context-curator-recovery:{issue_id}:1",
            trigger_detail=error,
        )
        event = "context_summary.recovery_queued"
        payload = {"attempt": 1, "error": error}
    else:
        updated_actions["issue_status"] = "blocked"
        updated_actions["notify_supervisor"] = True
        event = "context_summary.recovery_exhausted"
        payload = {"attempts": attempts + 1, "error": error}
    log_activity(
        db_path,
        action=event,
        target_type="issue",
        target_id=issue_id,
        actor_agent_id=agent_id,
        run_id=run_id,
        payload=payload,
    )
    return updated_actions


def append_verified_context_summary(
    db_path: Path,
    *,
    issue_id: str,
    action: dict[str, Any],
    run_id: str = "",
) -> dict[str, Any]:
    """Valida contra el slice vigente y persiste un bloque con cursor correcto."""
    expected = build_context_curation_target(db_path, issue_id=issue_id)
    if not expected:
        raise ValueError("no unsynthesized parent context is available")
    _validate_action(db_path, parent_id=issue_id, action=action, expected=expected)
    summary = str(action.get("summary_markdown") or "").strip()
    causal_units = list(action.get("causal_units") or [])
    end_chars = _comment_length(
        db_path, issue_id=issue_id, comment_id=str(expected["end_comment_id"])
    )
    end_complete = int(expected["end_char_offset"]) >= end_chars
    document = append_summary_block(
        db_path,
        issue_id=issue_id,
        block={
            "summary_markdown": summary,
            "start_comment_id": expected["start_comment_id"],
            "end_comment_id": expected["end_comment_id"],
            "char_count_original": expected["char_count_original"],
            "start_char_offset": expected["start_char_offset"],
            "end_char_offset": expected["end_char_offset"],
            "causal_units": causal_units,
            "semantic_contract_version": 1,
        },
        synthesized_through_comment_id=(str(expected["end_comment_id"]) if end_complete else None),
        partial_comment_id=(None if end_complete else str(expected["end_comment_id"])),
        partial_char_offset=(None if end_complete else int(expected["end_char_offset"])),
        run_id=run_id or None,
    )
    return {"document": document, "target": expected, "end_complete": end_complete}


def _validate_action(
    db_path: Path,
    *,
    parent_id: str,
    action: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    for field in ("target_issue_id", "start_comment_id", "end_comment_id", "char_count_original"):
        if str(action.get(field) or "") != str(expected.get(field) or ""):
            raise ValueError(f"context summary {field} does not match the durable source slice")
    end_chars = _comment_length(db_path, issue_id=parent_id, comment_id=str(expected["end_comment_id"]))
    whole_range = int(expected["start_char_offset"]) == 0 and int(expected["end_char_offset"]) == end_chars
    offsets_omitted = int(action.get("start_char_offset") or 0) == 0 and int(action.get("end_char_offset") or 0) == 0
    if not (whole_range and offsets_omitted):
        for field in ("start_char_offset", "end_char_offset"):
            if str(action.get(field) or "") != str(expected.get(field) or ""):
                raise ValueError(f"context summary {field} does not match the durable source slice")
    summary = str(action.get("summary_markdown") or "").strip()
    if not summary:
        raise ValueError("context summary body is empty")
    causal_units = action.get("causal_units")
    validate_causal_units(
        causal_units,
        allowed_comment_ids={str(item["id"]) for item in expected.get("comments") or []},
    )
    summary_limit = int(int(expected["char_count_original"]) * MAX_COMPRESSION_RATIO)
    if len(summary) > summary_limit:
        raise ValueError(
            f"context summary exceeds the 30% compression budget ({len(summary)}/{summary_limit} chars)"
        )


def validate_causal_units(units: Any, *, allowed_comment_ids: set[str]) -> None:
    """Valida forma, relaciones y provenance; no afirma que la síntesis sea verdadera."""
    if not isinstance(units, list):
        raise ValueError("context summary causal_units are required")
    if len(units) > MAX_CAUSAL_UNITS:
        raise ValueError(f"context summary causal_units exceed the {MAX_CAUSAL_UNITS} unit cap")
    index_chars = len(json.dumps(units, ensure_ascii=False, separators=(",", ":")))
    if index_chars > MAX_CAUSAL_INDEX_CHARS:
        raise ValueError(
            f"context summary causal_units exceed the {MAX_CAUSAL_INDEX_CHARS} character cap"
        )
    seen: set[str] = set()
    for index, unit in enumerate(units, start=1):
        if not isinstance(unit, dict):
            raise ValueError(f"context summary causal_unit {index} is invalid")
        unit_id = str(unit.get("id") or "").strip()
        kind = str(unit.get("kind") or "").strip().lower()
        statement = str(unit.get("statement") or "").strip()
        if not unit_id or unit_id in seen:
            raise ValueError(f"context summary causal_unit {index} id is missing or duplicated")
        seen.add(unit_id)
        if kind not in CAUSAL_KINDS:
            raise ValueError(f"context summary causal_unit {unit_id} has unsupported kind")
        if not statement:
            raise ValueError(f"context summary causal_unit {unit_id} statement is empty")
        if len(statement) > 400:
            raise ValueError(f"context summary causal_unit {unit_id} statement is too long")
        source_ids = unit.get("source_comment_ids")
        if not isinstance(source_ids, list) or not source_ids:
            raise ValueError(f"context summary causal_unit {unit_id} requires source_comment_ids")
        normalized_sources = {str(value or "").strip() for value in source_ids}
        if "" in normalized_sources or not normalized_sources <= allowed_comment_ids:
            raise ValueError(f"context summary causal_unit {unit_id} references comments outside the durable slice")
        if len(normalized_sources) > 8:
            raise ValueError(f"context summary causal_unit {unit_id} has too many source comments")
        links = unit.get("links")
        if not isinstance(links, list):
            raise ValueError(f"context summary causal_unit {unit_id} links must be a list")
        if len(links) > 12:
            raise ValueError(f"context summary causal_unit {unit_id} has too many links")
        relations: set[str] = set()
        for link in links:
            relation, separator, value = str(link or "").partition(":")
            if not separator or not relation.strip() or not value.strip():
                raise ValueError(f"context summary causal_unit {unit_id} has an invalid relation link")
            if len(str(link)) > 200:
                raise ValueError(f"context summary causal_unit {unit_id} relation link is too long")
            relations.add(relation.strip().lower())
        missing = REQUIRED_LINKS_BY_KIND.get(kind, frozenset()) - relations
        if missing:
            raise ValueError(
                f"context summary causal_unit {unit_id} is missing links: {', '.join(sorted(missing))}"
            )


def _parent_issue_id(db_path: Path, *, issue_id: str) -> str:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("SELECT parent_id FROM issues WHERE id=?", (issue_id,)).fetchone()
    parent_id = str(row["parent_id"] or "") if row else ""
    if not parent_id:
        raise ValueError("context curator issue has no parent")
    return parent_id


def _comment_length(db_path: Path, *, issue_id: str, comment_id: str) -> int:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT LENGTH(body) AS body_chars FROM issue_comments WHERE id=? AND issue_id=?",
            (comment_id, issue_id),
        ).fetchone()
    if row is None:
        raise ValueError("context summary end comment is missing")
    return int(row["body_chars"] or 0)


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn
