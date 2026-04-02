# Plan de Agentidad — De Pipeline a Equipo Autonomo

> Documento historico de exploracion. No usar como fuente de verdad operativa.
> Referencias activas: `task.md`, `docs/ARCHITECTURE_PLAN.md`, `docs/TASKS_2026_03_28.md`.
> Fecha: 2026-03-22
> Estado: Batch 5 + Batch 6 completados (2026-03-22)
> Prerequisitos: Batch 1-4 de Agent Flow v2 completados (shared state, gate iteration, sessions, MCP, LLM adapters, communication, skills)

## Diagnostico

Los agentes funcionan como **function calls aisladas**: reciben un blob de contexto estatico, devuelven texto, y no pueden actuar sobre su entorno durante la ejecucion. No pueden pedir herramientas, crear sub-tareas, consultar memoria de otros agentes, ni escalar decisiones.

### Causa raiz

El orquestador es un **planificador sincronico omnisciente** que:
1. Pre-computa todo el contexto antes de invocar al LLM
2. Inyecta contexto estatico en prompts (sin consultas en vivo)
3. Solo parsea el output del LLM como texto final, ignorando peticiones intermedias
4. Nunca expone a los agentes: memory queries, tool invocation, delegacion, voting, ni feedback en vivo

---

## 8 Mejoras Identificadas

| # | Mejora | Impacto | Complejidad | Archivos clave |
|---|--------|---------|-------------|----------------|
| 1 | **Agent self-delegation** | CRITICO | Media | `orchestrator.py` |
| 2 | **Tool invocation por agentes** | CRITICO | Alta | `orchestrator.py`, `tool_dispatch.py`, `mcp_manager.py` |
| 3 | **Cross-agent memory queries** | CRITICO | Baja | `memory.py`, `orchestrator.py` |
| 4 | **Session history en retries** | ALTO | Baja | `agent_session.py`, `orchestrator.py` |
| 5 | **Peer dialogue (back-and-forth)** | ALTO | Media | `orchestrator.py`, `communication.py` |
| 6 | **Decision rank enforcement** | MEDIO | Media | `profiles.py`, `orchestrator.py` |
| 7 | **Skill ranking activo en prompts** | MEDIO | Baja | `autotools.py`, `orchestrator.py` |
| 8 | **Eager processing (sub-loop)** | ALTO | Baja | `orchestrator.py` |

---

## Batch 5: Primera ronda (impacto alto, complejidad baja-media)

### 5.1 Agent Self-Delegation
**Problema**: Si un engineer descubre que necesita investigacion, no puede pedirla — solo el orquestador decide que tareas crear.

**Solucion**: Parsear el output del agente buscando bloques estructurados `[REQUEST_TASK]` y crear sub-tareas dinamicamente.

- En `_run_task()`, despues de obtener el output del LLM, buscar patron `[REQUEST_TASK type=research|engineer|review topic="..." priority=high|medium|low]`
- Si se encuentra, crear sub-tarea via `taskboard.add_task()` con dependencia al task actual
- Limitar a max 2 sub-tareas por agente por ejecucion (evitar explosion)
- Registrar en workflow_state y event_logger
- El agente original completa con resultado parcial + nota de delegacion

**Archivos**: `aiteam/orchestrator.py` (metodo nuevo `_parse_agent_requests()`, modificar `_run_task()`)

### 5.2 Cross-Agent Memory Queries
**Problema**: Cada agente tiene memoria aislada en JSONL separado. Engineer B no sabe que Engineer A ya descubrio un pitfall.

**Solucion**: Agregar metodo `memory.relevant_across_agents()` que busque en todas las memorias.

- Nuevo metodo en `AgentMemoryStore`: `relevant_across_agents(query, exclude_agent=None, limit=5)`
- Itera sobre todos los archivos `{agent_id}.jsonl` del directorio de memoria
- Filtra por relevancia (keyword match en content) y recencia
- Inyectar en `_build_collaboration_context()` como seccion "Conocimiento del equipo"
- Limitar a 500 chars por entry, max 5 entries

**Archivos**: `aiteam/memory.py`, `aiteam/orchestrator.py`

### 5.3 Session History en Retries
**Problema**: Cuando un task se reintenta (gate iteration), el agente solo recibe el feedback del reviewer pero pierde todo el contexto de su intento previo.

**Solucion**: Inyectar resumen del intento anterior en el prompt de retry.

- En `_run_task()`, cuando `gate_iteration > 0`, leer sesiones anteriores via `session_store.sessions_for_task(task_id)`
- Extraer: approach tomado, tools usados, output producido, razon del fallo
- Formatear como "Intento anterior: {resumen}. Feedback del reviewer: {feedback}. Ajusta tu enfoque."
- Agregar al prompt despues del review_feedback existente

**Archivos**: `aiteam/orchestrator.py`, `aiteam/agent_session.py`

### 5.4 Eager Processing (Sub-Loop)
**Problema**: `process_once()` ejecuta tareas READY, completa, y sale. Las tareas que se vuelven READY como resultado no se procesan hasta la siguiente ronda — 1 ronda de latencia por nivel de dependencia.

**Solucion**: Loop interno en `process_once()` que re-chequea readiness despues de cada batch.

- Despues de `_execute_claimed_tasks()`, llamar `_release_blocked_parent_tasks()`
- Re-checkear `taskboard.ready_tasks()` — si hay nuevas, ejecutarlas inmediatamente
- Cap de 20 sub-iteraciones para evitar loops infinitos
- Registrar evento `eager_processing` con count de sub-iteraciones

**Archivos**: `aiteam/orchestrator.py` (modificar `process_once()`)

---

## Batch 6: Segunda ronda (impacto medio-alto, complejidad media)

### 6.1 Tool Invocation por Agentes
Exponer MCP/CLI tools como acciones que el agente puede pedir en su output (`[USE_TOOL server=semgrep tool=scan args={...}]`). El orquestador parsea, invoca, e inyecta el resultado de vuelta.

### 6.2 Peer Dialogue (Back-and-Forth)
Implementar protocolo de 2 rondas: agente propone → peer responde → agente decide. No solo broadcast one-way.

### 6.3 Decision Rank Enforcement
Validar que decisiones criticas (deploy, breaking changes) requieran aprobacion de agente con `decision_rank >= 5`.

### 6.4 Skill Ranking Activo
Ordenar skills en prompt por success rate del rol. Marcar top skills como "recomendados" y bottom como "usar con precaucion".

---

## Riesgos y Mitigaciones

| Riesgo | Mitigacion |
|--------|------------|
| Self-delegation crea explosion de sub-tareas | Max 2 sub-tareas por agente, max 10 total por workflow |
| Cross-agent memory es lento con muchos agentes | Cache + limit a 5 entries, lazy load |
| Eager processing loop infinito | Cap de 20 sub-iteraciones |
| Tool invocation introduce riesgo de seguridad | Reutilizar CommandPolicy existente + approval para MCP |
| Session history hace prompts muy largos | Limitar resumen a 300 chars |

---

## Verificacion

1. `pytest tests/ -q --tb=short` — todos los tests pasan
2. Test manual Batch 5:
   - Enviar tarea compleja → verificar que engineer genera `[REQUEST_TASK]` y se crea sub-tarea
   - Verificar que discovery findings aparecen en contexto del engineer (cross-agent memory)
   - Forzar gate failure → verificar que retry incluye resumen del intento previo
   - Pipeline de 6 fases completa en <= 3 rondas (eager processing)
3. Eventos en `runtime/events.jsonl`:
   - `agent_delegation` (self-delegation)
   - `cross_agent_memory_query` (memory)
   - `eager_processing` (sub-loop)
