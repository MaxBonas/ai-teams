# AI Teams — Historia de desarrollo

**Última actualización**: 2026-04-03
**Suite al cerrar este bloque**: `858 passed`

Registro condensado de hitos completados. Para detalle técnico de cada uno, ver `docs/archive/`.

---

## Bloque 0 — Fundación del orquestador (hasta 2026-03-27)

- Orquestador multi-agente base con roles `team_lead`, `engineer`, `reviewer`, `qa`
- Adapters de subscripción y API, router híbrido con prioridad Pro-first + fallback
- TaskBoard, EventLogger, LCP directives iniciales (`[CLARIFY]`, `[SKIP]`, `[ABORT_PHASES]`, `[DELEGATE]`)
- Evidence gate inicial, scoring de output, `TaskState` básico
- Tests unitarios base

## Bloque 1 — EPIC-1/2/3: workflow dinámico y visibilidad (2026-03-28)

- **EPIC-1**: fases dinámicas generadas por el Team Lead (`lead_intake` → fases → `lead_close`)
- **EPIC-2**: agent lanes en tiempo real — visibilidad por agente en el IDE
- **EPIC-3**: rediseño de UI del StatusPanel
- Three-tier delegation, scout layer, scoring reform, `WAITING_USER` conversacional
- LCP directives ampliadas: `[FORCE_GATE]`, `[RETRY_ROUTE]`, `[ADVISORY_MODE]`
- Diseño documentado en `docs/archive/DESIGN_2026_03_28.md` y `docs/archive/DESIGN_2026_03_31.md`

## Bloque 2 — Estabilización y portabilidad entre máquinas (2026-03-31 → 2026-04-01)

- SQLite como persistencia principal para `tasks` y `workflow_state`
- API/UI leen SQLite como fuente normal; JSON solo como compatibilidad residual
- Evidence gate corregido: no acepta `git diff` ajeno en modo simulado
- Bootstrap local de `venv/` por máquina; `runtime/` local, no compartido
- Flujo `git pull → prepare_dev_env.bat → seguir trabajando` validado en `MAX-GAMINGPC` y `ORCH-01`
- `FileLockRegistry` endurecido para Windows (retry/backoff)
- `AITEAM_SIM_MODE` como nombre canónico (B5)
- Suite estabilizada: `757 passed` → `776 passed`

## Bloque 3 — B7/B8/B9: routing editable, proyectos externos, quorum (2026-04-01 → 2026-04-02)

**B7 — Routing editable:**
- B7a: catálogo de routing con payload versionado, capas `defaults/override_local/effective`, blockers estables
- B7b: overrides locales de routing persistidos (`routing_overrides.py`, endpoints API, validación)
- B7c: frontend editable — modo inspección/edición, guardar/reset, validación visible

**B8 — Proyectos externos: contexto y planes:**
- B8a: planes persistidos como `.md` en el proyecto (no estado opaco del runtime)
- B8b: `.aiteam/instructions.md` por proyecto leído e inyectado en prompt del Lead
- B8c: Plan/Quorum — Lead + consultor avanzado para consolidar el plan antes de la run productiva

**B9 — Proyectos externos: aislamiento:**
- B9a: runtime de proyectos externos migrado a `.aiteam/` (migración automática)
- B9b: aislamiento de contexto por `project_root` en `context_curator` (namespace + hash corto para paths largos en Windows)
- B9c: `product_artifacts` en `last_chat_run` + sección dedicada en StatusPanel

**Otras mejoras del bloque:**
- `mode: "probe"` en chat (solo `lead_intake`, devuelve plan sin lanzar fases)
- Planning como modo de primera clase: evidence gate de planning, `run_mode` `architecture_review` y `roadmap`
- `peer_consultation_summary` visible en chat y progreso
- Anthropic restringido a `team_lead` en defaults
- Suite: `799 passed` → `823 passed`

## Bloque 4 — C-series: audit fixes de test_aiteams (2026-04-02)

Gaps identificados en auditoría forense del caso `test_aiteams` (videojuego):

- **C1**: delegate tasks creadas lazy (no en bulk al planificar) — `deferred_evidence_specs` + `_maybe_spawn_deferred_delegates()`
- **C2**: `continuation_policy` en `TeamChatRequest` (`auto`/`clean_retry`/`force_continue`) + `TaskState.ARCHIVED` + `taskboard.archive_incomplete_tasks()`
- **C3**: `_maybe_deposit_minimal_output()` deposita `PROJECT_PLAN.md` en workspace vacío cuando `lead_intake` completo pero `build` no arrancó

Suite: `823 passed`

## Bloque 5 — A-series: Lead adaptativo (2026-04-02 → 2026-04-03)

- **A1**: `run_health.py` — RunHealthReport estructurado inyectado en `lead_close` (gate rejections, routing errors, recursos ausentes, presupuesto consumido)
- **A2**: `[PAUSE_FOR_USER]` en `lead_close` — Lead pausa la run y pregunta al usuario; reutiliza `WAITING_USER` y reanudación vía chat
- **A3**: `[SKIP_PHASE]` y `[DEGRADE]` en `lead_close` — Lead salta fases irrecuperables o acepta entrega parcial con diagnóstico
- **A4**: briefing de capacidades pre-run — `lead_intake` recibe `== SYSTEM CAPABILITIES ==` cuando faltan API keys/modelos o hay MCPs degradados
- **A5**: `lead_memory.py` — memoria primaria del Lead por proyecto (`lead_memory.md`): historial de runs, decisiones, capacidades observadas, inyectado antes de `lead_intake`

Suite final: `858 passed`

## Bloque 6 — Estabilización ORCH-01 (2026-04-03)

- `pytest_local_stable.bat` / `pytest_local_stable.py` — runner estable para Windows con PermissionError en tmp manejado
- `autotools.py::_probe_mcp_command`: fast-path para `npx` (de ~200s bloqueado a inmediato)
- `cli.py::cmd_system_check`: `mcp_probe_timeout` capeado a 5s (alineado con provider probes)
- `conftest.py` endurecido — aislamiento de env vars por test más robusto
- Nuevos tests: `test_autotools.py`, `test_cli_providers.py`, `test_evidence_gate.py` ampliado

---

## Documentos archivados

Los documentos de diseño, playbooks y handoffs de sesión de cada bloque están en `docs/archive/`.
No usarlos como backlog activo ni como descripción del estado actual.
