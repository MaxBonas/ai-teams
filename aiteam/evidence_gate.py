"""
evidence_gate.py — Validacion de evidencia y calidad de output por tarea.

Responsabilidades:
- verify_task_evidence: decide si una tarea produjo evidencia real (git diff, doc, output live).
- assess_output_quality: valida calidad minima del output LLM en modo live.
- build_gate_evidence_context: construye el contexto rico para los gates Review/QA.
- summarize_git_diff: parsea un git diff crudo en resumen legible.
- detect_conversational_task: detecta si una tarea es conversacional (no requiere artefactos).

Ninguna funcion de este modulo tiene estado propio. Todas reciben lo que necesitan como
parametros explicitos para ser testeables de forma aislada.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from aiteam.types import Role, WorkTask

if TYPE_CHECKING:
    from aiteam.agent_session import SessionStore


# ── Constantes ──────────────────────────────────────────────────────────────

_CONVERSATIONAL_KEYWORDS: frozenset[str] = frozenset(
    {
        # Español
        "¿",
        "explica",
        "explícame",
        "describe",
        "qué es",
        "qué son",
        "cuál es",
        "cuáles son",
        "cómo funciona",
        "cómo se",
        "por qué",
        "para qué",
        "diferencia entre",
        "compara",
        "análisis",
        "analiza",
        "reflexión",
        "reflexiona",
        "opinión",
        "filosofía",
        "filosófico",
        "teoría",
        "teórico",
        "estrategia",
        "recomendación",
        "recomienda",
        "debería",
        "consejo",
        "resumen",
        "resume",
        "enumera",
        "lista de",
        "ventajas",
        "desventajas",
        "pros y contras",
        "cuándo",
        "qué piensas",
        # English
        "what is",
        "what are",
        "how does",
        "how do",
        "why is",
        "why are",
        "explain",
        "compare",
        "analysis",
        "analyze",
        "theory",
        "theoretical",
        "philosophy",
        "opinion",
        "strategy",
        "recommend",
        "should i",
        "pros and cons",
        "when to",
        "what do you think",
        "summarize",
        "summary",
        "list of",
        "advantages",
        "disadvantages",
    }
)

_TRIVIAL_PHRASES: tuple[str, ...] = (
    "tarea completada",
    "task completed",
    "done.",
    "listo.",
    "completado.",
    "finalizado.",
    "he completado",
    "he realizado",
    "he implementado",
    "como se solicito",
)

_PLACEHOLDER_PATTERNS: tuple[str, ...] = (
    r"^\[[a-z0-9_\-]+:[a-z0-9_\.\-]+:(subscription|api)\]",
    r"^\[simulado\s*\|",
)

_PLANNING_RUN_MODES: frozenset[str] = frozenset(
    {"planning_only", "architecture_review", "roadmap"}
)
_ENGINEER_MATERIAL_PHASE_HINTS: tuple[str, ...] = (
    "build",
    "engineer",
    "implement",
    "implementation",
    "fix",
    "code",
)


# ── API publica ──────────────────────────────────────────────────────────────


def verify_task_evidence(
    task: WorkTask,
    workspace: Path,
    *,
    project_root: Path | None,
    runtime_dir: Path,
) -> tuple[bool, str]:
    """Decide si la tarea produjo evidencia real de trabajo.

    Jerarquia de checks:
    1. Sim-mode no-conversacional: acepta output limpio, bloquea placeholders.
    2. Git diff del repo del proyecto.
    3. Tareas conversacionales: doc creado, output persistido, o cualquier output.
    4. Modo live sin git diff: calidad minima del output por rol.
    5. Strict gate: nada de lo anterior paso.
    """
    agent_output = str(task.metadata.get("_last_agent_output", ""))
    phase_name = task.task_id.split("::")[-1] if "::" in task.task_id else ""
    is_conversational = bool(
        task.metadata.get("conversational") or task.metadata.get("interactive_chat")
    )
    run_mode = str(task.metadata.get("run_mode", "") or "").strip().lower()
    live_api_enabled = os.getenv("AITEAM_ENABLE_LIVE_API", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    requires_material_artifacts = _requires_material_artifact_evidence(task)
    real_channel_present = bool(str(task.metadata.get("last_channel", "") or "").strip())

    # ── 1. Modo simulado no-conversacional ──────────────────────────────────
    # El output textual del adapter es la unica evidencia valida.
    # No aceptar git diff ajeno del worktree de desarrollo.
    if not is_conversational:
        if "[SIMULADO |" in agent_output:
            return False, "simulated_placeholder_blocked:placeholder_output"
        if (
            not live_api_enabled
            and agent_output.strip()
            and not (real_channel_present and requires_material_artifacts)
        ):
            return True, "simulated_mode_accepted"

    # ── 2. Git diff ──────────────────────────────────────────────────────────
    try:
        repo = (project_root or workspace).resolve()
        pathspec_args: list[str] = []
        top_level_proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if top_level_proc.returncode == 0:
            git_root = Path(top_level_proc.stdout.strip()).resolve()
            if git_root != repo:
                try:
                    scoped_path = repo.relative_to(git_root)
                except ValueError:
                    scoped_path = None
                if scoped_path is not None:
                    pathspec_args = ["--", scoped_path.as_posix()]
        proc = subprocess.run(
            ["git", "status", "--porcelain", *pathspec_args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            diff_proc = subprocess.run(
                ["git", "diff", *pathspec_args],
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            diff_content = diff_proc.stdout.strip()
            if not diff_content:
                diff_proc = subprocess.run(
                    ["git", "diff", "--cached", *pathspec_args],
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                )
            diff_content = diff_proc.stdout.strip()

            task.metadata["git_diff_evidence"] = diff_content
            return True, "git_diff_detected"
    except Exception:
        pass

    artifact_count = _material_artifact_count(task, runtime_dir=runtime_dir)
    if artifact_count > 0:
        return True, f"artifact_events_detected:{artifact_count}"

    if run_mode in _PLANNING_RUN_MODES:
        structured_doc = _find_structured_markdown_evidence(
            workspace=workspace,
            runtime_dir=runtime_dir,
        )
        if structured_doc is not None:
            task.metadata["doc_evidence"] = str(structured_doc)
            return True, f"planning_structured_doc:{structured_doc.name}"
        return False, "planning_requires_structured_markdown"

    # ── 3. Tarea conversacional ──────────────────────────────────────────────
    if is_conversational:
        doc_exts = {".md", ".txt", ".rst", ".adoc"}
        for search_root in [workspace, runtime_dir]:
            try:
                for p in Path(search_root).rglob("*"):
                    if p.suffix.lower() in doc_exts and p.is_file():
                        task.metadata["doc_evidence"] = str(p)
                        return True, f"conversational_doc:{p.name}"
            except Exception:
                pass

        if len(agent_output.strip()) >= 400:
            try:
                doc_dir = runtime_dir / "docs"
                doc_dir.mkdir(parents=True, exist_ok=True)
                safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", task.task_id)
                doc_path = doc_dir / f"{safe_id}.md"
                doc_path.write_text(
                    f"# {task.title}\n\n{agent_output}\n",
                    encoding="utf-8",
                )
                task.metadata["doc_evidence"] = str(doc_path)
                return True, f"conversational_output_persisted:{doc_path.name}"
            except Exception:
                pass

        if agent_output.strip():
            return True, "conversational_response_accepted"

    # ── 4. Modo live: calidad minima del output ──────────────────────────────
    if live_api_enabled and agent_output.strip():
        quality_ok, quality_reason = assess_output_quality(
            agent_output, task.role, phase_name
        )
        if quality_ok:
            if requires_material_artifacts:
                return False, "engineer_material_artifacts_required"
            return True, f"live_output_quality:{quality_reason}"

    if requires_material_artifacts:
        return (
            False,
            "engineer_material_artifacts_required",
        )

    return (
        False,
        "Strict Evidence Gate: No file modifications detected. Tasks must produce tangible output.",
    )


def _requires_material_artifact_evidence(task: WorkTask) -> bool:
    if task.role != Role.ENGINEER:
        return False
    is_chat_contract_task = bool(task.metadata.get("phase_contract_enforced")) or bool(
        task.metadata.get("chat_parent")
    ) or str(task.task_id or "").startswith("CHAT-")
    if not is_chat_contract_task:
        return False

    required_capabilities = {
        str(item).strip().lower()
        for item in list(task.metadata.get("required_capabilities", []) or [])
        if str(item).strip()
    }
    if "coding" in required_capabilities:
        return True

    phase_name = str(task.metadata.get("phase", "") or "").strip().lower()
    return any(hint in phase_name for hint in _ENGINEER_MATERIAL_PHASE_HINTS)


def _material_artifact_count(task: WorkTask, *, runtime_dir: Path) -> int:
    metadata_count = int(task.metadata.get("artifact_created_count", 0) or 0) + int(
        task.metadata.get("artifact_modified_count", 0) or 0
    )
    if metadata_count > 0:
        return metadata_count

    artifact_paths = list(task.metadata.get("artifact_paths", []) or [])
    if artifact_paths:
        return len(artifact_paths)

    events_path = runtime_dir / "events.jsonl"
    if not events_path.exists():
        return 0
    count = 0
    try:
        with events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                event_type = str(row.get("event_type", "") or "").strip()
                if event_type not in {"artifact_created", "artifact_modified"}:
                    continue
                payload = row.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("task_id", "") or "").strip() != str(task.task_id or "").strip():
                    continue
                count += 1
    except Exception:
        return 0
    return count


def _find_structured_markdown_evidence(
    *,
    workspace: Path,
    runtime_dir: Path,
) -> Path | None:
    runtime_resolved: Path | None = None
    try:
        runtime_resolved = runtime_dir.resolve()
    except Exception:
        runtime_resolved = None

    try:
        for path in workspace.rglob("*.md"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if runtime_resolved is not None and runtime_resolved in resolved.parents:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except Exception:
                continue
            if len(content.strip()) <= 200:
                continue
            heading_count = sum(
                1
                for line in content.splitlines()
                if str(line).lstrip().startswith(("## ", "### ", "# "))
            )
            if heading_count >= 2:
                return path
    except Exception:
        return None
    return None


def assess_output_quality(output: str, role: Role, phase: str) -> tuple[bool, str]:
    """Valida calidad minima del output LLM en modo live (sin git diff).

    Evita que respuestas triviales como 'Tarea completada.' pasen el gate.
    Orden: placeholder → trivial → rol especifico → longitud minima.
    Retorna (pasa, razon).
    """
    text = output.strip()
    if not text:
        return False, "output_vacio"

    lower = text.lower()

    if any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in _PLACEHOLDER_PATTERNS
    ):
        return False, "placeholder_output"

    is_trivial = any(p in lower for p in _TRIVIAL_PHRASES) and len(text) < 200
    if is_trivial:
        return False, "output_trivial_sin_contenido_tecnico"

    if role == Role.REVIEWER:
        reviewer_signals = [
            "issue", "problema", "error", "bug", "sugerencia", "mejora",
            "recomendacion", "fix:", "correc", "falta", "observacion",
            "nota:", "- ", "* ", "1.", "2.", "•",
        ]
        has_signal = any(s in lower for s in reviewer_signals)
        if has_signal or len(text) >= 300:
            return True, "review_con_observaciones"
        if len(text) < 80:
            return False, f"output_muy_corto:{len(text)}_chars"
        return False, "review_sin_observaciones_accionables"

    if role == Role.QA:
        qa_signals = [
            "passed", "failed", "error", "test", "prueba", "resultado",
            "pass", "fail", "assert", "verificado", "ok:", "✓", "✗",
            "coverage", "cobertura", "suite",
        ]
        has_signal = any(s in lower for s in qa_signals)
        if has_signal:
            return True, "qa_con_resultados"
        if len(text) >= 300:
            return True, "qa_output_sustancial"
        return False, "qa_sin_resultados_de_test"

    if len(text) < 80:
        return False, f"output_muy_corto:{len(text)}_chars"
    if len(text) >= 200:
        return True, "substantial_technical_output"
    return False, f"output_insuficiente_en_live:{len(text)}_chars"


def build_gate_evidence_context(
    task: WorkTask,
    *,
    session_store: "SessionStore",
    compact_fn: Callable[[str, int], str],
) -> str:
    """Construye contexto rico para los gates Review/QA a partir del trabajo del Engineer."""
    lines: list[str] = []

    parent_sessions = session_store.sessions_for_task(task.task_id)
    if parent_sessions:
        last_session = parent_sessions[-1]
        raw_actions = (
            last_session.get("actions", [])
            if isinstance(last_session, dict)
            else getattr(last_session, "actions", [])
        )
        exec_actions = [
            a
            for a in (raw_actions or [])
            if (
                getattr(a, "action_type", None) in ("command_exec", "llm_call")
                or (
                    isinstance(a, dict)
                    and str(a.get("action_type", "")).strip()
                    in ("command_exec", "llm_call")
                )
            )
        ]
        if exec_actions:
            lines.append("Acciones del engineer:")
            for a in exec_actions[-6:]:
                if isinstance(a, dict):
                    status = "OK" if bool(a.get("success", False)) else "FAIL"
                    action_type = str(a.get("action_type", "") or "")
                    detail = str(a.get("detail", "") or "")
                else:
                    status = "OK" if getattr(a, "success", False) else "FAIL"
                    action_type = str(getattr(a, "action_type", "") or "")
                    detail = str(getattr(a, "detail", "") or "")
                lines.append(f"  [{status}] {action_type}: {detail[:120]}")

    raw_diff = task.metadata.get("git_diff_evidence", "")
    if raw_diff:
        diff_summary = summarize_git_diff(raw_diff)
        lines.append(f"Resumen de cambios:\n{diff_summary}")

    justification = task.metadata.get("decision_justification", "")
    if justification:
        lines.append(f"Razonamiento del engineer: {compact_fn(justification, 300)}")

    consulted = task.metadata.get("consulted_roles", [])
    if consulted:
        lines.append(f"Peers consultados: {', '.join(consulted)}")

    gate_iter = int(task.metadata.get("gate_iteration", 0))
    if gate_iter > 0:
        lines.append(f"NOTA: Esta es la iteracion {gate_iter + 1} de revision.")
        prev_feedback = task.metadata.get("review_feedback", "")
        if prev_feedback:
            lines.append(f"Feedback previo: {compact_fn(prev_feedback, 300)}")

    return "\n".join(lines)


def summarize_git_diff(raw_diff: str) -> str:
    """Parsea un git diff crudo en resumen legible por el LLM."""
    if not raw_diff:
        return "Sin diferencias detectadas."
    files_changed: dict[str, tuple[int, int]] = {}
    current_file = ""
    added = 0
    removed = 0
    for line in raw_diff.split("\n"):
        if line.startswith("diff --git"):
            if current_file:
                files_changed[current_file] = (added, removed)
            parts = line.split(" b/")
            current_file = parts[-1] if len(parts) > 1 else line
            added = 0
            removed = 0
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    if current_file:
        files_changed[current_file] = (added, removed)

    total_added = sum(a for a, _ in files_changed.values())
    total_removed = sum(r for _, r in files_changed.values())
    summary_lines = [
        f"{len(files_changed)} archivos, +{total_added}/-{total_removed} lineas"
    ]
    for fname, (a, r) in list(files_changed.items())[:8]:
        summary_lines.append(f"  {fname}: +{a}/-{r}")
    if len(files_changed) > 8:
        summary_lines.append(f"  ... y {len(files_changed) - 8} archivos mas")
    return "\n".join(summary_lines)


def detect_conversational_task(task: WorkTask) -> bool:
    """Detecta si una tarea es conversacional/teorica (no requiere artefactos de codigo)."""
    phase = str(task.metadata.get("phase", "") or "").strip().lower()
    if phase in {"build", "review", "qa"}:
        return False
    blob = f"{task.title} {task.description}".lower()
    if "?" in blob:
        return True
    return any(kw in blob for kw in _CONVERSATIONAL_KEYWORDS)
