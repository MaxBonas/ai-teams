from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from pathlib import Path

# Absolute import if possible, but assuming api package exists
from api.utils import (
    _require_api_auth_request,
    _workspace_from_request,
    _sanitize_project_name,
    _allocate_project_path,
    PROJECT_ROOT,
    get_current_workspace,
    set_current_workspace,
)

router = APIRouter()

class WorkspacePath(BaseModel):
    path: str

class NewProjectRequest(BaseModel):
    name: str

@router.get("/api/workspace")
async def get_workspace(request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    return {"workspace": str(workspace.as_posix())}

@router.post("/api/workspace")
async def set_workspace(payload: WorkspacePath, request: Request):
    _require_api_auth_request(request)
    new_path = Path(payload.path)
    if not new_path.is_absolute():
        new_path = (PROJECT_ROOT / new_path).resolve()
    else:
        new_path = new_path.resolve()

    allowed_root = PROJECT_ROOT.parent.resolve()
    if allowed_root not in new_path.parents and new_path != allowed_root:
        raise HTTPException(status_code=400, detail="Workspace path is outside the allowed project root.")

    new_path.mkdir(parents=True, exist_ok=True)
    set_current_workspace(new_path)

    return {"success": True, "workspace": str(get_current_workspace().as_posix())}

@router.post("/api/projects/new")
async def create_project(payload: NewProjectRequest, request: Request):
    _require_api_auth_request(request)
    import api.main as _main
    projects_root = _main.PROJECT_ROOT.parent
    projects_root.mkdir(parents=True, exist_ok=True)

    normalized_name = _sanitize_project_name(payload.name)
    target = _allocate_project_path(projects_root, normalized_name)
    target.mkdir(parents=True, exist_ok=False)

    return {
        "success": True,
        "workspace": str(target.as_posix()),
        "project_name": target.name,
    }
