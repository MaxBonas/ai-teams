"""
workflow_planner.py — Planificacion dinamica de fases por el Team Lead.

El Team Lead emite un bloque [WORKFLOW_PLAN]...[/WORKFLOW_PLAN] en su output.
Este modulo lo parsea y convierte en una lista de PhaseSpec.
Si el plan es invalido o ausente, se devuelve None y el llamador usa default_phases().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------

@dataclass
class PhaseSpec:
    """Especificacion de una fase generada por el Team Lead."""
    phase_id: str
    role: str          # "RESEARCHER" | "ENGINEER" | "REVIEWER" | "QA"
    objective: str
    depends_on: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aliases de rol (para infer_role_from_phase_id y validacion flexible)
# ---------------------------------------------------------------------------

ROLE_ALIASES: dict[str, str] = {
    # RESEARCHER
    "research":      "RESEARCHER",
    "discovery":     "RESEARCHER",
    "analysis":      "RESEARCHER",
    "investigate":   "RESEARCHER",
    "explore":       "RESEARCHER",
    "context":       "RESEARCHER",
    "constraints":   "RESEARCHER",
    # ENGINEER
    "build":         "ENGINEER",
    "implement":     "ENGINEER",
    "develop":       "ENGINEER",
    "code":          "ENGINEER",
    "fix":           "ENGINEER",
    "refactor":      "ENGINEER",
    "engineering":   "ENGINEER",
    "plan_engineer": "ENGINEER",
    # REVIEWER
    "review":        "REVIEWER",
    "security":      "REVIEWER",
    "audit":         "REVIEWER",
    "validate_design": "REVIEWER",
    "plan_risks":    "REVIEWER",
    "risks":         "REVIEWER",
    # QA
    "qa":            "QA",
    "test":          "QA",
    "verify":        "QA",
    "acceptance":    "QA",
    "validation":    "QA",
}

VALID_ROLES = {"RESEARCHER", "ENGINEER", "REVIEWER", "QA"}

# Roles reservados para el sistema (no pueden aparecer en el plan del Lead)
_RESERVED_PHASES = {"lead_intake", "lead_close"}

# Maximo de fases que el Lead puede proponer (excluyendo lead_intake/lead_close)
_MAX_USER_PHASES = 10


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def parse_workflow_plan(lead_output: str) -> Optional[list[PhaseSpec]]:
    """
    Extrae y valida el WORKFLOW_PLAN del output del Team Lead.

    Retorna la lista de PhaseSpec si es valida, o None si:
    - No hay bloque WORKFLOW_PLAN
    - El bloque tiene menos de 1 fase o mas de MAX_USER_PHASES
    - Alguna fase tiene rol invalido
    - Las dependencias referencian phase_ids inexistentes
    - El grafo tiene ciclos
    """
    block = _extract_plan_block(lead_output)
    if block is None:
        return None

    phases = _parse_phase_blocks(block)
    if not phases:
        return None

    if len(phases) > _MAX_USER_PHASES:
        return None

    if not _validate_phases(phases):
        return None

    return phases


def default_phases(mode: str) -> list[PhaseSpec]:
    """
    Fases por defecto identicas al comportamiento hardcodeado anterior.
    Usado como fallback cuando parse_workflow_plan retorna None.
    """
    if mode == "classic":
        return [
            PhaseSpec(
                phase_id="discovery",
                role="RESEARCHER",
                objective="Recopila contexto tecnico y restricciones para ejecutar la solicitud del usuario.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="build",
                role="ENGINEER",
                objective="Implementa la solucion principal con foco en codigo listo para revisar.",
                depends_on=["discovery"],
            ),
            PhaseSpec(
                phase_id="review",
                role="REVIEWER",
                objective="Revisa la salida de build, valida calidad, seguridad y mantenibilidad.",
                depends_on=["build"],
            ),
            PhaseSpec(
                phase_id="qa",
                role="QA",
                objective="Valida criterios de aceptacion y riesgos de regresion.",
                depends_on=["review"],
            ),
        ]
    else:  # sprint5 (default)
        return [
            PhaseSpec(
                phase_id="plan_research",
                role="RESEARCHER",
                objective="Construye base de decision: restricciones, riesgos tecnicos y supuestos criticos.",
                depends_on=[],
            ),
            PhaseSpec(
                phase_id="plan_engineering",
                role="ENGINEER",
                objective="Define corte de implementacion: tareas secuenciadas y criterios de aceptacion.",
                depends_on=["plan_research"],
            ),
            PhaseSpec(
                phase_id="plan_risks",
                role="REVIEWER",
                objective="Define quality gates, pruebas minimas y riesgos de release.",
                depends_on=["plan_research"],
            ),
            PhaseSpec(
                phase_id="build",
                role="ENGINEER",
                objective="Ejecuta el slice de mayor impacto definido en planning.",
                depends_on=["plan_engineering", "plan_risks"],
            ),
            PhaseSpec(
                phase_id="review",
                role="REVIEWER",
                objective="Revisa el build contra el plan acordado.",
                depends_on=["build"],
            ),
            PhaseSpec(
                phase_id="qa",
                role="QA",
                objective="Valida criterios de aceptacion para lo ejecutado.",
                depends_on=["build"],
            ),
        ]


def infer_role_from_phase_id(phase_id: str) -> str:
    """
    Infiere el rol mas apropiado para un phase_id por coincidencia de alias.
    Retorna "ENGINEER" si no hay coincidencia.
    """
    lower = phase_id.lower()
    for keyword, role in ROLE_ALIASES.items():
        if keyword in lower:
            return role
    return "ENGINEER"


def normalize_role(raw: str) -> Optional[str]:
    """
    Normaliza un string de rol a uno de VALID_ROLES.
    Acepta variantes como 'engineer', 'Engineer', 'ENGINEER', 'eng'.
    """
    upper = raw.strip().upper()
    if upper in VALID_ROLES:
        return upper
    # Aliases cortos
    short_map = {
        "ENG": "ENGINEER",
        "ENGR": "ENGINEER",
        "DEV": "ENGINEER",
        "RES": "RESEARCHER",
        "RSRCH": "RESEARCHER",
        "REV": "REVIEWER",
        "REVIEW": "REVIEWER",
        "TEAM_LEAD": None,   # no permitido en planes de usuario
        "LEAD": None,
        "TL": None,
    }
    return short_map.get(upper)


# ---------------------------------------------------------------------------
# Internals: extraccion del bloque
# ---------------------------------------------------------------------------

def _extract_plan_block(text: str) -> Optional[str]:
    """Extrae el contenido entre [WORKFLOW_PLAN] y [/WORKFLOW_PLAN]."""
    match = re.search(
        r'\[WORKFLOW_PLAN\](.*?)\[/WORKFLOW_PLAN\]',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip()


# ---------------------------------------------------------------------------
# Internals: parser de fases
# ---------------------------------------------------------------------------

def _parse_phase_blocks(block: str) -> list[PhaseSpec]:
    """
    Parsea el bloque WORKFLOW_PLAN linea a linea.

    Formato esperado:
        - phase_id: research
          role: RESEARCHER
          objective: Investigar restricciones
          depends_on: []
        - phase_id: build
          role: ENGINEER
          objective: Implementar la solucion
          depends_on: [research]
    """
    phases: list[PhaseSpec] = []
    current: dict = {}

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if _is_phase_start(line):
            if current:
                spec = _build_spec(current)
                if spec:
                    phases.append(spec)
            val = _extract_value(line)
            current = {"phase_id": val} if val else {}
            continue

        if not current:
            continue

        key, val = _split_kv(line)
        if key is None:
            continue

        if key == "role":
            current["role"] = val.upper()
        elif key == "objective":
            current["objective"] = val
        elif key == "depends_on":
            current["depends_on"] = _parse_list(val)

    # No olvidar la ultima fase
    if current:
        spec = _build_spec(current)
        if spec:
            phases.append(spec)

    return phases


def _is_phase_start(line: str) -> bool:
    return bool(re.match(r'^-?\s*phase_id\s*:', line, re.IGNORECASE))


def _extract_value(line: str) -> Optional[str]:
    """Extrae el valor despues del primer ':' en una linea."""
    parts = line.split(":", 1)
    if len(parts) < 2:
        return None
    val = parts[1].strip().strip('"').strip("'")
    return val if val else None


def _split_kv(line: str) -> tuple[Optional[str], str]:
    """Divide 'key: value' en (key, value). Retorna (None, '') si no es KV."""
    # Eliminar el guion inicial de lista
    clean = re.sub(r'^-\s*', '', line)
    if ':' not in clean:
        return None, ''
    key, _, val = clean.partition(':')
    return key.strip().lower(), val.strip().strip('"').strip("'")


def _parse_list(raw: str) -> list[str]:
    """
    Parsea un valor de lista: '[]', '[a]', '[a, b, c]' o 'a, b, c'.
    """
    stripped = raw.strip().strip('[]')
    if not stripped:
        return []
    items = [x.strip().strip('"').strip("'") for x in stripped.split(',')]
    return [x for x in items if x]


def _build_spec(current: dict) -> Optional[PhaseSpec]:
    """Construye un PhaseSpec desde un dict parcial. Retorna None si faltan campos."""
    phase_id = current.get("phase_id", "").strip()
    if not phase_id:
        return None

    # Normalizar rol: si no viene, inferir del phase_id
    # Si viene explicitamente pero es invalido (ej. TEAM_LEAD), rechazar la spec
    raw_role = current.get("role", "")
    if raw_role:
        role = normalize_role(raw_role)
        if role is None:
            return None  # rol explicito invalido → rechazar toda la spec
    else:
        role = infer_role_from_phase_id(phase_id)

    objective = current.get("objective", "").strip()
    if not objective:
        objective = f"Ejecutar fase: {phase_id}"

    depends_on = current.get("depends_on", [])

    return PhaseSpec(
        phase_id=phase_id,
        role=role,
        objective=objective,
        depends_on=depends_on,
    )


# ---------------------------------------------------------------------------
# Internals: validacion
# ---------------------------------------------------------------------------

def _validate_phases(phases: list[PhaseSpec]) -> bool:
    """
    Valida:
    1. Todos los roles son validos
    2. No hay phase_ids reservados
    3. Las dependencias referencian phase_ids existentes
    4. No hay ciclos en el grafo
    """
    ids = {p.phase_id for p in phases}

    for p in phases:
        # Roles validos
        if p.role not in VALID_ROLES:
            return False

        # phase_ids reservados
        if p.phase_id in _RESERVED_PHASES:
            return False

        # Dependencias existentes
        for dep in p.depends_on:
            if dep not in ids:
                return False

    return _no_cycles(phases)


def _no_cycles(phases: list[PhaseSpec]) -> bool:
    """Kahn's algorithm para deteccion de ciclos en el DAG de fases."""
    graph: dict[str, list[str]] = {p.phase_id: list(p.depends_on) for p in phases}
    in_degree: dict[str, int] = {p.phase_id: 0 for p in phases}

    for p in phases:
        for dep in p.depends_on:
            in_degree[p.phase_id] = in_degree.get(p.phase_id, 0)

    # Recalcular in-degree (cuantas fases dependen de esta)
    reverse: dict[str, list[str]] = {p.phase_id: [] for p in phases}
    for p in phases:
        for dep in p.depends_on:
            if dep in reverse:
                reverse[dep].append(p.phase_id)

    in_deg: dict[str, int] = {p.phase_id: len(p.depends_on) for p in phases}

    queue = [pid for pid, deg in in_deg.items() if deg == 0]
    visited = 0

    while queue:
        node = queue.pop(0)
        visited += 1
        for neighbor in reverse.get(node, []):
            in_deg[neighbor] -= 1
            if in_deg[neighbor] == 0:
                queue.append(neighbor)

    return visited == len(phases)
