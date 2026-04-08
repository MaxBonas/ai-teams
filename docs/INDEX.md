# AI Team — Índice de Documentación

**Actualizado**: 2026-04-03
**Suite validada**: `858 passed`
**Máquina**: `ORCH-01`

---

## Documentos operativos (fuentes de verdad)

| Documento | Qué contiene |
|---|---|
| `../task.md` | Backlog vivo: pendiente activo, siguiente bloque, riesgos |
| `../HANDOFF.md` | Estado del sistema, protocolo entre máquinas, scripts clave |
| `HISTORY.md` | Registro condensado de hitos por bloque (0-6) |
| `ARCHITECTURE_PLAN.md` | Mapa de fricciones arquitectónicas y plan de acción priorizado |
| `MODEL_POLICY.md` | Catálogo de modelos, tiers, reglas de routing Pro-first + fallback |
| `MULTIMODEL_ROUTING_REFERENCE.md` | Pools TL/worker, quorum deliberativo, env vars, fixes de robustez (2026-04-03) |
| `NAMING_COLLISION_INVESTIGATION.md` | Taxonomía de capas y colisiones de nombres; norma `.aiteam/` |
| `COMMUNICATION_GUIDE_FOR_DEVS.md` | Cómo hablar con agentes sin ambigüedad de capa |
| `DECISION_LOG.md` | ADR — registro de decisiones arquitectónicas |

## Documentos de visión activa (features pendientes)

| Documento | Qué contiene | Estado |
|---|---|---|
| `ROUTING_EDITOR_VISION.md` | Vista perfecta de configuración de roles, providers, modelos, fallbacks | Parcialmente implementado (MVP editable operativo) |
| `ROUTING_CATALOG_VIEW.md` | Estado actual del catálogo de routing: capas, blockers, overrides | Vigente |
| `CONVERSATIONAL_AGENTS_PLAN.md` | Sesiones conversacionales reales por agente y proyecto | Pendiente |
| `CHAT_UX_COMPACT_FLOW_VISION.md` | UX compacto estilo Claude App (RunDisclosure, CompactLaneRow) | Pendiente |
| `MCP_CLI_SKILLS_ROADMAP.md` | Fases de integración de herramientas externas vía MCP/CLI/Skills | Pendiente |
| `EXTERNAL_PROJECT_RUNTIME_GAPS.md` | Gaps en proyectos externos — deuda viva en explicabilidad de estados | Parcialmente cerrado (B9 completo) |

## Documentos de visión implementada (referencia)

| Documento | Qué contiene | Estado |
|---|---|---|
| `LEAD_ADAPTIVE_FLOW_VISION.md` | Lead adaptativo: RunHealthReport, PAUSE/SKIP/DEGRADE, briefing, memoria | ✅ Implementado (A1-A5, bloque 5) |
| `LEAD_QUORUM_PROJECT_CONTEXT_VISION.md` | Plan/Quorum, planes persistidos, `.aiteam/instructions.md` | ✅ Implementado (B8, bloque 3) |

## Documentos de referencia

| Documento | Qué contiene |
|---|---|
| `INTERNAL_QUALITIES_ROADMAP.md` | Cualidades no negociables del producto |
| `INTEGRATION_GUIDE.md` | Contrato de adaptadores, wrapping de herramientas externas |
| `SECURITY_COMPLIANCE.md` | Políticas de compliance: guardrails, redacción, doble aprobación prod |
| `SECURITY_AUDIT.md` | 5 categorías de control de seguridad |
| `PRODUCTION_ROLLOUT_RUNBOOK.md` | Despliegue en stage/prod, health checks, rollback |
| `EXTERNAL_TOOLS_INVENTORY.md` | Herramientas externas candidatas (WhatsApp, Remotion, etc.) |
| `LEARNING_REGISTRY_SCHEMA.md` | Schema JSONL para el registro de aprendizaje |
| `PROJECT_LEARNING_GUIDE.md` | Guía para capturar fallos y aprendizajes |

## Archivo histórico

Los documentos de diseño, playbooks y handoffs de sesión de los bloques 0-6 están en `docs/archive/`.

No usar como backlog activo ni como descripción del estado actual.

---

## Lectura recomendada según objetivo

**Retomar el desarrollo tras un pull:**
→ `../HANDOFF.md` → `../task.md`

**Entender qué se ha construido y por qué:**
→ `HISTORY.md` → `ARCHITECTURE_PLAN.md`

**Implementar una nueva feature:**
→ `../task.md` (candidatos) → doc de visión correspondiente → `ARCHITECTURE_PLAN.md`

**Entender el routing multimodelo:**
→ `MULTIMODEL_ROUTING_REFERENCE.md` → `MODEL_POLICY.md` → `ROUTING_CATALOG_VIEW.md`

**Entender proyectos externos y `.aiteam/`:**
→ `NAMING_COLLISION_INVESTIGATION.md` → `EXTERNAL_PROJECT_RUNTIME_GAPS.md`

**Hablar con agentes sin confundir capas:**
→ `COMMUNICATION_GUIDE_FOR_DEVS.md` → `NAMING_COLLISION_INVESTIGATION.md`

**Desplegar a producción:**
→ `PRODUCTION_ROLLOUT_RUNBOOK.md` → `SECURITY_COMPLIANCE.md`
