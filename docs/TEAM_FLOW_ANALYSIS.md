# Análisis de Flujos de Equipo: Timing y Respeto de Dependencias

**Estado:** Análisis completado — bugs identificados, fixes propuestos
**Fecha:** 2026-03-26
**Autor:** Claude Sonnet 4.6 (investigación con MaxBonas)

---

## Síntoma reportado

> "A menudo me da la sensación de que no se respetan los tiempos entre ellos,
> ni respetan los tiempos de las tareas."

La sensación es correcta. Hay cuatro problemas reales identificados en el código.

---

## Problema 1: Evidence Gate rompe el flujo en modo mock (CRÍTICO)

### Qué pasa

El `_verify_task_evidence()` exige para tareas de Engineer/QA una de estas dos evidencias:

1. **Git diff** — archivos modificados detectados por `git status --porcelain`
2. **Respuesta conversacional sustancial** — output del LLM con >400 caracteres

En modo mock (`AITEAM_ENABLE_LIVE_API=0`), el adapter devuelve:
```
[openai:gpt-4.1-mini:api] Processed prompt (123 chars).
```
Esto tiene ~55 caracteres. No pasa el umbral de 400. No hay git diff porque no se
ejecutó código real. Resultado: la tarea de `build` falla con:
```
EvidenceGate Blocked: Strict Evidence Gate: No file modifications detected.
```

### La cascada

```
build FAILED
  → review.dependencies = ["CHAT-xxx::build"]  ← build nunca completa
  → _refresh_readiness() mantiene review en PENDING (no READY)
  → process_once() no encuentra más tareas READY → devuelve 0
  → run_until_idle() para
  → qa, lead_close: nunca ejecutados
```

El workflow **se corta silenciosamente** a mitad.

### Por qué el auto-detect no lo rescata

El `_detect_conversational_task()` corre ANTES de que el agente produzca output.
Pero la respuesta de 55 chars llega DESPUÉS. El ciclo es:
```
1. _detect_conversational_task() → False (la descripción no tiene keywords de pregunta)
2. LLM invocado → respuesta 55 chars
3. task.metadata["_last_agent_output"] = respuesta_55_chars
4. _verify_task_evidence():
   - git diff → no hay
   - conversational? → False (no se detectó antes)
   → FALLA
```

### Fix propuesto

En `_verify_task_evidence()`, añadir un tercer fallback:
si `AITEAM_ENABLE_LIVE_API=0`, aceptar cualquier output no vacío (modo simulación):
```python
if not self._live_api_enabled() and _agent_output.strip():
    return True, "simulated_mode_accepted"
```

---

## Problema 2: Las sub-iteraciones dentro de un round crean sensación de "rush"

### Qué pasa

`process_once()` tiene un bucle interno de hasta **20 sub-iteraciones**:

```python
for _sub in range(max_sub_iterations):   # max_sub_iterations = 20
    claimed_tasks = self._claim_ready_tasks(active_round)
    self._execute_claimed_tasks(claimed_tasks, active_round)
    self._release_blocked_parent_tasks()
```

Esto significa que en un solo "round" se pueden ejecutar 20 lotes de tareas consecutivos.
El efecto visible: `lead_intake` → `plan_research+engineering+risks` → `build` aparecen
todos en el mismo `execution_round`, como si hubieran corrido simultáneamente.

### Por qué se siente que no se respetan los tiempos

Desde el exterior (logs, dashboard), todos esos tasks tienen `execution_round=1`.
No hay separación temporal visible. El usuario ve que review llegó "al mismo tiempo"
que build, porque ambos están en round=1.

### Fix propuesto

Reducir `max_sub_iterations` de 20 a 4-6, o (mejor) añadir un `execution_round`
diferente por cada sub-iteración:

```python
for _sub in range(max_sub_iterations):
    active_round = self._round + 1 + _sub   # ← cada sub-iter tiene su propio round
    ...
```

Esto haría que los logs y el dashboard muestren correctamente el orden de ejecución.

---

## Problema 3: Tareas paralelas no respetan la barrera de dependencia completamente

### Qué pasa

`_claim_ready_tasks()` recoge TODAS las tareas READY en el mismo lote:

```python
def _claim_ready_tasks(self, active_round: int) -> list[WorkTask]:
    for task in self.taskboard.ready_tasks():   # ← todos los READY de golpe
        ...
        claimed_tasks.append(claimed)
    return claimed_tasks
```

Luego `_execute_claimed_tasks()` las ejecuta con `ThreadPoolExecutor`.

El problema: si `plan_research` y `plan_engineering` terminan en paralelo y ambas
desbloquean `build`, el sistema puede reclamar `build` ANTES de que la segunda planificación
haya completado su persistencia en disco (race condition sutil entre threads).

### Evidencia en código

```python
def _refresh_readiness(self) -> None:
    unresolved = [
        dep for dep in task.dependencies
        if dep not in self._tasks or self._tasks[dep].state != TaskState.COMPLETED
    ]
```

El lock de `_refresh_readiness` es un `threading.RLock()` en el Taskboard, pero
`_execute_claimed_tasks` libera el lock entre la ejecución del task y la actualización
del estado. En modo paralelo, esto puede provocar que un task lea el estado de otro
antes de que este haya persistido.

### Fix propuesto

Añadir una barrera explícita de "dependencias completadas" en `_claim_ready_tasks`:
verificar no solo el estado en memoria sino también en la persistencia antes de reclamar.

---

## Problema 4: Sync meetings sin output real en modo mock

### Qué pasa

Después de cada round, `_run_round_sync_meeting()` invoca a `TeamCommunicator.run_sync_meeting()`.
Este manda un mensaje vía mailbox, pero en modo mock no hace ninguna LLM call real —
solo registra un "meeting" con standup lines vacías. El resultado es que los logs
muestran meetings que no tienen contenido real.

### Efecto colateral

El usuario ve en el mailbox mensajes de "meeting" que no contienen nada útil, lo que
puede confundir sobre si el equipo realmente está coordinando.

---

## Resumen de problemas y severidad

| # | Problema | Severidad | Modo afectado |
|---|----------|-----------|---------------|
| 1 | Evidence gate corta workflow en modo mock | 🔴 CRÍTICO | Mock (`LIVE_API=0`) |
| 2 | 20 sub-iteraciones crean "rush" en un round | 🟡 MEDIO | Siempre |
| 3 | Race condition en claim paralelo | 🟡 MEDIO | Paralelo (`MAX_PARALLEL>1`) |
| 4 | Sync meetings vacíos en mock | 🟢 BAJO | Mock |

---

## Plan de fixes

### Fix 1 — Evidence gate (1 línea en `orchestrator.py`)

En `_verify_task_evidence()`, antes del `return False`:
```python
# Modo simulación: aceptar cualquier output no vacío
if not ApiAdapter._live_api_enabled() and _agent_output.strip():
    return True, "simulated_mode_accepted"
```

### Fix 2 — Sub-iteraciones con round propio (5 líneas en `orchestrator.py`)

```python
for _sub in range(max_sub_iterations):
    active_sub_round = self._round + 1   # mantener comportamiento de round
    claimed_tasks = self._claim_ready_tasks(active_sub_round)
    if not claimed_tasks:
        ...
    self._execute_claimed_tasks(claimed_tasks, active_sub_round)
    total_processed += len(claimed_tasks)
    self._release_blocked_parent_tasks()
    # Registrar sub-iteración en evento para trazabilidad
    if total_processed > 0:
        self.event_logger.emit("round_sub_iteration", {
            "sub": _sub, "tasks_this_sub": len(claimed_tasks)
        })
```

### Fix 3 — Barrera de dependencias (Taskboard)

```python
def _refresh_readiness(self) -> None:
    for task in self._tasks.values():
        ...
        # Verificar que dependencias están realmente terminadas (no solo en memoria)
        unresolved = [
            dep for dep in task.dependencies
            if dep not in self._tasks
            or self._tasks[dep].state not in {TaskState.COMPLETED}
            # Excluir FAILED: si un dep falla, la tarea hijo tampoco puede avanzar
            # En lugar de quedarse en PENDING para siempre, marcarla como BLOCKED
        ]
        if any(
            self._tasks.get(dep, WorkTask(...)).state == TaskState.FAILED
            for dep in task.dependencies
            if dep in self._tasks
        ):
            task.state = TaskState.BLOCKED
            task.metadata["blocked_reason"] = "dependency_failed"
        else:
            task.state = TaskState.PENDING if unresolved else TaskState.READY
```

Este fix es el más importante para el flujo: actualmente, si un dep FALLA,
la tarea hija se queda en PENDING para siempre. Con el fix, se marca BLOCKED
y el sistema puede reportarlo claramente en lugar de silenciar el error.

---

## Relación con el plan de sesiones conversacionales

Ambos problemas están relacionados: con sesiones conversacionales reales (ver
`CONVERSATIONAL_AGENTS_PLAN.md`), el Engineer podría recibir el feedback de la
evidence gate como un turno de conversación y autocorregirse sin que el task
se marque como FAILED. El flujo sería:

```
build → respuesta mock → evidence gate falla
  → (con conversational sessions) → turno siguiente:
    "El evidence gate requiere output concreto. ¿Puedes listar los archivos que crearías?"
  → Engineer responde con lista detallada > 400 chars → gate aceptado
```

---

## Siguiente paso recomendado

Implementar Fix 1 (evidence gate en modo simulación) primero — es el más crítico
y más simple. Los demás fixes pueden ir en un PR separado tras validar en pruebas.
