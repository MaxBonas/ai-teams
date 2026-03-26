from __future__ import annotations

import argparse
import os
import webbrowser
import shutil
import subprocess
import shlex
import urllib.error
import urllib.request
from pathlib import Path
import json
from datetime import datetime, timezone

from aiteam.adapters import (
    ApiAdapter,
    SubscriptionAdapter,
    build_external_adapter_template,
    load_external_adapters,
)
from aiteam.autotools import AutoToolIntegrator
from aiteam.communication import MeetingParticipant
from aiteam.config import build_default_router_policy
from aiteam.dashboard import build_dashboard_payload, render_dashboard_html
from aiteam.finops import BudgetManager, BudgetPolicy
from aiteam.observability import EventLogger
from aiteam.orchestrator import AITeamOrchestrator
from aiteam.pilot import PilotThresholds, compute_pilot_metrics, evaluate_pilot
from aiteam.provider_ops import build_provider_ops_view
from aiteam.router import HybridRouter
from aiteam.snapshots import SnapshotManager
from aiteam.tool_inventory import write_inventory
from aiteam.types import Complexity, Criticality, Role, WorkTask
from aiteam.learning_registry import LearningRegistry


def _load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env_key = key.strip()
        if not env_key or env_key in os.environ:
            continue
        clean_value = value.strip().strip('"').strip("'")
        os.environ[env_key] = clean_value


def build_default_orchestrator(
    runtime_dir: Path,
    browser_mode: str = "basic",
    environment: str = "dev",
) -> AITeamOrchestrator:
    policy = build_default_router_policy()
    budget = BudgetManager(
        runtime_dir=runtime_dir,
        policy=BudgetPolicy(
            daily_api_budget_usd=policy.daily_api_budget_usd,
            monthly_api_budget_usd=policy.monthly_api_budget_usd,
        ),
    )
    event_logger = EventLogger(runtime_dir)

    adapters = [
        # ── Canal Subscription (Pro-first) ─────────────────────────────────
        # Prioridad maxima: usan la suscripcion del usuario sin coste extra por token.
        # Requieren AITEAM_ENABLE_LIVE_API=1 + clave de cada provider en .env.
        SubscriptionAdapter(
            name="openai_pro",
            provider="openai",
            model="gpt-4.1",  # gpt-4.1 (2025) — mejor relacion calidad/coste
            capabilities={"reasoning", "coding", "review", "analysis"},
            routing_priority=10,
        ),
        SubscriptionAdapter(
            name="gemini_pro",
            provider="google",
            model="gemini-2.0-flash",  # 2.0 Flash — rapido, multimodal, sin coste extra en Pro
            capabilities={"analysis", "summarization", "reasoning", "coding"},
            routing_priority=20,
        ),
        SubscriptionAdapter(
            name="claude_pro",
            provider="anthropic",
            model="claude-3-5-sonnet-20241022",  # Sonnet — mejor para coding y razonamiento complejo
            capabilities={"reasoning", "coding", "analysis", "review"},
            routing_priority=30,
        ),
        SubscriptionAdapter(
            name="claude_haiku",
            provider="anthropic",
            model="claude-3-5-haiku-20241022",  # Haiku — rapido y barato para tasks simples
            capabilities={"reasoning", "coding", "analysis"},
            routing_priority=40,
            cost_tier=0,
        ),
        # ── Canal API (fallback presupuestado) ─────────────────────────────
        # Activado cuando subscription falla o se agota. Consume cuota de presupuesto.
        ApiAdapter(
            name="openai_api_mini",
            provider="openai",
            model="gpt-4.1-mini",
            capabilities={"reasoning", "coding", "analysis", "review"},
            cost_tier=1,
        ),
        ApiAdapter(
            name="openai_api_fast",
            provider="openai",
            model="gpt-4o-mini",
            capabilities={"reasoning", "analysis", "multimodal", "tool_calling"},
            cost_tier=1,
        ),
        ApiAdapter(
            name="groq_api_fast",
            provider="groq",
            model="llama-3.3-70b-versatile",  # Llama 3.3 70B — fallback gratuito ultra-rapido
            capabilities={"reasoning", "coding", "analysis", "review"},
            cost_tier=0,
            require_key=True,
        ),
    ]

    external_config_path = runtime_dir / "adapters.json"
    external_adapters = load_external_adapters(external_config_path)
    if external_adapters:
        adapters = external_adapters + adapters
        _prepend_provider_priority(
            policy.preferred_subscription_providers, external_adapters, "subscription"
        )
        _prepend_provider_priority(
            policy.preferred_api_providers, external_adapters, "api"
        )

    shared_tools_root = Path(
        os.getenv("AITEAM_SHARED_TOOLS_ROOT", str(Path.cwd().parent))
    ).resolve()
    additional_roots = [shared_tools_root] if shared_tools_root.exists() else []

    router = HybridRouter(
        adapters=adapters,
        policy=policy,
        budget_manager=budget,
        event_logger=event_logger,
    )
    return AITeamOrchestrator(
        router=router,
        runtime_dir=runtime_dir,
        project_root=Path.cwd(),
        additional_workspace_roots=additional_roots,
        browser_mode=browser_mode,
        environment=environment,
    )


def _prepend_provider_priority(
    priority: list[str], adapters: list, channel: str
) -> None:
    providers = []
    for adapter in adapters:
        if adapter.channel.value != channel:
            continue
        if adapter.provider not in providers:
            providers.append(adapter.provider)
    for provider in reversed(providers):
        if provider in priority:
            priority.remove(provider)
        priority.insert(0, provider)


def _default_tool_requests_template() -> dict:
    return {
        "tool_requirements": [
            {
                "name": "context7_mcp",
                "required": False,
                "enabled": False,
                "acquire": True,
                "category": "mcp",
                "source_type": "npm",
                "source": "@upstash/context7-mcp",
                "capabilities": ["documentation", "ground_truth", "research"],
                "role_targets": ["researcher", "engineer"],
            },
            {
                "name": "github_mcp",
                "required": False,
                "enabled": False,
                "acquire": True,
                "category": "mcp",
                "source_type": "npm",
                "source": "@modelcontextprotocol/server-github",
                "capabilities": ["github", "pr_management", "issue_triage"],
                "role_targets": ["team_lead", "engineer", "reviewer"],
                "requires_approval": True,
            },
            {
                "name": "remotion_skill",
                "required": False,
                "category": "skill",
                "source_type": "builtin",
                "capabilities": ["video_generation", "multimodal", "rendering"],
            },
        ]
    }


def _default_provider_accounts_template() -> dict:
    return {
        "subscription_accounts": [
            {
                "provider": "openai",
                "model": "gpt-4o",
                "enabled_env": "AITEAM_SUBSCRIPTION_OPENAI_ENABLED",
                "notes": "Primary senior coder account",
            },
            {
                "provider": "google",
                "model": "gemini-1.5-pro",
                "enabled_env": "AITEAM_SUBSCRIPTION_GOOGLE_ENABLED",
                "notes": "Second senior coder account",
            },
            {
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-20241022",
                "enabled_env": "AITEAM_SUBSCRIPTION_ANTHROPIC_ENABLED",
                "notes": "Third senior coder account",
            },
        ],
        "api_accounts": [
            {
                "provider": "openai",
                "models": ["gpt-4.1-mini", "gpt-4o-mini"],
                "api_key_env": "OPENAI_API_KEY",
                "notes": "Fallback API budget-aware",
            },
            {
                "provider": "groq",
                "models": ["llama-3.3-70b-versatile"],
                "api_key_env": "GROQ_API_KEY",
                "notes": "Secondary API fallback for fast reasoning",
            },
        ],
    }


def _provider_connection_specs() -> list[dict]:
    return [
        {
            "name": "openai_pro_cli",
            "provider": "openai",
            "model": "gpt-4o",
            "required": True,
            "env_command": "AITEAM_OPENAI_PRO_COMMAND",
            "candidates": [
                ["codex", "--version"],
                ["npx", "-y", "@openai/codex", "--version"],
                ["opencode", "--help"],
            ],
            "command_template": [
                "npx",
                "-y",
                "@openai/codex",
                "exec",
                "--skip-git-repo-check",
                "{prompt}",
            ],
            "capabilities": ["coding", "reasoning", "review", "analysis"],
            "role_targets": ["team_lead", "engineer", "reviewer"],
            "routing_priority": 10,
            "source": "openai_pro_cli",
        },
        {
            "name": "gemini_pro_cli",
            "provider": "google",
            "model": "gemini-1.5-pro",
            "required": True,
            "env_command": "AITEAM_GEMINI_PRO_COMMAND",
            "candidates": [
                ["gemini", "--help"],
                ["gemini-cli", "--help"],
                ["npx", "-y", "@google/gemini-cli", "--help"],
            ],
            "command_suffix": ["{prompt}"],
            "capabilities": ["coding", "reasoning", "analysis", "summarization"],
            "role_targets": ["team_lead", "engineer", "researcher"],
            "routing_priority": 20,
            "source": "gemini_pro_cli",
        },
        {
            "name": "claude_pro_cli",
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-20241022",
            "required": True,
            "env_command": "AITEAM_CLAUDE_PRO_COMMAND",
            "candidates": [
                ["claude", "--version"],
                ["npx", "-y", "@anthropic-ai/claude-code", "--version"],
            ],
            "command_suffix": ["-p", "{prompt}"],
            "capabilities": ["coding", "reasoning", "analysis", "review"],
            "role_targets": ["team_lead", "engineer", "reviewer", "researcher"],
            "routing_priority": 30,
            "source": "claude_pro_cli",
        },
    ]


def _resolve_provider_command(spec: dict) -> list[str] | None:
    env_key = str(spec.get("env_command", "")).strip()
    if env_key:
        raw = os.getenv(env_key, "").strip()
        parsed = _parse_command_value(raw)
        if parsed and _probe_command(parsed):
            return parsed

    candidates = spec.get("candidates", [])
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        command = [str(item) for item in candidate if str(item).strip()]
        if not command:
            continue
        if _probe_command(command):
            template = spec.get("command_template")
            if isinstance(template, list) and all(
                isinstance(item, str) for item in template
            ):
                return [str(item) for item in template]

            invocation = [item for item in command if item != "--help"]
            if command[0].lower().endswith("claude") or "claude" in command[0].lower():
                return [command[0], "-p", "{prompt}"]
            if any("claude-code" in part.lower() for part in command):
                package = next(
                    (part for part in command if "claude-code" in part.lower()),
                    "@anthropic-ai/claude-code",
                )
                return [command[0], "-y", package, "-p", "{prompt}"]
            if any("gemini-cli" in part.lower() for part in command):
                package = next(
                    (part for part in command if "gemini-cli" in part.lower()),
                    "@google/gemini-cli",
                )
                if (
                    os.name == "nt"
                    and os.getenv("GOOGLE_API_KEY")
                    and not os.getenv("GEMINI_API_KEY")
                ):
                    return [
                        "cmd",
                        "/c",
                        f"set GEMINI_API_KEY=%GOOGLE_API_KEY% && {command[0]} -y {package} {{prompt}}",
                    ]

            if invocation and invocation[-1] != "{prompt}":
                invocation.append("{prompt}")
            return invocation
    return None


def _provider_runtime_health(spec: dict, command: list[str] | None) -> tuple[bool, str]:
    if not command:
        return False, "command_missing"
    provider = str(spec.get("provider", "")).strip().lower()
    if provider == "openai":
        return _openai_pro_health()
    if provider == "google":
        return _gemini_health(command)
    if provider == "anthropic":
        return _claude_auth_health(command)
    return True, "command_ok"


def _collect_provider_health(
    specs: list[dict] | None = None,
) -> tuple[list[dict], int, int]:
    provider_specs = specs if specs is not None else _provider_connection_specs()
    rows: list[dict] = []
    required_healthy = 0
    required_total = 0

    for spec in provider_specs:
        command = _resolve_provider_command(spec)
        healthy, details = _provider_runtime_health(spec, command)
        required = bool(spec.get("required", False))
        if required:
            required_total += 1
            if healthy:
                required_healthy += 1
        rows.append(
            {
                "provider": spec["provider"],
                "name": spec["name"],
                "healthy": healthy,
                "details": details,
                "command": command or [],
                "required": required,
            }
        )

    return rows, required_healthy, required_total


def _required_provider_health_minimum(environment: str, required_total: int) -> int:
    if required_total <= 0:
        return 0
    env = environment.strip().lower()
    if env == "dev":
        return 1
    return required_total


def _probe_command(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    resolved = _resolve_executable(executable)
    if resolved is None:
        return False
    args = [resolved] + command[1:]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _claude_auth_health(command: list[str] | None = None) -> tuple[bool, str]:
    auth_command: list[str] | None = None
    if command:
        normalized = [
            str(item).strip()
            for item in command
            if str(item).strip() and str(item).strip() != "{prompt}"
        ]
        if normalized:
            base = normalized[0]
            if Path(base).name.lower().startswith("npx") and any(
                "claude-code" in item.lower() for item in normalized[1:]
            ):
                package = next(
                    (item for item in normalized[1:] if "claude-code" in item.lower()),
                    "@anthropic-ai/claude-code",
                )
                auth_command = [base, "-y", package, "auth", "status"]
            else:
                auth_command = [base, "auth", "status"]
    if not auth_command:
        claude = shutil.which("claude")
        if not claude:
            return False, "claude_not_found"
        auth_command = [claude, "auth", "status"]
    resolved = _resolve_executable(auth_command[0])
    if resolved is None:
        return False, "claude_not_found"
    auth_command[0] = resolved
    try:
        proc = subprocess.run(
            auth_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"claude_auth_error:{exc}"
    if proc.returncode != 0:
        return False, "claude_auth_status_failed"
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return True, "claude_auth_unknown"
    if not isinstance(payload, dict):
        return True, "claude_auth_unknown"
    logged = bool(payload.get("loggedIn", False))
    subscription = str(payload.get("subscriptionType", "unknown"))
    if not logged:
        return False, "claude_not_logged_in"
    return True, f"claude_logged_in:{subscription}"


def _detect_local_coding_runtime() -> tuple[bool, dict[str, object]]:
    candidates = [
        shutil.which("ollama"),
        str(Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"),
    ]
    binary = next((item for item in candidates if item and Path(item).exists()), None)
    if not binary:
        return False, {
            "provider": "ollama",
            "healthy": False,
            "details": "ollama_not_found",
            "command": [],
        }
    try:
        proc = subprocess.run(
            [binary, "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, {
            "provider": "ollama",
            "healthy": False,
            "details": f"ollama_probe_error:{exc}",
            "command": [binary],
        }
    blob = proc.stdout or ""
    preferred_models = ["aiteam-qwen-coder:14b", "qwen2.5-coder:14b"]
    if proc.returncode != 0:
        return False, {
            "provider": "ollama",
            "healthy": False,
            "details": "ollama_list_failed",
            "command": [binary],
        }
    model = next((item for item in preferred_models if item in blob), "")
    if not model:
        return False, {
            "provider": "ollama",
            "healthy": False,
            "details": f"model_missing:{preferred_models[0]}",
            "command": [binary],
        }
    return True, {
        "provider": "ollama",
        "healthy": True,
        "details": f"model_ready:{model}",
        "command": [binary, "run", model, "{prompt}"],
        "model": model,
    }


def _openai_pro_health() -> tuple[bool, str]:
    npx = _resolve_executable("npx")
    if not npx:
        return False, "npx_not_found"
    command = [npx, "-y", "@openai/codex", "login", "status"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"openai_status_error:{exc}"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().lower()
        if "not logged in" in stderr:
            return False, "openai_not_logged_in"
        return False, "openai_status_failed"
    out = " ".join([(proc.stdout or "").strip(), (proc.stderr or "").strip()]).lower()
    if "logged in" in out:
        if "chatgpt" in out:
            return True, "openai_logged_in:chatgpt"
        return True, "openai_logged_in"
    return False, "openai_not_logged_in"


def _gemini_health(command: list[str]) -> tuple[bool, str]:
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return True, "gemini_auth_env_key"

    auth_command = _gemini_auth_status_command(command)
    if not auth_command:
        return False, "gemini_command_missing"

    resolved = _resolve_executable(auth_command[0])
    if resolved is None:
        return False, "gemini_command_not_found"
    auth_command[0] = resolved

    try:
        proc = subprocess.run(
            auth_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"gemini_probe_error:{exc}"
    if proc.returncode == 0:
        return True, "gemini_logged_in"
    stderr = (proc.stderr or "").strip().lower()
    stdout = (proc.stdout or "").strip().lower()
    blob = f"{stderr} {stdout}"
    if (
        "please set an auth method" in blob
        or "gemini_api_key" in blob
        or "google_genai_use_vertexai" in blob
        or "not logged in" in blob
    ):
        return False, "gemini_auth_missing"
    return False, "gemini_probe_failed"


def _gemini_auth_status_command(command: list[str] | None) -> list[str] | None:
    if not command:
        return None
    base = str(command[0]).strip()
    if not base:
        return None

    normalized = [
        str(item).strip()
        for item in command
        if str(item).strip() and str(item).strip() != "{prompt}"
    ]
    if (
        len(normalized) >= 3
        and normalized[-2].lower() == "auth"
        and normalized[-1].lower() == "status"
    ):
        return normalized

    executable_name = Path(base).name.lower()
    if executable_name.startswith("npx"):
        package = "@google/gemini-cli"
        for item in command[1:]:
            text = str(item).strip()
            if not text or text in {"-y", "--yes", "--help", "{prompt}"}:
                continue
            if "gemini-cli" in text.lower():
                package = text
                break
        return [base, "-y", package, "auth", "status"]

    return [base, "auth", "status"]


def _resolve_executable(executable: str) -> str | None:
    name = executable.strip()
    if not name:
        return None
    resolved = shutil.which(name)
    if resolved is not None:
        return resolved
    if os.name == "nt" and not name.lower().endswith((".cmd", ".exe", ".bat")):
        for suffix in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(name + suffix)
            if resolved is not None:
                return resolved
    return None


def _parse_command_value(raw: str) -> list[str] | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return [item for item in parsed if item.strip()]
        return None
    try:
        parts = shlex.split(text)
    except ValueError:
        return None
    return [part for part in parts if part.strip()]


def _load_json_file(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return dict(default)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return dict(default)
    if not isinstance(payload, dict):
        return dict(default)
    return payload


def _build_learning_export_for_notebook(
    runtime_dir: Path,
    export_format: str,
    days: int,
) -> str:
    registry = LearningRegistry(runtime_dir)
    normalized = export_format.strip().lower()
    if normalized == "json":
        learnings = registry.read_all()
        return json.dumps(
            {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total": len(learnings),
                "learnings": learnings,
            },
            indent=2,
            default=str,
        )
    if normalized == "markdown":
        return registry.export_markdown()

    all_learnings = registry.read_all()
    now = datetime.now(timezone.utc)
    lines = [
        "# Learning Registry Export",
        f"**Exported**: {now.strftime('%Y-%m-%d')} at {now.strftime('%H:%M:%S')} UTC",
        "",
    ]
    if not all_learnings:
        lines.append("*No learnings recorded yet.*")
        return "\n".join(lines)

    summary = registry.summary()
    lines.extend(
        [
            "## Summary",
            f"- **Total Learnings**: {summary.get('total', 0)}",
            f"- **Open Items**: {summary.get('open', 0)}",
            f"- **Addressed**: {summary.get('addressed', 0)}",
            f"- **Archived**: {summary.get('archived', 0)}",
            "",
        ]
    )

    cutoff = now.timestamp() - max(1, days) * 86400
    recent: list[dict] = []
    for record in all_learnings:
        ts = str(record.get("ts") or record.get("created_at") or "").strip()
        if not ts:
            continue
        try:
            ts_norm = ts.replace("Z", "+00:00")
            ts_value = datetime.fromisoformat(ts_norm).timestamp()
        except ValueError:
            continue
        if ts_value >= cutoff:
            recent.append(record)

    lines.append(f"## Recent Learnings (Last {max(1, days)} Days)")
    if recent:
        for item in recent[-10:]:
            lines.append(
                f"- **{item.get('category', 'UNKNOWN')}**: {item.get('title', 'Untitled')} "
                f"[*{item.get('status', 'unknown')}*]"
            )
    else:
        lines.append("*No recent learnings.*")
    lines.append("")

    lines.append("## Open Action Items")
    open_items = registry.read_open_items()
    if open_items:
        for item in open_items:
            lines.append(
                f"- [{str(item.get('priority', 'medium')).upper()}] "
                f"{item.get('category', 'UNKNOWN')}: {item.get('title', 'Untitled')}"
            )
    else:
        lines.append("*No open items.*")
    return "\n".join(lines)


def _notebooklm_ingest_command(runtime_dir: Path) -> list[str] | None:
    raw = os.getenv("NOTEBOOKLM_INGEST_COMMAND", "").strip()
    parsed = _parse_command_value(raw)
    if parsed:
        return parsed

    script_path = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "notebooklm_ingest_bridge.py"
    ).resolve()
    if not script_path.exists():
        return None
    python_exec = _resolve_executable("python") or "python"
    return [
        python_exec,
        str(script_path),
        "--payload-path",
        "{payload_path}",
        "--runtime-dir",
        str(runtime_dir),
    ]


def cmd_notebooklm_connect(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    adapters_path = runtime_dir / "adapters.json"
    if not adapters_path.exists():
        build_external_adapter_template(adapters_path)

    payload = _load_json_file(adapters_path, default={"external_adapters": []})
    adapters = payload.get("external_adapters", [])
    if not isinstance(adapters, list):
        adapters = []

    python_exec = _resolve_executable("python") or "python"
    entry = {
        "type": "external_program",
        "name": "notebooklm_bridge",
        "provider": "notebooklm",
        "model": "knowledge-sync-v1",
        "channel": "subscription",
        "command": [
            python_exec,
            "-m",
            "aiteam.cli",
            "notebooklm-sync",
            "--runtime-dir",
            str(runtime_dir),
            "--notebooklm-from-prompt",
            "{prompt}",
            "--notebooklm-title",
            "AI Team Adapter Sync",
            "--quiet",
        ],
        "capabilities": ["knowledge_base", "learning_sync", "summarization"],
        "role_targets": ["team_lead", "researcher"],
        "priority": "secondary",
        "routing_priority": 180,
        "enabled": True,
        "requires_approval": False,
        "timeout_seconds": 180,
        "cost_tier": 0,
        "source": "notebooklm_bridge",
    }
    adapters = _upsert_adapter(adapters, entry)
    payload["external_adapters"] = adapters
    adapters_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(f"NotebookLM bridge adapter enabled: {adapters_path}")


def cmd_notebooklm_sync(
    runtime_dir: Path,
    notebook_id: str,
    title: str,
    source: str,
    content_file: str,
    from_prompt: str,
    export_format: str,
    days: int,
    dry_run: bool,
    quiet: bool,
) -> dict:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    resolved_notebook_id = (
        notebook_id.strip() or os.getenv("NOTEBOOKLM_NOTEBOOK_ID", "").strip()
    )

    if from_prompt.strip():
        content = from_prompt
        resolved_source = "adapter_prompt"
    elif content_file.strip():
        content = Path(content_file).read_text(encoding="utf-8")
        resolved_source = source.strip() or "file"
    else:
        content = _build_learning_export_for_notebook(runtime_dir, export_format, days)
        resolved_source = source.strip() or "learning_registry"

    now = datetime.now(timezone.utc)
    outbox_dir = runtime_dir / "notebooklm_outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    payload_path = outbox_dir / f"sync_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {
        "ts": now.isoformat(),
        "title": title.strip() or "AI Team Sync",
        "notebook_id": resolved_notebook_id,
        "source": resolved_source,
        "content": content,
    }
    payload_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )

    endpoint = os.getenv("NOTEBOOKLM_INGEST_ENDPOINT", "").strip()
    api_key = (
        os.getenv("NOTEBOOKLM_API_KEY", "").strip()
        or os.getenv("GOOGLE_NOTEBOOKLM_API_KEY", "").strip()
    )
    command = _notebooklm_ingest_command(runtime_dir)

    mode = "manual_export"
    connected = False
    success = True
    details = "Payload queued locally; configure NOTEBOOKLM_INGEST_ENDPOINT or NOTEBOOKLM_INGEST_COMMAND for auto-sync."

    if dry_run:
        mode = "dry_run"
        details = "Dry run completed; payload prepared but not sent."
    elif endpoint:
        mode = "endpoint"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(
            endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                status_code = int(getattr(response, "status", 200))
                response_text = (
                    (response.read() or b"").decode("utf-8", errors="replace").strip()
                )
            connected = 200 <= status_code < 300
            success = connected
            details = f"HTTP {status_code}: {(response_text or 'ok')[:220]}"
        except urllib.error.HTTPError as exc:
            success = False
            details = f"HTTP error: {exc.code}"
        except urllib.error.URLError as exc:
            success = False
            details = f"Connection error: {exc.reason}"
    elif command:
        mode = "command"
        resolved_command = [
            part.replace("{payload_path}", str(payload_path)).replace(
                "{runtime_dir}", str(runtime_dir)
            )
            for part in command
        ]
        try:
            proc = subprocess.run(
                resolved_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            connected = proc.returncode == 0
            success = connected
            details_blob = (proc.stdout or "").strip() or (proc.stderr or "").strip()
            details = f"command_exit={proc.returncode}; {(details_blob or 'ok')[:220]}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            success = False
            details = f"Command sync failed: {exc}"

    status = {
        "ts": now.isoformat(),
        "mode": mode,
        "connected": connected,
        "success": success,
        "details": details,
        "payload_path": str(payload_path),
        "notebook_id": resolved_notebook_id,
        "source": resolved_source,
    }
    status_path = runtime_dir / "notebooklm_sync_status.json"
    status_path.write_text(
        json.dumps(status, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    if not quiet:
        print(json.dumps(status, indent=2, ensure_ascii=True))
    return status


def _upsert_adapter(items: list, entry: dict) -> list:
    name = str(entry.get("name", "")).strip().lower()
    if not name:
        return items
    updated = False
    output = []
    for item in items:
        if not isinstance(item, dict):
            output.append(item)
            continue
        if str(item.get("name", "")).strip().lower() == name:
            output.append(entry)
            updated = True
        else:
            output.append(item)
    if not updated:
        output.append(entry)
    return output


def cmd_init(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "tasks.json").write_text("[]\n", encoding="utf-8")
    (runtime_dir / "mailbox.jsonl").write_text("", encoding="utf-8")
    (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (runtime_dir / "cost_ledger.jsonl").write_text("", encoding="utf-8")
    (runtime_dir / "memory").mkdir(parents=True, exist_ok=True)
    if not (runtime_dir / "mcp_servers.json").exists():
        (runtime_dir / "mcp_servers.json").write_text(
            json.dumps({"servers": []}, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    if not (runtime_dir / "tool_registry.json").exists():
        (runtime_dir / "tool_registry.json").write_text(
            json.dumps({"entries": []}, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    if not (runtime_dir / "adapters.json").exists():
        build_external_adapter_template(runtime_dir / "adapters.json")
    if not (runtime_dir / "tool_requests.json").exists():
        (runtime_dir / "tool_requests.json").write_text(
            json.dumps(_default_tool_requests_template(), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
    if not (runtime_dir / "provider_accounts.json").exists():
        (runtime_dir / "provider_accounts.json").write_text(
            json.dumps(
                _default_provider_accounts_template(), indent=2, ensure_ascii=True
            ),
            encoding="utf-8",
        )
    print(f"Runtime initialized at: {runtime_dir}")


def cmd_plan() -> None:
    plan_path = Path("docs") / "TASKS_AI_TEAM.md"
    print(plan_path.read_text(encoding="utf-8"))


def cmd_demo(runtime_dir: Path, browser_mode: str, environment: str) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )

    task1 = WorkTask(
        task_id="T-001",
        title="Descomponer iniciativa AI Team",
        description="Crear plan de ejecucion para integrar tus programas agenticos.",
        role=Role.TEAM_LEAD,
        complexity=Complexity.MEDIUM,
        criticality=Criticality.MEDIUM,
    )
    task2 = WorkTask(
        task_id="T-002",
        title="Implementar policy Pro-first",
        description=(
            "Definir reglas de prioridad de suscripcion y fallback API. "
            "FORCE_API_FALLBACK"
        ),
        role=Role.ENGINEER,
        complexity=Complexity.HIGH,
        criticality=Criticality.HIGH,
        dependencies=["T-001"],
        metadata={
            "required_capabilities": ["coding"],
            "owned_files": ["aiteam/router.py", "aiteam/config.py"],
        },
    )
    task3 = WorkTask(
        task_id="T-003",
        title="Analizar estrategia de costo",
        description="Validar presupuesto diario y disparadores de fallback.",
        role=Role.RESEARCHER,
        complexity=Complexity.MEDIUM,
        criticality=Criticality.MEDIUM,
        dependencies=["T-001"],
        metadata={"required_capabilities": ["analysis"]},
    )
    task4 = WorkTask(
        task_id="T-004",
        title="Validar entorno local y browser",
        description="Ejecutar comandos de entorno y navegacion basica.",
        role=Role.ENGINEER,
        complexity=Complexity.MEDIUM,
        criticality=Criticality.MEDIUM,
        dependencies=["T-001"],
        metadata={
            "required_capabilities": ["coding"],
            "execution_plan": [
                {"type": "cmd", "command": "python --version", "timeout": 30},
                {
                    "type": "powershell",
                    "command": "Write-Output 'PowerShell OK from AI Team'",
                    "timeout": 30,
                },
                {"type": "browser_fetch", "url": "https://example.com", "timeout": 20},
                {
                    "type": "browser_script",
                    "url": "https://example.com",
                    "timeout": 30,
                    "actions": [{"type": "wait_for_selector", "selector": "body"}],
                },
            ],
        },
    )

    for task in [task1, task2, task3, task4]:
        existing = orchestrator.taskboard.get_task(task.task_id)
        if not existing:
            orchestrator.submit_task(task)

    orchestrator.run_until_idle(max_rounds=8)
    print("Demo run completed.")


def cmd_status(runtime_dir: Path, browser_mode: str, environment: str) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    print("Tasks:")
    for task in orchestrator.taskboard.list_tasks():
        print(
            f"- {task.task_id} [{task.state.value}] role={task.role.value} "
            f"assignee={task.assignee} title={task.title}"
        )
    print("\nMailbox:")
    for msg in orchestrator.mailbox.list_messages():
        print(f"- {msg.timestamp} {msg.sender} -> {msg.recipient}: {msg.subject}")
    print("\nObservability:")
    summary = orchestrator.event_logger.summary()
    pilot_metrics = compute_pilot_metrics(orchestrator.taskboard.list_tasks(), summary)
    print(f"- total_events={summary['total_events']} by_type={summary['event_types']}")
    print(f"- compliance_environment={environment}")
    print(
        "- task_execution="
        f"{summary['task_execution_success']}/{summary['task_execution_total']} "
        f"({summary['task_execution_success_rate']}%)"
    )
    print(
        f"- channels={summary['channels']} providers={summary['providers']} "
        f"quality_gates_opened={summary['quality_gates_opened']} "
        f"compliance_violations={summary['compliance_violations']}"
    )
    print(
        f"- api_share_percent={summary['api_share_percent']} alerts={summary['alerts']}"
    )
    print(
        "- pilot_metrics "
        f"task_success_rate={pilot_metrics['task_success_rate']}% "
        f"gate_pass_rate={pilot_metrics['gate_pass_rate']}% "
        f"pro_share={pilot_metrics['pro_share_percent']}% "
        f"api_fallback={pilot_metrics['api_fallback_rate_percent']}%"
    )
    print(
        "- execution_roots="
        f"{[str(path) for path in orchestrator.execution.executor.allowed_roots]}"
    )

    tool_integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir, project_root=Path.cwd()
    )
    coverage = tool_integrator.skill_coverage(runtime_dir=runtime_dir)
    print(
        "- skills_coverage="
        f"{coverage['coverage_percent']}% "
        f"({coverage['skill_guidance_events']}/{coverage['total_task_execution']})"
    )

    budget = orchestrator.router.budget_manager
    if budget is not None:
        snapshot = budget.snapshot()
        signal = budget.api_signal()
        print(
            "\nFinOps:"
            f" daily=${snapshot['daily_api_spend_usd']}/${snapshot['daily_api_budget_usd']}"
            f" monthly=${snapshot['monthly_api_spend_usd']}/${snapshot['monthly_api_budget_usd']}"
        )
        print(
            "- budget_signal="
            f"can_use_api={signal.can_use_api} reason={signal.reason} "
            f"max_api_tier={signal.max_api_cost_tier} "
            f"suggested_api_attempts={signal.suggested_max_api_attempts}"
        )

    print("\nMemories:")
    for agent in orchestrator.memory.list_agents():
        print(f"- {agent}: {orchestrator.memory.count(agent)} entries")


def cmd_run(
    runtime_dir: Path, rounds: int, browser_mode: str, environment: str
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    orchestrator.run_until_idle(max_rounds=rounds)
    print(f"Run finished. max_rounds={rounds}")


def cmd_meeting(
    runtime_dir: Path, topic: str, browser_mode: str, environment: str
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    orchestrator.communicator.run_sync_meeting(
        topic=topic,
        participants=[
            MeetingParticipant(agent_id="lead-1", role=Role.TEAM_LEAD.value),
            MeetingParticipant(agent_id="research-1", role=Role.RESEARCHER.value),
            MeetingParticipant(agent_id="eng-1", role=Role.ENGINEER.value),
            MeetingParticipant(agent_id="review-1", role=Role.REVIEWER.value),
            MeetingParticipant(agent_id="qa-1", role=Role.QA.value),
        ],
    )
    print(f"Meeting completed: {topic}")


def cmd_memory(runtime_dir: Path, agent: str, limit: int) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode="basic",
        environment="dev",
    )
    entries = orchestrator.memory.recent(agent, limit=limit)
    if not entries:
        print(f"No memory entries for agent={agent}")
        return
    for entry in entries:
        print(f"- {entry.ts} [{entry.kind}] task={entry.task_id} {entry.content[:200]}")


def cmd_exec(
    runtime_dir: Path, shell: str, command: str, browser_mode: str, environment: str
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    if shell == "cmd":
        result = orchestrator.execution.executor.run_cmd(command=command)
    else:
        result = orchestrator.execution.executor.run_powershell(command=command)
    print(f"success={result.success} exit={result.exit_code} reason={result.reason}")
    if result.stdout.strip():
        print("STDOUT:")
        print(result.stdout.strip())
    if result.stderr.strip():
        print("STDERR:")
        print(result.stderr.strip())


def cmd_adapters(runtime_dir: Path, create_template: bool) -> None:
    path = runtime_dir / "adapters.json"
    if create_template:
        build_external_adapter_template(path)
        print(f"Adapter template written: {path}")
        return

    adapters = load_external_adapters(path)
    if not adapters:
        print(f"No external adapters loaded from: {path}")
        return
    print(f"Loaded {len(adapters)} external adapters:")
    for adapter in adapters:
        print(
            f"- {adapter.name} provider={adapter.provider} model={adapter.model} "
            f"channel={adapter.channel.value} roles={sorted(adapter.role_targets)} "
            f"priority={adapter.routing_priority} requires_approval={adapter.requires_approval}"
        )


def cmd_pilot_check(
    runtime_dir: Path,
    browser_mode: str,
    environment: str,
    min_task_success_rate: float,
    min_gate_pass_rate: float,
    min_pro_share: float,
    max_compliance_violations: int,
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    summary = orchestrator.event_logger.summary()
    metrics = compute_pilot_metrics(orchestrator.taskboard.list_tasks(), summary)
    result = evaluate_pilot(
        metrics,
        PilotThresholds(
            min_task_success_rate=min_task_success_rate,
            min_gate_pass_rate=min_gate_pass_rate,
            min_pro_share_percent=min_pro_share,
            max_compliance_violations=max_compliance_violations,
        ),
    )
    print("Pilot check:")
    print(
        f"- task_success_rate={metrics['task_success_rate']}% "
        f"gate_pass_rate={metrics['gate_pass_rate']}% "
        f"pro_share={metrics['pro_share_percent']}% "
        f"api_fallback={metrics['api_fallback_rate_percent']}% "
        f"compliance_violations={metrics['compliance_violations']}"
    )
    for message in result.messages:
        print(f"- {message}")
    if not result.ok:
        raise SystemExit(1)


def cmd_catalog_tools(catalog_root: Path, limit: int) -> None:
    root = catalog_root.resolve()
    if not root.exists() or not root.is_dir():
        print(f"Catalog root not found: {root}")
        return

    projects = [path for path in root.iterdir() if path.is_dir()]
    projects.sort(key=lambda item: item.name.lower())

    print(f"Catalog root: {root}")
    print(f"Projects detected: {len(projects)}")
    for project in projects[: max(1, limit)]:
        markers: list[str] = []
        if (project / "package.json").exists():
            markers.append("node")
        if (project / "requirements.txt").exists() or (
            project / "pyproject.toml"
        ).exists():
            markers.append("python")
        if (project / "README.md").exists() or (project / "README.txt").exists():
            markers.append("readme")
        if list(project.glob("*.exe")):
            markers.append("exe")
        marker_text = ",".join(markers) if markers else "unknown"
        print(f"- {project.name} [{marker_text}]")


def cmd_inventory_tools(catalog_root: Path, output_path: Path, limit: int) -> None:
    payload = write_inventory(root=catalog_root, output_path=output_path, limit=limit)
    print(f"Tool inventory written: {output_path}")
    print(f"- root={payload['root']}")
    print(f"- total_tools={payload['total']}")
    for item in payload["tools"][:10]:
        print(
            f"- {item['project_name']} -> adapter={item['adapter_name']} "
            f"enabled={item['enabled']} requires_approval={item['requires_approval']}"
        )


def cmd_tool_catalog(catalog_path: Path, limit: int) -> None:
    if not catalog_path.exists():
        print(f"Tool catalog not found: {catalog_path}")
        return
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Tool catalog invalid JSON: {exc}")
        return

    items = payload.get("tools", [])
    if not isinstance(items, list):
        print("Tool catalog has no tools list")
        return
    print(f"Tool catalog: {catalog_path}")
    print(f"Tools: {len(items)}")
    for item in items[: max(1, limit)]:
        if not isinstance(item, dict):
            continue
        print(
            "- "
            f"{item.get('name')} category={item.get('category')} source={item.get('source_type')} "
            f"requires_approval={item.get('requires_approval', False)}"
        )


def cmd_tool_sync(
    runtime_dir: Path,
    environment: str,
    request_path: Path,
    catalog_path: Path,
    strict: bool,
    allow_internet: bool,
) -> None:
    if not request_path.exists():
        print(f"Tool request file not found: {request_path}")
        return
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Tool request JSON invalid: {exc}")
        return

    requirements = payload.get("tool_requirements", [])
    if not isinstance(requirements, list) or not requirements:
        print("No tool_requirements found")
        return

    metadata = {
        "tool_requirements": requirements,
        "auto_integrate_tools": True,
    }
    internet_allowed = allow_internet or environment != "prod"
    integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir,
        project_root=Path.cwd(),
        catalog_path=catalog_path,
    )
    report = integrator.integrate_from_metadata(
        task_id="manual_tool_sync",
        metadata=metadata,
        internet_allowed=internet_allowed,
    )
    print(
        "Tool sync: "
        f"success={report.success} adapters={len(report.integrated_adapters)} "
        f"mcp={len(report.integrated_mcp_servers)} skills={len(report.integrated_skills)}"
    )
    if report.messages:
        for item in report.messages[:20]:
            print(f"- {item}")
    if report.errors:
        for item in report.errors[:20]:
            print(f"- ERROR {item}")
    if strict and not report.success:
        raise SystemExit(1)


def cmd_skills_library(runtime_dir: Path, show_content: bool) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    entries = integrator.skill_library_entries()
    if not entries:
        print("No skills found in library")
        return
    print(f"Skills library entries: {len(entries)}")
    for entry in entries:
        name = str(entry.get("name", "")).strip().lower()
        roles = entry.get("roles", [])
        capabilities = entry.get("capabilities", [])
        print(f"- {name} roles={roles} capabilities={capabilities}")
        if show_content:
            print(f"  purpose={entry.get('purpose', entry.get('description', ''))}")


def cmd_skills_sync(runtime_dir: Path, force: bool, targets: str) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    created = integrator.sync_skill_library(
        force=force,
        targets=_parse_skill_targets(targets),
    )
    print(f"Skills synced: {len(created)}")
    for item in created:
        print(f"- {item}")


def _parse_skill_targets(raw: str) -> set[str]:
    parts = {item.strip().lower() for item in str(raw).split(",") if item.strip()}
    normalized = {item for item in parts if item in {"cloud", "agents", "claude"}}
    if not normalized:
        return {"cloud", "agents", "claude"}
    return normalized


def cmd_skills_pull(
    runtime_dir: Path,
    batch: str,
    force: bool,
    max_items: int,
    targets: str,
) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    report = integrator.pull_skill_sources(
        batch=batch,
        force=force,
        max_items=max(0, int(max_items)),
        targets=_parse_skill_targets(targets),
    )
    print(
        "Skills pull: "
        f"pulled={report['pulled']} changed={report['changed']} "
        f"synced={report['synced']} skipped={report['skipped']}"
    )
    print(f"- targets={report['targets']}")
    for item in report.get("changed_skills", [])[:30]:
        print(f"- changed {item}")
    for item in report.get("warnings", [])[:20]:
        print(f"- WARN {item}")
    for item in report.get("errors", [])[:20]:
        print(f"- ERROR {item}")


def cmd_skills_export(runtime_dir: Path, force: bool, targets: str) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    synced = integrator.sync_skill_library(
        force=force,
        targets=_parse_skill_targets(targets),
    )
    print(f"Skills exported: {len(synced)}")
    for item in synced:
        print(f"- {item}")


def cmd_skills_doctor(runtime_dir: Path) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    status = integrator.skills_status()
    print("Skills doctor:")
    print(
        f"- library={status['library_skills']} registry={status['registry_skills']} "
        f"cloud={status['cloud_skills']} agents={status['agents_skills']} "
        f"claude={status['claude_skills']}"
    )


def cmd_mcp_status(runtime_dir: Path) -> None:
    path = runtime_dir / "mcp_servers.json"
    if not path.exists():
        print(f"MCP registry not found: {path}")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid MCP registry JSON: {exc}")
        return
    servers = payload.get("servers", [])
    if not isinstance(servers, list) or not servers:
        print("No MCP servers registered")
        return
    enabled = 0
    print(f"MCP servers: {len(servers)}")
    for server in servers:
        if not isinstance(server, dict):
            continue
        is_enabled = bool(server.get("enabled", False))
        if is_enabled:
            enabled += 1
        health = server.get("health_status", "unknown")
        reason = server.get("health_reason", "")
        print(
            f"- {server.get('name')} enabled={is_enabled} transport={server.get('transport')} "
            f"approval={server.get('requires_approval', True)} health={health} reason={reason}"
        )
    print(f"Enabled MCP servers: {enabled}")


def cmd_mcp_doctor(
    runtime_dir: Path,
    timeout: int,
    enable_healthy: bool,
    enable_sensitive: bool,
) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    report = integrator.mcp_doctor(
        timeout=timeout,
        enable_healthy=enable_healthy,
        enable_sensitive=enable_sensitive,
    )
    print(
        f"MCP doctor checked={report['total']} healthy={report['healthy']} "
        f"enabled={report['enabled']} auto_enabled={report['auto_enabled']} "
        f"skipped_sensitive={report['skipped_sensitive']}"
    )
    for item in report["reports"]:
        print(
            f"- {item['name']} status={item['status']} enabled={item['enabled']} "
            f"reason={item['reason']}"
        )


def cmd_provider_status(runtime_dir: Path, environment: str) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir=runtime_dir,
        browser_mode="basic",
        environment=environment,
    )
    print("Provider status:")
    for adapter in orchestrator.router.adapters:
        if adapter.channel.value not in {"subscription", "api"}:
            continue
        print(
            f"- {adapter.name} provider={adapter.provider} model={adapter.model} "
            f"channel={adapter.channel.value} available={adapter.available()}"
        )

    key_checks = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
    ]
    print("API keys:")
    for key in key_checks:
        print(f"- {key}={'set' if bool(os.getenv(key)) else 'missing'}")


def cmd_provider_doctor(runtime_dir: Path, strict: bool) -> None:
    specs = _provider_connection_specs()
    rows, required_healthy, required_total = _collect_provider_health(specs)
    local_ok, local_runtime = _detect_local_coding_runtime()
    report = {
        "providers": rows,
        "api_keys": {},
        "local_runtime": local_runtime,
        "healthy": required_healthy >= required_total,
    }

    print("Provider doctor:")
    for row in rows:
        print(
            f"- {row['name']} provider={row['provider']} healthy={row['healthy']} details={row['details']}"
        )
    print(
        f"- local_runtime provider={local_runtime['provider']} healthy={local_ok} details={local_runtime['details']}"
    )

    for key in [
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "NOTEBOOKLM_API_KEY",
        "GOOGLE_NOTEBOOKLM_API_KEY",
        "NOTEBOOKLM_INGEST_ENDPOINT",
        "NOTEBOOKLM_INGEST_COMMAND",
    ]:
        state = "set" if bool(os.getenv(key)) else "missing"
        report["api_keys"][key] = state
        print(f"- {key}={state}")

    path = runtime_dir / "provider_doctor.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"- report={path}")

    if strict and not report["healthy"]:
        raise SystemExit(1)


def _provider_smoke_probe(
    name: str, command: list[str], env: dict[str, str] | None = None
) -> tuple[bool, str]:
    prompt = "Reply with exactly: OK"
    resolved = [item.replace("{prompt}", prompt) for item in command]
    if resolved and resolved[0].lower() != "cmd":
        binary = _resolve_executable(resolved[0])
        if binary is None:
            return False, "smoke_command_not_found"
        resolved[0] = binary
    try:
        proc = subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"smoke_error:{exc}"
    blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().lower()
    if proc.returncode == 0 and "ok" in blob:
        return True, "smoke_ok"
    return False, f"smoke_failed:{blob[:160]}"


def cmd_provider_smoke(runtime_dir: Path, strict: bool) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    specs = _provider_connection_specs()
    rows = []
    dotenv_loaded = False
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(".env"))
        dotenv_loaded = True
    except Exception:
        dotenv_loaded = False
    for spec in specs:
        command = _resolve_provider_command(spec)
        if not command:
            rows.append(
                {"name": spec["name"], "healthy": False, "details": "command_missing"}
            )
            continue
        env = os.environ.copy()
        if (
            spec["provider"] == "google"
            and env.get("GOOGLE_API_KEY")
            and not env.get("GEMINI_API_KEY")
        ):
            env["GEMINI_API_KEY"] = env["GOOGLE_API_KEY"]
        healthy, details = _provider_smoke_probe(spec["name"], command, env=env)
        rows.append(
            {
                "name": spec["name"],
                "healthy": healthy,
                "details": details,
                "command": command,
            }
        )
    local_ok, local_runtime = _detect_local_coding_runtime()
    if local_ok and isinstance(local_runtime.get("command"), list):
        healthy, details = _provider_smoke_probe(
            "ollama_local", list(local_runtime["command"])
        )
        rows.append(
            {
                "name": "ollama_qwen_coder_local",
                "healthy": healthy,
                "details": details,
                "command": local_runtime["command"],
            }
        )
    report = {"dotenv_loaded": dotenv_loaded, "smoke": rows}
    print("Provider smoke:")
    for row in rows:
        print(f"- {row['name']} healthy={row['healthy']} details={row['details']}")
    path = runtime_dir / "provider_smoke.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")
    print(f"- report={path}")
    if strict and any(not row["healthy"] for row in rows):
        raise SystemExit(1)


def cmd_provider_ops(runtime_dir: Path) -> None:
    payload = build_provider_ops_view(runtime_dir)
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    print("Provider ops:")
    print(
        f"- operational={summary.get('operational_count', 0)} degraded={summary.get('degraded_count', 0)}"
    )
    print(f"- team_lead_candidates={summary.get('team_lead_candidates', [])}")
    print(f"- alerts={payload.get('alerts', [])}")
    for row in payload.get("providers", []):
        print(
            f"- {row['adapter_name']} tier={row['tier']} operational={row['operational']} doctor={row['doctor_details']} smoke={row['smoke_details']}"
        )
    print(f"- report={runtime_dir / 'provider_ops.json'}")


def cmd_provider_connect(runtime_dir: Path, strict: bool) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    adapters_path = runtime_dir / "adapters.json"
    if not adapters_path.exists():
        build_external_adapter_template(adapters_path)

    payload = _load_json_file(adapters_path, default={"external_adapters": []})
    adapters = payload.get("external_adapters", [])
    if not isinstance(adapters, list):
        adapters = []

    specs = _provider_connection_specs()
    missing: list[str] = []
    for spec in specs:
        name = spec["name"]
        command = _resolve_provider_command(spec)
        enabled, details = _provider_runtime_health(spec, command)
        if (
            spec.get("provider") == "google"
            and command
            and command[:3] == ["npx", "-y", "@google/gemini-cli"]
            and os.name == "nt"
            and bool(os.getenv("GOOGLE_API_KEY"))
            and not bool(os.getenv("GEMINI_API_KEY"))
        ):
            command = [
                "cmd",
                "/c",
                "set GEMINI_API_KEY=%GOOGLE_API_KEY% && npx -y @google/gemini-cli {prompt}",
            ]

        if not enabled and spec.get("required", False):
            missing.append(name)

        entry = {
            "type": "external_program",
            "name": name,
            "provider": spec["provider"],
            "model": spec["model"],
            "channel": "subscription",
            "command": command
            or ["python", "-c", "print('provider cli not configured')"],
            "capabilities": spec["capabilities"],
            "role_targets": spec["role_targets"],
            "priority": "primary",
            "routing_priority": spec.get("routing_priority", 20),
            "enabled": enabled,
            "requires_approval": False,
            "timeout_seconds": 180,
            "cost_tier": 0,
            "source": spec.get("source", "cli"),
        }
        adapters = _upsert_adapter(adapters, entry)
        print(
            f"- {name} enabled={enabled} details={details} "
            f"command={command or 'not-found'}"
        )

    local_ok, local_runtime = _detect_local_coding_runtime()
    if local_runtime.get("command"):
        local_entry = {
            "type": "external_program",
            "name": "ollama_qwen_coder_local",
            "provider": "local",
            "model": str(local_runtime.get("model", "qwen2.5-coder:14b")),
            "channel": "subscription",
            "command": local_runtime.get("command", []),
            "capabilities": ["coding", "reasoning", "analysis", "review"],
            "role_targets": ["engineer", "reviewer", "researcher", "qa"],
            "priority": "secondary",
            "routing_priority": 40,
            "enabled": local_ok,
            "requires_approval": False,
            "timeout_seconds": 240,
            "cost_tier": 0,
            "source": "ollama_local",
        }
        adapters = _upsert_adapter(adapters, local_entry)
        print(
            f"- ollama_qwen_coder_local enabled={local_ok} details={local_runtime['details']} command={local_runtime.get('command', [])}"
        )

    payload["external_adapters"] = adapters
    adapters_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )

    accounts_path = runtime_dir / "provider_accounts.json"
    accounts = _default_provider_accounts_template()
    for row in accounts["subscription_accounts"]:
        provider = str(row.get("provider", "")).strip().lower()
        spec = next((item for item in specs if item["provider"] == provider), None)
        if spec is None:
            continue
        command = _resolve_provider_command(spec)
        connected, details = _provider_runtime_health(spec, command)
        row["connected"] = connected
        row["details"] = details
    accounts_path.write_text(
        json.dumps(accounts, indent=2, ensure_ascii=True), encoding="utf-8"
    )

    print(f"Provider connections updated: {adapters_path}")
    if missing:
        print(f"Missing required provider CLIs: {missing}")
        if strict:
            raise SystemExit(1)


def cmd_system_check(
    runtime_dir: Path,
    environment: str,
    browser_mode: str,
    doctor_timeout: int,
    strict: bool,
    min_skills_coverage: float,
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir=runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    summary = orchestrator.event_logger.summary()
    metrics = compute_pilot_metrics(orchestrator.taskboard.list_tasks(), summary)
    tool_integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir, project_root=Path.cwd()
    )
    mcp_report = tool_integrator.mcp_doctor(
        timeout=doctor_timeout, enable_healthy=False
    )
    skill_coverage = tool_integrator.skill_coverage(runtime_dir=runtime_dir)
    provider_rows, required_healthy, required_total = _collect_provider_health()
    required_minimum = _required_provider_health_minimum(environment, required_total)

    subscription_available = 0
    api_available = 0
    for adapter in orchestrator.router.adapters:
        if adapter.channel.value == "subscription" and adapter.available():
            subscription_available += 1
        if adapter.channel.value == "api" and adapter.available():
            api_available += 1

    # Check for cost anomalies
    cost_anomaly_detected = False
    cost_anomaly_reason = "normal"
    if orchestrator.router.budget_manager:
        cost_anomaly_detected, cost_anomaly_reason = (
            orchestrator.router.budget_manager.detect_cost_anomaly()
        )

    from aiteam.tool_lock import ToolLockManager

    lock_manager = ToolLockManager(runtime_dir)
    catalog_tools = tool_integrator._catalog_items()
    drifts = lock_manager.check_drift(catalog_tools)

    checks = [
        (
            subscription_available >= 1,
            f"subscription_available={subscription_available}",
        ),
        (
            mcp_report["healthy"] >= 1 or mcp_report["total"] == 0,
            f"mcp_healthy={mcp_report['healthy']}/{mcp_report['total']}",
        ),
        (
            required_healthy >= required_minimum,
            (
                f"required_provider_health={required_healthy}/{required_total} "
                f"required>={required_minimum}"
            ),
        ),
        (
            skill_coverage["coverage_percent"] >= min_skills_coverage,
            (
                f"skills_coverage={skill_coverage['coverage_percent']}% "
                f"required>={min_skills_coverage}%"
            ),
        ),
        (
            metrics["compliance_violations"] == 0,
            f"compliance_violations={metrics['compliance_violations']}",
        ),
        (not cost_anomaly_detected, f"cost_anomaly={cost_anomaly_reason}"),
        (
            len(drifts) == 0,
            f"tool_drift_detected={len(drifts)} files (run tool-lock to fix)",
        ),
    ]

    print("System check:")
    print(f"- environment={environment}")
    print(
        f"- providers subscription_available={subscription_available} api_available={api_available}"
    )
    print(
        f"- required_provider_health={required_healthy}/{required_total} "
        f"required_minimum={required_minimum}"
    )
    print(
        f"- pilot task_success={metrics['task_success_rate']}% "
        f"gates={metrics['gate_pass_rate']}% pro_share={metrics['pro_share_percent']}%"
    )
    print(
        f"- mcp healthy={mcp_report['healthy']}/{mcp_report['total']} enabled={mcp_report['enabled']}"
    )
    print(
        f"- skills_coverage={skill_coverage['coverage_percent']}% "
        f"({skill_coverage['skill_guidance_events']}/{skill_coverage['total_task_execution']})"
    )
    print(f"- cost_anomaly={cost_anomaly_reason}")
    print(f"- alerts={summary.get('alerts', [])}")

    failed = [message for ok, message in checks if not ok]
    if failed:
        print("- checks_failed:")
        for item in failed:
            print(f"  - {item}")
    else:
        print("- checks_passed")

    report_path = runtime_dir / "system_check.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": environment,
        "providers": {
            "subscription_available": subscription_available,
            "api_available": api_available,
            "required_healthy": required_healthy,
            "required_total": required_total,
            "required_minimum": required_minimum,
            "details": provider_rows,
        },
        "pilot": metrics,
        "mcp": {
            "healthy": mcp_report["healthy"],
            "total": mcp_report["total"],
            "enabled": mcp_report["enabled"],
        },
        "skills_coverage": skill_coverage,
        "finops": {
            "cost_anomaly_detected": cost_anomaly_detected,
            "cost_anomaly_reason": cost_anomaly_reason,
        },
        "alerts": summary.get("alerts", []),
        "failed_checks": failed,
    }
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(f"- report={report_path}")

    if strict and failed:
        raise SystemExit(1)


def cmd_tool_lock(runtime_dir: Path, catalog_path: Path) -> None:
    from aiteam.autotools import AutoToolIntegrator
    from aiteam.tool_lock import ToolLockManager

    integrator = AutoToolIntegrator(
        runtime_dir=runtime_dir,
        project_root=Path.cwd(),
        catalog_path=catalog_path,
    )
    tools = integrator._catalog_items()
    if not tools:
        print("No tools found in catalog to lock")
        return

    manager = ToolLockManager(runtime_dir)
    manager.generate_lockfile(tools)
    print(f"Tool lockfile generated with {len(tools)} tools at {manager.lock_file}")


def cmd_system_prune(runtime_dir: Path, max_days: int) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir=runtime_dir,
        browser_mode="basic",
        environment="dev",
    )
    archive_dir = runtime_dir / "archive"
    removed = orchestrator.event_logger.prune_events(
        max_days=max_days, archive_dir=archive_dir
    )
    print(f"System prune: {removed} events archived to {archive_dir}")


def cmd_snapshot_create(
    runtime_dir: Path,
    label: str,
    max_keep: int,
    include_sensitive: bool,
) -> None:
    manager = SnapshotManager(project_root=Path.cwd())
    entry = manager.create_snapshot(
        label=label,
        max_keep=max_keep,
        include_sensitive=include_sensitive,
    )
    print(
        f"Snapshot created: id={entry['id']} files={entry['file_count']} "
        f"size={entry['size_bytes']} label={entry['label']}"
    )


def cmd_snapshot_list() -> None:
    manager = SnapshotManager(project_root=Path.cwd())
    snapshots = manager.list_snapshots()
    if not snapshots:
        print("No snapshots available")
        return
    print(f"Snapshots: {len(snapshots)}")
    for item in snapshots[:50]:
        print(
            f"- {item.get('id')} created={item.get('created_at')} "
            f"files={item.get('file_count')} label={item.get('label', '')}"
        )


def cmd_snapshot_restore(snapshot_id: str, no_backup: bool, dry_run: bool) -> None:
    manager = SnapshotManager(project_root=Path.cwd())
    if not snapshot_id.strip():
        raise SystemExit("snapshot-restore requires --snapshot-id")

    if not no_backup and not dry_run:
        backup = manager.create_snapshot(
            label=f"auto_backup_before_restore:{snapshot_id}"
        )
        print(f"Backup snapshot created: {backup['id']}")

    result = manager.restore_snapshot(snapshot_id, dry_run=dry_run)
    print(
        f"Snapshot restore: id={result['snapshot_id']} restored_files={result['restored_files']} "
        f"dry_run={result['dry_run']}"
    )


def cmd_skills_coverage(runtime_dir: Path) -> None:
    integrator = AutoToolIntegrator(runtime_dir=runtime_dir, project_root=Path.cwd())
    coverage = integrator.skill_coverage(runtime_dir=runtime_dir)
    print("Skills coverage:")
    print(
        f"- guidance_events={coverage['skill_guidance_events']} "
        f"task_execution={coverage['total_task_execution']} "
        f"coverage_percent={coverage['coverage_percent']}"
    )


def cmd_dashboard(
    runtime_dir: Path,
    browser_mode: str,
    environment: str,
    output_path: Path,
    open_browser: bool,
) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode=browser_mode,
        environment=environment,
    )
    tasks = orchestrator.taskboard.list_tasks()
    summary = orchestrator.event_logger.summary()
    pilot_metrics = compute_pilot_metrics(tasks, summary)
    budget = orchestrator.router.budget_manager
    budget_snapshot = budget.snapshot() if budget is not None else None
    memory_counts = {
        agent: orchestrator.memory.count(agent)
        for agent in orchestrator.memory.list_agents()
    }

    payload = build_dashboard_payload(
        runtime_dir=runtime_dir,
        tasks=tasks,
        summary=summary,
        pilot_metrics=pilot_metrics,
        budget_snapshot=budget_snapshot,
        memory_counts=memory_counts,
        environment=environment,
    )
    html_content = render_dashboard_html(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
    print(f"Dashboard written: {output_path}")
    if open_browser:
        webbrowser.open(output_path.resolve().as_uri())
        print("Dashboard opened in browser")


def cmd_autotune_doctor(runtime_dir: Path, environment: str, window_hours: int) -> None:
    orchestrator = build_default_orchestrator(
        runtime_dir,
        browser_mode="basic",
        environment=environment,
    )
    events = orchestrator.event_logger.recent_events(hours=max(1, window_hours))
    task_events = [
        item
        for item in events
        if str(item.get("event_type", "")) == "task_execution"
        and isinstance(item.get("payload"), dict)
    ]

    if not task_events:
        print("Autotune doctor: no recent task_execution events.")
        print("Suggested env overrides:")
        print("- AITEAM_PARALLEL_AUTOTUNE=1")
        print("- AITEAM_MAX_PARALLEL_TASKS=2")
        print("- AITEAM_MIN_PARALLEL_TASKS=1")
        print("- AITEAM_PARALLEL_TARGET_LATENCY_MS=1200")
        print("- AITEAM_PARALLEL_MAX_FAILURE_RATE=25")
        return

    env = environment.strip().lower()
    max_key = f"AITEAM_MAX_PARALLEL_TASKS_{env.upper()}"
    configured_max = (
        os.getenv(max_key, "").strip()
        or os.getenv("AITEAM_MAX_PARALLEL_TASKS", "1").strip()
    )
    try:
        current_max = max(1, int(configured_max))
    except ValueError:
        current_max = 1

    latencies: list[int] = []
    failures = 0
    by_agent: dict[str, list[int]] = {}
    for event in task_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if not bool(payload.get("success", False)):
            failures += 1
        latency = int(payload.get("latency_ms", 0) or 0)
        if latency > 0:
            latencies.append(latency)
            assignee = str(payload.get("assignee", "")).strip()
            if assignee:
                by_agent.setdefault(assignee, []).append(latency)

    success_rate = 100.0 - ((failures / len(task_events)) * 100.0)
    p95_global = _percentile_int(latencies, 95)
    p50_global = _percentile_int(latencies, 50)

    if env == "prod":
        hard_cap = 2
        success_floor = 97.0
        latency_ceiling = 900
        default_failure_rate = 15
    elif env == "stage":
        hard_cap = 4
        success_floor = 94.0
        latency_ceiling = 1200
        default_failure_rate = 25
    else:
        hard_cap = 6
        success_floor = 90.0
        latency_ceiling = 1400
        default_failure_rate = 30

    if success_rate < success_floor or p95_global > latency_ceiling:
        recommended_max = max(1, current_max - 1)
    elif success_rate > 98.0 and p95_global < (latency_ceiling * 0.6):
        recommended_max = min(hard_cap, current_max + 1)
    else:
        recommended_max = min(hard_cap, current_max)

    recommended_target_latency = max(
        400, int(p95_global * 1.2) if p95_global > 0 else latency_ceiling
    )
    recommended_failure_rate = (
        default_failure_rate
        if success_rate >= success_floor
        else max(10, default_failure_rate - 5)
    )

    print(f"Autotune doctor window_hours={max(1, window_hours)} env={env}")
    print(f"- task_events={len(task_events)} success_rate={success_rate:.2f}%")
    print(f"- latency_p50_ms={p50_global} latency_p95_ms={p95_global}")
    if by_agent:
        print("- agent_p95_ms:")
        for agent in sorted(by_agent.keys()):
            print(f"  - {agent}: {_percentile_int(by_agent[agent], 95)}")

    print("Suggested env overrides:")
    print("- AITEAM_PARALLEL_AUTOTUNE=1")
    print(f"- {max_key}={recommended_max}")
    print("- AITEAM_MIN_PARALLEL_TASKS=1")
    print(f"- AITEAM_PARALLEL_TARGET_LATENCY_MS={recommended_target_latency}")
    print(f"- AITEAM_PARALLEL_MAX_FAILURE_RATE={recommended_failure_rate}")


def _percentile_int(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    sorted_values = sorted(values)
    position = max(
        0,
        min(len(sorted_values) - 1, (len(sorted_values) * percentile + 99) // 100 - 1),
    )
    return int(sorted_values[position])


def cmd_contract_first(
    runtime_dir: Path, epic_id: str, title: str, description: str
) -> None:
    orchestrator = build_default_orchestrator(runtime_dir, environment="dev")
    lead = WorkTask(
        task_id=f"{epic_id}::lead",
        title=f"Plan {title}",
        description=description,
        role=Role.TEAM_LEAD,
    )
    researcher = WorkTask(
        task_id=f"{epic_id}::research",
        title=f"Research {title}",
        description="Explorar repositorio y preparar contrato tecnico.",
        role=Role.RESEARCHER,
        dependencies=[lead.task_id],
    )
    engineer = WorkTask(
        task_id=f"{epic_id}::implement",
        title=f"Implement {title}",
        description="Implementar contrato acordado por Team Lead y Researcher.",
        role=Role.ENGINEER,
        dependencies=[researcher.task_id],
        metadata={"required_capabilities": ["coding"]},
    )
    for task in (lead, researcher, engineer):
        if not orchestrator.taskboard.get_task(task.task_id):
            orchestrator.submit_task(task)

    orchestrator.mailbox.send(
        sender="team_lead",
        recipient="broadcast",
        subject=f"Contract-first spawned: {epic_id}",
        body=(
            "Fases creadas: lead -> research -> implement. "
            "Las gates de review/qa se abriran automaticamente en implementacion."
        ),
    )
    print(f"Contract-first pipeline creado para {epic_id}")


def cmd_learning(
    runtime_dir: Path,
    action: str = "list",
    title: str = "",
    category: str = "",
    description: str = "",
    tags: str = "",
    priority: str = "medium",
    format: str = "text",
    status: str = "",
    tag: str = "",
    project: str = "",
) -> None:
    """Handle learning registry commands."""

    registry = LearningRegistry(runtime_dir)
    normalized_tags = [item.strip() for item in tags.split(",") if item.strip()]

    if action == "record-failure":
        if not title:
            print("Error: --learning-title is required for record-failure")
            return
        registry.record_project_failure(
            title=title,
            error_message=description or "",
            what_happened="",
            why_it_happened="",
            impact="",
            how_to_prevent="",
            tags=normalized_tags,
            project_id=project or None,
        )
        print(f"OK Project failure recorded: {title}")

    elif action == "record-insight":
        if not title:
            print("Error: --learning-title is required for record-insight")
            return
        registry.record_system_insight(
            title=title,
            observation=description or "",
            implication="",
            suggested_action="",
            tags=normalized_tags,
        )
        print(f"OK System insight recorded: {title}")

    elif action == "record-team":
        if not title:
            print("Error: --learning-title is required for record-team")
            return
        registry.record_team_learning(
            title=title,
            what_we_learned=description or "",
            how_we_discovered_it="",
            how_to_apply="",
            tags=normalized_tags,
        )
        print(f"OK Team learning recorded: {title}")

    elif action == "record-feedback":
        if not title:
            print("Error: --learning-title is required for record-feedback")
            return
        registry.record_user_feedback(
            title=title,
            feedback=description or "",
            context="",
            opportunity="",
            from_user="cli",
        )
        print(f"OK User feedback recorded: {title}")

    elif action == "mark-addressed":
        if not title:
            print("Error: --learning-title is required for mark-addressed")
            return
        registry.mark_addressed(title)
        print(f"OK Learning marked as addressed: {title}")

    elif action == "list":
        learnings = registry.read_all()
        if category:
            category_lookup = {
                "project_failure": "project_failure",
                "failure": "project_failure",
                "system_insight": "system_insight",
                "insight": "system_insight",
                "team_learning": "team_learning",
                "team": "team_learning",
                "user_feedback": "user_feedback",
                "feedback": "user_feedback",
            }
            normalized_category = category_lookup.get(
                category.strip().lower(), category.strip().lower()
            )
            learnings = [
                item
                for item in learnings
                if item.get("category") == normalized_category
            ]
        if status:
            learnings = [
                item for item in learnings if item.get("status") == status.lower()
            ]
        if tag:
            learnings = [item for item in learnings if tag in item.get("tags", [])]
        if project:
            learnings = [
                item for item in learnings if item.get("project_id") == project
            ]
        if not learnings:
            print("No learnings recorded yet.")
            return

        print(f"\nLearning Registry ({len(learnings)} items)\n")
        for idx, learning in enumerate(learnings, 1):
            cat = learning.get("category", "UNKNOWN")
            title = learning.get("title", "Untitled")
            status = learning.get("status", "unknown")
            print(f"{idx}. [{cat}] {title} ({status})")

    elif action == "summary":
        summary = registry.summary()
        print("\nLearning Registry Summary")
        print(f"  Total Learnings: {summary['total_records']}")
        print(f"  Open Items: {summary['open_count']}")
        print(f"  Addressed: {summary['addressed_count']}")
        print(f"  Archived: {summary['by_status'].get('archived', 0)}")

    elif action == "export":
        if format == "json":
            learnings = registry.read_all()
            export_data = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "total": len(learnings),
                "learnings": learnings,
            }
            print(json.dumps(export_data, indent=2, default=str))
        elif format == "markdown":
            print(registry.export_markdown())
        else:  # text
            # Use the ingest_learnings.py logic
            all_learnings = registry.read_all()
            if not all_learnings:
                print("No learnings recorded yet.")
                return

            print(f"# Learning Registry Export\n")
            summary = registry.summary()
            print(f"- **Total Learnings**: {summary['total_records']}")
            print(f"- **Open Items**: {summary['open_count']}")
            print(f"- **Addressed**: {summary['addressed_count']}\n")


def main() -> None:
    _load_dotenv_if_present(Path(".env"))

    # Load and validate schemas if validation not disabled
    if os.getenv("AITEAM_SKIP_CONFIG_VALIDATION", "0") not in {"1", "true"}:
        from aiteam.config_schema import validate_config

        config_dir = Path("config")
        checks = [
            (config_dir / "routing_policy.example.json", "routing_policy"),
            (config_dir / "tool_sources.catalog.json", "tool_catalog"),
            (config_dir / "skills.library.json", "skills_library"),
        ]
        errors = []
        for p, t in checks:
            ok, err = validate_config(p, t)
            if not ok:
                errors.append(f"{p}: {err}")
        if errors:
            print("Configuration Validation Errors:")
            for e in errors:
                print(f" - {e}")
            raise SystemExit(
                "\nPlease fix configuration errors before starting AI Team."
            )

    parser = argparse.ArgumentParser(description="AI Team Hybrid Orchestrator")
    parser.add_argument(
        "command",
        choices=[
            "init",
            "plan",
            "demo",
            "status",
            "pilot-check",
            "catalog-tools",
            "inventory-tools",
            "tool-catalog",
            "tool-sync",
            "tool-lock",
            "skills-library",
            "skills-sync",
            "skills-pull",
            "skills-export",
            "skills-doctor",
            "skills-coverage",
            "mcp-status",
            "mcp-doctor",
            "provider-status",
            "provider-connect",
            "provider-doctor",
            "provider-smoke",
            "provider-ops",
            "autotune-doctor",
            "system-check",
            "system-prune",
            "snapshot-create",
            "snapshot-list",
            "snapshot-restore",
            "dashboard",
            "run",
            "meeting",
            "memory",
            "exec",
            "adapters",
            "contract-first",
            "learning",
            "notebooklm-connect",
            "notebooklm-sync",
        ],
    )
    parser.add_argument(
        "learning_subcommand",
        nargs="?",
        choices=[
            "record-failure",
            "record-insight",
            "record-team",
            "record-feedback",
            "list",
            "summary",
            "export",
            "mark-addressed",
        ],
        help="Optional learning subcommand (when command=learning)",
    )
    parser.add_argument(
        "--runtime-dir", default="runtime", help="Runtime storage directory"
    )
    parser.add_argument(
        "--epic-id", default="EPIC-001", help="Epic ID for contract-first"
    )
    parser.add_argument("--title", default="Nuevo flujo AI Team", help="Epic title")
    parser.add_argument(
        "--description",
        default="Plan e implementacion contract-first para el AI Team.",
        help="Epic description",
    )
    parser.add_argument("--rounds", type=int, default=10, help="Rounds for run command")
    parser.add_argument("--topic", default="Weekly Sync", help="Meeting topic")
    parser.add_argument("--agent", default="lead-1", help="Agent ID for memory command")
    parser.add_argument("--limit", type=int, default=10, help="Memory entries to print")
    parser.add_argument("--shell", choices=["cmd", "powershell"], default="cmd")
    parser.add_argument(
        "--command-text", default="python --version", help="Command for exec"
    )
    parser.add_argument("--min-task-success-rate", type=float, default=85.0)
    parser.add_argument("--min-gate-pass-rate", type=float, default=85.0)
    parser.add_argument("--min-pro-share", type=float, default=60.0)
    parser.add_argument("--max-compliance-violations", type=int, default=0)
    parser.add_argument(
        "--catalog-root",
        default=os.getenv("AITEAM_SHARED_TOOLS_ROOT", str(Path.cwd().parent)),
        help="Root directory to catalog external tools",
    )
    parser.add_argument(
        "--catalog-limit", type=int, default=30, help="Max projects in catalog"
    )
    parser.add_argument(
        "--inventory-output",
        default="runtime/tool_inventory.json",
        help="Output JSON path for inventory-tools",
    )
    parser.add_argument(
        "--tool-request-file",
        default="runtime/tool_requests.json",
        help="Input JSON with tool_requirements for tool-sync",
    )
    parser.add_argument(
        "--tool-catalog-file",
        default="config/tool_sources.catalog.json",
        help="Tool catalog JSON path",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail command when tool-sync reports errors",
    )
    parser.add_argument(
        "--allow-internet",
        action="store_true",
        help="Allow internet tool acquisition even in prod for tool-sync",
    )
    parser.add_argument(
        "--show-content",
        action="store_true",
        help="Show extended description output for catalog/library commands",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration for commands that sync artifacts",
    )
    parser.add_argument(
        "--skills-batch",
        default="",
        help="Comma-separated source batches for skills-pull",
    )
    parser.add_argument(
        "--skills-max-items",
        type=int,
        default=0,
        help="Optional max number of skills to pull in this run (0 = no limit)",
    )
    parser.add_argument(
        "--skills-targets",
        default="cloud,agents,claude",
        help="Comma-separated export targets: cloud,agents,claude",
    )
    parser.add_argument(
        "--doctor-timeout",
        type=int,
        default=20,
        help="Timeout seconds for mcp-doctor probes",
    )
    parser.add_argument(
        "--enable-healthy",
        action="store_true",
        help="Enable MCP servers that pass mcp-doctor checks",
    )
    parser.add_argument(
        "--enable-sensitive",
        action="store_true",
        help="Allow mcp-doctor to auto-enable sensitive MCP servers",
    )
    parser.add_argument(
        "--min-skills-coverage",
        type=float,
        default=20.0,
        help="Minimum required skill coverage percent for system-check",
    )
    parser.add_argument(
        "--prune-max-days",
        type=int,
        default=30,
        help="Max age of events to keep in log (for system-prune)",
    )
    parser.add_argument(
        "--autotune-window-hours",
        type=int,
        default=6,
        help="Hours of recent events for autotune-doctor",
    )
    parser.add_argument(
        "--snapshot-id",
        default="",
        help="Snapshot ID for snapshot-restore",
    )
    parser.add_argument(
        "--snapshot-label",
        default="",
        help="Optional label for snapshot-create",
    )
    parser.add_argument(
        "--snapshot-max-keep",
        type=int,
        default=30,
        help="Maximum number of snapshots to keep",
    )
    parser.add_argument(
        "--snapshot-include-sensitive",
        action="store_true",
        help="Include sensitive files like .env in snapshot-create",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not auto-create backup before snapshot restore",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview snapshot restore without writing files",
    )
    parser.add_argument(
        "--dashboard-output",
        default="runtime/dashboard.html",
        help="Output HTML path for dashboard command",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open dashboard output in browser",
    )
    parser.add_argument(
        "--browser-mode",
        choices=["basic", "playwright"],
        default=os.getenv("AITEAM_BROWSER_MODE", "basic"),
        help="Browser execution backend",
    )
    parser.add_argument(
        "--environment",
        choices=["dev", "stage", "prod"],
        default=os.getenv("AITEAM_ENV", "dev"),
        help="Compliance environment profile",
    )
    parser.add_argument(
        "--create-template",
        action="store_true",
        help="Create template file for adapters command",
    )
    parser.add_argument(
        "--learning-action",
        choices=[
            "record-failure",
            "record-insight",
            "record-team",
            "record-feedback",
            "list",
            "summary",
            "export",
            "mark-addressed",
        ],
        default="list",
        help="Learning registry action",
    )
    parser.add_argument(
        "--learning-title",
        default="",
        help="Title for learning record",
    )
    parser.add_argument(
        "--learning-category",
        default="",
        help="Category for learning record",
    )
    parser.add_argument(
        "--category",
        dest="learning_category",
        help="Alias for --learning-category",
    )
    parser.add_argument(
        "--learning-description",
        default="",
        help="Description for learning record",
    )
    parser.add_argument(
        "--learning-tags",
        default="",
        help="Comma-separated tags for learning record",
    )
    parser.add_argument(
        "--learning-priority",
        choices=["low", "medium", "high"],
        default="medium",
        help="Priority for learning record",
    )
    parser.add_argument(
        "--learning-status",
        choices=["open", "actionable", "addressed", "archived"],
        default="",
        help="Filter list action by status",
    )
    parser.add_argument(
        "--status",
        dest="learning_status",
        choices=["open", "actionable", "addressed", "archived"],
        help="Alias for --learning-status",
    )
    parser.add_argument(
        "--learning-tag",
        default="",
        help="Filter list action by exact tag",
    )
    parser.add_argument(
        "--tag",
        dest="learning_tag",
        help="Alias for --learning-tag",
    )
    parser.add_argument(
        "--learning-project",
        default="",
        help="Filter list action by project id",
    )
    parser.add_argument(
        "--project",
        dest="learning_project",
        help="Alias for --learning-project",
    )
    parser.add_argument(
        "--learning-format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Export format for learning records",
    )
    parser.add_argument(
        "--format",
        dest="learning_format",
        choices=["text", "markdown", "json"],
        help="Alias for --learning-format",
    )
    parser.add_argument(
        "--notebooklm-notebook-id",
        default="",
        help="NotebookLM notebook identifier for sync",
    )
    parser.add_argument(
        "--notebooklm-title",
        default="AI Team Sync",
        help="Title used for NotebookLM sync payload",
    )
    parser.add_argument(
        "--notebooklm-source",
        default="aiteam",
        help="Source label for NotebookLM sync payload",
    )
    parser.add_argument(
        "--notebooklm-content-file",
        default="",
        help="Optional path to content file for NotebookLM sync",
    )
    parser.add_argument(
        "--notebooklm-from-prompt",
        default="",
        help="Optional inline content/prompt used by notebooklm-sync",
    )
    parser.add_argument(
        "--notebooklm-format",
        choices=["text", "markdown", "json"],
        default="markdown",
        help="Learning export format when notebooklm-sync builds content",
    )
    parser.add_argument(
        "--notebooklm-days",
        type=int,
        default=7,
        help="Days window when building learning export for notebooklm-sync",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce command output",
    )
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir)

    if args.command == "init":
        cmd_init(runtime_dir)
    elif args.command == "plan":
        cmd_plan()
    elif args.command == "demo":
        cmd_demo(
            runtime_dir, browser_mode=args.browser_mode, environment=args.environment
        )
    elif args.command == "status":
        cmd_status(
            runtime_dir, browser_mode=args.browser_mode, environment=args.environment
        )
    elif args.command == "contract-first":
        cmd_contract_first(
            runtime_dir=runtime_dir,
            epic_id=args.epic_id,
            title=args.title,
            description=args.description,
        )
    elif args.command == "run":
        cmd_run(
            runtime_dir=runtime_dir,
            rounds=args.rounds,
            browser_mode=args.browser_mode,
            environment=args.environment,
        )
    elif args.command == "pilot-check":
        cmd_pilot_check(
            runtime_dir=runtime_dir,
            browser_mode=args.browser_mode,
            environment=args.environment,
            min_task_success_rate=args.min_task_success_rate,
            min_gate_pass_rate=args.min_gate_pass_rate,
            min_pro_share=args.min_pro_share,
            max_compliance_violations=args.max_compliance_violations,
        )
    elif args.command == "catalog-tools":
        cmd_catalog_tools(
            catalog_root=Path(args.catalog_root), limit=args.catalog_limit
        )
    elif args.command == "inventory-tools":
        cmd_inventory_tools(
            catalog_root=Path(args.catalog_root),
            output_path=Path(args.inventory_output),
            limit=args.catalog_limit,
        )
    elif args.command == "tool-catalog":
        cmd_tool_catalog(
            catalog_path=Path(args.tool_catalog_file), limit=args.catalog_limit
        )
    elif args.command == "tool-sync":
        cmd_tool_sync(
            runtime_dir=runtime_dir,
            environment=args.environment,
            request_path=Path(args.tool_request_file),
            catalog_path=Path(args.tool_catalog_file),
            strict=args.strict,
            allow_internet=args.allow_internet,
        )
    elif args.command == "skills-library":
        cmd_skills_library(runtime_dir=runtime_dir, show_content=args.show_content)
    elif args.command == "skills-sync":
        cmd_skills_sync(
            runtime_dir=runtime_dir, force=args.force, targets=args.skills_targets
        )
    elif args.command == "skills-pull":
        cmd_skills_pull(
            runtime_dir=runtime_dir,
            batch=args.skills_batch,
            force=args.force,
            max_items=args.skills_max_items,
            targets=args.skills_targets,
        )
    elif args.command == "skills-export":
        cmd_skills_export(
            runtime_dir=runtime_dir,
            force=args.force,
            targets=args.skills_targets,
        )
    elif args.command == "skills-doctor":
        cmd_skills_doctor(runtime_dir=runtime_dir)
    elif args.command == "skills-coverage":
        cmd_skills_coverage(runtime_dir=runtime_dir)
    elif args.command == "mcp-status":
        cmd_mcp_status(runtime_dir=runtime_dir)
    elif args.command == "mcp-doctor":
        cmd_mcp_doctor(
            runtime_dir=runtime_dir,
            timeout=args.doctor_timeout,
            enable_healthy=args.enable_healthy,
            enable_sensitive=args.enable_sensitive,
        )
    elif args.command == "provider-status":
        cmd_provider_status(runtime_dir=runtime_dir, environment=args.environment)
    elif args.command == "provider-connect":
        cmd_provider_connect(runtime_dir=runtime_dir, strict=args.strict)
    elif args.command == "provider-doctor":
        cmd_provider_doctor(runtime_dir=runtime_dir, strict=args.strict)
    elif args.command == "provider-smoke":
        cmd_provider_smoke(runtime_dir=runtime_dir, strict=args.strict)
    elif args.command == "provider-ops":
        cmd_provider_ops(runtime_dir=runtime_dir)
    elif args.command == "autotune-doctor":
        cmd_autotune_doctor(
            runtime_dir=runtime_dir,
            environment=args.environment,
            window_hours=args.autotune_window_hours,
        )
    elif args.command == "system-check":
        cmd_system_check(
            runtime_dir=runtime_dir,
            environment=args.environment,
            browser_mode=args.browser_mode,
            doctor_timeout=args.doctor_timeout,
            strict=args.strict,
            min_skills_coverage=args.min_skills_coverage,
        )
    elif args.command == "tool-lock":
        cmd_tool_lock(
            runtime_dir=runtime_dir,
            catalog_path=Path(args.tool_catalog_file),
        )
    elif args.command == "system-prune":
        cmd_system_prune(
            runtime_dir=runtime_dir,
            max_days=args.prune_max_days,
        )
    elif args.command == "snapshot-create":
        cmd_snapshot_create(
            runtime_dir=runtime_dir,
            label=args.snapshot_label,
            max_keep=args.snapshot_max_keep,
            include_sensitive=args.snapshot_include_sensitive,
        )
    elif args.command == "snapshot-list":
        cmd_snapshot_list()
    elif args.command == "snapshot-restore":
        cmd_snapshot_restore(
            snapshot_id=args.snapshot_id,
            no_backup=args.no_backup,
            dry_run=args.dry_run,
        )
    elif args.command == "dashboard":
        cmd_dashboard(
            runtime_dir=runtime_dir,
            browser_mode=args.browser_mode,
            environment=args.environment,
            output_path=Path(args.dashboard_output),
            open_browser=args.open_browser,
        )
    elif args.command == "meeting":
        cmd_meeting(
            runtime_dir=runtime_dir,
            topic=args.topic,
            browser_mode=args.browser_mode,
            environment=args.environment,
        )
    elif args.command == "memory":
        cmd_memory(runtime_dir=runtime_dir, agent=args.agent, limit=args.limit)
    elif args.command == "exec":
        cmd_exec(
            runtime_dir=runtime_dir,
            shell=args.shell,
            command=args.command_text,
            browser_mode=args.browser_mode,
            environment=args.environment,
        )
    elif args.command == "learning":
        learning_action = args.learning_subcommand or args.learning_action
        cmd_learning(
            runtime_dir=runtime_dir,
            action=learning_action,
            title=args.learning_title,
            category=args.learning_category or "",
            description=args.learning_description,
            tags=args.learning_tags,
            priority=args.learning_priority,
            format=args.learning_format,
            status=args.learning_status,
            tag=args.learning_tag,
            project=args.learning_project,
        )
    elif args.command == "adapters":
        cmd_adapters(runtime_dir=runtime_dir, create_template=args.create_template)
    elif args.command == "notebooklm-connect":
        cmd_notebooklm_connect(runtime_dir=runtime_dir)
    elif args.command == "notebooklm-sync":
        cmd_notebooklm_sync(
            runtime_dir=runtime_dir,
            notebook_id=args.notebooklm_notebook_id,
            title=args.notebooklm_title,
            source=args.notebooklm_source,
            content_file=args.notebooklm_content_file,
            from_prompt=args.notebooklm_from_prompt,
            export_format=args.notebooklm_format,
            days=args.notebooklm_days,
            dry_run=args.dry_run,
            quiet=args.quiet,
        )


if __name__ == "__main__":
    main()
