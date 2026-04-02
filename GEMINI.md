# AI Team Hybrid Orchestrator — Gemini Context

Sistema de orquestacion multi-agente para desarrollo y entrega de software.

Estado validado: `2026-04-02`, `MAX-GAMINGPC`, `763 passed`.

Fuentes de verdad operativas:

- `task.md`
- `walkthrough.md`
- `docs/ARCHITECTURE_PLAN.md`
- `docs/TASKS_2026_03_28.md`
- `docs/INDEX.md`

## Prioridad de trabajo

Este repo se desarrolla en dos maquinas. La prioridad principal es continuidad de desarrollo:

- avanzar en una maquina
- hacer `git push`
- cambiar a la otra
- hacer `git pull`
- seguir programando rapido sin reparar manualmente el entorno

## Modelo operativo correcto

Git comparte solo lo portable:

- codigo
- tests
- scripts
- docs
- plantillas en `config/`

Cada maquina conserva su propio estado local:

- `venv/`
- `runtime/`
- `node_modules/`

No tratar `runtime/` como fuente de verdad compartida.

## Comandos recomendados

Post-pull:

```powershell
.\scripts\prepare_dev_env.bat
```

Python:

```powershell
.\scripts\python_local.bat
```

Tests:

```powershell
.\scripts\pytest_local.bat tests -q --tb=line
```

Arranque del IDE:

```powershell
.\start_ide.bat
```

## Estrategia de commit segura

Si una tanda mezcla entorno, limpieza del repo y funcionalidad, separarla en commits distintos:

1. portabilidad y bootstrap
2. dejar de trackear estado local
3. funcionalidad, tests y docu

Documento de referencia:

- `docs/COMMIT_STRATEGY_2026_04_02.md`

## Persistencia

- SQLite en `runtime/aiteam.db` para `tasks` y `workflow_state`
- JSONL para eventos, ledger y registros append-only
- JSON legacy solo como compatibilidad

## Reglas de configuracion compartida

Si un ajuste debe existir en ambas maquinas, editar:

- `config/adapters.example.json`
- `config/mcp_servers.example.json`
- `config/model_catalog.example.json`

No confiar en cambios hechos solo en:

- `runtime/adapters.json`
- `runtime/mcp_servers.json`
- `runtime/model_catalog.json`
- `runtime/provider_ops.json`
- `runtime/provider_smoke.json`

## Bootstrap local

- `scripts/ensure_local_venv.ps1`: valida o recrea `venv/`
- `scripts/ensure_local_runtime.ps1`: rehidrata `runtime/`
- `scripts/prepare_dev_env.bat`: deja la maquina lista tras un pull

## Dos maquinas

| Maquina | Rol | Notas |
|---------|-----|-------|
| `MAX-GAMINGPC` | principal | desarrollo activo |
| `ORCH-01` | secundaria | continuidad y pruebas |

GitHub es la fuente de verdad del codigo.

## Regla de diagnostico

Antes de tocar nada, listar 3 causas probables.

Orden tipico:

1. entorno local roto
2. configuracion machine-specific mezclada por error
3. runtime local ausente o desfasado

## Documentos utiles

- `AGENTS.md`
- `CLAUDE.md`
- `HANDOFF.md`
- `README.md`
- `walkthrough.md`
- `task.md`
