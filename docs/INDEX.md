# AI Team — Índice de Documentación

**Actualizado**: 2026-03-31
**Tests**: 386 passing | 28 fallos pre-existentes (infraestructura, ver T-F en TASKS)
**Visión del proyecto**: IDE multimodelo con equipo de agentes (Team Lead, Researcher, Engineer, Reviewer, QA, Scout) que delega a modelos baratos, celebra reuniones multimodelo donde cada modelo aporta su perspectiva, y el Lead tiene la última palabra pero debe justificar los desacuerdos.

---

## Documentos activos (fuentes de verdad)

| Documento | Qué contiene |
|---|---|
| `TASKS_2026_03_28.md` | **Backlog principal** — epics completados, tareas pendientes, definición de done |
| `DESIGN_2026_03_31.md` | Diseño de scoring, scout layer, WAITING_USER, LCP directives (implementado) |
| `DESIGN_2026_03_28.md` | Diseño de Agent Lanes y Dynamic Phases (implementado) |
| `ARCHITECTURE.md` | Arquitectura del sistema: módulos, roles, flujo de fases, calidad gates |
| `MODEL_POLICY.md` | Catálogo de modelos, tiers, reglas de ruteo Pro-first + fallback |
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

## Lectura recomendada según objetivo

### Quiero entender el estado actual y el backlog
→ `TASKS_2026_03_28.md` + `DESIGN_2026_03_31.md`

### Quiero implementar una nueva feature
→ `DESIGN_2026_03_31.md` → `ARCHITECTURE.md` → `TASKS_2026_03_28.md` (buscar el epic)

### Quiero entender cómo funciona el routing multimodelo
→ `MODEL_POLICY.md` → `ARCHITECTURE.md#router`

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
