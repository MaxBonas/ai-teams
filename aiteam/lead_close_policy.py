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
        "review_failed",
        "qa_failed",
    }
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
    if normalized_role == "engineer":
        return "build"
    if normalized_role == "reviewer":
        return "review"
    if normalized_role == "qa":
        return "qa"
    if "review" in normalized_phase:
        return "review"
    if "qa" in normalized_phase:
        return "qa"
    if any(h in normalized_phase for h in _ENGINEER_PHASE_HINTS):
        return "build"
    return ""


def _select_primary_gate_verdict(
    verdicts: dict[str, dict[str, Any]],
    gate_kind: str,
) -> dict[str, Any]:
    normalized_gate = str(gate_kind or "").strip().lower()
    if not normalized_gate:
        return {}
    explicit = dict(verdicts.get(normalized_gate, {}) or {})
    if explicit:
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

    reason_codes: list[str] = []

    # ── 0. Detect missing implementation phase ────────────────────────────────
    # A run that only contains researcher/scout phases with no engineer/build
    # phase is structurally incomplete: it diagnosed but never implemented.
    # Checking this early (before timing-dependent signals) ensures the Lead
    # cannot declare DONE when the run produced zero implementation work.
    _all_executed = (
        set(verdicts.keys())
        | set(states.keys())
        | set(_phase_outputs_dict.keys())
    ) - _SKIP_PHASES
    if _all_executed:
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
    if build_contract_status == "drift" or "slice_drift" in build_reasons:
        reason_codes.append("slice_drift")

    # ── 2. Sweep ALL remaining phase verdicts for blocked/failed status ───────
    # Continuation runs may use custom phase names (e.g. "engineer_toc_implementation")
    # that do not match the hardcoded keys above.  If the engineer emitted a
    # structured [PHASE_VERDICT] block with status=blocked/failed, it ends up here.
    for _pv_id, _pv in verdicts.items():
        if _pv_id in _ALREADY_CHECKED_PHASES or _pv_id in _SKIP_PHASES:
            continue
        _pv_status = str((_pv or {}).get("status", "") or "").strip().lower()
        if _pv_status in {"blocked", "failed", "rejected"}:
            reason_codes.append(f"{_safe_reason_label(_pv_id)}_blocked")

    # ── 3. Phase states from taskboard ───────────────────────────────────────
    for phase_id, state in sorted(states.items()):
        if phase_id in _SKIP_PHASES:
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
        if _po_id in _SKIP_PHASES:
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
    primary_reasons = [
        reason for reason in ordered_reasons if _is_semantic_primary_signal(reason)
    ]
    secondary_reasons = [
        reason for reason in ordered_reasons if reason not in primary_reasons
    ]
    rejected_reasons = {
        "review_rejected",
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
