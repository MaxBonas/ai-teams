from __future__ import annotations

import json
import re
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,255}$")
_SCOPE_FIELDS = (
    "agent_id",
    "issue_id",
    "adapter_type",
    "profile_id",
    "provider",
    "model",
    "channel",
    "workspace_id",
)


@dataclass(frozen=True)
class SessionScope:
    """Identidad durable que una sesión CLI no puede cruzar.

    El ID del proveedor es solo un puntero. La autoridad sigue perteneciendo al
    rol/agente y a la issue de AI Teams; por eso todos los campos deben coincidir
    antes de que un adapter pueda siquiera plantear una reanudación.
    """

    agent_id: str
    issue_id: str
    adapter_type: str
    profile_id: str
    provider: str
    model: str
    channel: str
    workspace_id: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SessionScope":
        return cls(**{field: str(value.get(field) or "").strip() for field in _SCOPE_FIELDS})


def validate_resume_scope(
    *,
    previous: SessionScope,
    current: SessionScope,
    session_id: str,
    previous_status: str,
    explicit_opt_in: bool,
) -> dict[str, Any]:
    """Decide de forma pura si un experimento puede reutilizar una sesión.

    No selecciona `--last`/`--continue`, no consulta historiales globales y no
    convierte esta decisión en activación productiva.
    """

    reasons: list[str] = []
    clean_session_id = str(session_id or "").strip()
    if not explicit_opt_in:
        reasons.append("experiment_not_enabled")
    if str(previous_status or "").strip() != "completed":
        reasons.append("previous_run_not_completed")
    if not _SAFE_SESSION_ID.fullmatch(clean_session_id):
        reasons.append("invalid_explicit_session_id")
    if current.channel != "subscription":
        reasons.append("channel_not_subscription")
    for field in _SCOPE_FIELDS:
        if not getattr(previous, field) or not getattr(current, field):
            reasons.append(f"missing_{field}")
        elif getattr(previous, field) != getattr(current, field):
            reasons.append(f"scope_mismatch_{field}")
    return {
        "allowed": not reasons,
        "session_id": clean_session_id if not reasons else None,
        "reasons": reasons,
        "scope_fields": list(_SCOPE_FIELDS),
        "selector": "explicit_id_only",
    }


def extract_codex_session_id(jsonl: str) -> str | None:
    """Extrae el UUID de `thread.started` sin aceptar selectores implícitos."""

    for raw_line in str(jsonl or "").splitlines():
        try:
            event = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict) or event.get("type") != "thread.started":
            continue
        candidate = str(event.get("thread_id") or event.get("session_id") or "").strip()
        if _SAFE_SESSION_ID.fullmatch(candidate):
            return candidate
    return None


def audit_session_experiment(
    reports: Iterable[dict[str, Any]],
    *,
    min_seeds: int = 2,
) -> dict[str, Any]:
    """Audita un A/B stateless vs resumed sin ejecutar ningún proveedor.

    Cada brazo debe demostrar recuerdo del hecho inicial, aplicación de la
    instrucción nueva y ausencia de la instrucción revocada. El ahorro solo se
    calcula cuando ambos brazos tienen usage comparable.
    """

    rows = [dict(item) for item in reports]
    issues: list[str] = []
    cells: dict[tuple[int, str], dict[str, Any]] = {}
    providers: set[str] = set()
    for row in rows:
        try:
            seed = int(row.get("seed"))
        except (TypeError, ValueError):
            issues.append("report_without_seed")
            continue
        arm = str(row.get("arm") or "").strip()
        if arm not in {"stateless", "resumed"}:
            issues.append(f"invalid_arm:{arm or 'missing'}")
            continue
        key = (seed, arm)
        if key in cells:
            issues.append(f"duplicate_cell:{seed}:{arm}")
        cells[key] = row
        providers.add(str(row.get("provider") or "").strip())

    seeds = sorted({seed for seed, _arm in cells})
    for seed in seeds:
        for arm in ("stateless", "resumed"):
            if (seed, arm) not in cells:
                issues.append(f"missing_cell:{seed}:{arm}")
    if len(seeds) < max(1, int(min_seeds)):
        issues.append(f"insufficient_seeds:{len(seeds)}<{max(1, int(min_seeds))}")

    quality_failures: list[str] = []
    token_savings: list[float] = []
    duration_savings: list[float] = []
    for seed in seeds:
        stateless = cells.get((seed, "stateless"))
        resumed = cells.get((seed, "resumed"))
        if not stateless or not resumed:
            continue
        for arm, row in (("stateless", stateless), ("resumed", resumed)):
            if str(row.get("status") or "") != "completed":
                quality_failures.append(f"{seed}:{arm}:run_not_completed")
            gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
            for gate in ("retains_initial_fact", "applies_new_instruction", "revoked_instruction_absent"):
                if gates.get(gate) is not True:
                    quality_failures.append(f"{seed}:{arm}:{gate}")
        if resumed.get("explicit_session_id") is not True:
            quality_failures.append(f"{seed}:resumed:explicit_session_id")
        if resumed.get("scope_match") is not True:
            quality_failures.append(f"{seed}:resumed:scope_match")

        if all(str(row.get("status") or "") == "completed" for row in (stateless, resumed)):
            stateless_tokens = _positive_number(stateless.get("input_tokens"))
            resumed_tokens = _positive_number(resumed.get("input_tokens"))
            if stateless_tokens is not None and resumed_tokens is not None:
                token_savings.append((stateless_tokens - resumed_tokens) / stateless_tokens)
            stateless_seconds = _positive_number(stateless.get("duration_seconds"))
            resumed_seconds = _positive_number(resumed.get("duration_seconds"))
            if stateless_seconds is not None and resumed_seconds is not None:
                duration_savings.append((stateless_seconds - resumed_seconds) / stateless_seconds)

    if quality_failures:
        issues.append("quality_or_isolation_gate_failed")
    if len(token_savings) != len(seeds):
        issues.append("token_usage_not_comparable")
    if len(duration_savings) != len(seeds):
        issues.append("duration_not_comparable")

    quality_equal = not quality_failures and bool(seeds)
    token_median = median(token_savings) if token_savings else None
    duration_median = median(duration_savings) if duration_savings else None
    beneficial = (
        quality_equal
        and token_median is not None
        and duration_median is not None
        and token_median > 0
        and duration_median > 0
    )
    return {
        "kind": "cli_session_continuity",
        "arms": ["stateless", "resumed"],
        "seeds": seeds,
        "providers": sorted(item for item in providers if item),
        "complete_cells": len(cells),
        "quality_equal": quality_equal,
        "quality_failures": quality_failures,
        "median_input_token_savings_ratio": token_median,
        "median_duration_savings_ratio": duration_median,
        "beneficial": beneficial,
        "production_activation_allowed": beneficial and not issues,
        "issues": sorted(set(issues)),
        "goodhart_risk": (
            "material: el canario mide memoria/override/aislamiento, no ausencia universal de contaminación"
        ),
    }


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
