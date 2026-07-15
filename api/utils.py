from __future__ import annotations

import logging
import os
import re
import shutil
import json
from pathlib import Path
from typing import Mapping

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CURRENT_WORKSPACE: Path = PROJECT_ROOT


def get_configured_projects_root() -> Path:
    """Return the user-configured projects root.

    Precedence:
      1. ``AITEAM_PROJECTS_ROOT`` env var (useful for CI / portable installs).
      2. ``projects_root`` in ``~/.config/aiteams/settings.json`` (set via UI).
      3. Fallback: parent of the source-code directory (legacy behaviour).
    """
    env_override = os.environ.get("AITEAM_PROJECTS_ROOT", "").strip()
    if env_override:
        return Path(env_override).resolve()
    try:
        from aiteam.user_config import get_projects_root as _get_projects_root
        configured = _get_projects_root()
        if configured is not None:
            return configured
    except Exception:
        pass
    return PROJECT_ROOT.parent


def get_current_workspace() -> Path:
    global _CURRENT_WORKSPACE
    if _CURRENT_WORKSPACE.resolve() == PROJECT_ROOT.resolve():
        restored = _load_persisted_workspace()
        if restored is not None:
            _CURRENT_WORKSPACE = restored
    return _CURRENT_WORKSPACE


def set_current_workspace(path: Path, *, persist: bool = False) -> None:
    global _CURRENT_WORKSPACE
    _CURRENT_WORKSPACE = Path(path).resolve()
    if persist:
        _persist_current_workspace(_CURRENT_WORKSPACE)


def clear_persisted_workspace() -> None:
    # Mismo guard que persist/load: sin él, cualquier pytest que pase por el
    # branch de workspace-missing (test_workspace_api) o el borrado de
    # proyectos borraba el current_workspace.json REAL del desarrollador, y el
    # siguiente reinicio del backend olvidaba el proyecto activo (visto en
    # vivo dos veces el 2026-07-15).
    if _workspace_persistence_disabled():
        return
    path = _workspace_state_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _workspace_state_path() -> Path:
    return PROJECT_ROOT / "runtime" / "current_workspace.json"


def _persist_current_workspace(path: Path) -> None:
    if _workspace_persistence_disabled():
        return
    state_path = _workspace_state_path()
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"workspace": str(path.resolve())}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # Sin persistencia, un reinicio del backend olvida el proyecto activo y
        # el heartbeat deja de procesarlo hasta que alguien re-selecciona en la
        # UI — el fallo tiene que ser visible en el log, no tragado.
        logger.warning("failed to persist current workspace to %s", state_path, exc_info=True)


def _load_persisted_workspace() -> Path | None:
    if _workspace_persistence_disabled():
        return None
    state_path = _workspace_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = str(payload.get("workspace") or "").strip() if isinstance(payload, dict) else ""
    if not raw:
        return None
    candidate = Path(raw).resolve()
    try:
        db_path = resolve_runtime_dir(candidate, PROJECT_ROOT) / "aiteam.db"
    except OSError:
        return None
    if candidate.exists() and db_path.exists():
        return candidate
    logger.warning(
        "persisted workspace %s is stale (dir exists=%s, db exists=%s) — clearing",
        candidate, candidate.exists(), db_path.exists(),
    )
    clear_persisted_workspace()
    return None


def _workspace_persistence_disabled() -> bool:
    return (
        os.environ.get("AITEAM_DISABLE_WORKSPACE_PERSISTENCE", "").strip().lower() in {"1", "true", "yes"}
        or "PYTEST_CURRENT_TEST" in os.environ
    )


def require_configured_workspace(request: Request) -> Path:
    """Rechaza con 409 las mutaciones cuando no hay proyecto activo.

    Sin este guard, un POST (wakeup, chat) tras un reinicio que olvidó el
    workspace iba contra la DB legacy del repo (runtime/aiteam.db) y moría con
    un críptico "FOREIGN KEY constraint failed" — o peor, escribía en una DB
    que ningún heartbeat procesa. Visto en vivo el 2026-07-15.
    """
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    if workspace.resolve() == PROJECT_ROOT.resolve():
        raise HTTPException(
            status_code=409,
            detail=(
                "No hay proyecto activo: selecciona un workspace con POST /api/workspace "
                "o pasa la cabecera x-aiteam-workspace."
            ),
        )
    return workspace


def resolve_runtime_dir(workspace: Path, project_root: Path = PROJECT_ROOT) -> Path:
    workspace = Path(workspace).resolve()
    project_root = Path(project_root).resolve()
    if workspace == project_root:
        return workspace / "runtime"

    dotdir = workspace / ".aiteam"
    legacy = workspace / "runtime"
    if legacy.exists() and not dotdir.exists():
        try:
            legacy.rename(dotdir)
        except OSError:
            dotdir.mkdir(parents=True, exist_ok=True)
            _absorb_legacy_runtime(legacy, dotdir)
    elif legacy.exists() and dotdir.exists():
        _absorb_legacy_runtime(legacy, dotdir)
    return dotdir


def _absorb_legacy_runtime(legacy: Path, dotdir: Path) -> None:
    dotdir.mkdir(parents=True, exist_ok=True)
    for candidate in sorted(legacy.rglob("*"), key=lambda path: (len(path.parts), str(path))):
        relative = candidate.relative_to(legacy)
        target = dotdir / relative
        if candidate.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            candidate.rename(target)
        except OSError:
            shutil.copy2(candidate, target)
            try:
                candidate.unlink()
            except OSError:
                pass
    for candidate in sorted(
        [path for path in legacy.rglob("*") if path.is_dir()],
        key=lambda path: (len(path.parts), str(path)),
        reverse=True,
    ):
        try:
            candidate.rmdir()
        except OSError:
            pass
    try:
        legacy.rmdir()
    except OSError:
        pass


def _require_api_auth_request(request: Request) -> None:
    expected = os.environ.get("AITEAM_API_KEY", "").strip()
    require = os.environ.get("AITEAM_REQUIRE_API_KEY", "").strip().lower() in {"1", "true", "yes"}
    if not expected and not require:
        return
    provided = (
        request.headers.get("x-aiteam-api-key")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if expected and provided == expected:
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _workspace_from_request(
    request_or_headers: Request | Mapping[str, str],
    current_workspace: Path,
    project_root: Path = PROJECT_ROOT,
) -> Path:
    headers: Mapping[str, str]
    if isinstance(request_or_headers, Request):
        headers = request_or_headers.headers
    else:
        headers = request_or_headers

    raw = (
        headers.get("x-aiteam-workspace")
        or headers.get("x-workspace")
        or headers.get("x-project-root")
        or ""
    ).strip()
    if not raw:
        return Path(current_workspace).resolve()

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(project_root) / candidate
    return candidate.resolve()


def _sanitize_project_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", str(name or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        raise HTTPException(status_code=400, detail="Project name is required")
    return cleaned


def _allocate_project_path(projects_root: Path, name: str) -> Path:
    base = projects_root / name
    if not base.exists():
        return base
    for index in range(2, 1000):
        candidate = projects_root / f"{name} {index}"
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail="Could not allocate a unique project path")
