from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.tools.catalog import default_capabilities_for_role
from aiteam.provider_identity import profile_perspective_key
from aiteam.model_compatibility import compatibility_decision
from aiteam.model_default_rollout import (
    model_default_rollout_mode,
    select_model_default_for_new_slot,
)
from aiteam.compatibility_service import resolve_assignment_compatibility
from aiteam.policies import canonical_role
from aiteam.user_config import (
    ROLE_CAPABILITY_PROFILES,
    load_adapter_profiles,
    model_is_selectable,
    model_options,
    model_options_for_role,
    profile_is_connected,
)


PROJECT_CONFIG_NAME = "project_config.json"
logger = logging.getLogger(__name__)

# Role tiers for the hiring policy live in aiteam.policies (fase 5) —
# aliases kept here for existing imports.
from aiteam.policies import (  # noqa: E402
    AUTONOMY_MODES,
    JUNIOR_ROLES as JUNIOR_ROLES,
    SENIOR_ROLES,
    TIER3_ROLES,
    cost_policy_enforced as _cost_policy_enforced,
    default_autonomy,
)

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


def choose_adapter_for_role(
    role: str,
    seniority: str | None,
    profiles: list[dict[str, Any]],
    *,
    demoted_profile_ids: set[str] | None = None,
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: list[str] | None = None,
    preferred_model: str | None = None,
) -> dict[str, Any] | None:
    if not profiles:
        return None
    executable_profiles = []
    role_key = canonical_role(role)
    role_profile = ROLE_CAPABILITY_PROFILES.get(role_key, {})
    for candidate in profiles:
        supported_roles = candidate.get("supported_roles")
        if (
            isinstance(supported_roles, list)
            and supported_roles
            and role_key not in {str(item).strip().lower() for item in supported_roles}
        ):
            continue
        candidate_options = candidate.get("model_options")
        if not isinstance(candidate_options, list) or not candidate_options:
            # Callers using minimal test/custom profiles have no runtime catalog;
            # preserve their existing explicit configuration.
            executable_profiles.append(candidate)
            continue
        static_by_value = {
            str(item.get("value") or ""): item
            for item in model_options().get(str(candidate.get("id") or ""), [])
        }
        enriched_options = [
            {**static_by_value.get(str(item.get("value") or ""), {}), **item}
            for item in candidate_options if isinstance(item, dict)
        ]
        compatible_options = [
            item for item in enriched_options
            if isinstance(item, dict) and compatibility_decision(
                profile=candidate,
                model=item,
                role=role_key,
                run_profile=run_profile,
                criticality=criticality,
                data_class=data_class,
                required_capabilities=required_capabilities or [],
                role_profile=role_profile,
            ).get("allowed")
            and (
                not str(preferred_model or "").strip()
                or str(item.get("value") or "") == str(preferred_model).strip()
            )
        ]
        if compatible_options:
            executable_profiles.append({**candidate, "model_options": compatible_options})
    if not executable_profiles:
        return None
    profiles = executable_profiles
    seniority_key = str(seniority or "").strip().lower()
    needs_senior = role_key in SENIOR_ROLES or seniority_key in {"lead", "senior"}
    ranked = sorted(
        profiles,
        key=lambda profile: _profile_score(profile, needs_senior=needs_senior),
        reverse=True,
    )
    ranked = _apply_cost_policy(role_key, ranked)
    # Feedback salud→routing: los perfiles de proveedores unhealthy (ventana
    # reciente, hiring_economics.demoted_profile_ids) van al final — sort
    # estable, así que el orden interno de cada grupo se conserva. Nunca se
    # excluye: un canal roto sigue siendo mejor que ningún canal.
    if demoted_profile_ids:
        ranked = sorted(ranked, key=lambda p: str(p.get("id") or "") in demoted_profile_ids)
    profile = ranked[0]
    profile_config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    runtime_options = profile.get("model_options") if isinstance(profile.get("model_options"), list) else []
    selectable_models = None
    if runtime_options:
        selectable_models = {
            str(item.get("value") or "")
            for item in runtime_options
            if isinstance(item, dict) and model_is_selectable(item)
        }
    preferred = str(preferred_model or "").strip()
    model = preferred if preferred and (
        selectable_models is None or preferred in selectable_models
    ) else _choose_model(
        str(profile.get("id") or ""),
        role=role_key,
        needs_senior=needs_senior,
        configured_model=str(profile_config.get("model") or "").strip() or None,
        channel=str(profile.get("channel") or ""),
        selectable_models=selectable_models,
    )
    config = {"profile_id": profile.get("id")}
    if model:
        config["model"] = model
        selected_option = next(
            (
                item for item in model_options().get(str(profile.get("id") or ""), [])
                if str(item.get("value") or "") == model
            ),
            {},
        )
        efforts = selected_option.get("reasoning_effort_by_role")
        if isinstance(efforts, dict) and efforts.get(role_key):
            config["model_reasoning_effort"] = str(efforts[role_key])
    return {
        "adapter_type": profile.get("adapter_type"),
        "adapter_config": config,
        "adapter_profile_id": profile.get("id"),
        "model": model,
    }


def choose_adapter_for_new_slot(
    db_path: Path,
    *,
    role: str,
    seniority: str | None,
    profiles: list[dict[str, Any]],
    selection_scope: str,
    issue_id: str = "",
    **legacy_context: Any,
) -> dict[str, Any] | None:
    """Select a new unpinned slot under the governed M.7 rollout.

    Shadow keeps the established selector. Recommend records the canonical
    decision and also keeps it. Auto applies only a sealed eligible winner; if
    none exists it returns an explicitly unresolved builtin assignment instead
    of inventing an LLM fallback.
    """
    mode = model_default_rollout_mode()
    legacy = lambda: choose_adapter_for_role(  # noqa: E731 - lazy fallback
        role, seniority, profiles, **legacy_context
    )
    if mode == "shadow":
        return legacy()
    try:
        from aiteam.model_selection_context import contextual_model_selection

        projection = contextual_model_selection(
            Path(db_path),
            role=role,
            issue_id=issue_id,
            run_profile=str(legacy_context.get("run_profile") or ""),
            criticality=str(legacy_context.get("criticality") or "medium"),
            data_class=str(legacy_context.get("data_class") or "public"),
            required_capabilities=legacy_context.get("required_capabilities") or (),
            profiles=profiles,
        )
        selected = select_model_default_for_new_slot(
            Path(db_path),
            selection_scope=selection_scope,
            role=role,
            issue_id=issue_id,
            projection=projection,
            rollout=mode,
            profiles=profiles,
        )
    except Exception:
        logger.warning(
            "model default rollout failed for new slot scope=%r role=%r mode=%r",
            selection_scope,
            role,
            mode,
            exc_info=True,
        )
        return _unresolved_model_default("rollout_evaluation_failed") if mode == "auto" else legacy()
    if selected is not None:
        return selected
    return _unresolved_model_default("no_auto_eligible_candidate") if mode == "auto" else legacy()


def _unresolved_model_default(reason: str) -> dict[str, Any]:
    return {
        "adapter_type": "role_builtin",
        "adapter_config": {
            "model_default_rollout": {
                "schema_version": "model_default_rollout_v1",
                "mode": "auto",
                "state": "default_unresolved",
                "reason": reason,
            }
        },
        "adapter_profile_id": None,
        "model": None,
    }


def is_unresolved_model_default(selection: dict[str, Any] | None) -> bool:
    config = (selection or {}).get("adapter_config") or {}
    rollout = config.get("model_default_rollout") if isinstance(config, dict) else None
    return isinstance(rollout, dict) and rollout.get("state") == "default_unresolved"


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
    zero_cost = [p for p in ranked if _profile_has_zero_marginal_cost(p) and profile_is_connected(p)]
    if not zero_cost:
        return ranked
    remainder = [p for p in ranked if p not in zero_cost]
    return zero_cost + remainder


def _profile_has_zero_marginal_cost(profile: dict[str, Any]) -> bool:
    channel = str(profile.get("channel") or "")
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    return channel in {"local", "subscription", "free_gateway"} or bool(config.get("free_tier"))


def apply_adapter_policy_to_member(
    member: dict[str, Any],
    profiles: list[dict[str, Any]],
    *,
    run_profile: str = "",
    criticality: str = "medium",
    data_class: str = "",
    required_capabilities: list[str] | None = None,
) -> dict[str, Any]:
    selection = choose_adapter_for_role(
        str(member.get("role") or ""),
        str(member.get("seniority") or ""),
        profiles,
        run_profile=run_profile,
        criticality=criticality,
        data_class=data_class,
        required_capabilities=required_capabilities or [],
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


def reconcile_project_agent_policy(db_path: Path, *, include_tier3: bool = True) -> list[str]:
    """Repair default/simulated agents so a project uses its selected adapters.

    Two upgrade paths:
    1. Default/placeholder adapters (role_builtin, lead_builtin, manual, empty) are
       always replaced with the best adapter for the role.
    La capacidad de escritura ya no se infiere del tipo API/CLI: la decide el
    gate modelo×rol. Reconcile nunca cambia de canal solo por ser API.
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
            current_adapter = str(row["adapter_type"] or "").strip()
            try:
                _current_config = json.loads(str(row["adapter_config_json"] or "{}"))
            except (TypeError, ValueError):
                _current_config = {}
            if not isinstance(_current_config, dict):
                _current_config = {}
            unresolved_default = (
                isinstance(_current_config.get("model_default_rollout"), dict)
                and _current_config["model_default_rollout"].get("state")
                == "default_unresolved"
            )
            is_adapter_missing_profile = (
                current_adapter not in {"", "manual", "role_builtin", "lead_builtin"}
                and not str(_current_config.get("profile_id") or "").strip()
            )
            selection = choose_adapter_for_role(role, str(row["seniority"] or ""), profiles)
            if not selection and is_adapter_missing_profile:
                # Recovery only needs to restore the lost transport identity and
                # preserves the agent's explicit legacy model below. Do not let
                # a newer Team catalog prevent that metadata repair.
                repair_profiles = [
                    {key: value for key, value in profile.items() if key != "model_options"}
                    for profile in profiles
                ]
                selection = choose_adapter_for_role(role, str(row["seniority"] or ""), repair_profiles)
            if not selection:
                continue
            sets: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list[Any] = []
            # Determine whether this agent's adapter should be replaced.
            is_placeholder = current_adapter in {"", "manual", "role_builtin", "lead_builtin"}
            selected_adapter = str(selection.get("adapter_type") or "")
            # A subscription_cli agent whose config lost its profile_id falls
            # back to the runtime's default binary ('claude'), which may not be
            # installed at all — observed live: 95 straight failed runs with
            # "command not found: 'claude'" on the one agent missing profile_id
            # while every sibling carried codex_subscription. Repair source, in
            # order: the project selection (when it picks a CLI profile), else
            # a sibling agent's working subscription_cli profile — the project
            # allowlist can drift (observed: only openai_api listed while the
            # whole team runs codex), and the siblings are the ground truth of
            # what actually works on this machine.
            _profile_repair_config: dict[str, Any] | None = None
            _profile_repair_adapter = current_adapter
            _policy_repair_config: dict[str, Any] | None = None
            if is_adapter_missing_profile:
                if selected_adapter == current_adapter:
                    _profile_repair_config = dict(selection.get("adapter_config") or {})
                    _policy_repair_config = dict(_profile_repair_config)
                elif current_adapter == "subscription_cli":
                    _sibling_profile = _sibling_cli_profile_id(conn, exclude_agent_id=str(row["id"]))
                    if _sibling_profile:
                        _profile_repair_config = {"profile_id": _sibling_profile}
                        _profile_repair_adapter = "subscription_cli"
                # Keep an explicitly chosen model when only the profile was lost.
                if _profile_repair_config is not None and str(_current_config.get("model") or "").strip():
                    _profile_repair_config["model"] = _current_config["model"]
                if _profile_repair_config is not None:
                    repaired_decision = resolve_assignment_compatibility(
                        adapter_type=_profile_repair_adapter,
                        adapter_config=_profile_repair_config,
                        role=role,
                        profiles=profiles,
                    )
                    if not repaired_decision.get("allowed"):
                        _profile_repair_config = _policy_repair_config
                        if _profile_repair_config is not None:
                            repaired_decision = resolve_assignment_compatibility(
                                adapter_type=_profile_repair_adapter,
                                adapter_config=_profile_repair_config,
                                role=role,
                                profiles=profiles,
                            )
                        if not repaired_decision.get("allowed"):
                            _profile_repair_config = None
            if is_placeholder and not unresolved_default:
                sets.extend(["adapter_type = ?", "adapter_config_json = ?"])
                params.extend([
                    selected_adapter or current_adapter or "manual",
                    json.dumps(selection.get("adapter_config") or {}, ensure_ascii=False, sort_keys=True),
                ])
            elif _profile_repair_config is not None:
                sets.extend(["adapter_type = ?", "adapter_config_json = ?"])
                params.extend([
                    _profile_repair_adapter,
                    json.dumps(_profile_repair_config, ensure_ascii=False, sort_keys=True),
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
    if include_tier3:
        # Team modes keep cheap specialists ready. A true `solo_lead`
        # project must not materialize dormant pseudo-team members.
        tier3_created = ensure_tier3_agents(db_path, profiles=profiles)
        repaired.extend(tier3_created)
    return repaired


def _profile_score(profile: dict[str, Any], *, needs_senior: bool) -> int:
    profile_id = str(profile.get("id") or "").lower()
    provider = str(profile.get("provider") or "").lower()
    channel = str(profile.get("channel") or "").lower()
    health_status = str((profile.get("health") or {}).get("status") or "").lower()
    score = 0
    if health_status == "ok":
        score += 40
    elif health_status == "installed":
        score += 15
    if needs_senior:
        if any(token in profile_id for token in ("openai", "codex", "anthropic", "gemini")):
            score += 25
        if channel in {"api", "subscription", "free_gateway"}:
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
        # Capacidad de workspace es un hard gate previo. Aquí solo se ordenan
        # salud/economía; API y CLI no reciben premios o castigos ficticios.
    return score


def _choose_model(
    profile_id: str,
    *,
    role: str = "",
    needs_senior: bool,
    configured_model: str | None = None,
    channel: str = "",
    selectable_models: set[str] | None = None,
) -> str | None:
    """Select the best model for a given profile and role.

    Uses role capability profiles when available (prefers the top-scored option
    that matches the role's capability needs).  Falls back to the original
    senior/cheap heuristic for unknown roles.
    """
    # Un perfil local representa un modelo que el owner ya instaló y probó.
    # No lo sustituimos por una opción estática más nueva que quizá no exista
    # en esa máquina; los upgrades locales requieren selección/health explícitos.
    if str(channel or "").strip().lower() == "local" and configured_model:
        return configured_model if selectable_models is None or configured_model in selectable_models else None

    if role:
        options = model_options_for_role(profile_id, role)
        if selectable_models is not None:
            options = [item for item in options if str(item.get("value") or "") in selectable_models]
        if options:
            return str(options[0]["value"])

    # Legacy fallback: senior → best, junior → cheapest option with "mini"/"flash"/"lite"
    options = model_options().get(profile_id, [])
    if selectable_models is not None:
        options = [item for item in options if str(item.get("value") or "") in selectable_models]
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


def senior_model_for_profile(profile_id: str) -> str | None:
    """Modelo de máxima capacidad del perfil — objetivo de la cascada de escalado.

    Deliberadamente SIN role: las opciones por rol devuelven el modelo que el
    scoring consideró suficiente para ese rol (el barato que acaba de fallar);
    la cascada necesita el tope del perfil, no otra vez el mismo peldaño.
    """
    profile = next(
        (item for item in load_adapter_profiles() if str(item.get("id") or "") == profile_id),
        {},
    )
    config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
    return _choose_model(
        profile_id,
        role="",
        needs_senior=True,
        configured_model=str(config.get("model") or "").strip() or None,
        channel=str(profile.get("channel") or ""),
    )


def ensure_quorum_agents(
    db_path: Path,
    *,
    profiles: list[dict[str, Any]],
    explicit_selections: dict[str, dict[str, Any]] | None = None,
    target_agent_ids: list[str] | None = None,
    issue_id: str = "",
) -> list[str]:
    """Create quorum auditor agents if they do not already exist.

    Called automatically when a ``lead_quorum`` task is created so the Lead can
    immediately assign sub-issues to ``role:quorum_auditor_1`` and
    ``role:quorum_auditor_2`` without FK failures or hallucinated hires.

    Returns the list of agent IDs that were inserted (empty if all existed).
    """
    canonical_ids = ["role:quorum_auditor_1", "role:quorum_auditor_2"]
    quorum_ids = target_agent_ids or canonical_ids
    if any(agent_id not in canonical_ids for agent_id in quorum_ids):
        raise ValueError("unknown quorum auditor id")
    explicit_by_id = explicit_selections or {}
    created: list[str] = []
    profiles_by_id = {str(profile.get("id") or ""): profile for profile in profiles}
    used_profile_ids: set[str] = set()
    used_perspectives: set[str] = set()
    compatibility_context: dict[str, Any] = {}
    if issue_id:
        from aiteam.compatibility_service import issue_compatibility_context

        compatibility_context = issue_compatibility_context(db_path, issue_id)
    with sqlite3.connect(str(db_path), timeout=20.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        lead_id = _lead_agent_id(conn)
        for existing in conn.execute(
            "SELECT id, adapter_config_json FROM agents WHERE id IN (?, ?)", canonical_ids
        ).fetchall():
            if str(existing["id"]) in quorum_ids:
                continue
            try:
                existing_config = json.loads(str(existing["adapter_config_json"] or "{}"))
            except (TypeError, ValueError):
                existing_config = {}
            existing_profile_id = str(existing_config.get("profile_id") or "")
            existing_profile = profiles_by_id.get(existing_profile_id, {})
            if existing_profile_id:
                used_profile_ids.add(existing_profile_id)
            if existing_profile:
                used_perspectives.add(profile_perspective_key(
                    existing_profile,
                    selected_model=str(existing_config.get("model") or ""),
                ))
        for agent_id in quorum_ids:
            existing = conn.execute(
                "SELECT id, adapter_config_json FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if existing:
                try:
                    existing_config = json.loads(str(existing["adapter_config_json"] or "{}"))
                except (TypeError, ValueError):
                    existing_config = {}
                existing_profile_id = str(existing_config.get("profile_id") or "")
                existing_profile = profiles_by_id.get(existing_profile_id, {})
                if existing_profile_id:
                    used_profile_ids.add(existing_profile_id)
                if existing_profile:
                    used_perspectives.add(profile_perspective_key(
                        existing_profile,
                        selected_model=str(existing_config.get("model") or ""),
                    ))
                continue
            explicit = explicit_by_id.get(agent_id)
            if explicit:
                selected_profile_id = str(explicit.get("profile_id") or "")
                selected_profile = profiles_by_id.get(selected_profile_id)
                if selected_profile is None:
                    raise ValueError(f"unknown quorum profile: {selected_profile_id}")
                selected_model = str(explicit.get("model") or "")
                perspective = profile_perspective_key(
                    selected_profile, selected_model=selected_model
                )
                if perspective in used_perspectives:
                    raise ValueError("quorum selection must preserve provider perspective diversity")
                selection = {
                    "adapter_type": selected_profile.get("adapter_type") or "manual",
                    "adapter_profile_id": selected_profile_id,
                    "adapter_config": {
                        **dict(selected_profile.get("config") or {}),
                        "profile_id": selected_profile_id,
                        "model": selected_model,
                        "selection_intent": {
                            "schema_version": "model_selection_intent_v1",
                            "mode": "owner_explicit",
                            "source": "model_role_selector",
                            "candidate_id": explicit.get("candidate_id"),
                        },
                    },
                }
            else:
                # Independencia por construcción: el segundo auditor intenta primero
                # otro proveedor y después, si no existe, al menos otro perfil/canal.
                candidates = [
                    profile for profile in profiles
                    if profile_perspective_key(profile) not in used_perspectives
                ] or [
                    profile for profile in profiles
                    if str(profile.get("id") or "") not in used_profile_ids
                ] or profiles
                selection = choose_adapter_for_new_slot(
                    db_path,
                    role="quorum_auditor",
                    seniority="senior",
                    profiles=candidates,
                    selection_scope=f"quorum:new-agent:{agent_id}",
                    issue_id=issue_id,
                    **compatibility_context,
                )
                selected_profile_id = str((selection or {}).get("adapter_profile_id") or "")
                selected_profile = profiles_by_id.get(selected_profile_id, {})
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
                if selected_profile_id:
                    used_profile_ids.add(selected_profile_id)
                if selected_profile:
                    used_perspectives.add(profile_perspective_key(
                        selected_profile,
                        selected_model=str(
                            ((selection or {}).get("adapter_config") or {}).get("model") or ""
                        ),
                    ))
                # The next governed selection persists its snapshot through a
                # separate SQLite connection. Release this idempotent agent
                # write first; a crash is recovered by the next ensure call.
                conn.commit()
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
            selection = choose_adapter_for_new_slot(
                db_path,
                role=role,
                seniority=seniority,
                profiles=profiles,
                selection_scope=f"tier3:new-agent:{agent_id}",
            )
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
                # See ensure_quorum_agents: avoid holding a writer lock while
                # the next slot persists its score snapshot independently.
                conn.commit()
        conn.commit()
    return created


def _sibling_cli_profile_id(conn: sqlite3.Connection, *, exclude_agent_id: str) -> str | None:
    """profile_id from another active subscription_cli agent whose config has one.

    When a CLI agent's config lost its profile_id and the project allowlist
    can't supply a CLI profile (allowlist drift), the siblings that ARE
    running successfully are the ground truth of what works on this machine.
    """
    rows = conn.execute(
        """
        SELECT adapter_config_json FROM agents
        WHERE adapter_type = 'subscription_cli'
          AND status IN ('active', 'idle', 'running')
          AND id != ?
        ORDER BY updated_at DESC
        """,
        (exclude_agent_id,),
    ).fetchall()
    for row in rows:
        try:
            config = json.loads(str(row["adapter_config_json"] or "{}"))
        except (TypeError, ValueError):
            continue
        profile_id = str((config or {}).get("profile_id") or "").strip()
        if profile_id:
            return profile_id
    return None


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
