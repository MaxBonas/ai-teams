# Handoff para Codex — AI Teams (2026-04-02)

<!-- layer: system-development | audiencia: Codex | NO es artefacto de producto -->

Hola Codex. Este documento te pone al dia con el estado exacto del sistema en este momento, lo que acaba de cambiar, y lo que tienes que hacer a continuacion. Leelo completo antes de tocar nada.

---

## El proyecto

**Repo**: `github.com/MaxBonas/ai-teams` (privado)
**Ruta local**: `C:\Users\she__\Documents\Antigravity Projects\Ai_Teams`
**Nombre del paquete**: `aiteam-hybrid` v0.1.0
**Suite validada**: `799 passed, 0 failed` — `MAX-GAMINGPC`, `2026-04-02`

Es un orquestador multi-agente (IDE multimodelo): Lead, Scout, Researcher, Engineer, Reviewer, QA. Backend FastAPI + SQLite, frontend React 19 + TypeScript + Vite. Los tests son la fuente de verdad.

**Documentos que debes leer antes de empezar**:
1. `AGENTS.md` — contexto operativo del repo, glosario de capas, normas criticas
2. `task.md` — estado actual y backlog inmediato
3. `docs/IMPLEMENTATION_PLAYBOOK.md` — guia tecnica detallada con archivos, funciones y tests para cada tarea
4. `docs/NAMING_COLLISION_INVESTIGATION.md` — investigacion de colisiones de nombres (critico para B8b)
5. `docs/COMMUNICATION_GUIDE_FOR_DEVS.md` — como hablar del sistema sin confundir capas

---

## Lo que acaba de pasar en esta sesion

### URGENTE-1 resuelto: 2 tests LCP fallando

**Causa raiz**: los checkpoints `lead_report_*` y `lead_preflight_*` del orquestador se bloqueaban con `specialist_quorum_not_met` antes de poder ejecutar y emitir sus directivas LCP (`[FORCE_GATE]`, `[RETRY_ROUTE]`).

El mecanismo de bloqueo: `_refresh_context_pressure()` propagaba `context_curator_recommended=True` desde el workflow state al metadata de CUALQUIER tarea nueva, incluidos los checkpoints del Lead. Eso activaba `wants_context_curator=True` → `context_curator` se añadia al roster de especialistas → el quorum fallaba porque no habia MCP disponible en el entorno de test → la tarea se marcaba `BLOCKED`.

**Fix aplicado** en `aiteam/orchestrator.py`:
```python
# En _maybe_spawn_lead_report_checkpoint() y _maybe_spawn_lead_preflight_checkpoint():
metadata={
    ...,
    "skip_specialist_prefetch": True,   # ← AÑADIDO
}
```

El flag `skip_specialist_prefetch: True` hace que `_collect_specialist_prefetch_context()` retorne `""` inmediatamente (lineas 312-313), saltando todo el sistema de quorum. Los checkpoints del Lead reciben su contexto en el `task_description`, no necesitan MCP.

**Tests afectados** (ahora pasan):
- `test_chat_force_gate_integration_reopens_completed_phase`
- `test_chat_retry_route_integration_retries_target_with_alternate_adapter`

---

### Investigacion de colisiones de nombres completada

Este sistema construye otros sistemas. Eso crea colisiones de lenguaje sistematicas. Se hizo una investigacion completa y se crearon dos documentos clave:

**`docs/NAMING_COLLISION_INVESTIGATION.md`** — mapa de todas las colisiones con nivel de peligro:
- Colision 1 `AGENTS.md` (🔴 CRITICO): Codex escribe `AGENTS.md` en proyectos externos; si AI Teams leyera ese archivo como instrucciones del usuario, el Lead seguiria instrucciones de Codex sin que nadie lo note
- `agent` (🔴 CRITICO): puede referirse al agente de desarrollo (Codex, Claude Code) o al rol del orquestador (Lead, Engineer)
- `project` (🔴 CRITICO): puede ser el sistema Ai_Teams o un proyecto externo gestionado
- `task`, `run`, `phase`, `workspace` (🟠 ALTO): todos ambiguos entre capas

**`docs/COMMUNICATION_GUIDE_FOR_DEVS.md`** — vocabulario canonico y patrones de fraseado para evitar ambiguedad.

### Correccion critica de B8b

El diseno original de B8b leia `workspace/AGENTS.md`. Eso ya esta corregido en `docs/IMPLEMENTATION_PLAYBOOK.md`:

```python
# INCORRECTO (diseno original, NO implementar):
_agents_md_path = workspace / "AGENTS.md"

# CORRECTO:
_instructions_path = workspace / ".aiteam" / "instructions.md"
```

**Razon**: `AGENTS.md` en proyectos externos es la convencion de Codex/OpenCode. Si el usuario usa Codex en su proyecto, Codex puede crear o modificar ese archivo, y AI Teams lo leeria como instrucciones del usuario para el Lead. Resultado: el Lead ejecuta instrucciones de Codex, no del usuario. No hay error visible.

La solucion es que todos los artefactos de producto de AI Teams vivan bajo `.aiteam/` del proyecto externo — namespace propio que nunca colisiona con convenciones de proveedor.

---

## Las tres capas del sistema (critico)

Este sistema construye otros sistemas. Los mismos terminos significan cosas diferentes segun la capa.

| Capa | Que es | Ejemplos |
|---|---|---|
| **Capa 0** | Herramientas que usas para programar el sistema | Codex, Claude Code, Gemini CLI |
| **Capa 1** | El sistema AI Teams que estamos construyendo | `orchestrator.py`, `taskboard.py`, la UI, los docs de desarrollo |
| **Capa 2** | Lo que AI Teams produce para usuarios finales | Runtime de proyecto externo, Lead respondiendo al usuario, `.aiteam/` |

**Vocabulario rapido**:
- "el sistema" = Ai_Teams (Capa 1)
- "el producto" = lo que AI Teams hace para usuarios (Capa 2)
- "proyecto externo" = un proyecto gestionado por AI Teams (Capa 2)
- "agente de desarrollo" = Codex, Claude Code (Capa 0)
- "rol" = Lead, Engineer, Reviewer (Capa 2)
- "WorkTask" = tarea del orquestador (Capa 2)
- "workspace del usuario" = directorio del proyecto externo (Capa 2)

---

## Estado actual del backlog

### Cerrado/listo

- [x] URGENTE-1: 2 tests LCP fallando — `skip_specialist_prefetch: True` en checkpoints
- [x] URGENTE-2: prefetch best-effort con retry corto y degradacion graceful para `context_curator`
- [x] SQLite como persistencia principal
- [x] Evidence gate, planning first-class, LCP directives
- [x] Peer consultation summary en UI
- [x] Vista consultable `Routing` en StatusPanel + `/api/aiteam/routing/catalog`
- [x] B7a/B7b/B7c: catalogo endurecido + overrides persistidos + UI editable minima operativa
- [x] B8a: planes persistidos como `.md` visibles del proyecto
- [x] B8b: `.aiteam/instructions.md` por proyecto
- [x] B8c: `Plan/Quorum` MVP con `quorum: bool`, consultor adicional y consolidacion final del Lead
- [x] B9a: runtime externo en `.aiteam/`
- [x] B5: `AITEAM_SIM_MODE` canonico
- [x] Bootstrap local: `venv/`, `runtime/`, flujo entre maquinas

### Estado de esta tanda

`URGENTE-2`, `B7a`, `B7b`, `B7c`, `B8a`, `B8b`, `B8c`, `B9a`, `B9b` y `B9c` ya quedaron cerrados en codigo, tests y docu.

- La semántica operativa en proyectos externos ya quedó visible en UI y API: `StatusPanel` consume `last_chat_run.task_operational_summary` y distingue `pending`, `blocked`, `waiting_user` y `carried_over` con motivos operativos visibles.
- `resolve_runtime_dir()` quedó endurecido para Windows: si `runtime -> .aiteam` falla con `WinError 5`, reintenta corto y luego absorbe el contenido legacy de forma segura en `.aiteam/`.
- La siguiente deuda de producto ya no es semántica de estados, sino convertir la auditoría de `test_aiteams` en fixes priorizados y seguir limpiando la docu viva.

---

## Como verificar antes y despues de cualquier cambio

```bash
# Smoke test rapido (~2s)
.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x

# Suite completa
.\scripts\pytest_local.bat tests -q --tb=line

# TypeScript (si tocas frontend)
cd ide-frontend && npm exec -- tsc -b
```

**Regla**: verificar suite antes de empezar. Si hay tests rotos que no son tuyos, documentarlo y no proceder hasta entender por que.

### Nota operativa para ORCH-01 / Codex en Windows

En ORCH-01 y en algunas sesiones de Codex sobre Windows, una corrida monolítica de pytest puede acercarse al timeout de la sesión o chocar con temporales/permisos del entorno. Además, ejecutar dos corridas a la vez puede bloquear el `venv`.

Usar preferentemente:

```bash
.\scripts\pytest_local_stable.bat ...
```

Y, si la validación amplia tarda demasiado, partirla en 2 o 3 fases:

```bash
.\scripts\pytest_local_stable.bat tests/test_lcp_directives.py tests/test_taskboard.py tests/test_run_health.py tests/test_mid_run_clarify.py tests/test_orchestrator.py -q --tb=short
.\scripts\pytest_local_stable.bat tests/test_api_team_chat.py -q --tb=short
.\scripts\pytest_local_stable.bat tests/test_api_aiteam_state.py -q --tb=short
```

No lanzar esas baterías en paralelo en Windows.

---

## Normas criticas que no debes olvidar

1. **Tests primero**: ninguna feature se da por done sin tests que la cubran. El criterio de done de cada tarea esta en `docs/IMPLEMENTATION_PLAYBOOK.md`.

2. **No refactorizar sin razon funcional**: `api/main.py` es grande y acoplado — dejarlo como esta salvo que haya necesidad de producto. `TeamChat.tsx` (73KB) es riesgoso de tocar — extraer hooks solo si la feature lo requiere.

3. **`.aiteam/` para todo Capa 2 en proyectos externos**: AI Teams nunca crea `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` ni archivos con nombres de convencion de proveedor en proyectos externos. Todo va bajo `.aiteam/`.

4. **El propio repo usa `runtime/`**: B9a solo aplica a proyectos externos. El repo Ai_Teams mantiene `runtime/` para compatibilidad.

5. **SQLite es la fuente de verdad**: `tasks` y `workflow_state` viven en `runtime/aiteam.db`. La compatibilidad JSON residual solo existe para fixtures/tests legacy y constructores legacy. No anadir nuevos lectores JSON al camino normal.

6. **`skip_specialist_prefetch`**: si creas un tipo nuevo de tarea que es checkpoint del Lead o similar (no necesita MCP), anadir `"skip_specialist_prefetch": True` en su metadata para evitar el mismo bug que URGENTE-1.
7. **Prefetch best-effort**: `context_curator` ya no debe bloquear tareas padre cuando un adapter queda temporalmente inelegible; mantén esa degradación graceful.

8. **Dos maquinas**: `venv/`, `runtime/`, `node_modules/` son locales por maquina. No viajan por Git. Si cambias de maquina: `git pull` + `.\scripts\prepare_dev_env.bat`.

9. **Runtime externo robusto**: si un proyecto externo acaba con `.aiteam/` y `runtime/` a la vez, la resolución del runtime debe absorber ficheros legacy faltantes en `.aiteam/` y no dejar estado útil invisible.

10. **Artefactos de producto en `last_chat_run`**: el preview de `files` puede truncarse; el total real debe viajar aparte (`file_count`) y el mensaje no debe derivarse del preview.

---

## Orden de ejecucion recomendado

```
Tanda B7-B9 cerrada
```

La guia tecnica completa con archivos, funciones, patrones de codigo y tests requeridos para cada tarea esta en `docs/IMPLEMENTATION_PLAYBOOK.md`. Siguela en orden.

---

## Puntos debiles conocidos (no tocar salvo necesidad)

- `api/main.py`: grande y acoplado. Refactorizacion parcial ya hecha en `api/chat_*.py`. No seguir troceando.
- `aiteam/cli.py`: 108KB. Extraer a `aiteam/project.py` solo cuando un endpoint nuevo lo requiera.
- `ide-frontend/src/components/TeamChat.tsx`: 73KB. Tocar con cuidado.
- `SqliteStore`: se instancia por llamada en `_save_workflow_state`. Solo optimizar si se detecta latencia real.
- Compatibilidad JSON residual en tests: migrar uno a uno cuando fallen, no batch proactivo.

---

## Señales de que algo va mal

- Tests de `test_api_team_chat.py` fallando → probablemente cambio en formato de prompts del Lead
- Tests de `test_orchestrator.py` fallando → cambio en logica de fases o estados de tasks
- TypeScript compile error → propiedad nueva en la API no reflejada en tipos del frontend
- `specialist_quorum_not_met` en un checkpoint nuevo → falta `skip_specialist_prefetch: True`
- `no_eligible_adapter` para `context_curator` → revisar que el fallback graceful siga activo y que no vuelva a contar contra quorum

---

Lee los documentos, ejecuta el smoke test, y define la siguiente tanda solo si no aparece una regresion real mas urgente.
