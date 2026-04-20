import re

from aiteam.types import WorkTask

from api.chat_logic import _env_bool, _safe_int_value


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
        hint = "Buen avance; toma el siguiente slice de impacto con pruebas de regresion."

    return productivity_score, reasoning_score, productivity_status, hint


def _classify_check_from_command(command: str) -> str:
    text = str(command or "").strip().lower()
    if not text:
        return ""
    if "test import smoke" in text:
        return "test"
    if "syntax smoke" in text:
        return "build"
    if re.search(r"\b(?:python|py)\s+-m\s+compileall\b", text):
        return "build"
    if re.search(r"\b(?:python|py)\s+-c\s+['\"]?\s*import\s+[a-zA-Z_]", text):
        return "import"
    if re.search(r"\bnode\s+-e\s+['\"]?\s*(?:require|import)\b", text):
        return "import"
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
    if text.startswith("write:"):
        return "file_delivery"
    if "pip install" in text:
        return "build"
    if "python setup.py" in text:
        return "build"
    return ""


def _is_placeholder_output_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lower = text.lower()
    if re.match(r"^\[[a-z0-9_\-]+:[a-z0-9_.\-]+:(subscription|api)\]", lower):
        return True
    if re.match(r"^\[simulado\s*\|", lower):
        return True
    if lower.startswith("[demo]"):
        return True
    if lower.startswith("respuesta mock"):
        return True
    return False


def _assess_execution_mode(
    *,
    task_rows: list[WorkTask],
    execution_steps: int,
    artifact_created: int,
    artifact_modified: int,
) -> tuple[str, int, float, int]:
    result_texts: list[str] = []
    for task in task_rows:
        result = str(task.metadata.get("result") or task.metadata.get("error") or "").strip()
        if result:
            result_texts.append(result)

    if not result_texts:
        mode = (
            "live"
            if (execution_steps > 0 or (artifact_created + artifact_modified) > 0)
            else "text_only"
        )
        return mode, 0, 0.0, 0

    placeholder_count = sum(1 for row in result_texts if _is_placeholder_output_text(row))
    placeholder_ratio = float(placeholder_count) / float(len(result_texts))
    has_execution_evidence = execution_steps > 0 or (artifact_created + artifact_modified) > 0

    if not has_execution_evidence:
        return "text_only", placeholder_count, placeholder_ratio, len(result_texts)

    if placeholder_count == len(result_texts) and execution_steps == 0:
        return "text_only", placeholder_count, placeholder_ratio, len(result_texts)
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
    require_test_or_build_check: bool,
    require_review_qa: bool = True,
) -> list[str]:
    def _select_gate_task(gate_kind: str) -> WorkTask | None:
        normalized_gate = str(gate_kind or "").strip().lower()
        explicit = task_rows_by_phase.get(normalized_gate)
        if explicit is not None:
            return explicit
        for phase_id, candidate in task_rows_by_phase.items():
            if candidate is None:
                continue
            normalized_phase = str(phase_id or "").strip().lower()
            if normalized_phase.startswith(("lead_", "delegate_", "plan_")):
                continue
            if normalized_gate == "build" and candidate.role.value == "engineer":
                return candidate
            if normalized_gate == "review" and candidate.role.value == "reviewer":
                return candidate
            if normalized_gate == "qa" and candidate.role.value == "qa":
                return candidate
        return None

    failures: list[str] = []
    build_task = _select_gate_task("build")
    review_task = _select_gate_task("review")
    qa_task = _select_gate_task("qa")
    review_validation_only = require_review_qa and build_task is None and (
        review_task is not None or qa_task is not None
    )
    if review_validation_only:
        target_phases = []
        if review_task is not None:
            target_phases.append("review")
        if qa_task is not None:
            target_phases.append("qa")
    elif not require_review_qa:
        target_phases = ["build"]
    else:
        target_phases = ["build", "review", "qa"]
    for phase in target_phases:
        task = {
            "build": build_task,
            "review": review_task,
            "qa": qa_task,
        }.get(phase)
        if task is None:
            failures.append(f"{phase}:missing_task")
            continue
        if task.state.value != "completed":
            if (
                phase == "build"
                and bool(task.metadata.get("require_execution_plan", False))
                and not bool(task.metadata.get("execution_plan_requirement_waived", False))
            ):
                raw_plan = task.metadata.get("execution_plan", [])
                if not isinstance(raw_plan, list) or not raw_plan:
                    failures.append("build:missing_execution_plan")
            task_error = str(task.metadata.get("error") or "").strip().lower()
            if task.state.value == "failed":
                if "ungrounded_phase_block_detected" in task_error:
                    failures.append(f"{phase}:ungrounded_phase_block")
                elif "ungrounded_evidence_output_detected" in task_error:
                    failures.append(f"{phase}:ungrounded_evidence")
                else:
                    failures.append(f"{phase}:phase_failed")
            elif task.state.value == "blocked":
                failures.append(f"{phase}:blocked")
            else:
                failures.append(f"{phase}:not_completed")
            continue
        result_text = str(task.metadata.get("result") or task.metadata.get("error") or "").strip()
        if not result_text:
            failures.append(f"{phase}:empty_result")
            continue
        if _is_placeholder_output_text(result_text):
            failures.append(f"{phase}:placeholder_output")

        if (
            phase == "build"
            and bool(task.metadata.get("require_execution_plan", False))
            and not bool(task.metadata.get("execution_plan_requirement_waived", False))
        ):
            raw_plan = task.metadata.get("execution_plan", [])
            if not isinstance(raw_plan, list) or not raw_plan:
                failures.append("build:missing_execution_plan")

    if not review_validation_only:
        build_has_output = all(not row.startswith("build:") for row in failures)
        if build_has_output and execution_steps <= 0 and (artifact_created + artifact_modified) <= 0:
            failures.append("build:no_execution_evidence")
        if execution_steps_success <= 0:
            failures.append("build:no_successful_execution_steps")
        if execution_steps_success > 0 and not successful_checks:
            failures.append("build:no_successful_post_build_checks")
        if require_test_or_build_check and execution_steps_success > 0:
            if not any(check in {"test", "build", "import"} for check in successful_checks):
                failures.append("build:missing_test_or_build_check")
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
    final_state: str = "",
    policy_review_required: bool = False,
    semantic_gate_failures: list[str] | None = None,
    evidence_gate_failures: list[str] | None = None,
) -> str:
    execution_label = execution_mode
    placeholder_label = "salidas placeholder"
    decision_text = _presentable_decision_text(str(decision_compact or "").strip())
    if not decision_text:
        if execution_mode == "text_only":
            decision_text = "Sin output del Team Lead; sin pasos de ejecucion verificables (text_only)."
        elif execution_mode == "hybrid" and placeholder_outputs > 0:
            decision_text = "Coordinacion parcial completada; parte del output fue placeholder."
        else:
            decision_text = "Sin sintesis del Team Lead en esta ronda."
    semantic_gate_failures = [
        str(item).strip()
        for item in list(semantic_gate_failures or [])
        if str(item).strip()
    ]
    evidence_gate_failures = [
        str(item).strip()
        for item in list(evidence_gate_failures or [])
        if str(item).strip()
    ]
    authoritative_state = str(final_state or "").strip().lower()
    authoritative_banner = _compose_authoritative_close_banner(
        final_state=authoritative_state,
        policy_review_required=policy_review_required,
        semantic_gate_failures=semantic_gate_failures,
        evidence_gate_failures=evidence_gate_failures,
        failed_line=failed_line,
        next_action_hint=next_action_hint,
    )
    if authoritative_state in {"failed", "rejected"} and _decision_text_overstates_success(
        decision_text
    ):
        decision_text = (
            "La narrativa original del Team Lead indicaba cierre positivo, "
            "pero fue invalidada por el veredicto autoritativo de la run."
        )

    meta_parts = [f"fases: hecho={done_line} | pendiente={pending_line} | fallido={failed_line}"]
    if artifact_files:
        files_line = ", ".join(artifact_files[:8])
        meta_parts.append(f"archivos({artifact_created}+{artifact_modified}): {files_line}")
    if execution_mode != "live":
        meta_parts.append(f"modo={execution_label}")
    if placeholder_outputs > 0 and execution_mode != "live":
        meta_parts.append(f"{placeholder_label}={placeholder_outputs}")
    meta_parts.append(f"calidad={productivity_score}/100 | {elapsed_ms}ms | {task_root}")

    lines: list[str] = [
        "Resumen del Team Lead para ti:",
    ]
    if authoritative_banner:
        lines.extend([authoritative_banner, ""])
    lines.extend(
        [
            decision_text,
            "",
            f"Solicitud: {request_line}",
            f"Continuity: {continuation_line}",
            f"Modo: {mode} | rondas {rounds_used}/{round_budget}",
            f"Participantes: {participants_line}",
            f"Reasoning: {reasoning_score}/100 | productividad={productivity_status}",
            f"Siguiente paso: {next_action_hint}",
            "",
            "---",
            " | ".join(meta_parts),
        ]
    )
    return "\n".join(lines)


def _compose_authoritative_close_banner(
    *,
    final_state: str,
    policy_review_required: bool,
    semantic_gate_failures: list[str],
    evidence_gate_failures: list[str],
    failed_line: str,
    next_action_hint: str,
) -> str:
    if final_state not in {"failed", "rejected"}:
        return ""

    state_label = "RECHAZADA" if final_state == "rejected" else "NO COMPLETADA"
    reason_codes = list(
        dict.fromkeys(
            [
                *semantic_gate_failures,
                *evidence_gate_failures,
            ]
        )
    )
    reason_text = ", ".join(reason_codes[:4]) if reason_codes else failed_line
    if not reason_text or reason_text == "none":
        reason_text = "bloqueos de policy o fases pendientes"
    suffix = f" Motivo: {reason_text}."
    if policy_review_required and next_action_hint:
        suffix += f" Siguiente paso: {next_action_hint}."
    elif next_action_hint:
        suffix += f" Siguiente paso sugerido: {next_action_hint}."
    return f"Estado autoritativo: run {state_label}.{suffix}"


def _decision_text_overstates_success(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    positive_patterns = [
        r"\bdone\b",
        r"\bqa aprobada\b",
        r"\bqa aprobado\b",
        r"\bcompletad[oa]s?\b",
        r"\bproyecto recuperado\b",
        r"\bexitos[oa]\b",
        r"\bcompleted\b",
        r"\bapproved\b",
        r"\baprobad[oa]s?\b",
        r"\bcerrad[oa]s?\b",
    ]
    return any(re.search(pattern, text) for pattern in positive_patterns)


def _presentable_decision_text(value: str) -> str:
    decision_text = str(value or "").strip()
    if not decision_text or _is_placeholder_output_text(decision_text):
        return ""
    return decision_text


def _compact_text_line(value: str, limit: int = 320) -> str:
    flat = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 3)] + "..."


def _is_placeholder_like_text(value: str) -> bool:
    return _is_placeholder_output_text(value)


def _compact_delegated_result(value: str, *, state: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "sin resultado"
    if _is_placeholder_output_text(text):
        lower = text.lower()
        if lower.startswith("[demo]"):
            return "demo"
        if re.match(r"^\[simulado\s*\|", lower):
            return "placeholder/simulado"
        return "placeholder" if state == "completed" else f"placeholder/{state}"
    return _compact_text_line(_presentable_decision_text(text) or text, 220)


def _trim_at_boundary(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    for sep in ("\n\n", ". ", "\n", " "):
        idx = chunk.rfind(sep)
        if idx > limit * 0.80:
            return chunk[: idx + len(sep)].rstrip()
    return chunk.rstrip()


def _limit_chat_response(text: str, *, limit: int = 12000) -> str:
    content = str(text or "")
    if len(content) <= limit:
        return content

    marker = "\nLead message for user:\n"
    if marker in content:
        prefix, suffix = content.split(marker, 1)
        suffix_budget = max(3500, int(limit * 0.60))
        prefix_budget = max(800, limit - suffix_budget - len(marker) - 20)
        compact_prefix = _trim_at_boundary(prefix, prefix_budget)
        compact_suffix = _trim_at_boundary(suffix, suffix_budget)
        content = compact_prefix + marker + compact_suffix
        if len(content) <= limit:
            return content

    return _trim_at_boundary(content, limit - 20) + "\n[... truncado]"


def _stream_display_chunk(task_id: str, chunk: str) -> str:
    text = str(chunk or "").strip()
    if not text:
        return ""
    if _is_placeholder_like_text(text):
        phase = str(task_id or "").split("::")[-1].strip().lower()
        phase_label_map = {
            "lead_intake": "Analizando solicitud",
            "plan_research": "Investigando contexto",
            "plan_engineering": "Definiendo implementacion",
            "plan_risks": "Evaluando riesgos",
            "build": "Preparando entrega",
            "review": "Revisando resultado",
            "qa": "Validando salida",
            "lead_close": "Cerrando sintesis",
        }
        phase_label = phase_label_map.get(phase, "Coordinando equipo")
        return f"{phase_label}...\n"
    return text


def _resolve_chat_decision_text(
    *,
    lead_response: str,
    intake_response: str,
    phase_states: dict[str, str],
    workflow_phase_keys: list[str],
    phase_results: dict[str, str],
) -> str:
    lead_text = str(lead_response or "").strip()
    if lead_text:
        return lead_text

    lead_close_state = str(phase_states.get("lead_close", "") or "").strip().lower()
    intake_text = str(intake_response or "").strip()
    if lead_close_state == "completed" and intake_text:
        return intake_text

    done_phases = [phase for phase in workflow_phase_keys if phase_states.get(phase) == "completed"]
    blocked_phases = [phase for phase in workflow_phase_keys if phase_states.get(phase) == "blocked"]
    failed_phases = [phase for phase in workflow_phase_keys if phase_states.get(phase) == "failed"]
    pending_phases = [
        phase
        for phase in workflow_phase_keys
        if phase_states.get(phase) in {"pending", "ready", "claimed"}
    ]

    fragments: list[str] = []
    if done_phases:
        fragments.append(f"completado={', '.join(done_phases)}")

    if failed_phases:
        failed_with_context: list[str] = []
        for phase in failed_phases[:4]:
            detail = re.sub(r"\s+", " ", str(phase_results.get(phase, "") or "")).strip()
            if detail:
                failed_with_context.append(f"{phase} ({detail[:120]})")
            else:
                failed_with_context.append(phase)
        fragments.append(f"fallido={', '.join(failed_with_context)}")

    if blocked_phases:
        fragments.append(f"bloqueado={', '.join(blocked_phases)}")

    if pending_phases:
        fragments.append(f"pendiente={', '.join(pending_phases)}")

    if lead_close_state and lead_close_state != "completed":
        fragments.append(f"lead_close={lead_close_state}")
    elif not lead_close_state:
        fragments.append("lead_close=missing")

    if not fragments:
        return "Corrida sin cierre final; aun no hay sintesis definitiva del Team Lead."

    return "Corrida sin cierre final. " + "; ".join(
        fragment.rstrip(".") for fragment in fragments
    ) + "."
