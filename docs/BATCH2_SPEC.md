# Batch 2 — Barreras de Paralelismo Seguro

**Fecha**: 2026-03-26
**Estado**: EN IMPLEMENTACION
**Agente asignado**: Engineer
**Prerequisito**: Batch 1 completado. Verificar antes de empezar con suite focalizada del batch.

```bash
# Verificacion de prerequisito — debe pasar antes de tocar nada
venv/Scripts/python.exe -m pytest tests/test_parallel_taskboard.py tests/test_orchestrator.py tests/test_taskboard.py -q
# Resultado esperado: suite focalizada del Batch 2 en verde
```

---

## Contexto del problema

El orchestrator puede ejecutar tareas en paralelo cuando `AITEAM_MAX_PARALLEL_TASKS > 1`.
El flujo actual en `process_once()` es:

```
sub-iteration N:
  1. _claim_ready_tasks()         ← reclama TODAS las READY del lote
  2. _execute_claimed_tasks()     ← ejecuta en ThreadPoolExecutor (paralelo o secuencial)
     └─ dentro: _run_task() → mark_completed() → _refresh_readiness()
                                  ← cada thread puede actualizar estados de hijos
  3. _release_blocked_parent_tasks() ← libera tareas bloqueadas por gates fallidos
  4. volver a 1
```

**Problema B2.1**: `_claim_ready_tasks()` llama a `taskboard.ready_tasks()` que internamente
llama `_refresh_readiness()`. Este metodo puede marcar un hijo como READY si todas sus
dependencias estan COMPLETED. Pero en modo paralelo, mientras un padre A se esta ejecutando
(estado CLAIMED), otro padre B puede haber completado ya. Si el hijo C depende de [A, B],
`_refresh_readiness()` solo lo pondra READY cuando AMBOS sean COMPLETED. Esto es correcto.

Sin embargo, hay un caso sutil: `_claim_ready_tasks()` itera sobre `ready_tasks()` y para
cada una llama `claim_task()`. `claim_task()` llama OTRA VEZ a `_refresh_readiness()` antes
de verificar el estado. Esto crea una doble evaluacion no documentada. Si entre las dos
llamadas el estado cambia (otro thread hace `mark_completed`), la segunda evaluacion puede
ver un estado diferente al de la primera.

**Problema B2.2**: No existe una barrera operativa explicita entre el fin de la ejecucion paralela
y el comienzo del siguiente claim. El `ThreadPoolExecutor` ya garantiza que todos los futures
terminaron; el gap real es de observabilidad y de checkpoint explicito entre sub-iteraciones.

**Problema B2.3**: No hay tests que ejerciten ejecucion paralela con dependencias
compartidas y verifiquen que los estados finales son correctos.

**Problema B2.4**: El default `AITEAM_MAX_PARALLEL_TASKS=1` es seguro pero limita la
productividad. No hay documentacion de cuando es seguro subir a 2 o 3.

---

## Solucion detallada

### B2.1 — Validacion de dependencias al momento del claim

**Archivo**: `aiteam/taskboard.py`
**Metodo**: `claim_task()` — linea 43

**Problema exacto** (lineas 43-63 de taskboard.py):
```python
def claim_task(self, task_id: str, assignee: str) -> bool:
    with self._lock:
        self._refresh_readiness()          # ← primera evaluacion
        task = self._tasks.get(task_id)
        if not task or task.state != TaskState.READY:
            return False
        # ... (adquiere file locks y marca CLAIMED)
```

`_refresh_readiness()` recorre TODAS las tareas cada vez que se llama. Es O(N) y se llama
tanto en `ready_tasks()` (dentro de `_claim_ready_tasks`) como en `claim_task()`. La segunda
llamada es redundante y podria ver un estado transitorio en paralelo.

**Que hacer**: Anadir un guard que verifica explicitamente que todas las dependencias del task
esten en COMPLETED antes de permitir el claim. Esto es una doble verificacion de seguridad
independiente del estado READY derivado de `_refresh_readiness`.

**Codigo a anadir** — dentro de `claim_task()`, justo despues de verificar `task.state != TaskState.READY`:

```python
# Guard de seguridad: verificar dependencias directamente, independiente de _refresh_readiness
if task.dependencies:
    unmet = [
        dep for dep in task.dependencies
        if dep not in self._tasks or self._tasks[dep].state != TaskState.COMPLETED
    ]
    if unmet:
        failed = [
            dep for dep in unmet
            if dep in self._tasks and self._tasks[dep].state == TaskState.FAILED
        ]
        if failed:
            task.state = TaskState.BLOCKED
            task.metadata["blocked_reason"] = "dependency_failed"
            task.metadata["blocked_dependencies"] = failed
        else:
            task.state = TaskState.PENDING
        self._save()
        return False
```

**Donde insertarlo**: despues de la linea `if not task or task.state != TaskState.READY: return False`
y antes de la linea `owned_files = self._owned_files(task)`.

**Resultado esperado**: Si por alguna razon un task llega a READY con dependencias sin completar,
el claim lo detecta y lo devuelve a `PENDING` o `BLOCKED` segun el estado real de sus dependencias.

---

### B2.2 — Barrera explicita post-ejecucion

**Archivo**: `aiteam/orchestrator.py`
**Metodo**: `process_once()` — linea 392

**Situacion actual** (lineas 428-430):
```python
self._execute_claimed_tasks(claimed_tasks, active_round)
total_processed += len(claimed_tasks)
self._release_blocked_parent_tasks()
# ← aqui no hay flush explicito antes del siguiente claim
```

**Que hacer**: No llamar `_save()` privado desde el orchestrator. En su lugar:

- emitir un evento `sub_iteration_barrier`,
- exponer un metodo publico `taskboard.checkpoint()`,
- ejecutar ese checkpoint despues de `_release_blocked_parent_tasks()`.

Esto deja una frontera estable y observable entre sub-iteraciones sin acoplar el orchestrator
a detalles privados del taskboard.

**Codigo a cambiar** — en `process_once()`, el bloque del bucle for queda:

```python
self._execute_claimed_tasks(claimed_tasks, active_round)
total_processed += len(claimed_tasks)
self._release_blocked_parent_tasks()
# ── Barrera: checkpoint explicito antes de siguiente sub-iteracion ──
self.taskboard.checkpoint()
```

Ademas, anadir un evento de barrera para trazabilidad:

```python
self.event_logger.emit(
    "sub_iteration_barrier",
    {
        "execution_round": active_round,
        "sub_iteration": sub_iteration,
        "tasks_processed_so_far": total_processed,
    },
)
self.taskboard.checkpoint()
```

**Donde insertarlo**: despues de `self._release_blocked_parent_tasks()` en el cuerpo
del bucle for, antes de volver al inicio del bucle.

---

### B2.3 — Tests de paralelismo y race conditions

**Archivo a crear**: `tests/test_parallel_taskboard.py`

Nota de implementacion: no se sigue literalmente la consigna de "exactamente estos 5 tests".
Se implementa una suite equivalente pero mas alineada con el codigo real y con lo ya cerrado en Batch 1.
La prioridad es cubrir invariantes reales del scheduler y del taskboard.

Suite implementada:

- `test_parallel_claim_is_safe_with_shared_dependency`
- `test_child_not_claimable_while_parent_claimed`
- `test_failed_dependency_remains_blocked_during_claim_guard`
- `test_parallel_tasks_with_shared_child_complete_correctly`
- `test_parallel_run_emits_sub_iteration_barrier`

Referencia historica de la propuesta original:

#### Test 1: `test_parallel_claim_is_safe_with_shared_dependency`
Verifica que dos workers en paralelo no pueden ambos reclamar un hijo con dependencia compartida.

```python
def test_parallel_claim_is_safe_with_shared_dependency() -> None:
    """Dos tareas paralelas no deben poder reclamar el mismo hijo."""
    import threading
    with tempfile.TemporaryDirectory() as tmp:
        board = TaskBoard(Path(tmp) / "tasks.json")
        parent = WorkTask(task_id="P", title="Parent", description="x", role=Role.ENGINEER)
        child = WorkTask(
            task_id="C",
            title="Child",
            description="x",
            role=Role.ENGINEER,
            dependencies=["P"],
        )
        board.add_task(parent)
        board.add_task(child)

        assert board.claim_task("P", "worker-1")
        board.mark_completed("P", details="done")

        # Dos threads intentan reclamar el mismo hijo al mismo tiempo
        results = []
        def try_claim():
            results.append(board.claim_task("C", "worker-x"))

        t1 = threading.Thread(target=try_claim)
        t2 = threading.Thread(target=try_claim)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # Exactamente uno de los dos debe haber tenido exito
        assert results.count(True) == 1
        assert results.count(False) == 1
```

#### Test 2: `test_child_not_claimable_while_parent_claimed`
Verifica que el guard de B2.1 funciona: un hijo no puede reclamarse si su padre esta en CLAIMED.

```python
def test_child_not_claimable_while_parent_claimed() -> None:
    """Un hijo no puede reclamarse si su padre esta CLAIMED (no COMPLETED)."""
    with tempfile.TemporaryDirectory() as tmp:
        board = TaskBoard(Path(tmp) / "tasks.json")
        parent = WorkTask(task_id="P", title="Parent", description="x", role=Role.ENGINEER)
        child = WorkTask(
            task_id="C",
            title="Child",
            description="x",
            role=Role.ENGINEER,
            dependencies=["P"],
        )
        board.add_task(parent)
        board.add_task(child)

        board.claim_task("P", "worker-1")
        # Forzar child a READY artificialmente para simular estado corrupto
        board._tasks["C"].state = TaskState.READY

        # El guard de claim_task debe rechazarlo porque P esta CLAIMED, no COMPLETED
        result = board.claim_task("C", "worker-2")
        assert result is False
        # El child debe haber vuelto a PENDING
        child_task = board.get_task("C")
        assert child_task is not None
        assert child_task.state == TaskState.PENDING
```

#### Test 3: `test_parallel_tasks_with_shared_child_complete_correctly`
Test de integracion: pipeline A+B en paralelo -> C que depende de ambos.

```python
def test_parallel_tasks_with_shared_child_complete_correctly() -> None:
    """Pipeline A||B -> C: C solo se ejecuta cuando A Y B estan completados."""
    import tempfile
    from aiteam.adapters.subscription import SubscriptionAdapter
    from aiteam.config import build_default_router_policy
    from aiteam.orchestrator import AITeamOrchestrator
    from aiteam.router import HybridRouter
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True)
        project_root.mkdir(parents=True)

        adapter = SubscriptionAdapter(
            name="test_adapter",
            provider="openai",
            model="gpt-4.1",
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())

        with patch.dict("os.environ", {
            "AITEAM_ENABLE_LIVE_API": "0",
            "AITEAM_MAX_PARALLEL_TASKS": "2",
        }, clear=False):
            orch = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task_a = WorkTask(
                task_id="ROOT::A",
                title="Task A",
                description="Analyze component",
                role=Role.RESEARCHER,
                metadata={"skip_quality_gates": True},
            )
            task_b = WorkTask(
                task_id="ROOT::B",
                title="Task B",
                description="Analyze library",
                role=Role.RESEARCHER,
                metadata={"skip_quality_gates": True},
            )
            task_c = WorkTask(
                task_id="ROOT::C",
                title="Task C",
                description="Build integration",
                role=Role.ENGINEER,
                dependencies=["ROOT::A", "ROOT::B"],
                metadata={"skip_quality_gates": True},
            )
            orch.submit_task(task_a)
            orch.submit_task(task_b)
            orch.submit_task(task_c)

            orch.run_until_idle(max_rounds=6)

        final_c = orch.taskboard.get_task("ROOT::C")
        assert final_c is not None
        assert final_c.state == TaskState.COMPLETED, (
            f"Task C deberia estar COMPLETED, estado: {final_c.state.value}. "
            f"Task A: {orch.taskboard.get_task('ROOT::A').state.value}, "
            f"Task B: {orch.taskboard.get_task('ROOT::B').state.value}"
        )
```

#### Test 4: `test_sub_iteration_barrier_event_emitted`
Verifica que el evento `sub_iteration_barrier` se emite despues de cada batch.

```python
def test_sub_iteration_barrier_event_emitted() -> None:
    """El evento sub_iteration_barrier se emite al final de cada sub-iteracion."""
    import tempfile
    from aiteam.adapters.subscription import SubscriptionAdapter
    from aiteam.config import build_default_router_policy
    from aiteam.orchestrator import AITeamOrchestrator
    from aiteam.router import HybridRouter
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True)
        project_root.mkdir(parents=True)

        adapter = SubscriptionAdapter(
            name="test_adapter",
            provider="openai",
            model="gpt-4.1",
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())

        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
            orch = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            task = WorkTask(
                task_id="BAR-1",
                title="Task",
                description="Do something",
                role=Role.RESEARCHER,
                metadata={"skip_quality_gates": True},
            )
            orch.submit_task(task)
            orch.run_until_idle(max_rounds=3)

        all_events = orch.event_logger._records()
        barrier_events = [e for e in all_events if e.get("event_type") == "sub_iteration_barrier"]
        assert len(barrier_events) >= 1, "Debe emitirse al menos un sub_iteration_barrier"
        for ev in barrier_events:
            payload = ev.get("payload", {})
            assert "execution_round" in payload
            assert "sub_iteration" in payload
            assert "tasks_processed_so_far" in payload
```

#### Test 5: `test_default_parallel_config_is_safe_for_production`
Verifica que los defaults de paralelismo son seguros para produccion.

```python
def test_default_parallel_config_is_safe_for_production() -> None:
    """El default AITEAM_MAX_PARALLEL_TASKS=1 es secuencial y siempre seguro."""
    import tempfile
    from aiteam.adapters.subscription import SubscriptionAdapter
    from aiteam.config import build_default_router_policy
    from aiteam.orchestrator import AITeamOrchestrator
    from aiteam.router import HybridRouter
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        runtime_dir = Path(tmp) / "runtime"
        project_root = Path(tmp) / "workspace"
        runtime_dir.mkdir(parents=True)
        project_root.mkdir(parents=True)

        adapter = SubscriptionAdapter(
            name="test_adapter",
            provider="openai",
            model="gpt-4.1",
            capabilities={"coding", "reasoning", "analysis", "review"},
        )
        router = HybridRouter(adapters=[adapter], policy=build_default_router_policy())

        # Sin AITEAM_MAX_PARALLEL_TASKS → default debe ser 1 (secuencial)
        with patch.dict("os.environ", {"AITEAM_ENABLE_LIVE_API": "0"}, clear=False):
            orch = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            assert orch.max_parallel_tasks == 1, (
                f"Default deberia ser 1 (secuencial seguro), es {orch.max_parallel_tasks}"
            )

        # Con AITEAM_MAX_PARALLEL_TASKS=3 → debe respetarse
        with patch.dict("os.environ", {
            "AITEAM_ENABLE_LIVE_API": "0",
            "AITEAM_MAX_PARALLEL_TASKS": "3",
        }, clear=False):
            orch3 = AITeamOrchestrator(
                router=router,
                runtime_dir=runtime_dir,
                project_root=project_root,
            )
            assert orch3.max_parallel_tasks == 3
```

---

### B2.4 — Documentar defaults de paralelismo en CLAUDE.md

**Archivo**: `CLAUDE.md` — seccion `Convenciones`

Anadir al final del archivo:

```markdown
## Paralelismo — configuracion recomendada

| Entorno | AITEAM_MAX_PARALLEL_TASKS | Razon |
|---------|--------------------------|-------|
| dev | 1 (default) | Secuencial, mas facil de debuggear |
| stage | 2 | Paralelo moderado, seguro con file locks |
| prod | 2-3 | Ajustar segun latencia observada |

- Con `AITEAM_MAX_PARALLEL_TASKS=1` la ejecucion es siempre secuencial (mas segura).
- Con valor > 1 se usa `ThreadPoolExecutor`; los file locks del TaskBoard previenen colisiones.
- `AITEAM_PARALLEL_AUTOTUNE=1` ajusta el parallelismo automaticamente segun latencia y tasa de fallo.
- Para debug de race conditions: fijar `AITEAM_MAX_PARALLEL_TASKS=1` y reproducir el problema.
```

---

## Orden de implementacion

```
1. taskboard.py  → claim_task() guard (B2.1)     ← base, sin dependencias
2. orchestrator.py → barrera en process_once() (B2.2)  ← usa taskboard
3. tests/test_parallel_taskboard.py → 5 tests (B2.3)
4. CLAUDE.md → seccion de paralelismo (B2.4)
5. ROADMAP_FLUJOS_Y_AGENTES.md → marcar Batch 2 como COMPLETADO
6. TASKS.md → anadir entrada de Batch 12 (o renombrar si corresponde)
```

---

## Definition of Done

Todos estos comandos deben pasar antes de commitear:

```bash
# 1. Smoke test — no debe romper nada de lo que habia
venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x
# Resultado esperado: "43 passed"

# 2. Tests nuevos — todos los del nuevo archivo deben pasar
venv/Scripts/python.exe -m pytest tests/test_parallel_taskboard.py -v --tb=short
# Resultado esperado: "5 passed"

# 3. Suite completa — no regresiones
venv/Scripts/python.exe -m pytest tests/ -q --tb=short
# Resultado esperado: "276 passed" (271 + 5 nuevos)

# 4. Verificar que el evento sub_iteration_barrier aparece en eventos
venv/Scripts/python.exe -m pytest tests/test_parallel_taskboard.py::test_sub_iteration_barrier_event_emitted -v --tb=short
```

---

## Que NO hacer

- **No cambiar la firma publica de ninguna clase**: `TaskBoard`, `AITeamOrchestrator` deben
  mantener la misma interfaz. Los cambios son internos.
- **No mover `_refresh_readiness`** a otro lugar ni cambiar cuando se llama en `mark_completed`.
- **No subir el default de `AITEAM_MAX_PARALLEL_TASKS`** por encima de 1 en el codigo.
  El default debe seguir siendo 1. Documentar el valor recomendado en `.env.example`.
- **No eliminar el `ThreadPoolExecutor`** ni su logica — solo anadirle la barrera.
- **No tocar** `conftest.py` ni los tests existentes.

---

## Archivos a modificar

| Archivo | Tipo de cambio | Lineas aprox. |
|---------|---------------|---------------|
| `aiteam/taskboard.py` | Anadir guard en `claim_task()` | +8 lineas en L43-63 |
| `aiteam/orchestrator.py` | Anadir barrera + evento en `process_once()` | +8 lineas en L428-430 |
| `tests/test_parallel_taskboard.py` | Crear desde cero | ~160 lineas |
| `CLAUDE.md` | Anadir seccion paralelismo | +15 lineas al final |
| `ROADMAP_FLUJOS_Y_AGENTES.md` | Marcar Batch 2 COMPLETADO | ~5 lineas |
| `TASKS.md` | Entrada de completacion | ~5 lineas |

**Total estimado**: ~200 lineas nuevas o modificadas.

---

## Contexto adicional relevante del codigo

### Donde esta `_claim_ready_tasks` en orchestrator.py
- Linea 320: definicion de `_claim_ready_tasks(self, active_round, sub_iteration)`
- Linea 325: `for task in self.taskboard.ready_tasks():` ← itera todas las READY
- Linea 327: `self.taskboard.claim_task(task.task_id, assignee=assignee)` ← aqui se aplica el guard

### Donde esta `process_once` en orchestrator.py
- Linea 392: definicion de `process_once(self)`
- Linea 410: `claimed_tasks = self._claim_ready_tasks(active_round, sub_iteration)` ← primer claim
- Linea 428: `self._execute_claimed_tasks(claimed_tasks, active_round)` ← ejecucion paralela
- Linea 430: `self._release_blocked_parent_tasks()` ← liberacion de gates
- **Anadir barrera aqui**: despues de linea 430

### Donde esta `claim_task` en taskboard.py
- Linea 43: definicion de `claim_task(self, task_id, assignee)`
- Linea 45: `self._refresh_readiness()` ← primera evaluacion
- Linea 46: `task = self._tasks.get(task_id)`
- Linea 47: `if not task or task.state != TaskState.READY: return False`
- **Anadir guard aqui**: despues de linea 47, antes de linea 49 (`owned_files = ...`)

### Imports necesarios en test_parallel_taskboard.py
```python
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aiteam.taskboard import TaskBoard
from aiteam.types import Role, TaskState, WorkTask
```
