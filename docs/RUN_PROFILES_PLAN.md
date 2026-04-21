# Run Profiles — Plan de diseño e implementación

**Fecha:** 2026-04-18  
**Estado:** En desarrollo activo — Fase 1 (gaps solo_lead)

---

## Modelo mental de perfiles

Cada perfil responde a una pregunta diferente:

| Perfil | Pregunta | Analogía |
|---|---|---|
| `solo_lead` | ¿Puede un solo cerebro resolver esto directamente? | Codex / Claude Code / OpenCode |
| `lead_quorum` | ¿Es el enfoque correcto antes de actuar? | Senior que consulta a peers antes de empezar |
| `ai_team_basic` | ¿Necesito más herramientas que tokens del Lead? | Tech Lead que orquesta un equipo pequeño |
| `ai_teams_full` | ¿Máxima cobertura, máximo rigor? | Equipo con arquitectura + revisión completa |

---

## Descripción por perfil

### `solo_lead`
Un único TEAM_LEAD que lee, escribe, valida y repara. Sin delegación, sin scouts, sin roles auxiliares.  
Loop: `lee → decide → escribe → valida (real) → repara si falla → avanza`.  
La validación es su propia herramienta de feedback (py_compile / pytest), no un gate externo.  
Ideal para: tareas concretas de código donde el scope es claro y el riesgo es recuperable.

### `lead_quorum`
Igual que `solo_lead` en ejecución. Añade un paso **antes** de actuar: el Lead somete su `WORKFLOW_PLAN` a un quorum de IAs senior.  
El quorum evalúa el **plan**, no el código. El Lead puede aceptar o rechazar el feedback con justificación.  
El quorum no bloquea — es un segundo par de ojos que puede señalar enfoques incorrectos o riesgos no vistos.  
Ideal para: refactors grandes, cambios arquitectónicos, tareas con alto riesgo de enfoque incorrecto.

### `ai_team_basic`
El Lead deja de escribir código directamente y pasa a planificar, delegar y sintetizar.  
Agentes especializados (ENGINEER, RESEARCHER) hacen el trabajo pesado.  
El Lead preserva sus tokens para decisiones; los agentes baratos/especializados ejecutan.  
Gates: solo QA (sin Reviewer). Scouts: activados.  
Ideal para: tareas complejas donde la ejecución es costosa o multi-step para un solo agente.

### `ai_teams_full`
`ai_team_basic` + quorum en el plan inicial.  
Review + QA como gates. Scouts. 2 ciclos de delegación. Peer consultation.  
El sistema actual `team_advanced` es esencialmente este perfil.  
Ideal para: entregas de alta criticidad, auditorías, cambios con impacto en producción.

---

## Matriz de capacidades

| Característica | `solo_lead` | `lead_quorum` | `ai_team_basic` | `ai_teams_full` |
|---|:---:|:---:|:---:|:---:|
| Scouts de estado | ❌ | ❌ | ✅ | ✅ |
| Ciclos de delegación | 0 | 0 | 2 | 2 |
| Quality gates | ninguno | ninguno | qa | review + qa |
| Quorum en plan | ❌ | ✅ | ❌ | ✅ |
| Evidence gate | saltado | saltado | activo | activo |
| Post-write validation | py_compile | py_compile | pytest | pytest |
| Repair cycles | 2 | 2 | 1 | 1 |
| Peer consultation | ❌ | ❌ | ❌ | ✅ |
| Advisory gates only | ✅ | ✅ | ❌ | ❌ |

---

## `ProfileConfig` — diseño de la tabla unificada

Archivo destino: `aiteam/run_profiles.py` (nuevo módulo)

```python
@dataclass
class ProfileConfig:
    skip_scouts: bool
    delegation_cycles: int
    quality_gates: list[str]            # [] | ["qa"] | ["review", "qa"]
    quorum_on_plan: bool
    peer_consultation: bool
    skip_evidence_gate: bool            # True en solo_lead / lead_quorum
    post_write_validation: str          # "none" | "py_compile" | "pytest"
    max_repair_cycles: int
    clarify_suppress_phases: list[str]
    advisory_gates_only: bool
    suppress_no_impl_phase_check: bool  # TEAM_LEAD cuenta como implementador
```

Objetivo: reemplazar todos los `if run_profile == "solo_lead"` dispersos por
`profile_config = PROFILE_CONFIGS[run_profile]` + lectura de campo.

---

## Gaps actuales de `solo_lead` (Fase 1)

### Por qué ciertas validaciones son contraproducentes

**Evidence gate**: diseñado para ENGINEER en `team_advanced` (detecta narrativa vacía).
Para TEAM_LEAD en `solo_lead`, evalúa el texto del output buscando patrones, no el workspace real.
Resultado: puede bloquear output legítimo por patrones de texto, y no detecta SyntaxError.
**Acción: saltarlo en solo_lead.**

**`no_implementation_phase` check**: en `lead_close_policy`, si no hay fase con hints de engineer
(`engineer`, `build`, `implement`...) se añade `no_implementation_phase` como señal bloqueante.
La fase `build` con role `TEAM_LEAD` puede no matchear estos hints si el phase_id fue custom.
**Acción: en solo_lead, suprimir este check o incluir TEAM_LEAD como implementador.**

**Supresión reactiva de CLARIFY**: el sistema tachona el `[CLARIFY]` después de que el Lead lo
escribe. El Lead puede haber estructurado su output asumiendo que haría la pregunta. Resultado
impredecible. La solución correcta es que el system prompt prohíba las preguntas operacionales
directamente — la supresión queda solo como safety net.

### Los 6 fixes

#### Fix 1 — Desactivar evidence gate en `solo_lead`
- **Archivo**: `aiteam/orchestrator.py:6593`
- **Cambio**: añadir condición `skip_evidence_gate` antes del bloque del evidence gate
- **Razón**: el workspace real es la fuente de verdad, no el texto del output

#### Fix 2 — Post-write py_compile con repair loop
- **Archivo**: `aiteam/orchestrator.py:6443` (después de `_extract_and_write_code_blocks`)
- **Cambio**: si `post_write_validation != "none"` y `files_written > 0` → correr validación real
  - Si pasa → `task.metadata["post_write_validated"] = True`
  - Si falla y hay repair cycles → alimentar error al Lead, relanzar ronda
  - Si falla y ciclos agotados → `mark_failed` con error real
- **Razón**: igual que Codex/OpenCode — validate after every write, repair if broken

#### Fix 3 — Bloquear `completed` con writes no validados
- **Archivo**: `aiteam/orchestrator.py:6825` (antes de `mark_completed`)
- **Cambio**: si `files_written_count > 0` y `post_write_validated != True` → `mark_failed`
- **Razón**: nunca `completed` si hay archivos escritos sin verificación posterior

#### Fix 4 — System prompt de `solo_lead` con instrucción directa
- **Archivo**: `aiteam/profiles.py` — sección del Lead cuando perfil es `solo_lead`
- **Cambio**: añadir bloque `PERFIL SOLO_LEAD` que prohíba CLARIFY operacional explícitamente
- **Razón**: la prohibición debe ser internalizada por el modelo, no suprimida por el sistema

#### Fix 5 — Continue prompt profile-aware en TeamChat.tsx
- **Archivo**: `ide-frontend/src/components/TeamChat.tsx:628` (`buildContinueDraft`)
- **Cambio**: rama para `run_profile === "solo_lead"` con instrucción Codex-style
- **Razón**: el prompt genérico no comunica la semántica de "inspecciona → valida → avanza o repara"

#### Fix 6 — Suprimir `no_implementation_phase` para `solo_lead`
- **Archivo**: `aiteam/lead_close_policy.py:416-429`
- **Cambio**: si `suppress_no_impl_phase_check` (o run_profile == "solo_lead") → skip el check
- **Razón**: TEAM_LEAD en solo_lead es el implementador; la heurística de fases no aplica

---

## Roadmap

```
Fase 0: ProfileConfig como tabla unificada (refactor sin cambio de comportamiento)
Fase 1: 6 fixes de solo_lead                             ← trabajo actual
Fase 2: lead_quorum (conectar quorum.py en lead_intake)
Fase 3: ai_team_basic (team_advanced sin scouts/reviewer, config diferente)
Fase 4: ai_teams_full (ai_team_basic + quorum flag)
```

---

## Referencias de código

```
api/chat_models.py:14-36        — definición run_profile en request
api/main.py:927-936             — normalización de aliases
api/main.py:939-978             — _direct_profile_phase_specs()
api/main.py:4248-4271           — scouts y delegation_cycles
aiteam/orchestrator.py:853-893  — _direct_profile_should_suppress_midrun_clarify
aiteam/orchestrator.py:5093     — skip evidence delegate spawning
aiteam/orchestrator.py:6443     — _extract_and_write_code_blocks (punto de inserción Fix 2)
aiteam/orchestrator.py:6593     — evidence gate (punto Fix 1)
aiteam/orchestrator.py:6825     — mark_completed (punto Fix 3)
aiteam/orchestrator.py:8419     — skip peer consultation
aiteam/lead_close_policy.py:416 — no_implementation_phase check (punto Fix 6)
aiteam/profiles.py:9            — _team_lead_system_prompt (punto Fix 4)
ide-frontend/src/components/TeamChat.tsx:628  — buildContinueDraft (punto Fix 5)
tests/test_run_profiles.py      — tests de perfiles existentes
```
