<!-- layer: system-development | audiencia: sesiones de desarrollo | NO es mecanismo de producto -->

# AI Team Hybrid Orchestrator — Handoff Actual

Estado validado: `2026-04-03`, `ORCH-01`, `858 passed`.

Fuentes de verdad operativas:

- `task.md` — backlog vivo y siguiente prioridad
- `docs/HISTORY.md` — registro de hitos completados por bloque
- `docs/ARCHITECTURE_PLAN.md` — mapa de riesgo técnico y fricciones arquitectónicas
- `docs/INDEX.md` — índice de documentación activa

## Aclaración de capas

Este `HANDOFF.md` es un documento de traspaso entre sesiones de desarrollo del sistema.

No debe confundirse con:

- el handoff técnico entre adapters del orquestador
- instrucciones persistentes de proyectos externos (`.aiteam/instructions.md`)
- artefactos creados por AI Teams en productos del usuario (`.aiteam/`)

Referencia: `docs/NAMING_COLLISION_INVESTIGATION.md`

## Estado técnico actual

| Componente | Estado |
|---|---|
| Persistencia | SQLite para `tasks` y `workflow_state`; JSON solo residual en tests/constructores |
| Routing | Catálogo versionado, overrides locales persistidos, frontend editable operativo |
| Proyectos externos | Runtime en `.aiteam/`, planes persistidos, `.aiteam/instructions.md` por proyecto |
| Lead adaptativo | RunHealthReport, PAUSE_FOR_USER, SKIP_PHASE, DEGRADE, briefing de capacidades, lead_memory |
| Evidence gate | Rechaza placeholders, output trivial y diffs ajenos; acepta calidad mínima en live mode |
| Bootstrap | `prepare_dev_env.bat` deja el repo listo en ambas máquinas tras `git pull` |

## Regla de oro

Git comparte código, tests, scripts, documentación y plantillas de configuración.

Cada máquina mantiene local: `venv/`, `runtime/`, `node_modules/`.

## Protocolo al cambiar de máquina

```powershell
# En la máquina origen:
git commit && git push

# En la máquina destino:
git pull
.\scripts\prepare_dev_env.bat
```

## Scripts clave

```powershell
.\scripts\prepare_dev_env.bat      # bootstrap completo tras pull
.\scripts\pytest_local_stable.bat  # runner estable para Windows (evita PermissionError en tmp)
.\start_ide.bat                    # arranca backend + frontend
```

## Suite

```powershell
# Smoke rápido:
.\scripts\pytest_local_stable.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py -q --tb=line -x

# Suite completa (~5 min):
.\scripts\pytest_local_stable.bat tests -q --tb=short
```

## Máquinas

| Máquina | Rol |
|---|---|
| `MAX-GAMINGPC` | principal de desarrollo |
| `ORCH-01` | secundaria |

GitHub es la fuente de verdad del código.

## Siguiente prioridad

Ver `task.md`.
