import json
import logging
import mimetypes
import os
import shutil
import sqlite3
import uuid
from typing import Literal

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from pathlib import Path

# Absolute import if possible, but assuming api package exists
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
    _sanitize_project_name,
    _allocate_project_path,
    PROJECT_ROOT,
    clear_persisted_workspace,
    get_configured_projects_root,
    get_current_workspace,
    resolve_runtime_dir,
    set_current_workspace,
)
from aiteam.db.migration import SCHEMA_PATH
from aiteam.policies import WORKSPACE_NOISE_DIRS as _WS_SKIP_DIRS
from aiteam.project_adapters import (
    available_project_profiles,
    choose_adapter_for_role,
    ensure_quorum_agents,
    project_profiles,
    reconcile_project_agent_policy,
    write_project_adapter_policy,
)
from aiteam.run_profiles import FULL_TEAM, LEAD_QUORUM, normalize_run_profile
from aiteam.user_config import profile_is_connected
from aiteam.db.comments import create_comment
from aiteam.db.wakeups import enqueue_wakeup
from aiteam.tools.catalog import default_capabilities_for_role

router = APIRouter()

class WorkspacePath(BaseModel):
    path: str

class NewProjectRequest(BaseModel):
    name: str
    initial_task: str | None = None
    adapter_profile_ids: list[str] = Field(default_factory=list)
    run_profile: Literal["solo_lead", "lead_quorum", "full_team"] = FULL_TEAM

class DeleteProjectRequest(BaseModel):
    confirmation: str

@router.get("/api/workspace")
async def get_workspace(request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    configured = workspace.resolve() != PROJECT_ROOT.resolve()
    if configured and not workspace.exists():
        if get_current_workspace().resolve() == workspace.resolve():
            set_current_workspace(PROJECT_ROOT)
            clear_persisted_workspace()
        return {
            "workspace": "",
            "configured": False,
            "projects_root": str(get_configured_projects_root().as_posix()),
            "missing_workspace": str(workspace.as_posix()),
            "reason": "workspace_missing",
        }
    if configured and not (resolve_runtime_dir(workspace, PROJECT_ROOT) / "aiteam.db").exists():
        return {
            "workspace": "",
            "configured": False,
            "projects_root": str(get_configured_projects_root().as_posix()),
            "missing_workspace": str(workspace.as_posix()),
            "reason": "workspace_db_missing",
        }
    return {
        "workspace": str(workspace.as_posix()) if configured else "",
        "configured": configured,
        "projects_root": str(get_configured_projects_root().as_posix()),
    }

@router.post("/api/workspace")
async def set_workspace(payload: WorkspacePath, request: Request):
    _require_api_auth_request(request)
    new_path = Path(payload.path)
    if not new_path.is_absolute():
        new_path = (PROJECT_ROOT / new_path).resolve()
    else:
        new_path = new_path.resolve()

    allowed_root = get_configured_projects_root().resolve()
    if allowed_root not in new_path.parents and new_path != allowed_root:
        raise HTTPException(status_code=400, detail="Workspace path is outside the configured projects root.")

    new_path.mkdir(parents=True, exist_ok=True)
    _initialize_project_runtime(new_path)
    set_current_workspace(new_path, persist=True)

    return {"success": True, "workspace": str(get_current_workspace().as_posix()), "configured": True}

@router.get("/api/projects")
async def list_projects(request: Request):
    """List all AI Teams projects found under projects_root."""
    _require_api_auth_request(request)
    projects_root = get_configured_projects_root().resolve()
    projects: list[dict] = []
    if not projects_root.exists():
        return {"projects": [], "projects_root": str(projects_root.as_posix())}
    current_ws = get_current_workspace().resolve()
    for entry in sorted(projects_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        runtime_dir = resolve_runtime_dir(entry, PROJECT_ROOT)
        db_path = runtime_dir / "aiteam.db"
        if not db_path.exists():
            continue
        projects.append({
            "name": entry.name,
            "path": str(entry.as_posix()),
            "current": entry.resolve() == current_ws,
        })
    return {"projects": projects, "projects_root": str(projects_root.as_posix())}


@router.post("/api/projects/new")
async def create_project(payload: NewProjectRequest, request: Request):
    _require_api_auth_request(request)
    projects_root = get_configured_projects_root()
    projects_root.mkdir(parents=True, exist_ok=True)

    normalized_name = _sanitize_project_name(payload.name)

    # ── Connectivity check ────────────────────────────────────────────────
    # A project whose selected adapters all lack credentials produces a first
    # run that dies silently. Warn by default; hard-block when the operator
    # sets AITEAM_REQUIRE_CONNECTED_ADAPTER=1.
    selected_profiles = available_project_profiles(payload.adapter_profile_ids)
    _unconnected = [str(p.get("id") or "") for p in selected_profiles if not profile_is_connected(p)]
    adapter_warning: str | None = None
    if selected_profiles and len(_unconnected) == len(selected_profiles):
        adapter_warning = (
            "Ningún adapter seleccionado tiene credenciales verificadas "
            f"({', '.join(_unconnected)}). El primer run fallará hasta que "
            "guardes la API key o hagas login del CLI y pruebes la conexión."
        )
        if os.environ.get("AITEAM_REQUIRE_CONNECTED_ADAPTER", "").strip().lower() in {"1", "true", "yes"}:
            raise HTTPException(status_code=400, detail=adapter_warning)

    target = _allocate_project_path(projects_root, normalized_name)
    target.mkdir(parents=True, exist_ok=False)
    runtime_dir = resolve_runtime_dir(target, PROJECT_ROOT)
    try:
        write_project_adapter_policy(runtime_dir, profile_ids=payload.adapter_profile_ids)
    except ValueError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run_profile = normalize_run_profile(payload.run_profile)
    _initialize_project_runtime(
        target,
        initial_task=payload.initial_task,
        run_profile=run_profile,
    )
    # VCS del workspace: solo en proyectos RECIÉN creados por la app (un
    # workspace externo seleccionado a posteriori nunca se toca — sin marker
    # git_managed tampoco habrá commits automáticos).
    try:
        from aiteam.workspace_git import init_managed_repo
        init_managed_repo(target)
    except Exception:
        logger.warning("workspace git init failed for %s", target, exc_info=True)
    set_current_workspace(target, persist=True)

    # Enqueue the Lead's first wakeup so the HeartbeatLoop can start immediately
    # without waiting for a manual "Iniciar" click in the frontend.
    try:
        _db = resolve_runtime_dir(target, PROJECT_ROOT) / "aiteam.db"
        enqueue_wakeup(
            _db,
            agent_id="role:lead",
            source="project_bootstrap",
            reason="new_project",
            payload={
                "issue_id": "issue:intake",
                "wake_reason": "new_project",
                "profile": run_profile,
            },
            idempotency_key="bootstrap:issue:intake:role:lead",
        )
    except Exception:
        pass  # Non-fatal — the user can still click Iniciar manually

    if adapter_warning:
        try:
            create_comment(
                resolve_runtime_dir(target, PROJECT_ROOT) / "aiteam.db",
                issue_id="issue:intake",
                author_agent_id=None,
                body=f"⚠ Sistema: {adapter_warning}",
                metadata={"source": "project_creation_connectivity_check"},
            )
        except Exception:
            pass  # informational only

    return {
        "success": True,
        "workspace": str(target.as_posix()),
        "project_name": target.name,
        "configured": True,
        "run_profile": run_profile,
        "adapter_warning": adapter_warning,
    }

@router.delete("/api/projects/current")
async def delete_current_project(payload: DeleteProjectRequest, request: Request):
    return _delete_current_project(payload, request)


@router.post("/api/projects/current/delete")
async def post_delete_current_project(payload: DeleteProjectRequest, request: Request):
    return _delete_current_project(payload, request)


def _delete_current_project(payload: DeleteProjectRequest, request: Request):
    _require_api_auth_request(request)
    if payload.confirmation != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm project deletion.")

    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    workspace = workspace.resolve()
    projects_root = get_configured_projects_root().resolve()
    if workspace == PROJECT_ROOT.resolve() or workspace == projects_root:
        raise HTTPException(status_code=400, detail="Refusing to delete the AI Teams source or projects root.")
    if projects_root not in workspace.parents:
        raise HTTPException(status_code=400, detail="Workspace path is outside the allowed project root.")
    if workspace.is_symlink():
        raise HTTPException(status_code=400, detail="Refusing to delete a symlinked project.")
    if not workspace.exists():
        set_current_workspace(PROJECT_ROOT)
        clear_persisted_workspace()
        return {"success": True, "workspace": "", "configured": False, "deleted": False, "reason": "already_missing"}
    if not workspace.is_dir():
        raise HTTPException(status_code=400, detail="Workspace is not a directory.")
    runtime_dir = resolve_runtime_dir(workspace, PROJECT_ROOT)
    if not (runtime_dir / "aiteam.db").exists():
        raise HTTPException(status_code=400, detail="Workspace does not look like an AI Teams project.")

    if get_current_workspace().resolve() == workspace:
        set_current_workspace(PROJECT_ROOT)
        clear_persisted_workspace()
    outcome = _remove_project_tree(workspace, projects_root=projects_root)
    return {"success": True, "workspace": "", "configured": False, **outcome}


def _remove_project_tree(workspace: Path, *, projects_root: Path) -> dict[str, object]:
    try:
        _rmtree_project_tree(workspace)
        return {"deleted": True}
    except OSError as exc:
        tombstone = projects_root / f".aiteam-deleted-{workspace.name}-{uuid.uuid4().hex[:8]}"
        try:
            workspace.rename(tombstone)
        except OSError as rename_exc:
            raise HTTPException(
                status_code=423,
                detail=(
                    "Project folder is locked by Windows or another process. "
                    "Close terminals/editors using it and retry deletion. "
                    f"Original error: {exc}; rename error: {rename_exc}"
                ),
            ) from rename_exc
        try:
            _rmtree_project_tree(tombstone)
        except OSError:
            return {
                "deleted": True,
                "cleanup_pending": True,
                "cleanup_path": str(tombstone.as_posix()),
                "reason": "moved_to_tombstone",
            }
        return {"deleted": True, "moved_before_delete": True}


def _rmtree_project_tree(path: Path) -> None:
    shutil.rmtree(path, onerror=_remove_readonly)


def _remove_readonly(function, path, _exc_info) -> None:
    try:
        os.chmod(path, 0o700)
        function(path)
    except OSError:
        raise


_WS_MAX_READ_BYTES = 256 * 1024  # 256 KB per file for the API


@router.get("/api/workspace/files")
async def list_workspace_files(request: Request):
    """Return a flat list of all workspace files with path, size, and MIME type."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT).resolve()
    if not workspace.exists() or not workspace.is_dir():
        return {"files": [], "workspace": str(workspace.as_posix())}

    files: list[dict] = []
    try:
        for entry in sorted(workspace.rglob("*"), key=lambda p: str(p)):
            if not entry.is_file():
                continue
            try:
                rel = entry.relative_to(workspace)
            except ValueError:
                continue
            parts = rel.parts
            if any(part.startswith(".") or part in _WS_SKIP_DIRS for part in parts[:-1]):
                continue
            if parts[-1].startswith("."):
                continue
            mime, _ = mimetypes.guess_type(str(entry))
            files.append({
                "path": str(rel).replace("\\", "/"),
                "size_bytes": entry.stat().st_size,
                "mime": mime or "application/octet-stream",
            })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"files": files, "workspace": str(workspace.as_posix())}


@router.get("/api/workspace/files/{file_path:path}")
async def read_workspace_file(file_path: str, request: Request):
    """Return the content of a single workspace file as plain text."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT).resolve()

    # Path traversal protection
    rel = file_path.lstrip("/\\")
    if len(rel) >= 2 and rel[1] == ":":
        rel = rel[2:].lstrip("/\\")
    target = (workspace / rel).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside workspace")

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    # Skip binary files
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    sample = raw[:512]
    if sample:
        non_text = sum(1 for b in sample if b < 9 or (13 < b < 32) or b == 127)
        if len(sample) > 0 and non_text / len(sample) > 0.15:
            raise HTTPException(status_code=415, detail="Binary file — cannot serve as text")

    content = raw[:_WS_MAX_READ_BYTES].decode("utf-8", errors="replace")
    truncated = len(raw) > _WS_MAX_READ_BYTES
    return {
        "path": str(target.relative_to(workspace)).replace("\\", "/"),
        "content": content,
        "size_bytes": len(raw),
        "truncated": truncated,
    }


def _initialize_project_runtime(
    project_path: Path,
    *,
    initial_task: str | None = None,
    run_profile: str = FULL_TEAM,
) -> None:
    run_profile = normalize_run_profile(run_profile)
    runtime_dir = resolve_runtime_dir(project_path, PROJECT_ROOT)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    instructions = runtime_dir / "instructions.md"
    if not instructions.exists():
        instructions.write_text(
            "# Project Instructions\n\nDescribe durable preferences for the AI Teams Lead here.\n",
            encoding="utf-8",
        )
    db_path = runtime_dir / "aiteam.db"
    lead_adapter = choose_adapter_for_role("lead", "lead", project_profiles(runtime_dir))
    lead_adapter_type = str((lead_adapter or {}).get("adapter_type") or "lead_builtin")
    lead_adapter_config = json.dumps((lead_adapter or {}).get("adapter_config") or {}, ensure_ascii=False, sort_keys=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT OR IGNORE INTO agents (
                id, role, name, seniority, adapter_type,
                adapter_config_json, capabilities_json,
                budget_monthly_cents, heartbeat_interval_sec, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "role:lead",
                "lead",
                "Team Lead",
                "lead",
                lead_adapter_type,
                lead_adapter_config,
                json.dumps(default_capabilities_for_role("lead"), ensure_ascii=False),
                0,
                0,
                '{"source":"project_bootstrap"}',
            ),
        )
        task = str(initial_task or "").strip()
        # Always create goal:intake + issue:intake so the Lead always has a
        # rooted issue to attach runs, comments, and interactions to.
        # If no initial_task is provided, use a placeholder title — the user
        # can send a chat message later to give the Lead the real task.
        intake_title = task[:160] if task else "Nuevo proyecto — cuéntame qué quieres construir"
        intake_desc = task if task else ""
        conn.execute(
            """
            INSERT OR IGNORE INTO goals (id, title, description, source, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "goal:intake",
                intake_title,
                intake_desc,
                "project_bootstrap",
                json.dumps({"profile": run_profile}, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO issues (
                id, goal_id, title, description, status, role,
                complexity, criticality, assignee_agent_id, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "issue:intake",
                "goal:intake",
                intake_title,
                intake_desc,
                "todo",
                "lead",
                "medium",
                "medium",
                "role:lead",
                json.dumps(
                    {
                        "profile": run_profile,
                        "source": "project_bootstrap",
                        "wake_reason": "new_project",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        if task:
            # If an initial task was provided, also store it as the first user comment
            conn.execute(
                """
                INSERT OR IGNORE INTO issue_comments (
                    id, issue_id, author_user_id, body, metadata_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "comment:intake:user",
                    "issue:intake",
                    "user",
                    task,
                    json.dumps({"source": "project_bootstrap"}, ensure_ascii=False, sort_keys=True),
                ),
            )
        conn.commit()

    # Bootstrap minimum org chart: repair Lead adapter + create Tier 3 agents.
    # Idempotent — safe to call on rename/re-init as well.
    reconcile_project_agent_policy(db_path, include_tier3=run_profile != "solo_lead")
    if run_profile == LEAD_QUORUM:
        ensure_quorum_agents(db_path, profiles=project_profiles(runtime_dir))
