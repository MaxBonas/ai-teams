# AI Team Documentation Index

**Last Updated**: 2026-03-26
**Current Phase**: Roadmap activo de flujo, continuidad y agentes conversacionales
**Status**: Sistema operativo con documentacion historica y roadmap vivo

---

## Estado real verificado

- Suite completa: **282 passing**
- Comando verificado: `venv/Scripts/python.exe -m pytest tests/ -q --tb=short`
- Fuente de verdad para trabajo activo: `ROADMAP_FLUJOS_Y_AGENTES.md`
- Backlog operativo consolidado: `TASKS.md`

---

## Lectura recomendada segun objetivo

### Quiero entender el estado actual
1. `README.md`
2. `ROADMAP_FLUJOS_Y_AGENTES.md`
3. `TASKS.md`
4. `docs/CONVERSATIONAL_AGENTS_PLAN.md`
5. `docs/TEAM_FLOW_ANALYSIS.md`

### Quiero trabajar en el roadmap activo
1. `ROADMAP_FLUJOS_Y_AGENTES.md`
2. `TASKS.md`
3. `docs/BATCH2_SPEC.md`
4. `docs/CONVERSATIONAL_AGENTS_PLAN.md`

### Quiero revisar documentacion historica
1. `docs/SPRINT_ROADMAP_Q1_2026.md`
2. `docs/EXECUTION_QUICK_START.md`
3. `docs/TEST_MATRIX_SPRINTS_1_2_3.md`
4. `docs/DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md`

### Quiero entender arquitectura y operacion
1. `docs/ARCHITECTURE.md`
2. `docs/SECURITY_COMPLIANCE.md`
3. `docs/PRODUCTION_ROLLOUT_RUNBOOK.md`
4. `docs/LLM_CONNECTION_SYSTEM.md`

---

## Mapa rapido

| Documento | Estado | Uso recomendado |
|---|---|---|
| `README.md` | vigente | overview funcional del proyecto |
| `ROADMAP_FLUJOS_Y_AGENTES.md` | vigente | roadmap vivo por batches/fixes |
| `TASKS.md` | vigente | backlog y estado de ejecucion |
| `docs/CONVERSATIONAL_AGENTS_PLAN.md` | vigente | objetivo de agentes conversacionales reales |
| `docs/TEAM_FLOW_ANALYSIS.md` | vigente | diagnostico de problemas de flujo |
| `docs/BATCH2_SPEC.md` | vigente | especificacion del tramo de paralelismo |
| `docs/SPRINT_ROADMAP_Q1_2026.md` | historico | referencia del hardening Q1 completado |
| `docs/EXECUTION_QUICK_START.md` | historico | guia de ejecucion del plan Q1 |
| `docs/TEST_MATRIX_SPRINTS_1_2_3.md` | historico | matriz de tests del plan Q1 |

---

## Implementado / Parcial / Planificado

### Implementado
- Evidence gate robusto en mock
- Dependencias fallidas en `BLOCKED`
- Rounds y sub-iteraciones visibles
- Barreras de sub-iteracion y claim guard
- Meetings con menos ruido
- Mailbox conversacional basico Team Lead -> agente -> Team Lead
- Contexto por proyecto con `project_key`
- Observabilidad del flujo en dashboard, API y timeline UI

### Parcial
- Threads conversacionales persistentes por agente/proyecto
- Continuidad multi-turn basada en prompt enriquecido con thread
- Separacion memoria global vs proyecto, con compaction minima

### Planificado
- Adapters nativos con `messages[]`
- Conversacion mas profunda TL <-> agentes
- Cierre documental total y E2E del modelo multi-LLM

---

## Nota sobre documentos historicos

`docs/SPRINT_ROADMAP_Q1_2026.md`, `docs/EXECUTION_QUICK_START.md` y `docs/TEST_MATRIX_SPRINTS_1_2_3.md` siguen siendo utiles como referencia, pero ya no describen el trabajo activo del proyecto. Usarlos como contexto historico, no como fuente de verdad operativa.

---

## Siguiente accion recomendada

Empieza por `ROADMAP_FLUJOS_Y_AGENTES.md` y `TASKS.md`.
