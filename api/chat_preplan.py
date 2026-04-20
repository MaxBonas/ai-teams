import re

from pathlib import Path

from aiteam.chat_runtime import ChatRunState
from aiteam.context_curator import (
    ContextCuratorStore,
    estimate_context_compaction_value,
    estimate_context_pressure,
    project_key_from_runtime_dir,
)
from aiteam.phase_verdicts import is_missing_contract_objective
from aiteam.workflow_planner import PhaseSpec

from api.chat_delegate import _extract_evidence_plan, _summarize_delegate_economics
from api.chat_observability import _coerce_delegate_batches, _coerce_phase_evidence_plan
from api.utils import _load_chat_specialist_insights, _read_runtime_workflow_state


def _message_suggests_browser_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    strong_browser_patterns = (
        r"\bfrontend\b",
        r"\bui\b",
        r"\bux\b",
        r"\bbrowser\b",
        r"\bplaywright\b",
        r"\bdom\b",
        r"\bselector\b",
        r"\bscreenshot\b",
        r"\bscrape\b",
        r"\bscraping\b",
        r"\bscreen\b",
        r"\bform\b",
        r"\breact\b",
        r"\bvite\b",
        r"\bcomponent\b",
        r"\bwebapp\b",
        r"\bwebsite\b",
    )
    if any(re.search(pattern, normalized) for pattern in strong_browser_patterns):
        return True

    weak_visual_patterns = (r"\bcss\b", r"\bhtml\b", r"\bpage\b", r"\bweb\b")
    if not any(re.search(pattern, normalized) for pattern in weak_visual_patterns):
        return False

    browser_context_terms = (
        "browser flow",
        "browser repro",
        "visual",
        "render",
        "interactive",
        "frontend",
        "ui",
        "ux",
        "dom",
        "playwright",
        "react",
        "vite",
        "website",
        "webapp",
    )
    return any(term in normalized for term in browser_context_terms)


def _message_suggests_security_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    security_terms = (
        "security",
        "seguridad",
        "secure",
        "vulnerability",
        "vulnerabilidad",
        "semgrep",
        "sast",
        "audit",
        "auditoria",
        "compliance",
        "hardening",
        "auth",
        "authentication",
        "authorization",
        "secret",
        "credential",
        "token",
        "xss",
        "csrf",
        "sql injection",
        "owasp",
    )
    return any(term in normalized for term in security_terms)


def _message_suggests_research_surface(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    research_terms = (
        "research",
        "investiga",
        "investigar",
        "documentacion",
        "documentation",
        "docs",
        "api reference",
        "best practices",
        "ground truth",
        "context7",
        "perplexity",
        "look up",
        "lookup",
        "manual",
        "spec",
        "specification",
        "integration guide",
    )
    return any(term in normalized for term in research_terms)


def _detect_preplan_surface_hints(message: str) -> dict[str, object]:
    surfaces: list[str] = []
    recommended_delegate_intents: list[str] = []
    recommended_specialists: list[str] = []

    if _message_suggests_browser_surface(message):
        surfaces.append("browser")
        recommended_delegate_intents.append("delegate_browser_repro")
        recommended_specialists.extend(["browser_operator", "skill_worker"])
    if _message_suggests_security_surface(message):
        surfaces.append("security")
        recommended_delegate_intents.append("delegate_mcp_probe")
        recommended_specialists.extend(["skill_worker", "repo_scout"])
    if _message_suggests_research_surface(message):
        surfaces.append("research")
        recommended_delegate_intents.append("delegate_mcp_probe")
        recommended_specialists.extend(["skill_worker", "repo_scout", "lsp_navigator"])

    if not surfaces:
        surfaces.append("general")
        recommended_delegate_intents.append("delegate_repo_scan")
        recommended_specialists.append("repo_scout")

    return {
        "surfaces": list(dict.fromkeys(surfaces)),
        "recommended_delegate_intents": list(dict.fromkeys(recommended_delegate_intents)),
        "recommended_specialists": list(dict.fromkeys(recommended_specialists)),
    }


def _estimate_preplan_context_pressure(
    *,
    runtime_dir: Path,
    continuation_requested: bool,
    continuation_of: str,
    continuation_snapshot: str,
) -> dict[str, object]:
    previous_delegate_batches = 0
    previous_phase_summaries = 0
    previous_specialist_reports = 0
    previous_invalidations = 0
    previous_open_questions = 0
    previous_phase_outputs: dict[str, object] = {}
    previous_phase_context_summaries: dict[str, object] = {}
    previous_project_context_summary = ""
    previous_chat_context_summary = ""

    if continuation_of:
        workflow_payload = _read_runtime_workflow_state(runtime_dir)
        if isinstance(workflow_payload, dict):
            previous_entry = workflow_payload.get(continuation_of, {})
            if isinstance(previous_entry, dict):
                previous_delegate_batches = len(
                    _coerce_delegate_batches(previous_entry.get("delegate_batches", []))
                )
                previous_phase_context_summaries = dict(
                    previous_entry.get("phase_context_summaries", {}) or {}
                )
                previous_phase_summaries = len(previous_phase_context_summaries)
                previous_phase_outputs = dict(previous_entry.get("phase_outputs", {}) or {})
                previous_project_context_summary = str(
                    previous_entry.get("project_context_summary", "") or ""
                )
                previous_chat_context_summary = str(
                    previous_entry.get("chat_context_summary", "") or ""
                )
        previous_specialist_reports = len(
            list(
                _load_chat_specialist_insights(runtime_dir, continuation_of).get(
                    "specialist_reports",
                    [],
                )
                or []
            )
        )
        curator_store = ContextCuratorStore(runtime_dir)
        chat_context = curator_store.load_chat_context(
            continuation_of,
            project_key=project_key_from_runtime_dir(runtime_dir),
        )
        previous_invalidations = len(list(chat_context.get("invalidations", []) or []))
        previous_open_questions = len(list(chat_context.get("open_questions", []) or []))

    pressure = estimate_context_pressure(
        continuation_requested=continuation_requested,
        continuation_snapshot=continuation_snapshot,
        phase_summary_count=previous_phase_summaries,
        delegate_batch_count=previous_delegate_batches,
        specialist_report_count=previous_specialist_reports,
        invalidation_count=previous_invalidations,
        open_question_count=previous_open_questions,
    )
    compaction_value = estimate_context_compaction_value(
        phase_outputs=previous_phase_outputs,
        project_context_summary=previous_project_context_summary,
        chat_context_summary=previous_chat_context_summary,
        phase_context_summaries=previous_phase_context_summaries,
    )
    merged_signals = list(pressure.get("signals", []) or [])
    value_level = str(compaction_value.get("level", "") or "").strip().lower()
    if value_level in {"medium", "high"}:
        signal_name = f"context_compaction_value_{value_level}"
        if signal_name not in merged_signals:
            merged_signals.append(signal_name)
    merged = dict(pressure)
    merged["signals"] = merged_signals
    merged["recommend_context_curator"] = bool(
        pressure.get("recommend_context_curator", False)
        or compaction_value.get("priority_boost", False)
    )
    merged["context_compaction"] = dict(compaction_value)
    return merged


def _build_preplan_signal_block(surface_hints: dict[str, object]) -> str:
    surfaces = [
        str(item).strip().lower()
        for item in list(surface_hints.get("surfaces", []) or [])
        if str(item).strip()
    ]
    intents = [
        str(item).strip().lower()
        for item in list(surface_hints.get("recommended_delegate_intents", []) or [])
        if str(item).strip()
    ]
    specialists = [
        str(item).strip().lower()
        for item in list(surface_hints.get("recommended_specialists", []) or [])
        if str(item).strip()
    ]
    if not surfaces:
        return ""
    return (
        "\n\n[PREPLAN_SIGNALS]\n"
        f"surfaces={', '.join(surfaces)}\n"
        f"recommended_delegate_intents={', '.join(intents)}\n"
        f"recommended_specialists={', '.join(specialists)}\n"
        "Usa estas señales como pista previa al plan: si te ayudan, delega barato y "
        "evita cargar el contexto pesado en el Lead. No son obligatorias.\n"
        "[/PREPLAN_SIGNALS]"
    )


def _build_context_curator_prompt(
    *,
    message: str,
    surface_hints: dict[str, object],
    project_state_raw: str,
    session_history_raw: str,
    continuation_target_context: str = "",
) -> str:
    surfaces = ", ".join(
        [
            str(item).strip().lower()
            for item in list(surface_hints.get("surfaces", []) or [])
            if str(item).strip()
        ]
    ) or "general"
    continuation_block = (
        "[CONTINUATION_TARGET]\n"
        f"{continuation_target_context}\n"
        "[/CONTINUATION_TARGET]\n\n"
        if continuation_target_context
        else ""
    )
    return (
        "Compacta el contexto del proyecto para el Team Lead en maximo 8 lineas utiles.\n"
        f"Solicitud del usuario: {message[:180]}\n"
        f"Superficies detectadas: {surfaces}\n\n"
        "Prioriza solo lo relevante para decidir el plan inicial: hechos, archivos/areas "
        "probables, riesgos y senales utiles. Sin teoria y sin transcripts crudos.\n"
        "Separa explicitamente en tu resumen: (1) estado actual confirmado del workspace, "
        "(2) contexto historico util, (3) huecos que requieren revalidacion.\n"
        "NO promociones historial, lead memory o runs previas a hechos confirmados del estado actual.\n"
        "Si existe CONTINUATION TARGET PRIORITARIO, ese target manda sobre objetivos historicos mas antiguos.\n\n"
        f"{continuation_block}"
        "[PROJECT_STATE]\n"
        f"{project_state_raw}\n"
        "[/PROJECT_STATE]\n\n"
        "[SESSION_HISTORY]\n"
        f"{session_history_raw}\n"
        "[/SESSION_HISTORY]"
    )


def _context_project_key(workspace: Path) -> str:
    return str(workspace.resolve())


def _persist_preplan_context(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    user_message: str,
    surface_hints: dict[str, object],
    curator_summary: str,
    lead_summary: str,
    source_task_ids: list[str],
) -> tuple[dict[str, object], dict[str, object]]:
    store = ContextCuratorStore(runtime_dir)
    return store.remember_preplan(
        project_key=_context_project_key(workspace),
        chat_root=task_root,
        user_message=user_message,
        surface_hints=surface_hints,
        curator_summary=curator_summary,
        lead_summary=lead_summary,
        source_task_ids=source_task_ids,
    )


def _build_curated_context_block(
    *,
    runtime_dir: Path,
    workspace: Path,
    continuation_of: str = "",
) -> str:
    store = ContextCuratorStore(runtime_dir)
    parts: list[str] = []
    def _summary_for_sections(
        payload: dict[str, object],
        *,
        sections: set[str],
        drop_working_set: bool,
    ) -> str:
        filtered_payload = {
            key: value
            for key, value in payload.items()
        }
        for section_name in (
            "working_set",
            "durable_facts",
            "decisions",
            "historical_context",
            "hypotheses",
            "open_questions",
            "invalidations",
            "next_actions",
        ):
            if section_name not in sections:
                filtered_payload[section_name] = []
        summary = store.build_summary(filtered_payload)
        if not drop_working_set:
            return summary
        lines = []
        for line in str(summary or "").splitlines():
            normalized = str(line).strip()
            if not normalized or normalized.startswith("working_set:"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    project_payload = store.load_project_context(_context_project_key(workspace))
    project_summary = _summary_for_sections(
        project_payload,
        sections={"durable_facts", "decisions", "working_set", "next_actions", "open_questions"},
        drop_working_set=False,
    )
    project_historical_summary = _summary_for_sections(
        project_payload,
        sections={"historical_context", "hypotheses"},
        drop_working_set=True,
    )
    continuation_root = str(continuation_of or "").strip()
    chat_summary = ""
    chat_historical_summary = ""
    if continuation_root:
        chat_payload = store.load_chat_context(
            continuation_root,
            project_key=_context_project_key(workspace),
        )
        chat_summary = _summary_for_sections(
            chat_payload,
            sections={"durable_facts", "decisions", "working_set", "next_actions", "open_questions"},
            drop_working_set=True,
        )
        chat_historical_summary = _summary_for_sections(
            chat_payload,
            sections={"historical_context", "hypotheses"},
            drop_working_set=True,
        )
    if continuation_root:
        project_summary = _summary_for_sections(
            project_payload,
            sections={"durable_facts", "decisions", "working_set", "next_actions", "open_questions"},
            drop_working_set=True,
        )
    if not project_summary and chat_summary:
        filtered_lines = [
            line
            for line in chat_summary.splitlines()
            if not str(line).strip().startswith("next_actions:")
        ]
        project_summary = "\n".join(filtered_lines[:3]).strip()
    if project_summary:
        parts.append("Contexto curado del proyecto (actual y operativo):")
        parts.extend(f"- {line}" for line in project_summary.splitlines())
    if project_historical_summary:
        parts.append("Contexto historico del proyecto (no confirmado; revalidar antes de usar):")
        parts.extend(f"- {line}" for line in project_historical_summary.splitlines())
    if continuation_root and chat_summary:
        parts.append(f"Contexto curado de {continuation_root} (actual y operativo):")
        parts.extend(f"- {line}" for line in chat_summary.splitlines())
    if continuation_root and chat_historical_summary:
        parts.append(f"Contexto historico de {continuation_root} (no confirmado; revalidar antes de usar):")
        parts.extend(f"- {line}" for line in chat_historical_summary.splitlines())
    return ("\n" + "\n".join(parts)) if parts else ""


def _record_context_invalidation(
    *,
    runtime_dir: Path,
    workspace: Path,
    task_root: str,
    reason: str,
    affected_phases: list[str],
    source_task_ids: list[str],
) -> tuple[str, str]:
    store = ContextCuratorStore(runtime_dir)
    project_ctx, chat_ctx = store.remember_invalidation(
        project_key=_context_project_key(workspace),
        chat_root=task_root,
        reason=reason,
        affected_phases=affected_phases,
        source_task_ids=source_task_ids,
    )
    return store.build_summary(project_ctx), store.build_summary(chat_ctx)


def _synthesize_default_phase_evidence_plan(
    phases: list[PhaseSpec],
    *,
    message: str,
    run_mode: str,
    close_pending_mode: bool = False,
) -> dict[str, dict[str, object]]:
    """Fallback conservador cuando el Lead no define EVIDENCE_PLAN.

    Prioriza evidencia barata y operativa para builds estandar sin cargar tokens
    del Team Lead con transcripts extensos de tools.
    """

    if run_mode != "standard":
        return {}

    browser_surface = _message_suggests_browser_surface(message)
    security_surface = _message_suggests_security_surface(message)
    research_surface = _message_suggests_research_surface(message)
    plan: dict[str, dict[str, object]] = {}
    for spec in phases:
        phase_id = str(spec.phase_id or "").strip()
        if not phase_id:
            continue
        intents: list[str] = []
        wait_policy = "best_effort"
        delegate_budget = 2
        phase_is_planning = phase_id.lower().startswith("plan_")

        if close_pending_mode:
            if spec.role == "RESEARCHER" or phase_id in {
                "discovery",
                "research",
                "analysis",
                "investigate",
            }:
                intents.append("delegate_repo_scan")
            elif spec.role == "ENGINEER" or phase_id == "build":
                intents.append("delegate_test_run")
                delegate_budget = 1
            elif spec.role == "REVIEWER" or phase_id == "review":
                intents.append("delegate_repo_scan")
                delegate_budget = 1
            elif spec.role == "QA" or phase_id == "qa":
                intents.append("delegate_test_run")
                delegate_budget = 1
            if intents:
                plan[phase_id] = {
                    "delegate_intents": list(dict.fromkeys(intents)),
                    "wait_policy": wait_policy,
                    "delegate_budget": delegate_budget,
                }
            continue

        if phase_is_planning:
            intents.append("delegate_repo_scan")
            plan[phase_id] = {
                "delegate_intents": list(dict.fromkeys(intents)),
                "wait_policy": wait_policy,
                "delegate_budget": 1,
            }
            continue

        if spec.role == "RESEARCHER" or phase_id in {
            "discovery",
            "research",
            "analysis",
            "investigate",
        }:
            intents.append("delegate_repo_scan")
            if research_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "ENGINEER" or phase_id == "build":
            intents.append("delegate_test_run")
            wait_policy = "quorum"
            delegate_budget = 4
            if browser_surface:
                intents.append("delegate_browser_repro")
            if security_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "REVIEWER" or phase_id == "review":
            intents.append("delegate_lsp_impact")
            if security_surface or research_surface:
                intents.append("delegate_mcp_probe")
        elif spec.role == "QA" or phase_id == "qa":
            intents.append("delegate_test_run")
            wait_policy = "quorum"
            delegate_budget = 3
            if browser_surface:
                intents.append("delegate_browser_repro")
            if security_surface:
                intents.append("delegate_mcp_probe")

        if intents:
            plan[phase_id] = {
                "delegate_intents": list(dict.fromkeys(intents)),
                "wait_policy": wait_policy,
                "delegate_budget": delegate_budget,
            }
    return plan


def _resolve_phase_evidence_plan(
    *,
    lead_output: str,
    phases: list[PhaseSpec],
    message: str,
    run_mode: str,
    close_pending_mode: bool = False,
) -> tuple[dict[str, dict[str, object]], str]:
    explicit_plan = _coerce_phase_evidence_plan(_extract_evidence_plan(lead_output))
    if explicit_plan:
        return explicit_plan, "lead"
    return _synthesize_default_phase_evidence_plan(
        phases,
        message=message,
        run_mode=run_mode,
        close_pending_mode=close_pending_mode,
    ), "default"


def _sync_chat_runtime_state(
    orch,
    *,
    task_root: str,
    chat_run_state: ChatRunState,
    lead_run_mode: str,
    delegated_task_ids: list[str] | None = None,
    evidence_plan_source: str = "",
) -> None:
    ws = orch._get_workflow_state(task_root)
    ws["phase_contracts"] = _build_phase_contracts(chat_run_state.phases)
    ws["phase_evidence_plan"] = _coerce_phase_evidence_plan(
        chat_run_state.phase_evidence_plan
    )
    ws["lead_run_mode"] = str(lead_run_mode or "standard").strip() or "standard"
    ws["phase_task_ids"] = chat_run_state.phase_task_ids
    ws["workflow_phase_keys"] = chat_run_state.workflow_phase_keys
    if delegated_task_ids is not None:
        ws["delegated_task_ids"] = list(delegated_task_ids)
    ws.setdefault("delegate_batches", [])
    ws["delegate_economics_summary"] = _summarize_delegate_economics(
        _coerce_delegate_batches(ws.get("delegate_batches", []))
    )
    if evidence_plan_source:
        ws["evidence_plan_source"] = evidence_plan_source
    orch._save_workflow_state()


def _build_phase_contracts(phases: list[PhaseSpec]) -> dict[str, dict[str, object]]:
    contracts: dict[str, dict[str, object]] = {}
    for spec in phases:
        phase_id = str(spec.phase_id or "").strip()
        if not phase_id:
            continue
        objective = str(spec.objective or "").strip()
        contracts[phase_id] = {
            "phase_id": phase_id,
            "role": str(spec.role or "").strip(),
            "objective": "" if is_missing_contract_objective(objective) else objective,
            "depends_on": [
                str(dep).strip()
                for dep in list(spec.depends_on or [])
                if str(dep).strip()
            ],
        }
    return contracts
