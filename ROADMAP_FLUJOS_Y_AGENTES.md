# Roadmap Flujos y Agentes — AI Team Hybrid Orchestrator

> Fecha: 2026-03-26
> Fuente: consolidado de `TASKS.md`, `docs/CONVERSATIONAL_AGENTS_PLAN.md` y `docs/TEAM_FLOW_ANALYSIS.md`

## Objetivo

Llevar el sistema desde un orquestador de tareas LLM con memoria parcial a un equipo multi-LLM con:

- sesiones conversacionales persistentes por agente y por proyecto,
- comunicacion real Team Lead <-> agentes en contexto,
- timing visible y creible entre fases,
- dependencias y bloqueos expresados correctamente,
- observabilidad suficiente para entender por que un flujo avanza, se reintenta o se detiene.

## Principios de implementacion

1. Primero estabilizar el flujo actual.
2. Luego introducir conversacion persistente sin romper compatibilidad.
3. Despues conectar mailbox, contexto por proyecto y observabilidad.
4. Cerrar con limpieza documental y pruebas E2E.

## Batch 1 — Estabilizacion del flujo actual

**Estado**: COMPLETADO (2026-03-26)

### Objetivo

Eliminar los fallos que hoy distorsionan el comportamiento del equipo y hacen dificil confiar en el timing del sistema.

### Tareas

- [x] B1.1 Evidence Gate robusto en modo mock/simulado.
- [x] B1.2 Dependencias fallidas marcan `BLOCKED` en hijos.
- [x] B1.3 Sub-iteraciones visibles en `process_once()`.
- [x] B1.4 Eventos y trazas para distinguir `round`, `sub_iteration` y `gate_iteration`.

### Como se hizo

- **B1.1**: se añadio fallback en `aiteam/orchestrator.py` para aceptar output no vacio en mock (`AITEAM_ENABLE_LIVE_API=0`) y se guardo `evidence_reason` para trazabilidad.
- **B1.2**: se modifico `aiteam/taskboard.py` para que un padre en `FAILED` bloquee hijos con `blocked_reason=dependency_failed` y `blocked_dependencies`, y para que un retry limpie ese bloqueo cuando corresponda.
- **B1.3**: se extendio el scheduler en `aiteam/orchestrator.py` para persistir `execution_sub_iteration` en cada tarea y emitir eventos `round_sub_iteration` por intento de claim y por batch ejecutado.
- **B1.4**: se enriquecieron los eventos `task_started`, `task_execution`, `gate_iteration` y se anadio `round_completed` para exponer `execution_round`, `execution_sub_iteration`, `gate_iteration`, `sub_iterations_used` y `tasks_processed`.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_dashboard.py -q`
- Resultado: `28 passed`

### Archivos clave

- `aiteam/orchestrator.py`
- `aiteam/taskboard.py`
- `tests/test_orchestrator.py`
- `tests/test_taskboard.py`

### Definition of done

- Un run en mock no falla solo por ausencia de git diff si hay output no vacio.
- Si un padre falla, el hijo no queda en `PENDING` silencioso: queda en `BLOCKED` con causa.
- Los logs/eventos permiten ver el orden real de sub-rondas.
- Los eventos distinguen `round`, `sub_iteration` y `gate_iteration`.
- Los tests nuevos cubren estos casos.

### Riesgo que reduce

Reduce la sensacion de que "no se respetan los tiempos" cuando en realidad el problema es de modelado del estado y de observabilidad.

---

## Batch 2 — Barreras de dependencia y paralelismo seguro

**Estado**: EN PROGRESO (B2.1-B2.4 implementados; Fix 5, Fix 6, Fix 7, Fix 8 y Fix 9 completados)

### Objetivo

Hacer que el procesamiento paralelo sea comprensible y evite carreras sutiles al desbloquear tareas hijas.

### Tareas

- [x] B2.1 Revisar claim de todas las READY del lote.
- [x] B2.2 Introducir barrera explicita antes de reclamar hijos desbloqueados.
- [x] B2.3 Añadir tests de paralelismo y race conditions.
- [x] B2.4 Revisar `AITEAM_MAX_PARALLEL_TASKS` y defaults operativos recomendados.

### Especificacion

- `docs/BATCH2_SPEC.md`

### Como se hizo

- **B2.1**: se anadio un guard en `aiteam/taskboard.py` dentro de `claim_task()` para revalidar dependencias al momento del claim y rechazar estados READY corruptos o adelantados.
- **B2.2**: no se uso `taskboard._save()` privado desde el orchestrator; en su lugar se introdujo una barrera publica con `taskboard.checkpoint()` y evento `sub_iteration_barrier` despues de cada batch.
- **B2.3**: se creo `tests/test_parallel_taskboard.py` con pruebas de claim concurrente, dependencia compartida, guard con padre CLAIMED, dependencia fallida y evento de barrera.
- **B2.4**: se documento en `docs/BATCH2_SPEC.md` la recomendacion operativa de paralelismo por entorno: `dev=1`, `stage=2`, `prod=2-3`, manteniendo el default de codigo en `1`.
- **Fix 5**: se redujo ruido de meetings en `aiteam/communication.py` con clasificacion `informational/actionable`, skip de reuniones sin senal util, evento `sync_meeting_skipped` y metadata de utilidad (`meeting_kind`, `useful_participants`, `decision_count`).
- **Fix 6**: se introdujo `ConversationThread` y `ThreadStore` en `aiteam/agent_session.py`, se consumen mensajes accionables del mailbox dentro del hilo del agente y se responde al Team Lead via mailbox con eventos `conversation_mailbox_consumed` y `conversation_mailbox_reply`.
- **Fix 7**: se introdujo `project_key` en `aiteam/memory.py`, se filtra memoria por proyecto en contexto/handoff/cross-agent memory y se anadio compaction minima de threads en `aiteam/agent_session.py` para evitar mezcla de proyectos y crecimiento sin control.
- **Fix 8**: se mejoro la observabilidad del flujo en `aiteam/dashboard.py`, `api/main.py`, `api/utils.py` e `ide-frontend/src/components/OperatorTimeline.tsx` para mostrar rounds, sub-iteraciones, gate iterations, bloqueos, handoffs, meetings y eventos conversacionales en backend y UI.
- **Fix 9**: se alineo la documentacion operativa (`README.md`, `docs/INDEX.md`, `docs/EXECUTION_QUICK_START.md`, `docs/SPRINT_ROADMAP_Q1_2026.md`, `docs/TEST_MATRIX_SPRINTS_1_2_3.md`) con el estado real del proyecto, separando claramente documentos vigentes vs historicos y actualizando la baseline a **282 tests passing**.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/ -q --tb=short`
- Resultado: `282 passed`

### Archivos clave

- `aiteam/orchestrator.py`
- `aiteam/taskboard.py`
- `aiteam/communication.py`
- `aiteam/agent_session.py`
- `aiteam/memory.py`
- `aiteam/dashboard.py`
- `api/main.py`
- `api/utils.py`
- `ide-frontend/src/components/OperatorTimeline.tsx`
- `README.md`
- `docs/INDEX.md`
- `docs/EXECUTION_QUICK_START.md`
- `docs/SPRINT_ROADMAP_Q1_2026.md`
- `docs/TEST_MATRIX_SPRINTS_1_2_3.md`
- `tests/test_parallel_taskboard.py`
- `tests/test_memory_comms.py`
- `tests/test_orchestrator.py`
- `tests/test_dashboard.py`
- `tests/test_api_team_chat.py`
- `docs/BATCH2_SPEC.md`

### Definition of done

- Un hijo no se reclama antes de que sus dependencias queden persistidas como completadas.
- Los runs paralelos no muestran solapes imposibles en timeline.
- Hay pruebas que reproducen el caso y validan la correccion.
- El default de codigo sigue siendo seguro (`1`) y los valores recomendados por entorno quedan documentados.
- Los sync meetings informativos sin senal util ya no se emiten como ruido.
- Los mensajes accionables del Team Lead ya pueden entrar en el hilo del agente y generar respuesta trazable.
- La memoria operativa y el hilo conversacional ya no mezclan facilmente proyectos distintos.
- El timeline operativo ya expone rounds, sub-iteraciones, bloqueos, handoffs, meetings y señales conversacionales de forma legible.
- La documentacion operativa ya distingue entre estado vigente, estado parcial y documentos historicos.

---

## Batch 3 — Conversation Threads base

**Estado**: COMPLETADO

### Objetivo

Introducir el concepto estructural clave: hilo conversacional persistente por agente y proyecto.

### Tareas

- [x] B3.1 Crear `ConversationThread`.
- [x] B3.2 Crear `ThreadStore` en `runtime/sessions/threads/`.
- [x] B3.3 Persistir y recuperar threads por `agent_id + project_root`.
- [x] B3.4 Añadir tests de persistencia y continuidad.

### Como se hizo

- Se introdujeron `ConversationTurn`, `ConversationThread` y `ThreadStore` en `aiteam/agent_session.py`.
- Los threads se persisten por `agent_id + project_root` en `runtime/sessions/threads/`.
- Se anadio compaction minima de turnos antiguos para mantener eficiencia del contexto.
- Se cubrio persistencia, continuidad y compaction en `tests/test_memory_comms.py` y `tests/test_orchestrator.py`.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/test_memory_comms.py tests/test_orchestrator.py -q`
- Resultado: soporte base de thread validado dentro de la suite conversacional actual.

### Archivos clave

- `aiteam/agent_session.py`
- `tests/test_orchestrator.py`
- `tests/test_memory_comms.py`

### Definition of done

- El sistema puede crear, cargar y actualizar un thread por agente/proyecto.
- Reiniciar el proceso no pierde el historial.
- Aun no hace falta usarlo en adapters; solo debe existir y persistir bien.

---

## Batch 4 — Adapters con `messages[]`

**Estado**: COMPLETADO

### Objetivo

Preparar la capa de invocacion LLM para soportar historiales conversacionales reales sin romper el modo actual por `prompt`.

### Tareas

- [x] B4.1 Extender contrato base `invoke(prompt, messages=None)`.
- [x] B4.2 Implementar soporte en `ApiAdapter`.
- [x] B4.3 Implementar soporte en `SubscriptionAdapter`.
- [x] B4.4 Mantener backward compatibility total.
- [x] B4.5 Añadir tests de compatibilidad y formato.

### Como se hizo

- Se extendio el contrato base en `aiteam/adapters/base.py` con `normalize_messages()` y `messages_to_prompt()`.
- `ApiAdapter`, `SubscriptionAdapter` y `ExternalProgramAdapter` ya aceptan `messages[]`.
- `HybridRouter.route_and_invoke()` ya pasa `messages[]` y detecta adapters viejos para no romper compatibilidad.
- Se cubrio compatibilidad e invocacion live/mock con historial en `tests/test_router.py` y `tests/test_api_adapter_live.py`.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/test_router.py tests/test_api_adapter_live.py tests/test_orchestrator.py tests/test_memory_comms.py -q`
- Resultado: `52 passed`

### Archivos clave

- `aiteam/adapters/base.py`
- `aiteam/adapters/api.py`
- `aiteam/adapters/subscription.py`
- `tests/test_api_adapter_live.py`

### Definition of done

- Si `messages` existe, el adapter usa historial.
- Si `messages` no existe, el comportamiento actual sigue intacto.
- Ningun flujo existente se rompe.

---

## Batch 5 — Orchestrator conversacional

**Estado**: COMPLETADO

### Objetivo

Hacer que cada nueva invocacion del mismo agente recupere su thread previo y responda en continuidad real.

### Tareas

- [x] B5.1 En `_run_task()`, recuperar thread del agente/proyecto.
- [x] B5.2 Añadir el task actual como turno `user`.
- [x] B5.3 Guardar respuesta del agente como turno `assistant`.
- [x] B5.4 Mantener integracion con `workflow_state`, retries y gates.
- [x] B5.5 Añadir tests de continuidad entre build -> review feedback -> build retry.

### Como se hizo

- `_run_task()` ya construye `messages[]` reales con `system + thread reciente + user actual`.
- El mailbox accionable entra en el hilo antes de invocar al adapter.
- La respuesta del agente se persiste como turno `assistant` y puede contestar al Team Lead.
- Peer consultation y peer round 2 ya usan tambien `messages[]` compactos y eficientes.
- Se emitieron eventos `conversation_messages_built` y `peer_messages_built` para trazabilidad.
- Los retries por gate iteration ya usan mensajes compactos de retry y se persisten como `task_retry`, evitando repetir contexto innecesario.
- `ConversationThread` ya deduplica turnos consecutivos identicos para no contaminar el historial con ruido repetido.
- Se anadio un test E2E conversacional completo con feedback Team Lead -> quality gates -> respuesta final coherente.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_memory_comms.py tests/test_router.py tests/test_api_adapter_live.py tests/test_cli_providers.py -q`
- Resultado: `74 passed`

### Archivos clave

- `aiteam/orchestrator.py`
- `aiteam/agent_session.py`
- `tests/test_orchestrator.py`

### Definition of done

- Un mismo `engineer-*` referencia su razonamiento previo en el mismo proyecto.
- Un retry por gate iteration reutiliza el hilo anterior, no solo metadata aislada.
- El contexto conversacional se mantiene compacto: al grano, con detalle util y sin arrastrar ruido innecesario.
- Los retries no vuelven a inyectar todo el contexto si solo cambia el feedback de gates.
- Existe verificacion E2E del flujo conversacional completo con mailbox, gates, retry y respuesta final.

### Siguiente tarea importante

- Endurecimiento operativo: alertas por cambios de estado, dependencia de fallbacks y degradacion de senior cloud usando la vista unificada `provider_ops`.

## Politica Team Lead y Relevo

- `team_lead` solo puede usar modelos `senior_cloud` o `advanced_api`.
- `team_lead` nunca puede usar modelos locales, aunque esten sanos y disponibles.
- Si los modelos Pro senior no estan sanos, el relevo permitido es una API avanzada y eficiente, no un modelo local.
- El ranking de relevo debe combinar capacidad de coding, razonamiento, confianza y salud real (`provider_smoke.json`).
- El catalogo de modelos debe ser editable sin tocar codigo via `runtime/model_catalog.json`.
- El router debe consumir `runtime/provider_ops.json` como autoridad operativa principal para decisiones de elegibilidad del relevo.
- Referencias: `aiteam/model_catalog.py`, `config/model_catalog.example.json`, `docs/MODEL_POLICY.md`, `aiteam/router.py`.

---

## Batch 6 — Mailbox conversacional Team Lead <-> agentes

**Estado**: COMPLETADO

### Objetivo

Convertir el mailbox de bitacora/event bus a canal util de conversacion integrada al hilo del agente.

### Tareas

- [x] B6.1 Definir mensajes consumibles por thread.
- [x] B6.2 Insertar mensajes relevantes del Team Lead en el `ConversationThread` del agente.
- [x] B6.3 Registrar respuestas del agente en mailbox y thread.
- [x] B6.4 Distinguir mensajes informativos vs accionables vs ya consumidos.

### Como se hizo

- El Team Lead ya delega con `delegation_brief` estructurado por rol/fase en `api/main.py`.
- `_run_task()` integra mensajes accionables del mailbox en el hilo antes de invocar al agente.
- El agente puede responder al Team Lead y dejar trazabilidad en mailbox, thread y eventos.
- Los handoffs entre agentes ya incluyen contexto estructurado, fallo previo, feedback pendiente y siguiente accion esperada.
- `Mailbox` ya distingue `kind=actionable|informational` y registra `consumed` / `consumed_by`.

### Verificacion ejecutada

- `venv/Scripts/python.exe -m pytest tests/test_memory_comms.py tests/test_orchestrator.py tests/test_api_team_chat.py tests/test_provider_ops.py tests/test_dashboard.py tests/test_router.py tests/test_cli_providers.py -q`
- Resultado: `91 passed`

### Archivos clave

- `aiteam/mailbox.py`
- `aiteam/orchestrator.py`
- `aiteam/agent_session.py`
- `tests/test_memory_comms.py`

### Definition of done

- El Team Lead puede mandar feedback contextual a un agente.
- El agente lo recibe en su hilo y responde en continuidad.
- El mailbox refleja el intercambio de forma auditable.
- El Team Lead delega con briefs estructurados y handoffs claros por rol.
- Los mensajes del mailbox se distinguen entre informativos, accionables y consumidos.

---

## Batch 7 — Contexto por proyecto y politicas de memoria

### Objetivo

Separar con claridad memoria duradera del agente y conversacion viva del proyecto para evitar contaminacion entre proyectos.

### Tareas

- [ ] B7.1 Delimitar memoria global del agente vs thread del proyecto.
- [ ] B7.2 Definir estrategia de truncado/resumen de threads largos.
- [ ] B7.3 Añadir limites por tokens/turnos.
- [ ] B7.4 Añadir politicas de compaction auditables.

### Archivos clave

- `aiteam/agent_session.py`
- `aiteam/memory.py`
- `aiteam/orchestrator.py`

### Definition of done

- Dos proyectos distintos no comparten razonamiento conversacional por accidente.
- Los threads no crecen sin control.

---

## Batch 8 — Observabilidad del flujo de equipo

### Objetivo

Hacer visible para usuario y operador que paso, en que orden, por que se bloqueo y quien hablo con quien.

### Tareas

- [ ] B8.1 Exponer `execution_round`, `sub_iteration`, `gate_iteration`, `blocked_reason`, `conversation_thread_id`.
- [ ] B8.2 Timeline por proyecto/agente/fase.
- [ ] B8.3 Visualizacion de handoffs, retries, conflictos y meetings.
- [ ] B8.4 API/frontend para inspeccionar threads y estado de consumo del mailbox.

### Archivos clave

- `aiteam/orchestrator.py`
- `api/main.py`
- `ide-frontend/src/`
- `runtime/dashboard.html`

### Definition of done

- Un operador puede reconstruir el flujo de un proyecto sin leer el codigo.
- El usuario entiende el orden real de ejecucion.

---

## Batch 9 — Meetings y coordinacion con valor real

### Objetivo

Reducir ruido y hacer que las reuniones automaticas aporten informacion accionable.

### Tareas

- [ ] B9.1 Añadir umbral minimo de contenido util.
- [ ] B9.2 Evitar meetings vacios o triviales.
- [ ] B9.3 Diferenciar sync informativo, sync de conflicto y sync de bloqueo.
- [ ] B9.4 Medir utilidad de meetings en logs/eventos.

### Archivos clave

- `aiteam/communication.py`
- `aiteam/orchestrator.py`

### Definition of done

- Las reuniones no inflan artificialmente la sensacion de coordinacion.
- Cada meeting tiene motivo, contenido util y trazabilidad.

---

## Batch 10 — Limpieza documental y cierre E2E

### Objetivo

Sincronizar la documentacion con el estado real y cerrar con pruebas end-to-end del nuevo modelo.

### Tareas

- [ ] B10.1 Actualizar metricas y conteo real de tests.
- [ ] B10.2 Marcar implementado/parcial/planificado en docs clave.
- [ ] B10.3 Añadir pruebas E2E para conversaciones persistentes multi-LLM por proyecto.
- [ ] B10.4 Documentar runbooks de debugging de flujo.

### Archivos clave

- `README.md`
- `docs/SPRINT_ROADMAP_Q1_2026.md`
- `docs/INDEX.md`
- `docs/EXECUTION_QUICK_START.md`
- `docs/TEST_MATRIX_SPRINTS_1_2_3.md`
- `tests/test_integration_cli.py`

### Definition of done

- La documentacion deja de mezclar pasado, presente y roadmap.
- Existe un recorrido de prueba reproducible del modelo conversacional.

---

## Orden recomendado

```text
Batch 1  -> estabilizar flujo actual
Batch 2  -> asegurar dependencias/paralelismo
Batch 3  -> crear threads persistentes
Batch 4  -> habilitar adapters conversacionales
Batch 5  -> conectar orchestrator al thread
Batch 6  -> integrar mailbox real al hilo
Batch 7  -> separar memoria vs proyecto
Batch 8  -> exponer observabilidad completa
Batch 9  -> limpiar meetings/coordination noise
Batch 10 -> documentacion + E2E + cierre
```

## Recomendacion de arranque

Empezar por **Batch 1**. Sin esa estabilizacion, cualquier implementacion de agentes conversacionales se apoyaria en un scheduler y una semantica de estados que hoy ya generan confusion por si mismos.

---

## Deuda tecnica — auditoria 2026-03-26

Puntos flacos identificados para funcionar como equipo LLM real (Claude Teams).
Ver detalle completo en `TASKS.md` seccion "Puntos flacos identificados".

### Urgente (bloquea produccion real)

**1. claude_pro_cli caido por creditos agotados** — PENDIENTE operativo
- Impacto: Anthropic queda fuera del pool de team_lead. El router lo excluye automaticamente via `provider_smoke`.
- Estado en `runtime/provider_smoke.json`: `smoke_failed: credit balance is too low`.
- Accion: recargar creditos Anthropic o promover OpenAI/Gemini como Team Lead primario mientras dure.

**2. Google Gemini aplana messages[] — historial conversacional roto** — ✅ RESUELTO 2026-03-26
- `_invoke_google()` reescrita en `aiteam/adapters/subscription.py`.
- Ahora usa `contents[{role, parts}]` nativo, `system_instruction` separado, fusiona turnos consecutivos.
- 5 tests en `GeminiConversationalTests` en `tests/test_api_adapter_live.py`.

### Alta (limita calidad del equipo)

**3. Tool calling via texto `[USE_TOOL]` — fragil** — ✅ RESUELTO 2026-03-26
- Native function calling implementado para OpenAI y Anthropic.
- `NativeToolDefinition(name, description, parameters)` en `aiteam/adapters/base.py`.
- `ToolCall(id, name, arguments)` dataclass + `AdapterResponse.tool_calls` en `aiteam/types.py`.
- `ApiAdapter` y `SubscriptionAdapter`: OpenAI usa `tools`/`tool_choice`/`tool_calls`; Anthropic usa `input_schema`/`tool_use` blocks.
- Router pasa `tools` via `inspect.signature` (backward compatible).
- Orchestrator: `_build_native_tools_for_task()`, `_execute_native_tool_calls()`, dos rondas con guard `_native_tool_round_done`.
- Gemini mantiene `[USE_TOOL]` como fallback; adapters legacy no afectados.
- 4 tests en `NativeFunctionCallingTests` en `tests/test_api_adapter_live.py`. Total: 321 tests.

**4. Sin streaming al frontend** — PENDIENTE (spec para siguiente agente)
- Llamadas LLM de 20-60s sin feedback visible para el usuario.
- **Spec del fix**:
  - Añadir `invoke_stream(prompt, messages) -> Iterator[str]` en `ModelAdapter` (default: yield el invoke completo en un chunk).
  - Implementar streaming real en `ApiAdapter` para OpenAI (`stream: true`, parsear `data: {delta}` SSE).
  - Implementar streaming real en `SubscriptionAdapter` para Anthropic (`stream: true`, parsear `content_block_delta`).
  - En `api/main.py`: nuevo endpoint `GET /tasks/{task_id}/stream` que hace SSE al cliente React.
  - El orchestrator emite eventos `token_chunk` por task_id al event_logger mientras el adapter hace stream.
  - El frontend se suscribe al SSE y muestra los chunks en tiempo real en la timeline del task.

### Media (limita robustez en uso prolongado)

**5. Context overflow sin gestion de tokens** — ✅ RESUELTO 2026-03-26
- `_compact_turns()` ahora acepta `max_chars=60_000` y calcula total de chars del thread.
- Ajusta `keep_recent` dinamicamente para dejar los turnos retenidos bajo el 70% del limite.
- 3 tests en `ThreadCompactionTests` en `tests/test_api_adapter_live.py`.

**6. Paralelismo=1 por defecto — equipo secuencial** — PENDIENTE operativo
- `AITEAM_MAX_PARALLEL_TASKS=1` ejecuta todo en serie.
- Para tareas independientes el equipo podria correr en paralelo real (2-3 workers).
- Accion: subir a 2 en `.env` y verificar que no hay race conditions nuevas con la suite completa.

**7. Evidence gate no valida calidad** — ✅ RESUELTO 2026-03-26
- Nuevo `_assess_output_quality(output, role, phase)` en `aiteam/orchestrator.py`.
- En live mode: detecta respuestas triviales; exige observaciones para REVIEWER; resultados de test para QA; output sustancial para ENGINEER.
- 7 tests en `EvidenceGateQualityTests` en `tests/test_api_adapter_live.py`.

### Baja (confusion operativa)

**8. SubscriptionAdapter y ApiAdapter son identicos en la practica** — PENDIENTE documental
- Ambos usan REST + API key via `urllib`. La distincion "Pro-first" es solo de prioridad en el router.
- Los smoke tests usan Codex CLI / Gemini CLI / Claude Code CLI solo para health check, no para inferencia.
- Accion: documentar en `docs/MODEL_POLICY.md`.

**9. model_catalog.json usa nombres de clase, no IDs reales de API** — PENDIENTE documental
- `"model": "gpt-5.4 / gpt-4o class"` es metadata descriptiva, no el ID que se envia a la API.
- El `adapter.model` real es lo que importa. Editar el catalogo no cambia el modelo invocado.
- Fix sugerido: separar `model_display_name` de `api_model_id` en el esquema del catalogo.

### Baseline tras esta ronda de fixes

- **Antes**: 302 tests passing
- **Despues**: 317 tests passing (+15 tests nuevos)
- Fixes implementados: Gemini messages[], context token-aware, evidence gate quality
