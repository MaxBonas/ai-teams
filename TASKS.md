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

- [ ] **Batch 12 — Conversational Agents reales por proyecto**
  - Objetivo: pasar de orquestacion por prompts aislados a agentes con hilo conversacional persistente por proyecto.
  - Alcance:
    - `ConversationThread` persistente por `agent_id + project_root`.
    - Persistencia en `runtime/sessions/threads/`.
    - Adapters con soporte `messages[]` ademas de `prompt`.
    - Orchestrator reutiliza historial previo al invocar de nuevo al mismo agente.
    - Team Lead puede insertar mensajes en el hilo del agente y el agente responde en contexto.
  - Archivos clave: `aiteam/agent_session.py`, `aiteam/adapters/api.py`, `aiteam/adapters/subscription.py`, `aiteam/orchestrator.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

## Backlog Prioritario

- [ ] **Fix 4 — Barrera de dependencia mas segura en paralelo**
  - Problema: `_claim_ready_tasks()` reclama todas las READY del lote y puede dar sensacion de solape excesivo o carreras sutiles.
  - Hacer:
    - Revisar orden de claim/persistencia cuando `AITEAM_MAX_PARALLEL_TASKS > 1`.
    - Introducir una barrera explicita antes de reclamar hijos desbloqueados.
    - Añadir pruebas de paralelismo y race conditions.
  - Archivos clave: `aiteam/orchestrator.py`, `aiteam/taskboard.py`, `tests/test_orchestrator.py`, `tests/test_chaos.py`.
  - Referencia: `docs/TEAM_FLOW_ANALYSIS.md`.

- [ ] **Fix 5 — Sync meetings con valor real y menos ruido**
  - Problema: las reuniones pueden parecer coordinacion real aunque solo agreguen memoria pobre o vacia.
  - Hacer:
    - Reducir meetings vacios o triviales.
    - Añadir umbral minimo de contenido util.
    - Diferenciar meeting informativo vs meeting accionable.
  - Archivos clave: `aiteam/communication.py`, `aiteam/orchestrator.py`.
  - Referencia: `docs/TEAM_FLOW_ANALYSIS.md`.

- [ ] **Fix 6 — Mailbox conversacional real Team Lead <-> agentes**
  - Problema: hoy el mailbox sirve mas como bitacora/event bus que como conversacion integrada al hilo del agente.
  - Hacer:
    - Convertir mensajes relevantes del Team Lead en turnos del `ConversationThread` del agente.
    - Permitir respuesta del agente en el mismo hilo y reflejo en mailbox.
    - Marcar mensajes consumidos vs solo informativos.
  - Archivos clave: `aiteam/mailbox.py`, `aiteam/orchestrator.py`, `aiteam/agent_session.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

- [ ] **Fix 7 — Contexto por proyecto de verdad, no solo memoria global por agente**
  - Problema: la memoria actual por agente no equivale a una sesion conversacional por proyecto.
  - Hacer:
    - Separar claramente memoria duradera de agente vs hilo de proyecto.
    - Evitar mezcla de razonamientos entre proyectos distintos.
    - Definir estrategia de truncado/resumen para context windows.
  - Archivos clave: `aiteam/agent_session.py`, `aiteam/memory.py`, `aiteam/orchestrator.py`.
  - Referencia: `docs/CONVERSATIONAL_AGENTS_PLAN.md`.

- [ ] **Fix 8 — Observabilidad del flujo de equipo**
  - Problema: hoy cuesta distinguir entre ejecucion real, retries, gate iteration, conflictos y reuniones.
  - Hacer:
    - Dashboard de timeline por proyecto/agente/fase.
    - Visualizar `execution_round`, `sub_iteration`, `gate_iteration`, bloqueos y handoffs.
    - Exponer mejor estos datos en API/frontend.
  - Archivos clave: `aiteam/orchestrator.py`, `api/main.py`, `ide-frontend/src/`, `runtime/dashboard.html`.

- [ ] **Fix 9 — Documentacion alineada con estado real**
  - Problema: varias docs siguen hablando de 108/122/142 tests cuando la suite actual pasa con 264.
  - Hacer:
    - Sincronizar roadmap, quick starts, matrices y metricas.
    - Distinguir claramente entre implementado, parcial y planificado.
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
