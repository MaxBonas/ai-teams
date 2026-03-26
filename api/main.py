import asyncio
import logging
import os
import json
import time
import uuid
import re
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import subprocess
import threading
import sys
import json as std_json

try:
    from dotenv import load_dotenv

    _root_env = Path(__file__).parent.parent / ".env"
    if _root_env.exists():
        load_dotenv(_root_env)
except ImportError:
    pass

# Import AI Team Dashboard requirements
from aiteam.dashboard import build_dashboard_payload
from aiteam.cli import build_default_orchestrator, cmd_notebooklm_sync
from aiteam.persistence import AtomicFileWriter
from aiteam.pilot import compute_pilot_metrics
from aiteam.types import Complexity, Criticality, Role, WorkTask

logger = logging.getLogger(__name__)


class SimplePTY:
    def __init__(self, cols, rows):
        self.proc = None

    def spawn(self, cmd, cwd=None):
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def write(self, data):
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def read(self, size):
        if self.proc and self.proc.stdout:
            return self.proc.stdout.read(1)
        return ""

    def set_size(self, cols, rows):
        pass

    def isalive(self):
        if not self.proc:
            return False
        return self.proc.poll() is None

    def close(self):
        if self.proc:
            self.proc.terminate()


try:
    from pywinpty import PTY
except ImportError:
    PTY = SimplePTY

app = FastAPI(title="AI Teams IDE Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:9483",
        "http://127.0.0.1:9483",
        "http://localhost:9490",
        "http://127.0.0.1:9490",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

active_pty = None


class WorkspacePath(BaseModel):
    path: str


class NewProjectRequest(BaseModel):
    name: str


class TeamChatRequest(BaseModel):
    message: str
    role: str = "engineer"
    complexity: str = "medium"
    criticality: str = "medium"
    mode: str = "sprint5"
    max_rounds: int | None = None
    client_task_id: str = ""
    strict_mode: bool = False
    auto_extend_weak_runs: bool = True
    allow_low_productivity_override: bool = False


class TeamChatResponse(BaseModel):
    task_id: str
    role: str
    state: str
    response: str
    decision_justification: str
    elapsed_ms: int
    lead_task_id: str
    delegated_task_ids: list[str]
    phase_task_ids: dict[str, str]
    chat_mode: str = "sprint5"
    round_budget: int = 0
    rounds_used: int = 0
    completed_tasks: int = 0
    pending_tasks: int = 0
    continuation_requested: bool = False
    continuation_of: str = ""
    artifact_created: int = 0
    artifact_modified: int = 0
    artifact_files: list[str] = []
    productivity_score: int = 0
    reasoning_score: int = 0
    productivity_status: str = "weak"
    execution_attempts: int = 0
    execution_success: int = 0
    execution_steps: int = 0
    next_action_hint: str = ""
    strict_mode: bool = False
    strict_mode_applied: bool = False
    auto_extended_rounds: int = 0
    productivity_threshold: int = 35
    low_productivity_rejected: bool = False
    low_productivity_override: bool = False
    execution_mode: str = "simulated"
    placeholder_outputs: int = 0
    placeholder_output_ratio: float = 0.0
    evidence_gate_applied: bool = False
    evidence_gate_failures: list[str] = []
    execution_steps_success: int = 0
    successful_checks: list[str] = []
    successful_check_count: int = 0
    live_mode_required: bool = False
    live_mode_rejected: bool = False


class TeamChatProgressResponse(BaseModel):
    task_id: str
    exists: bool = False
    state: str = "queued"
    round_budget: int = 0
    rounds_used: int = 0
    phase_states: dict[str, str] = {}
    completed_tasks: int = 0
    pending_tasks: int = 0
    failed_tasks: int = 0
    execution_attempts: int = 0
    execution_steps: int = 0
    execution_steps_success: int = 0
    execution_mode: str = "queued"
    placeholder_outputs: int = 0
    successful_checks: list[str] = []
    successful_check_count: int = 0
    live_mode_required: bool = False
    live_mode_rejected: bool = False
    evidence_gate_rejected: bool = False
    evidence_gate_failures: list[str] = []
    last_event: str = ""
    last_event_ts: str = ""


class OperatorTimelineItem(BaseModel):
    ts: str = ""
    event_type: str = ""
    task_id: str = ""
    level: str = "info"
    summary: str = ""
    assignee: str = ""
    execution_round: int = 0
    execution_sub_iteration: int = 0
    gate_iteration: int = 0
    blocked_reason: str = ""
    handoff_from: str = ""
    handoff_to: str = ""
    conversation_thread_id: str = ""
    meeting_kind: str = ""
    artifact_created: int = 0
    artifact_modified: int = 0
    artifact_files: list[str] = []
    productivity_score: int = 0
    reasoning_score: int = 0


class OperatorTimelineResponse(BaseModel):
    selected_task_id: str = ""
    latest_task_id: str = ""
    available_runs: list[str] = []
    total: int = 0
    items: list[OperatorTimelineItem] = []
    progress: TeamChatProgressResponse | None = None


def _normalize_chat_mode(raw_mode: str) -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"classic", "legacy", "pipeline", "phased"}:
        return "classic"
    return "sprint5"


def _resolve_chat_round_budget(
    requested_rounds: int | None,
    chat_mode: str,
    complexity: Complexity,
    criticality: Criticality,
) -> int:
    if isinstance(requested_rounds, int):
        return max(3, min(requested_rounds, 80))
    if chat_mode == "sprint5":
        return 5
    return _chat_round_budget(complexity=complexity, criticality=criticality)


def _recent_chat_roots(
    runtime_dir: Path, max_chats: int = 4
) -> list[dict[str, object]]:
    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
    roots = _group_chat_roots(tasks_payload)
    if not roots:
        return []

    events = _read_jsonl_records(runtime_dir / "events.jsonl")
    task_started_ts: dict[str, str] = {}
    for event in events:
        if str(event.get("event_type", "")) != "task_started":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id", "") or "")
        if not task_id.startswith("CHAT-"):
            continue
        root = task_id.split("::", 1)[0]
        ts = str(event.get("ts", "") or "")
        current = task_started_ts.get(root, "")
        if ts > current:
            task_started_ts[root] = ts

    for root_id, item in roots.items():
        item["latest_ts"] = task_started_ts.get(root_id, "")

    ordered = sorted(
        roots.values(),
        key=lambda row: str(row.get("latest_ts", "")),
        reverse=True,
    )
    return ordered[: max(1, max_chats)]


def _is_continuation_message(message: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(message or "")).strip().lower()
    normalized = normalized.strip(".!? ")
    if not normalized:
        return False

    direct = {
        "continue",
        "continue please",
        "continua",
        "continua por favor",
        "continúe",
        "proceed",
        "go on",
        "carry on",
        "sigue",
        "seguir",
    }
    if normalized in direct:
        return True

    return bool(
        re.match(
            r"^(continue|continua|continúe|proceed|go on|carry on|sigue|seguir)(\b|$)",
            normalized,
        )
    )


def _extract_chat_root_from_message(message: str) -> str:
    text = str(message or "")
    match = re.search(r"\bCHAT-([0-9a-fA-F]{8})\b", text)
    if not match:
        return ""
    return f"CHAT-{match.group(1).upper()}"


def _resolve_task_root(client_task_id: str) -> str:
    candidate = str(client_task_id or "").strip().upper()
    if re.match(r"^CHAT-[0-9A-F]{8}$", candidate):
        return candidate
    return f"CHAT-{uuid.uuid4().hex[:8].upper()}"


def _safe_int_value(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except Exception:
        return default


def _normalize_task_root(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "::" in text:
        text = text.split("::", 1)[0]
    candidate = text.upper()
    if re.match(r"^CHAT-[0-9A-F]{8}$", candidate):
        return candidate
    return ""


def _env_bool(key: str, default: bool = False) -> bool:
    raw = str(os.getenv(key, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_game_request(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    hints = ["juego", "game", "arcade", "platformer", "minijuego", "videojuego"]
    return any(token in normalized for token in hints)


def _is_game_followup_request(workspace: Path, message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    has_game_files = (
        (workspace / "game.js").exists()
        or (workspace / "index.html").exists()
        or (workspace / ".aiteam_game_progress.json").exists()
    )
    if not has_game_files:
        return False
    followup_hints = [
        "continue",
        "continua",
        "continúe",
        "sigue",
        "next slice",
        "next step",
        "highest-impact",
        "design",
        "diseno",
        "diseño",
        "gameplay",
        "iteracion",
        "iteración",
    ]
    return any(token in normalized for token in followup_hints)


def _workspace_artifact_snapshot(workspace: Path) -> dict[str, tuple[int, int]]:
    skip_dirs = {
        "runtime",
        ".git",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".pytest_cache",
    }
    snapshot: dict[str, tuple[int, int]] = {}
    if not workspace.exists():
        return snapshot

    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in skip_dirs for part in relative.parts):
            continue
        key = relative.as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[key] = (int(stat.st_mtime_ns), int(stat.st_size))
    return snapshot


def _workspace_artifact_diff(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    created = sorted(path for path in after.keys() if path not in before)
    modified = sorted(
        path for path in after.keys() if path in before and after[path] != before[path]
    )
    return created, modified


def _read_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_json_dict(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _materialize_game_iteration(workspace: Path, message: str) -> dict[str, object]:
    progress_path = workspace / ".aiteam_game_progress.json"
    is_initial_bootstrap = not progress_path.exists()
    should_apply = is_initial_bootstrap and (
        _is_game_request(message) or _is_game_followup_request(workspace, message)
    )
    if not should_apply:
        return {
            "applied": False,
            "iteration": 0,
            "files": [],
            "reason": "bootstrap_already_done"
            if not is_initial_bootstrap
            else "not_game_request",
        }

    iteration = 1

    index_html = workspace / "index.html"
    styles_css = workspace / "styles.css"
    game_js = workspace / "game.js"
    readme_md = workspace / "README.md"

    html_content = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>Juego Test</title>
    <link rel=\"stylesheet\" href=\"styles.css\" />
  </head>
  <body>
    <main class=\"app\">
      <h1>Juego Test</h1>
      <p class=\"hint\">Move with arrow keys or WASD. Collect stars and avoid hazards.</p>
      <canvas id=\"game\" width=\"640\" height=\"400\"></canvas>
      <div class=\"hud\">
        <span id=\"score\">Score: 0</span>
        <span id=\"status\">Status: ready</span>
      </div>
    </main>
    <script src=\"game.js\"></script>
  </body>
</html>
"""

    css_content = """* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  place-items: center;
  font-family: \"Segoe UI\", Tahoma, sans-serif;
  background: radial-gradient(circle at 20% 10%, #1d2a3a, #0a1018 60%);
  color: #f3f7fb;
}
.app { width: min(94vw, 760px); text-align: center; }
h1 { margin: 0 0 8px; }
.hint { margin: 0 0 12px; color: #b9c6d6; font-size: 14px; }
canvas {
  width: 100%;
  border: 1px solid #2e435a;
  border-radius: 10px;
  background: linear-gradient(180deg, #102033, #0e1827);
}
.hud {
  margin-top: 10px;
  display: flex;
  justify-content: space-between;
  color: #d2dceb;
  font-size: 14px;
}
"""

    game_v1 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 1,
  running: true,
  player: { x: 320, y: 200, size: 16, speed: 3 },
  star: { x: 120, y: 100, size: 10 },
  keys: new Set(),
};

function randomPoint(padding = 20) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => {
  state.keys.add(event.key.toLowerCase());
});

window.addEventListener('keyup', (event) => {
  state.keys.delete(event.key.toLowerCase());
});

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    resetStar();
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  statusLabel.textContent = `Status: running · level ${state.level}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    game_v2 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 2,
  running: true,
  timeLeft: 60,
  player: { x: 320, y: 200, size: 16, speed: 3.2 },
  star: { x: 120, y: 100, size: 10 },
  hazard: { x: 200, y: 180, size: 12, vx: 2.1, vy: 1.7 },
  keys: new Set(),
};

function randomPoint(padding = 20) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => state.keys.add(event.key.toLowerCase()));
window.addEventListener('keyup', (event) => state.keys.delete(event.key.toLowerCase()));

setInterval(() => {
  if (!state.running) return;
  state.timeLeft -= 1;
  if (state.timeLeft <= 0) {
    state.running = false;
    statusLabel.textContent = `Status: finished · final score ${state.score}`;
  }
}, 1000);

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  state.hazard.x += state.hazard.vx;
  state.hazard.y += state.hazard.vy;
  if (state.hazard.x < 10 || state.hazard.x > canvas.width - 10) state.hazard.vx *= -1;
  if (state.hazard.y < 10 || state.hazard.y > canvas.height - 10) state.hazard.vy *= -1;

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    resetStar();
  }

  if (intersects(p, state.hazard, 14)) {
    state.score = Math.max(0, state.score - 15);
    scoreLabel.textContent = `Score: ${state.score}`;
    const next = randomPoint();
    state.hazard.x = next.x;
    state.hazard.y = next.y;
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  drawRect(state.hazard.x, state.hazard.y, state.hazard.size, '#fb7185');
  statusLabel.textContent = state.running
    ? `Status: running · level ${state.level} · ${state.timeLeft}s`
    : `Status: finished · final score ${state.score}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    game_v3 = """const canvas = document.getElementById('game');
const ctx = canvas.getContext('2d');
const scoreLabel = document.getElementById('score');
const statusLabel = document.getElementById('status');

const state = {
  score: 0,
  level: 3,
  running: true,
  wave: 1,
  player: { x: 320, y: 200, size: 16, speed: 3.4 },
  star: { x: 120, y: 100, size: 10 },
  hazards: [
    { x: 180, y: 140, size: 11, vx: 1.8, vy: 1.2 },
    { x: 460, y: 240, size: 11, vx: -1.6, vy: 1.5 },
  ],
  keys: new Set(),
};

function randomPoint(padding = 24) {
  return {
    x: padding + Math.random() * (canvas.width - padding * 2),
    y: padding + Math.random() * (canvas.height - padding * 2),
  };
}

function resetStar() {
  const next = randomPoint();
  state.star.x = next.x;
  state.star.y = next.y;
}

function drawRect(x, y, size, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - size / 2, y - size / 2, size, size);
}

function intersects(a, b, threshold) {
  return Math.abs(a.x - b.x) < threshold && Math.abs(a.y - b.y) < threshold;
}

window.addEventListener('keydown', (event) => state.keys.add(event.key.toLowerCase()));
window.addEventListener('keyup', (event) => state.keys.delete(event.key.toLowerCase()));

function update() {
  if (!state.running) return;
  const p = state.player;
  if (state.keys.has('arrowleft') || state.keys.has('a')) p.x -= p.speed;
  if (state.keys.has('arrowright') || state.keys.has('d')) p.x += p.speed;
  if (state.keys.has('arrowup') || state.keys.has('w')) p.y -= p.speed;
  if (state.keys.has('arrowdown') || state.keys.has('s')) p.y += p.speed;

  p.x = Math.max(8, Math.min(canvas.width - 8, p.x));
  p.y = Math.max(8, Math.min(canvas.height - 8, p.y));

  for (const hazard of state.hazards) {
    hazard.x += hazard.vx;
    hazard.y += hazard.vy;
    if (hazard.x < 10 || hazard.x > canvas.width - 10) hazard.vx *= -1;
    if (hazard.y < 10 || hazard.y > canvas.height - 10) hazard.vy *= -1;
    if (intersects(p, hazard, 14)) {
      state.running = false;
    }
  }

  if (intersects(p, state.star, 14)) {
    state.score += 10;
    scoreLabel.textContent = `Score: ${state.score}`;
    if (state.score % 50 === 0) {
      state.wave += 1;
      state.hazards.push({
        x: randomPoint().x,
        y: randomPoint().y,
        size: 10 + state.wave,
        vx: 1 + Math.random() * 2,
        vy: 1 + Math.random() * 2,
      });
    }
    resetStar();
  }
}

function render() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  drawRect(state.player.x, state.player.y, state.player.size, '#7dd3fc');
  drawRect(state.star.x, state.star.y, state.star.size, '#facc15');
  for (const hazard of state.hazards) {
    drawRect(hazard.x, hazard.y, hazard.size, '#fb7185');
  }
  statusLabel.textContent = state.running
    ? `Status: running · level ${state.level} · wave ${state.wave}`
    : `Status: game over · score ${state.score}`;
}

function loop() {
  update();
  render();
  requestAnimationFrame(loop);
}

resetStar();
loop();
"""

    readme = f"""# Juego Test

Generated by AI Team artifact-first bootstrap.

## Run

Open `index.html` in your browser.

## Iteration

Current automatic game iteration: {iteration}

## Controls

- Arrow keys / WASD to move.
- Collect yellow stars.
- Avoid hazards.
"""

    if iteration <= 1:
        game_content = game_v1
    elif iteration == 2:
        game_content = game_v2
    else:
        game_content = game_v3

    index_html.write_text(html_content, encoding="utf-8")
    styles_css.write_text(css_content, encoding="utf-8")
    game_js.write_text(game_content, encoding="utf-8")
    readme_md.write_text(readme, encoding="utf-8")

    progress_payload = {
        "iteration": iteration,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "artifact_first_game_bootstrap",
        "last_message": str(message or "")[:300],
    }
    _write_json_dict(progress_path, progress_payload)

    return {
        "applied": True,
        "iteration": iteration,
        "files": [
            "index.html",
            "styles.css",
            "game.js",
            "README.md",
            ".aiteam_game_progress.json",
        ],
    }


def _build_chat_progress(runtime_dir: Path, task_root: str) -> TeamChatProgressResponse:
    normalized_root = _normalize_task_root(task_root)
    if not normalized_root:
        return TeamChatProgressResponse(task_id="", exists=False)

    phase_states: dict[str, str] = {}
    rounds_used = 0
    round_budget = 0
    exists = False
    failed_tasks = 0
    execution_attempts = 0
    execution_steps = 0
    execution_steps_success = 0
    execution_mode = "queued"
    placeholder_outputs = 0
    successful_checks: list[str] = []
    evidence_gate_rejected = False
    evidence_gate_failures: list[str] = []
    live_mode_required = False
    live_mode_rejected = False

    tasks_payload = _read_json_payload(runtime_dir / "tasks.json", fallback=[])
    if isinstance(tasks_payload, list):
        for item in tasks_payload:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id", "") or "")
            task_id_upper = task_id.upper()
            if not task_id_upper.startswith(f"{normalized_root}::"):
                continue
            exists = True
            phase_name = task_id.split("::", 1)[1]
            state_value = str(item.get("state", "pending") or "pending")
            phase_states[phase_name] = state_value
            if state_value == "failed":
                failed_tasks += 1
            metadata = item.get("metadata", {})
            if isinstance(metadata, dict):
                rounds_used = max(
                    rounds_used, _safe_int_value(metadata.get("execution_round", 0), 0)
                )

    last_event = ""
    last_event_ts = ""
    exhausted = False
    root_event_seen = False
    for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
        event_type = str(record.get("event_type", "") or "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        event_task_id = str(payload.get("task_id", "") or "")
        event_task_id_upper = event_task_id.upper()
        is_root_related = (
            event_task_id_upper == normalized_root
            or event_task_id_upper.startswith(f"{normalized_root}::")
        )
        if not is_root_related:
            continue
        root_event_seen = True
        if event_type == "chat_plan_created" and event_task_id_upper == normalized_root:
            round_budget = max(
                round_budget, _safe_int_value(payload.get("round_budget", 0), 0)
            )
        if (
            event_type == "chat_auto_rounds_extended"
            and event_task_id_upper == normalized_root
        ):
            round_budget = max(
                round_budget, _safe_int_value(payload.get("to_round_budget", 0), 0)
            )
        if (
            event_type == "chat_execution_mode_assessed"
            and event_task_id_upper == normalized_root
        ):
            execution_mode = str(
                payload.get("execution_mode", execution_mode) or execution_mode
            )
            placeholder_outputs = max(
                placeholder_outputs,
                _safe_int_value(payload.get("placeholder_outputs", 0), 0),
            )
            live_mode_required = bool(
                payload.get("live_mode_required", live_mode_required)
            )
        if (
            event_type == "chat_quality_assessed"
            and event_task_id_upper == normalized_root
        ):
            raw_checks = payload.get("successful_checks", [])
            if isinstance(raw_checks, list):
                successful_checks = sorted(
                    {
                        str(item or "").strip()
                        for item in raw_checks
                        if str(item or "").strip()
                    }
                )
        if (
            event_type == "chat_evidence_gate_rejected"
            and event_task_id_upper == normalized_root
        ):
            evidence_gate_rejected = True
            raw_failures = payload.get("failures", [])
            if isinstance(raw_failures, list):
                evidence_gate_failures = [
                    str(item or "").strip()
                    for item in raw_failures
                    if str(item or "").strip()
                ][:12]
        if (
            event_type == "chat_live_mode_required_rejected"
            and event_task_id_upper == normalized_root
        ):
            live_mode_required = True
            live_mode_rejected = True
        if (
            event_type == "chat_window_exhausted"
            and event_task_id_upper == normalized_root
        ):
            exhausted = True
            rounds_used = max(
                rounds_used, _safe_int_value(payload.get("rounds_used", 0), 0)
            )
        if event_type == "task_execution":
            execution_attempts += 1
            rounds_used = max(
                rounds_used, _safe_int_value(payload.get("execution_round", 0), 0)
            )
        if event_type == "execution_step":
            execution_steps += 1
            if bool(payload.get("success", False)):
                execution_steps_success += 1
        last_event = _event_summary(event_type, payload)
        last_event_ts = str(record.get("ts", "") or "")

    exists = exists or root_event_seen
    completed_tasks = sum(1 for state in phase_states.values() if state == "completed")
    pending_tasks = sum(
        1
        for state in phase_states.values()
        if state in {"pending", "ready", "claimed", "blocked"}
    )
    lead_state = phase_states.get("lead_close", "")

    if not exists:
        return TeamChatProgressResponse(
            task_id=normalized_root,
            exists=False,
            state="queued",
            round_budget=round_budget,
            rounds_used=rounds_used,
            phase_states=phase_states,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            failed_tasks=failed_tasks,
            execution_attempts=execution_attempts,
            execution_steps=execution_steps,
            execution_steps_success=execution_steps_success,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
            successful_checks=successful_checks,
            successful_check_count=len(successful_checks),
            live_mode_required=live_mode_required,
            live_mode_rejected=live_mode_rejected,
            evidence_gate_rejected=evidence_gate_rejected,
            evidence_gate_failures=evidence_gate_failures,
            last_event=last_event,
            last_event_ts=last_event_ts,
        )

    if evidence_gate_rejected:
        progress_state = "rejected"
    elif failed_tasks > 0 or lead_state == "failed":
        progress_state = "failed"
    elif lead_state == "completed" and pending_tasks == 0:
        progress_state = "completed"
    elif exhausted:
        progress_state = "in_progress"
    elif pending_tasks > 0:
        progress_state = "running"
    elif completed_tasks > 0:
        progress_state = "completed"
    else:
        progress_state = "running"

    return TeamChatProgressResponse(
        task_id=normalized_root,
        exists=True,
        state=progress_state,
        round_budget=round_budget,
        rounds_used=rounds_used,
        phase_states=phase_states,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        failed_tasks=failed_tasks,
        execution_attempts=execution_attempts,
        execution_steps=execution_steps,
        execution_steps_success=execution_steps_success,
        execution_mode=execution_mode,
        placeholder_outputs=placeholder_outputs,
        successful_checks=successful_checks,
        successful_check_count=len(successful_checks),
        live_mode_required=live_mode_required,
        live_mode_rejected=live_mode_rejected,
        evidence_gate_rejected=evidence_gate_rejected,
        evidence_gate_failures=evidence_gate_failures,
        last_event=last_event,
        last_event_ts=last_event_ts,
    )


def _build_operator_timeline(
    runtime_dir: Path,
    *,
    task_id: str,
    limit: int,
    key_only: bool,
) -> OperatorTimelineResponse:
    recent_runs = _recent_chat_roots(runtime_dir, max_chats=24)
    available_runs: list[str] = []
    for item in recent_runs:
        if not isinstance(item, dict):
            continue
        root_id = _normalize_task_root(str(item.get("root_id", "") or ""))
        if root_id and root_id not in available_runs:
            available_runs.append(root_id)

    latest_task_id = available_runs[0] if available_runs else ""
    selected_task_id = _normalize_task_root(task_id) or latest_task_id

    if not selected_task_id:
        return OperatorTimelineResponse(
            selected_task_id="",
            latest_task_id="",
            available_runs=available_runs,
            total=0,
            items=[],
            progress=None,
        )

    key_events = {
        "chat_plan_created",
        "task_execution",
        "execution_step",
        "chat_artifact_bootstrap",
        "chat_artifacts_detected",
        "chat_auto_rounds_extended",
        "chat_quality_assessed",
        "chat_strict_mode_blocked_close",
        "chat_low_productivity_rejected",
        "chat_low_productivity_override",
        "chat_window_exhausted",
        "task_failed",
    }

    records = _read_jsonl_records(runtime_dir / "events.jsonl")
    timeline_items: list[OperatorTimelineItem] = []
    for record in records:
        event_type = str(record.get("event_type", "") or "")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue

        event_task_id = str(payload.get("task_id", "") or "")
        event_task_root = _normalize_task_root(event_task_id)
        if not event_task_root and "::" in event_task_id:
            event_task_root = _normalize_task_root(event_task_id.split("::", 1)[0])
        if event_task_root != selected_task_id:
            continue
        if key_only and event_type not in key_events:
            continue

        level = "info"
        if event_type in {
            "task_failed",
            "chat_low_productivity_rejected",
            "chat_strict_mode_blocked_close",
        }:
            level = "error"
        elif event_type in {"chat_window_exhausted", "chat_auto_rounds_extended"}:
            level = "warn"
        elif event_type == "task_execution":
            level = "info" if bool(payload.get("success", False)) else "error"
        elif event_type == "execution_step":
            level = "info" if bool(payload.get("success", False)) else "warn"

        raw_files = payload.get("files", [])
        files = raw_files if isinstance(raw_files, list) else []
        timeline_items.append(
            OperatorTimelineItem(
                ts=str(record.get("ts", "") or ""),
                event_type=event_type,
                task_id=event_task_id,
                level=level,
                summary=_event_summary(event_type, payload),
                assignee=str(payload.get("assignee", "") or ""),
                execution_round=_safe_int_value(payload.get("execution_round", 0), 0),
                execution_sub_iteration=_safe_int_value(
                    payload.get(
                        "execution_sub_iteration", payload.get("sub_iteration", 0)
                    ),
                    0,
                ),
                gate_iteration=_safe_int_value(
                    payload.get("gate_iteration", payload.get("iteration", 0)), 0
                ),
                blocked_reason=str(payload.get("blocked_reason", "") or ""),
                handoff_from=str(payload.get("from", "") or ""),
                handoff_to=str(payload.get("to", "") or ""),
                conversation_thread_id=str(payload.get("thread_id", "") or ""),
                meeting_kind=str(payload.get("meeting_kind", "") or ""),
                artifact_created=_safe_int_value(payload.get("created", 0), 0),
                artifact_modified=_safe_int_value(payload.get("modified", 0), 0),
                artifact_files=[
                    str(item or "") for item in files if str(item or "").strip()
                ][:16],
                productivity_score=_safe_int_value(
                    payload.get("productivity_score", 0), 0
                ),
                reasoning_score=_safe_int_value(payload.get("reasoning_score", 0), 0),
            )
        )

    timeline_items.sort(key=lambda item: item.ts, reverse=True)
    effective_limit = max(20, min(limit, 300))
    limited_items = timeline_items[:effective_limit]
    progress = _build_chat_progress(runtime_dir, selected_task_id)

    return OperatorTimelineResponse(
        selected_task_id=selected_task_id,
        latest_task_id=latest_task_id,
        available_runs=available_runs,
        total=len(timeline_items),
        items=limited_items,
        progress=progress,
    )


def _evaluate_chat_quality(
    *,
    decision_text: str,
    justification_text: str,
    completed_tasks: int,
    total_tasks: int,
    pending_tasks: int,
    failed_tasks: int,
    execution_attempts: int,
    execution_success: int,
    execution_steps: int,
    successful_checks: list[str],
    artifact_created: int,
    artifact_modified: int,
) -> tuple[int, int, str, str]:
    total = max(1, total_tasks)
    completion_ratio = completed_tasks / total
    artifact_total = max(0, artifact_created) + max(0, artifact_modified)

    reasoning_score = 0
    decision_len = len(str(decision_text or "").strip())
    justification_len = len(str(justification_text or "").strip())
    if decision_len >= 160:
        reasoning_score += 30
    elif decision_len >= 80:
        reasoning_score += 20
    elif decision_len >= 30:
        reasoning_score += 12

    if justification_len >= 180:
        reasoning_score += 25
    elif justification_len >= 90:
        reasoning_score += 16
    elif justification_len >= 35:
        reasoning_score += 10

    if completion_ratio >= 0.75:
        reasoning_score += 20
    elif completion_ratio >= 0.4:
        reasoning_score += 12
    elif completed_tasks > 0:
        reasoning_score += 8

    if failed_tasks == 0:
        reasoning_score += 10
    if pending_tasks <= max(1, total // 3):
        reasoning_score += 15

    productivity_score = 0
    if execution_attempts > 0:
        productivity_score += 8
        if execution_attempts >= max(2, total // 2):
            productivity_score += 4
        success_ratio = execution_success / max(1, execution_attempts)
        productivity_score += int(success_ratio * 8)

    if execution_steps > 0:
        productivity_score += 30
        if execution_steps >= 3:
            productivity_score += 15

    checks_count = len(successful_checks)
    if checks_count > 0:
        productivity_score += 6
        if checks_count >= 2:
            productivity_score += 6
        if checks_count >= 3:
            productivity_score += 4

    if artifact_total > 0:
        productivity_score += 35
        if artifact_total >= 3:
            productivity_score += 10

    if completion_ratio >= 0.75:
        productivity_score += 6
    elif completion_ratio >= 0.4:
        productivity_score += 4

    if failed_tasks == 0:
        productivity_score += 4

    reasoning_score = max(0, min(100, reasoning_score))
    productivity_score = max(0, min(100, productivity_score))

    if productivity_score >= 75:
        productivity_status = "strong"
    elif productivity_score >= 45:
        productivity_status = "moderate"
    else:
        productivity_status = "weak"

    if execution_attempts == 0:
        hint = "No hubo ejecucion de tareas; fuerza un slice implementable y vuelve a correr."
    elif execution_steps == 0:
        hint = "Hubo routing, pero sin pasos de ejecucion; agrega comandos/pruebas minimas en build."
    elif artifact_total == 0:
        hint = "No se detectaron artefactos nuevos o modificados; prioriza cambios concretos en archivos."
    elif failed_tasks > 0:
        hint = "Resuelve fases fallidas antes de ampliar alcance."
    else:
        hint = (
            "Buen avance; toma el siguiente slice de impacto con pruebas de regresion."
        )

    return productivity_score, reasoning_score, productivity_status, hint


def _classify_check_from_command(command: str) -> str:
    text = str(command or "").strip().lower()
    if not text:
        return ""
    test_tokens = [
        "pytest",
        "npm test",
        "pnpm test",
        "bun test",
        "vitest",
        "jest",
        "go test",
        "cargo test",
    ]
    lint_tokens = [
        "eslint",
        "ruff",
        "flake8",
        "pylint",
        "npm run lint",
        "pnpm lint",
        "bun lint",
    ]
    build_tokens = [
        "npm run build",
        "pnpm build",
        "bun run build",
        "vite build",
        "tsc -b",
        "cargo build",
        "go build",
    ]
    if any(token in text for token in test_tokens):
        return "test"
    if any(token in text for token in lint_tokens):
        return "lint"
    if any(token in text for token in build_tokens):
        return "build"
    return ""


def _is_placeholder_output_text(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    # Formato legacy: "[provider:model:api] Processed prompt ..."
    if "processed prompt" in text:
        return True
    # Formato actual: "[SIMULADO | provider:model] Respuesta mock ..."
    if "simulado |" in text or "respuesta mock" in text:
        return True
    patterns = [
        r"^\[[a-z0-9_\-]+:[a-z0-9_\.\-]+:(subscription|api)\]",
        r"^\[simulado\s*\|",
    ]
    return any(re.search(pattern, text) is not None for pattern in patterns)


def _assess_execution_mode(
    *,
    task_rows: list[WorkTask],
    execution_steps: int,
    artifact_created: int,
    artifact_modified: int,
) -> tuple[str, int, float, int]:
    result_texts: list[str] = []
    for task in task_rows:
        result = str(
            task.metadata.get("result") or task.metadata.get("error") or ""
        ).strip()
        if result:
            result_texts.append(result)

    if not result_texts:
        mode = (
            "live"
            if (execution_steps > 0 or (artifact_created + artifact_modified) > 0)
            else "simulated"
        )
        return mode, 0, 0.0, 0

    placeholder_count = sum(
        1 for row in result_texts if _is_placeholder_output_text(row)
    )
    placeholder_ratio = float(placeholder_count) / float(len(result_texts))

    if placeholder_count == len(result_texts) and execution_steps == 0:
        return "simulated", placeholder_count, placeholder_ratio, len(result_texts)
    if placeholder_count > 0:
        return "hybrid", placeholder_count, placeholder_ratio, len(result_texts)
    return "live", placeholder_count, placeholder_ratio, len(result_texts)


def _evaluate_phase_evidence_gate(
    *,
    task_rows_by_phase: dict[str, WorkTask],
    execution_steps: int,
    execution_steps_success: int,
    successful_checks: list[str],
    artifact_created: int,
    artifact_modified: int,
    require_followup_artifact_delta: bool,
    require_test_or_build_check: bool,
) -> list[str]:
    failures: list[str] = []
    target_phases = ["build", "review", "qa"]
    for phase in target_phases:
        task = task_rows_by_phase.get(phase)
        if task is None:
            failures.append(f"{phase}:missing_task")
            continue
        if task.state.value != "completed":
            failures.append(f"{phase}:not_completed")
            continue
        result_text = str(
            task.metadata.get("result") or task.metadata.get("error") or ""
        ).strip()
        if not result_text:
            failures.append(f"{phase}:empty_result")
            continue
        if _is_placeholder_output_text(result_text):
            failures.append(f"{phase}:placeholder_output")

        if phase == "build" and bool(
            task.metadata.get("require_execution_plan", False)
        ):
            raw_plan = task.metadata.get("execution_plan", [])
            if not isinstance(raw_plan, list) or not raw_plan:
                failures.append("build:missing_execution_plan")

    build_has_output = all(not row.startswith("build:") for row in failures)
    if (
        build_has_output
        and execution_steps <= 0
        and (artifact_created + artifact_modified) <= 0
    ):
        failures.append("build:no_execution_evidence")
    if execution_steps_success <= 0:
        failures.append("build:no_successful_execution_steps")
    if execution_steps_success > 0 and not successful_checks:
        failures.append("build:no_successful_post_build_checks")
    if require_test_or_build_check and execution_steps_success > 0:
        if not any(check in {"test", "build"} for check in successful_checks):
            failures.append("build:missing_test_or_build_check")
    if require_followup_artifact_delta and (artifact_created + artifact_modified) <= 0:
        failures.append("build:no_followup_artifact_delta")
    return failures


def _compose_user_facing_run_summary(
    *,
    task_root: str,
    request_line: str,
    continuation_line: str,
    mode: str,
    rounds_used: int,
    round_budget: int,
    elapsed_ms: int,
    done_line: str,
    pending_line: str,
    failed_line: str,
    participants_line: str,
    decision_compact: str,
    artifact_created: int,
    artifact_modified: int,
    artifact_files: list[str],
    productivity_score: int,
    reasoning_score: int,
    productivity_status: str,
    next_action_hint: str,
    execution_mode: str,
    placeholder_outputs: int,
) -> str:
    decision_text = str(decision_compact or "").strip()
    if (
        not decision_text
        or "Processed prompt" in decision_text
        or "SIMULADO |" in decision_text
    ):
        decision_text = "Se priorizo completar el slice de mayor impacto de esta ronda y cerrar con review + QA."

    if artifact_files:
        files_line = ", ".join(artifact_files[:10])
        files_text = f"Se detectaron cambios en archivos (creados={artifact_created}, modificados={artifact_modified}): {files_line}."
    else:
        files_text = "No se detectaron cambios de archivos en esta ronda; se requiere ejecutar implementacion concreta en la siguiente iteracion."

    return "\n".join(
        [
            "Resumen del Team Lead para ti:",
            f"- Solicitud atendida: {request_line}",
            f"- Gestion de la conversacion: modo={mode}, rondas={rounds_used}/{round_budget}, continuidad={continuation_line}, participantes={participants_line}.",
            f"- Tipo de ejecucion detectado: {execution_mode} (salidas placeholder={placeholder_outputs}).",
            f"- Que se decidio:\n  {decision_text.replace(chr(10), chr(10) + '  ')}",
            f"- Que se hizo: completado={done_line}; pendiente={pending_line}; fallido={failed_line}.",
            f"- Archivos: {files_text}",
            f"- Calidad de ejecucion: productividad={productivity_score}/100 ({productivity_status}), razonamiento={reasoning_score}/100.",
            f"- Siguiente paso recomendado: {next_action_hint}",
            f"- Referencia de corrida: {task_root} ({elapsed_ms}ms).",
        ]
    )


class NotebookLMSyncRequest(BaseModel):
    title: str = "AI Team Sync"
    source: str = "api"
    content: str = ""
    export_format: str = "markdown"
    days: int = 7
    dry_run: bool = False
    notebook_id: str = ""


from api.utils import (
    _truncate_text,
    _read_json_payload,
    _read_jsonl_records,
    _event_summary,
    _auth_expected_key,
    _extract_auth_token,
    _is_authorized,
    _require_api_auth_request,
    _normalize_workspace_path,
    _workspace_from_header_map,
    _workspace_from_request,
    _safe_workspace_target,
    _extract_user_message_from_task_description,
    _group_chat_roots,
    _build_project_continuity_context,
    _chat_round_budget,
    _sanitize_project_name,
    _allocate_project_path,
    _detect_notebooklm_status,
    PROJECT_ROOT,
    get_current_workspace,
    set_current_workspace,
)

from api.routers import workspace as workspace_router
from api.routers import aiteam as aiteam_router

app.include_router(workspace_router.router)
app.include_router(aiteam_router.router)


@app.post("/api/notebooklm/sync")
async def post_notebooklm_sync(payload: NotebookLMSyncRequest, request: Request):
    _require_api_auth_request(request)
    try:
        workspace = _workspace_from_request(
            request, get_current_workspace(), PROJECT_ROOT
        )
        runtime_dir = workspace / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)

        def _sync():
            return cmd_notebooklm_sync(
                runtime_dir=runtime_dir,
                notebook_id=payload.notebook_id,
                title=payload.title,
                source=payload.source,
                content_file="",
                from_prompt=payload.content,
                export_format=payload.export_format,
                days=max(1, int(payload.days)),
                dry_run=bool(payload.dry_run),
                quiet=True,
            )

        return await asyncio.to_thread(_sync)
    except Exception as e:
        import logging

        logging.getLogger(__name__).exception("Unhandled error in notebooklm sync")
        return {"error": str(e)}


@app.post("/api/aiteam/chat", response_model=TeamChatResponse)
async def post_aiteam_chat(payload: TeamChatRequest, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    role_map = {
        "team_lead": Role.TEAM_LEAD,
        "lead": Role.TEAM_LEAD,
        "researcher": Role.RESEARCHER,
        "engineer": Role.ENGINEER,
        "reviewer": Role.REVIEWER,
        "qa": Role.QA,
    }
    complexity_map = {
        "low": Complexity.LOW,
        "medium": Complexity.MEDIUM,
        "high": Complexity.HIGH,
    }
    criticality_map = {
        "low": Criticality.LOW,
        "medium": Criticality.MEDIUM,
        "high": Criticality.HIGH,
    }

    preferred_role = role_map.get(payload.role.strip().lower(), Role.ENGINEER)
    complexity = complexity_map.get(
        payload.complexity.strip().lower(), Complexity.MEDIUM
    )
    criticality = criticality_map.get(
        payload.criticality.strip().lower(), Criticality.MEDIUM
    )

    def _task_result(task: WorkTask | None) -> str:
        if task is None:
            return ""
        return str(task.metadata.get("result") or task.metadata.get("error") or "")

    def _compact_text_line(value: str, limit: int = 320) -> str:
        flat = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(flat) <= limit:
            return flat
        return flat[: max(0, limit - 3)] + "..."

    def _run_chat() -> TeamChatResponse:
        orch = build_default_orchestrator(
            runtime_dir=runtime_dir,
            browser_mode="basic",
            environment="dev",
        )
        previous_runs = _recent_chat_roots(runtime_dir, max_chats=3)
        previous_root = previous_runs[0] if previous_runs else {}
        previous_by_root: dict[str, dict[str, object]] = {
            str(item.get("root_id", "")).upper(): item
            for item in previous_runs
            if isinstance(item, dict)
            and str(item.get("root_id", "")).upper().startswith("CHAT-")
        }
        continuation_requested = _is_continuation_message(payload.message)
        continuation_target = _extract_chat_root_from_message(payload.message)
        continuation_of = ""
        continuation_snapshot = ""
        continuation_source: dict[str, object] = {}
        if continuation_requested:
            if continuation_target and continuation_target in previous_by_root:
                continuation_source = previous_by_root.get(continuation_target, {})
            elif previous_root:
                continuation_source = previous_root

        if continuation_requested and continuation_source:
            continuation_of = str(continuation_source.get("root_id", "") or "")
            previous_states = continuation_source.get("phase_states", {})
            unresolved: list[str] = []
            if isinstance(previous_states, dict):
                for phase_name, state in previous_states.items():
                    state_value = str(state or "")
                    if state_value != "completed":
                        unresolved.append(f"{phase_name}:{state_value}")
            continuation_snapshot = (
                ", ".join(unresolved[:8]) if unresolved else "all_completed"
            )
        elif continuation_requested and continuation_target:
            continuation_of = continuation_target
            continuation_snapshot = "target_not_found"

        task_root = _resolve_task_root(payload.client_task_id)
        chat_mode = _normalize_chat_mode(payload.mode)
        round_budget = _resolve_chat_round_budget(
            requested_rounds=payload.max_rounds,
            chat_mode=chat_mode,
            complexity=complexity,
            criticality=criticality,
        )
        require_build_execution_plan = not bool(continuation_requested)

        if chat_mode == "classic":
            phase_task_ids = {
                "lead_intake": f"{task_root}::lead_intake",
                "discovery": f"{task_root}::discovery",
                "build": f"{task_root}::build",
                "review": f"{task_root}::review",
                "qa": f"{task_root}::qa",
                "lead_close": f"{task_root}::lead_close",
            }
            workflow_phase_keys = [
                "lead_intake",
                "discovery",
                "build",
                "review",
                "qa",
                "lead_close",
            ]
            delegated_task_ids = [
                phase_task_ids["discovery"],
                phase_task_ids["build"],
                phase_task_ids["review"],
                phase_task_ids["qa"],
            ]
        else:
            phase_task_ids = {
                "lead_intake": f"{task_root}::lead_intake",
                "plan_research": f"{task_root}::plan_research",
                "plan_engineering": f"{task_root}::plan_engineering",
                "plan_risks": f"{task_root}::plan_risks",
                "build": f"{task_root}::build",
                "review": f"{task_root}::review",
                "qa": f"{task_root}::qa",
                "lead_close": f"{task_root}::lead_close",
            }
            workflow_phase_keys = [
                "lead_intake",
                "plan_research",
                "plan_engineering",
                "plan_risks",
                "build",
                "review",
                "qa",
                "lead_close",
            ]
            delegated_task_ids = [
                phase_task_ids["plan_research"],
                phase_task_ids["plan_engineering"],
                phase_task_ids["plan_risks"],
                phase_task_ids["build"],
                phase_task_ids["review"],
                phase_task_ids["qa"],
            ]

        lead_task_id = phase_task_ids["lead_intake"]
        continuity_context = _build_project_continuity_context(runtime_dir)
        continuity_block = f"\n\n{continuity_context}\n" if continuity_context else ""

        orch.mailbox.send(
            sender="user",
            recipient="team_lead",
            subject=f"User input: {task_root}",
            body=payload.message,
            task_id=task_root,
        )
        orch.event_logger.emit(
            "user_input",
            {
                "task_id": task_root,
                "role": payload.role,
                "complexity": payload.complexity,
                "criticality": payload.criticality,
                "message": payload.message,
                "continuation_requested": continuation_requested,
                "continuation_of": continuation_of,
            },
        )
        orch.memory.remember(
            agent_id="lead-1",
            role=Role.TEAM_LEAD.value,
            kind="user_input",
            content=payload.message,
            task_id=task_root,
            tags=["chat", "user_input"],
        )

        staged_tasks: list[WorkTask]
        if chat_mode == "classic":
            staged_tasks = [
                WorkTask(
                    task_id=phase_task_ids["lead_intake"],
                    title="Lead intake and project framing",
                    description=(
                        "Eres Team Lead senior. Escucha al usuario, define alcance y estrategia de ejecucion.\n"
                        f"Solicitud original:\n{payload.message}\n"
                        "Entrega: objetivos, supuestos, riesgos y orden de trabajo del equipo."
                        f"{continuity_block}"
                    ),
                    role=Role.TEAM_LEAD,
                    complexity=complexity,
                    criticality=criticality,
                    metadata={
                        "required_capabilities": ["reasoning"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "lead_intake",
                        "chat_preferred_role": preferred_role.value,
                        "continuation_requested": continuation_requested,
                        "continuation_of": continuation_of,
                        "continuation_snapshot": continuation_snapshot,
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["discovery"],
                    title="Discovery and constraints",
                    description=(
                        "Recopila contexto tecnico y restricciones para ejecutar la solicitud del usuario.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega hallazgos accionables para implementacion."
                        f"{continuity_block}"
                    ),
                    role=Role.RESEARCHER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["lead_intake"]],
                    metadata={
                        "required_capabilities": ["analysis"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "discovery",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["build"],
                    title="Build implementation",
                    description=(
                        "Implementa la solucion principal solicitada por el usuario con foco en codigo listo para revisar.\n"
                        f"Solicitud: {payload.message}\n"
                        "Incluye decisiones tecnicas y tradeoffs."
                        f"{continuity_block}"
                    ),
                    role=Role.ENGINEER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["discovery"]],
                    metadata={
                        "required_capabilities": ["coding"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "require_execution_plan": require_build_execution_plan,
                        "phase": "build",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["review"],
                    title="Review quality and risks",
                    description=(
                        "Revisa la salida de build, valida calidad, seguridad y mantenibilidad.\n"
                        f"Solicitud: {payload.message}"
                        f"{continuity_block}"
                    ),
                    role=Role.REVIEWER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["build"]],
                    metadata={
                        "required_capabilities": ["review"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "review",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["qa"],
                    title="QA verification",
                    description=(
                        "Valida criterios de aceptacion y riesgos de regresion sobre la propuesta.\n"
                        f"Solicitud: {payload.message}"
                        f"{continuity_block}"
                    ),
                    role=Role.QA,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["review"]],
                    metadata={
                        "required_capabilities": ["analysis"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "qa",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["lead_close"],
                    title="Lead synthesis and response",
                    description=(
                        "Como Team Lead senior, sintetiza discovery/build/review/qa y responde al usuario con plan final.\n"
                        f"Solicitud original: {payload.message}\n"
                        "Incluye justificacion de decisiones y proximos pasos de implementacion."
                        f"{continuity_block}"
                    ),
                    role=Role.TEAM_LEAD,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["qa"]],
                    metadata={
                        "required_capabilities": ["reasoning"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "lead_close",
                        "chat_parent": task_root,
                    },
                ),
            ]
        else:
            staged_tasks = [
                WorkTask(
                    task_id=phase_task_ids["lead_intake"],
                    title="Lead intake and sprint framing",
                    description=(
                        "Eres Team Lead senior. Convierte el input en plan de ejecucion de ventana corta.\n"
                        f"Solicitud original:\n{payload.message}\n"
                        "Entrega en <=12 lineas: objetivo, backlog priorizado (P0/P1), riesgos y que se intentara completar en esta corrida."
                        f"{continuity_block}"
                    ),
                    role=Role.TEAM_LEAD,
                    complexity=complexity,
                    criticality=criticality,
                    metadata={
                        "required_capabilities": ["reasoning"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "lead_intake",
                        "chat_preferred_role": preferred_role.value,
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["plan_research"],
                    title="Planning research pack",
                    description=(
                        "Construye base de decision para el plan: restricciones, riesgos tecnicos y supuestos criticos.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: checklist accionable para build en esta ventana."
                        f"{continuity_block}"
                    ),
                    role=Role.RESEARCHER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["lead_intake"]],
                    metadata={
                        "required_capabilities": ["analysis"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "plan_research",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["plan_engineering"],
                    title="Engineering execution plan",
                    description=(
                        "Define corte de implementacion para esta ventana.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: tareas secuenciadas, criterios de aceptacion y tradeoffs para ejecutar en build."
                        f"{continuity_block}"
                    ),
                    role=Role.ENGINEER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["lead_intake"]],
                    metadata={
                        "required_capabilities": ["coding"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "plan_engineering",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["plan_risks"],
                    title="Risk and quality plan",
                    description=(
                        "Define quality gates, pruebas minimas y riesgos de release para esta ventana.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: criterios de revison/QA para validar lo ejecutado."
                        f"{continuity_block}"
                    ),
                    role=Role.REVIEWER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["lead_intake"]],
                    metadata={
                        "required_capabilities": ["review"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "plan_risks",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["build"],
                    title="Build highest-impact slice",
                    description=(
                        "Ejecuta el slice de mayor impacto definido en planning para esta ventana.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: implementado, pendientes explicitos y riesgos abiertos."
                        f"{continuity_block}"
                    ),
                    role=Role.ENGINEER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[
                        phase_task_ids["plan_research"],
                        phase_task_ids["plan_engineering"],
                        phase_task_ids["plan_risks"],
                    ],
                    metadata={
                        "required_capabilities": ["coding"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "require_execution_plan": require_build_execution_plan,
                        "phase": "build",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["review"],
                    title="Review quality and maintainability",
                    description=(
                        "Revisa el build contra el plan acordado.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: hallazgos, bloqueos y decisiones para cerrar o iterar."
                        f"{continuity_block}"
                    ),
                    role=Role.REVIEWER,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["build"]],
                    metadata={
                        "required_capabilities": ["review"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "review",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["qa"],
                    title="QA validation",
                    description=(
                        "Valida criterios de aceptacion para lo ejecutado en la ventana.\n"
                        f"Solicitud: {payload.message}\n"
                        "Entrega: veredicto, cobertura lograda y riesgos de regresion."
                        f"{continuity_block}"
                    ),
                    role=Role.QA,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["build"]],
                    metadata={
                        "required_capabilities": ["analysis"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "qa",
                        "chat_parent": task_root,
                        "delegated_by": "team_lead",
                    },
                ),
                WorkTask(
                    task_id=phase_task_ids["lead_close"],
                    title="Lead sprint synthesis",
                    description=(
                        "Como Team Lead, sintetiza planning+build+review+qa y responde estado final de la ventana.\n"
                        f"Solicitud original: {payload.message}\n"
                        "Entrega: completado, pendiente y siguiente iteracion propuesta."
                        f"{continuity_block}"
                    ),
                    role=Role.TEAM_LEAD,
                    complexity=complexity,
                    criticality=criticality,
                    dependencies=[phase_task_ids["review"], phase_task_ids["qa"]],
                    metadata={
                        "required_capabilities": ["reasoning"],
                        "interactive_chat": True,
                        "skip_quality_gates": True,
                        "require_peer_consultation": True,
                        "phase": "lead_close",
                        "chat_parent": task_root,
                    },
                ),
            ]

        artifact_before = _workspace_artifact_snapshot(workspace)
        bootstrap_result = _materialize_game_iteration(workspace, payload.message)
        if bool(bootstrap_result.get("applied", False)):
            raw_bootstrap_files = bootstrap_result.get("files", [])
            bootstrap_files = (
                raw_bootstrap_files if isinstance(raw_bootstrap_files, list) else []
            )
            orch.event_logger.emit(
                "chat_artifact_bootstrap",
                {
                    "task_id": task_root,
                    "iteration": _safe_int_value(
                        bootstrap_result.get("iteration", 0), 0
                    ),
                    "files": [
                        str(item or "")
                        for item in bootstrap_files
                        if str(item or "").strip()
                    ],
                },
            )

        started = time.perf_counter()
        for item in staged_tasks:
            orch.submit_task(item)

        workflow_label = " -> ".join(workflow_phase_keys)
        orch.event_logger.emit(
            "chat_plan_created",
            {
                "task_id": task_root,
                "chat_mode": chat_mode,
                "round_budget": round_budget,
                "phase_count": len(workflow_phase_keys),
                "delegated_count": len(delegated_task_ids),
                "continuation_requested": continuation_requested,
                "continuation_of": continuation_of,
                "continuation_snapshot": continuation_snapshot,
            },
        )

        orch.mailbox.send(
            sender="team_lead",
            recipient="broadcast",
            subject=f"Lead delegation created: {task_root}",
            body=(
                "Lead received user request and created phased workflow: "
                f"{workflow_label} (mode={chat_mode}, round_budget={round_budget})"
            ),
            task_id=task_root,
        )

        orch.run_until_idle(max_rounds=round_budget)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        artifact_after = _workspace_artifact_snapshot(workspace)
        created_artifacts, modified_artifacts = _workspace_artifact_diff(
            artifact_before, artifact_after
        )
        artifact_created = len(created_artifacts)
        artifact_modified = len(modified_artifacts)
        artifact_files = sorted(set(created_artifacts + modified_artifacts))
        bootstrap_files = bootstrap_result.get("files", [])
        if isinstance(bootstrap_files, list):
            for item in bootstrap_files:
                name = str(item or "").strip()
                if name:
                    artifact_files.append(name)
        artifact_files = sorted(set(artifact_files))

        if artifact_files:
            orch.event_logger.emit(
                "chat_artifacts_detected",
                {
                    "task_id": task_root,
                    "created": artifact_created,
                    "modified": artifact_modified,
                    "files": artifact_files[:16],
                },
            )

        phase_task_set = set(phase_task_ids.values())
        game_followup_requested = _is_game_followup_request(workspace, payload.message)

        def _collect_phase_progress() -> tuple[
            WorkTask | None, dict[str, str], int, int, int
        ]:
            local_lead = orch.taskboard.get_task(phase_task_ids["lead_close"])
            local_phase_states: dict[str, str] = {}
            local_rounds_used = 0
            for phase_name, phase_id in phase_task_ids.items():
                task = orch.taskboard.get_task(phase_id)
                if task is None:
                    local_phase_states[phase_name] = "missing"
                    continue
                local_phase_states[phase_name] = task.state.value
                execution_round = _safe_int_value(
                    task.metadata.get("execution_round", 0), 0
                )
                local_rounds_used = max(local_rounds_used, execution_round)

            local_completed = sum(
                1 for state in local_phase_states.values() if state == "completed"
            )
            local_pending = sum(
                1
                for state in local_phase_states.values()
                if state in {"pending", "ready", "claimed", "blocked"}
            )
            return (
                local_lead,
                local_phase_states,
                local_rounds_used,
                local_completed,
                local_pending,
            )

        auto_extended_rounds = 0
        if bool(payload.auto_extend_weak_runs) and round_budget < 80:
            execution_steps_so_far = 0
            for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
                if str(record.get("event_type", "") or "") != "execution_step":
                    continue
                payload_dict = record.get("payload", {})
                if not isinstance(payload_dict, dict):
                    continue
                route_task_id = str(payload_dict.get("task_id", "") or "")
                if route_task_id not in phase_task_set:
                    continue
                execution_steps_so_far += 1

            weak_without_evidence = (
                artifact_created == 0 and execution_steps_so_far == 0
            )
            if weak_without_evidence:
                next_round_budget = min(80, round_budget + 3)
                if next_round_budget > round_budget:
                    auto_extended_rounds = next_round_budget - round_budget
                    orch.event_logger.emit(
                        "chat_auto_rounds_extended",
                        {
                            "task_id": task_root,
                            "from_round_budget": round_budget,
                            "to_round_budget": next_round_budget,
                            "reason": "weak_run_without_artifacts_or_execution_steps",
                        },
                    )
                    round_budget = next_round_budget
                    orch.run_until_idle(max_rounds=round_budget)
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    artifact_after = _workspace_artifact_snapshot(workspace)
                    created_artifacts, modified_artifacts = _workspace_artifact_diff(
                        artifact_before, artifact_after
                    )
                    artifact_created = len(created_artifacts)
                    artifact_modified = len(modified_artifacts)
                    artifact_files = sorted(set(created_artifacts + modified_artifacts))
                    if isinstance(bootstrap_files, list):
                        for item in bootstrap_files:
                            name = str(item or "").strip()
                            if name:
                                artifact_files.append(name)
                    artifact_files = sorted(set(artifact_files))
                    if artifact_files:
                        orch.event_logger.emit(
                            "chat_artifacts_detected",
                            {
                                "task_id": task_root,
                                "created": artifact_created,
                                "modified": artifact_modified,
                                "files": artifact_files[:16],
                            },
                        )

        lead_result_task, phase_states, rounds_used, completed_tasks, pending_tasks = (
            _collect_phase_progress()
        )

        lead_completed = (
            lead_result_task is not None and lead_result_task.state.value == "completed"
        )
        lead_response = _task_result(lead_result_task)
        delegated_lines: list[str] = []
        phase_name_by_task_id = {
            task_id: phase for phase, task_id in phase_task_ids.items()
        }
        if lead_result_task is None:
            final_state = "in_progress" if pending_tasks > 0 else "failed"
        elif lead_result_task.state.value == "completed":
            final_state = "completed"
        elif lead_result_task.state.value == "failed":
            final_state = "failed"
        else:
            final_state = "in_progress"
        for delegated_id in delegated_task_ids:
            delegated_task = orch.taskboard.get_task(delegated_id)
            if delegated_task is None:
                delegated_phase = phase_name_by_task_id.get(delegated_id, delegated_id)
                delegated_lines.append(f"- {delegated_phase}: missing")
                continue
            delegated_outcome = _task_result(delegated_task)
            delegated_phase = phase_name_by_task_id.get(delegated_id, delegated_id)
            delegated_lines.append(
                f"- {delegated_phase}: state={delegated_task.state.value} result={_compact_text_line(delegated_outcome, 140)}"
            )
            if delegated_task.state.value == "failed":
                final_state = "failed"

        task_rows_by_phase: dict[str, WorkTask] = {}
        for phase_name, phase_id in phase_task_ids.items():
            task = orch.taskboard.get_task(phase_id)
            if task is not None:
                task_rows_by_phase[phase_name] = task

        role_participants = sorted(
            {task.role.value for task in task_rows_by_phase.values()}
        )
        assignee_participants = sorted(
            {
                str(task.assignee).strip()
                for task in task_rows_by_phase.values()
                if str(task.assignee or "").strip()
            }
        )

        done_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) == "completed"
        ]
        pending_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) in {"pending", "ready", "claimed", "blocked"}
        ]
        failed_phases = [
            phase
            for phase in workflow_phase_keys
            if phase_states.get(phase) == "failed"
        ]

        decision_source = lead_response
        if not decision_source:
            intake_task = task_rows_by_phase.get("lead_intake")
            decision_source = _task_result(intake_task)

        decision_compact = str(decision_source or "").strip()
        if len(decision_compact) > 1500:
            decision_compact = decision_compact[:1490] + "...\n[truncado]"

        route_records: list[tuple[str, str, str, bool]] = []
        execution_steps = 0
        execution_steps_success = 0
        successful_checks_set: set[str] = set()
        for record in _read_jsonl_records(runtime_dir / "events.jsonl"):
            event_type = str(record.get("event_type", "") or "")
            payload_dict = record.get("payload", {})
            if not isinstance(payload_dict, dict):
                continue
            route_task_id = str(payload_dict.get("task_id", "") or "")
            if route_task_id not in phase_task_set:
                continue
            if event_type == "task_execution":
                route_records.append(
                    (
                        str(payload_dict.get("provider", "-") or "-"),
                        str(payload_dict.get("model", "-") or "-"),
                        str(payload_dict.get("channel", "-") or "-"),
                        bool(payload_dict.get("success", False)),
                    )
                )
                continue
            if event_type == "execution_step":
                execution_steps += 1
                if bool(payload_dict.get("success", False)):
                    execution_steps_success += 1
                    check_type = _classify_check_from_command(
                        str(payload_dict.get("command", "") or "")
                    )
                    if check_type:
                        successful_checks_set.add(check_type)

        successful_checks = sorted(successful_checks_set)

        route_counts: dict[tuple[str, str, str], int] = {}
        successful_routes = 0
        for provider, model, channel, was_success in route_records:
            route_key = (provider, model, channel)
            route_counts[route_key] = int(route_counts.get(route_key, 0)) + 1
            if was_success:
                successful_routes += 1
        used_routes = sorted(
            [
                f"{provider}/{model} ({channel}) x{count}"
                for (provider, model, channel), count in route_counts.items()
            ]
        )
        execution_attempts = len(route_records)
        execution_success = successful_routes
        (
            execution_mode,
            placeholder_outputs,
            placeholder_output_ratio,
            output_result_count,
        ) = _assess_execution_mode(
            task_rows=list(task_rows_by_phase.values()),
            execution_steps=execution_steps,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
        )

        orch.event_logger.emit(
            "chat_execution_mode_assessed",
            {
                "task_id": task_root,
                "execution_mode": execution_mode,
                "placeholder_outputs": placeholder_outputs,
                "placeholder_output_ratio": round(placeholder_output_ratio, 4),
                "execution_steps": execution_steps,
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
                "live_mode_required": _env_bool(
                    "AITEAM_REQUIRE_LIVE_MODE", default=False
                ),
            },
        )

        live_mode_required = (
            _env_bool("AITEAM_REQUIRE_LIVE_MODE", default=False)
            and not continuation_requested
        )
        live_mode_rejected = False
        if live_mode_required and execution_mode != "live":
            live_mode_rejected = True
            if final_state != "failed":
                final_state = "rejected"
            productivity_status = "weak"
            next_action_hint = "Este entorno requiere modo live; la corrida detectada no fue live. Configura adapters live y reintenta."
            orch.event_logger.emit(
                "chat_live_mode_required_rejected",
                {
                    "task_id": task_root,
                    "execution_mode": execution_mode,
                    "required": True,
                },
            )

        evidence_gate_failures = _evaluate_phase_evidence_gate(
            task_rows_by_phase=task_rows_by_phase,
            execution_steps=execution_steps,
            execution_steps_success=execution_steps_success,
            successful_checks=successful_checks,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            require_followup_artifact_delta=game_followup_requested,
            require_test_or_build_check=True,
        )
        if continuation_requested:
            evidence_gate_failures = [
                f
                for f in evidence_gate_failures
                if not f.endswith("placeholder_output")
                and not f.endswith("no_execution_evidence")
                and not f.endswith("no_successful_execution_steps")
                and not f.endswith("missing_test_or_build_check")
            ]

        evidence_gate_applied = False
        if evidence_gate_failures:
            evidence_gate_applied = True
            if final_state != "failed":
                final_state = "rejected"
            productivity_status = "weak"
            next_action_hint = "Evidence gate bloquea cierre sin evidencia valida en build/review/qa; corrige y reintenta."
            orch.event_logger.emit(
                "chat_evidence_gate_rejected",
                {
                    "task_id": task_root,
                    "failures": evidence_gate_failures,
                    "execution_mode": execution_mode,
                    "execution_steps": execution_steps,
                    "artifact_created": artifact_created,
                    "artifact_modified": artifact_modified,
                },
            )
            # Emitir compliance_violation por cada fallo del evidence gate
            _FAILURE_REASON_MAP = {
                "missing_execution_plan": "missing_execution_plan_required",
                "no_execution_evidence": "no_execution_evidence",
                "no_successful_execution_steps": "no_successful_execution_steps",
                "missing_task": "build_phase_missing",
                "not_completed": "missing_execution_plan_required",
                "empty_result": "build_phase_empty_result",
                "placeholder_output": "build_phase_placeholder_output",
            }
            for failure in evidence_gate_failures:
                if not failure.startswith("build:"):
                    continue
                failure_code = failure.split(":", 1)[1] if ":" in failure else failure
                reason = _FAILURE_REASON_MAP.get(failure_code, failure_code)
                orch.event_logger.emit(
                    "compliance_violation",
                    {
                        "task_id": task_root,
                        "reason": reason,
                        "failure": failure,
                    },
                )

        lead_justification = ""
        if lead_result_task is not None:
            lead_justification = str(
                lead_result_task.metadata.get("decision_justification", "")
            )
        if not lead_justification:
            intake_task = orch.taskboard.get_task(phase_task_ids["lead_intake"])
            if intake_task is not None:
                lead_justification = str(
                    intake_task.metadata.get("decision_justification", "")
                )

        productivity_score, reasoning_score, productivity_status, next_action_hint = (
            _evaluate_chat_quality(
                decision_text=decision_source,
                justification_text=lead_justification,
                completed_tasks=completed_tasks,
                total_tasks=len(phase_task_ids),
                pending_tasks=pending_tasks,
                failed_tasks=len(failed_phases),
                execution_attempts=execution_attempts,
                execution_success=execution_success,
                execution_steps=execution_steps,
                successful_checks=successful_checks,
                artifact_created=artifact_created,
                artifact_modified=artifact_modified,
            )
        )

        orch.event_logger.emit(
            "chat_quality_assessed",
            {
                "task_id": task_root,
                "productivity_score": productivity_score,
                "reasoning_score": reasoning_score,
                "productivity_status": productivity_status,
                "execution_attempts": execution_attempts,
                "execution_steps": execution_steps,
                "execution_steps_success": execution_steps_success,
                "execution_mode": execution_mode,
                "placeholder_outputs": placeholder_outputs,
                "successful_checks": successful_checks,
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
            },
        )

        participants_line = (
            ", ".join(role_participants) if role_participants else "none"
        )
        agents_line = (
            ", ".join(assignee_participants) if assignee_participants else "none"
        )
        used_line = ", ".join(used_routes[:5]) if used_routes else "none"
        done_line = ", ".join(done_phases) if done_phases else "none"
        pending_line = ", ".join(pending_phases) if pending_phases else "none"
        failed_line = ", ".join(failed_phases) if failed_phases else "none"
        request_line = _compact_text_line(payload.message, limit=180)
        if continuation_of and continuation_snapshot == "target_not_found":
            continuity_line = (
                f"requested target not found (continuation_of={continuation_of})"
            )
        elif continuation_of:
            continuity_line = f"yes (continuation_of={continuation_of}; carryover={continuation_snapshot or '-'})"
        elif continuation_requested:
            continuity_line = "requested, but no previous chat root found"
        elif previous_root:
            continuity_line = f"new run (latest_previous={str(previous_root.get('root_id', '')) or '-'})"
        else:
            continuity_line = "new run (no previous chat roots)"

        strict_mode_applied = False
        if bool(payload.strict_mode) and not continuation_requested:
            has_minimum_evidence = (
                artifact_created + artifact_modified
            ) > 0 or execution_steps > 0
            mode_is_reliable = execution_mode in {"live", "hybrid"}
            if final_state == "completed" and (
                not has_minimum_evidence or not mode_is_reliable
            ):
                strict_mode_applied = True
                final_state = "in_progress"
                productivity_status = "weak"
                if not mode_is_reliable:
                    next_action_hint = "Strict mode bloquea cierre en modo simulado; activa adapters live o agrega ejecucion verificable."
                else:
                    next_action_hint = "Strict mode bloquea cierre sin evidencia minima; agrega pasos ejecutados o artefactos."
                orch.event_logger.emit(
                    "chat_strict_mode_blocked_close",
                    {
                        "task_id": task_root,
                        "reason": "simulated_mode_or_missing_evidence",
                        "execution_steps": execution_steps,
                        "artifact_created": artifact_created,
                        "artifact_modified": artifact_modified,
                        "execution_mode": execution_mode,
                    },
                )

        productivity_threshold = 35
        low_productivity_override = bool(
            payload.allow_low_productivity_override
        ) or bool(continuation_requested)
        low_productivity_rejected = False
        if (
            productivity_score < productivity_threshold
            and not low_productivity_override
            and final_state != "failed"
        ):
            low_productivity_rejected = True
            final_state = "rejected"
            productivity_status = "weak"
            next_action_hint = f"Corrida rechazada por productividad<{productivity_threshold}; genera evidencia ejecutable y reintenta."
            orch.event_logger.emit(
                "chat_low_productivity_rejected",
                {
                    "task_id": task_root,
                    "productivity_score": productivity_score,
                    "threshold": productivity_threshold,
                    "override": False,
                },
            )
        elif productivity_score < productivity_threshold and low_productivity_override:
            orch.event_logger.emit(
                "chat_low_productivity_override",
                {
                    "task_id": task_root,
                    "productivity_score": productivity_score,
                    "threshold": productivity_threshold,
                    "override": True,
                },
            )

        if not lead_completed:
            orch.event_logger.emit(
                "chat_window_exhausted",
                {
                    "task_id": task_root,
                    "chat_mode": chat_mode,
                    "round_budget": round_budget,
                    "rounds_used": rounds_used,
                    "phase_states": phase_states,
                },
            )

        workflow_lines = "\n".join(f"- {phase}" for phase in workflow_phase_keys)
        user_facing_summary = _compose_user_facing_run_summary(
            task_root=task_root,
            request_line=request_line,
            continuation_line=continuity_line,
            mode=chat_mode,
            rounds_used=rounds_used,
            round_budget=round_budget,
            elapsed_ms=elapsed_ms,
            done_line=done_line,
            pending_line=pending_line,
            failed_line=failed_line,
            participants_line=participants_line,
            decision_compact=decision_compact,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            artifact_files=artifact_files,
            productivity_score=productivity_score,
            reasoning_score=reasoning_score,
            productivity_status=productivity_status,
            next_action_hint=next_action_hint,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
        )

        orch.mailbox.send(
            sender="team_lead",
            recipient="user",
            subject=f"Lead user summary: {task_root}",
            body=user_facing_summary,
            task_id=task_root,
        )
        orch.event_logger.emit(
            "chat_user_summary_published",
            {
                "task_id": task_root,
                "summary_chars": len(user_facing_summary),
                "artifact_created": artifact_created,
                "artifact_modified": artifact_modified,
            },
        )

        response_lines = [
            "Lead summary:",
            f"Status={final_state} mode={chat_mode} rounds={rounds_used}/{round_budget} elapsed={elapsed_ms}ms",
            f"Request: {request_line}",
            f"Continuity: {continuity_line}",
            f"Participants (roles): {participants_line}",
            f"Participants (agents): {agents_line}",
            f"Decision: {decision_compact or 'pending synthesis'}",
            f"Done: {done_line}",
            f"Pending: {pending_line}",
            f"Failed: {failed_line}",
            f"Used: {used_line}",
            f"Route attempts: {len(route_records)} (success={successful_routes})",
            f"Execution steps: {execution_steps} (success={execution_steps_success})",
            f"Execution mode: {execution_mode} (placeholder_outputs={placeholder_outputs}/{max(1, output_result_count)})",
            f"Live mode gate: {'rejected' if live_mode_rejected else ('required' if live_mode_required else 'off')}",
            f"Checks passed: {', '.join(successful_checks) if successful_checks else 'none'}",
            f"Evidence gate: {'rejected' if evidence_gate_applied else 'pass'} ({', '.join(evidence_gate_failures) if evidence_gate_failures else 'ok'})",
            f"Artifacts: created={artifact_created} modified={artifact_modified}",
            f"Quality: productivity={productivity_score}/100 ({productivity_status}) reasoning={reasoning_score}/100",
            f"Action hint: {next_action_hint}",
            f"Strict mode: {'blocked_close' if strict_mode_applied else ('on' if payload.strict_mode else 'off')}",
            f"Low productivity gate: {'rejected' if low_productivity_rejected else ('override' if low_productivity_override and productivity_score < productivity_threshold else 'active')}",
            f"Auto-extended rounds: +{auto_extended_rounds}",
            "",
            "Workflow phases:",
            workflow_lines,
            "",
            "Delegation results:",
            "\n".join(delegated_lines[:12]) if delegated_lines else "- none",
            "",
            "Lead message for user:",
            user_facing_summary,
        ]
        if artifact_files:
            response_lines.extend(
                [
                    "",
                    f"Artifact files: {', '.join(artifact_files[:12])}",
                ]
            )
        if not lead_completed or low_productivity_rejected:
            response_lines.extend(
                [
                    "",
                    "Next step: continue to close pending phases and produce final synthesis.",
                ]
            )
        merged_response = "\n".join(response_lines)

        return TeamChatResponse(
            task_id=task_root,
            role=Role.TEAM_LEAD.value,
            state=final_state,
            response=merged_response[:4000],
            decision_justification=lead_justification[:2000],
            elapsed_ms=elapsed_ms,
            lead_task_id=lead_task_id,
            delegated_task_ids=delegated_task_ids,
            phase_task_ids=phase_task_ids,
            chat_mode=chat_mode,
            round_budget=round_budget,
            rounds_used=rounds_used,
            completed_tasks=completed_tasks,
            pending_tasks=pending_tasks,
            continuation_requested=continuation_requested,
            continuation_of=continuation_of,
            artifact_created=artifact_created,
            artifact_modified=artifact_modified,
            artifact_files=artifact_files,
            productivity_score=productivity_score,
            reasoning_score=reasoning_score,
            productivity_status=productivity_status,
            execution_attempts=execution_attempts,
            execution_success=execution_success,
            execution_steps=execution_steps,
            execution_steps_success=execution_steps_success,
            successful_checks=successful_checks,
            successful_check_count=len(successful_checks),
            live_mode_required=live_mode_required,
            live_mode_rejected=live_mode_rejected,
            next_action_hint=next_action_hint,
            strict_mode=bool(payload.strict_mode),
            strict_mode_applied=strict_mode_applied,
            auto_extended_rounds=auto_extended_rounds,
            productivity_threshold=productivity_threshold,
            low_productivity_rejected=low_productivity_rejected,
            low_productivity_override=low_productivity_override,
            execution_mode=execution_mode,
            placeholder_outputs=placeholder_outputs,
            placeholder_output_ratio=round(placeholder_output_ratio, 4),
            evidence_gate_applied=evidence_gate_applied,
            evidence_gate_failures=evidence_gate_failures,
        )

    return await asyncio.to_thread(_run_chat)


@app.get("/api/aiteam/chat/progress/{task_id}", response_model=TeamChatProgressResponse)
async def get_aiteam_chat_progress(task_id: str, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    normalized_root = _normalize_task_root(task_id)
    if not normalized_root:
        return TeamChatProgressResponse(task_id="", exists=False)
    if not runtime_dir.exists():
        return TeamChatProgressResponse(task_id=normalized_root, exists=False)
    return await asyncio.to_thread(_build_chat_progress, runtime_dir, normalized_root)


# ── Background chat runs with SSE streaming ───────────────────

_background_runs: dict[str, dict] = {}  # task_root → {status, progress_queue, result}
_background_runs_lock = threading.Lock()


@app.post("/api/aiteam/chat/async")
async def post_aiteam_chat_async(payload: TeamChatRequest, request: Request):
    """Inicia un chat en background y retorna el task_id inmediatamente.

    Usar GET /api/aiteam/chat/stream/{task_id} para recibir progreso via SSE.
    """
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    import queue as queue_module

    task_root = f"CHAT-{uuid.uuid4().hex[:8].upper()}"
    progress_queue = queue_module.Queue()

    with _background_runs_lock:
        _background_runs[task_root] = {
            "status": "running",
            "progress_queue": progress_queue,
            "result": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    def _run_bg():
        try:
            from aiteam.cli import build_default_orchestrator
            from aiteam.types import Complexity, Criticality, Role, TaskState, WorkTask

            role_map = {
                "team_lead": Role.TEAM_LEAD,
                "lead": Role.TEAM_LEAD,
                "researcher": Role.RESEARCHER,
                "engineer": Role.ENGINEER,
                "reviewer": Role.REVIEWER,
                "qa": Role.QA,
            }
            complexity_map = {
                "low": Complexity.LOW,
                "medium": Complexity.MEDIUM,
                "high": Complexity.HIGH,
            }
            criticality_map = {
                "low": Criticality.LOW,
                "medium": Criticality.MEDIUM,
                "high": Criticality.HIGH,
            }

            preferred_role = role_map.get(payload.role.strip().lower(), Role.ENGINEER)
            complexity = complexity_map.get(
                payload.complexity.strip().lower(), Complexity.MEDIUM
            )
            criticality = criticality_map.get(
                payload.criticality.strip().lower(), Criticality.MEDIUM
            )

            orch = build_default_orchestrator(runtime_dir=runtime_dir)
            round_budget = min(max(1, payload.max_rounds or 5), 20)

            # Submit tasks (simplified — lead_intake only for async)
            task = WorkTask(
                task_id=f"{task_root}::build",
                title=f"Build: {payload.message[:80]}",
                description=payload.message,
                role=preferred_role,
                complexity=complexity,
                criticality=criticality,
                metadata={"required_capabilities": ["coding", "analysis"]},
            )
            orch.submit_task(task)

            # Run with progress
            for progress in orch.run_until_idle_with_progress(max_rounds=round_budget):
                progress["task_root"] = task_root
                progress_queue.put(("progress", progress))

            # Collect result
            tasks = orch.taskboard.list_tasks()
            completed = sum(1 for t in tasks if t.state == TaskState.COMPLETED)
            failed = sum(1 for t in tasks if t.state == TaskState.FAILED)
            results = []
            for t in tasks:
                result_text = str(t.metadata.get("result", ""))
                if result_text:
                    results.append(result_text[:500])

            final = {
                "task_root": task_root,
                "status": "completed",
                "tasks_total": len(tasks),
                "tasks_completed": completed,
                "tasks_failed": failed,
                "result_summary": "\n---\n".join(results)[:3000],
            }
            progress_queue.put(("done", final))

            with _background_runs_lock:
                if task_root in _background_runs:
                    _background_runs[task_root]["status"] = "completed"
                    _background_runs[task_root]["result"] = final

        except Exception as exc:
            error_result = {
                "task_root": task_root,
                "status": "failed",
                "error": str(exc)[:500],
            }
            progress_queue.put(("error", error_result))
            with _background_runs_lock:
                if task_root in _background_runs:
                    _background_runs[task_root]["status"] = "failed"
                    _background_runs[task_root]["result"] = error_result

    thread = threading.Thread(target=_run_bg, daemon=True)
    thread.start()

    return {
        "task_root": task_root,
        "status": "running",
        "stream_url": f"/api/aiteam/chat/stream/{task_root}",
    }


@app.get("/api/aiteam/chat/stream/{task_root}")
async def stream_chat_progress(task_root: str, request: Request):
    """SSE endpoint para recibir progreso de un chat en background."""
    _require_api_auth_request(request)

    with _background_runs_lock:
        run = _background_runs.get(task_root)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No background run for {task_root}"
        )

    progress_queue = run["progress_queue"]

    async def event_stream():
        import queue as queue_module

        while True:
            try:
                event_type, data = await asyncio.to_thread(
                    progress_queue.get, timeout=30
                )
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
                if event_type in ("done", "error"):
                    break
            except Exception:
                # Timeout or queue empty — send keepalive
                yield f"event: keepalive\ndata: {{}}\n\n"
                # Check if run is still active
                with _background_runs_lock:
                    current = _background_runs.get(task_root, {})
                if current.get("status") in ("completed", "failed"):
                    break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/aiteam/chat/async/{task_root}")
async def get_async_chat_status(task_root: str, request: Request):
    """Consulta el estado de un chat async."""
    _require_api_auth_request(request)
    with _background_runs_lock:
        run = _background_runs.get(task_root)
    if run is None:
        raise HTTPException(
            status_code=404, detail=f"No background run for {task_root}"
        )
    return {
        "task_root": task_root,
        "status": run["status"],
        "started_at": run.get("started_at", ""),
        "result": run.get("result"),
    }


@app.get("/api/aiteam/operator/timeline", response_model=OperatorTimelineResponse)
async def get_aiteam_operator_timeline(
    request: Request,
    task_id: str = "",
    limit: int = 120,
    key_only: bool = True,
):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = workspace / "runtime"
    if not runtime_dir.exists():
        return OperatorTimelineResponse()
    return await asyncio.to_thread(
        _build_operator_timeline,
        runtime_dir,
        task_id=task_id,
        limit=limit,
        key_only=key_only,
    )


@app.get("/api/aiteam/mailbox/inbox")
async def get_mailbox_inbox(request: Request):
    """Query agent mailbox with optional filters."""
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    runtime_dir = Path(workspace) / "runtime"
    mailbox_path = runtime_dir / "mailbox.jsonl"
    if not mailbox_path.exists():
        return {"messages": [], "total": 0, "unread": 0}

    from aiteam.mailbox import Mailbox

    mb = Mailbox(mailbox_path)
    recipient = request.query_params.get("recipient", "")
    sender_filter = request.query_params.get("sender", "")
    task_filter = request.query_params.get("task_id", "")
    unread_only = request.query_params.get("unread_only", "false").lower() in (
        "true",
        "1",
    )
    limit = min(int(request.query_params.get("limit", "50")), 200)

    messages = mb.inbox_query(
        recipient=recipient,
        unread_only=unread_only,
        sender=sender_filter or None,
        task_id=task_filter or None,
        limit=limit,
    )
    total = len(mb.list_messages(recipient=recipient or None))
    unread = mb.unread_count(recipient) if recipient else 0

    return {
        "messages": [
            {
                "message_id": m.message_id,
                "timestamp": m.timestamp,
                "sender": m.sender,
                "recipient": m.recipient,
                "subject": m.subject,
                "body": m.body[:500],
                "task_id": m.task_id,
            }
            for m in messages
        ],
        "total": total,
        "unread": unread,
    }


@app.get("/api/fs/tree")
async def get_fs_tree(request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)

    def build_tree(path: Path):
        name = path.name
        if name in [
            ".git",
            "__pycache__",
            "venv",
            ".pytest_cache",
            ".aiteam_snapshots",
            "node_modules",
        ]:
            return None
        try:
            if path.is_file():
                return {
                    "name": name,
                    "path": str(path.relative_to(workspace).as_posix()),
                    "type": "file",
                }
            elif path.is_dir():
                children = []
                for child in path.iterdir():
                    node = build_tree(child)
                    if node:
                        children.append(node)
                # Sort alphabetically, directories first
                children.sort(
                    key=lambda x: (
                        0 if x["type"] == "directory" else 1,
                        x["name"].lower(),
                    )
                )
                return {
                    "name": name,
                    "path": str(path.relative_to(workspace).as_posix()),
                    "type": "directory",
                    "children": children,
                }
        except Exception:
            return None

    return build_tree(workspace)


class FileContent(BaseModel):
    content: str


@app.get("/api/fs/file")
async def read_file(path: str, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    target = _safe_workspace_target(workspace, path)
    if target is None:
        raise HTTPException(status_code=400, detail="Path is outside workspace")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        return {"content": target.read_text(encoding="utf-8")}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except Exception as e:
        logger.exception("Error reading file: %s", path)
        raise HTTPException(status_code=500, detail="Error reading file")


@app.put("/api/fs/file")
async def write_file(path: str, payload: FileContent, request: Request):
    _require_api_auth_request(request)
    workspace = _workspace_from_request(request, get_current_workspace(), PROJECT_ROOT)
    target = _safe_workspace_target(workspace, path)
    if target is None:
        raise HTTPException(status_code=400, detail="Path is outside workspace")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload.content, encoding="utf-8")
        return {"success": True}
    except PermissionError:
        raise HTTPException(status_code=403, detail="File is read-only or in use")
    except Exception as e:
        logger.exception("Error writing file: %s", path)
        raise HTTPException(status_code=500, detail="Error writing file")


@app.websocket("/api/terminal")
async def terminal_endpoint(websocket: WebSocket):
    global active_pty
    header_map = {k.lower(): v for k, v in websocket.headers.items()}
    query_api_key = str(websocket.query_params.get("api_key", "") or "").strip()
    query_workspace_path = str(
        websocket.query_params.get("workspace_path", "") or ""
    ).strip()
    if query_api_key:
        header_map.setdefault("x-api-key", query_api_key)
    if query_workspace_path:
        header_map["x-workspace-path"] = query_workspace_path
    logger.debug(
        "WebSocket connection request to /api/terminal from %s",
        websocket.client.host if websocket.client else "unknown",
    )
    # Temporarily bypass auth for debugging if requested by localhost
    is_authorized = _is_authorized(header_map)
    if not is_authorized:
        logger.debug(
            "WebSocket auth failed for header_map keys: %s", list(header_map.keys())
        )
        # Bypass for local dev
        if websocket.client and websocket.client.host in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            logger.debug("Bypassing auth for local connection")
            is_authorized = True

    if not is_authorized:
        await websocket.close(code=1008)
        return
    workspace = _workspace_from_header_map(
        header_map, get_current_workspace(), PROJECT_ROOT
    )
    logger.debug("WebSocket accepted for workspace: %s", workspace)
    await websocket.accept()
    if PTY is None:
        await websocket.send_text(
            "Error: pywinpty is not installed on this system.\r\n"
        )
        await websocket.close()
        return

    pty = PTY(80, 24)
    active_pty = pty
    # Spawn shell inside the workspace (cross-platform)
    _shell = "powershell.exe" if sys.platform == "win32" else "bash"
    if sys.platform == "win32":
        # Check standard location as fallback if powershell.exe not in PATH
        std_ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        if not any(
            Path(p).joinpath("powershell.exe").exists()
            for p in os.environ.get("PATH", "").split(os.pathsep)
        ):
            if Path(std_ps).exists():
                _shell = std_ps

    try:
        pty.spawn(_shell, cwd=str(workspace))
    except Exception as e:
        logger.exception("Failed to spawn shell %s in %s", _shell, workspace)
        await websocket.send_text(f"\r\nError: Failed to spawn shell {_shell}. {e}\r\n")
        await websocket.close()
        return

    async def read_pty():
        try:
            while pty.isalive():
                # Read from PTY in a separate thread so it doesn't block the async event loop
                data = await asyncio.to_thread(pty.read, 4096)
                if data:
                    await websocket.send_text(data)
                else:
                    await asyncio.sleep(0.01)
        except Exception as e:
            logger.error("PTY read error: %s", e)

    task = asyncio.create_task(read_pty())
    try:
        while True:
            message = await websocket.receive_text()
            # If the user sends a resize payload like '{"type":"resize","cols":100,"rows":30}'
            if message.startswith('{"type":"resize"'):
                try:
                    payload = json.loads(message)
                    cols = payload.get("cols", 80)
                    rows = payload.get("rows", 24)
                    pty.set_size(cols, rows)
                except Exception:
                    pass
            else:
                pty.write(message)
    except WebSocketDisconnect:
        logger.info("Client disconnected from terminal")
    finally:
        if active_pty == pty:
            active_pty = None
        task.cancel()
        pty.close()
