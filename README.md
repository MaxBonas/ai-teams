# AI Team Hybrid Orchestrator

Sistema de orquestacion multi-agente para desarrollo y entrega de software.

## Estado validado

- Fecha: `2026-04-02`
- Maquina: `MAX-GAMINGPC`
- Suite completa: `763 passed`
- Persistencia principal: `runtime/aiteam.db` para `tasks` y `workflow_state`
- Compatibilidad legacy: JSON residual solo en fixtures/tests y constructores legacy, fuera del camino normal de la API

## Stack

- Backend: FastAPI + Python 3.12
- Frontend: React 19 + TypeScript 5.9 + Vite
- Persistencia:
  - SQLite para `TaskBoard` y `workflow_state`
  - JSONL para ledger, eventos y otros registros append-only
- Tests: `pytest`

## Arquitectura

El flujo base es:

`lead_intake -> dynamic phases -> lead_close`

Roles activos:

- Team Lead
- Scout
- Researcher
- Engineer
- Reviewer
- QA

Puntos clave:

- Router `pro-first` con fallback a API y budget tiers
- Quality gates obligatorios para trabajo de Engineer
- Workflow state compartido por proyecto
- Continuidad conversacional por agente/proyecto
- SSE para progreso y trazabilidad del equipo

## Arranque

Backend:

```powershell
.\scripts\python_local.bat -m uvicorn api.main:app --reload --port 8010
```

Frontend:

```powershell
Set-Location ide-frontend
npm run dev -- --port 9490
```

Arranque conjunto en Windows:

```powershell
.\start_ide.bat
```

## Tests

Bootstrap local del entorno:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_local_venv.ps1
```

Reanudacion rapida tras `git pull` en cualquiera de las dos maquinas:

```powershell
.\scripts\prepare_dev_env.bat
```

Smoke:

```powershell
.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x
```

Suite completa:

```powershell
.\scripts\pytest_local.bat tests -q --tb=line
```

Si `venv` llega roto desde otra maquina, `start_ide.bat`, `python_local.bat` y `pytest_local.bat` intentan repararlo automaticamente mediante `scripts/ensure_local_venv.ps1`.

## Flujo recomendado entre MAX-GAMINGPC y ORCH-01

El objetivo recomendado no es compartir `runtime/` ni `venv/`, sino compartir solo codigo portable por Git.

Secuencia recomendada:

1. Hacer commit y push en la maquina donde avanzaste.
2. En la otra maquina: `git pull`
3. Ejecutar `.\scripts\prepare_dev_env.bat`
4. Seguir trabajando con `.\scripts\python_local.bat` y `.\scripts\pytest_local.bat`

Invariantes:

- `runtime/` es estado local por maquina y ya no debe viajar por Git.
- `venv/` es local por maquina y se reconstruye si hace falta.
- Las plantillas compartidas viven en `config/*.example.json`.
- `prepare_dev_env` refresca una config local desde plantilla solo si ese archivo seguia sincronizado con la plantilla anterior; si detecta override local, lo conserva.
- `prepare_dev_env` reinstala dependencias Python si detecta cambios en `pyproject.toml`.

## Documentacion clave

- `docs/INDEX.md`: indice vivo de la documentacion
- `CONTRIBUTING.md`: protocolo de contribucion y trabajo entre maquinas
- `walkthrough.md`: resumen tecnico de la estabilizacion reciente
- `task.md`: backlog y siguientes pasos validados
- `docs/TASKS_2026_03_28.md`: backlog ampliado del proyecto
- `docs/ROUTING_CATALOG_VIEW.md`: objetivos y diseño de la vista consultable del routing por rol
- `AGENTS.md`: contexto operativo para agentes en este repo

## Politica de documentacion interna

- Fuente de verdad operativa: `task.md`, `walkthrough.md`, `docs/ARCHITECTURE_PLAN.md`, `docs/TASKS_2026_03_28.md`, `docs/INDEX.md`
- Fuente de verdad de codigo: Git + tests + runtime SQLite
- Los documentos root historicos (`TASKS.md`, `PLAN_AGENTIDAD.md`, `PLAN_MEJORAS.md`, `ROADMAP_*.md`) se conservan como referencia, no como backlog activo
