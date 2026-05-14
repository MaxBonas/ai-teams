"""
aiteam — command-line interface for the AI Teams orchestrator.

Entry point registered in pyproject.toml:
    [project.scripts]
    aiteam = "aiteam.cli:main"

Usage
-----
    aiteam serve              # start the API backend
    aiteam dev                # start backend + React frontend
    aiteam heartbeat          # run heartbeat loop (no HTTP server needed)
    aiteam status             # system status from the DB
    aiteam project list       # list projects
    aiteam project create     # create a new project
    aiteam project use        # switch active project
    aiteam issue list         # list issues
    aiteam issue show <id>    # show issue details
    aiteam issue create       # create an issue directly
    aiteam run list           # list recent runs
    aiteam run trigger        # enqueue a manual wakeup
    aiteam system-check       # adapter smoke test
    aiteam migrate-to-v2      # DB migration helper
    aiteam budget-status      # monthly cost report
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── colour helpers (work on Windows with ENABLE_VIRTUAL_TERMINAL_PROCESSING) ──

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_DIM   = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED   = "\033[31m"
_CYAN  = "\033[36m"
_BLUE  = "\033[34m"
_MAGENTA = "\033[35m"


def _enable_win_ansi() -> None:
    """Enable ANSI escape processing on Windows (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _c(colour: str, text: str) -> str:
    """Wrap text in an ANSI colour code, honouring NO_COLOR env var."""
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return text
    return f"{colour}{text}{_RESET}"


def _ok(msg: str)  -> str: return _c(_GREEN, f"✓ {msg}")
def _warn(msg: str) -> str: return _c(_YELLOW, f"⚠ {msg}")
def _err(msg: str)  -> str: return _c(_RED, f"✗ {msg}")
def _head(msg: str) -> str: return _c(_BOLD + _CYAN, msg)
def _dim(msg: str)  -> str: return _c(_DIM, msg)


# ── project-root / DB resolution ──────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_runtime_dir(workspace: Path) -> Path:
    """Mirror api.utils.resolve_runtime_dir without importing FastAPI."""
    workspace = workspace.resolve()
    if workspace == _PROJECT_ROOT.resolve():
        return workspace / "runtime"
    dotdir = workspace / ".aiteam"
    legacy = workspace / "runtime"
    if legacy.exists() and not dotdir.exists():
        return legacy
    return dotdir


def _default_db(workspace: Path | None = None) -> Path:
    ws = workspace or _load_current_workspace() or _PROJECT_ROOT
    return _resolve_runtime_dir(ws) / "aiteam.db"


def _load_current_workspace() -> Path | None:
    state = _PROJECT_ROOT / "runtime" / "current_workspace.json"
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
        raw = str(payload.get("workspace") or "").strip()
        if raw:
            p = Path(raw).resolve()
            db = _resolve_runtime_dir(p) / "aiteam.db"
            if p.exists() and db.exists():
                return p
    except Exception:
        pass
    return None


def _db_connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(_err(f"Database not found: {db_path}\n  Run 'aiteam serve' once to initialise it."))
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_python() -> str:
    """Return path to the venv Python on this machine."""
    if sys.platform == "win32":
        candidates = [
            _PROJECT_ROOT / "venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            _PROJECT_ROOT / "venv" / "bin" / "python",
            _PROJECT_ROOT / "venv" / "bin" / "python3",
        ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable


# ── formatting helpers ────────────────────────────────────────────────────────

_STATUS_COLOUR = {
    "done": _GREEN, "completed": _GREEN,
    "in_progress": _CYAN, "running": _CYAN,
    "blocked": _RED, "failed": _RED,
    "backlog": _DIM, "open": _BLUE,
    "review": _MAGENTA, "cancelled": _DIM,
}


def _fmt_status(s: str) -> str:
    colour = _STATUS_COLOUR.get(s.lower(), "")
    return _c(colour, s) if colour else s


def _fmt_dt(raw: str | None) -> str:
    if not raw:
        return _dim("—")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:16]


def _table(rows: list[dict[str, Any]], cols: list[str]) -> None:
    widths = {c: len(c) for c in cols}
    str_rows: list[dict[str, str]] = []
    for row in rows:
        sr: dict[str, str] = {}
        for c in cols:
            val = str(row.get(c) or "")
            sr[c] = val
            widths[c] = max(widths[c], len(val))
        str_rows.append(sr)

    header = "  ".join(c.upper().ljust(widths[c]) for c in cols)
    sep = "  ".join("─" * widths[c] for c in cols)
    print(_c(_BOLD, header))
    print(_dim(sep))
    for sr in str_rows:
        print("  ".join(sr[c].ljust(widths[c]) for c in cols))


# ── sub-command implementations ───────────────────────────────────────────────

# ---- serve ------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    """Start the FastAPI backend (and optionally the heartbeat loop)."""
    from api.utils import get_current_workspace, resolve_runtime_dir
    python = _resolve_python()

    host = args.host
    port = args.port
    reload_flag = args.reload
    no_hb = getattr(args, "no_heartbeat", False)

    uvicorn_cmd = [
        python, "-m", "uvicorn", "api.main:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload_flag:
        uvicorn_cmd.append("--reload")

    print(_head(f"\n  AI Teams — backend server"))
    print(f"  API   → {_c(_CYAN, f'http://{host}:{port}')}")
    if not no_hb:
        print(f"  Heartbeat loop runs inside uvicorn (via lifespan)")
    print(_dim("  Press Ctrl+C to stop\n"))

    try:
        proc = subprocess.Popen(uvicorn_cmd, cwd=str(_PROJECT_ROOT))
        proc.wait()
    except KeyboardInterrupt:
        print("\n[serve] Shutting down...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode or 0


# ---- dev --------------------------------------------------------------------

def cmd_dev(args: argparse.Namespace) -> int:
    """Start backend + React frontend via scripts/dev.mjs."""
    dev_script = _PROJECT_ROOT / "scripts" / "dev.mjs"
    if not dev_script.exists():
        sys.exit(_err(f"dev launcher not found: {dev_script}"))

    node = shutil.which("node")
    if not node:
        sys.exit(_err("'node' not found in PATH. Install Node.js to use 'aiteam dev'."))

    try:
        proc = subprocess.Popen([node, str(dev_script)], cwd=str(_PROJECT_ROOT))
        proc.wait()
    except KeyboardInterrupt:
        print("\n[dev] Shutting down...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode or 0


# ---- heartbeat --------------------------------------------------------------

def cmd_heartbeat(args: argparse.Namespace) -> int:
    """Run the heartbeat loop without the HTTP server."""
    import asyncio
    from aiteam.adapters.registry import build_default_registry
    from aiteam.heartbeat.executor import RunExecutor
    from aiteam.heartbeat.loop import HeartbeatLoop

    db_path = Path(args.db) if args.db else _default_db()
    if not db_path.exists():
        # Apply schema
        _apply_schema(db_path)

    registry = build_default_registry()
    executor = RunExecutor(db_path, registry)
    loop = HeartbeatLoop(db_path, executor, tick_interval_sec=float(args.interval))

    print(_head("\n  AI Teams — heartbeat loop"))
    print(f"  DB   → {_dim(str(db_path))}")
    print(f"  Tick → {args.interval}s")
    print(_dim("  Press Ctrl+C to stop\n"))

    async def _run() -> None:
        if args.once:
            n = await loop.run_once()
            print(f"  Dispatched {n} run(s).")
        else:
            await loop.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[heartbeat] Stopped.")
    return 0


def _apply_schema(db_path: Path) -> None:
    schema = _PROJECT_ROOT / "aiteam" / "db" / "schema.sql"
    if not schema.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=20)) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema.read_text(encoding="utf-8"))


# ---- status -----------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Print a snapshot of the current system state from the DB."""
    db_path = Path(args.db) if args.db else _default_db()
    workspace = _load_current_workspace() or _PROJECT_ROOT

    print(_head("\n  AI Teams — system status"))
    print(f"  Workspace  {_dim(str(workspace))}")
    print(f"  Database   {_dim(str(db_path))}")

    if not db_path.exists():
        print(_warn("  Database does not exist yet. Run 'aiteam serve' to initialise."))
        return 0

    with contextlib.closing(_db_connect(db_path)) as conn:
        # Issues by status
        issue_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM issues GROUP BY status ORDER BY n DESC"
        ).fetchall()
        # Pending wakeups
        pending_wakeups = conn.execute(
            "SELECT COUNT(*) FROM wakeup_requests WHERE status = 'pending'"
        ).fetchone()[0]
        # Running runs
        running_runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'running'"
        ).fetchone()[0]
        # Recent failures
        recent_failures = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status = 'failed' "
            "AND started_at > datetime('now', '-1 hour')"
        ).fetchone()[0]
        # Agent count
        agent_count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]

    print()
    print(f"  Agents     {_c(_BOLD, str(agent_count))}")
    print(f"  Running    {_c(_CYAN, str(running_runs))} run(s)")
    print(f"  Pending    {_c(_YELLOW, str(pending_wakeups))} wakeup(s)")
    if recent_failures:
        print(f"  Failures   {_c(_RED, str(recent_failures))} run(s) failed in last hour")

    if issue_rows:
        print()
        print(_c(_BOLD, "  Issues by status"))
        for row in issue_rows:
            bar = "█" * min(row["n"], 40)
            print(f"    {_fmt_status(row['status']):<22} {_c(_DIM, bar)}  {row['n']}")

    return 0


# ---- project list / create / use --------------------------------------------

def cmd_project_list(args: argparse.Namespace) -> int:
    projects_root = _get_projects_root()
    current = (_load_current_workspace() or Path()).resolve()

    print(_head("\n  Projects"))
    print(f"  Root: {_dim(str(projects_root))}\n")

    found: list[dict[str, Any]] = []
    if projects_root.exists():
        for entry in sorted(projects_root.iterdir()):
            if not entry.is_dir():
                continue
            # Skip soft-deleted tombstone dirs
            if entry.name.startswith(".aiteam-deleted-"):
                continue
            db = _resolve_runtime_dir(entry) / "aiteam.db"
            is_active = entry.resolve() == current
            found.append({
                "name": entry.name,
                "active": "●" if is_active else " ",
                "db": "yes" if db.exists() else "no",
                "path": str(entry),
            })

    if not found:
        print(_dim("  No projects found."))
        print(f"  Create one with: {_c(_CYAN, 'aiteam project create <name>')}")
        return 0

    if getattr(args, "json", False):
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0

    for p in found:
        active_marker = _c(_GREEN, "●") if p["active"] == "●" else " "
        db_tag = _ok("db") if p["db"] == "yes" else _warn("no db")
        print(f"  {active_marker} {_c(_BOLD, p['name']):<30} {db_tag}  {_dim(p['path'])}")
    return 0


def cmd_project_create(args: argparse.Namespace) -> int:
    name: str = args.name
    task: str | None = getattr(args, "task", None)

    projects_root = _get_projects_root()
    projects_root.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in name).strip()
    project_dir = projects_root / safe_name

    if project_dir.exists():
        print(_warn(f"Project '{safe_name}' already exists at {project_dir}"))
        return 1

    project_dir.mkdir(parents=True)
    runtime_dir = project_dir / ".aiteam"
    runtime_dir.mkdir()

    # Copy template configs
    config_dir = _PROJECT_ROOT / "config"
    for template_name, target_name in [
        ("control_plane.example.json", "control_plane.json"),
        ("agents.example.json", "agents.json"),
    ]:
        src = config_dir / template_name
        dst = runtime_dir / target_name
        if src.exists() and not dst.exists():
            import shutil as _shutil
            _shutil.copy2(src, dst)

    # Initialise DB
    db_path = runtime_dir / "aiteam.db"
    _apply_schema(db_path)

    # Persist as current workspace
    _set_current_workspace(project_dir)

    print(_ok(f"Project '{safe_name}' created"))
    print(f"  Path → {_dim(str(project_dir))}")
    print(f"  DB   → {_dim(str(db_path))}")

    if task:
        # Create an initial issue
        from aiteam.db.issues import create_issue
        issue = create_issue(db_path, title=task, status="backlog")
        print(_ok(f"Initial issue created: [{issue['id'][:8]}] {task}"))

    return 0


def cmd_project_use(args: argparse.Namespace) -> int:
    target = Path(args.path).resolve()
    if not target.exists():
        sys.exit(_err(f"Path does not exist: {target}"))

    db = _resolve_runtime_dir(target) / "aiteam.db"
    if not db.exists():
        print(_warn(f"No aiteam.db found in {target}. Switching anyway."))

    _set_current_workspace(target)
    print(_ok(f"Active project → {target}"))
    return 0


def _get_projects_root() -> Path:
    try:
        from aiteam.user_config import get_projects_root
        r = get_projects_root()
        if r is not None:
            return r
    except Exception:
        pass
    env = os.environ.get("AITEAM_PROJECTS_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    return _PROJECT_ROOT.parent


def _set_current_workspace(path: Path) -> None:
    state = _PROJECT_ROOT / "runtime" / "current_workspace.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"workspace": str(path.resolve())}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---- issue list / show / create ---------------------------------------------

def cmd_issue_list(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else _default_db()
    status_filter: str | None = getattr(args, "status", None)
    limit: int = getattr(args, "limit", 30)

    with contextlib.closing(_db_connect(db_path)) as conn:
        query = "SELECT id, title, status, role, assignee_agent_id, created_at FROM issues"
        params: list[Any] = []
        if status_filter:
            query += " WHERE status = ?"
            params.append(status_filter)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()

    if getattr(args, "json", False):
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print(_dim("  No issues found."))
        return 0

    print(_head(f"\n  Issues  {_dim('(' + str(len(rows)) + ')')}"))
    print()
    for row in rows:
        short_id = str(row["id"])[:8]
        status   = _fmt_status(row["status"] or "")
        role     = _dim(f"[{row['role']}]") if row["role"] else ""
        title    = row["title"] or "(untitled)"
        print(f"  {_dim(short_id)}  {status:<22}  {title}  {role}")

    return 0


def cmd_issue_show(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else _default_db()
    issue_id: str = args.id

    with contextlib.closing(_db_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM issues WHERE id = ? OR id LIKE ?",
            (issue_id, f"{issue_id}%"),
        ).fetchone()
        if not row:
            sys.exit(_err(f"Issue not found: {issue_id}"))
        row_dict = dict(row)

        # Recent comments
        comments = conn.execute(
            "SELECT author_agent_id, body, created_at FROM comments "
            "WHERE issue_id = ? ORDER BY created_at DESC LIMIT 5",
            (row_dict["id"],),
        ).fetchall()

    if getattr(args, "json", False):
        row_dict["recent_comments"] = [dict(c) for c in comments]
        print(json.dumps(row_dict, ensure_ascii=False, indent=2))
        return 0

    print(_head(f"\n  Issue {row_dict['id'][:8]}"))
    print(f"  Title      {_c(_BOLD, row_dict['title'] or '')}")
    print(f"  Status     {_fmt_status(row_dict['status'] or '')}")
    print(f"  Role       {row_dict.get('role') or _dim('—')}")
    print(f"  Assignee   {row_dict.get('assignee_agent_id') or _dim('—')}")
    print(f"  Created    {_fmt_dt(row_dict.get('created_at'))}")
    if row_dict.get("description"):
        print(f"\n  Description\n  {_dim(row_dict['description'][:300])}")

    if comments:
        print(f"\n  Recent comments ({len(comments)})")
        for c in comments:
            author = c["author_agent_id"] or "system"
            body_preview = (c["body"] or "")[:120].replace("\n", " ")
            print(f"    {_dim(_fmt_dt(c['created_at']))}  {_c(_CYAN, author)}")
            print(f"    {body_preview}")
            print()

    return 0


def cmd_issue_create(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else _default_db()
    if not db_path.exists():
        _apply_schema(db_path)

    from aiteam.db.issues import create_issue
    issue = create_issue(
        db_path,
        title=args.title,
        status=getattr(args, "status", "backlog") or "backlog",
        description=getattr(args, "description", None),
        role=getattr(args, "role", None),
    )
    print(_ok(f"Issue created: [{issue['id'][:8]}] {args.title}"))
    if getattr(args, "json", False):
        print(json.dumps(issue, ensure_ascii=False, indent=2))
    return 0


# ---- run list / trigger -----------------------------------------------------

def cmd_run_list(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else _default_db()
    limit: int = getattr(args, "limit", 20)

    with contextlib.closing(_db_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.agent_id, r.status, r.issue_id,
                   r.started_at, r.finished_at, r.adapter_type
            FROM runs r
            ORDER BY r.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    if getattr(args, "json", False):
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print(_dim("  No runs found."))
        return 0

    print(_head(f"\n  Runs  {_dim('(' + str(len(rows)) + ')')}"))
    print()
    for row in rows:
        short_id    = str(row["id"])[:8]
        agent       = row["agent_id"] or "?"
        status      = _fmt_status(row["status"] or "")
        issue       = _dim(f"issue:{str(row['issue_id'] or '')[:8]}") if row["issue_id"] else ""
        adapter     = _dim(row["adapter_type"] or "")
        started     = _fmt_dt(row["started_at"])
        print(f"  {_dim(short_id)}  {agent:<20}  {status:<22}  {started}  {issue}  {adapter}")

    return 0


def cmd_run_trigger(args: argparse.Namespace) -> int:
    db_path = Path(args.db) if args.db else _default_db()
    agent_id: str = args.agent_id
    issue_id: str = args.issue_id

    from aiteam.db.wakeups import enqueue_wakeup
    wakeup = enqueue_wakeup(
        db_path,
        agent_id=agent_id,
        source="cli_manual",
        reason=f"Manually triggered via CLI by operator",
        payload={"issue_id": issue_id},
        idempotency_key=f"cli_manual:{agent_id}:{issue_id}:{int(time.time())}",
    )
    print(_ok(f"Wakeup enqueued for agent '{agent_id}' on issue '{issue_id[:8]}'"))
    print(f"  Wakeup ID → {_dim(str(wakeup.get('id', '?')))}")
    return 0


# ---- legacy sub-commands (kept for back-compat) ─────────────────────────────

def cmd_system_check(_args: argparse.Namespace) -> int:
    from aiteam.adapters import build_default_registry
    registry = build_default_registry()
    payload = {
        "control_plane": "v2",
        "legacy_round_orchestrator": "retired",
        "adapters": [d.adapter_type for d in registry.descriptors()],
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from aiteam.db.migration import migrate_to_v2
    summary = migrate_to_v2(Path(args.db), apply=bool(args.apply), backup=not bool(args.no_backup))
    data = summary.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        for key, value in data.items():
            print(f"{key}: {value}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    from aiteam.db.finops import check_budget
    status = check_budget(Path(args.db), agent_id=args.agent_id, period=args.period)
    print(json.dumps(status.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0


# ── argument parser ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiteam",
        description="AI Teams orchestrator — command-line interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  aiteam serve                        Start the API backend on port 8010
  aiteam serve --port 9000 --reload   Dev mode with auto-reload
  aiteam dev                          Start backend + React frontend
  aiteam heartbeat --once             Run one heartbeat tick and exit
  aiteam status                       Show issue/run counts from the DB
  aiteam project list                 List all projects
  aiteam project create "My App"      Create a new project
  aiteam project use /path/to/proj    Switch active project
  aiteam issue list --status blocked  List blocked issues
  aiteam issue create "Fix login"     Create an issue
  aiteam issue show abc123            Show issue details
  aiteam run list --limit 5           Last 5 runs
  aiteam run trigger lead abc123      Manually wake the lead on an issue
  aiteam system-check                 Adapter smoke test
  aiteam budget-status --agent-id lead
""",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # serve
    p_serve = sub.add_parser("serve", help="Start the API backend")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8010)
    p_serve.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload")
    p_serve.add_argument("--no-heartbeat", action="store_true", help="Skip embedded heartbeat loop")

    # dev
    sub.add_parser("dev", help="Start backend + React frontend (requires Node.js)")

    # heartbeat
    p_hb = sub.add_parser("heartbeat", help="Run heartbeat loop without HTTP server")
    p_hb.add_argument("--db", default=None, help="Path to aiteam.db")
    p_hb.add_argument("--interval", type=float, default=30.0, help="Tick interval in seconds")
    p_hb.add_argument("--once", action="store_true", help="Run one tick and exit")

    # status
    p_st = sub.add_parser("status", help="Show system status from the DB")
    p_st.add_argument("--db", default=None)
    p_st.add_argument("--json", action="store_true")

    # project sub-group
    p_proj = sub.add_parser("project", help="Manage projects")
    proj_sub = p_proj.add_subparsers(dest="project_command", metavar="<subcommand>")

    pp_list = proj_sub.add_parser("list", help="List projects")
    pp_list.add_argument("--json", action="store_true")

    pp_create = proj_sub.add_parser("create", help="Create a new project")
    pp_create.add_argument("name", help="Project name")
    pp_create.add_argument("--task", default=None, help="Optional initial task/issue title")

    pp_use = proj_sub.add_parser("use", help="Switch active project")
    pp_use.add_argument("path", help="Path to the project directory")

    # issue sub-group
    p_issue = sub.add_parser("issue", help="Manage issues")
    issue_sub = p_issue.add_subparsers(dest="issue_command", metavar="<subcommand>")

    pi_list = issue_sub.add_parser("list", help="List issues")
    pi_list.add_argument("--db", default=None)
    pi_list.add_argument("--status", default=None, help="Filter by status")
    pi_list.add_argument("--limit", type=int, default=30)
    pi_list.add_argument("--json", action="store_true")

    pi_show = issue_sub.add_parser("show", help="Show issue details")
    pi_show.add_argument("id", help="Issue ID or prefix")
    pi_show.add_argument("--db", default=None)
    pi_show.add_argument("--json", action="store_true")

    pi_create = issue_sub.add_parser("create", help="Create an issue")
    pi_create.add_argument("title")
    pi_create.add_argument("--description", default=None)
    pi_create.add_argument("--role", default=None)
    pi_create.add_argument("--status", default="backlog")
    pi_create.add_argument("--db", default=None)
    pi_create.add_argument("--json", action="store_true")

    # run sub-group
    p_run = sub.add_parser("run", help="Manage runs")
    run_sub = p_run.add_subparsers(dest="run_command", metavar="<subcommand>")

    pr_list = run_sub.add_parser("list", help="List recent runs")
    pr_list.add_argument("--db", default=None)
    pr_list.add_argument("--limit", type=int, default=20)
    pr_list.add_argument("--json", action="store_true")

    pr_trigger = run_sub.add_parser("trigger", help="Manually trigger a wakeup for an agent")
    pr_trigger.add_argument("agent_id")
    pr_trigger.add_argument("issue_id")
    pr_trigger.add_argument("--db", default=None)

    # legacy commands
    sub.add_parser("system-check", help="Adapter smoke test")

    p_mig = sub.add_parser("migrate-to-v2", help="Run SQLite v2 migration")
    p_mig.add_argument("--db", default="runtime/aiteam.db")
    p_mig.add_argument("--apply", action="store_true")
    p_mig.add_argument("--no-backup", action="store_true")
    p_mig.add_argument("--json", action="store_true")

    p_bud = sub.add_parser("budget-status", help="Show monthly budget for an agent")
    p_bud.add_argument("--db", default="runtime/aiteam.db")
    p_bud.add_argument("--agent-id", required=True)
    p_bud.add_argument("--period", default=None)

    return parser


# ── entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    _enable_win_ansi()
    # Ensure stdout/stderr use UTF-8 on Windows (avoids cp1252 UnicodeEncodeError)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    # ── top-level dispatch ────────────────────────────────────────────────
    if args.command == "serve":
        return cmd_serve(args)
    if args.command == "dev":
        return cmd_dev(args)
    if args.command == "heartbeat":
        return cmd_heartbeat(args)
    if args.command == "status":
        return cmd_status(args)
    if args.command == "system-check":
        return cmd_system_check(args)
    if args.command == "migrate-to-v2":
        return cmd_migrate(args)
    if args.command == "budget-status":
        return cmd_budget(args)

    # ── project dispatch ──────────────────────────────────────────────────
    if args.command == "project":
        pc = getattr(args, "project_command", None)
        if pc is None:
            parser.parse_args(["project", "--help"])
            return 0
        if pc == "list":
            return cmd_project_list(args)
        if pc == "create":
            return cmd_project_create(args)
        if pc == "use":
            return cmd_project_use(args)

    # ── issue dispatch ────────────────────────────────────────────────────
    if args.command == "issue":
        ic = getattr(args, "issue_command", None)
        if ic is None:
            parser.parse_args(["issue", "--help"])
            return 0
        if ic == "list":
            return cmd_issue_list(args)
        if ic == "show":
            return cmd_issue_show(args)
        if ic == "create":
            return cmd_issue_create(args)

    # ── run dispatch ──────────────────────────────────────────────────────
    if args.command == "run":
        rc = getattr(args, "run_command", None)
        if rc is None:
            parser.parse_args(["run", "--help"])
            return 0
        if rc == "list":
            return cmd_run_list(args)
        if rc == "trigger":
            return cmd_run_trigger(args)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
