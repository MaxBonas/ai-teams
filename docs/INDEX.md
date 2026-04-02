# AI Team — Indice de Documentacion

**Actualizado**: 2026-04-02
**Maquina validada**: `MAX-GAMINGPC`
**Tests**: `763 passed`
**Persistencia**: SQLite en `runtime/aiteam.db` para `tasks` y `workflow_state`, con compatibilidad JSON residual solo para tests/constructores legacy
**Vision del proyecto**: IDE multimodelo con equipo de agentes (Team Lead, Scout, Researcher, Engineer, Reviewer, QA) que delega por tiers, mantiene continuidad por proyecto y aplica quality gates antes de cerrar trabajo de build.

---

## Taxonomia documental

- **Fuente de verdad operativa**: backlog vivo, estado validado, orden de prioridades y decisiones de refactor
- **Referencia vigente**: diseño, arquitectura o políticas útiles, pero no backlog operativo
- **Historico**: contexto útil para auditoría o trazabilidad; no usar para decidir el siguiente trabajo

---

## Documentos activos (fuentes de verdad)

| Documento | Qué contiene |
|---|---|
| `../walkthrough.md` | Walkthrough tecnico de la estabilizacion reciente en `MAX-GAMINGPC` |
| `../task.md` | Estado actual, backlog inmediato y siguientes pasos |
| `TASKS_2026_03_28.md` | **Backlog principal** — epics completados, tareas pendientes, definición de done |
| `DESIGN_2026_03_31.md` | Diseño de scoring, scout layer, WAITING_USER, LCP directives (implementado) |
| `DESIGN_2026_03_28.md` | Diseño de Agent Lanes y Dynamic Phases (implementado) |
| `ARCHITECTURE.md` | Arquitectura del sistema: módulos, roles, flujo de fases, calidad gates |
| `ARCHITECTURE_PLAN.md` | **Plan de fricciones arquitectónicas**: qué tocar, cuándo y en qué orden. Fuente de verdad para decisiones de refactor. |
| `MODEL_POLICY.md` | Catálogo de modelos, tiers, reglas de ruteo Pro-first + fallback |
| `ROUTING_CATALOG_VIEW.md` | Objetivos y diseño de la nueva vista consultable de routing por rol |
| `EXTERNAL_PROJECT_RUNTIME_GAPS.md` | Investigación de gaps en proyectos externos: tareas bloqueadas, artefactos no creados y separación pendiente entre producto y estado interno |
| `CONVERSATIONAL_AGENTS_PLAN.md` | Objetivo de agentes con continuidad conversacional real por proyecto |
| `SECURITY_COMPLIANCE.md` | Políticas de compliance: guardrails, redacción, doble aprobación prod |
| `PRODUCTION_ROLLOUT_RUNBOOK.md` | Despliegue en stage/prod, health checks, rollback |

---

## Documentos de referencia (no activos, pero útiles)

| Documento | Qué contiene |
|---|---|
| `INTERNAL_QUALITIES_ROADMAP.md` | Cualidades no negociables del producto (valores del sistema) |
| `MCP_CLI_SKILLS_ROADMAP.md` | Fases de integración de herramientas externas vía MCP/CLI |
| `INTEGRATION_GUIDE.md` | Contrato de adaptadores, prioridades, wrapping de programas externos |
| `SECURITY_AUDIT.md` | 5 categorías de control de seguridad (referencia defensiva) |
| `EXTERNAL_TOOLS_INVENTORY.md` | Inventario de herramientas externas candidatas (WhatsApp, Remotion, etc.) |
| `LEARNING_REGISTRY_SCHEMA.md` | Schema JSONL para el registro de aprendizaje del sistema |
| `PROJECT_LEARNING_GUIDE.md` | Guía para capturar fallos y aprendizajes del sistema |
| `DECISION_LOG.md` | Registro ADR de decisiones arquitectónicas |

---

## Documentos historicos del repo raiz

Estos archivos se conservan por trazabilidad, pero no deben usarse como backlog vivo ni como descripción actual del sistema:

| Documento | Estado |
|---|---|
| `../TASKS.md` | Historico consolidado |
| `../PLAN_AGENTIDAD.md` | Historico / exploracion |
| `../PLAN_MEJORAS.md` | Historico / exploracion |
| `../ROADMAP_FLUJOS_Y_AGENTES.md` | Historico / supersedido |
| `../ROADMAP_PRODUCCION_AITEAM.md` | Historico / supersedido |

---

## Lectura recomendada según objetivo

### Quiero entender el estado actual y el backlog
→ `../task.md` + `TASKS_2026_03_28.md`

### Quiero entender la estabilizacion reciente en gamingpc
→ `../walkthrough.md` + `../task.md`

### Quiero implementar una nueva feature
→ `DESIGN_2026_03_31.md` → `ARCHITECTURE.md` → `TASKS_2026_03_28.md` (buscar el epic)

### Quiero entender cómo funciona el routing multimodelo
→ `MODEL_POLICY.md` → `ROUTING_CATALOG_VIEW.md` → `ARCHITECTURE.md#router`

### Quiero entender la visión de agentes conversacionales
→ `CONVERSATIONAL_AGENTS_PLAN.md` → `DESIGN_2026_03_28.md`

### Quiero desplegar a producción
→ `PRODUCTION_ROLLOUT_RUNBOOK.md` → `SECURITY_COMPLIANCE.md`

### Quiero integrar una herramienta externa
→ `MCP_CLI_SKILLS_ROADMAP.md` → `INTEGRATION_GUIDE.md` → `EXTERNAL_TOOLS_INVENTORY.md`

---

## Archivos eliminados (2026-03-31)

Los siguientes 22 archivos fueron eliminados por ser legacy, superseded o redundantes:

- `AGENT_FLOW_IMPROVEMENT_PLAN.md` — análisis histórico de frameworks (crewAI, LangGraph, etc.)
- `AUDIT_2026_03_27.md` — auditoría histórica, issues incorporados en TASKS
- `BATCH2_SPEC.md` — spec de paralelismo (implementado en EPIC-1)
- `DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` — auditoría fase 2 (supersedida)
- `EXECUTION_QUICK_START.md` — guía Q1 (marcada como histórica por su propio autor)
- `LLM_CONNECTION_SYSTEM.md` — supersedida por MODEL_POLICY.md + ARCHITECTURE.md
- `NOTEBOOKLM_AND_LEARNING_IMPLEMENTATION.md` + 7 `NOTEBOOKLM_*.md` — integración NotebookLM (no alineada con visión IDE multimodelo)
- `PLAN_PRODUCCION_CONSOLIDADO.md` — consolidación supersedida por TASKS
- `PROJECT_AUDIT_AND_HARDENING_PLAN.md` — plan de hardening (completado)
- `SPRINT_ROADMAP_Q1_2026.md` — todos los sprints completados, histórico
- `TASKS_AI_TEAM.md` — supersedida por `TASKS_2026_03_28.md`
- `TEAM_FLOW_ANALYSIS.md` — diagnóstico incorporado en DESIGN_2026_03_31.md
- `TEAMS_DEEP_IMPLEMENTATION_PLAN.md` — plan wave (supersedido por TASKS)
- `TEST_MATRIX_SPRINTS_1_2_3.md` — matriz Q1 (sprints completados)
- `UI_REDESIGN_2026_03_28.md` — spec del rediseño (implementado en EPIC-3)
