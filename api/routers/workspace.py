import json
import contextlib
import mimetypes
import os
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# Absolute import if possible, but assuming api package exists
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
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
    choose_adapter_for_new_slot,
    choose_adapter_for_role,
    ensure_quorum_agents,
    project_profiles,
    reconcile_project_agent_policy,
    is_unresolved_model_default,
)
from aiteam.run_profiles import FULL_TEAM, LEAD_QUORUM, normalize_run_profile
from aiteam.model_selection_intent import normalize_owner_explicit_selection
from aiteam.objective_classification import classify_objective
from aiteam.tools.catalog import default_capabilities_for_role

router = APIRouter()

class WorkspacePath(BaseModel):
    path: str

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

    if not new_path.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Workspace does not exist. Create or import it with the guided setup.",
        )
    runtime_dir = resolve_runtime_dir(new_path, PROJECT_ROOT)
    if not (runtime_dir / "aiteam.db").is_file():
        raise HTTPException(
            status_code=409,
            detail="Workspace is not initialized. Import it with the guided setup.",
        )
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

    outcome = _remove_project_tree(workspace)
    if get_current_workspace().resolve() == workspace:
        set_current_workspace(PROJECT_ROOT)
        clear_persisted_workspace()
    return {"success": True, "workspace": "", "configured": False, **outcome}


def _remove_project_tree(workspace: Path) -> dict[str, object]:
    try:
        _rmtree_project_tree(workspace)
        return {"deleted": True}
    except OSError as exc:
        raise HTTPException(
            status_code=423,
            detail=(
                "Project folder could not be deleted completely. "
                "AI Teams will not rename it or schedule cleanup. "
                "Close terminals/editors using it, inspect the original path "
                f"and retry explicitly. Original error: {exc}"
            ),
        ) from exc


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
    lead_adapter_profile_id: str | None = None,
    lead_model: str | None = None,
    lead_candidate_id: str | None = None,
    data_class: str = "",
    objective_kind: str = "auto",
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
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")
    profiles = project_profiles(runtime_dir)
    lead_profiles = (
        [profile for profile in profiles if str(profile.get("id") or "") == lead_adapter_profile_id]
        if lead_adapter_profile_id
        else profiles
    )
    if lead_model:
        lead_adapter = choose_adapter_for_role(
            "lead", "lead", lead_profiles,
            run_profile=run_profile,
            criticality="medium",
            data_class=data_class,
            preferred_model=lead_model,
        )
    else:
        lead_adapter = choose_adapter_for_new_slot(
            db_path,
            role="lead",
            seniority="lead",
            profiles=lead_profiles,
            selection_scope="bootstrap:new-agent:role:lead",
            run_profile=run_profile,
            criticality="medium",
            data_class=data_class,
        )
    if not lead_adapter:
        raise ValueError("No compatible Lead adapter/model selection")
    if is_unresolved_model_default(lead_adapter):
        raise ValueError(
            "No auto-eligible Lead model is available; select one explicitly or roll back to shadow"
        )
    lead_adapter_type = str((lead_adapter or {}).get("adapter_type") or "lead_builtin")
    lead_config = dict((lead_adapter or {}).get("adapter_config") or {})
    if lead_model:
        if lead_candidate_id:
            lead_config["selection_intent"] = {
                "schema_version": "model_selection_intent_v1",
                "mode": "owner_explicit",
                "source": "onboarding_model_role_selector",
                "candidate_id": lead_candidate_id,
            }
        lead_config = normalize_owner_explicit_selection(
            db_path,
            role="lead",
            adapter_config=lead_config,
            source="onboarding_model_role_selector",
        )
    lead_adapter_config = json.dumps(lead_config, ensure_ascii=False, sort_keys=True)
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
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
                json.dumps(
                    {
                        "source": "project_bootstrap",
                        "lead_adapter_profile_id": (lead_adapter or {}).get("adapter_profile_id"),
                        "selected_by_user": bool(lead_adapter_profile_id),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        )
        task = str(initial_task or "").strip()
        objective_classification = classify_objective(
            task[:160],
            task,
            explicit_kind=objective_kind,
        )
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
                json.dumps(
                    {
                        "profile": run_profile,
                        "objective_classification": objective_classification.to_metadata(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
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
                        "data_class": data_class or None,
                        "objective_classification": objective_classification.to_metadata(),
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
        ensure_quorum_agents(
            db_path,
            profiles=project_profiles(runtime_dir),
            issue_id="issue:intake",
        )
