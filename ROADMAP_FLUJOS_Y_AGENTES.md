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

### Objetivo

Hacer que el procesamiento paralelo sea comprensible y evite carreras sutiles al desbloquear tareas hijas.

### Tareas

- [ ] B2.1 Revisar claim de todas las READY del lote.
- [ ] B2.2 Introducir barrera explicita antes de reclamar hijos desbloqueados.
- [ ] B2.3 Añadir tests de paralelismo y race conditions.
- [ ] B2.4 Revisar `AITEAM_MAX_PARALLEL_TASKS` y defaults operativos recomendados.

### Archivos clave

- `aiteam/orchestrator.py`
- `aiteam/taskboard.py`
- `tests/test_orchestrator.py`
- `tests/test_chaos.py`

### Definition of done

- Un hijo no se reclama antes de que sus dependencias queden persistidas como completadas.
- Los runs paralelos no muestran solapes imposibles en timeline.
- Hay pruebas que reproducen el caso y validan la correccion.

---

## Batch 3 — Conversation Threads base

### Objetivo

Introducir el concepto estructural clave: hilo conversacional persistente por agente y proyecto.

### Tareas

- [ ] B3.1 Crear `ConversationThread`.
- [ ] B3.2 Crear `ThreadStore` en `runtime/sessions/threads/`.
- [ ] B3.3 Persistir y recuperar threads por `agent_id + project_root`.
- [ ] B3.4 Añadir tests de persistencia y continuidad.

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

### Objetivo

Preparar la capa de invocacion LLM para soportar historiales conversacionales reales sin romper el modo actual por `prompt`.

### Tareas

- [ ] B4.1 Extender contrato base `invoke(prompt, messages=None)`.
- [ ] B4.2 Implementar soporte en `ApiAdapter`.
- [ ] B4.3 Implementar soporte en `SubscriptionAdapter`.
- [ ] B4.4 Mantener backward compatibility total.
- [ ] B4.5 Añadir tests de compatibilidad y formato.

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

### Objetivo

Hacer que cada nueva invocacion del mismo agente recupere su thread previo y responda en continuidad real.

### Tareas

- [ ] B5.1 En `_run_task()`, recuperar thread del agente/proyecto.
- [ ] B5.2 Añadir el task actual como turno `user`.
- [ ] B5.3 Guardar respuesta del agente como turno `assistant`.
- [ ] B5.4 Mantener integracion con `workflow_state`, retries y gates.
- [ ] B5.5 Añadir tests de continuidad entre build -> review feedback -> build retry.

### Archivos clave

- `aiteam/orchestrator.py`
- `aiteam/agent_session.py`
- `tests/test_orchestrator.py`

### Definition of done

- Un mismo `engineer-*` referencia su razonamiento previo en el mismo proyecto.
- Un retry por gate iteration reutiliza el hilo anterior, no solo metadata aislada.

---

## Batch 6 — Mailbox conversacional Team Lead <-> agentes

### Objetivo

Convertir el mailbox de bitacora/event bus a canal util de conversacion integrada al hilo del agente.

### Tareas

- [ ] B6.1 Definir mensajes consumibles por thread.
- [ ] B6.2 Insertar mensajes relevantes del Team Lead en el `ConversationThread` del agente.
- [ ] B6.3 Registrar respuestas del agente en mailbox y thread.
- [ ] B6.4 Distinguir mensajes informativos vs accionables vs ya consumidos.

### Archivos clave

- `aiteam/mailbox.py`
- `aiteam/orchestrator.py`
- `aiteam/agent_session.py`
- `tests/test_memory_comms.py`

### Definition of done

- El Team Lead puede mandar feedback contextual a un agente.
- El agente lo recibe en su hilo y responde en continuidad.
- El mailbox refleja el intercambio de forma auditable.

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
