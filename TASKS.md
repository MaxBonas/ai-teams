# Tasks — AI Team Hybrid Orchestrator

> Ultima actualizacion: 2026-03-26 (auditoria y limpieza de planes)

## Completado

- [x] **Batch 1**: Shared workflow state, result propagation, gate iteration loop, eager dep check, team ledger (2026-03-21)
- [x] **Batch 2**: Agent sessions, tool dispatch, 7 API endpoints (2026-03-21)
- [x] **Batch 3**: MCP server lifecycle manager, catalog sync, 9 MCP API endpoints (2026-03-21)
- [x] **Batch 4**: Real LLM adapters (OpenAI/Anthropic/Google/Groq), team decisions + voting, async chat SSE, skill usage tracking (2026-03-21)
- [x] **UI Redesign**: Progressive disclosure metadata, composer compacto, progress visual con badges, OpsHub status badges, tipografia legible (2026-03-22)
- [x] **Batch 5**: Agentidad — self-delegation, cross-agent memory, session history en retries, eager processing (2026-03-22)
- [x] **Batch 6**: Agentidad avanzada — tool invocation `[USE_TOOL]`, peer dialogue 2 rondas, decision rank enforcement, skill ranking activo (2026-03-22)
- [x] **Backlog sweep**: Budget signaling, tool availability broadcast, agent specialization routing, conflict resolution protocol, mailbox read/unread + inbox queries + API (2026-03-22)
- [x] **Batch 7**: UI observability — event category filter en Timeline, mailbox inbox con unread badges + filtros, test suite 26 tests nuevos (2026-03-22)
- [x] **Batch 8**: Inteligencia de agentes — gate context enrichment, memory-driven prompts, tool recommendation engine, adaptive error recovery (2026-03-22)
- [x] **Batch 9**: Evidence Gate por fase — plan_*/lead_intake/lead_close/discovery fuera del gate; build/review/qa/security con gate. 12 tests nuevos (2026-03-22)
- [x] **Batch 10**: Conversational task detection — auto-deteccion de tareas teoricas, evidencia alternativa por doc/output, 15 tests nuevos (2026-03-22)
- [x] **AGENT_FLOW_IMPROVEMENT_PLAN**: Todos los batches A/B/C — WorkflowState, result propagation, team context, gate iteration loop, review feedback, eager processing, team ledger (2026-03-21/26). Ver `docs/AGENT_FLOW_IMPROVEMENT_PLAN.md`.
- [x] **Batch 11**: Estabilizacion de flujo actual — evidence gate robusto en mock, dependencias fallidas pasan a `BLOCKED`, sub-iteraciones visibles y trazabilidad enriquecida de `round/sub_iteration/gate_iteration` (2026-03-26)
- [x] **Auditoria de planes y modelos** (2026-03-26):
  - Todos los docs de sprint (Q1 2026) actualizados a estado real (271 tests, todos los sprints completados).
  - `docs/AGENT_FLOW_IMPROVEMENT_PLAN.md` marcado como completado.
  - Modelos actualizados en `aiteam/cli.py`: `gpt-4.1`, `gemini-2.0-flash`, `claude-3-5-haiku` añadido.
  - Respuestas mock mejoradas — ahora incluyen contexto de la tarea y aviso de configuracion real.
  - Como se hizo:
    - fallback en `aiteam/orchestrator.py` para aceptar output no vacio en modo simulado y registrar `evidence_reason`
    - propagacion de `dependency_failed` en `aiteam/taskboard.py` con desbloqueo correcto al reintentar/completar el padre
    - persistencia de `execution_sub_iteration` y eventos `round_sub_iteration` / `round_completed`
    - enrichment de `task_started`, `task_execution` y `gate_iteration` con contexto de ejecucion
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_dashboard.py -q` -> `28 passed`

## En Progreso

- [x] **Batch 12 — Conversational Agents reales por proyecto**
  - Objetivo: pasar de orquestacion por prompts aislados a agentes con hilo conversacional persistente por proyecto.
  - Alcance:
    - [x] `ConversationThread` persistente por `agent_id + project_root`.
    - [x] Persistencia en `runtime/sessions/threads/`.
    - [x] Adapters con soporte `messages[]` ademas de `prompt`.
    - [x] Orchestrator reutiliza historial previo al invocar de nuevo al mismo agente.
    - [x] Team Lead puede insertar mensajes en el hilo del agente y el agente responde en contexto.
  - Avance actual:
    - Batch 3 completado: threads persistentes + compaction minima.
    - Batch 4 completado: adapters/router con `messages[]` y backward compatibility.
    - Batch 5 completado: `_run_task()`, peer consultation y retries/gates usan `messages[]` compactos y ya existe cierre E2E conversacional.
  - Como se hizo hasta ahora:
    - `ConversationThread`, `ThreadStore` y persistencia por proyecto en `aiteam/agent_session.py`
    - `project_key` en memoria y aislamiento por proyecto en `aiteam/memory.py`
    - mailbox accionable integrado al hilo y reply trazable al Team Lead en `aiteam/orchestrator.py`
    - soporte `messages[]` en `aiteam/adapters/base.py`, `aiteam/adapters/api.py`, `aiteam/adapters/subscription.py`, `aiteam/adapters/external_program.py` y `aiteam/router.py`
    - orquestacion compacta de `system + thread reciente + user actual`, peers eficientes y retries de gates con `task_retry`
    - deduplicacion de turnos consecutivos para no ensuciar el hilo con repeticiones inutiles
    - test E2E conversacional completo validado en `tests/test_orchestrator.py`
  - Verificacion reciente: `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_memory_comms.py tests/test_router.py tests/test_api_adapter_live.py tests/test_cli_providers.py -q` -> `74 passed`
  - Archivos clave: `aiteam/agent_session.py`, `aiteam/adapters/api.py`, `aiteam/adapters/subscription.py`, `aiteam/orchestrator.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

- [x] **Siguiente tarea importante — Cierre E2E conversacional**
  - Objetivo: validar de punta a punta que el sistema ya trabaja conversacionalmente de forma consistente.
  - Como se hizo:
    - flujo E2E validado: task inicial -> feedback Team Lead -> gates -> respuesta final coherente
    - verificacion de mailbox, reply al Team Lead, continuidad y coherencia final
    - verificacion de eventos conversacionales y varias pasadas de review
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_memory_comms.py tests/test_router.py tests/test_api_adapter_live.py tests/test_cli_providers.py -q` -> `74 passed`
  - Archivos clave: `tests/test_orchestrator.py`, `aiteam/orchestrator.py`, `aiteam/agent_session.py`, `aiteam/mailbox.py`.

- [ ] **Prioridad inmediata — Readiness de modelos y providers**
  - Objetivo: asegurar que cuentas, APIs y modelos locales aptos para coding esten disponibles antes de profundizar mas el equipo multi-LLM.
  - Hacer:
    - verificar cuentas y claves de OpenAI, Anthropic, Google y Groq
    - definir integracion de modelos gratis de Zencoder/OpenCode Zen si son utilizables desde cuenta o custom provider
    - instalar y validar un modelo local de coding en esta maquina (preferencia: Qwen coder via Ollama o runtime equivalente)
    - decidir orden de fallback: suscripcion -> API -> local
    - consolidar y operar alertas de providers/modelos desde `provider_ops`
  - Archivos clave: `config/routing_policy.example.json`, `config/adapters.example.json`, `aiteam/adapters/`, `aiteam/router.py`.

- [x] **Politica dura de Team Lead y relevo avanzado**
  - Objetivo: asegurar que `team_lead` siempre use el mejor modelo cloud disponible y nunca un modelo local.
  - Como se hizo:
    - catalogo inicial en `aiteam/model_catalog.py` y `config/model_catalog.example.json`
    - regla dura en `aiteam/router.py`: `team_lead` solo admite `senior_cloud` o `advanced_api`
    - exclusión explicita de modelos locales para `team_lead`
    - relevo por salud real consumiendo `runtime/provider_smoke.json`
    - tests para preferencia senior cloud, rechazo de local y fallback a API avanzada
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_router.py -q`.
  - Archivos clave: `aiteam/model_catalog.py`, `aiteam/router.py`, `config/model_catalog.example.json`, `tests/test_router.py`.

- [x] **Catalogo vivo de modelos con ranking configurable**
  - Objetivo: hacer editable el ranking/capacidad/confianza sin hardcodear todo en el router.
  - Como se hizo:
    - carga del catalogo desde `runtime/model_catalog.json` con fallback a defaults en `aiteam/model_catalog.py`
    - override por runtime/maquina para ajustar prioridades reales sin tocar codigo
    - prueba en `tests/test_router.py` para verificar que el router respeta overrides del catalogo
    - documentacion en `docs/MODEL_POLICY.md`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_router.py tests/test_cli_providers.py -q` -> `30 passed`
  - Archivos clave: `aiteam/model_catalog.py`, `aiteam/router.py`, `runtime/model_catalog.json`, `docs/MODEL_POLICY.md`.

- [x] **Fallback economico sin descartar modelos baratos**
  - Objetivo: mantener modelos cheap/efficient dentro del sistema para degradacion inteligente por presupuesto o limites.
  - Como se hizo:
    - nuevo tier `budget_api` para modelos baratos en `runtime/model_catalog.json` y `config/model_catalog.example.json`
    - ajuste del router para preferir `budget_api` bajo presion de presupuesto API en roles no `team_lead`
    - documentacion de politica de fallback economico en `docs/MODEL_POLICY.md`
    - test de degradacion economica en `tests/test_router.py`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_router.py tests/test_cli_providers.py -q`
  - Archivos clave: `aiteam/router.py`, `runtime/model_catalog.json`, `config/model_catalog.example.json`, `docs/MODEL_POLICY.md`, `tests/test_router.py`.

- [x] **Vista operativa unificada y alertas de providers/modelos**
  - Objetivo: tener una fuente oficial del estado de modelos/providers y alertar cuando cambie.
  - Como se hizo:
    - `aiteam/provider_ops.py` unifica `provider_doctor`, `provider_smoke`, `provider_accounts` y `model_catalog`
    - `provider-ops` genera `runtime/provider_ops.json` y expone `team_lead_candidates`, `operational`, `degraded` y `alerts`
    - deteccion de cambios de estado entre ejecuciones con emision de `provider_ops_alert` a eventos y mailbox
    - el router ya consume `provider_ops.json` como fuente operativa principal para elegibilidad de `team_lead`
    - paneles `Provider Ops Summary`, `Provider Alerts` y tabla `Provider Ops` en dashboard
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_provider_ops.py tests/test_dashboard.py tests/test_router.py tests/test_cli_providers.py -q` -> `35 passed`
  - Archivos clave: `aiteam/provider_ops.py`, `aiteam/cli.py`, `aiteam/dashboard.py`, `runtime/provider_ops.json`, `tests/test_provider_ops.py`, `tests/test_dashboard.py`.

## Backlog Prioritario

- [x] **Fix 4 — Barrera de dependencia mas segura en paralelo**
  - Problema: `_claim_ready_tasks()` reclama todas las READY del lote y puede dar sensacion de solape excesivo o carreras sutiles.
  - Como se hizo:
    - guard en `aiteam/taskboard.py` para revalidar dependencias al reclamar una task y rechazar READY adelantados
    - barrera publica con `taskboard.checkpoint()` y evento `sub_iteration_barrier` al final de cada batch en `aiteam/orchestrator.py`
    - nueva suite `tests/test_parallel_taskboard.py` para claims concurrentes, dependencia compartida y barrera
    - defaults operativos documentados en `docs/BATCH2_SPEC.md`: `dev=1`, `stage=2`, `prod=2-3`, manteniendo el default de codigo en `1`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_parallel_taskboard.py tests/test_orchestrator.py tests/test_taskboard.py tests/test_dashboard.py -q` -> `33 passed`
  - Archivos clave: `aiteam/orchestrator.py`, `aiteam/taskboard.py`, `tests/test_parallel_taskboard.py`, `docs/BATCH2_SPEC.md`.
  - Referencias: `docs/TEAM_FLOW_ANALYSIS.md`, `docs/BATCH2_SPEC.md`.

- [x] **Fix 5 — Sync meetings con valor real y menos ruido**
  - Problema: las reuniones pueden parecer coordinacion real aunque solo agreguen memoria pobre o vacia.
  - Como se hizo:
    - clasificacion explicita `informational/actionable` en `aiteam/communication.py` y `aiteam/orchestrator.py`
    - skip de meetings informativos sin senal util con evento `sync_meeting_skipped`
    - enrichment del evento `sync_meeting` con `meeting_kind`, `useful_participants` y `decision_count`
    - tests nuevos en `tests/test_memory_comms.py` para reuniones omitidas y reuniones accionables persistidas
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_memory_comms.py tests/test_orchestrator.py tests/test_parallel_taskboard.py tests/test_taskboard.py tests/test_dashboard.py -q` -> `40 passed`
  - Archivos clave: `aiteam/communication.py`, `aiteam/orchestrator.py`, `tests/test_memory_comms.py`.
  - Referencia: `docs/TEAM_FLOW_ANALYSIS.md`.

- [x] **Fix 6 — Mailbox conversacional real Team Lead <-> agentes**
  - Problema: hoy el mailbox sirve mas como bitacora/event bus que como conversacion integrada al hilo del agente.
  - Como se hizo:
    - `ConversationThread` y `ThreadStore` en `aiteam/agent_session.py` para persistencia por agente/proyecto
    - consumo de mensajes accionables del Team Lead/rol dentro de `_run_task()` en `aiteam/orchestrator.py`
    - insercion de mensajes de mailbox como turnos `user` con `source="mailbox"`
    - reply automatico al Team Lead via mailbox y eventos `conversation_mailbox_consumed` / `conversation_mailbox_reply`
    - test de continuidad en `tests/test_orchestrator.py`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_memory_comms.py tests/test_parallel_taskboard.py tests/test_taskboard.py tests/test_dashboard.py -q` -> `41 passed`
  - Archivos clave: `aiteam/agent_session.py`, `aiteam/orchestrator.py`, `tests/test_orchestrator.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

- [x] **Fix 7 — Contexto por proyecto de verdad, no solo memoria global por agente**
  - Problema: la memoria actual por agente no equivale a una sesion conversacional por proyecto.
  - Como se hizo:
    - `project_key` en `aiteam/memory.py` y filtros por proyecto en `recent`, `relevant` y `relevant_across_agents`
    - wrapper `_remember_memory()` en `aiteam/orchestrator.py` para guardar memorias nuevas con el proyecto activo
    - filtrado del proyecto actual en collaboration context, handoff y cross-agent memory
    - compaction minima de `ConversationThread` en `aiteam/agent_session.py` con resumen de turnos antiguos
    - tests de aislamiento y compaction en `tests/test_memory_comms.py`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_memory_comms.py tests/test_orchestrator.py tests/test_parallel_taskboard.py tests/test_taskboard.py tests/test_dashboard.py -q` -> `44 passed`
  - Archivos clave: `aiteam/agent_session.py`, `aiteam/memory.py`, `aiteam/orchestrator.py`, `tests/test_memory_comms.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

- [x] **Fix 8 — Observabilidad del flujo de equipo**
  - Problema: hoy cuesta distinguir entre ejecucion real, retries, gate iteration, conflictos y reuniones.
  - Como se hizo:
    - `flow_timeline` y `flow_summary` en `aiteam/dashboard.py` con rounds, sub-iteraciones, gates, bloqueos y eventos clave
    - extension de `OperatorTimelineItem` en `api/main.py` con `assignee`, `execution_round`, `execution_sub_iteration`, `gate_iteration`, `blocked_reason`, handoff y señales conversacionales
    - resumentes de eventos enriquecidos en `api/utils.py` para hacer legible el flujo desde backend
    - mejoras visuales en `ide-frontend/src/components/OperatorTimeline.tsx` para mostrar flow metadata directamente en la UI
    - tests nuevos/actualizados en `tests/test_dashboard.py` y `tests/test_api_team_chat.py`
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/test_dashboard.py tests/test_api_team_chat.py tests/test_memory_comms.py tests/test_orchestrator.py tests/test_parallel_taskboard.py tests/test_taskboard.py -q` -> `60 passed`
  - Archivos clave: `aiteam/dashboard.py`, `api/main.py`, `api/utils.py`, `ide-frontend/src/components/OperatorTimeline.tsx`, `tests/test_dashboard.py`, `tests/test_api_team_chat.py`.

- [x] **Fix 9 — Documentacion alineada con estado real**
  - Problema: varias docs seguian hablando de 108/122/142 tests y mezclaban roadmap historico con roadmap vivo.
  - Como se hizo:
    - verificacion real de baseline con `venv/Scripts/python.exe -m pytest tests/ -q --tb=short` -> `282 passed`
    - actualizacion de `README.md` con baseline real, estado de implementacion y referencias vigentes
    - reescritura de `docs/INDEX.md` y `docs/EXECUTION_QUICK_START.md` para distinguir documentos activos vs historicos
    - actualizacion de `docs/SPRINT_ROADMAP_Q1_2026.md` y `docs/TEST_MATRIX_SPRINTS_1_2_3.md` para tratarlos como referencia historica del plan Q1
    - separacion explicita entre implementado, parcial y planificado
  - Verificacion: `venv/Scripts/python.exe -m pytest tests/ -q --tb=short` -> `282 passed`
  - Archivos clave: `README.md`, `docs/SPRINT_ROADMAP_Q1_2026.md`, `docs/INDEX.md`, `docs/EXECUTION_QUICK_START.md`, `docs/TEST_MATRIX_SPRINTS_1_2_3.md`.

## Backlog Secundario

- [ ] Peer debate logic — dialogo real entre peers con resolucion de desacuerdos mas rica.
- [ ] Auto task decomposition — fragmentacion automatica de tareas complejas.
- [ ] Evidence summarization — resumen curado de diffs para reviewers.
- [ ] Domain expert knowledge transfer — compartir expertise entre agentes.
- [ ] Politica de resumen/compaction de threads antiguos para no agotar contexto.
- [ ] Pruebas E2E especificas para conversaciones persistentes multi-LLM por proyecto.

## Criterios de Exito del siguiente tramo

- [ ] Un mismo `engineer-*` recuerda y referencia su razonamiento previo dentro del mismo proyecto.
- [ ] El Team Lead puede enviar feedback a un agente y ese feedback entra en su hilo, no solo en metadata suelta.
- [ ] Un fallo de build no deja al workflow en silencio: el sistema muestra `FAILED` o `BLOCKED` con causa clara.
- [ ] El timeline del proyecto muestra orden real entre rondas, sub-rondas, gates y retries.
- [ ] El modo mock no corta flujos validos por falta de git diff.
- [ ] Documentacion y metricas publicadas reflejan el estado real de la suite y de la arquitectura.
