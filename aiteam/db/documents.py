from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


class DocumentConflict(ValueError):
    """Raised when a document update is based on a stale revision."""


def put_document(
    db_path: Path,
    *,
    issue_id: str,
    key: str,
    title: str,
    body: str,
    format: str = "markdown",
    base_revision_id: str | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_key = _clean_key(key)
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            current = conn.execute(
                "SELECT * FROM issue_documents WHERE issue_id = ? AND key = ?",
                (issue_id, clean_key),
            ).fetchone()
            if current is None:
                if base_revision_id:
                    raise DocumentConflict("document does not exist for base_revision_id")
                document_id = str(uuid.uuid4())
                revision_id = str(uuid.uuid4())
                revision_number = 1
                doc_row = conn.execute(
                    """
                    INSERT INTO issue_documents (
                        id, issue_id, key, title, format, body, current_revision_id,
                        revision_number, created_by_run_id, updated_by_run_id, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING *
                    """,
                    (
                        document_id,
                        issue_id,
                        clean_key,
                        title.strip(),
                        format,
                        body,
                        revision_id,
                        revision_number,
                        run_id,
                        run_id,
                        metadata_json,
                    ),
                ).fetchone()
            else:
                if base_revision_id and base_revision_id != current["current_revision_id"]:
                    raise DocumentConflict("stale base_revision_id")
                document_id = current["id"]
                revision_id = str(uuid.uuid4())
                revision_number = int(current["revision_number"] or 0) + 1
                doc_row = conn.execute(
                    """
                    UPDATE issue_documents
                    SET title = ?,
                        format = ?,
                        body = ?,
                        current_revision_id = ?,
                        revision_number = ?,
                        updated_by_run_id = ?,
                        metadata_json = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    RETURNING *
                    """,
                    (
                        title.strip(),
                        format,
                        body,
                        revision_id,
                        revision_number,
                        run_id,
                        metadata_json,
                        document_id,
                    ),
                ).fetchone()

            conn.execute(
                """
                INSERT INTO issue_document_revisions (
                    id, document_id, issue_id, key, title, format, body,
                    revision_number, created_by_run_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    document_id,
                    issue_id,
                    clean_key,
                    title.strip(),
                    format,
                    body,
                    revision_number,
                    run_id,
                    metadata_json,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return _decode(dict(doc_row))


def get_document(db_path: Path, *, issue_id: str, key: str) -> dict[str, Any] | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM issue_documents WHERE issue_id = ? AND key = ?",
            (issue_id, _clean_key(key)),
        ).fetchone()
        return _decode(dict(row)) if row else None


def list_documents(db_path: Path, *, issue_id: str) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM issue_documents
            WHERE issue_id = ?
            ORDER BY key ASC
            """,
            (issue_id,),
        ).fetchall()
        return [_decode(dict(row)) for row in rows]


def list_revisions(db_path: Path, *, issue_id: str, key: str) -> list[dict[str, Any]]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT r.*
            FROM issue_document_revisions r
            JOIN issue_documents d ON d.id = r.document_id
            WHERE d.issue_id = ? AND d.key = ?
            ORDER BY r.revision_number ASC
            """,
            (issue_id, _clean_key(key)),
        ).fetchall()
        return [_decode(dict(row)) for row in rows]


def append_summary_block(
    db_path: Path,
    *,
    issue_id: str,
    block: dict[str, Any],
    synthesized_through_comment_id: str | None = None,
    partial_comment_id: str | None = None,
    partial_char_offset: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Append a synthesis block to the ``context_summary`` document.

    The document body is a JSON object::

        {
            "blocks": [
                {"summary_markdown": "...", "start_comment_id": ..., ...}
            ],
            "synthesized_through_comment_id": "<last synthesized comment id>"
        }

    Creates the document if it doesn't exist yet.  If *synthesized_through_comment_id*
    is provided it replaces the stored value. Oversized comments use
    *partial_comment_id* + *partial_char_offset* until their final segment; a
    completed segment clears that partial cursor.

    Thread-safe: delegates to :func:`put_document` which uses ``BEGIN IMMEDIATE``.
    """
    doc = get_document(db_path, issue_id=issue_id, key="context_summary")
    if doc is None:
        current_data: dict[str, Any] = {"blocks": []}
        base_revision_id: str | None = None
    else:
        try:
            current_data = json.loads(doc["body"] or "{}")
        except (json.JSONDecodeError, TypeError):
            current_data = {"blocks": []}
        base_revision_id = doc.get("current_revision_id")

    if not isinstance(current_data.get("blocks"), list):
        current_data["blocks"] = []

    current_data["blocks"].append(block)
    if synthesized_through_comment_id is not None:
        current_data["synthesized_through_comment_id"] = synthesized_through_comment_id
    if partial_comment_id is not None and partial_char_offset is not None:
        current_data["partial_comment_id"] = partial_comment_id
        current_data["partial_char_offset"] = int(partial_char_offset)
    else:
        current_data.pop("partial_comment_id", None)
        current_data.pop("partial_char_offset", None)

    return put_document(
        db_path,
        issue_id=issue_id,
        key="context_summary",
        title="Context Summary",
        body=json.dumps(current_data, ensure_ascii=False),
        format="json",
        base_revision_id=base_revision_id,
        run_id=run_id,
    )


def get_context_summary(db_path: Path, *, issue_id: str) -> dict[str, Any] | None:
    """Return the parsed body of the ``context_summary`` document, or ``None`` if absent."""
    doc = get_document(db_path, issue_id=issue_id, key="context_summary")
    if doc is None:
        return None
    try:
        return json.loads(doc["body"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def _clean_key(key: str) -> str:
    clean = key.strip().lower().replace(" ", "-")
    if not clean:
        raise ValueError("document key must not be empty")
    if any(ch in clean for ch in "\\/"):
        raise ValueError("document key must not contain path separators")
    return clean


def _decode(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.get("metadata_json") or "{}")
    return row
