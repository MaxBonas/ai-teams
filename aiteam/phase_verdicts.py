from __future__ import annotations

import re
from typing import Any


_PHASE_VERDICT_BLOCK_RE = re.compile(
    r"(?is)\[PHASE_VERDICT\](.*?)\[/PHASE_VERDICT\]"
)
_PHASE_VERDICT_INLINE_RE = re.compile(
    r"(?im)^\s*\[PHASE_VERDICT:\s*([^\]]+?)\s*\]\s*$"
)
_KEY_VALUE_RE = re.compile(r"(?im)^\s*([a-zA-Z_]+)\s*:\s*(.+?)\s*$")
_REVIEW_REJECTED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict|estado|status|result|resultado)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:rechazad[oa]|rejected|changes_requested|cambios\s+solicitados?|solicita\s+cambios)\b|\b[\"']?(?:recomendaci[oó]n|recommendation|status|result|resultado|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:rechazad[oa]|rejected|changes_requested|cambios\s+solicitados?|solicita\s+cambios)\b)"
)
_REVIEW_BLOCKED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict|estado|status|result|resultado)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:bloquead[oa]|blocked)\b|\b[\"']?(?:recomendaci[oó]n|recommendation|status|result|resultado|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:bloquead[oa]|blocked)\b|\b(?:no\s+(?:puedo|puede|pude|se\s+puede)|cannot|can't|could\s+not)\s+(?:revisar|review|validar|validate|verificar|verify)\b|\b(?:insufficient|missing|lack(?:ing)?)\s+(?:review\s+)?evidence\b|\bevidencia\b.{0,40}\binsuficiente\b|\b(?:falta\s+evidencia|no\s+hay\s+evidencia)\b)"
)
_QA_BLOCKED_RE = re.compile(
    r"(?is)(?:^\s*(?:\*\*)?[\"']?(?:summary|resumen|estado|status|result|resultado|decisi[oó]n|decision|recomendaci[oó]n|recommendation|veredicto|verdict)[\"']?(?:\*\*)?\s*:\s*(?:\*\*)?[\"']?(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b[\"']?(?:summary|resumen|estado|status|result|resultado|recomendaci[oó]n|recommendation|veredicto|verdict)[\"']?\s*:\s*[\"']?(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b(?:summary|resumen|estado)\b.{0,240}\b(?:bloquead[oa]|blocked|failed|fallid[oa])\b|\b(?:no\s+(?:puedo|puede|pude|se\s+puede)|cannot|can't|could\s+not)\s+(?:validar|validate|verificar|verify|probar|test)\b|\b(?:insufficient|missing|lack(?:ing)?)\s+(?:qa\s+|validation\s+)?evidence\b|\b(?:evidencia\s+insuficiente|falta\s+evidencia|no\s+hay\s+evidencia|faltan?\s+(?:tests?|checks?|validaciones?|criterios\s+de\s+aceptaci[oó]n)|no\s+(?:hay|existen?)\s+(?:tests?|checks?|validaciones?|criterios\s+de\s+aceptaci[oó]n))\b)"
)
_GENERIC_BLOCKED_PREFIX_RE = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:blocked|bloquead[oa]|bloqueo(?:\s+contractual)?)(?:\*\*)?\s*[:—-]"
)
_SLICE_ID_RE = re.compile(r"(?i)\bslice\s+(\d+)\b")
_BUILD_SLICE_DRIFT_RE = re.compile(
    r"(?is)(?:\ba pesar de\b.{0,240}\b(?:directriz|directive)\b|\bslice de mayor impacto\b)"
)
# Matches engineer self-reports of blocking: "BLOQUEADA:" at line start, or
# specific system phrases that never appear in contextual prose.
# The colon is required for the bloqueada/bloqueado variants so we do not match
# contextual descriptions ("la fase está bloqueada porque...").
# NOTE: flags must be at the start of the entire pattern (Python 3.12+).
_ENGINEER_BLOCKED_LABEL_RE = re.compile(
    r"(?im)^\s*(?:bloqueada|bloqueado)\s*:"
)
_ENGINEER_BLOCKED_PHRASE_RE = re.compile(
    r"(?i)\b(?:evidencegate|evidence\s+gate|no\s+hay\s+evidencia|missing\s+evidence|bloqueo\s+contractual|no\s+puedo\s+(?:proceder|cumplir|implementar)|cannot\s+(?:proceed|comply|implement))\b"
)
_CODE_PATH_RE = re.compile(
    r"```(?:\w+\s+|\s*)path=[\"']?([^\"'\n\s`]+)[\"']?",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"(?i)(?:`([^`\n]+)`|(?<![:\w\\])([a-z0-9_.-]+(?:/[a-z0-9_.-]+)+|readme(?:\.md)?|[a-z0-9_.-]+\.[a-z0-9]{1,8})(?![\w:]))"
)
_ESCAPED_NEWLINE_PATH_RE = re.compile(
    r"(?i)\\[nrt]([a-z0-9_.-]+(?:/[a-z0-9_.-]+)+)"
)
# Phase name fragments that indicate an engineer role
_ENGINEER_PHASE_HINTS = ("engineer", "build", "implement", "develop", "code")
_INVALID_CONTRACT_OBJECTIVE_PREFIXES = (
    "[objetivo no especificado",
    "[contrato invalido",
    "ejecutar fase:",
)
_INVALID_CONTRACT_OBJECTIVE_VALUES = {
    "",
    "|",
    "-",
    "—",
    "n/a",
    "none",
    "null",
    "undefined",
    "sin objetivo",
    "objetivo pendiente",
    "por definir",
    "pendiente",
    "no especificado",
    "no especificada",
}

_COMMON_PROJECT_ROOT_HINTS = (
    ".aiteam",
    ".git",
    "src",
    "tests",
    "docs",
    "api",
    "config",
    "scripts",
    "runtime",
    "ide-frontend",
)

_COMMON_FILE_EXTENSIONS = {
    "bat",
    "c",
    "cc",
    "cfg",
    "cmd",
    "cpp",
    "cs",
    "css",
    "csv",
    "go",
    "h",
    "hpp",
    "html",
    "ini",
    "java",
    "js",
    "json",
    "jsx",
    "kt",
    "lock",
    "md",
    "php",
    "ps1",
    "py",
    "pyi",
    "rb",
    "rs",
    "sass",
    "scss",
    "sh",
    "sql",
    "svelte",
    "swift",
    "toml",
    "ts",
    "tsx",
    "txt",
    "vue",
    "xml",
    "yaml",
    "yml",
}

_INTERNAL_ROLE_PATH_PREFIXES = {
    "team_lead",
    "lead",
    "engineer",
    "eng",
    "reviewer",
    "review",
    "qa",
    "researcher",
    "research",
    "scout",
}

_INTERNAL_PROVIDER_PATH_PREFIXES = {
    "anthropic",
    "openai",
    "google",
    "groq",
    "gemini",
    "claude",
}


def _looks_like_noise_path_hint(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith(("r'", 'r"', "rf'", 'rf"', "fr'", 'fr"', "f'", 'f"')):
        return True
    if any(marker in normalized for marker in ("=", ",")) or any(ch.isspace() for ch in normalized):
        return True
    if any(ch in normalized for ch in ("^", "$", "[", "]", "(", ")", "{", "}", "*", "+", "?", "|", "'", '"')):
        return True
    if normalized in {"e.g", "i.e"}:
        return True
    if re.fullmatch(r"\d+(?:\.\d+)+", normalized):
        return True
    if re.fullmatch(r"\d+\.[xX]", normalized):
        return True
    parts = normalized.rsplit(".", 1)
    if len(parts) == 2 and "/" not in normalized:
        ext = parts[1]
        if re.fullmatch(r"\d{1,4}", ext):
            return True
    if "/" not in normalized and re.fullmatch(r"[a-z][\w-]*\d+\.\d[\w.-]*", normalized):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)?/\d+(?:\.\d+)?", normalized):
        return True
    if "/" not in normalized and re.fullmatch(r"\.[a-z0-9]{1,8}", normalized):
        return True
    if "/" not in normalized and normalized.count(".") >= 1:
        parts = [part for part in normalized.split(".") if part]
        if len(parts) >= 2:
            ext = parts[-1]
            if ext not in _COMMON_FILE_EXTENSIONS:
                return True
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if (
        "/" in normalized
        and "." not in normalized
        and not normalized.startswith(_COMMON_PROJECT_ROOT_HINTS)
        and re.fullmatch(r"[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_-]*)+", normalized)
    ):
        return True
    if "/" in normalized and "." not in normalized:
        left, _, right = normalized.partition("/")
        if (
            left in _INTERNAL_ROLE_PATH_PREFIXES
            and re.fullmatch(r"[a-z_]+-\d+", right)
        ):
            return True
    if any(marker in normalized for marker in ("/subscription/", "/api/", "/thread/")):
        return True
    if "/" in normalized:
        left = normalized.split("/", 1)[0]
        if left in _INTERNAL_PROVIDER_PATH_PREFIXES:
            return True
    return False


def _is_non_gate_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return True
    return normalized.startswith(("lead_", "delegate_", "plan_"))


def _is_support_phase_id(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith("plan_") and any(
        marker in normalized
        for marker in ("research", "discovery", "analysis", "constraints", "context")
    ):
        return True
    return normalized.startswith(
        ("lead_", "delegate_", "scout_", "lead_preflight_", "lead_report_")
    )


def _gate_kind_for_phase(phase_id: str, role_hint: str = "") -> str:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role_hint or "").strip().lower() or _phase_role_hint(normalized_phase)
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


def _is_slice_source_phase(phase_id: str) -> bool:
    normalized = str(phase_id or "").strip().lower()
    if not normalized:
        return False
    return normalized == "lead_intake" or normalized.startswith("plan_")


def _select_approved_slice_from_verdicts(
    verdicts: dict[str, dict[str, Any]],
) -> str:
    explicit = dict(verdicts.get("lead_intake", {}) or {})
    explicit_slice = str(explicit.get("slice_id", "") or "").strip()
    if explicit_slice:
        return explicit_slice
    for phase_id, entry in verdicts.items():
        if _is_support_phase_id(phase_id):
            continue
        if not _is_slice_source_phase(phase_id):
            continue
        if not isinstance(entry, dict):
            continue
        slice_id = str(entry.get("slice_id", "") or "").strip()
        if slice_id:
            return slice_id
    return ""


def _normalize_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "failed": "failed",
        "failure": "failed",
        "fallido": "failed",
        "ok": "approved",
        "pass": "approved",
        "passed": "approved",
        "approved": "approved",
        "approve": "approved",
        "aprobado": "approved",
        "completado": "completed",
        "complete": "completed",
        "completed": "completed",
        "done": "completed",
        "partial": "partial",
        "parcial": "partial",
        "blocked": "blocked",
        "bloqueado": "blocked",
        "bloqueada": "blocked",
        "reject": "rejected",
        "rejected": "rejected",
        "rechazado": "rejected",
        "rechazada": "rejected",
        "unknown": "unknown",
    }
    return mapping.get(raw, raw)


def _normalize_contract_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "ok": "aligned",
        "aligned": "aligned",
        "alineado": "aligned",
        "drift": "drift",
        "desalineado": "drift",
        "mismatch": "drift",
        "unknown": "unknown",
    }
    return mapping.get(raw, raw)


def _parse_reason_codes(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,|]", str(value or ""))
    return [
        str(item).strip().lower()
        for item in items
        if str(item).strip()
    ]


def _phase_role_hint(phase_id: str) -> str:
    normalized = str(phase_id or "").strip().lower()
    if normalized == "review":
        return "reviewer"
    if normalized == "qa":
        return "qa"
    if normalized == "build":
        return "engineer"
    if normalized in {"lead_intake", "lead_close"}:
        return "team_lead"
    if normalized.startswith("plan_"):
        return "planner"
    if "review" in normalized:
        return "reviewer"
    if "qa" in normalized or "validation" in normalized or normalized.startswith("validate"):
        return "qa"
    # Custom phase names that embed an engineer hint (e.g. "engineer_toc_implementation",
    # "implement_auth", "code_review_backend").
    if any(h in normalized for h in _ENGINEER_PHASE_HINTS):
        return "engineer"
    return ""


def _normalize_path_hint(value: object) -> str:
    raw = str(value or "").strip().strip("`\"'")
    if not raw:
        return ""
    if "..." in raw or "…" in raw:
        return ""
    while raw.startswith("\\n") or raw.startswith("\\r") or raw.startswith("\\t"):
        raw = raw[2:].lstrip()
    raw = (
        raw.replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\t", " ")
    )
    normalized = raw.replace("\\", "/").lower()
    if "://" in normalized:
        return ""
    if normalized.startswith("./"):
        normalized = normalized[2:]
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.strip("/").rstrip(".,;:!?)]}")
    return normalized


def is_missing_contract_objective(value: object) -> bool:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    if not raw:
        return True
    normalized = raw.lower()
    if normalized in _INVALID_CONTRACT_OBJECTIVE_VALUES:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in _INVALID_CONTRACT_OBJECTIVE_PREFIXES
    )


def extract_path_candidates(text: str) -> list[str]:
    raw_text = str(text or "")
    if not raw_text.strip():
        return []

    candidates: list[str] = []
    for match in _ESCAPED_NEWLINE_PATH_RE.finditer(raw_text):
        normalized = _normalize_path_hint(match.group(1))
        if normalized and not _looks_like_noise_path_hint(normalized):
            candidates.append(normalized)

    for match in _CODE_PATH_RE.finditer(raw_text):
        normalized = _normalize_path_hint(match.group(1))
        if normalized and not _looks_like_noise_path_hint(normalized):
            candidates.append(normalized)

    for match in _PATH_TOKEN_RE.finditer(raw_text):
        token = match.group(1) or match.group(2) or ""
        normalized = _normalize_path_hint(token)
        if not normalized:
            continue
        if _looks_like_noise_path_hint(normalized):
            continue
        if "/" not in normalized and "." not in normalized and normalized != "readme":
            continue
        candidates.append(normalized)

    return list(dict.fromkeys(candidates))[:16]


def infer_objective_path_hints(objective: str) -> list[str]:
    raw_objective = str(objective or "").strip()
    if not raw_objective:
        return []

    objective_lower = raw_objective.lower()
    hints: list[str] = []

    for candidate in extract_path_candidates(raw_objective):
        hints.append(candidate)

    if "readme" in objective_lower:
        hints.extend(["readme", "readme.md"])

    if any(
        keyword in objective_lower
        for keyword in (
            " test",
            "tests",
            "pytest",
            "unit test",
            "integration test",
            "cobertura",
            "coverage",
        )
    ):
        hints.extend(["tests", "test_", "_test.", ".spec.", ".test."])

    if any(
        keyword in objective_lower
        for keyword in ("docs", "document", "documenta", "markdown", "md ")
    ):
        hints.extend(["docs", ".md"])

    return list(dict.fromkeys(_normalize_path_hint(item) for item in hints if _normalize_path_hint(item)))[:12]


def _path_matches_hint(path: str, hint: str) -> bool:
    normalized_path = _normalize_path_hint(path)
    normalized_hint = _normalize_path_hint(hint)
    if not normalized_path or not normalized_hint:
        return False

    if normalized_hint in {"readme", "readme.md"}:
        basename = normalized_path.rsplit("/", 1)[-1]
        return basename == "readme.md" or basename.startswith("readme.")
    if normalized_hint in {"tests", "docs"}:
        return normalized_path == normalized_hint or normalized_path.startswith(normalized_hint + "/")
    if normalized_hint.endswith("/"):
        return normalized_path.startswith(normalized_hint)
    if normalized_hint in {"test_", "_test.", ".spec.", ".test."}:
        return normalized_hint in normalized_path
    if (
        "/" in normalized_hint
        and "." not in normalized_hint.rsplit("/", 1)[-1]
    ):
        return normalized_path == normalized_hint or normalized_path.startswith(
            normalized_hint + "/"
        )
    if "/" in normalized_hint:
        return normalized_path == normalized_hint or normalized_path.endswith("/" + normalized_hint)
    return normalized_hint in normalized_path


def detect_continuation_drift(
    *,
    objective: str,
    output_text: str = "",
    proposed_paths: list[str] | None = None,
) -> dict[str, Any]:
    expected_hints = infer_objective_path_hints(objective)
    if not expected_hints:
        return {}

    normalized_paths = [
        _normalize_path_hint(item)
        for item in list(proposed_paths or extract_path_candidates(output_text))
        if _normalize_path_hint(item)
    ]
    normalized_paths = list(dict.fromkeys(normalized_paths))[:12]
    if not normalized_paths:
        return {}

    if any(
        _path_matches_hint(path, hint)
        for path in normalized_paths
        for hint in expected_hints
    ):
        return {}

    expected_preview = ", ".join(expected_hints[:4])
    proposed_preview = ", ".join(normalized_paths[:4])
    summary = (
        f"continuation drift: objetivo acotado a {expected_preview} "
        f"pero la propuesta apunta a {proposed_preview}"
    )
    return {
        "phase_id": "build",
        "status": "rejected",
        "contract_status": "drift",
        "reason_codes": ["slice_drift", "continuation_drift"],
        "summary": summary[:240],
        "expected_path_hints": expected_hints[:8],
        "proposed_paths": normalized_paths[:8],
    }


def detect_contract_path_drift(
    *,
    proposed_paths: list[str] | None = None,
    forbidden_path_hints: list[str] | None = None,
    allowed_module_path_hints: list[str] | None = None,
) -> dict[str, Any]:
    normalized_paths = [
        _normalize_path_hint(item)
        for item in list(proposed_paths or [])
        if _normalize_path_hint(item)
    ]
    normalized_paths = list(dict.fromkeys(normalized_paths))[:16]
    if not normalized_paths:
        return {}

    forbidden_hints = [
        _normalize_path_hint(item)
        for item in list(forbidden_path_hints or [])
        if _normalize_path_hint(item)
    ]
    allowed_hints = [
        _normalize_path_hint(item)
        for item in list(allowed_module_path_hints or [])
        if _normalize_path_hint(item)
    ]

    for path in normalized_paths:
        if any(_path_matches_hint(path, hint) for hint in forbidden_hints):
            summary = f"contract drift: ruta prohibida detectada ({path})"
            return {
                "phase_id": "build",
                "status": "rejected",
                "contract_status": "drift",
                "reason_codes": ["slice_drift", "forbidden_path"],
                "summary": summary[:240],
                "proposed_paths": [path],
                "forbidden_path_hints": forbidden_hints[:8],
            }

    if not allowed_hints:
        return {}

    for path in normalized_paths:
        if not (path.startswith("src/") and path.endswith(".py")):
            continue
        basename = path.rsplit("/", 1)[-1]
        if basename == "__init__.py":
            continue
        if any(_path_matches_hint(path, hint) for hint in allowed_hints):
            continue
        summary = (
            "contract drift: modulo Python fuera del scope permitido "
            f"({path}); permitido: {', '.join(allowed_hints[:4])}"
        )
        return {
            "phase_id": "build",
            "status": "rejected",
            "contract_status": "drift",
            "reason_codes": ["slice_drift", "forbidden_module_scope"],
            "summary": summary[:240],
            "proposed_paths": [path],
            "allowed_module_path_hints": allowed_hints[:8],
        }

    return {}


def _structured_phase_verdict(text: str, *, phase_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    block_match = _PHASE_VERDICT_BLOCK_RE.search(text)
    if block_match:
        body = str(block_match.group(1) or "")
        for key, value in _KEY_VALUE_RE.findall(body):
            payload[str(key).strip().lower()] = str(value).strip()
    else:
        inline_match = _PHASE_VERDICT_INLINE_RE.search(text)
        if inline_match:
            payload["status"] = str(inline_match.group(1) or "").strip()
    if not payload:
        return {}

    status = _normalize_status(payload.get("status") or payload.get("verdict"))
    contract_status = _normalize_contract_status(payload.get("contract_status"))
    reason_codes = _parse_reason_codes(payload.get("reason_codes") or payload.get("reason_code"))
    slice_id = str(payload.get("slice_id", "") or "").strip()
    summary = str(payload.get("summary", "") or "").strip()
    verdict_phase_id = str(payload.get("phase_id", phase_id) or phase_id).strip().lower()

    verdict: dict[str, Any] = {
        "phase_id": verdict_phase_id or str(phase_id or "").strip().lower(),
        "role_hint": _phase_role_hint(verdict_phase_id or phase_id),
        "source": "structured",
    }
    if status:
        verdict["status"] = status
    if contract_status:
        verdict["contract_status"] = contract_status
    if reason_codes:
        verdict["reason_codes"] = list(dict.fromkeys(reason_codes))[:8]
    if slice_id:
        verdict["slice_id"] = slice_id
    if summary:
        verdict["summary"] = summary[:240]
    return verdict


def extract_phase_verdict(text: str, *, phase_id: str) -> dict[str, Any]:
    raw_text = str(text or "").strip()
    normalized_phase = str(phase_id or "").strip().lower()
    if not raw_text or not normalized_phase:
        return {}

    structured = _structured_phase_verdict(raw_text, phase_id=normalized_phase)
    if structured:
        return structured

    verdict: dict[str, Any] = {
        "phase_id": normalized_phase,
        "role_hint": _phase_role_hint(normalized_phase),
        "source": "heuristic",
    }
    reason_codes: list[str] = []
    gate_kind = _gate_kind_for_phase(
        normalized_phase,
        str(verdict.get("role_hint", "") or ""),
    )

    if gate_kind == "review" and _REVIEW_REJECTED_RE.search(raw_text):
        verdict["status"] = "rejected"
        reason_codes.append("review_rejected")
    elif gate_kind == "review" and (
        _GENERIC_BLOCKED_PREFIX_RE.search(raw_text)
        or _REVIEW_BLOCKED_RE.search(raw_text)
    ):
        verdict["status"] = "blocked"
        reason_codes.append("review_blocked")
    elif gate_kind == "qa" and (
        _GENERIC_BLOCKED_PREFIX_RE.search(raw_text)
        or _QA_BLOCKED_RE.search(raw_text)
    ):
        verdict["status"] = "blocked"
        reason_codes.append("qa_blocked")
    elif gate_kind == "build":
        slice_match = _SLICE_ID_RE.search(raw_text)
        if slice_match:
            verdict["slice_id"] = str(slice_match.group(1) or "").strip()
        if _BUILD_SLICE_DRIFT_RE.search(raw_text):
            verdict["contract_status"] = "drift"
            reason_codes.append("slice_drift")
        # Detect engineer/build self-reported blocking for any build-like gate.
        _sample = raw_text[:300]
        if (
            _GENERIC_BLOCKED_PREFIX_RE.search(_sample)
            or _ENGINEER_BLOCKED_LABEL_RE.search(_sample)
            or _ENGINEER_BLOCKED_PHRASE_RE.search(raw_text)
        ):
            verdict["status"] = "blocked"
            reason_codes.append("engineer_blocked")
    elif any(h in normalized_phase for h in _ENGINEER_PHASE_HINTS):
        # Custom engineer phase names (e.g. "engineer_toc_implementation"):
        # detect self-reported blocking using structural keywords only.
        _sample = raw_text[:300]
        if (
            _GENERIC_BLOCKED_PREFIX_RE.search(_sample)
            or _ENGINEER_BLOCKED_LABEL_RE.search(_sample)
            or _ENGINEER_BLOCKED_PHRASE_RE.search(raw_text)
        ):
            verdict["status"] = "blocked"
            reason_codes.append("engineer_blocked")

    if _is_slice_source_phase(normalized_phase):
        slice_match = _SLICE_ID_RE.search(raw_text)
        if slice_match:
            verdict["slice_id"] = str(slice_match.group(1) or "").strip()

    if reason_codes:
        verdict["reason_codes"] = list(dict.fromkeys(reason_codes))[:8]
    if len(verdict) <= 3:
        return {}
    return verdict


def coerce_phase_verdicts(payload: object) -> dict[str, dict[str, Any]]:
    verdicts: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return verdicts
    for raw_phase_id, raw_entry in payload.items():
        phase_id = str(raw_phase_id or "").strip().lower()
        if not phase_id or not isinstance(raw_entry, dict):
            continue
        entry: dict[str, Any] = {"phase_id": phase_id}
        status = _normalize_status(raw_entry.get("status"))
        contract_status = _normalize_contract_status(raw_entry.get("contract_status"))
        source = str(raw_entry.get("source", "") or "").strip().lower()
        role_hint = str(raw_entry.get("role_hint", "") or "").strip().lower()
        slice_id = str(raw_entry.get("slice_id", "") or "").strip()
        summary = str(raw_entry.get("summary", "") or "").strip()
        reason_codes = _parse_reason_codes(raw_entry.get("reason_codes", []))
        if status:
            entry["status"] = status
        if contract_status:
            entry["contract_status"] = contract_status
        if source:
            entry["source"] = source
        if role_hint:
            entry["role_hint"] = role_hint
        if slice_id:
            entry["slice_id"] = slice_id
        if summary:
            entry["summary"] = summary[:240]
        if reason_codes:
            entry["reason_codes"] = list(dict.fromkeys(reason_codes))[:8]
        verdicts[phase_id] = entry
    return verdicts


def derive_run_verdict_from_phase_verdicts(payload: object) -> dict[str, Any]:
    verdicts = coerce_phase_verdicts(payload)
    if not verdicts:
        return {}

    failures: list[str] = []
    approved_slice = _select_approved_slice_from_verdicts(verdicts)

    review_verdict = _select_primary_gate_verdict(verdicts, "review")
    review_status = str(review_verdict.get("status", "") or "").strip().lower()
    review_reason_codes = _parse_reason_codes(review_verdict.get("reason_codes", []))
    if review_status in {"rejected", "failed"} or "review_rejected" in review_reason_codes:
        failures.append("review:rejected_decision")
    elif review_status == "blocked" or "review_blocked" in review_reason_codes:
        failures.append("review:blocked_status")

    qa_verdict = _select_primary_gate_verdict(verdicts, "qa")
    qa_status = str(qa_verdict.get("status", "") or "").strip().lower()
    qa_reason_codes = _parse_reason_codes(qa_verdict.get("reason_codes", []))
    if qa_status in {"blocked", "rejected", "failed"} or "qa_blocked" in qa_reason_codes:
        failures.append("qa:blocked_status")

    build_verdict = _select_primary_gate_verdict(verdicts, "build")
    build_contract_status = str(build_verdict.get("contract_status", "") or "").strip().lower()
    build_reason_codes = _parse_reason_codes(build_verdict.get("reason_codes", []))
    build_slice = str(build_verdict.get("slice_id", "") or "").strip() or "unknown"
    if build_contract_status == "drift" or "slice_drift" in build_reason_codes:
        failures.append(f"build:slice_drift:{approved_slice or 'unknown'}->{build_slice}")

    if not failures:
        return {}

    unique_failures = list(dict.fromkeys(failures))
    return {
        "state": "rejected",
        "result": "fallido",
        "reason_codes": unique_failures[:24],
        "policy_signals": ["semantic_gate_failed"],
        "policy_review_required": True,
        "semantic_gate_applied": True,
        "semantic_gate_failures": unique_failures[:12],
        "evidence_gate_applied": False,
        "evidence_gate_failures": [],
        "advisory_mode": False,
        "degraded_delivery": False,
        "reconstructed_from_phase_verdicts": True,
    }


def build_phase_verdict_prompt_block(*, phase_id: str, role: str) -> str:
    normalized_phase = str(phase_id or "").strip().lower()
    normalized_role = str(role or "").strip().upper()
    if not normalized_phase:
        return ""
    if normalized_role == "REVIEWER":
        status_help = "approved|rejected|blocked|unknown"
    elif normalized_role == "QA":
        status_help = "approved|blocked|rejected|failed|unknown"
    else:
        status_help = "completed|approved|blocked|rejected|partial|unknown"
    decision_mapping = ""
    if normalized_role == "REVIEWER":
        decision_mapping = (
            "Mapa de decision: APPROVED=>approved; CHANGES_REQUESTED o REJECTED=>rejected; BLOCKED=>blocked.\n"
        )
    elif normalized_role == "QA":
        decision_mapping = (
            "Mapa de decision: PASSED=>approved; CONDITIONAL_PASS=>approved si no bloquea, si no blocked; FAILED=>failed; BLOCKED=>blocked.\n"
        )
    return (
        "\n\n[PHASE_VERDICT_CONTRACT]\n"
        "Incluye al final un bloque estructurado exactamente con este formato:\n"
        "[PHASE_VERDICT]\n"
        f"phase_id: {normalized_phase}\n"
        f"status: {status_help}\n"
        "reason_codes: code1, code2\n"
        "contract_status: aligned|drift|unknown\n"
        "slice_id: <numero o vacio>\n"
        "summary: una linea breve\n"
        "[/PHASE_VERDICT]\n"
        f"{decision_mapping}"
        "Si no aplica un campo, dejalo vacio o usa unknown. No cambies los nombres de clave.\n"
        "[/PHASE_VERDICT_CONTRACT]"
    )
