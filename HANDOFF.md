<!-- layer: system-development | audiencia: sesiones de desarrollo | NO es mecanismo de producto -->

# AI Team Hybrid Orchestrator — Handoff Actual

Estado validado: `2026-04-02`, `MAX-GAMINGPC`, `776 passed`.

Este archivo sustituye el handoff historico de fin de marzo como punto de entrada rapido para retomar el proyecto.

Fuentes de verdad operativas:

- `task.md`
- `walkthrough.md`
- `docs/ARCHITECTURE_PLAN.md`
- `docs/TASKS_2026_03_28.md`
- `docs/INDEX.md`

## Aclaracion de capas

Este `HANDOFF.md` es un documento de traspaso entre sesiones de desarrollo del sistema.

No debe confundirse con:

- el handoff tecnico entre adapters del orquestador
- instrucciones persistentes de proyectos externos
- artefactos creados por AI Teams en productos del usuario

Regla de naming:

- nombres de proveedor como `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` pertenecen a la capa de desarrollo del sistema
- los artefactos de producto de AI Teams van bajo `.aiteam/`
- las instrucciones persistentes del proyecto externo viven en `.aiteam/instructions.md`

Referencia: `docs/NAMING_COLLISION_INVESTIGATION.md`

## Objetivo operativo

La prioridad no es compartir el runtime vivo entre `MAX-GAMINGPC` y `ORCH-01`.
La prioridad es poder seguir programando rapido en ambas maquinas y que un `git pull` no rompa el entorno local.

## Regla de oro

Git comparte:

- codigo
- tests
- scripts
- documentacion
- plantillas de configuracion

Cada maquina mantiene local:

- `venv/`
- `runtime/`
- `node_modules/`

## Protocolo al cambiar de maquina

1. En la maquina donde avanzaste: `git commit` + `git push`
2. En la otra maquina: `git pull`
3. Ejecutar:

```powershell
.\scripts\prepare_dev_env.bat
```

4. Seguir trabajando con:

```powershell
.\scripts\python_local.bat
.\scripts\pytest_local.bat
```

## Estrategia de commit recomendada

Para esta fase del repo, la regla es:

- no mezclar portabilidad, limpieza de estado local y funcionalidad en un único commit grande

Orden recomendado:

1. commit de portabilidad del entorno
2. commit que deja de trackear `runtime/` y otros artefactos locales
3. commit de funcionalidad/tests/docu

Referencia detallada:

- `docs/COMMIT_STRATEGY_2026_04_02.md`

## Estado tecnico actual

- `TaskBoard` y `workflow_state` persisten en SQLite
- La API/UI leen SQLite como fuente normal; JSON legacy queda solo como compatibilidad residual de tests/constructores
- El evidence gate ya no acepta `git diff` ajeno antes de rechazar output simulado
- La persistencia ya no pisa snapshots completos entre procesos
- El repo ya trata `runtime/` como estado local por maquina
- `E10-W9` ya queda cerrado: 8 tests E2E multiagente activos y sin `skip`

## Scripts clave

- `scripts/ensure_local_venv.ps1`
- `scripts/ensure_local_runtime.ps1`
- `scripts/prepare_dev_env.bat`
- `scripts/python_local.bat`
- `scripts/pytest_local.bat`
- `start_ide.bat`

## Reglas de configuracion

Si un cambio debe viajar por Git, editar plantillas en `config/`:

- `config/adapters.example.json`
- `config/mcp_servers.example.json`
- `config/model_catalog.example.json`

No usar estos archivos como fuente de verdad compartida:

- `runtime/adapters.json`
- `runtime/mcp_servers.json`
- `runtime/model_catalog.json`
- `runtime/provider_*.json`
- `runtime/system_check.json`

## Smoke recomendado

```powershell
.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_api_aiteam_state.py -q --tb=line -x
```

## Suite completa

```powershell
.\scripts\pytest_local.bat tests -q --tb=line
```

## Maquinas

| Maquina | Rol |
|---------|-----|
| `MAX-GAMINGPC` | principal de desarrollo |
| `ORCH-01` | secundaria |

GitHub es la fuente de verdad del codigo.

## Siguiente deuda tecnica relevante

- Robustizar la vista `Routing` antes de abrir la fase editable
- Abrir la fase editable de asignacion de providers/modelos por rol con override local seguro
- Mantener limpieza, unificacion y criba de documentacion interna como prioridad activa
- Vigilar unos dias el flujo `pull -> prepare_dev_env -> seguir programando`
