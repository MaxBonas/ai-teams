# Plan de Mejora del Flujo de Agentes en Equipo

**Fecha**: 2026-03-21
**Actualizado**: 2026-03-26
**Estado**: COMPLETADO — todos los batches implementados y verificados (271 tests passing)
**Impacto**: Critico — afecta la cohesion, eficiencia y calidad del equipo de agentes

---

## 1. Analisis Comparativo: Estado Actual vs Estado del Arte

### 1.1 Frameworks investigados

| Framework | Patron de comunicacion | Sincronizacion | Feedback loop | Fortaleza clave |
|-----------|----------------------|----------------|---------------|-----------------|
| **CrewAI** | Task-mediated (`context=[prev_task]`) | Sequential/Hierarchical | Manager re-delega | Output de tarea N es input de N+1 |
| **LangGraph** | Shared State TypedDict | Graph topology (edges) | Conditional edges loop-back | Estado compartido con reducers |
| **AutoGen** | Group Chat broadcast | Speaker selection | Natural en chat | Todos los agentes ven todo |
| **OpenAI Swarm** | Handoff functions | Sequential atomico | Context variables compartidas | Simplicidad extrema |
| **Magentic-One** | Orchestrator-mediated + Ledger | Ledger post-turno | Orchestrator re-planifica | Deteccion de estancamiento |
| **Cursor** | Single agent loop + tools | Secuencial con linter feedback | Auto-retry on linter/test fail | Checkpoints + revert |
| **Windsurf** | Cascade flow + coherence engine | Sequential multi-file | Intent drift detection | Coherencia cross-file |
| **Claude Code** | Single while-loop + tools | Tool results drive flow | Error output → retry | Contexto completo persistente |
| **Devin** | Planner-Executor hierarchical | Checklist progress tracking | Multi-level retry + web search | Autonomia total con VM |
| **Copilot Workspace** | Structured pipeline | Plan → Implement → Validate | User-initiated | Plan editable y transparente |

### 1.2 Diagnostico del sistema actual (aiteam-hybrid)

| # | Problema | Severidad | Donde ocurre | Patron que lo resuelve |
|---|----------|-----------|--------------|----------------------|
| P1 | **Sin propagacion de resultados**: cada fase empieza sin saber que produjo la anterior | CRITICO | `_build_collaboration_context()` no lee `metadata["result"]` de dependencias | CrewAI `context=[]` |
| P2 | **Memoria privada por agente**: Agent A no ve lo que Agent B descubrio | CRITICO | `memory.py` — cada agente tiene su propio JSONL | LangGraph shared state |
| P3 | **Review falla = tarea muerta**: no hay iteracion engineer↔reviewer | CRITICO | `_release_blocked_parent_tasks()` → `mark_failed` sin retry | Evaluator-Optimizer loop |
| P4 | **Latencia de 1 ronda por dependencia**: 6 fases = 6+ rondas minimo | ALTO | `process_once()` no re-chequea READY tras completar | LangGraph eager execution |
| P5 | **Sin tracking de progreso**: no se sabe si el equipo avanza o esta estancado | ALTO | No existe ledger | Magentic-One ledger |
| P6 | **Prompts sin contexto de equipo**: el engineer no sabe que descubrio el researcher | MEDIO | `build_prompt()` solo recibe titulo+descripcion | Todos los IDEs modernos |
| P7 | **Mailbox decorativo**: mensajes se escriben pero nadie los consume realmente | MEDIO | `mailbox.py` es append-only, no hay consumo real | Reemplazar con workflow_state |
| P8 | **Meetings costosos sin impacto**: sync meetings leen toda la memoria pero nadie usa las actas | BAJO | `_run_round_sync_meeting()` genera actas al mailbox | Ledger reemplaza meetings |

---

## 2. Plan de Implementacion Detallado

### Fase A: Infraestructura de Estado Compartido

#### A.1 — WorkflowState (Blackboard Pattern)
**Archivo**: `aiteam/orchestrator.py`
**Resuelve**: P1, P2, P7

**Que hacer**:
- Anadir `self.workflow_state: dict[str, dict]` en `__init__`
- Clave: `task_root` (ej. "sprint5_abc123"), Valor: dict con phase_outputs, facts, ledger, review_feedback
- Metodo `_get_workflow_state(task_root)` — devuelve o crea estado para un workflow
- Metodo `_update_workflow_state(task_root, phase, output, facts)` — actualiza tras completar una fase
- Metodo `_task_root(task_id)` — extrae el root de un task_id (ej. "ROOT::build" → "ROOT")
- Persistir workflow_state a `runtime_dir / "workflow_state.json"` para continuidad

**Criterio de done**:
- `_get_workflow_state("x")` devuelve dict vacio si no existe
- `_update_workflow_state("x", "discovery", "hallazgos...", ["hecho1"])` persiste correctamente
- Todos los tests existentes siguen pasando

#### A.2 — Propagacion de resultados entre fases
**Archivo**: `aiteam/orchestrator.py` → `_build_collaboration_context()` (linea 1247)
**Resuelve**: P1, P6

**Que hacer**:
- Nuevo metodo `_build_dependency_output_context(task)`:
  - Recorre `task.dependencies`
  - Para cada dep completada, lee su `metadata["result"]` del taskboard
  - Compacta cada resultado a 400 chars max
  - Retorna string formateado: "## Discovery\n{resultado}\n## Build\n{resultado}"
- Integrar en `_build_collaboration_context` despues de la memoria reciente
- Tambien inyectar facts del workflow_state

**Criterio de done**:
- Un task de build con dependency en discovery recibe el output de discovery en su contexto
- Output compactado no excede 2000 chars total

#### A.3 — Team context en prompts
**Archivo**: `aiteam/profiles.py` → `build_prompt()` (linea 159)
**Resuelve**: P6

**Que hacer**:
- Agregar parametro `team_context: str = ""` a `build_prompt()`
- Si no vacio, incluir como seccion "Contexto del equipo" en el prompt
- Actualizar la llamada en orchestrator.py linea 401

**Criterio de done**:
- `build_prompt(role, title, desc, team_context="discovery found X")` incluye la seccion
- Llamada existente sin team_context sigue funcionando (backward compat)

---

### Fase B: Feedback Loop y Gate Iteration

#### B.1 — Recoger feedback de gates fallidos
**Archivo**: `aiteam/orchestrator.py`
**Resuelve**: P3

**Que hacer**:
- Nuevo metodo `_collect_gate_feedback(failed_gate_ids: list[str]) -> str`:
  - Para cada gate_id, obtener task del taskboard
  - Leer `metadata.get("result", "")` o `metadata.get("error", "")`
  - Formatear como feedback accionable
  - Retornar string compactado

#### B.2 — Gate Iteration Loop
**Archivo**: `aiteam/orchestrator.py` → `_release_blocked_parent_tasks()` (linea 965)
**Resuelve**: P3

**Que hacer**:
- Antes de marcar failed, chequear `gate_iteration` vs `max_gate_iterations` (default 2)
- Si iteration < max: recoger feedback, inyectar en metadata, limpiar gates, retry task
- Si iteration >= max: fallo definitivo (comportamiento actual)
- Emitir evento `gate_iteration` con detalles

**Cambios en `_release_blocked_parent_tasks()`**:
```
if failed_gates:
    iteration = task.metadata.get("gate_iteration", 0)
    max_iters = task.metadata.get("max_gate_iterations", 2)
    if iteration < max_iters:
        feedback = self._collect_gate_feedback(failed_gates)
        task.metadata["review_feedback"] = feedback
        task.metadata["gate_iteration"] = iteration + 1
        self._cleanup_gate_tasks(gate_tasks)
        task.metadata.pop("quality_gate_spawned", None)
        task.metadata.pop("quality_gate_tasks", None)
        self.taskboard.retry_task(task.task_id, reason=f"gate_iteration_{iteration+1}")
        self.event_logger.emit("gate_iteration", {...})
        continue  # no marcar failed
    else:
        # fallo definitivo (codigo existente)
```

#### B.3 — Inyectar review feedback en el prompt del engineer
**Archivo**: `aiteam/orchestrator.py` → `_run_task()` (despues de linea 401)
**Resuelve**: P3

**Que hacer**:
- Despues de `build_prompt()`, chequear `task.metadata.get("review_feedback")`
- Si existe, agregar seccion al prompt con el feedback y la instruccion de corregir
- Incluir numero de iteracion

#### B.4 — Limpieza de gate tasks para re-iteracion
**Archivo**: `aiteam/taskboard.py`
**Resuelve**: P3

**Que hacer**:
- Nuevo metodo `remove_tasks(task_ids: list[str])`:
  - Elimina tasks del dict interno `_tasks`
  - Libera file locks asociados
  - Persiste
- Llamado por `_cleanup_gate_tasks()` en el orchestrator

---

### Fase C: Eager Processing y Ledger

#### C.1 — Eager dependency processing
**Archivo**: `aiteam/orchestrator.py` → `process_once()` (linea 116)
**Resuelve**: P4

**Que hacer**:
- Envolver el cuerpo de `process_once()` en un `while True` con cap de sub-iteraciones (max 20)
- Despues de ejecutar claimed_tasks, llamar `_release_blocked_parent_tasks()` y re-chequear `ready_tasks()`
- Si hay nuevas tareas READY, procesarlas inmediatamente
- Si no hay nuevas, break
- Mover `_round += 1` y sync meeting fuera del while interno

**Pseudocodigo**:
```python
def process_once(self) -> int:
    total_processed = 0
    sub_iterations = 0
    max_sub = 20
    while sub_iterations < max_sub:
        sub_iterations += 1
        claimed = self._claim_ready_tasks()
        if not claimed:
            self._release_blocked_parent_tasks()
            # Re-check after releasing
            claimed = self._claim_ready_tasks()
            if not claimed:
                break
        self._execute_claimed_tasks(claimed)
        total_processed += len(claimed)
        self._release_blocked_parent_tasks()

    if total_processed > 0:
        self._round += 1
        self._run_round_sync_meeting()
    return total_processed
```

**Criterio de done**:
- Un pipeline de 6 fases secuenciales se completa en 1-2 rondas en vez de 6+
- Cap de sub_iterations previene loops infinitos

#### C.2 — Team Ledger
**Archivo**: `aiteam/orchestrator.py`
**Resuelve**: P5, P8

**Que hacer**:
- Nuevo metodo `_update_team_ledger(task, output, success)`:
  - Agrega entry al ledger en workflow_state
  - Calcula assessment (progreso, estancamiento)
  - Emite evento si detecta stall
- Llamar desde `_run_task()` al completar o fallar un task
- El ledger se incluye automaticamente en el contexto via `_build_collaboration_context`

**Estructura del ledger entry**:
```python
{
    "round": self._round,
    "phase": task.role.value,
    "task_id": task.task_id,
    "assignee": assignee,
    "status": "completed" | "failed",
    "output_summary": output[:300],
    "iteration": task.metadata.get("gate_iteration", 0),
}
```

**Deteccion de estancamiento**:
- Si ultimas 3 entries son "failed" → emitir `stall_detected`
- Si mismo task aparece 3+ veces → emitir `stale_loop_detected`

---

## 3. Orden de Implementacion

```
A.1 WorkflowState              ← fundacion, todo lo demas depende de esto
 |
 ├── A.2 Result Propagation    ← usa workflow_state
 ├── A.3 Team context prompts  ← firma de build_prompt
 |
B.1 Collect gate feedback      ← independiente
B.4 Taskboard cleanup          ← independiente
 |
 └── B.2 Gate Iteration Loop   ← usa B.1 + B.4
     └── B.3 Review feedback   ← usa B.2
 |
C.1 Eager Processing           ← independiente de A/B
C.2 Team Ledger                ← usa workflow_state (A.1)
```

**Batch 1** (fundacion): A.1, A.3, B.1, B.4 — **COMPLETADO 2026-03-26**
**Batch 2** (propagacion + iteration): A.2, B.2, B.3 — **COMPLETADO 2026-03-26**
**Batch 3** (optimizacion): C.1, C.2 — **COMPLETADO 2026-03-21/26**

---

## 4. Tests de Verificacion

### Tests existentes que deben seguir pasando
- `pytest tests/ -q` — **271 tests passing** (2026-03-26)
- Especial atencion a: `test_orchestrator.py`, `test_taskboard.py`, `test_api_team_chat.py`

### Nuevas verificaciones
- [ ] Workflow state se crea y persiste correctamente
- [ ] Discovery output aparece en el prompt del engineer
- [ ] Gate failure con iteration < 2 → retry con feedback (no mark_failed)
- [ ] Gate failure con iteration >= 2 → mark_failed (comportamiento original)
- [ ] Pipeline 6 fases completa en <= 3 rondas (vs 6+ actual)
- [ ] Ledger detecta stall despues de 3 failures consecutivos
- [ ] `build_prompt` con team_context incluye la seccion
- [ ] `taskboard.remove_tasks` limpia correctamente

---

## 5. Metricas de Exito

| Metrica | Antes | Despues (esperado) |
|---------|-------|-------------------|
| Rondas para pipeline 6 fases | 6-10 | 1-3 | **IMPLEMENTADO** — eager sub-iterations |
| Review feedback llega al engineer | Nunca | Siempre (max 2 iteraciones) | **IMPLEMENTADO** — gate_iteration loop |
| Contexto de fases previas en prompt | 0% | 100% | **IMPLEMENTADO** — _build_dependency_output_context |
| Deteccion de estancamiento | No existe | Automatica (3+ failures) | **IMPLEMENTADO** — _update_team_ledger con stall detection |
| Resultados compartidos entre agentes | Via mailbox (nadie lee) | Via workflow_state (siempre disponible) | **IMPLEMENTADO** — workflow_state.json |
