"""Ex-ante run economics and hiring-decision auditing (cost policy, phase A2).

Fills the already-wired ``runs.estimated_cost_cents`` and
``runs.estimated_savings_cents`` with real numbers, and emits auditable
``hiring.decision`` activity events whenever an agent is (re)assigned an
adapter — including ``policy_deviation`` when a worker role lands on a
per-token premium model.

The cost policy chooses the *model inside the tier*; it never changes the
tier — routing by criticality×complexity stays the Lead's job.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from aiteam.db.activity_log import log_activity
from aiteam.pricing import estimate_cost_cents, price_per_mtok, typical_tokens_for_role
from aiteam.project_adapters import JUNIOR_ROLES, choose_adapter_for_role, project_profiles
from aiteam.user_config import load_adapter_profiles, profile_is_connected, resolve_adapter_config

logger = logging.getLogger(__name__)

_ADAPTER_DEFAULT_PROVIDER = {
    "openai_api": "openai",
    "gemini_api": "google",
    "anthropic_api": "anthropic",
    "anthropic_sonnet": "anthropic",
    "openai_compatible_api": "openai-compatible",
}
_ADAPTER_DEFAULT_MODEL = {
    "openai_api": "gpt-5.6-terra",
    "gemini_api": "gemini-3.5-flash",
    "anthropic_api": "claude-opus-4-8",
    "anthropic_sonnet": "claude-sonnet-5",
}


def provider_and_model_for(adapter_type: str, adapter_config: dict[str, Any] | None) -> tuple[str, str]:
    """Resolve the effective (provider, model) an agent will bill against."""
    adapter_type = str(adapter_type or "").strip()
    config = adapter_config if isinstance(adapter_config, dict) else {}
    merged = resolve_adapter_config(adapter_type, config)
    provider = ""
    profile_id = str(config.get("profile_id") or "").strip()
    if profile_id:
        for profile in load_adapter_profiles():
            if str(profile.get("id") or "") == profile_id:
                provider = str(profile.get("provider") or "")
                break
    if not provider:
        provider = _ADAPTER_DEFAULT_PROVIDER.get(adapter_type, "")
    model = str(merged.get("model") or "").strip() or _ADAPTER_DEFAULT_MODEL.get(adapter_type, "")
    return provider, model


def estimate_run_economics(db_path: Path, agent_id: str) -> tuple[int, int]:
    """Return (estimated_cost_cents, estimated_savings_cents) for the agent's
    next run, based on its adapter and the role's typical token usage.

    Savings compare against the premium adapter a senior assignment would use
    in this project — the "what if this ran on the expensive model" number.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT role, adapter_type, adapter_config_json FROM agents WHERE id = ?",
            (agent_id,),
        ).fetchone()
    if row is None:
        return (0, 0)
    role = str(row["role"] or "")
    adapter_config = _decode_json(row["adapter_config_json"])
    provider, model = provider_and_model_for(str(row["adapter_type"] or ""), adapter_config)
    input_tokens, output_tokens = typical_tokens_for_role(db_path, role)
    estimated = 0 if resolve_adapter_config(
        str(row["adapter_type"] or ""), adapter_config
    ).get("free_tier") else estimate_cost_cents(provider, model, input_tokens, output_tokens)
    premium = premium_alternative_cents(db_path, role, input_tokens, output_tokens)
    return (estimated, max(0, premium - estimated))


def premium_alternative_cents(db_path: Path, role: str, input_tokens: int, output_tokens: int) -> int:
    """Cost of the same run on the adapter a senior assignment would get."""
    try:
        profiles = [p for p in project_profiles(Path(db_path).parent) if profile_is_connected(p)]
    except Exception:
        return 0
    selection = choose_adapter_for_role(role, "senior", profiles)
    if not selection:
        return 0
    profile = next(
        (p for p in profiles if str(p.get("id") or "") == str(selection.get("adapter_profile_id") or "")),
        None,
    )
    provider = str((profile or {}).get("provider") or "") or _ADAPTER_DEFAULT_PROVIDER.get(
        str(selection.get("adapter_type") or ""), ""
    )
    model = str(selection.get("model") or "").strip() or _ADAPTER_DEFAULT_MODEL.get(
        str(selection.get("adapter_type") or ""), ""
    )
    config = (profile or {}).get("config") if isinstance((profile or {}).get("config"), dict) else {}
    return 0 if config.get("free_tier") else estimate_cost_cents(provider, model, input_tokens, output_tokens)


def log_hiring_decision(
    db_path: Path,
    *,
    agent_id: str,
    role: str,
    adapter_type: str,
    adapter_config: dict[str, Any] | None,
    adapter_profile_id: str | None = None,
    source: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Emit an auditable ``hiring.decision`` event for an adapter assignment.

    ``policy_deviation`` is set when a junior/worker role lands on a
    per-token premium model:
    - ``no_zero_cost_channel_connected`` — the project simply has no
      connected local/subscription channel (actionable: connect one),
    - ``scoring_preferred_premium`` — a zero-cost channel existed but the
      scoring still chose premium (worth reviewing).
    """
    provider, model = provider_and_model_for(adapter_type, adapter_config)
    input_tokens, output_tokens = typical_tokens_for_role(db_path, role)
    resolved_config = resolve_adapter_config(adapter_type, adapter_config or {})
    estimated = 0 if resolved_config.get("free_tier") else estimate_cost_cents(
        provider, model, input_tokens, output_tokens
    )
    premium = premium_alternative_cents(db_path, role, input_tokens, output_tokens)

    deviation: str | None = None
    # Per-token billing is the deviation signal, not the rounded estimate —
    # cheap "mini" models cost <1¢/run and would escape an `estimated > 0` check.
    if (
        str(role or "").strip().lower() in JUNIOR_ROLES
        and not resolved_config.get("free_tier")
        and price_per_mtok(provider, model) != (0, 0)
    ):
        deviation = (
            "scoring_preferred_premium"
            if _zero_cost_channel_connected(db_path)
            else "no_zero_cost_channel_connected"
        )

    payload = {
        "role": role,
        "adapter_type": adapter_type,
        "adapter_profile_id": adapter_profile_id or str((adapter_config or {}).get("profile_id") or "") or None,
        "provider": provider,
        "model": model,
        "estimated_cost_cents": estimated,
        "premium_alternative_cents": premium,
        "estimated_savings_cents": max(0, premium - estimated),
        "policy_deviation": deviation,
        "source": source,
    }
    try:
        log_activity(
            db_path,
            action="hiring.decision",
            target_type="agent",
            target_id=agent_id,
            actor_agent_id=None,
            run_id=run_id,
            payload=payload,
        )
    except Exception:
        logger.warning("hiring.decision log failed for agent %s", agent_id, exc_info=True)
    return payload


def detect_policy_deviations(db_path: Path) -> list[dict[str, Any]]:
    """Live scan: active worker-role agents currently billing per-token.

    Returns one entry per deviating agent with its estimated cost per run and
    the actionable reason, for /api/loop-health and UI warnings.
    """
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            rows = conn.execute(
                """
                SELECT id, role, adapter_type, adapter_config_json
                FROM agents
                WHERE status IN ('active', 'idle', 'running')
                ORDER BY id ASC
                """
            ).fetchall()
    except sqlite3.Error:
        return []
    deviations: list[dict[str, Any]] = []
    zero_cost_available: bool | None = None
    for row in rows:
        role = str(row["role"] or "").strip().lower()
        if role not in JUNIOR_ROLES:
            continue
        adapter_config = _decode_json(row["adapter_config_json"])
        provider, model = provider_and_model_for(str(row["adapter_type"] or ""), adapter_config)
        if (
            resolve_adapter_config(str(row["adapter_type"] or ""), adapter_config).get("free_tier")
            or price_per_mtok(provider, model) == (0, 0)
        ):
            continue
        input_tokens, output_tokens = typical_tokens_for_role(db_path, role)
        estimated = estimate_cost_cents(provider, model, input_tokens, output_tokens)
        if zero_cost_available is None:
            zero_cost_available = _zero_cost_channel_connected(db_path)
        deviations.append({
            "agent_id": str(row["id"]),
            "role": role,
            "provider": provider,
            "model": model,
            "estimated_cost_cents_per_run": estimated,
            "reason": "scoring_preferred_premium" if zero_cost_available else "no_zero_cost_channel_connected",
        })
    return deviations


def provider_router_health(
    db_path: Path, *, min_runs: int = 5, window_hours: int | None = None
) -> list[dict[str, Any]]:
    """Tasa de escalación (fallos de infra) por proveedor, como señal de salud
    del router.

    Patrón 2026 (skill multi-model-orchestration): el router es infraestructura
    de producción y su salud se mide. Aquí un run que falla en el transporte del
    proveedor o el entorno del CLI (INFRA_ERROR_CODES) es una "escalación"
    involuntaria — el trabajo tuvo que recuperarse en vez de completarse. Una
    tasa alta significa que ESE proveedor está fallando (credenciales, rate
    limits, binario ausente), no que el equipo sea malo: fue exactamente el
    patrón del proyecto Unity (claude-code al 24%, subscription_cli_not_found).

    Devuelve una fila por proveedor con >= ``min_runs`` runs, ordenada por tasa
    descendente, marcando ``unhealthy`` cuando supera el umbral env-tunable.
    """
    from aiteam.policies import INFRA_ERROR_CODES, provider_escalation_threshold

    placeholders = ",".join("?" for _ in INFRA_ERROR_CODES)
    window_filter = ""
    params: tuple[Any, ...] = tuple(INFRA_ERROR_CODES)
    if window_hours and window_hours > 0:
        window_filter = "AND created_at >= datetime('now', ?)"
        params = (*params, f"-{int(window_hours)} hours")
    try:
        with contextlib.closing(_connect(db_path)) as conn:
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE(provider, '?') AS provider,
                    COUNT(*) AS total_runs,
                    SUM(CASE WHEN status = 'failed'
                              AND error_code IN ({placeholders})
                             THEN 1 ELSE 0 END) AS infra_failures,
                    GROUP_CONCAT(DISTINCT adapter_type) AS adapter_types
                FROM runs
                WHERE provider IS NOT NULL
                  {window_filter}
                GROUP BY COALESCE(provider, '?')
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        return []

    threshold = provider_escalation_threshold()
    out: list[dict[str, Any]] = []
    for row in rows:
        total = int(row["total_runs"] or 0)
        if total < min_runs:
            continue
        infra = int(row["infra_failures"] or 0)
        rate = infra / total if total else 0.0
        out.append({
            "provider": str(row["provider"]),
            "total_runs": total,
            "infra_failures": infra,
            "escalation_rate": round(rate, 4),
            "threshold": threshold,
            "unhealthy": rate > threshold,
            "adapter_types": sorted(str(row["adapter_types"] or "").split(",")) if row["adapter_types"] else [],
            "window_hours": window_hours,
        })
    out.sort(key=lambda item: item["escalation_rate"], reverse=True)
    return out


def demoted_profile_ids(
    db_path: Path, profiles: list[dict[str, Any]], *, window_hours: int = 24
) -> set[str]:
    """Perfiles a DEMOTAR en el routing porque su adapter corre sobre un
    proveedor unhealthy en la ventana reciente.

    Cierra el lazo salud→routing: la métrica ya diagnosticaba (proyecto Unity,
    claude-code al 24% de infra-fallos) pero el router seguía eligiendo el
    mismo canal roto. Demote = reordenar al final, NUNCA excluir: si es el
    único canal conectado, se sigue usando.
    """
    try:
        unhealthy_adapter_types: set[str] = set()
        for row in provider_router_health(db_path, window_hours=window_hours):
            if row["unhealthy"]:
                unhealthy_adapter_types.update(t for t in row["adapter_types"] if t)
        if not unhealthy_adapter_types:
            return set()
        return {
            str(p.get("id") or "")
            for p in profiles
            if str(p.get("adapter_type") or "") in unhealthy_adapter_types
        }
    except Exception:
        return set()


def _zero_cost_channel_connected(db_path: Path) -> bool:
    try:
        profiles = project_profiles(Path(db_path).parent)
    except Exception:
        return False
    for profile in profiles:
        config = profile.get("config") if isinstance(profile.get("config"), dict) else {}
        if (
            str(profile.get("channel") or "") in {"local", "subscription", "free_gateway"}
            or bool(config.get("free_tier"))
        ) and profile_is_connected(profile):
            return True
    return False


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20.0)
    conn.row_factory = sqlite3.Row
    return conn


def _decode_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
