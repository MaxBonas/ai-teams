# Implementation Playbook — Tareas pendientes con guia tecnica completa

Fecha: `2026-04-02`
Estado base: `799 passed, 0 failed`
Maquina: `MAX-GAMINGPC`

Este documento es la guia operativa para agentes que deben implementar las tareas pendientes.
Cada tarea incluye: contexto, archivos involucrados, estrategia tecnica, patron de codigo, tests requeridos y criterio de done.

**Regla principal**: no refactorizar sin razon funcional. Tests y comportamiento real mandan sobre estetica.

## Orden de ejecucion de los proximos bloques

```
C1 (delegate lazy)              ← fix rapido, alto impacto en ruido de backlog
C2 (nuevo intento limpio)       ← fix de UX en continuidad entre runs
C3 (entrega minima en vacio)    ← fix de percepcion en proyectos nuevos
    │
    ▼
A1 (RunHealthReport)            ← Lead ve lo que paso en la run
A3 (SKIP_PHASE + DEGRADE)       ← Lead puede cerrar dignamente
    ├─▶ A2 (PAUSE_FOR_USER)     ← Lead puede preguntar al usuario
    └─▶ A4 (capabilities)       ← Lead planifica con lo que tiene
A5 (lead_memory)                ← Lead aprende entre runs
```

Ver diseno completo de A1-A5 en `docs/LEAD_ADAPTIVE_FLOW_VISION.md`.

---

## Estado operativo actualizado

- `URGENTE-2` completado: prefetch de especialistas con retry corto y degradacion graceful para `context_curator`.
- `B7a` completado: payload de `/api/aiteam/routing/catalog` versionado y endurecido.
- `B9a` completado: runtime externo resuelto en `.aiteam/` con migracion automatica.
- Semantica operativa completada: `StatusPanel` ya distingue `pending`, `blocked`, `waiting_user` y `carried_over` con motivos visibles desde `last_chat_run.task_operational_summary`.
- Hardening posterior de `B9a`: `resolve_runtime_dir()` ya reintenta `runtime -> .aiteam` y degrada a absorcion segura del contenido legacy si `rename()` falla temporalmente en Windows.
- `B8a` completado: los modos de planning persisten el plan como `.md` visible del proyecto.
- `B8b` completado: `.aiteam/instructions.md` por proyecto inyectado en `lead_intake`.
- `B8c` completado: quorum opcional de planning con consultor adicional y consolidacion final del Lead.
- `B7b` implementado: overrides locales persistidos en `routing_overrides.json`, aplicados al router y expuestos por API.
- `B7c` completado: la pestaña `Routing` ya soporta edicion local con diff, validacion previa y preview de impacto.

Validacion dirigida mas reciente de `B7b`:

- `tests/test_api_aiteam_state.py`: `33 passed`
- `tests/test_routing_overrides.py`: `5 passed`
- `tests/test_router.py -k "role_provider_exclusions_from_policy or role_primary_provider"`: `2 passed`

Validacion dirigida mas reciente de `B8c`:

- `tests/test_api_team_chat.py`: `74 passed`

Validacion mas reciente de semantica operativa y runtime externo:

- `tests/test_api_aiteam_state.py`: `40 passed`
- `tests -q --tb=short`: `799 passed`

---

## URGENTE-1: Arreglar los 2 tests fallidos

### Contexto

Hay 2 tests de integracion fallando en `tests/test_api_team_chat.py`. Ambos testean directivas LCP (Lead Control Protocol) que el Lead emite durante `lead_close`:

1. `test_chat_force_gate_integration_reopens_completed_phase`
2. `test_chat_retry_route_integration_retries_target_with_alternate_adapter`

Estos tests validan mecanica central del sistema: quality gates y routing retry. No son cosmeticos.

### Test 1: force_gate

**Que deberia pasar**:
- El Lead emite `[FORCE_GATE: "build"]` durante `lead_close`
- El sistema re-abre la fase `build` para re-ejecucion
- Se crean tareas downstream `::build::review` y `::build::qa`
- Esas tareas aparecen en el runtime

**Que falla**:
- `AssertionError: '::build::review' not found in tasks JSON`
- Las tareas downstream no se crean

**Donde mirar** (en este orden):
1. `api/main.py` lineas ~1914-1981: `_extract_force_gate_request_from_outputs()` — esta funcion busca la directiva en los outputs de fases lead y la aplica
2. `aiteam/lead_control.py` lineas 150-152: parsing de `[FORCE_GATE: ...]`
3. `aiteam/lead_control.py` funcion `iter_lead_checkpoint_directives()` lineas 212-232: itera outputs de fases lead
4. El test adapter `ForceGateIntegrationAdapter` en `tests/test_api_team_chat.py` lineas 179-280: emite la directiva cuando recibe prompt de lead_close con "Fase origen: review"

**Hipotesis mas probables** (verificar en este orden):
1. El adapter no esta emitiendo la directiva porque el prompt de lead_close cambio de formato (verificar que "Fase origen: review" aparece en el texto que recibe el adapter)
2. `iter_lead_checkpoint_directives()` no encuentra la fase correcta porque el nombre del output cambio (verificar que `phase_outputs` contiene un key que empieza con `lead_`)
3. El force_gate se encuentra pero la tarea target no se localiza correctamente (verificar el matching entre el nombre de fase en la directiva y los task_ids reales)

**Como diagnosticar**:
```python
# Anadir prints temporales en api/main.py dentro de _extract_force_gate_request_from_outputs:
# 1. Imprimir phase_outputs.keys()
# 2. Imprimir las directivas encontradas por iter_lead_checkpoint_directives
# 3. Imprimir el task target encontrado
```

**Patron de fix**: si el formato del prompt de lead_close cambio (por ejemplo, ya no dice "Fase origen:"), hay que actualizar el adapter de test para matchear el formato nuevo. Si la logica de matching de tareas cambio, hay que ajustar el lookup en `_extract_force_gate_request_from_outputs`.

### Test 2: retry_route

**Que deberia pasar**:
- El Lead emite `[RETRY_ROUTE: "build"]` durante `lead_close`
- El sistema excluye el adapter previo (`primary_route`)
- Re-ejecuta la fase build con adapter alternativo (`secondary_route`)
- `build_task.metadata["last_adapter_name"]` == `"secondary_route"`
- `build_task.metadata["excluded_adapters"]` == `["primary_route"]`

**Que falla**:
- `AssertionError: 'primary_route' != 'secondary_route'`
- El retry no se ejecuto o no excluyo el adapter primario

**Donde mirar** (en este orden):
1. `api/main.py` lineas ~1780-1912: `_extract_retry_route_request_from_outputs()` — misma estructura que force_gate
2. Las condiciones de ejecucion en lineas ~1810-1815: hay 5 condiciones que deben cumplirse TODAS
3. `api/chat_replan.py` lineas 106-119: parsing de `[RETRY_ROUTE: ...]`
4. El test adapter `RetryRouteIntegrationAdapter` en `tests/test_api_team_chat.py` lineas 1011-1100

**Condiciones requeridas para que retry_route se ejecute** (todas deben ser True):
```python
_target_task is not None
_phase_started_for_replan(_target_task)  # task in {CLAIMED, COMPLETED, FAILED, WAITING_USER} or execution_round > 0
not _phase_started_for_replan(_lead_close_task)  # lead_close NO ha sido CLAIMED/COMPLETED
len(_retry_removed_phase_ids) > 0
not _downstream_started  # ninguna fase downstream ha iniciado
```

**Hipotesis mas probables**:
1. El adapter no esta emitiendo la directiva (verificar igual que force_gate)
2. Una de las 5 condiciones falla silenciosamente (la mas probable: `_phase_started_for_replan(_lead_close_task)` devuelve True cuando no deberia, bloqueando el retry)
3. El retry se ejecuta pero con el mismo adapter porque `excluded_adapters` no se propaga al router

**Como diagnosticar**:
```python
# En api/main.py, antes del bloque de condiciones (~linea 1810):
# 1. Imprimir _target_task, su estado y metadata
# 2. Imprimir _phase_started_for_replan(_target_task)
# 3. Imprimir _phase_started_for_replan(_lead_close_task)
# 4. Imprimir _retry_removed_phase_ids
# 5. Imprimir _downstream_started
```

**Estrategia de fix**:
- Si el problema es el formato del prompt → actualizar adapter de test
- Si el problema es una condicion que falla → determinar por que cambio y ajustar
- Si el problema es propagacion de excluded_adapters → verificar que `aiteam/router.py` linea 365 recibe y aplica la exclusion

### Tests de verificacion

Despues de arreglar ambos tests:
```bash
venv/Scripts/python.exe -m pytest tests/test_api_team_chat.py -k "force_gate or retry_route" -v --tb=long
```

Suite completa:
```bash
venv/Scripts/python.exe -m pytest tests/ -q --tb=short
```

**Criterio de done**: 776+ passed, 0 failed.

---

## URGENTE-2 (completado): Bug `context_curator/no_eligible_adapter`

### Contexto

En el proyecto externo `test_aiteams`, tres runs encadenadas fallaron:
- `CHAT-ABCE891F`: `lead_intake` fallo por placeholder gate agresivo (ya arreglado)
- `CHAT-1F789CCB`: `lead_intake` completo pero `plan_research` quedo `blocked`
- `CHAT-28015BB0`: continuacion; mismo bloqueo

La causa inmediata observada en eventos:
```
specialist_prefetch_failed
  specialist: context_curator
  reason: no_eligible_adapter
→ specialist_quorum_not_met
```

**IMPORTANTE**: la reproduccion en frio del router actual SI encuentra adapters elegibles para ese caso. El bug es intermitente o dependiente del estado live.

### Archivos involucrados

1. `aiteam/tool_specialists.py` — prefetch de especialistas, buscar `prefetch` y `no_eligible`
2. `aiteam/router.py` funcion `_eligible()` lineas 342-405 — filtrado de adapters
3. `aiteam/orchestrator.py` — donde se llama al prefetch antes de ejecutar tareas
4. `aiteam/context_curator.py` — el especialista que falla

### Hipotesis ordenadas

1. **Estado transitorio del adapter**: el adapter estaba temporalmente `unavailable()` en el momento del prefetch (rate limit, timeout, cuota agotada) pero se recupero despues. Verificar si `adapter.available()` depende de estado mutable.

2. **Inconsistencia de role_targets en prefetch vs routing normal**: el prefetch puede estar pidiendo un routing con `role_targets` o condiciones distintas a las que usa el routing normal. Verificar que `select_specialists_for_task()` construye el `RoutingRequest` igual que el camino principal.

3. **Ordering/timing**: el prefetch se ejecuta antes de que ciertos adapters se hayan registrado o inicializado. Verificar si hay una race condition entre el registro de adapters y el prefetch.

### Como investigar

```python
# En aiteam/tool_specialists.py, funcion de prefetch:
# 1. Logear el RoutingRequest que se construye
# 2. Logear los adapters disponibles en ese momento
# 3. Logear el resultado de _eligible() para ese request

# En aiteam/router.py _eligible():
# 1. Logear cada adapter descartado y la razon
```

### Estrategia de fix

Si la causa es estado transitorio:
- Anadir retry con backoff corto al prefetch de especialistas (no al routing normal)
- O marcar el prefetch como best-effort y no bloquear la tarea si falla

Si la causa es inconsistencia de RoutingRequest:
- Unificar la construccion del request entre prefetch y routing principal

Si la causa es timing:
- Asegurar que el registro de adapters se completa antes del primer prefetch

### Tests requeridos

Crear test en `tests/test_orchestrator.py` o `tests/test_tool_specialists.py`:
```python
def test_specialist_prefetch_retries_on_transient_unavailability():
    """Si un adapter esta temporalmente unavailable durante prefetch,
    el sistema debe reintentar o degradar gracefully."""
    # Mock adapter que falla available() la primera vez y pasa la segunda
    # Verificar que el prefetch no bloquea la tarea
```

**Criterio de done**: el bug tiene una causa identificada y un fix con test que lo cubre, o se documenta como no reproducible con evidencia de que el retry/degradacion graceful esta implementado.

---

## C-series — Audit fixes de test_aiteams (hacer antes de A1-A5)

Estos tres fixes salen directamente de la auditoria forense del caso `test_aiteams`. Son independientes entre si y mas simples que la serie A. Se pueden hacer en orden o en paralelo.

Referencia: `docs/TEST_AITEAMS_GAME_AUDIT_2026_04_02.md`

---

### C1 — Delegate tasks creadas lazy, no por adelantado

**Contexto**

Hoy cuando `lead_intake` emite el `WORKFLOW_PLAN` con fases, el orchestrator crea inmediatamente todas las tareas delegadas de evidence (`delegate_build_test_runner_0`, `delegate_qa_lint_0`, etc.) para todas las fases, incluso las que todavia no han empezado. El resultado: si `plan_research` se bloquea en la primera fase, el usuario ve 20-30 tareas `pending` que nunca van a ejecutarse, produciendo ruido y falsa sensacion de complejidad.

**Archivos involucrados**

- `aiteam/orchestrator.py` — buscar donde se crean las tareas delegadas en bulk. Probablemente en `_spawn_phase_tasks()` o equivalente.
- `aiteam/taskboard.py` — añadir soporte para `TaskCreationPolicy.LAZY` si se quiere ser explicito.

**Estrategia**

Crear las tareas delegadas de una fase solo cuando la fase padre transiciona a `CLAIMED` o `RUNNING`, no cuando se crea el plan. La logica de dependencias del taskboard ya maneja el caso de "tarea con dependencia pendiente" — lo que cambia es el momento de creacion, no el grafo de dependencias.

```python
# En orchestrator._run_task() o en el punto donde se ejecuta una fase:
# ANTES de ejecutar la fase, crear sus tareas delegadas de evidence
# (no al crear el WORKFLOW_PLAN)

def _spawn_delegate_tasks_for_phase(self, phase_task: WorkTask) -> None:
    """Crea las tareas delegadas de evidence solo cuando la fase padre esta activa."""
    if phase_task.metadata.get("delegates_spawned"):
        return  # ya creadas
    # ... logica actual de creacion de delegates ...
    phase_task.metadata["delegates_spawned"] = True
```

**Advertencia**: verificar que los tests de evidence gate y de delegacion siguen pasando. La logica de quorum puede depender de que las tareas existan desde el principio.

**Tests requeridos**

```python
def test_delegate_tasks_not_created_until_phase_starts():
    """Las tareas delegadas no deben existir en el taskboard hasta que la fase padre arranca."""

def test_delegate_tasks_created_when_phase_claimed():
    """Al reclamar una fase, sus delegates deben crearse inmediatamente."""

def test_blocked_phase_does_not_create_delegates():
    """Si una fase queda blocked, sus delegates no se crean nunca."""
```

**Criterio de done**: en una run donde `plan_research` se bloquea, el taskboard tiene ≤5 tareas visibles (las fases principales + la bloqueada), no 20-30. Tests pasando. Suite completa pasa.

---

### C2 — Opcion explicita nuevo intento vs continuar

**Contexto**

Hoy cuando el usuario envia un mensaje a un proyecto que tiene runs previas en estado malo (`blocked`, `failed`, `simulated_only`), la continuidad es implicita: el sistema intenta continuar desde donde quedo. Eso arrastra deuda de runs previas y mezcla tareas de intentos anteriores con el nuevo intento. El usuario no tiene control sobre esto.

**Archivos involucrados**

- `api/chat_models.py` — añadir campo `retry_mode` o `continuation_policy` a `TeamChatRequest`
- `api/routers/aiteam.py` o `api/main.py` — aplicar la politica antes de lanzar la run
- `aiteam/taskboard.py` — metodo para "archivar" o "limpiar" tareas de runs previas si el usuario elige intento limpio
- `ide-frontend/src/components/TeamChat.tsx` — mostrar opcion cuando el ultimo run termino mal

**Diseno del campo**

```python
# En api/chat_models.py:
class TeamChatRequest(BaseModel):
    ...
    continuation_policy: Literal["auto", "clean_retry", "force_continue"] = "auto"
    # "auto": comportamiento actual (continuar si hay estado previo)
    # "clean_retry": archivar tareas de runs previas, empezar limpio con el mismo objetivo
    # "force_continue": continuar explicitamente aunque la run anterior terminara mal
```

**Logica de clean_retry**

```python
# En api/routers/aiteam.py, antes de lanzar la run:
if payload.continuation_policy == "clean_retry":
    # Marcar tareas de runs previas como "archived" o "superseded"
    # No borrarlas de SQLite — solo cambiar su estado a un estado terminal no-visible
    taskboard.archive_incomplete_tasks(reason="clean_retry_requested")
    # Limpiar workflow_state del task_root anterior si aplica
```

**UI**: cuando `last_chat_run.result` es `blocked`, `failed`, `aborted`, o `simulated`, mostrar en el chat thread un aviso discreto: "El intento anterior no se completo. [Continuar desde aqui] [Empezar limpio]". El boton "Empezar limpio" envia `continuation_policy: "clean_retry"`.

**Tests requeridos**

```python
def test_clean_retry_archives_previous_incomplete_tasks():
    """clean_retry debe marcar las tareas incompletas de runs previas como archivadas."""

def test_clean_retry_starts_fresh_workflow():
    """La run siguiente a clean_retry no hereda blocked tasks de la run anterior."""

def test_force_continue_inherits_previous_state():
    """force_continue debe continuar con las tareas pendientes de la run anterior."""

def test_auto_continues_if_previous_run_was_incomplete():
    """auto debe continuar si hay tareas pendientes, igual que hoy."""
```

**Criterio de done**: el usuario puede elegir explicitamente entre continuar y empezar limpio. El backlog de la run limpia no contiene tareas de runs previas. Tests pasando.

---

### C3 — Entrega minima garantizada en proyectos vacios

**Contexto**

Si el pipeline rompe antes de `build` (por bloqueo de `plan_research`, routing error, gate agresivo), el proyecto externo queda sin ningun archivo visible de producto. El usuario ve que "el sistema trabajo mucho pero no entrego nada". Incluso un plan `.md` o un brief tecnico seria infinitamente mejor que nada.

Esta es la brecha mas importante de percepcion de valor en proyectos nuevos y vacios.

**Que debe producir el sistema aunque `build` nunca arranque**

En orden de prioridad — el sistema produce lo que puede segun hasta donde llego:

1. **Si `lead_intake` completo**: persistir el plan como `.md` en el workspace del proyecto. (Ya implementado en B8a para modos de planning. Extender a todos los modos si el workspace esta vacio.)
2. **Si `lead_intake` completo pero `build` no llego**: generar adicionalmente un `BRIEF.md` o `PROJECT_PLAN.md` en la raiz del workspace con: objetivo, stack propuesto, fases planificadas, advertencia de que la run no alcanzo la ejecucion.
3. **Si todo fallo antes de `lead_intake`**: no hay nada recuperable. Documentar el error en `.aiteam/last_error.md`.

**Archivos involucrados**

- `api/routers/aiteam.py` o `api/main.py` — al cierre de run (antes de emitir `chat_completed`), detectar si el workspace del proyecto externo esta vacio de artefactos y el lead_intake si completo
- `aiteam/run_health.py` (cuando exista en A1) o en un nuevo `aiteam/project_scaffold.py`

**Implementacion provisional (sin A1)**

```python
# En api/routers/aiteam.py, al cierre de run:
def _maybe_deposit_minimal_output(
    workspace: Path,
    lead_output: str,
    phases_completed: list[str],
    chat_id: str,
) -> None:
    """Si el workspace no tiene artefactos de producto y lead_intake completo,
    depositar al menos el plan visible del proyecto."""

    # Solo actuar si el workspace existe y no tiene archivos de producto
    product_files = [
        f for f in workspace.rglob("*")
        if f.is_file()
        and ".aiteam" not in str(f)
        and not str(f).startswith(str(workspace / ".aiteam"))
    ]
    if product_files:
        return  # ya hay artefactos, no hacer nada

    if not lead_output:
        return  # lead_intake no completo, no hay nada que depositar

    # Extraer el WORKFLOW_PLAN del output del Lead si esta disponible
    brief_path = workspace / "PROJECT_PLAN.md"
    brief_content = f"""# Plan del Proyecto

Generado por AI Teams — {chat_id}

> Este archivo se generó porque la run planificó correctamente pero no alcanzó la fase de ejecución.
> Puedes usar este plan como punto de partida para la siguiente run.

---

{lead_output}
"""
    brief_path.write_text(brief_content, encoding="utf-8")
    # Emitir evento
    # orch.event_logger.emit("minimal_output_deposited", {"path": str(brief_path)})
```

**Condicion de activacion**: solo cuando el workspace del proyecto externo no tiene archivos de producto (fuera de `.aiteam/`) y `lead_intake` si completo con output valido.

**Tests requeridos**

```python
def test_minimal_output_deposited_when_workspace_empty_and_build_blocked(tmp_path):
    """Si lead_intake completo pero build quedo bloqueada, debe aparecer PROJECT_PLAN.md."""

def test_minimal_output_not_deposited_when_workspace_has_files(tmp_path):
    """Si ya hay archivos de producto, no depositar PROJECT_PLAN.md."""

def test_minimal_output_not_deposited_when_lead_intake_failed(tmp_path):
    """Si lead_intake no completo, no depositar nada."""
```

**Criterio de done**: un proyecto externo vacio donde `build` nunca arranca tiene al menos un `PROJECT_PLAN.md` con el plan que el Lead genero. Tests pasando. Suite completa pasa.

---

## B7 — Vista editable de routing

### Contexto

La vista consultable (B6) ya esta completada. El endpoint `/api/aiteam/routing/catalog` ya expone providers, adapters, primario/fallbacks y blockers. La pestaña `Routing` en `StatusPanel` ya muestra esto.

Ahora hay que convertirla en superficie editable.

### Fase B7a — Hardening del catalogo (hacer PRIMERO)

**Objetivo**: estabilizar el payload del catalogo para que la UI editable no rompa al cambiar el backend.

**Archivos**:
- `api/routers/aiteam.py` — endpoint `/api/aiteam/routing/catalog`
- `tests/test_api_aiteam_state.py` — tests del catalogo

**Cambios requeridos**:

1. **Anadir `payload_version: int`** al response del catalogo. Empezar con `1`. La UI debe verificar esta version y mostrar warning si no la reconoce.

2. **Separar explicitamente 3 capas en cada entrada por rol**:
```python
{
    "role": "engineer",
    "defaults": {  # lo que viene del codigo/repo
        "providers": ["openai", "google", "groq"],
        "models": ["gpt-5.3-codex", "claude-code", "gemini-3.1-pro"]
    },
    "override_local": null,  # override de esta maquina (cuando exista)
    "effective": {  # lo que realmente resuelve el router ahora
        "primary": {"provider": "openai", "model": "gpt-5.3-codex", "adapter": "openai_sub"},
        "fallbacks": [...]
    }
}
```

3. **Blockers exhaustivos** con codigos estables:
```python
BLOCKER_CODES = {
    "role_targets": "adapter restringido a otros roles",
    "team_lead_guard": "reservado para team_lead",
    "adapter_unavailable": "adapter no disponible",
    "provider_unhealthy": "provider con problemas",
    "cost_exceeded": "excede limite de coste",
    "capability_missing": "falta capacidad requerida",
    "channel_excluded": "canal no permitido para este rol",
}
```

4. **Exponer capacidades por adapter/modelo**:
```python
{
    "channel": "subscription",
    "tier": "pro",
    "cost_class": "high",
    "tool_support": true,
    "stream_support": true,
    "vision": false,
    "thinking": true,
    "long_context": true
}
```

**Tests requeridos**:
```python
def test_routing_catalog_has_payload_version():
    """El catalogo debe incluir payload_version >= 1."""

def test_routing_catalog_separates_defaults_and_effective():
    """Cada rol debe tener defaults y effective separados."""

def test_routing_catalog_blockers_have_stable_codes():
    """Los blockers deben usar codigos del enum conocido."""

def test_routing_catalog_exposes_capabilities():
    """Cada adapter debe exponer channel, tier, tool_support, etc."""
```

**Criterio de done**: payload versionado, capas separadas, blockers con codigos estables, capacidades expuestas. Tests pasando.

### Fase B7b — Persistencia de overrides locales (hacer SEGUNDO)

Estado: `implementado`

**Objetivo**: permitir guardar overrides de routing por maquina sin tocar codigo fuente.

**Archivos a crear/modificar**:
- CREAR `aiteam/routing_overrides.py` — logica de persistencia
- `api/routers/aiteam.py` — endpoints de lectura/escritura
- `aiteam/router.py` — aplicar overrides al resolver rutas

**Diseno de persistencia**:

Los overrides se guardan en `runtime/routing_overrides.json` (local por maquina, no viaja por Git):
```json
{
    "version": 1,
    "created_at": "2026-04-02T...",
    "updated_at": "2026-04-02T...",
    "overrides_by_role": {
        "engineer": {
            "providers": ["openai", "groq"],
            "models": ["gpt-5.3-codex", "gpt-4.1-mini"],
            "primary_provider": "openai",
            "excluded_providers": ["anthropic"]
        }
    }
}
```

**Modulo `aiteam/routing_overrides.py`**:
```python
from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class RoleOverride:
    providers: list[str] | None = None
    models: list[str] | None = None
    primary_provider: str | None = None
    excluded_providers: list[str] = field(default_factory=list)

@dataclass
class RoutingOverrides:
    version: int = 1
    overrides_by_role: dict[str, RoleOverride] = field(default_factory=dict)

def load_overrides(runtime_dir: Path) -> RoutingOverrides:
    """Carga overrides locales. Si no existen, devuelve defaults vacios."""
    path = runtime_dir / "routing_overrides.json"
    if not path.exists():
        return RoutingOverrides()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # ... parse y validar
        return overrides
    except Exception:
        return RoutingOverrides()

def save_overrides(runtime_dir: Path, overrides: RoutingOverrides) -> None:
    """Guarda overrides locales de forma atomica."""
    path = runtime_dir / "routing_overrides.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(overrides), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def validate_overrides(overrides: RoutingOverrides, policy: RouterPolicy) -> list[str]:
    """Valida que los overrides no dejen ningun rol sin ruta viable.
    Devuelve lista de errores. Lista vacia = valido."""
    errors = []
    for role, ovr in overrides.overrides_by_role.items():
        # Verificar que queda al menos 1 provider no excluido
        # Verificar que el primary_provider existe en la lista
        # ...
    return errors
```

**Endpoints nuevos en `api/routers/aiteam.py`**:
```python
@router.get("/api/aiteam/routing/overrides")
async def get_routing_overrides(request: Request):
    """Devuelve overrides locales actuales."""

@router.put("/api/aiteam/routing/overrides")
async def update_routing_overrides(request: Request):
    """Actualiza overrides locales con validacion previa.
    Rechaza si la validacion falla (400)."""

@router.delete("/api/aiteam/routing/overrides")
async def reset_routing_overrides(request: Request):
    """Borra overrides locales, volviendo a defaults del repo."""
```

**Tests requeridos**:
```python
def test_load_overrides_returns_empty_when_no_file():
def test_save_and_load_overrides_roundtrip():
def test_validate_overrides_rejects_role_without_provider():
def test_validate_overrides_accepts_valid_override():
def test_api_update_overrides_validates_before_saving():
def test_api_reset_overrides_deletes_file():
```

**Criterio de done**: overrides se persisten, se validan, se sirven por API y se aplican al router. Reset funciona. Suite completa pasa.

**Implementacion actual**:

- `aiteam/routing_overrides.py` ya persiste `routing_overrides.json` de forma atomica y valida roles/providers/modelos.
- `aiteam/cli.py` ya aplica los overrides locales al `RouterPolicy` efectivo durante `build_default_orchestrator(...)`.
- `aiteam/router.py` ya respeta `role_provider_exclusions` como filtro duro y `role_primary_provider` como preferencia fuerte de ordenacion.
- `api/routers/aiteam.py` ya expone:
  - `GET /api/aiteam/routing/overrides`
  - `PUT /api/aiteam/routing/overrides`
  - `DELETE /api/aiteam/routing/overrides`
- `/api/aiteam/routing/catalog` ya separa `defaults`, `override_local` y `effective` usando el policy efectivo real del router.
- Para proyectos externos, el fichero vive en `.aiteam/routing_overrides.json` porque `runtime_dir` ya se resuelve alli.

### Fase B7c — Frontend editable (hacer TERCERO)

Estado: `en progreso`

**Objetivo**: permitir al usuario editar routing por rol desde la pestaña Routing del StatusPanel.

**Archivo**: `ide-frontend/src/components/RoutingCatalogPanel.tsx` (ya existe, ~19KB)

**Cambios requeridos**:

1. **Dos modos: inspeccion y edicion**. Toggle visible en la UI. En modo inspeccion, todo es read-only. En modo edicion, se habilitan controles.

2. **Por cada rol, permitir**:
   - Reordenar providers (drag & drop o flechas arriba/abajo)
   - Marcar primary
   - Excluir providers
   - Preview de diff antes de guardar

3. **Boton "Guardar"**: llama a `PUT /api/aiteam/routing/overrides` con los cambios
4. **Boton "Reset"**: llama a `DELETE /api/aiteam/routing/overrides`
5. **Validacion visible**: si el backend rechaza (400), mostrar errores

**Patron de componente**:
```tsx
// Estado de edicion
const [editMode, setEditMode] = useState(false);
const [pendingOverrides, setPendingOverrides] = useState<OverridesByRole>({});
const [validationErrors, setValidationErrors] = useState<string[]>([]);

// Guardar
const handleSave = async () => {
    const res = await apiFetch('/api/aiteam/routing/overrides', {
        method: 'PUT',
        body: JSON.stringify(pendingOverrides),
    });
    if (!res.ok) {
        const data = await res.json();
        setValidationErrors(data.errors || ['Error desconocido']);
        return;
    }
    setEditMode(false);
    refreshCatalog();
};
```

**TypeScript no debe fallar**:
```bash
cd ide-frontend && node_modules/.bin/tsc --noEmit
```

**Criterio de done**: modo edicion funcional, validacion visible, guardar/reset operativo, TypeScript clean.

**Avance actual**:

- `RoutingCatalogPanel.tsx` ya tiene toggle visible entre `inspección` y `edición local`.
- La pestaña `Routing` ya permite:
  - editar `providers`
  - editar `models`
  - fijar `primary_provider`
  - excluir providers por rol
  - guardar via `PUT /api/aiteam/routing/overrides`
  - resetear via `DELETE /api/aiteam/routing/overrides`
- La validacion del backend ya se muestra inline en la UI.
- Verificacion actual:
  - `npm exec -- tsc -b` en `ide-frontend/`: OK
  - `vite build`: bloqueado en este sandbox por `esbuild spawn EPERM`

---

## B8 — Planning fuerte con Plan/Quorum

### Contexto

El sistema ya planifica (B2 cerro planning first-class con evidence gate de markdown y run_modes). Pero los planes son estado interno del runtime, no artefactos del proyecto. Y la topologia del Lead es siempre "Lead solo".

B8 tiene dos partes independientes que se pueden hacer en orden:

### Fase B8a — Planes persistidos como archivos del proyecto

**Objetivo**: cuando el Lead genera un plan, guardarlo como `.md` en el proyecto real, no solo en runtime.

**Archivos a modificar**:
- `api/main.py` — despues de que `lead_intake` genere el plan, persistirlo
- `aiteam/orchestrator.py` — o alternativamente, hacer la persistencia aqui

**Donde se genera el plan hoy**:
- `api/main.py` lineas ~1045-1048: `orch.event_logger.emit("chat_plan_created", {...})`
- El plan incluye: `planned_phases`, `lead_run_mode`, `round_budget`, etc.
- El output del Lead (`_lead_output_clean`) contiene el WORKFLOW_PLAN con fases

**Que anadir**:

1. Despues de emitir `chat_plan_created`, si el run_mode es de planning (`planning_only`, `architecture_review`, `roadmap`):

```python
# Persistir plan como archivo del proyecto
if _lead_run_mode in {"planning_only", "architecture_review", "roadmap"}:
    _plan_dir = workspace / "docs" / "aiteam"
    if not _plan_dir.exists():
        _plan_dir = workspace / "planning"
    _plan_dir.mkdir(parents=True, exist_ok=True)

    _slug = re.sub(r"[^a-zA-Z0-9_-]", "_", task_root[:20])
    _plan_filename = f"plan-{datetime.now().strftime('%Y-%m-%d')}-{_slug}.md"
    _plan_path = _plan_dir / _plan_filename

    _plan_content = f"""# Plan: {payload.message[:80]}

**Modo**: {_lead_run_mode}
**Fecha**: {datetime.now().isoformat()}
**Task ID**: {task_root}

---

{_lead_output_clean}
"""
    _plan_path.write_text(_plan_content, encoding="utf-8")

    orch.event_logger.emit(
        "chat_plan_persisted",
        {"task_id": task_root, "path": str(_plan_path), "run_mode": _lead_run_mode},
    )
```

2. Para runs normales (no planning), NO persistir automaticamente — el plan es interno.

**Tests requeridos**:
```python
def test_planning_mode_persists_plan_as_markdown(tmp_path):
    """Una run con run_mode=planning_only debe crear un .md en docs/aiteam/ o planning/."""
    # Configurar adapter que emita [RUN_MODE: planning_only]
    # Ejecutar chat
    # Verificar que existe el archivo .md
    # Verificar que tiene encabezados y contenido estructurado

def test_standard_mode_does_not_persist_plan(tmp_path):
    """Una run normal no debe crear archivos de plan en el workspace."""
```

**Criterio de done**: runs de planning crean `.md` en el proyecto. Runs normales no. Tests pasando.

### Fase B8b — `.aiteam/instructions.md` por proyecto

**Objetivo**: si el workspace del proyecto externo tiene un archivo `.aiteam/instructions.md`, el Lead debe leerlo e incorporarlo en su contexto como instrucciones persistentes del usuario.

**IMPORTANTE — colision de nombres resuelta**: el diseno original de B8b leía `workspace/AGENTS.md`. Esto fue corregido por riesgo de colision critica: `AGENTS.md` en proyectos externos es convencion de Codex/OpenCode (Capa 0). Si el usuario tiene Codex trabajando en su proyecto, AI Teams podria leer el `AGENTS.md` generado por Codex como instrucciones del usuario para el Lead, con resultados impredecibles. Ver `docs/NAMING_COLLISION_INVESTIGATION.md` seccion "Colision 1".

**La solucion**: AI Teams lee `.aiteam/instructions.md` — un archivo en su propio namespace, nunca confundible con convenciones de proveedor. El usuario lo crea manualmente para dar instrucciones persistentes al equipo.

**Archivos a modificar**:
- `aiteam/profiles.py` — `build_system_prompt()` o `build_prompt()` — incorporar contenido
- `api/main.py` — leer `.aiteam/instructions.md` del workspace antes de lead_intake

**Implementacion**:

1. En `api/main.py`, antes de ejecutar `lead_intake`:
```python
_instructions_path = workspace / ".aiteam" / "instructions.md"
_project_instructions = ""
if _instructions_path.exists():
    try:
        _project_instructions = _instructions_path.read_text(encoding="utf-8")[:4000]
    except Exception:
        pass
```

2. Inyectar en el prompt del Lead como seccion:
```python
if _project_instructions:
    _lead_context_sections.append(
        f"--- Instrucciones del proyecto (.aiteam/instructions.md) ---\n{_project_instructions}"
    )
```

3. Emitir evento para trazabilidad:
```python
if _project_instructions:
    orch.event_logger.emit(
        "project_instructions_loaded",
        {"task_id": task_root, "path": str(_instructions_path), "chars": len(_project_instructions)},
    )
```

**Tests requeridos**:
```python
def test_lead_intake_incorporates_project_instructions(tmp_path):
    """Si el workspace tiene .aiteam/instructions.md, su contenido aparece en el prompt del Lead."""
    # Crear .aiteam/instructions.md en tmp_path
    # Ejecutar chat apuntando a tmp_path
    # Verificar que el adapter recibio el contenido en el prompt

def test_lead_intake_works_without_project_instructions(tmp_path):
    """Si no hay .aiteam/instructions.md, la run funciona normalmente."""

def test_lead_intake_does_not_read_agents_md(tmp_path):
    """AI Teams NO debe leer AGENTS.md del proyecto externo (es convencion de Codex)."""
    # Crear AGENTS.md en tmp_path con contenido trampa
    # Verificar que el prompt del Lead NO contiene ese contenido
```

**Criterio de done**: `.aiteam/instructions.md` se lee, se inyecta, se emite evento. Sin el archivo no cambia nada. `AGENTS.md` del proyecto externo no es leido. Tests pasando.

### Fase B8c — Plan/Quorum (implementado, MVP actual)

**Objetivo**: permitir que el Lead consulte a otros modelos avanzados antes de decidir el plan.

**Estado actual**: implementado en una primera version pragmatica.

- `TeamChatRequest` ya expone `quorum: bool = False`
- `api/main.py` ya ejecuta quorum solo en modos de planning
- `aiteam/quorum.py` resuelve una consulta adicional con adapter distinto y una consolidacion final del Lead
- el plan consolidado se persiste con seccion `Quorum del Lead`
- si no hay consultor elegible, el flujo degrada graceful a `lead_only`

**Esta fase es la mas compleja y tiene dependencias**:
- Requiere B7b (overrides de routing, para poder configurar consultores)
- Requiere B8a (persistencia de planes)

**Diseno simplificado para primera version**:

1. Un nuevo parametro en `TeamChatRequest`: `quorum: bool = False`
2. Si `quorum=True` y el run_mode es de planning:
   - Ejecutar `lead_intake` con el modelo principal del Lead
   - Ejecutar `lead_intake` una segunda vez con un modelo consultor diferente (configurable)
   - El Lead recibe ambos outputs y emite el plan final
3. El plan final se persiste (B8a)

**Archivos a crear/modificar**:
- CREAR `aiteam/quorum.py` — logica de consulta multi-modelo
- `api/main.py` — integrar quorum en el flujo de lead_intake
- `api/chat_models.py` — anadir `quorum: bool = False` a `TeamChatRequest`

**Estructura de `aiteam/quorum.py`**:
```python
@dataclass
class QuorumResult:
    lead_plan: str
    consultant_plans: list[dict]  # [{model, output}]
    final_plan: str
    deliberation_log: list[str]

async def run_quorum(
    lead_adapter,
    consultant_adapters: list,
    prompt: str,
    context: str,
) -> QuorumResult:
    """Ejecuta lead_intake con multiples modelos y consolida."""
    # 1. Lead genera plan
    # 2. Cada consultor genera plan independiente
    # 3. Lead recibe todos los planes y emite decision final
```

**Tests requeridos**:
```python
def test_quorum_runs_multiple_models():
def test_quorum_lead_has_final_say():
def test_quorum_persists_consolidated_plan():
def test_no_quorum_by_default():
```

**Criterio de done**: quorum funciona con 2+ modelos, Lead decide, plan se persiste. Sin quorum, todo igual. Tests pasando.

---

## B9 — Separar runtime del sistema en proyectos externos

### Contexto

Hoy el sistema crea `workspace/runtime/` dentro de proyectos externos. Eso mezcla estado interno del orquestador con archivos del proyecto real del usuario. En `test_aiteams`, la raiz del proyecto solo muestra `runtime/` — no hay artefactos de producto visibles.

### Fase B9a — Cambio de raiz runtime

**Objetivo**: usar `.aiteam/` en vez de `runtime/` para el estado interno en proyectos externos.

**Archivos a modificar**:
- `api/main.py` — donde se construye `runtime_dir` para proyectos con workspace
- `aiteam/orchestrator.py` — donde se inicializa `self.runtime_dir`
- `scripts/ensure_local_runtime.ps1` — para el propio repo

**Logica de decision**:
```python
def resolve_runtime_dir(workspace: Path, is_self_project: bool = False) -> Path:
    """Determina donde va el runtime.

    Para el propio repo (dogfooding): usa runtime/ (compatibilidad)
    Para proyectos externos: usa .aiteam/
    """
    if is_self_project:
        return workspace / "runtime"

    aiteam_dir = workspace / ".aiteam"
    legacy_dir = workspace / "runtime"

    # Migracion: si existe runtime/ pero no .aiteam/, migrar
    if legacy_dir.exists() and not aiteam_dir.exists():
        import shutil
        shutil.copytree(str(legacy_dir), str(aiteam_dir))
        # Dejar runtime/ intacto por seguridad, logear la migracion

    aiteam_dir.mkdir(parents=True, exist_ok=True)
    return aiteam_dir
```

**CUIDADO**: el propio repo `Ai_Teams` debe seguir usando `runtime/` (compatibilidad). Solo los proyectos externos usan `.aiteam/`.

**Como detectar si es el propio repo**: verificar si existe `pyproject.toml` con `[project] name = "aiteam"` o si `aiteam/` existe como subdirectorio.

**Tests requeridos**:
```python
def test_external_project_uses_aiteam_dir(tmp_path):
    """Un proyecto externo usa .aiteam/ como runtime dir."""

def test_self_project_uses_runtime_dir(tmp_path):
    """El propio repo sigue usando runtime/."""

def test_migration_from_runtime_to_aiteam(tmp_path):
    """Si existe runtime/ y no .aiteam/, se migra automaticamente."""
```

**Criterio de done**: proyectos externos usan `.aiteam/`, migracion automatica funciona, propio repo no cambia. Tests pasando.

### Fase B9b — Aislamiento de contexto por proyecto

**Estado**: implementado.

**Resultado real**:
- `ContextCuratorStore` ya namespacea el chat context por `project_root`.
- La lectura mantiene compatibilidad con payloads legacy y migra al nombre canonico cuando corresponde.
- Se corrigio un bug real de Windows: usar el `project_key` absoluto como slug de filename hacia que las escrituras privadas fallaran en rutas largas. Ahora los ficheros usan nombres cortos y estables con hash.

**Cobertura valida**:
- `tests/test_context_curator.py::test_context_curator_isolates_by_project_root`
- `tests/test_context_curator.py::test_chat_context_insights_survive_external_runtime_migration`
- `tests/test_api_aiteam_state.py::test_state_and_conversations_include_delegate_economics_from_workflow_state`

### Fase B9c — Visibilidad de artefactos de producto

**Estado**: implementado.

**Resultado real**:
- `api/main.py` ya excluye `.aiteam/` del snapshot de artefactos de workspace.
- `api/routers/aiteam.py` ya expone un bloque estable `product_artifacts` en `last_chat_run`.
- `StatusPanel.tsx` ya muestra artefactos de producto por separado y, si no existen, el mensaje explícito "Esta run no genero artefactos de producto."

**Cobertura valida**:
- `tests/test_api_aiteam_state.py::test_state_last_chat_run_surfaces_product_artifacts_from_probe_event`
- `tests/test_api_aiteam_state.py::test_state_last_chat_run_explicitly_reports_when_no_product_artifacts_exist`
- `npm exec -- tsc -b`
def test_product_artifacts_lists_workspace_files():
def test_no_product_artifacts_message():
```

**Criterio de done**: la API distingue artefactos de producto vs runtime. La UI lo muestra. Tests pasando.

---

## Puntos debiles adicionales detectados

### PD-1: `TeamChat.tsx` es de 73KB

Este componente es el mas grande del frontend. Contiene logica de:
- SSE streaming
- Estado de la conversacion
- Renderizado de mensajes
- Progreso de la run
- Clarificaciones pendientes
- Historial de agentes

**Riesgo**: cualquier cambio en TeamChat.tsx tiene alto riesgo de regresion.

**Recomendacion**: NO refactorizar ahora. Pero si se necesita anadir funcionalidad nueva:
- Extraer la logica de SSE a un custom hook `useTeamChatSSE()`
- Extraer la logica de progreso a un custom hook `useRunProgress()`
- Dejar el componente como orquestador de rendering

**Cuando hacerlo**: solo si un cambio de producto requiere tocar la logica de SSE o progreso. No como refactor proactivo.

### PD-2: `cli.py` es de 108KB

Contiene logica de bootstrap, setup y orquestacion que deberia estar en modulos separados.

**Riesgo**: si se necesita un endpoint API que haga lo mismo que un comando CLI, hay que duplicar codigo.

**Recomendacion**: extraer a `aiteam/project.py` solo cuando un endpoint nuevo lo necesite. No antes.

### PD-3: Compatibilidad JSON residual en tests

Algunos tests todavia construyen objetos via JSON legacy. La lectura normal de la API ya no depende de JSON.

**Riesgo**: bajo. Pero si un test falla por "JSON not found", el fix es migrar ese test a SQLite.

**Recomendacion**: migrar tests uno a uno cuando fallen, no como batch proactivo.

### PD-4: `SqliteStore` instanciado en cada `_save_workflow_state`

El orchestrator crea un `SqliteStore` nuevo por llamada, corriendo `CREATE TABLE IF NOT EXISTS` cada vez.

**Riesgo**: overhead de I/O en runs muy largas (>50 fases).

**Recomendacion**: anadir `self._sqlite_store` lazy al orchestrator. Solo hacerlo si se detecta latencia real.

### PD-5: Sincronizacion Syncthing puede romper venv

Si `venv/` se sincroniza entre maquinas, se rompe porque `pyvenv.cfg` tiene paths hardcoded.

**Mitigacion ya aplicada**: `venv/` esta en `.gitignore` y deberia estar excluido de Syncthing.

**Verificacion**: comprobar que la configuracion de Syncthing en ambas maquinas excluye `venv/`, `node_modules/`, `runtime/`, `.git/`, `__pycache__/`.

---

## Orden de ejecucion recomendado

```
1. B7c: Frontend editable de routing
   ↓
2. B8a: Planes persistidos como .md del proyecto
   ↓
3. B7-B9 cerrados
   ↓
4. Siguiente tanda de backlog
```

**Razon del orden**:
- `B7c` va primero porque el backend de catalogo y overrides ya esta listo y ahora la deuda visible es cerrar la edicion UI
- `B8a` fue antes de `B8c` porque el quorum necesitaba persistir su plan como artefacto visible del proyecto
- `B9b` ya quedo absorbido al estabilizar el `context_curator` para proyectos externos y Windows
- `B9c` ya quedo absorbido en la misma tanda al subir el resumen estable de artefactos al estado API y a `StatusPanel`

---

## Comandos utiles

```bash
# Tests rapidos (smoke)
venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py -q --tb=short -x

# Tests completos
venv/Scripts/python.exe -m pytest tests/ -q --tb=short

# Tests especificos (ejemplo: evidence gate)
venv/Scripts/python.exe -m pytest tests/test_evidence_gate.py -v --tb=long

# TypeScript check (frontend)
cd ide-frontend && node_modules/.bin/tsc --noEmit

# Arrancar backend
venv/Scripts/python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8010

# Arrancar frontend
cd ide-frontend && npm run dev -- --port 9490
```

## Reglas para el agente que implemente

1. **Diagnosticar antes de arreglar**: listar 3 causas probables, verificar cada una, arreglar de una en una.
2. **No tocar archivos que no necesitas cambiar**: si una feature vive en un archivo y no necesitas tocarlo, no lo toques.
3. **Tests primero**: antes de marcar algo como hecho, `pytest tests/ -q` debe pasar con al menos el mismo numero de tests + los nuevos.
4. **Paths con espacios**: siempre entre comillas. `"C:\Users\she__\Documents\Antigravity Projects\Ai_Teams"`
5. **Python desde venv**: siempre `venv/Scripts/python.exe`, nunca `python` a secas.
6. **Git**: no hacer commit sin que te lo pidan. No hacer push sin que te lo pidan.
7. **No anadir abstracciones especulativas**: implementar exactamente lo descrito, no mas.
8. **Encoding en subprocess**: usar `encoding="utf-8", errors="replace"` en llamadas a subprocess para evitar `UnicodeDecodeError cp1252` en Windows.
