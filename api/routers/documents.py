from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.utils import PROJECT_ROOT, _require_api_auth_request, _workspace_from_request, get_current_workspace, resolve_runtime_dir
from aiteam.db.activity_log import log_activity
from aiteam.db.documents import (
    DocumentConflict,
    get_document,
    get_context_summary,
    list_documents,
    list_revisions,
    put_document,
)
from aiteam.context_curator import append_verified_context_summary
from aiteam.plan_contract import PLAN_FORMAT, encode_plan_contract, present_plan_document

router = APIRouter()


class PutDocumentRequest(BaseModel):
    title: str
    body: str
    format: str = "markdown"
    base_revision_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = {}
    plan: dict[str, Any] | None = None


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
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"success": True, "document": present_plan_document(document) if key == "plan" else document}


@router.put("/api/issues/{issue_id}/documents/{key}")
async def put_issue_document(issue_id: str, key: str, body: PutDocumentRequest, request: Request):
    _require_api_auth_request(request)
    db = _db(request)
    try:
        if key == "plan" and body.run_id:
            _require_assigned_lead_run(db, issue_id=issue_id, run_id=body.run_id)
        if key == "plan" and body.plan is None:
            raise HTTPException(
                status_code=422,
                detail="New plan revisions require the structured plan contract",
            )
        document_body = body.body
        document_format = body.format
        if key == "plan" and body.plan is not None:
            document_body = encode_plan_contract(body.plan)
            document_format = PLAN_FORMAT
        document = put_document(
            db,
            issue_id=issue_id,
            key=key,
            title=body.title,
            body=document_body,
            format=document_format,
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
    return {"success": True, "document": present_plan_document(document) if key == "plan" else document}


def _require_assigned_lead_run(db_path: Path, *, issue_id: str, run_id: str) -> None:
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT r.issue_id AS run_issue_id, r.agent_id, a.role, i.assignee_agent_id
            FROM runs r
            JOIN agents a ON a.id = r.agent_id
            JOIN issues i ON i.id = ?
            WHERE r.id = ?
            """,
            (issue_id, run_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="Unknown plan run provenance")
    role = str(row["role"] or "").strip().lower()
    if (
        str(row["run_issue_id"] or "") != issue_id
        or str(row["agent_id"] or "") != str(row["assignee_agent_id"] or "")
        or role not in {"lead", "team_lead", "lead_executor"}
    ):
        raise HTTPException(status_code=403, detail="Only the assigned Lead run may revise the plan")


class AppendSummaryBlockRequest(BaseModel):
    summary_markdown: str
    start_comment_id: str
    end_comment_id: str
    char_count_original: int
    start_char_offset: int
    end_char_offset: int
    causal_units: list[dict[str, Any]]
    run_id: str | None = None


@router.post("/api/issues/{issue_id}/context-summary/blocks")
async def post_context_summary_block(
    issue_id: str, body: AppendSummaryBlockRequest, request: Request
):
    """Persiste un bloque causal validado contra el slice durable vigente."""
    _require_api_auth_request(request)
    db = _db(request)

    if body.run_id:
        _require_context_curator_run(db, target_issue_id=issue_id, run_id=body.run_id)
    action = {
        "target_issue_id": issue_id,
        "summary_markdown": body.summary_markdown,
        "start_comment_id": body.start_comment_id,
        "end_comment_id": body.end_comment_id,
        "char_count_original": body.char_count_original,
        "start_char_offset": body.start_char_offset,
        "end_char_offset": body.end_char_offset,
        "causal_units": body.causal_units,
    }
    try:
        receipt = append_verified_context_summary(
            db,
            issue_id=issue_id,
            action=action,
            run_id=body.run_id or "",
        )
        doc = receipt["document"]
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)

    log_activity(
        db,
        action="context_summary.block_appended",
        target_type="issue_document",
        target_id=doc["id"],
        actor_agent_id=None,
        run_id=body.run_id,
        payload={
            "issue_id": issue_id,
            "start_comment_id": body.start_comment_id,
            "end_comment_id": body.end_comment_id,
            "char_count_original": body.char_count_original,
            "char_count_summary": len(body.summary_markdown),
            "semantic_chars": len(body.summary_markdown) + len(json.dumps(body.causal_units, ensure_ascii=False)),
            "causal_units": len(body.causal_units),
        },
    )
    return {"success": True, "document": doc}


def _require_context_curator_run(db_path: Path, *, target_issue_id: str, run_id: str) -> None:
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT r.agent_id, r.issue_id, a.role, i.assignee_agent_id, i.parent_id
            FROM runs r
            JOIN agents a ON a.id=r.agent_id
            JOIN issues i ON i.id=r.issue_id
            WHERE r.id=?
            """,
            (run_id,),
        ).fetchone()
    if row is None or (
        str(row["role"] or "").lower() != "context_curator"
        or str(row["agent_id"] or "") != str(row["assignee_agent_id"] or "")
        or str(row["parent_id"] or "") != target_issue_id
    ):
        raise HTTPException(status_code=403, detail="Only the assigned context curator run may append this block")


@router.get("/api/issues/{issue_id}/context-summary")
async def get_issue_context_summary(issue_id: str, request: Request):
    """Return the parsed context_summary document for an issue."""
    _require_api_auth_request(request)
    db = _db(request)
    try:
        data = get_context_summary(db, issue_id=issue_id)
    except sqlite3.OperationalError as exc:
        raise _schema_err(exc)
    if data is None:
        raise HTTPException(status_code=404, detail="No context summary for this issue")
    return {"success": True, "context_summary": data}


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
