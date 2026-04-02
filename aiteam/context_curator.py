from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_CONTEXT_VERSION = "project_context_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._-") or "default"


def _compact_text(value: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _split_compact_lines(value: str, limit: int = 6) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    lines: list[str] = []
    for raw_line in text.splitlines():
        normalized = raw_line.strip().lstrip("-*").strip()
        if not normalized:
            continue
        if normalized.startswith("[") and normalized.endswith("]"):
            continue
        if normalized not in lines:
            lines.append(_compact_text(normalized, 220))
        if len(lines) >= max(1, limit):
            break
    if lines:
        return lines
    return [_compact_text(text, 220)]


def _coerce_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def estimate_context_pressure(
    *,
    continuation_requested: bool = False,
    continuation_snapshot: str = "",
    phase_summary_count: int = 0,
    delegate_batch_count: int = 0,
    specialist_report_count: int = 0,
    invalidation_count: int = 0,
    open_question_count: int = 0,
) -> dict[str, Any]:
    """Estima si merece la pena activar compactación barata de contexto.

    La heurística intenta ser estable y barata:
    - continuations/reanudaciones pesan bastante
    - acumulación de batches delegados, informes especialistas y resúmenes de fase
      sube la presión gradualmente
    - invalidaciones (`REPLAN`, `FORCE_GATE`) y preguntas abiertas añaden ruido
      contextual y justifican volver a compactar
    """
    score = 0
    signals: list[str] = []

    normalized_snapshot = str(continuation_snapshot or "").strip().lower()
    phase_summary_count = _coerce_non_negative_int(phase_summary_count)
    delegate_batch_count = _coerce_non_negative_int(delegate_batch_count)
    specialist_report_count = _coerce_non_negative_int(specialist_report_count)
    invalidation_count = _coerce_non_negative_int(invalidation_count)
    open_question_count = _coerce_non_negative_int(open_question_count)

    if continuation_requested:
        score += 2
        signals.append("continuation_requested")
        if normalized_snapshot and normalized_snapshot not in {"all_completed", "target_not_found"}:
            score += 1
            signals.append("continuation_unresolved")

    if phase_summary_count >= 4:
        score += 1
        signals.append("phase_context_accumulated")
    if phase_summary_count >= 7:
        score += 1
        signals.append("phase_context_heavy")

    if delegate_batch_count >= 2:
        score += 1
        signals.append("delegate_batches_accumulated")
    if delegate_batch_count >= 4:
        score += 1
        signals.append("delegate_batches_heavy")

    if specialist_report_count >= 3:
        score += 1
        signals.append("specialist_reports_accumulated")
    if specialist_report_count >= 6:
        score += 1
        signals.append("specialist_reports_heavy")

    if invalidation_count >= 1:
        score += 1
        signals.append("invalidations_present")
    if invalidation_count >= 3:
        score += 1
        signals.append("invalidations_heavy")

    if open_question_count >= 3:
        score += 1
        signals.append("open_questions_accumulated")

    if score >= 6:
        level = "high"
    elif score >= 3:
        level = "medium"
    else:
        level = "low"

    return {
        "score": score,
        "level": level,
        "signals": signals,
        "recommend_context_curator": level in {"medium", "high"},
    }


def estimate_context_compaction_value(
    *,
    phase_outputs: dict[str, Any] | None = None,
    project_context_summary: str = "",
    chat_context_summary: str = "",
    phase_context_summaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estima el ahorro potencial de recompactar contexto ya acumulado."""
    raw_payload = phase_outputs if isinstance(phase_outputs, dict) else {}
    compact_payload = (
        phase_context_summaries if isinstance(phase_context_summaries, dict) else {}
    )

    raw_context_chars = sum(
        len(str(value or "").strip())
        for value in raw_payload.values()
        if str(value or "").strip()
    )
    compact_context_chars = (
        len(str(project_context_summary or "").strip())
        + len(str(chat_context_summary or "").strip())
        + sum(
            len(str(value or "").strip())
            for value in compact_payload.values()
            if str(value or "").strip()
        )
    )
    estimated_chars_saved = max(0, raw_context_chars - compact_context_chars)
    estimated_tokens_saved = max(0, estimated_chars_saved // 4)
    compression_ratio = round(
        (compact_context_chars / raw_context_chars),
        4,
    ) if raw_context_chars > 0 else 0.0

    signals: list[str] = []
    if raw_context_chars >= 800:
        signals.append("raw_context_present")
    if raw_context_chars >= 1800:
        signals.append("raw_context_heavy")
    if estimated_chars_saved >= 1200:
        signals.append("context_savings_material")
    if estimated_chars_saved >= 3000:
        signals.append("context_savings_high")
    if estimated_tokens_saved >= 300:
        signals.append("context_tokens_saved_material")
    if raw_context_chars >= 1200 and compression_ratio <= 0.65:
        signals.append("compact_memory_effective")
    if raw_context_chars >= 2400 and compression_ratio <= 0.5:
        signals.append("compact_memory_highly_effective")

    if (
        estimated_chars_saved >= 3000
        or estimated_tokens_saved >= 750
        or (raw_context_chars >= 2400 and compression_ratio <= 0.5)
    ):
        level = "high"
    elif (
        estimated_chars_saved >= 1200
        or estimated_tokens_saved >= 300
        or (raw_context_chars >= 1200 and compression_ratio <= 0.65)
    ):
        level = "medium"
    else:
        level = "low"

    return {
        "raw_context_chars": raw_context_chars,
        "compact_context_chars": compact_context_chars,
        "estimated_context_chars_saved": estimated_chars_saved,
        "estimated_context_tokens_saved": estimated_tokens_saved,
        "compression_ratio": compression_ratio,
        "level": level,
        "signals": signals,
        "priority_boost": raw_context_chars >= 800 and level in {"medium", "high"},
    }


def _coerce_item_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = _compact_text(item.get("text", ""), 300)
        if not text:
            continue
        items.append(
            {
                "text": text,
                "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.5) or 0.5))),
                "source_task_ids": [
                    str(task_id).strip()
                    for task_id in list(item.get("source_task_ids", []) or [])
                    if str(task_id).strip()
                ],
                "updated_at": str(item.get("updated_at", "") or _utc_now()),
                "supersedes": str(item.get("supersedes", "") or "").strip(),
            }
        )
    return items


def _append_unique_item(
    bucket: list[dict[str, Any]],
    *,
    text: str,
    confidence: float,
    source_task_ids: list[str],
    supersedes: str = "",
    limit: int = 12,
) -> None:
    normalized_text = _compact_text(text, 300)
    if not normalized_text:
        return
    key = normalized_text.lower()
    for entry in bucket:
        if str(entry.get("text", "")).strip().lower() != key:
            continue
        existing = {
            str(item).strip()
            for item in list(entry.get("source_task_ids", []) or [])
            if str(item).strip()
        }
        existing.update(source_task_ids)
        entry["source_task_ids"] = sorted(existing)
        entry["confidence"] = max(float(entry.get("confidence", 0.0) or 0.0), confidence)
        entry["updated_at"] = _utc_now()
        if supersedes:
            entry["supersedes"] = supersedes
        return
    bucket.append(
        {
            "text": normalized_text,
            "confidence": confidence,
            "source_task_ids": sorted({task_id for task_id in source_task_ids if task_id}),
            "updated_at": _utc_now(),
            "supersedes": supersedes,
        }
    )
    if len(bucket) > limit:
        del bucket[:-limit]


class ContextCuratorStore:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.base_dir = runtime_dir / "context"
        self.projects_dir = self.base_dir / "projects"
        self.chats_dir = self.base_dir / "chats"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    def remember_preplan(
        self,
        *,
        project_key: str,
        chat_root: str,
        user_message: str,
        surface_hints: dict[str, Any] | None = None,
        curator_summary: str = "",
        lead_summary: str = "",
        source_task_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project_ctx = self.load_project_context(project_key)
        chat_ctx = self.load_chat_context(chat_root, project_key=project_key)
        hints = surface_hints if isinstance(surface_hints, dict) else {}
        source_ids = [
            str(item).strip()
            for item in list(source_task_ids or []) or []
            if str(item).strip()
        ]
        surfaces = [
            str(item).strip().lower()
            for item in list(hints.get("surfaces", []) or [])
            if str(item).strip()
        ]
        recommended_delegate_intents = [
            str(item).strip().lower()
            for item in list(hints.get("recommended_delegate_intents", []) or [])
            if str(item).strip()
        ]

        working_set_text = (
            f"Solicitud: {_compact_text(user_message, 240)}"
            + (f" | surfaces={', '.join(surfaces)}" if surfaces else "")
        )
        _append_unique_item(
            project_ctx["working_set"],
            text=working_set_text,
            confidence=0.7,
            source_task_ids=source_ids,
            limit=10,
        )
        _append_unique_item(
            chat_ctx["working_set"],
            text=working_set_text,
            confidence=0.8,
            source_task_ids=source_ids,
            limit=12,
        )

        for line in _split_compact_lines(curator_summary, limit=6):
            _append_unique_item(
                project_ctx["durable_facts"],
                text=line,
                confidence=0.65,
                source_task_ids=source_ids,
                limit=20,
            )
            _append_unique_item(
                chat_ctx["durable_facts"],
                text=line,
                confidence=0.75,
                source_task_ids=source_ids,
                limit=14,
            )

        for line in _split_compact_lines(lead_summary, limit=4):
            _append_unique_item(
                project_ctx["decisions"],
                text=line,
                confidence=0.6,
                source_task_ids=source_ids,
                limit=12,
            )
            _append_unique_item(
                chat_ctx["decisions"],
                text=line,
                confidence=0.7,
                source_task_ids=source_ids,
                limit=10,
            )

        for intent in recommended_delegate_intents[:4]:
            _append_unique_item(
                chat_ctx["next_actions"],
                text=f"delegate:{intent}",
                confidence=0.55,
                source_task_ids=source_ids,
                limit=10,
            )

        if "?" in str(user_message or ""):
            _append_unique_item(
                chat_ctx["open_questions"],
                text=_compact_text(user_message, 260),
                confidence=0.5,
                source_task_ids=source_ids,
                limit=10,
            )

        self._finalize_context(project_ctx, project_key=project_key, chat_root="")
        self._finalize_context(chat_ctx, project_key=project_key, chat_root=chat_root)
        self._write_project_context(project_key, project_ctx)
        self._write_chat_context(chat_root, chat_ctx)
        return project_ctx, chat_ctx

    def remember_phase_summary(
        self,
        *,
        project_key: str,
        chat_root: str,
        phase: str,
        output: str,
        source_task_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        project_ctx = self.load_project_context(project_key)
        chat_ctx = self.load_chat_context(chat_root, project_key=project_key)
        source_ids = [
            str(item).strip()
            for item in list(source_task_ids or []) or []
            if str(item).strip()
        ]
        normalized_phase = str(phase or "").strip().lower()
        lines = _split_compact_lines(output, limit=5)
        summary_text = f"{normalized_phase}: " + " | ".join(lines[:3]) if lines else f"{normalized_phase}: sin datos"

        for line in lines:
            target_bucket = "decisions" if normalized_phase.startswith("lead_") else "durable_facts"
            _append_unique_item(
                project_ctx[target_bucket],
                text=f"{normalized_phase}: {line}",
                confidence=0.6 if normalized_phase.startswith("lead_") else 0.65,
                source_task_ids=source_ids,
                limit=18,
            )
            _append_unique_item(
                chat_ctx["working_set"],
                text=f"{normalized_phase}: {line}",
                confidence=0.75,
                source_task_ids=source_ids,
                limit=16,
            )

        self._finalize_context(project_ctx, project_key=project_key, chat_root="")
        self._finalize_context(chat_ctx, project_key=project_key, chat_root=chat_root)
        self._write_project_context(project_key, project_ctx)
        self._write_chat_context(chat_root, chat_ctx)
        return project_ctx, chat_ctx, _compact_text(summary_text, 320)

    def remember_invalidation(
        self,
        *,
        project_key: str,
        chat_root: str,
        reason: str,
        affected_phases: list[str] | None = None,
        source_task_ids: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project_ctx = self.load_project_context(project_key)
        chat_ctx = self.load_chat_context(chat_root, project_key=project_key)
        source_ids = [
            str(item).strip()
            for item in list(source_task_ids or []) or []
            if str(item).strip()
        ]
        affected = [
            str(item).strip().lower()
            for item in list(affected_phases or []) or []
            if str(item).strip()
        ]
        invalidation_text = (
            f"{_compact_text(reason, 220)}"
            + (f" | phases={', '.join(affected)}" if affected else "")
        )
        _append_unique_item(
            project_ctx["invalidations"],
            text=invalidation_text,
            confidence=0.8,
            source_task_ids=source_ids,
            limit=16,
        )
        _append_unique_item(
            chat_ctx["invalidations"],
            text=invalidation_text,
            confidence=0.9,
            source_task_ids=source_ids,
            limit=16,
        )
        if affected:
            for phase_name in affected:
                _append_unique_item(
                    chat_ctx["open_questions"],
                    text=f"revisar_de_nuevo:{phase_name}",
                    confidence=0.6,
                    source_task_ids=source_ids,
                    limit=12,
                )
        self._finalize_context(project_ctx, project_key=project_key, chat_root="")
        self._finalize_context(chat_ctx, project_key=project_key, chat_root=chat_root)
        self._write_project_context(project_key, project_ctx)
        self._write_chat_context(chat_root, chat_ctx)
        return project_ctx, chat_ctx

    def load_project_context(self, project_key: str) -> dict[str, Any]:
        path = self.projects_dir / f"{_slug(project_key)}.json"
        return self._load_context(path, project_key=project_key, chat_root="")

    def load_chat_context(self, chat_root: str, *, project_key: str = "") -> dict[str, Any]:
        path = self.chats_dir / f"{_slug(chat_root)}.json"
        return self._load_context(path, project_key=project_key, chat_root=chat_root)

    def build_summary(self, payload: dict[str, Any], *, max_items_per_section: int = 3) -> str:
        if not isinstance(payload, dict):
            return ""
        lines: list[str] = []
        for section_name, label in (
            ("working_set", "working_set"),
            ("durable_facts", "durable_facts"),
            ("decisions", "decisions"),
            ("next_actions", "next_actions"),
            ("open_questions", "open_questions"),
        ):
            entries = _coerce_item_list(payload.get(section_name, []))
            if not entries:
                continue
            texts = [str(item.get("text", "")).strip() for item in entries[:max_items_per_section] if str(item.get("text", "")).strip()]
            if texts:
                lines.append(f"{label}: " + " | ".join(texts))
        return "\n".join(lines[:5])

    def _load_context(self, path: Path, *, project_key: str, chat_root: str) -> dict[str, Any]:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return self._normalize_context(raw, project_key=project_key, chat_root=chat_root)
            except Exception:
                pass
        return self._empty_context(project_key=project_key, chat_root=chat_root)

    def _write_project_context(self, project_key: str, payload: dict[str, Any]) -> None:
        path = self.projects_dir / f"{_slug(project_key)}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except FileNotFoundError:
            pass

    def _write_chat_context(self, chat_root: str, payload: dict[str, Any]) -> None:
        path = self.chats_dir / f"{_slug(chat_root)}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except FileNotFoundError:
            pass

    def _empty_context(self, *, project_key: str, chat_root: str) -> dict[str, Any]:
        return {
            "version": PROJECT_CONTEXT_VERSION,
            "project_key": str(project_key or "").strip(),
            "chat_root": str(chat_root or "").strip(),
            "working_set": [],
            "durable_facts": [],
            "decisions": [],
            "open_questions": [],
            "invalidations": [],
            "next_actions": [],
            "source_task_ids": [],
            "updated_at": _utc_now(),
        }

    def _normalize_context(self, payload: Any, *, project_key: str, chat_root: str) -> dict[str, Any]:
        base = self._empty_context(project_key=project_key, chat_root=chat_root)
        if not isinstance(payload, dict):
            return base
        base["project_key"] = str(payload.get("project_key", project_key) or project_key).strip()
        base["chat_root"] = str(payload.get("chat_root", chat_root) or chat_root).strip()
        for section in ("working_set", "durable_facts", "decisions", "open_questions", "invalidations", "next_actions"):
            base[section] = _coerce_item_list(payload.get(section, []))
        base["source_task_ids"] = [
            str(item).strip()
            for item in list(payload.get("source_task_ids", []) or [])
            if str(item).strip()
        ]
        base["updated_at"] = str(payload.get("updated_at", "") or _utc_now())
        return base

    def _finalize_context(self, payload: dict[str, Any], *, project_key: str, chat_root: str) -> None:
        source_ids: set[str] = set()
        for section in ("working_set", "durable_facts", "decisions", "open_questions", "invalidations", "next_actions"):
            normalized = _coerce_item_list(payload.get(section, []))
            payload[section] = normalized
            for item in normalized:
                for task_id in list(item.get("source_task_ids", []) or []):
                    if str(task_id).strip():
                        source_ids.add(str(task_id).strip())
        payload["version"] = PROJECT_CONTEXT_VERSION
        payload["project_key"] = str(project_key or payload.get("project_key", "")).strip()
        payload["chat_root"] = str(chat_root or payload.get("chat_root", "")).strip()
        payload["source_task_ids"] = sorted(source_ids)
        payload["updated_at"] = _utc_now()
