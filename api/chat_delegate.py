from pathlib import Path

from aiteam.autotools import AutoToolIntegrator
from aiteam.lead_control import (
    extract_delegate_request as _lead_control_extract_delegate_request,
    extract_evidence_plan as _lead_control_extract_evidence_plan,
)
from aiteam.tool_specialists import (
    build_tool_specialist_metadata,
    replacement_specialists_from_metadata,
)
from aiteam.types import Complexity, Criticality, Role, WorkTask

from api.chat_logic import _safe_int_value
from api.chat_observability import _coerce_delegate_batches
from api.chat_quality import _compact_delegated_result
from api.utils import (
    PROJECT_ROOT,
    _build_scout_project_state_context,
    _build_scout_session_history_context,
)


def _extract_delegate_request(text: str):
    return _lead_control_extract_delegate_request(text)


def _extract_evidence_plan(text: str) -> dict[str, dict[str, object]]:
    return _lead_control_extract_evidence_plan(text)


def _resolve_delegate_plan(delegate_request) -> dict[str, object]:
    intent = str(getattr(delegate_request, "intent", "delegate") or "delegate").strip().lower()
    plans: dict[str, dict[str, object]] = {
        "delegate": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "phase_prefix": "delegate_scout",
            "title_prefix": "Delegate scout",
            "instruction": "Inspecciona el repositorio y el contexto local para responder con hechos concretos.",
        },
        "delegate_repo_scan": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "phase_prefix": "delegate_repo_scan",
            "title_prefix": "Delegate repo scan",
            "instruction": "Recorre archivos, estructura, git y pistas locales; devuelve un mapa corto y factual.",
        },
        "delegate_browser_repro": {
            "role": Role.QA,
            "specialist": "browser_operator",
            "required_capabilities": ["browser_test", "browser_nav"],
            "phase_prefix": "delegate_browser_repro",
            "title_prefix": "Delegate browser repro",
            "instruction": "Reproduce el flujo en navegador, Playwright o MCP UI y devuelve solo pasos, resultado y evidencia compacta.",
        },
        "delegate_lsp_impact": {
            "role": Role.RESEARCHER,
            "specialist": "lsp_navigator",
            "required_capabilities": ["lsp_symbols", "lsp_references"],
            "phase_prefix": "delegate_lsp_impact",
            "title_prefix": "Delegate LSP impact",
            "instruction": "Usa navegacion semantica y referencias para resumir impacto, hotspots y dependencias.",
        },
        "delegate_test_run": {
            "role": Role.QA,
            "specialist": "test_runner",
            "required_capabilities": ["test_execute", "build_execute"],
            "phase_prefix": "delegate_test_run",
            "title_prefix": "Delegate test run",
            "instruction": "Ejecuta checks o tests relevantes y resume fallos, evidencia y cobertura util.",
        },
        "delegate_mcp_probe": {
            "role": Role.SCOUT,
            "specialist": "mcp_operator",
            "required_capabilities": ["external_mcp"],
            "phase_prefix": "delegate_mcp_probe",
            "title_prefix": "Delegate MCP probe",
            "instruction": "Usa el MCP o integracion externa asignada y devuelve resultados estructurados y compactos.",
        },
    }
    return dict(plans.get(intent, plans["delegate"]))


def _resolve_delegate_round_budget(delegate_request) -> int:
    requested = _safe_int_value(getattr(delegate_request, "delegate_budget", 3), 3)
    wait_policy = str(getattr(delegate_request, "wait_policy", "all") or "all").strip().lower()
    budget = max(1, min(requested, 6))
    if wait_policy == "best_effort":
        budget = max(1, min(budget, 2))
    elif wait_policy == "quorum":
        budget = max(2, min(budget, 4))
    return budget


def _is_delegate_phase_name(phase_name: str) -> bool:
    return str(phase_name or "").strip().lower().startswith("delegate_")


def _is_supporting_control_phase(phase_name: str) -> bool:
    normalized = str(phase_name or "").strip().lower()
    return normalized == "lead_intake" or _is_delegate_phase_name(normalized)


def _delegate_specialist_plan(specialist: str) -> dict[str, object]:
    catalog: dict[str, dict[str, object]] = {
        "repo_scout": {
            "role": Role.SCOUT,
            "specialist": "repo_scout",
            "required_capabilities": ["repo_read"],
            "instruction": "Inspecciona repositorio, archivos, git y contexto local; devuelve hechos compactos.",
        },
        "browser_operator": {
            "role": Role.QA,
            "specialist": "browser_operator",
            "required_capabilities": ["browser_test", "browser_nav"],
            "instruction": "Opera navegador, Playwright o MCP UI y resume pasos reproducidos, resultado y evidencia compacta.",
        },
        "lsp_navigator": {
            "role": Role.RESEARCHER,
            "specialist": "lsp_navigator",
            "required_capabilities": ["lsp_symbols", "lsp_references"],
            "instruction": "Usa navegacion semantica para resumir impacto, referencias y hotspots.",
        },
        "test_runner": {
            "role": Role.QA,
            "specialist": "test_runner",
            "required_capabilities": ["test_execute", "build_execute"],
            "instruction": "Ejecuta checks o tests relevantes y resume fallos, evidencia y regresiones.",
        },
        "mcp_operator": {
            "role": Role.SCOUT,
            "specialist": "mcp_operator",
            "required_capabilities": ["external_mcp"],
            "instruction": "Usa MCPs o integraciones externas y devuelve resultados estructurados, compactos y sin transcripts crudos.",
        },
        "skill_worker": {
            "role": Role.SCOUT,
            "specialist": "skill_worker",
            "required_capabilities": ["skill_run"],
            "instruction": "Ejecuta la skill o playbook asignado y devuelve evidencia compacta, hallazgos y recomendacion operativa.",
        },
    }
    return dict(catalog.get(specialist, catalog["repo_scout"]))


def _delegate_specialist_targets(
    *,
    intent: str,
    specialist: str,
) -> tuple[list[str], list[str]]:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    skill_targets: list[str] = []
    lsp_targets: list[str] = []

    if normalized_specialist == "browser_operator" or normalized_intent == "delegate_browser_repro":
        skill_targets.append("playwright_qa_skill")
    if normalized_specialist == "lsp_navigator" or normalized_intent == "delegate_lsp_impact":
        lsp_targets.extend(["symbols", "references", "impact"])

    return list(dict.fromkeys(skill_targets)), list(dict.fromkeys(lsp_targets))


def _delegate_report_contract(
    *,
    intent: str,
    specialist: str,
) -> str:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    if normalized_specialist == "browser_operator" or normalized_intent == "delegate_browser_repro":
        return (
            "Formato obligatorio: summary, steps_reproduced, result, evidence, artifacts, risks, recommendation. "
            "Si usas navegador, Playwright o MCP UI, devuelve solo pasos reproducidos y evidencia compacta; no pegues transcripts crudos."
        )
    if normalized_specialist == "mcp_operator" or normalized_intent == "delegate_mcp_probe":
        return (
            "Formato obligatorio: summary, result, evidence, artifacts, risks, recommendation. "
            "Si operas un MCP de UI, incluye tambien steps_reproduced compactos; no pegues transcripts crudos."
        )
    return "Formato obligatorio: summary, evidence, artifacts, risks, recommendation compactos."


def _delegate_catalog_capabilities(
    *,
    intent: str,
    specialist: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    query: str = "",
) -> set[str]:
    normalized_intent = str(intent or "").strip().lower()
    normalized_specialist = str(specialist or "").strip().lower()
    normalized_query = str(query or "").strip().lower()
    caps: set[str] = set()

    for capability in list(required_capabilities or []):
        capability_name = str(capability or "").strip().lower()
        if capability_name in {"browser_nav", "browser_test"}:
            caps.update({"browser_testing", "e2e", "web_automation"})
        elif capability_name in {"test_execute", "build_execute"}:
            caps.update({"code_quality"})
        elif capability_name == "repo_read":
            caps.update({"research"})

    if normalized_intent == "delegate_browser_repro":
        caps.update({"browser_testing", "e2e", "web_automation"})
    if normalized_intent == "delegate_lsp_impact":
        caps.update({"research"})
    if normalized_intent == "delegate_test_run":
        caps.update({"code_quality"})
    if normalized_specialist == "mcp_operator" or normalized_intent == "delegate_mcp_probe":
        if any(token in normalized_query for token in ("semgrep", "sast", "security", "vulnerability")):
            caps.update({"security_scan", "sast", "code_quality"})
        if any(token in normalized_query for token in ("playwright", "browser", "ui", "e2e", "web")):
            caps.update({"browser_testing", "e2e", "web_automation"})
        if any(token in normalized_query for token in ("perplexity", "context7", "docs", "documentation", "research")):
            caps.update({"research", "documentation", "ground_truth"})
    return caps


def _resolve_delegate_rewiring(
    *,
    workspace: Path | None,
    intent: str,
    specialist: str,
    required_capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    query: str = "",
) -> dict[str, object]:
    normalized_specialist = str(specialist or "").strip().lower()
    if normalized_specialist != "mcp_operator":
        return {}

    project_root = Path(workspace or PROJECT_ROOT)
    integrator = AutoToolIntegrator(
        runtime_dir=project_root / "runtime",
        project_root=project_root,
    )
    discovery_caps = _delegate_catalog_capabilities(
        intent=intent,
        specialist=specialist,
        required_capabilities=required_capabilities,
        query=query,
    )
    if not discovery_caps:
        return {}

    suggestions = integrator.suggest_requirements(discovery_caps, limit=3)
    replacements = [
        row for row in suggestions if str(row.get("replacement_for", "") or "").strip()
    ]
    if not replacements:
        return {}

    candidate_names = [
        str(row.get("name", "") or "").strip().lower()
        for row in replacements
        if str(row.get("name", "") or "").strip()
    ]
    replacement_for = sorted(
        {
            str(row.get("replacement_for", "") or "").strip().lower()
            for row in replacements
            if str(row.get("replacement_for", "") or "").strip()
        }
    )
    replacement_specialists = replacement_specialists_from_metadata(
        {"tool_rewiring_candidates": candidate_names}
    )
    preferred_specialist = replacement_specialists[0] if replacement_specialists else ""
    skill_targets = [name for name in candidate_names if name.endswith("_skill")]
    lsp_targets: list[str] = []
    if not preferred_specialist:
        return {}

    return {
        "tool_rewiring_active": True,
        "tool_rewiring_candidates": candidate_names,
        "tool_rewiring_replacement_for": replacement_for,
        "tool_rewiring_preferred_specialist": preferred_specialist,
        "tool_rewiring_suppress_mcp_operator": True,
        "tool_rewiring_reason": "delegate_catalog_replacement_preferred_over_mcp_operator",
        "skill_targets": list(dict.fromkeys(skill_targets)),
        "lsp_targets": lsp_targets,
    }


def _build_delegate_request(intent: str, *, query: str, wait_policy: str, delegate_budget: int):
    class _DelegateRequest:
        def __init__(self, intent: str, query: str, wait_policy: str, delegate_budget: int) -> None:
            self.intent = intent
            self.query = query
            self.wait_policy = wait_policy
            self.delegate_budget = delegate_budget

    return _DelegateRequest(
        intent=str(intent or "").strip().lower(),
        query=str(query or "").strip(),
        wait_policy=str(wait_policy or "all").strip().lower(),
        delegate_budget=max(1, int(delegate_budget or 3)),
    )


def _resolve_delegate_assignments(
    delegate_request,
    *,
    workspace: Path | None = None,
) -> list[dict[str, object]]:
    primary = _resolve_delegate_plan(delegate_request)
    intent = str(getattr(delegate_request, "intent", "delegate") or "delegate").strip().lower()
    wait_policy = str(getattr(delegate_request, "wait_policy", "all") or "all").strip().lower()
    delegate_query = str(getattr(delegate_request, "query", "") or "").strip()
    roster_by_intent: dict[str, list[str]] = {
        "delegate": ["repo_scout", "lsp_navigator"],
        "delegate_repo_scan": ["repo_scout", "lsp_navigator"],
        "delegate_browser_repro": ["browser_operator", "mcp_operator", "test_runner", "repo_scout"],
        "delegate_lsp_impact": ["lsp_navigator", "repo_scout", "test_runner"],
        "delegate_test_run": ["test_runner", "repo_scout", "lsp_navigator"],
        "delegate_mcp_probe": ["mcp_operator", "repo_scout"],
    }
    roster = list(roster_by_intent.get(intent, [str(primary.get("specialist", "repo_scout"))]))
    if wait_policy == "best_effort":
        selected = roster[:1]
    elif wait_policy == "quorum":
        selected = roster[: min(len(roster), 3)]
    else:
        selected = roster

    assignments: list[dict[str, object]] = []
    for specialist_name in selected:
        specialist_plan = _delegate_specialist_plan(specialist_name)
        rewiring = _resolve_delegate_rewiring(
            workspace=workspace,
            intent=intent,
            specialist=specialist_name,
            required_capabilities=list(specialist_plan.get("required_capabilities", []) or []),
            query=delegate_query,
        )
        effective_specialist = str(
            rewiring.get("tool_rewiring_preferred_specialist", specialist_name) or specialist_name
        ).strip().lower()
        if effective_specialist != str(specialist_name or "").strip().lower():
            specialist_plan = _delegate_specialist_plan(effective_specialist)
        skill_targets, lsp_targets = _delegate_specialist_targets(
            intent=intent,
            specialist=effective_specialist,
        )
        skill_targets = list(
            dict.fromkeys(
                list(skill_targets)
                + [
                    str(item).strip()
                    for item in list(rewiring.get("skill_targets", []) or [])
                    if str(item).strip()
                ]
            )
        )
        lsp_targets = list(
            dict.fromkeys(
                list(lsp_targets)
                + [
                    str(item).strip()
                    for item in list(rewiring.get("lsp_targets", []) or [])
                    if str(item).strip()
                ]
            )
        )
        base_instruction = str(
            specialist_plan.get("instruction") or primary.get("instruction", "Responde con hechos concretos.")
        )
        if rewiring:
            base_instruction = (
                "El catalogo marca un replacement preferente sobre MCP para esta consulta. "
                "Opera la ruta de replacement asignada y devuelve evidencia compacta.\n"
                f"{base_instruction}"
            )
        assignments.append(
            {
                **specialist_plan,
                "phase_prefix": primary.get("phase_prefix", "delegate_scout"),
                "title_prefix": primary.get("title_prefix", "Delegate specialist"),
                "instruction": base_instruction,
                "skill_targets": skill_targets,
                "lsp_targets": lsp_targets,
                "rewired_from_specialist": (
                    str(specialist_name or "").strip().lower()
                    if rewiring and effective_specialist != str(specialist_name or "").strip().lower()
                    else ""
                ),
                **rewiring,
            }
        )
    return assignments


def _delegate_quorum_target(wait_policy: str, assignment_count: int) -> int:
    if assignment_count <= 0:
        return 0
    normalized = str(wait_policy or "all").strip().lower()
    if normalized == "best_effort":
        return 1
    if normalized == "all":
        return assignment_count
    if assignment_count == 1:
        return 1
    return max(2, (assignment_count // 2) + 1)


def _aggregate_delegate_results(
    entries: list[dict[str, object]],
    *,
    wait_policy: str,
) -> tuple[str, bool]:
    total = len(entries)
    quorum_target = _delegate_quorum_target(wait_policy, total)
    completed = sum(
        1
        for entry in entries
        if str(entry.get("state", "") or "").strip().lower() == "completed"
    )
    quorum_met = completed >= quorum_target if quorum_target > 0 else False
    header = (
        f"Delegacion especializada agregada "
        f"(wait_policy={wait_policy}, completed={completed}/{total}, quorum_target={quorum_target}, quorum_met={'yes' if quorum_met else 'no'})"
    )
    lines = [header]
    for entry in entries:
        specialist = str(entry.get("specialist", "") or "").strip()
        phase = str(entry.get("phase", "") or "").strip()
        state = str(entry.get("state", "") or "missing").strip()
        result = _compact_delegated_result(
            str(entry.get("result", "") or ""),
            state=state,
        )
        contract = str(entry.get("report_contract_version", "") or "").strip()
        contract_suffix = f" contract={contract}" if contract else ""
        lines.append(
            f"- {phase} [{specialist}] state={state}{contract_suffix} result={result}"
        )
    return "\n".join(lines), quorum_met


def _execute_delegate_request(
    *,
    orch,
    task_root: str,
    workspace: Path,
    runtime_dir: Path,
    delegate_request,
    source_task_id: str,
    source_phase: str,
    delegate_cycle: int,
    rerun_budget: int,
) -> dict[str, object]:
    delegate_plan = _resolve_delegate_plan(delegate_request)
    delegate_assignments = _resolve_delegate_assignments(delegate_request, workspace=workspace)
    delegate_round_budget = _resolve_delegate_round_budget(delegate_request)
    delegate_wait_policy = str(delegate_request.wait_policy or "all").strip().lower()
    delegate_query = str(delegate_request.query or "").strip()
    raw_context = (
        _build_scout_project_state_context(workspace)
        + "\n\n"
        + _build_scout_session_history_context(runtime_dir)
    )
    delegate_entries: list[dict[str, object]] = []

    for assignment in delegate_assignments:
        delegate_phase_prefix = str(
            assignment.get("phase_prefix", delegate_plan.get("phase_prefix", "delegate_scout"))
        )
        delegate_specialist = str(assignment.get("specialist", "repo_scout"))
        delegate_caps = list(assignment.get("required_capabilities", []) or [])
        delegate_skill_targets = [
            str(item).strip()
            for item in list(assignment.get("skill_targets", []) or [])
            if str(item).strip()
        ]
        delegate_lsp_targets = [
            str(item).strip()
            for item in list(assignment.get("lsp_targets", []) or [])
            if str(item).strip()
        ]
        if not delegate_skill_targets and not delegate_lsp_targets:
            delegate_skill_targets, delegate_lsp_targets = _delegate_specialist_targets(
                intent=str(delegate_request.intent or ""),
                specialist=delegate_specialist,
            )
        delegate_report_contract = _delegate_report_contract(
            intent=str(delegate_request.intent or ""),
            specialist=delegate_specialist,
        )
        delegate_phase = f"{delegate_phase_prefix}_{delegate_cycle}_{delegate_specialist}"
        delegate_task_id = f"{task_root}::{delegate_phase}"
        delegate_entries.append(
            {
                "task_id": delegate_task_id,
                "phase": delegate_phase,
                "specialist": delegate_specialist,
                "report_contract_version": "operator_report_v1",
            }
        )
        orch.submit_task(
            WorkTask(
                task_id=delegate_task_id,
                title=f"{assignment.get('title_prefix', 'Delegate specialist')}: {delegate_query[:60]}",
                description=(
                    f"{assignment.get('instruction', 'Responde con hechos concretos.')}\n\n"
                    f"Consulta del Team Lead (maximo 8 lineas de respuesta compacta):\n"
                    f"{delegate_query}\n\n"
                    "Contexto disponible:\n"
                    f"{raw_context}\n\n"
                    "Solo hechos, evidencia compacta y recomendacion operativa breve. "
                    "Sin arbitraje de producto.\n"
                    f"{delegate_report_contract}"
                ),
                role=assignment.get("role", Role.SCOUT),
                complexity=Complexity.LOW,
                criticality=Criticality.LOW,
                metadata={
                    "is_scout": assignment.get("role", Role.SCOUT) == Role.SCOUT,
                    "scout_type": "on_demand_delegate",
                    "skip_quality_gates": True,
                    "phase": delegate_phase,
                    "chat_parent": task_root,
                    "required_capabilities": delegate_caps,
                    "delegated_by": "team_lead",
                    "delegate_intent": delegate_request.intent,
                    "delegate_wait_policy": delegate_wait_policy,
                    "delegate_budget_rounds": delegate_round_budget,
                    "delegate_source_phase": source_phase,
                    "delegate_report_contract_version": "operator_report_v1",
                    "delegate_original_specialist": str(
                        assignment.get("rewired_from_specialist", "") or ""
                    ).strip(),
                    "skill_targets": delegate_skill_targets,
                    "lsp_targets": delegate_lsp_targets,
                    "tool_rewiring_active": bool(assignment.get("tool_rewiring_active", False)),
                    "tool_rewiring_candidates": list(
                        assignment.get("tool_rewiring_candidates", []) or []
                    ),
                    "tool_rewiring_replacement_for": list(
                        assignment.get("tool_rewiring_replacement_for", []) or []
                    ),
                    "tool_rewiring_preferred_specialist": str(
                        assignment.get("tool_rewiring_preferred_specialist", "") or ""
                    ).strip(),
                    "tool_rewiring_reason": str(
                        assignment.get("tool_rewiring_reason", "") or ""
                    ).strip(),
                    **build_tool_specialist_metadata(
                        specialist=delegate_specialist,
                        required_capabilities=delegate_caps,
                        reason=(
                            f"delegacion del Team Lead via {delegate_request.intent}; "
                            f"source_phase={source_phase}; wait_policy={delegate_wait_policy}"
                        ),
                        skill_targets=delegate_skill_targets,
                        lsp_targets=delegate_lsp_targets,
                    ),
                },
            )
        )

    orch.run_until_idle(max_rounds=delegate_round_budget)

    delegate_ws = orch._get_workflow_state(task_root)
    for entry in delegate_entries:
        task_id = str(entry.get("task_id", "") or "")
        phase = str(entry.get("phase", "") or "")
        delegate_task = orch.taskboard.get_task(task_id)
        result = str(delegate_task.metadata.get("result", "") if delegate_task else "")
        state = str(delegate_task.state.value if delegate_task else "missing")
        if not result:
            result = delegate_ws.get("phase_outputs", {}).get(phase, "")
        if not result:
            result = "Sin datos disponibles para esta consulta."
        entry["result"] = result
        entry["state"] = state
        entry["result_len"] = len(result)

    aggregated_delegate_result, delegate_quorum_met = _aggregate_delegate_results(
        delegate_entries,
        wait_policy=delegate_wait_policy,
    )
    batch_economics = _estimate_delegate_batch_economics(delegate_entries)
    source_task = orch.taskboard.get_task(source_task_id)
    if source_task is not None:
        inject_block = (
            f"\n\n[Resultado de tu delegación"
            f" (ciclo {delegate_cycle}, source_phase='{source_phase}', "
            f"intent='{delegate_request.intent}', wait_policy='{delegate_wait_policy}', "
            f"quorum_met='{delegate_quorum_met}', query: '{delegate_query[:80]}'):\n"
            f"{aggregated_delegate_result[:900]}\n]"
        )
        source_task.description = source_task.description + inject_block
        orch.taskboard.retry_task(
            source_task_id,
            reason=f"delegate_result_injected ({source_phase}, cycle {delegate_cycle})",
        )
        orch.run_until_idle(max_rounds=rerun_budget)

    batch_payload = {
        "source_phase": source_phase,
        "source_task_id": source_task_id,
        "intent": delegate_request.intent,
        "query": delegate_query[:200],
        "wait_policy": delegate_wait_policy,
        "delegate_budget_rounds": delegate_round_budget,
        "delegate_cycle": delegate_cycle,
        "quorum_met": delegate_quorum_met,
        "specialists": [str(entry.get("specialist", "") or "") for entry in delegate_entries],
        "task_ids": [str(entry.get("task_id", "") or "") for entry in delegate_entries],
        "states": [str(entry.get("state", "") or "") for entry in delegate_entries],
        "result_lengths": [int(entry.get("result_len", 0) or 0) for entry in delegate_entries],
        "economics": batch_economics,
    }
    delegate_ws.setdefault("delegate_batches", []).append(batch_payload)
    delegate_ws["delegate_economics_summary"] = _summarize_delegate_economics(
        _coerce_delegate_batches(delegate_ws.get("delegate_batches", []))
    )
    orch._save_workflow_state()
    orch.event_logger.emit(
        "lcp_directive_applied",
        {
            "task_id": task_root,
            "directive": "delegate",
            "source_phase": source_phase,
            "intent": delegate_request.intent,
            "query": delegate_query[:120],
            "cycle": delegate_cycle,
            "wait_policy": delegate_wait_policy,
            "delegate_budget_rounds": delegate_round_budget,
            "specialists": batch_payload["specialists"],
            "quorum_met": delegate_quorum_met,
            "delegate_result_len": len(aggregated_delegate_result),
        },
    )
    orch.event_logger.emit(
        "delegate_economics_estimated",
        {
            "task_id": task_root,
            "source_phase": source_phase,
            "intent": delegate_request.intent,
            "wait_policy": delegate_wait_policy,
            "quorum_met": delegate_quorum_met,
            **batch_economics,
        },
    )
    return {
        "aggregated_result": aggregated_delegate_result,
        "quorum_met": delegate_quorum_met,
        "entries": delegate_entries,
        "delegate_budget_rounds": delegate_round_budget,
        "wait_policy": delegate_wait_policy,
        "economics": batch_economics,
    }


def _structured_evidence_specs_for_phase(
    phase_id: str,
    phase_evidence_plan: dict[str, dict[str, object]],
    *,
    workspace: Path | None = None,
) -> list[dict[str, object]]:
    entry = dict(phase_evidence_plan.get(phase_id) or {})
    intents = [
        str(item).strip().lower()
        for item in list(entry.get("delegate_intents", []) or [])
        if str(item).strip()
    ]
    wait_policy = str(entry.get("wait_policy", "all") or "all").strip().lower()
    delegate_budget = max(1, _safe_int_value(entry.get("delegate_budget", 3), 3))
    specs: list[dict[str, object]] = []
    for intent in intents:
        delegate_request = _build_delegate_request(
            intent,
            query=f"Genera evidencia especializada para la fase '{phase_id}'.",
            wait_policy=wait_policy,
            delegate_budget=delegate_budget,
        )
        assignments = _resolve_delegate_assignments(
            delegate_request,
            workspace=Path(workspace or PROJECT_ROOT),
        )
        for assign_index, assignment in enumerate(assignments):
            specialist = str(assignment.get("specialist", "repo_scout"))
            evidence_phase_id = f"delegate_{phase_id}_{specialist}_{assign_index}"
            skill_targets = [
                str(item).strip()
                for item in list(assignment.get("skill_targets", []) or [])
                if str(item).strip()
            ]
            lsp_targets = [
                str(item).strip()
                for item in list(assignment.get("lsp_targets", []) or [])
                if str(item).strip()
            ]
            if not skill_targets and not lsp_targets:
                skill_targets, lsp_targets = _delegate_specialist_targets(
                    intent=intent,
                    specialist=specialist,
                )
            specs.append(
                {
                    "phase_id": evidence_phase_id,
                    "intent": intent,
                    "specialist": specialist,
                    "role": assignment.get("role", Role.SCOUT),
                    "required_capabilities": list(
                        assignment.get("required_capabilities", []) or []
                    ),
                    "instruction": str(
                        assignment.get("instruction", "Recoge evidencia especializada.")
                    ),
                    "skill_targets": skill_targets,
                    "lsp_targets": lsp_targets,
                    "report_contract": _delegate_report_contract(
                        intent=intent,
                        specialist=specialist,
                    ),
                    "wait_policy": wait_policy,
                    "delegate_budget": delegate_budget,
                    "source_phase": phase_id,
                }
            )
    return specs


def _delegate_specialist_economics_profile(specialist: str) -> dict[str, int]:
    specialist_key = str(specialist or "").strip().lower()
    profiles: dict[str, dict[str, int]] = {
        "repo_scout": {"avoided_tokens": 600, "operator_tokens": 180, "cost_units_saved": 5},
        "lsp_navigator": {"avoided_tokens": 700, "operator_tokens": 220, "cost_units_saved": 6},
        "test_runner": {"avoided_tokens": 800, "operator_tokens": 260, "cost_units_saved": 7},
        "browser_operator": {"avoided_tokens": 1400, "operator_tokens": 420, "cost_units_saved": 10},
        "mcp_operator": {"avoided_tokens": 900, "operator_tokens": 300, "cost_units_saved": 7},
        "skill_worker": {"avoided_tokens": 750, "operator_tokens": 240, "cost_units_saved": 6},
    }
    return profiles.get(
        specialist_key,
        {"avoided_tokens": 650, "operator_tokens": 220, "cost_units_saved": 5},
    )


def _delegate_state_multiplier(state: str) -> tuple[float, float]:
    normalized = str(state or "").strip().lower()
    if normalized == "completed":
        return 1.0, 1.0
    if normalized == "failed":
        return 0.5, 0.7
    if normalized in {"claimed", "ready", "pending", "blocked"}:
        return 0.4, 0.5
    return 0.3, 0.4


def _estimate_delegate_batch_economics(
    delegate_entries: list[dict[str, object]],
) -> dict[str, object]:
    specialist_breakdown: dict[str, dict[str, int]] = {}
    estimated_lead_tokens_avoided = 0
    estimated_operator_tokens_used = 0
    estimated_cost_units_saved = 0

    for entry in delegate_entries:
        specialist = str(entry.get("specialist", "") or "").strip().lower() or "unknown"
        state = str(entry.get("state", "") or "").strip().lower()
        avoided_factor, operator_factor = _delegate_state_multiplier(state)
        profile = _delegate_specialist_economics_profile(specialist)
        avoided_tokens = int(round(profile["avoided_tokens"] * avoided_factor))
        operator_tokens = int(round(profile["operator_tokens"] * operator_factor))
        cost_units_saved = max(0, int(round(profile["cost_units_saved"] * avoided_factor)))
        estimated_lead_tokens_avoided += avoided_tokens
        estimated_operator_tokens_used += operator_tokens
        estimated_cost_units_saved += cost_units_saved
        specialist_row = specialist_breakdown.setdefault(
            specialist,
            {
                "count": 0,
                "completed": 0,
                "failed": 0,
                "estimated_lead_tokens_avoided": 0,
                "estimated_operator_tokens_used": 0,
                "estimated_net_tokens_saved": 0,
                "estimated_cost_units_saved": 0,
            },
        )
        specialist_row["count"] += 1
        if state == "completed":
            specialist_row["completed"] += 1
        elif state == "failed":
            specialist_row["failed"] += 1
        specialist_row["estimated_lead_tokens_avoided"] += avoided_tokens
        specialist_row["estimated_operator_tokens_used"] += operator_tokens
        specialist_row["estimated_net_tokens_saved"] += max(0, avoided_tokens - operator_tokens)
        specialist_row["estimated_cost_units_saved"] += cost_units_saved

    return {
        "economics_version": "delegate_economics_v1",
        "estimated": True,
        "specialist_tasks": len(delegate_entries),
        "estimated_lead_tokens_avoided": estimated_lead_tokens_avoided,
        "estimated_operator_tokens_used": estimated_operator_tokens_used,
        "estimated_net_tokens_saved": max(
            0, estimated_lead_tokens_avoided - estimated_operator_tokens_used
        ),
        "estimated_cost_units_saved": estimated_cost_units_saved,
        "specialist_breakdown": specialist_breakdown,
    }


def _summarize_delegate_economics(
    delegate_batches: list[dict[str, object]],
) -> dict[str, object]:
    summary = {
        "economics_version": "delegate_economics_v1",
        "estimated": True,
        "batch_count": 0,
        "specialist_task_count": 0,
        "estimated_lead_tokens_avoided": 0,
        "estimated_operator_tokens_used": 0,
        "estimated_net_tokens_saved": 0,
        "estimated_cost_units_saved": 0,
        "quorum_met_count": 0,
        "quorum_met_ratio": 0.0,
        "wait_policy_counts": {},
        "intent_counts": {},
        "specialist_breakdown": {},
    }
    if not delegate_batches:
        return summary

    wait_policy_counts: dict[str, int] = {}
    intent_counts: dict[str, int] = {}
    specialist_breakdown: dict[str, dict[str, int]] = {}

    for batch in delegate_batches:
        if not isinstance(batch, dict):
            continue
        summary["batch_count"] += 1
        economics = dict(batch.get("economics", {}) or {})
        summary["specialist_task_count"] += _safe_int_value(economics.get("specialist_tasks", 0), 0)
        summary["estimated_lead_tokens_avoided"] += _safe_int_value(
            economics.get("estimated_lead_tokens_avoided", 0), 0
        )
        summary["estimated_operator_tokens_used"] += _safe_int_value(
            economics.get("estimated_operator_tokens_used", 0), 0
        )
        summary["estimated_cost_units_saved"] += _safe_int_value(
            economics.get("estimated_cost_units_saved", 0), 0
        )
        if bool(batch.get("quorum_met", False)):
            summary["quorum_met_count"] += 1

        wait_policy = str(batch.get("wait_policy", "") or "").strip().lower()
        if wait_policy:
            wait_policy_counts[wait_policy] = wait_policy_counts.get(wait_policy, 0) + 1
        intent = str(batch.get("intent", "") or "").strip().lower()
        if intent:
            intent_counts[intent] = intent_counts.get(intent, 0) + 1

        for specialist, values in dict(economics.get("specialist_breakdown", {}) or {}).items():
            if not isinstance(values, dict):
                continue
            row = specialist_breakdown.setdefault(
                str(specialist),
                {
                    "count": 0,
                    "completed": 0,
                    "failed": 0,
                    "estimated_lead_tokens_avoided": 0,
                    "estimated_operator_tokens_used": 0,
                    "estimated_net_tokens_saved": 0,
                    "estimated_cost_units_saved": 0,
                },
            )
            row["count"] += _safe_int_value(values.get("count", 0), 0)
            row["completed"] += _safe_int_value(values.get("completed", 0), 0)
            row["failed"] += _safe_int_value(values.get("failed", 0), 0)
            row["estimated_lead_tokens_avoided"] += _safe_int_value(
                values.get("estimated_lead_tokens_avoided", 0), 0
            )
            row["estimated_operator_tokens_used"] += _safe_int_value(
                values.get("estimated_operator_tokens_used", 0), 0
            )
            row["estimated_net_tokens_saved"] += _safe_int_value(
                values.get("estimated_net_tokens_saved", 0), 0
            )
            row["estimated_cost_units_saved"] += _safe_int_value(
                values.get("estimated_cost_units_saved", 0), 0
            )

    summary["estimated_net_tokens_saved"] = max(
        0,
        int(summary["estimated_lead_tokens_avoided"])
        - int(summary["estimated_operator_tokens_used"]),
    )
    if int(summary["batch_count"]) > 0:
        summary["quorum_met_ratio"] = round(
            int(summary["quorum_met_count"]) / int(summary["batch_count"]),
            4,
        )
    summary["wait_policy_counts"] = wait_policy_counts
    summary["intent_counts"] = intent_counts
    summary["specialist_breakdown"] = specialist_breakdown
    return summary
