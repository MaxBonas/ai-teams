from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import agents as agents_router
from api.routers import chat as chat_router
from api.routers import comments as comments_router
from api.routers import control_plane as control_plane_router
from api.routers import dependencies as dependencies_router
from api.routers import documents as documents_router
from api.routers import goals as goals_router
from api.routers import interactions as interactions_router
from api.routers import issues as issues_router
from api.routers import runs as runs_router
from api.routers import timeline as timeline_router
from api.routers import tool_access as tool_access_router
from api.routers import settings as settings_router
from api.routers import user_adapters as user_adapters_router
from api.routers import workspace as workspace_router
from api.utils import PROJECT_ROOT, get_current_workspace, resolve_runtime_dir

logger = logging.getLogger(__name__)

_REQUIRED_CONTROL_PLANE_TABLES = {"agents", "runs", "wakeup_requests"}
_SCHEMA_SQL = Path(__file__).parent.parent / "aiteam" / "db" / "schema.sql"


def _db_path() -> Path:
    return resolve_runtime_dir(get_current_workspace()) / "aiteam.db"


def _has_control_plane_schema(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=5.0)) as conn:
            rows = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (?, ?, ?)
                """,
                tuple(sorted(_REQUIRED_CONTROL_PLANE_TABLES)),
            ).fetchall()
    except sqlite3.Error:
        return False
    return {str(row[0]) for row in rows} >= _REQUIRED_CONTROL_PLANE_TABLES


def _apply_schema(db_path: Path) -> None:
    """Apply schema.sql to *db_path* using IF NOT EXISTS guards — safe to run on existing DBs."""
    if not _SCHEMA_SQL.exists():
        logger.warning("schema.sql not found at %s — skipping auto-migration", _SCHEMA_SQL)
        return
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(str(db_path), timeout=20.0)) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(sql)
        logger.info("schema applied to %s", db_path)
    except sqlite3.Error:
        logger.exception("failed to apply schema to %s", db_path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from aiteam.adapters.registry import build_default_registry
    from aiteam.db.runs import reconcile_stale_runs
    from aiteam.heartbeat.executor import RunExecutor
    from aiteam.heartbeat.loop import HeartbeatLoop

    db_path = _db_path()
    registry = build_default_registry()
    executor = RunExecutor(db_path, registry)
    task: asyncio.Task[None] | None = None

    # Auto-migrate: apply schema if tables are missing (idempotent — uses IF NOT EXISTS).
    if not _has_control_plane_schema(db_path):
        logger.info("control-plane schema missing at %s — applying schema.sql", db_path)
        _apply_schema(db_path)

    if _has_control_plane_schema(db_path):
        try:
            recovered = reconcile_stale_runs(db_path)
            if recovered:
                logger.warning("reconciled %d stale run(s) on startup: %s", len(recovered), recovered)
        except Exception:
            logger.exception("startup reconciliation failed — continuing")

        loop = HeartbeatLoop(
            db_path,
            executor,
            db_path_factory=_db_path,
            registry=registry,
        )
        task = asyncio.create_task(loop.run_forever())
    else:
        logger.warning("HeartbeatLoop not started; schema unavailable at %s", db_path)

    yield

    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.info("HeartbeatLoop stopped")


app = FastAPI(
    title="AI Teams Control Plane",
    version="0.2.0",
    description="Paperclip-like control plane for programming teams.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspace_router.router)
app.include_router(chat_router.router)
app.include_router(control_plane_router.router)
app.include_router(interactions_router.router)
app.include_router(goals_router.router)
app.include_router(agents_router.router)
app.include_router(issues_router.router)
app.include_router(runs_router.router)
app.include_router(comments_router.router)
app.include_router(dependencies_router.router)
app.include_router(documents_router.router)
app.include_router(timeline_router.router)
app.include_router(settings_router.router)
app.include_router(tool_access_router.router)
app.include_router(user_adapters_router.router)


@app.get("/")
async def root() -> dict[str, object]:
    return {
        "service": "aiteams-control-plane",
        "status": "ok",
        "project_root": str(Path(PROJECT_ROOT).as_posix()),
    }


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "success": True,
        "status": "ok",
        "mode": "control_plane_v2",
    }
