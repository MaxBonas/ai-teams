# AI Team Hybrid Orchestrator — Claude Context

Sistema de orquestacion multi-agente para desarrollo y entrega de software.

Estado validado: `2026-04-02`, `MAX-GAMINGPC`, `763 passed`.

Fuentes de verdad operativas:

- `task.md`
- `walkthrough.md`
- `docs/ARCHITECTURE_PLAN.md`
- `docs/TASKS_2026_03_28.md`
- `docs/INDEX.md`

## Regla principal

La prioridad del proyecto es poder seguir desarrollando rapido en `MAX-GAMINGPC` y `ORCH-01` sin que un `git pull` rompa el entorno local.

Modelo de trabajo correcto:

- Git comparte codigo y plantillas
- Cada maquina mantiene su propio `venv/`
- Cada maquina mantiene su propio `runtime/`
- Cada maquina mantiene su propio `node_modules/`

No intentar compartir `runtime/` vivo ni `venv/` entre maquinas.

## Stack

- Backend: FastAPI + Python 3.12
- Frontend: React 19 + TypeScript 5.9 + Vite
- Persistencia:
  - SQLite en `runtime/aiteam.db` para `tasks` y `workflow_state`
  - JSONL para ledger, eventos y registros append-only
- compatibilidad JSON residual solo para fixtures/tests y constructores legacy
- Tests: `pytest`

## Flujo rapido entre maquinas

Cuando cambies de maquina:

1. En la maquina origen: `git commit` + `git push`
2. En la maquina destino: `git pull`
3. Ejecutar `.\scripts\prepare_dev_env.bat`
4. Seguir trabajando

## Como commitear sin romper la otra maquina

Si el cambio es grande, separarlo por capas:

1. portabilidad del entorno
2. limpieza de estado local trackeado
3. funcionalidad/tests/docu

No mezclar en un solo commit:

- scripts de bootstrap
- borrado de `runtime/` trackeado
- cambios de producto

Referencia operativa:

- `docs/COMMIT_STRATEGY_2026_04_02.md`

Comandos recomendados:

- Preparar entorno: `.\scripts\prepare_dev_env.bat`
- Python local: `.\scripts\python_local.bat`
- Pytest local: `.\scripts\pytest_local.bat`
- Arranque IDE: `.\start_ide.bat`

## Que viaja por Git y que no

Si un cambio debe compartirse entre maquinas, debe vivir en codigo o en plantillas versionadas.

Comparte por Git:

- `aiteam/`
- `api/`
- `ide-frontend/`
- `tests/`
- `scripts/`
- `docs/`
- `config/*.example.json`
- `pyproject.toml`

No compartir por Git:

- `venv/`
- `runtime/` salvo `runtime/ollama/Modelfile.aiteam-qwen-coder`
- `ide-frontend/node_modules/`
- logs, caches y artefactos generados

Regla practica:

- Si cambias configuracion compartida, editar `config/*.example.json`
- No usar `runtime/*.json` como fuente de verdad compartida

## Bootstrap local

Scripts importantes:

- `scripts/ensure_local_venv.ps1`
  - valida o recrea `venv/`
  - instala dependencias desde `pyproject.toml`
- `scripts/ensure_local_runtime.ps1`
  - rehidrata `runtime/` local desde plantillas en `config/`
- `scripts/prepare_dev_env.bat`
  - ejecuta ambos pasos y deja el repo listo para seguir programando

`start_ide.bat` ya intenta preparar `venv/` y `runtime/` antes de arrancar.

## Pruebas

Smoke rapido:

```powershell
.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x
```

Suite completa:

```powershell
.\scripts\pytest_local.bat tests -q --tb=line
```

## Dos maquinas

| Maquina | Rol | Notas |
|---------|-----|-------|
| `MAX-GAMINGPC` | Principal de desarrollo | Windows 11, usuario `she__` |
| `ORCH-01` | Secundaria | Windows 11, acceso por RustDesk |

GitHub es la fuente de verdad del codigo.

Syncthing no debe sincronizar:

- `venv/`
- `node_modules/`
- `runtime/`
- `.git/`
- `__pycache__/`

## Diagnostico antes de tocar nada

Regla: listar 3 causas probables antes de tocar nada. Arreglar de una en una y verificar.

Orden sugerido para fallos tipicos en Windows:

1. Entorno local roto (`venv`, PATH, launcher, deps)
2. Path con espacios o ruta de otra maquina
3. Estado runtime local ausente o contaminado

## Notas operativas

- `api/main.py` sigue siendo un archivo grande y acoplado
- `runtime/` es local por maquina aunque la persistencia principal ya use SQLite
- Los warnings `UnicodeDecodeError cp1252` en subprocesos externos son ruido del SO salvo que rompan comportamiento real

## Documentos de referencia

- `AGENTS.md`: contexto operativo general del repo
- `README.md`: arranque y flujo recomendado
- `walkthrough.md`: estabilizacion tecnica reciente
- `task.md`: backlog y siguientes pasos
