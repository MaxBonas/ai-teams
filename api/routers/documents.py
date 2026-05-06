from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.documents import DocumentConflict, get_document, list_documents, list_revisions, put_document

router = APIRouter()


class PutDocumentRequest(BaseModel):
    title: str
    body: str
    format: str = "markdown"
    base_revision_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = {}


@router.get("/api/issues/{issue_id}/documents")
async def get_issue_documents(issue_id: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        documents = list_documents(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "documents": documents}


@router.get("/api/issues/{issue_id}/documents/{key}")
async def get_issue_document(issue_id: str, key: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        document = get_document(db, issue_id=issue_id, key=key)
        if document is None and key == "plan":
            document = _recover_plan_document_from_comments(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True, "document": document}


def _recover_plan_document_from_comments(db_path: Path, *, issue_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT body, source_run_id
            FROM issue_comments
            WHERE issue_id = ?
              AND author_agent_id IS NOT NULL
            ORDER BY created_at ASC, rowid ASC
            """,
            (issue_id,),
        ).fetchall()
    for row in rows:
        body = str(row["body"] or "").strip()
        if not _looks_like_plan(body):
            continue
        try:
            return put_document(
                db_path,
                issue_id=issue_id,
                key="plan",
                title="Plan recuperado del thread",
                body=body,
                format="markdown",
                run_id=str(row["source_run_id"] or "") or None,
                metadata={"source": "recovered_from_plan_comment"},
            )
        except Exception:
            return None
    return None


def _looks_like_plan(body: str) -> bool:
    text = body.strip().lower()
    if not text:
        return False
    if text.startswith(("plan inicial", "plan detallado", "# plan", "## plan")):
        return True
    markers = ["**objetivo**", "objetivo", "sub-issues", "accountability", "riesgos", "criterio de cierre"]
    return text.startswith("plan") and sum(1 for marker in markers if marker in text) >= 2


@router.put("/api/issues/{issue_id}/documents/{key}")
async def put_issue_document(issue_id: str, key: str, body: PutDocumentRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        document = put_document(
            db,
            issue_id=issue_id,
            key=key,
            title=body.title,
            body=body.body,
            format=body.format,
            base_revision_id=body.base_revision_id,
            run_id=body.run_id,
            metadata=body.metadata,
        )
    except DocumentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    log_activity(
        db,
        action="issue_document.put",
        target_type="issue_document",
        target_id=document["id"],
        actor_agent_id=None,
        run_id=body.run_id,
        payload={
            "issue_id": issue_id,
            "key": document["key"],
            "revision_id": document["current_revision_id"],
            "revision_number": document["revision_number"],
        },
    )
    return {"success": True, "document": document}


@router.get("/api/issues/{issue_id}/documents/{key}/revisions")
async def get_issue_document_revisions(issue_id: str, key: str, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        revisions = list_revisions(db, issue_id=issue_id, key=key)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    return {"success": True, "revisions": revisions}


def _db(request: Request) -> Path:
    ws = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return resolve_runtime_dir(ws, PROJECT_ROOT) / "aiteam.db"


def _schema_err(exc: sqlite3.OperationalError) -> HTTPException:
    if "no such table" in str(exc).lower():
        return HTTPException(status_code=503, detail="Schema not available")
    return HTTPException(status_code=500, detail=str(exc))
