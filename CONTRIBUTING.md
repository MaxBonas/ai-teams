# Contributing

Guia corta para contribuir a `AI Team Hybrid Orchestrator`.

## Objetivo principal

Este repo se desarrolla activamente en dos maquinas:

- `MAX-GAMINGPC`
- `ORCH-01`

La prioridad operativa es esta:

- poder avanzar en una maquina
- hacer `git push`
- cambiar a la otra
- hacer `git pull`
- seguir programando rapido sin arreglar manualmente el entorno

## Regla de oro

Git comparte solo lo portable:

- codigo
- tests
- scripts
- documentacion
- plantillas de configuracion

Cada maquina mantiene local:

- `venv/`
- `runtime/`
- `node_modules/`

No usar `runtime/` como fuente de verdad compartida.

## Capas y naming

Este repo mezcla tres capas que conviene separar al escribir o documentar cambios:

- Capa 0/1: herramientas externas y desarrollo del sistema (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `HANDOFF.md`)
- Capa 2: producto AI Teams ejecutandose sobre proyectos externos

Reglas:

- no usar `agent` sin calificar si puede haber ambiguedad
- no proponer `AGENTS.md`, `CLAUDE.md` o `GEMINI.md` como artefactos de producto
- para proyectos externos, usar siempre el namespace `.aiteam/`
- las instrucciones persistentes del proyecto van en `.aiteam/instructions.md`

Referencia: `docs/NAMING_COLLISION_INVESTIGATION.md`

## Flujo recomendado al cambiar de maquina

1. En la maquina origen:

```powershell
git status
git add -A
git commit -m "mensaje"
git push
```

2. En la maquina destino:

```powershell
git pull
.\scripts\prepare_dev_env.bat
```

3. Seguir trabajando con:

```powershell
.\scripts\python_local.bat
.\scripts\pytest_local_stable.bat
```

## Estrategia de commits grandes

Si una tanda es amplia, separarla al menos así:

1. `chore/dev-env-portability`
2. `chore/stop-tracking-local-state`
3. `feat/...` o `fix/...` con funcionalidad real

No mezclar en el mismo commit:

- scripts de bootstrap
- borrado de `runtime/` trackeado
- cambios de backend/frontend de producto

Si el commit elimina archivos históricamente trackeados en `runtime/`, hacer backup local antes de `pull` en la otra máquina.

Referencia: `docs/INDEX.md` → sección "Documentos operativos".

## Entorno local

Bootstrap del entorno:

```powershell
.\scripts\prepare_dev_env.bat
```

Este comando:

- rehidrata `runtime/` local
- valida o recrea `venv/`
- deja el frontend listo si faltan dependencias

Comandos recomendados:

- Python: `.\scripts\python_local.bat`
- Tests: `.\scripts\pytest_local_stable.bat`
- IDE completo: `.\start_ide.bat`

## Configuracion compartida

Si un cambio debe viajar bien por Git entre las dos maquinas, editar plantillas en `config/`:

- `config/adapters.example.json`
- `config/mcp_servers.example.json`
- `config/model_catalog.example.json`
- otros `*.example.json` del repo

No confiar en cambios hechos solo en:

- `runtime/adapters.json`
- `runtime/mcp_servers.json`
- `runtime/model_catalog.json`
- `runtime/provider_*.json`
- `runtime/system_check.json`

Regla de sincronizacion:

- `prepare_dev_env` actualiza un archivo local de `runtime/` desde su plantilla solo si ese archivo seguia sincronizado con la ultima plantilla aplicada
- si detecta divergencia local, conserva el override de esa maquina
- `prepare_dev_env` vuelve a instalar deps Python si `pyproject.toml` cambio desde la ultima sincronizacion local

## Persistencia

Persistencia principal actual:

- SQLite en `runtime/aiteam.db` para `tasks` y `workflow_state`
- JSONL para ledger, eventos y registros append-only
- JSON legacy solo como compatibilidad

Aunque exista SQLite, `runtime/` sigue siendo local por maquina.

## Antes de abrir una PR

Minimo recomendado:

```powershell
.\scripts\pytest_local_stable.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_api_aiteam_state.py -q --tb=line -x
```

Si el cambio toca varias areas, correr:

```powershell
.\scripts\pytest_local_stable.bat tests -q --tb=line
```

## Reglas practicas

- Documentacion del proyecto en espanol
- No commitear `.env`, secretos ni credenciales
- No commitear `venv/`, `runtime/` local ni `node_modules/`
- Si algo se rompe tras un `pull`, revisar primero:
  1. `venv` local
  2. runtime local faltante o stale
  3. paths o config de la otra maquina

## Documentos de referencia

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `HANDOFF.md`
- `walkthrough.md`
- `task.md`
