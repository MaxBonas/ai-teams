from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


_MAX_RECENT_RUNS = 5
_RUN_LINE_RE = re.compile(
    r"^- Run (?P<timestamp>.+?) \| "
    r"chat=(?P<chat_id>.*?) \| "
    r"objetivo=(?P<objective>.*?) \| "
    r"resultado=(?P<result>.*?) \| "
    r"fases=(?P<phases_completed>\d+)/(?P<phases_total>\d+) \| "
    r"duracion=(?P<duration_seconds>\d+)s"
    r"(?: \| errores=(?P<errors>.*?))?"
    r"(?: \| decisiones=(?P<decisions>.*))?$"
)


@dataclass
class LeadMemoryEntry:
    timestamp: str
    chat_id: str
    objective: str
    result: str
    phases_completed: int
    phases_total: int
    duration_seconds: int
    significant_errors: list[str]
    lead_decisions: list[str]


def lead_memory_path(runtime_dir: Path) -> Path:
    return runtime_dir / "lead_memory.md"


def load_lead_memory(runtime_dir: Path) -> str:
    path = lead_memory_path(runtime_dir)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def build_memory_prompt_block(*, runtime_dir: Path, project_root: Path) -> str:
    instructions_text = _load_project_instructions(project_root)
    memory_text = load_lead_memory(runtime_dir)
    if not memory_text:
        if not instructions_text:
            return ""
        memory_text = _render_memory_markdown(
            project_name=project_root.name or "Proyecto",
            entries=[],
            capabilities={},
            instructions_text=instructions_text,
        )
    return f"== LEAD MEMORY ==\n{memory_text}\n== FIN LEAD MEMORY =="


def observe_capabilities_snapshot(
    *,
    runtime_dir: Path,
    mcp_status: object | None = None,
) -> dict[str, Any]:
    doctor_payload = _read_json(runtime_dir / "provider_doctor.json")
    api_keys = dict(doctor_payload.get("api_keys", {}) or {})
    configured_keys = sorted(
        key_name
        for key_name, status in api_keys.items()
        if str(status or "").strip().lower() in {"set", "configured", "ok", "present"}
    )
    missing_keys = sorted(
        key_name
        for key_name, status in api_keys.items()
        if str(status or "").strip().lower() == "missing"
    )

    mcp_rows = _coerce_mcp_rows(mcp_status, runtime_dir)
    healthy_mcps: list[str] = []
    broken_mcps: list[str] = []
    for item in mcp_rows:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or item.get("server", "") or "").strip()
        if not name or not bool(item.get("enabled", True)):
            continue
        health_status = str(
            item.get("health_status", "") or item.get("status", "") or ""
        ).strip().lower()
        health_reason = str(
            item.get("health_reason", "") or item.get("reason", "") or ""
        ).strip()
        if health_status in {"", "healthy", "ok", "running"}:
            healthy_mcps.append(name)
        else:
            broken_mcps.append(
                f"{name} ({_compact(health_reason or health_status, 80)})"
            )

    return {
        "configured_keys": sorted(dict.fromkeys(configured_keys)),
        "missing_keys": sorted(dict.fromkeys(missing_keys)),
        "healthy_mcps": sorted(dict.fromkeys(healthy_mcps)),
        "broken_mcps": sorted(dict.fromkeys(broken_mcps)),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def update_lead_memory(
    *,
    runtime_dir: Path,
    project_root: Path,
    chat_id: str,
    objective: str,
    result: str,
    phases_completed: int,
    phases_total: int,
    significant_errors: list[str] | None = None,
    lead_decisions: list[str] | None = None,
    duration_seconds: int = 0,
    capabilities: dict[str, Any] | None = None,
) -> Path:
    existing_text = load_lead_memory(runtime_dir)
    existing_entries = _parse_entries(existing_text)
    existing_capabilities = _parse_capabilities(existing_text)
    normalized_entry = LeadMemoryEntry(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        chat_id=str(chat_id or "").strip(),
        objective=_compact(objective, 160),
        result=str(result or "").strip() or "parcial",
        phases_completed=max(0, int(phases_completed or 0)),
        phases_total=max(0, int(phases_total or 0)),
        duration_seconds=max(0, int(duration_seconds or 0)),
        significant_errors=_normalize_list(significant_errors),
        lead_decisions=_normalize_list(lead_decisions),
    )

    combined_entries = [normalized_entry] + [
        item for item in existing_entries if item.chat_id != normalized_entry.chat_id
    ]
    combined_entries = combined_entries[:_MAX_RECENT_RUNS]
    merged_capabilities = dict(existing_capabilities)
    merged_capabilities.update(dict(capabilities or {}))

    instructions_text = _load_project_instructions(project_root)
    rendered = _render_memory_markdown(
        project_name=project_root.name or "Proyecto",
        entries=combined_entries,
        capabilities=merged_capabilities,
        instructions_text=instructions_text,
    )
    path = lead_memory_path(runtime_dir)
    path.write_text(rendered, encoding="utf-8")
    return path


def _render_memory_markdown(
    *,
    project_name: str,
    entries: list[LeadMemoryEntry],
    capabilities: dict[str, Any],
    instructions_text: str,
) -> str:
    lines = [f"# Lead Memory - {project_name}", ""]
    lines.extend(
        [
            "## Identidad del sistema",
            (
                "Eres el Team Lead de AI Teams. Planificas, coordinas y evaluas. "
                "No ejecutas codigo directamente; trabajas con Scout, Researcher, "
                "Engineer, Reviewer y QA, y puedes adaptar el flujo con directivas LCP."
            ),
            "",
            "## Historial de runs recientes",
        ]
    )
    if entries:
        for entry in entries:
            errors_text = ", ".join(entry.significant_errors) if entry.significant_errors else "ninguno"
            decisions_text = ", ".join(entry.lead_decisions) if entry.lead_decisions else "ninguna"
            lines.append(
                f"- Run {entry.timestamp} | chat={entry.chat_id or '-'} | "
                f"objetivo={_sanitize_inline(entry.objective)} | "
                f"resultado={_sanitize_inline(entry.result)} | "
                f"fases={entry.phases_completed}/{entry.phases_total} | "
                f"duracion={entry.duration_seconds}s | "
                f"errores={_sanitize_inline(errors_text)} | "
                f"decisiones={_sanitize_inline(decisions_text)}"
            )
    else:
        lines.append("- Sin historial todavia.")

    configured_keys = _normalize_list(capabilities.get("configured_keys", []))
    missing_keys = _normalize_list(capabilities.get("missing_keys", []))
    healthy_mcps = _normalize_list(capabilities.get("healthy_mcps", []))
    broken_mcps = _normalize_list(capabilities.get("broken_mcps", []))
    updated_at = str(capabilities.get("updated_at", "") or "").strip()

    lines.extend(["", "## Capacidades conocidas de este entorno"])
    lines.append(
        f"- API keys configuradas: {', '.join(configured_keys) if configured_keys else 'ninguna'}"
    )
    lines.append(
        f"- API keys ausentes: {', '.join(missing_keys) if missing_keys else 'ninguna'}"
    )
    lines.append(
        f"- MCPs disponibles: {', '.join(healthy_mcps) if healthy_mcps else 'ninguno'}"
    )
    lines.append(
        f"- MCPs con error: {', '.join(broken_mcps) if broken_mcps else 'ninguno'}"
    )
    if updated_at:
        lines.append(f"- Ultima actualizacion: {updated_at}")

    if instructions_text:
        lines.extend(
            [
                "",
                "## Instrucciones del proyecto (.aiteam/instructions.md)",
                instructions_text,
            ]
        )

    return "\n".join(lines).strip()


def _parse_entries(text: str) -> list[LeadMemoryEntry]:
    entries: list[LeadMemoryEntry] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("- Run "):
            continue
        match = _RUN_LINE_RE.match(line)
        if not match:
            continue
        errors = _split_csv(match.group("errors"))
        decisions = _split_csv(match.group("decisions"))
        entries.append(
            LeadMemoryEntry(
                timestamp=match.group("timestamp").strip(),
                chat_id=match.group("chat_id").strip(),
                objective=match.group("objective").strip(),
                result=match.group("result").strip(),
                phases_completed=int(match.group("phases_completed") or 0),
                phases_total=int(match.group("phases_total") or 0),
                duration_seconds=int(match.group("duration_seconds") or 0),
                significant_errors=errors,
                lead_decisions=decisions,
            )
        )
    return entries


def _parse_capabilities(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("- API keys configuradas:"):
            payload["configured_keys"] = _split_csv(line.partition(":")[2])
        elif line.startswith("- API keys ausentes:"):
            payload["missing_keys"] = _split_csv(line.partition(":")[2])
        elif line.startswith("- MCPs disponibles:"):
            payload["healthy_mcps"] = _split_csv(line.partition(":")[2])
        elif line.startswith("- MCPs con error:"):
            payload["broken_mcps"] = _split_csv(line.partition(":")[2])
        elif line.startswith("- Ultima actualizacion:"):
            payload["updated_at"] = line.partition(":")[2].strip()
    return payload


def _coerce_mcp_rows(mcp_status: object | None, runtime_dir: Path) -> list[dict[str, Any]]:
    if isinstance(mcp_status, list):
        return [item for item in mcp_status if isinstance(item, dict)]
    if isinstance(mcp_status, dict):
        if isinstance(mcp_status.get("servers"), list):
            return [item for item in list(mcp_status.get("servers") or []) if isinstance(item, dict)]
        return [item for item in mcp_status.values() if isinstance(item, dict)]
    fallback = _read_json(runtime_dir / "mcp_servers.json")
    if isinstance(fallback.get("servers"), list):
        return [item for item in list(fallback.get("servers") or []) if isinstance(item, dict)]
    return []


def _load_project_instructions(project_root: Path) -> str:
    path = Path(project_root) / ".aiteam" / "instructions.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _normalize_list(values: object) -> list[str]:
    if isinstance(values, str):
        items = _split_csv(values)
    else:
        items = [
            str(item or "").strip()
            for item in list(values or [])
            if str(item or "").strip()
        ]
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        normalized = _compact(item, 120)
        if normalized in {"", "ninguna", "ninguno"} or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


def _split_csv(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _sanitize_inline(value: str) -> str:
    return _compact(str(value or "").replace("|", "/"), 180)


def _compact(value: object, limit: int = 160) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."
