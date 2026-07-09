from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.user_config import ROLE_CAPABILITY_PROFILES, load_adapter_profiles, model_options, model_options_for_role, profile_is_connected


PROJECT_CONFIG_NAME = "project_config.json"

# Role tiers for the hiring policy live in aiteam.policies (fase 5) —
# aliases kept here for existing imports.
from aiteam.policies import (  # noqa: E402
    AUTONOMY_MODES,
    JUNIOR_ROLES,
    SENIOR_ROLES,
    TIER3_ROLES,
    cost_policy_enforced as _cost_policy_enforced,
    default_autonomy,
)

# API-only adapters cannot write workspace files — they will immediately block
# when assigned to junior roles that require file writes (engineer, worker).
# reconcile_project_agent_policy upgrades these to subscription_cli when available.
_API_ONLY_ADAPTER_TYPES = {"openai_api", "anthropic_api", "anthropic_sonnet", "gemini_api"}

# Default Tier 3 agents created in every project.
# key = canonical agent id, value = (role, display_name, seniority)
_DEFAULT_TIER3_AGENTS: list[tuple[str, str, str, str]] = [
    ("role:file_scout",       "file_scout",       "File Scout",       "cheap"),
    ("role:web_scout",        "web_scout",        "Web Scout",        "cheap"),
    ("role:context_curator",  "context_curator",  "Context Curator",  "cheap"),
]


def write_project_adapter_policy(runtime_dir: Path, *, profile_ids: list[str]) -> dict[str, Any]:
    profiles = available_project_profiles(profile_ids)
    if not profiles:
        raise ValueError("Select at least one available adapter profile")
    payload = {
        "version": 1,
        "adapter_profile_ids": [str(profile["id"]) for profile in profiles],
        "adapter_policy": {
            "senior_preference": "advanced",
            "junior_preference": "cheap_or_local",
            "source": "project_creation",
        },
    }
    path = Path(runtime_dir) / PROJECT_CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def read_project_adapter_policy(runtime_dir: Path) -> dict[str, Any]:
    path = Path(runtime_dir) / PROJECT_CONFIG_NAME
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "adapter_profile_ids": [], "adapter_policy": {}}
    return parsed if isinstance(parsed, dict) else {"version": 1, "adapter_profile_ids": [], "adapter_policy": {}}


def project_autonomy(runtime_dir: Path) -> str:
    """Autonomy mode for a project: 'supervised' (default) or 'autonomous'.

    Stored in project_config.json under 'autonomy'; falls back to the
    machine-wide AITEAM_AUTONOMY env default when unset/invalid.
    """
    mode = str(read_project_adapter_policy(runtime_dir).get("autonomy") or "").strip().lower()
    return mode if mode in AUTONOMY_MODES else default_autonomy()


def set_project_autonomy(runtime_dir: Path, mode: str) -> dict[str, Any]:
    """Persist the autonomy mode, preserving the rest of project_config.json."""
    mode_key = str(mode or "").strip().lower()
    if mode_key not in AUTONOMY_MODES:
        raise ValueError(f"autonomy must be one of {sorted(AUTONOMY_MODES)}")
    config = read_project_adapter_policy(runtime_dir)
    config["autonomy"] = mode_key
    path = Path(runtime_dir) / PROJECT_CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return config


def available_project_profiles(profile_ids: list[str]) -> list[dict[str, Any]]:
    allowed = {str(item).strip() for item in profile_ids if str(item).strip()}
    if not allowed:
        return []
    profiles = []
    for profile in load_adapter_profiles():
        if str(profile.get("id") or "") not in allowed:
            continue
        if profile.get("status") == "blocked_by_provider":
            continue
        profiles.append(profile)
    return profiles


def project_profiles(runtime_dir: Path) -> list[dict[str, Any]]:
    policy = read_project_adapter_policy(runtime_dir)
    raw_ids = policy.get("adapter_profile_ids") or []
    ids = [str(item) for item in raw_ids] if isinstance(raw_ids, list) else []
    return available_project_profiles(ids)


def choose_adapter_for_role(role: str, seniority: str | None, profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not profiles:
        return None
    role_key = str(role or "").strip().lower()
    seniority_key = str(seniority or "").strip().lower()
    needs_senior = role_key in SENIOR_ROLES or seniority_key in {"lead", "senior"}
    ranked = sorted(
        profiles,
        key=lambda profile: _profile_score(profile, needs_senior=needs_senior),
        reverse=True,
    )
    ranked = _apply_cost_policy(role_key, ranked)
    profile = ranked[0]
    model = _choose_model(str(profile.get("id") or ""), role=role_key, needs_senior=needs_senior)
    config = {"profile_id": profile.get("id")}
    if model:
        config["model"] = model
    return {
        "adapter_type": profile.get("adapter_type"),
        "adapter_config": config,
        "adapter_profile_id": profile.get("id"),
        "model": model,
    }


def _apply_cost_policy(role_key: str, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Opt-in hard enforcement: Tier 3 roles never bill per-token while a
    connected zero-cost channel (local / subscription) exists in the project.

    Reorders *ranked* so connected zero-cost profiles come first when the
    scoring picked a per-token API profile for a Tier 3 role.
    """
    if not ranked or not _cost_policy_enforced() or role_key not in TIER3_ROLES:
        return ranked
    if str(ranked[0].get("channel") or "") != "api":
        return ranked
    zero_cost = [
        p for p in ranked
        if str(p.get("channel") or "") in {"local", "subscription"} and profile_is_connected(p)
    ]
    if not zero_cost:
        return ranked
    remainder = [p for p in ranked if p not in zero_cost]
    return zero_cost + remainder


def apply_adapter_policy_to_member(member: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
    selection = choose_adapter_for_role(
        str(member.get("role") or ""),
        str(member.get("seniority") or ""),
        profiles,
    )
    if not selection:
        return dict(member)
    return {
        **member,
        "adapter_type": selection["adapter_type"],
        "adapter_config": selection["adapter_config"],
        "adapter_profile_id": selection["adapter_profile_id"],
        "model": selection["model"],
    }


def reconcile_project_agent_policy(db_path: Path) -> list[str]:
    """Repair default/simulated agents so a project uses its selected adapters.

    Two upgrade paths:
    1. Default/placeholder adapters (role_builtin, lead_builtin, manual, empty) are
       always replaced with the best adapter for the role.
    2. API-only adapters on junior roles (engineer, worker) are upgraded to
       subscription_cli when a CLI profile is now available in the project's allowlist.
       This handles projects that had only openai_api initially but later added
       codex_subscription — without this, the engineer would stay on the API-only
       adapter and block immediately on every run.
    """

    db_path = Path(db_path)
    profiles = project_profiles(db_path.parent)
    if not profiles:
        return []
    repaired: list[str] = []
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        rows = conn.execute(
            """
            SELECT id, role, seniority, adapter_type, adapter_config_json,
                   capabilities_json, supervisor_agent_id
            FROM agents
            WHERE status IN ('active', 'idle', 'running')
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        lead_id = _lead_agent_id(conn)
        for row in rows:
            role = str(row["role"] or "").strip()
            selection = choose_adapter_for_role(role, str(row["seniority"] or ""), profiles)
            if not selection:
                continue
            sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = []
            current_adapter = str(row["adapter_type"] or "").strip()
            # Determine whether this agent's adapter should be replaced.
            is_placeholder = current_adapter in {"", "manual", "role_builtin", "lead_builtin"}
            is_junior = role.lower() in JUNIOR_ROLES
            selected_adapter = str(selection.get("adapter_type") or "")
            # Upgrade API-only junior agents to CLI when a CLI adapter is now available.
            # This fires when a project adds codex_subscription after initial setup.
            is_api_only_junior = (
                is_junior
                and current_adapter in _API_ONLY_ADAPTER_TYPES
                and selected_adapter == "subscription_cli"
            )
            # A subscription_cli agent whose config lost its profile_id falls
            # back to the runtime's default binary ('claude'), which may not be
            # installed at all — observed live: 89 straight failed runs with
            # "command not found: 'claude'" on the one agent missing profile_id
            # while every sibling carried codex_subscription. Re-select so the
            # config carries the project's actual connected profile again.
            try:
                _current_config = json.loads(str(row["adapter_config_json"] or "{}"))
            except (TypeError, ValueError):
                _current_config = {}
            if not isinstance(_current_config, dict):
                _current_config = {}
            is_cli_missing_profile = (
                current_adapter == "subscription_cli"
                and not str(_current_config.get("profile_id") or "").strip()
                and selected_adapter == "subscription_cli"
            )
            if is_placeholder or is_api_only_junior or is_cli_missing_profile:
                new_config = dict(selection.get("adapter_config") or {})
                # Keep an explicitly chosen model when only the profile was lost.
                if is_cli_missing_profile and str(_current_config.get("model") or "").strip():
                    new_config["model"] = _current_config["model"]
                sets.extend(["adapter_type = ?", "adapter_config_json = ?"])
                params.extend([
                    selected_adapter or current_adapter or "manual",
                    json.dumps(new_config, ensure_ascii=False, sort_keys=True),
                ])
            caps = _decode_list(row["capabilities_json"])
            if not caps:
                sets.append("capabilities_json = ?")
                params.append(json.dumps(default_capabilities_for_role(role), ensure_ascii=False))
            normalized_role = role.lower().replace(" ", "_").replace("-", "_")
            if normalized_role not in {"lead", "team_lead"} and not row["supervisor_agent_id"] and lead_id:
                sets.append("supervisor_agent_id = ?")
                params.append(lead_id)
            if len(sets) == 1:
                continue
            params.append(row["id"])
            conn.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", params)
            repaired.append(str(row["id"]))
        conn.commit()
    # After repairing existing agents, ensure Tier 3 defaults exist.
    # This is idempotent — skips agents that already exist.
    tier3_created = ensure_tier3_agents(db_path, profiles=profiles)
    repaired.extend(tier3_created)
    return repaired


def _profile_score(profile: dict[str, Any], *, needs_senior: bool) -> int:
    profile_id = str(profile.get("id") or "").lower()
    provider = str(profile.get("provider") or "").lower()
    channel = str(profile.get("channel") or "").lower()
    adapter_type = str(profile.get("adapter_type") or "").lower()
    health_status = str((profile.get("health") or {}).get("status") or "").lower()
    score = 0
    if health_status == "ok":
        score += 40
    elif health_status == "installed":
        score += 15
    if needs_senior:
        if any(token in profile_id for token in ("openai", "codex", "anthropic", "gemini")):
            score += 25
        if channel in {"api", "subscription"}:
            score += 15
        if any(token in profile_id for token in ("local", "qwen", "gem4", "gemma")):
            score -= 8
    else:
        if any(token in profile_id for token in ("mini", "flash", "lite", "local", "qwen", "gem4", "gemma")):
            score += 25
        if channel == "local":
            score += 20
        if provider in {"ollama", "lmstudio"}:
            score += 20
        # Engineer/QA roles need to write files — prefer adapters that can execute CLI.
        # API-only adapters (openai_api, anthropic_api, gemini_api, anthropic_sonnet)
        # cannot write workspace files and will immediately block with
        # liveness_reason = "api_only_engineer_no_workspace_changes".
        if adapter_type == "subscription_cli":
            score += 30  # subscription_cli can write files — ideal for engineer
        elif adapter_type in {"openai_api", "anthropic_api", "gemini_api", "anthropic_sonnet"}:
            score -= 30  # API-only immediately blocks engineer (file-write required)
    return score


def _choose_model(profile_id: str, *, role: str = "", needs_senior: bool) -> str | None:
    """Select the best model for a given profile and role.

    Uses role capability profiles when available (prefers the top-scored option
    that matches the role's capability needs).  Falls back to the original
    senior/cheap heuristic for unknown roles.
    """
    if role:
        options = model_options_for_role(profile_id, role)
        if options:
            return str(options[0]["value"])

    # Legacy fallback: senior → best, junior → cheapest option with "mini"/"flash"/"lite"
    options = model_options().get(profile_id, [])
    if not options:
        return None
    if needs_senior:
        return str(options[0]["value"])
    cheap_tokens = ("mini", "flash", "lite", "14b", "4b", "nano")
    for option in reversed(options):
        value = str(option.get("value") or "")
        label = str(option.get("label") or "")
        if any(token in value.lower() or token in label.lower() for token in cheap_tokens):
            return value
    return str(options[-1]["value"])


def ensure_quorum_agents(db_path: Path, *, profiles: list[dict[str, Any]]) -> list[str]:
    """Create quorum auditor agents if they do not already exist.

    Called automatically when a ``lead_quorum`` task is created so the Lead can
    immediately assign sub-issues to ``role:quorum_auditor_1`` and
    ``role:quorum_auditor_2`` without FK failures or hallucinated hires.

    Returns the list of agent IDs that were inserted (empty if all existed).
    """
    quorum_ids = ["role:quorum_auditor_1", "role:quorum_auditor_2"]
    created: list[str] = []
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        lead_id = _lead_agent_id(conn)
        for agent_id in quorum_ids:
            existing = conn.execute(
                "SELECT id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if existing:
                continue
            selection = choose_adapter_for_role("quorum_auditor", "senior", profiles)
            adapter_type = str((selection or {}).get("adapter_type") or "openai_api")
            adapter_config = json.dumps(
                (selection or {}).get("adapter_config") or {}, ensure_ascii=False, sort_keys=True
            )
            name = agent_id.replace("role:", "").replace("_", " ").title()
            conn.execute(
                """
                INSERT OR IGNORE INTO agents (
                    id, role, name, seniority,
                    adapter_type, adapter_config_json, capabilities_json,
                    supervisor_agent_id, budget_monthly_cents,
                    heartbeat_interval_sec, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    "quorum_auditor",
                    name,
                    "senior",
                    adapter_type,
                    adapter_config,
                    json.dumps(
                        default_capabilities_for_role("quorum_auditor"), ensure_ascii=False
                    ),
                    lead_id,
                    0,
                    0,
                    json.dumps({"source": "quorum_bootstrap"}, ensure_ascii=False),
                ),
            )
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0]:
                created.append(agent_id)
        conn.commit()
    return created


def ensure_tier3_agents(db_path: Path, *, profiles: list[dict[str, Any]]) -> list[str]:
    """Create the default Tier 3 agents (file_scout, web_scout, context_curator) if absent.

    These cheap specialist agents are created by default in every project so the
    Lead can immediately delegate file-reading, web-research, and context-compression
    tasks without spending Tier 1 tokens.

    Returns the list of agent IDs that were inserted (empty if all already exist).
    """
    created: list[str] = []
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        lead_id = _lead_agent_id(conn)
        for agent_id, role, name, seniority in _DEFAULT_TIER3_AGENTS:
            existing = conn.execute(
                "SELECT id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if existing:
                continue
            selection = choose_adapter_for_role(role, seniority, profiles)
            # Tier 3 scouts default to role_builtin when no cheap adapter is
            # configured — never openai_api, which would wastefully consume
            # expensive tokens for work designed to run on cheap/local models.
            adapter_type = str((selection or {}).get("adapter_type") or "role_builtin")
            adapter_config = json.dumps(
                (selection or {}).get("adapter_config") or {}, ensure_ascii=False, sort_keys=True
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO agents (
                    id, role, name, seniority,
                    adapter_type, adapter_config_json, capabilities_json,
                    supervisor_agent_id, budget_monthly_cents,
                    heartbeat_interval_sec, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id, role, name, seniority,
                    adapter_type, adapter_config,
                    json.dumps(
                        default_capabilities_for_role(role), ensure_ascii=False
                    ),
                    lead_id,
                    0, 0,
                    json.dumps({"source": "tier3_bootstrap"}, ensure_ascii=False),
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                created.append(agent_id)
        conn.commit()
    return created


def _lead_agent_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM agents
        WHERE id = 'role:lead' OR role IN ('lead', 'team_lead')
        ORDER BY CASE WHEN id = 'role:lead' THEN 0 ELSE 1 END, created_at ASC
        LIMIT 1
        """
    ).fetchone()
    return str(row["id"]) if row else None


def _decode_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    return []
