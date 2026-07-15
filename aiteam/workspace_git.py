"""Control de versiones de workspaces capa-2: diffs como recibo, rollback real.

Los workspaces creados por AI Teams no tenían VCS: cada file_op de un agente
sobrescribía sin historia, una run mala no se podía revertir y el reviewer
verificaba "presencia y contenido" en vez de diffs. Este módulo:

- ``init_managed_repo``: git init + identidad local + .gitignore en el
  bootstrap de un proyecto NUEVO (nunca en workspaces externos: si el usuario
  ya tenía repo, su historia es suya y no la tocamos — por eso el marker).
- ``commit_run_snapshot``: commit automático tras cada run que cambió el
  workspace, con el run/agente/issue en el mensaje. El diffstat resultante es
  el recibo estructurado de la run ("exige el recibo, no la narración").

Todo degrada en silencio si git no está disponible: el VCS es un refuerzo,
nunca un requisito para que el heartbeat avance.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MARKER = "git_managed"
_GITIGNORE = """# AI Teams — control plane y artefactos locales
.aiteam/
__pycache__/
*.pyc
.pytest_cache/
venv/
.venv/
node_modules/
"""

_GIT_TIMEOUT_SEC = 60


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        return subprocess.run(
            [exe, *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
        )
    except Exception:
        logger.warning("git %s failed in %s", " ".join(args[:2]), workspace, exc_info=True)
        return None


def _marker_path(workspace: Path) -> Path:
    return workspace / ".aiteam" / _MARKER


def is_git_managed(workspace: Path) -> bool:
    """True solo para repos que AI Teams creó (marker + .git presentes)."""
    workspace = Path(workspace)
    return _marker_path(workspace).exists() and (workspace / ".git").exists()


def init_managed_repo(workspace: Path) -> bool:
    """Inicializa git en un workspace RECIÉN creado por AI Teams.

    No hace nada si ya existe un .git (workspace externo del usuario: su
    historia no se toca, y sin marker tampoco se auto-commitea jamás).
    """
    workspace = Path(workspace)
    if (workspace / ".git").exists():
        return False
    result = _git(workspace, "init", "--initial-branch=main")
    if result is None or result.returncode != 0:
        # git viejo sin --initial-branch: reintento simple.
        result = _git(workspace, "init")
        if result is None or result.returncode != 0:
            logger.warning("git init failed in %s — workspace sin VCS", workspace)
            return False
    # Identidad local: los commits automáticos no dependen de la config global.
    _git(workspace, "config", "user.name", "AI Teams")
    _git(workspace, "config", "user.email", "aiteam@localhost")
    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(_GITIGNORE, encoding="utf-8")
    marker = _marker_path(workspace)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-m", "aiteam: workspace bootstrap", "--allow-empty")
    return True


def commit_run_snapshot(
    workspace: Path, *, run_id: str, agent_id: str, issue_id: str | None = None
) -> dict[str, Any] | None:
    """Commit de los cambios del workspace tras una run — el recibo.

    Devuelve ``{"commit": sha, "diffstat": resumen}`` o None si no hay repo
    gestionado, no hay cambios, o git falla (nunca rompe la run).
    """
    workspace = Path(workspace)
    if not is_git_managed(workspace):
        return None
    add = _git(workspace, "add", "-A")
    if add is None or add.returncode != 0:
        return None
    staged = _git(workspace, "diff", "--cached", "--stat")
    if staged is None or not staged.stdout.strip():
        return None  # sin cambios: sin commit vacío
    message = f"aiteam run {run_id} ({agent_id})"
    if issue_id:
        message += f" issue={issue_id}"
    commit = _git(workspace, "commit", "-m", message)
    if commit is None or commit.returncode != 0:
        logger.warning("git commit failed in %s: %s", workspace, (commit.stderr if commit else "")[:200])
        return None
    sha = _git(workspace, "rev-parse", "--short", "HEAD")
    diffstat = staged.stdout.strip()[-1500:]
    return {
        "commit": sha.stdout.strip() if sha and sha.returncode == 0 else "?",
        "diffstat": diffstat,
    }
