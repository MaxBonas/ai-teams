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
