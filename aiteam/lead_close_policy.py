from __future__ import annotations

from typing import Any

from aiteam.phase_verdicts import coerce_phase_verdicts


# Keywords that indicate an engineer-role phase is self-reporting a block in its
# output text.  Matches the same narrowed set used by _notify_dependents so both
# mechanisms stay in sync.
_ENGINEER_BLOCK_KEYWORDS = (
    "bloqueada:",        # explicit status label "BLOQUEADA: razon..."
    "bloqueado:",        # same, masculine form
    "evidencegate",      # compound system word
    "evidence gate",     # alternative gate phrase
    "no hay evidencia",  # explicit missing-evidence claim
    "missing evidence",  # same in English
    "status: blocked",   # structured field (bare colon is too broad)
)

# Phase name fragments that hint at engineer role for phase_outputs fallback scan
_ENGINEER_PHASE_HINTS = ("engineer", "build", "implement", "develop", "code")

# Phases whose verdicts/states are never used as blocking signals
_SKIP_PHASES = frozenset({"lead_intake", "lead_close"})

# Phases already handled by the hardcoded verdict checks below
_ALREADY_CHECKED_PHASES = frozenset({"review", "qa", "build"})

_SEMANTIC_PRIMARY_PREFIXES = (
    "review_rejected",
    "qa_blocked",
    "slice_drift",
    "continuation_drift",
    "semantic_gate_failed",
)
_SEMANTIC_PRIMARY_EXACT = frozenset(
    {
        "review_blocked",
        "review_failed",
        "qa_failed",
    }
)


def _is_advisory_context_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith(("lead_", "delegate_", "plan_")):
        return False
    if normalized in {
        "current_state",
        "existing_state",
        "repo_state",
        "workspace_state",
        "codebase_state",
        "scout_current_state",
        "research_current_state",
    }:
        return True
    has_current_marker = any(
        marker in normalized for marker in ("current", "existing", "baseline", "snapshot")
    )
    has_state_marker = any(
        marker in normalized
        for marker in (
            "state",
            "workspace",
            "repo",
            "codebase",
            "layout",
            "tree",
            "inventory",
            "structure",
        )
    )
    return has_current_marker and has_state_marker


def _is_advisory_planning_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(("lead_", "delegate_")):
        return False
    if not normalized.startswith("plan_"):
        return False
    return any(
        marker in normalized
        for marker in ("research", "discovery", "analysis", "constraints", "context")
    )


def _is_support_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return True
    return (
        normalized in _SKIP_PHASES
        or _is_advisory_context_phase_id(normalized)
        or _is_advisory_planning_phase_id(normalized)
        or normalized.startswith("scout_")
        or normalized.startswith("delegate_")
        or normalized.startswith("lead_preflight_")
        or normalized.startswith("lead_report_")
    )


def _is_non_gate_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return True
    return normalized.startswith(("lead_", "delegate_", "plan_"))


def _gate_kind_for_phase(phase_id: str, role_hint: str = "") -> str:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role_hint or "").strip().lower()
    if _is_non_gate_phase_id(normalized_phase):
        return ""
    if normalized_phase == "build":
        return "build"
    if normalized_phase == "review":
        return "review"
    if normalized_phase == "qa":
        return "qa"
    if "review" in normalized_phase:
        return "review"
    if "qa" in normalized_phase or "validation" in normalized_phase or normalized_phase.startswith("validate"):
        return "qa"
    if normalized_role == "engineer":
        return "build"
    if normalized_role == "reviewer":
        return "review"
    if normalized_role == "qa":
        return "qa"
    if any(h in normalized_phase for h in _ENGINEER_PHASE_HINTS):
        return "build"
    return ""


def _verdict_matches_gate(entry: dict[str, Any], gate_kind: str) -> bool:
    if not isinstance(entry, dict):
        return False
    normalized_gate = str(gate_kind or "").strip().lower()
    if not normalized_gate:
        return False
    entry_phase = str(entry.get("phase_id", "") or "").strip().lower()
    entry_role = str(entry.get("role_hint", "") or "").strip().lower()
    return _gate_kind_for_phase(entry_phase, entry_role) == normalized_gate


def _select_primary_gate_verdict(
    verdicts: dict[str, dict[str, Any]],
    gate_kind: str,
) -> dict[str, Any]:
    normalized_gate = str(gate_kind or "").strip().lower()
    if not normalized_gate:
        return {}
    explicit = dict(verdicts.get(normalized_gate, {}) or {})
    if explicit and _verdict_matches_gate(explicit, normalized_gate):
        return explicit
    for phase_id, entry in verdicts.items():
        if not isinstance(entry, dict):
            continue
        role_hint = str(entry.get("role_hint", "") or "").strip().lower()
        if _gate_kind_for_phase(phase_id, role_hint) == normalized_gate:
            return dict(entry)
    return {}


def _normalize_phase_states(payload: object) -> dict[str, str]:
    phase_states: dict[str, str] = {}
    if not isinstance(payload, dict):
        return phase_states
    for raw_phase_id, raw_state in payload.items():
        phase_id = str(raw_phase_id or "").strip().lower()
        state = str(raw_state or "").strip().lower()
        if phase_id and state:
            phase_states[phase_id] = state
    return phase_states


def _safe_reason_label(phase_id: str) -> str:
    """Convert an arbitrary phase_id into a safe reason_code label."""
    label = phase_id.strip().lower()
    # Replace non-alphanumeric/underscore chars with underscores
    result = []
    for ch in label:
        result.append(ch if ch.isalnum() or ch == "_" else "_")
    return "".join(result)[:48]


def _is_semantic_primary_signal(reason_code: str) -> bool:
    normalized = str(reason_code or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith("plan_") and (
        normalized.endswith("_failed") or normalized.endswith("_blocked")
    ):
        return True
    if normalized in _SEMANTIC_PRIMARY_EXACT:
        return True
    if any(normalized.startswith(prefix) for prefix in _SEMANTIC_PRIMARY_PREFIXES):
        return True
    if normalized.endswith("_drift"):
        return True
    return False


def _blocking_signal_sort_key(reason_code: str) -> tuple[int, str]:
    normalized = str(reason_code or "").strip().lower()
    if _is_semantic_primary_signal(normalized):
        return (0, normalized)
    if normalized.startswith("phase_failed:plan_"):
        return (0, normalized)
    if normalized in {"run_rejected", "run_failed", "no_implementation_phase"}:
        return (1, normalized)
    if normalized in {
        "evidence_gate_failed",
        "strict_mode_requires_more_evidence",
        "live_mode_required_non_live",
        "low_productivity_below_threshold",
    }:
        return (2, normalized)
    if normalized.endswith("_waiting_user"):
        return (3, normalized)
    if normalized.endswith("_failed") or normalized.endswith("_blocked"):
        return (4, normalized)
    return (5, normalized)


def _has_primary_planning_failure(reason_codes: list[str]) -> bool:
    for item in list(reason_codes or []):
        normalized = str(item or "").strip().lower()
        if normalized.startswith("phase_failed:plan_"):
            return True
        if normalized.startswith("plan_") and (
            normalized.endswith("_failed") or normalized.endswith("_blocked")
        ):
            return True
    return False


def _should_suppress_downstream_blocked_signal(
    reason_code: str,
    *,
    planning_failure_present: bool,
) -> bool:
    if not planning_failure_present:
        return False
    normalized = str(reason_code or "").strip().lower()
    if not normalized.endswith("_blocked"):
        return False
    if normalized.startswith("plan_"):
        return False
    if _is_semantic_primary_signal(normalized):
        return False
    phase_id = normalized[:-8]
    if phase_id == "lead_close":
        return True
    return _gate_kind_for_phase(phase_id) in {"build", "review", "qa"}


def _user_accepted_degraded_close(run_verdict: dict[str, Any]) -> bool:
    state = str(run_verdict.get("state", "") or "").strip().lower()
    if state != "completed":
        return False
    return bool(run_verdict.get("user_risk_acceptance")) and (
        bool(run_verdict.get("degraded_delivery"))
        or bool(run_verdict.get("advisory_mode"))
    )


def _risk_acceptance_can_cover_signal(reason_code: str) -> bool:
    normalized = str(reason_code or "").strip().lower()
    if not normalized:
        return False
    if normalized in {
        "qa_blocked",
        "qa_failed",
        "review_blocked",
        "evidence_gate_failed",
        "strict_mode_requires_more_evidence",
        "live_mode_required_non_live",
        "low_productivity_below_threshold",
    }:
        return True
    for suffix in ("_blocked", "_failed"):
        if not normalized.endswith(suffix):
            continue
        phase_id = normalized[: -len(suffix)]
        gate_kind = _gate_kind_for_phase(phase_id)
        if gate_kind == "qa":
            return True
        if gate_kind == "review" and suffix == "_blocked":
            return True
    return False


def _recommended_close_action(reason_codes: list[str]) -> str:
    normalized = {
        str(item or "").strip().lower()
        for item in list(reason_codes or [])
        if str(item or "").strip()
    }
    if "review_rejected" in normalized:
        return "rework_from_review_findings"
    if "review_blocked" in normalized:
        return "restore_review_evidence_or_retry_review"
    if "qa_blocked" in normalized or "qa_failed" in normalized:
        return "repair_or_rerun_validation"
    if "slice_drift" in normalized or "continuation_drift" in normalized:
        return "replan_with_current_user_scope"
    if "no_implementation_phase" in normalized:
        return "plan_and_execute_implementation"
    return ""


def derive_lead_close_policy(
    *,
    phase_verdicts: object,
    phase_states: object = None,
    run_verdict: object = None,
    phase_outputs: object = None,
) -> dict[str, Any]:
    """
    Derive the authoritative close policy for a lead_close phase.

    Parameters
    ----------
    phase_verdicts : dict
        Structured verdict objects keyed by phase_id (from workflow state).
    phase_states : dict, optional
        Raw task states keyed by phase_id (from taskboard).
    run_verdict : dict, optional
        The persisted run verdict dict; may contain ``policy_signals`` when called
        from observability endpoints *after* chat_policy has run.  When called
        from the orchestrator during lead_close execution this dict is not yet
        finalized, so ``policy_signals`` will be empty — that is expected.
    phase_outputs : dict, optional
        Raw phase output text keyed by phase_id.  Used as a belt-and-suspenders
        fallback to detect blocking in engineer phases that did not emit a
        structured [PHASE_VERDICT] block.  Only the first 1 000 chars are scanned.
    """
    verdicts = coerce_phase_verdicts(phase_verdicts)
    states = _normalize_phase_states(phase_states)
    run_verdict_dict = run_verdict if isinstance(run_verdict, dict) else {}
    _phase_outputs_dict = phase_outputs if isinstance(phase_outputs, dict) else {}
    failure_origin = str(run_verdict_dict.get("failure_origin", "") or "").strip().lower()
    failed_phases = [
        str(item).strip()
        for item in list(run_verdict_dict.get("failed_phases", []) or [])
        if str(item).strip()
    ]
    blocked_phases = [
        str(item).strip()
        for item in list(run_verdict_dict.get("blocked_phases", []) or [])
        if str(item).strip()
    ]
    pending_phases = [
        str(item).strip()
        for item in list(run_verdict_dict.get("pending_phases", []) or [])
        if str(item).strip()
    ]
    completed_phases = [
        phase_id
        for phase_id, state in sorted(states.items())
        if state == "completed" and not _is_support_phase_id(phase_id)
    ]

    reason_codes: list[str] = []

    if failure_origin == "preplanning_support":
        verdict_reason_codes = [
            str(item).strip().lower()
            for item in list(run_verdict_dict.get("reason_codes", []) or [])
            if str(item).strip()
        ]
        ordered_reasons = sorted(
            list(dict.fromkeys(verdict_reason_codes)),
            key=_blocking_signal_sort_key,
        )
        return {
            "authoritative_close_state": "not_completed",
            "blocking_signals": ordered_reasons[:12],
            "primary_blocking_signals": ordered_reasons[:8],
            "secondary_blocking_signals": [],
            "failure_origin": failure_origin,
            "run_reason_codes": ordered_reasons[:12],
            "failed_phases": failed_phases[:12],
            "blocked_phases": blocked_phases[:12],
            "pending_phases": pending_phases[:12],
            "completed_phases": completed_phases[:12],
            "primary_reason": "preplanning_support_failure",
            "can_declare_done": False,
            "requires_close_rewrite": True,
            "prefer_semantic_summary_first": True,
        }

    # ── 0. Detect missing implementation phase ────────────────────────────────
    # A run that only contains researcher/scout phases with no engineer/build
    # phase is structurally incomplete: it diagnosed but never implemented.
    # Checking this early (before timing-dependent signals) ensures the Lead
    # cannot declare DONE when the run produced zero implementation work.
    _all_executed = {
        phase_id
        for phase_id in (
            set(verdicts.keys())
            | set(states.keys())
            | set(_phase_outputs_dict.keys())
        )
        if not _is_support_phase_id(phase_id)
    }
    # En solo_lead / direct_coding_executor el TEAM_LEAD es el implementador.
    # La heurística de fases no aplica: suprimir el check para evitar falsos positivos
    # cuando el phase_id fue custom o no contiene hints de engineer.
    _run_profile_val = str(run_verdict_dict.get("run_profile", "") or "").strip().lower()
    _suppress_no_impl_check = _run_profile_val in {"solo_lead", "direct"}
    if _all_executed and not _suppress_no_impl_check:
        _has_impl = any(
            any(h in ph.lower() for h in _ENGINEER_PHASE_HINTS)
            for ph in _all_executed
        )
        _RESEARCHER_HINTS = ("researcher", "research", "diagnos", "scan", "synthesis", "analyz")
        _has_researcher = any(
            any(h in ph.lower() for h in _RESEARCHER_HINTS)
            for ph in _all_executed
        )
        # Only flag when there were researcher phases but zero implementation phases.
        # Pure review/QA runs (no researchers, no engineers) are not flagged here.
        if _has_researcher and not _has_impl:
            reason_codes.append("no_implementation_phase")

    # ── 1. Hardcoded checks for standard phase keys ───────────────────────────

    review_verdict = _select_primary_gate_verdict(verdicts, "review")
    review_status = str(review_verdict.get("status", "") or "").strip().lower()
    review_reasons = {
        str(item).strip().lower()
        for item in list(review_verdict.get("reason_codes", []) or [])
        if str(item).strip()
    }
    if review_status in {"rejected", "failed"} or "review_rejected" in review_reasons:
        reason_codes.append("review_rejected")
    elif review_status == "blocked" or "review_blocked" in review_reasons:
        reason_codes.append("review_blocked")

    qa_verdict = _select_primary_gate_verdict(verdicts, "qa")
    qa_status = str(qa_verdict.get("status", "") or "").strip().lower()
    qa_reasons = {
        str(item).strip().lower()
        for item in list(qa_verdict.get("reason_codes", []) or [])
        if str(item).strip()
    }
    if qa_status in {"blocked", "rejected", "failed"} or "qa_blocked" in qa_reasons:
        reason_codes.append("qa_blocked")

    build_verdict = _select_primary_gate_verdict(verdicts, "build")
    build_contract_status = str(build_verdict.get("contract_status", "") or "").strip().lower()
    build_reasons = {
        str(item).strip().lower()
        for item in list(build_verdict.get("reason_codes", []) or [])
        if str(item).strip()
    }
    run_profile = str(run_verdict_dict.get("run_profile", "") or "").strip().lower()
    direct_lead_minimal_slice = (
        run_profile == "solo_lead"
        and str(build_verdict.get("status", "") or "").strip().lower() == "completed"
        and "direct_implementation" in build_reasons
    )
    if (
        build_contract_status == "drift" or "slice_drift" in build_reasons
    ) and not direct_lead_minimal_slice:
        reason_codes.append("slice_drift")

    # ── 2. Sweep ALL remaining phase verdicts for blocked/failed status ───────
    # Continuation runs may use custom phase names (e.g. "engineer_toc_implementation")
    # that do not match the hardcoded keys above.  If the engineer emitted a
    # structured [PHASE_VERDICT] block with status=blocked/failed, it ends up here.
    for _pv_id, _pv in verdicts.items():
        if _pv_id in _ALREADY_CHECKED_PHASES or _is_support_phase_id(_pv_id):
            continue
        _pv_status = str((_pv or {}).get("status", "") or "").strip().lower()
        if _pv_status in {"blocked", "failed", "rejected"}:
            reason_codes.append(f"{_safe_reason_label(_pv_id)}_blocked")

    # ── 3. Phase states from taskboard ───────────────────────────────────────
    for phase_id, state in sorted(states.items()):
        if _is_support_phase_id(phase_id):
            continue
        if state == "failed":
            reason_codes.append(f"{phase_id}_failed")
        elif state == "blocked":
            reason_codes.append(f"{phase_id}_blocked")
        elif state == "waiting_user":
            reason_codes.append(f"{phase_id}_waiting_user")

    # ── 4. Run verdict state ──────────────────────────────────────────────────
    run_state = str(run_verdict_dict.get("state", "") or "").strip().lower()
    if run_state == "rejected":
        reason_codes.append("run_rejected")
    elif run_state == "failed":
        reason_codes.append("run_failed")

    # ── 5. Policy signals from run_verdict ───────────────────────────────────
    # These are populated by chat_policy AFTER lead_close completes, so they are
    # available when derive_lead_close_policy is called from observability/API
    # endpoints but NOT when called from the orchestrator during lead_close execution.
    # Including them here is still correct: when present they refine the result;
    # when absent (orchestrator path) the other checks above must carry the signal.
    _run_policy_signals = {
        str(item).strip().lower()
        for item in list(run_verdict_dict.get("policy_signals", []) or [])
        if str(item).strip()
    }
    _not_completed_signals = {
        "evidence_gate_failed",
        "semantic_gate_failed",
        "strict_mode_requires_more_evidence",
        "live_mode_required_non_live",
        "low_productivity_below_threshold",
    }
    for signal in sorted(_run_policy_signals & _not_completed_signals):
        reason_codes.append(signal)

    # ── 6. Phase outputs fallback — engineer blocking content ─────────────────
    # Belt-and-suspenders: scan phase_outputs for engineer-role phases that
    # did NOT emit a structured [PHASE_VERDICT] block but whose raw output
    # contains blocking markers.  This catches the case where an engineer
    # responds with "BLOQUEADA: El PHASE_CONTRACT está incompleto" without
    # wrapping it in a structured block.
    for _po_id, _po_text in _phase_outputs_dict.items():
        if _is_support_phase_id(_po_id):
            continue
        _po_id_lower = _po_id.lower()
        _is_engineer_phase = any(h in _po_id_lower for h in _ENGINEER_PHASE_HINTS)
        if not _is_engineer_phase:
            continue
        _po_lower = (_po_text or "")[:1000].lower()
        if any(kw in _po_lower for kw in _ENGINEER_BLOCK_KEYWORDS):
            _label = f"{_safe_reason_label(_po_id)}_output_blocked"
            if _label not in reason_codes:
                reason_codes.append(_label)

    # ── Final decision ────────────────────────────────────────────────────────
    unique_reasons = list(dict.fromkeys(reason_codes))
    planning_failure_present = _has_primary_planning_failure(unique_reasons)
    if planning_failure_present:
        unique_reasons = [
            reason
            for reason in unique_reasons
            if not _should_suppress_downstream_blocked_signal(
                reason,
                planning_failure_present=planning_failure_present,
            )
        ]
    ordered_reasons = sorted(unique_reasons, key=_blocking_signal_sort_key)
    accepted_risk_signals: list[str] = []
    if _user_accepted_degraded_close(run_verdict_dict):
        accepted_risk_signals = [
            reason for reason in ordered_reasons if _risk_acceptance_can_cover_signal(reason)
        ]
        if accepted_risk_signals:
            ordered_reasons = [
                reason
                for reason in ordered_reasons
                if reason not in set(accepted_risk_signals)
            ]
    primary_reasons = [
        reason for reason in ordered_reasons if _is_semantic_primary_signal(reason)
    ]
    secondary_reasons = [
        reason for reason in ordered_reasons if reason not in primary_reasons
    ]
    rejected_reasons = {
        "review_rejected",
        "review_blocked",
        "qa_blocked",
        "slice_drift",
        "continuation_drift",
        "review_failed",
        "qa_failed",
        "run_rejected",
    }

    authoritative_close_state = "eligible_for_done"
    if any(reason in rejected_reasons for reason in ordered_reasons):
        authoritative_close_state = "rejected"
    elif ordered_reasons or run_state == "failed":
        authoritative_close_state = "not_completed"
    elif run_state == "completed":
        authoritative_close_state = "eligible_for_done"

    return {
        "authoritative_close_state": authoritative_close_state,
        "blocking_signals": ordered_reasons[:12],
        "primary_blocking_signals": primary_reasons[:8],
        "secondary_blocking_signals": secondary_reasons[:8],
        "recommended_close_action": _recommended_close_action(ordered_reasons),
        "accepted_risk_signals": accepted_risk_signals[:12],
        "user_risk_acceptance": bool(run_verdict_dict.get("user_risk_acceptance")),
        "failure_origin": failure_origin,
        "run_reason_codes": [
            str(item).strip()
            for item in list(run_verdict_dict.get("reason_codes", []) or [])
            if str(item).strip()
        ][:12],
        "failed_phases": failed_phases[:12],
        "blocked_phases": blocked_phases[:12],
        "pending_phases": pending_phases[:12],
        "completed_phases": completed_phases[:12],
        "can_declare_done": authoritative_close_state == "eligible_for_done",
        "requires_close_rewrite": authoritative_close_state in {"rejected", "not_completed"},
        "prefer_semantic_summary_first": bool(primary_reasons),
    }


def build_lead_close_policy_prompt_block(policy: object) -> str:
    if not isinstance(policy, dict):
        return ""
    authoritative_close_state = str(
        policy.get("authoritative_close_state", "") or ""
    ).strip().lower()
    blocking_signals = [
        str(item).strip()
        for item in list(policy.get("blocking_signals", []) or [])
        if str(item).strip()
    ]
    primary_blocking_signals = [
        str(item).strip()
        for item in list(policy.get("primary_blocking_signals", []) or [])
        if str(item).strip()
    ]
    secondary_blocking_signals = [
        str(item).strip()
        for item in list(policy.get("secondary_blocking_signals", []) or [])
        if str(item).strip()
    ]
    failure_origin = str(policy.get("failure_origin", "") or "").strip().lower()
    run_reason_codes = [
        str(item).strip()
        for item in list(policy.get("run_reason_codes", []) or [])
        if str(item).strip()
    ]
    failed_phases = [
        str(item).strip()
        for item in list(policy.get("failed_phases", []) or [])
        if str(item).strip()
    ]
    blocked_phases = [
        str(item).strip()
        for item in list(policy.get("blocked_phases", []) or [])
        if str(item).strip()
    ]
    pending_phases = [
        str(item).strip()
        for item in list(policy.get("pending_phases", []) or [])
        if str(item).strip()
    ]
    completed_phases = [
        str(item).strip()
        for item in list(policy.get("completed_phases", []) or [])
        if str(item).strip()
    ]
    recommended_close_action = str(
        policy.get("recommended_close_action", "") or ""
    ).strip().lower()
    if not authoritative_close_state:
        return ""

    lines = [
        "== LEAD CLOSE POLICY ==",
        f"authoritative_close_state: {authoritative_close_state}",
        (
            f"blocking_signals: {', '.join(blocking_signals)}"
            if blocking_signals
            else "blocking_signals: none"
        ),
    ]
    if primary_blocking_signals:
        lines.append(
            f"primary_blocking_signals: {', '.join(primary_blocking_signals)}"
        )
    if secondary_blocking_signals:
        lines.append(
            f"secondary_blocking_signals: {', '.join(secondary_blocking_signals)}"
        )
    if failure_origin:
        lines.append(f"failure_origin: {failure_origin}")
    if run_reason_codes:
        lines.append(f"run_reason_codes: {', '.join(run_reason_codes)}")
    if failed_phases:
        lines.append(f"failed_phases: {', '.join(failed_phases)}")
    if blocked_phases:
        lines.append(f"blocked_phases: {', '.join(blocked_phases)}")
    if pending_phases:
        lines.append(f"pending_phases: {', '.join(pending_phases)}")
    if completed_phases:
        lines.append(f"completed_phases: {', '.join(completed_phases)}")
    if recommended_close_action:
        lines.append(f"recommended_close_action: {recommended_close_action}")
    if authoritative_close_state in {"rejected", "not_completed"}:
        lines.extend(
            [
                "Regla: NO declares DONE, completed, QA aprobada, ni proyecto recuperado.",
                (
                    "Si hay primary_blocking_signals, debes explicarlas primero como causa autoritativa; "
                    "problemas de routing, 429, fallback o capacidad van despues como contexto secundario."
                    if primary_blocking_signals
                    else "Si aparecen problemas de routing, 429, fallback o capacidad, tratalos como contexto secundario salvo que no exista un bloqueo semantico."
                ),
                "La causa raiz debe salir de failure_origin, run_reason_codes, failed_phases, blocked_phases, pending_phases o primary_blocking_signals de esta run actual; no cites bloqueos historicos o routing viejo como causa primaria si no aparecen aqui.",
                "Si primary_blocking_signals incluye review_rejected, la causa raiz es rechazo de review y el siguiente paso generico es rework desde findings; no lo reemplaces por routing, adaptadores, cuotas, 429 o modelos no disponibles.",
                "No uses [PAUSE_FOR_USER] para routing/capacidad si existe una causa semantica autoritativa como review_rejected; replanifica, pide rework o cierra rechazado con siguiente paso interno.",
                "No describas una fase de completed_phases como fallida, bloqueada, truncada o causa raiz de esta run.",
                "Debes describir la run como rechazada o no completada, enumerar bloqueos reales y proponer el siguiente paso.",
                "La primera linea de tu respuesta debe empezar por 'Estado autoritativo:' o 'Run rechazada:'.",
            ]
        )
    else:
        lines.extend(
            [
                "Regla: solo puedes declarar DONE si review y qa estan realmente cerradas y no hay drift, bloqueos ni rechazos.",
                "Si detectas cualquier contradiccion en fases o evidencias, baja el cierre a no completada.",
            ]
        )
    lines.append("== FIN LEAD CLOSE POLICY ==")
    return "\n".join(lines)
