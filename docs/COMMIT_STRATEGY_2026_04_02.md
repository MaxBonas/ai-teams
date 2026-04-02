# Estrategia de commit y pull entre `MAX-GAMINGPC` y `ORCH-01`

Fecha: `2026-04-02`
Estado del repo revisado: worktree grande, mezcla de:

- portabilidad de entorno
- limpieza de artefactos locales
- funcionalidad backend/frontend
- tests
- documentación

## Objetivo

Hacer el siguiente push de forma que:

- `git pull` en `MAX-GAMINGPC` no rompa el entorno local
- `git pull` en `ORCH-01` no arrastre `runtime/`, `venv/` ni artefactos locales
- la transición desde archivos runtime históricamente trackeados sea controlada
- si algo falla, sea fácil saber en qué bloque falló

## Regla principal

No hacer un commit monolítico.

La estrategia recomendada es en **3 commits**:

1. `chore/dev-env-portability`
2. `chore/stop-tracking-local-state`
3. `feat/orchestrator-routing-visibility`

## Commit 1 — `chore/dev-env-portability`

Este commit solo debe contener lo necesario para que un `pull` seguido de
`.\scripts\prepare_dev_env.bat` deje la máquina lista para trabajar.

### Archivos a incluir

- `.gitignore`
- `pyproject.toml`
- `package.json`
- `ide-frontend/package-lock.json`
- `scripts/dev.mjs`
- `scripts/ensure_frontend_deps.ps1`
- `scripts/ensure_local_runtime.ps1`
- `scripts/ensure_local_venv.ps1`
- `scripts/prepare_dev_env.bat`
- `scripts/pytest_local.bat`
- `scripts/python_local.bat`
- `start_ide.bat`
- `CONTRIBUTING.md`
- `.github/pull_request_template.md`

### Adaptación recomendada antes de cerrar este commit

Añadir también:

- `.gitattributes`

Con política simple:

- `*.py`, `*.ts`, `*.tsx`, `*.js`, `*.json`, `*.md`, `*.toml`, `*.yml` → `eol=lf`
- `*.bat`, `*.cmd`, `*.ps1` → `eol=crlf`

Razón:

- reducir ruido `LF/CRLF`
- evitar diffs innecesarios entre dos máquinas Windows
- hacer más predecible el `pull`

### Qué no meter aquí

- ningún cambio funcional de backend/frontend
- ningún test de producto
- ningún borrado de `runtime/`

## Commit 2 — `chore/stop-tracking-local-state`

Este commit hace una sola cosa:

- sacar del repo lo que ya no debe viajar por Git

### Archivos a incluir

Solo borrados ya intencionales:

- `.aiteam_snapshots/20260220T144449Z-c07a07.zip`
- `.aiteam_snapshots/20260220T144826Z-8061ad.zip`
- `.aiteam_snapshots/20260220T145708Z-cb901c.zip`
- `.aiteam_snapshots/20260220T152435Z-908126.zip`
- `.aiteam_snapshots/manifest.json`
- `ide-frontend/build_errors.txt`
- `ide-frontend/tsc_errors.txt`
- `runtime/adapters.json`
- `runtime/archive/events_archive_20260303T190133Z.jsonl`
- `runtime/errors.json`
- `runtime/errors.txt`
- `runtime/file_locks.json`
- `runtime/learning_registry.jsonl/learning_registry.jsonl`
- `runtime/mcp_servers.json`
- `runtime/model_catalog.json`
- `runtime/notebooklm_bridge_status.json`
- `runtime/notebooklm_outbox/sync_20260221T011405Z.json`
- `runtime/notebooklm_outbox/sync_20260221T012147Z.json`
- `runtime/notebooklm_ready/notebooklm_ready_20260221T012147Z.md`
- `runtime/provider_accounts.json`
- `runtime/provider_doctor.json`
- `runtime/provider_ops.json`
- `runtime/provider_smoke.json`
- `runtime/skills_registry.json`
- `runtime/system_check.json`
- `runtime/tool_inventory.json`
- `runtime/tool_lock.json`
- `runtime/tools.lock.json`

### Qué debe quedar fuera incluso si existe localmente

- todo el resto de `runtime/`, salvo:
  - `runtime/ollama/Modelfile.aiteam-qwen-coder`

### Riesgo de este commit

Este es el commit que más puede complicar el primer `pull` en `ORCH-01`, porque allí esos archivos
pueden seguir estando trackeados y además modificados localmente.

### Protocolo de transición para `ORCH-01` antes de hacer pull de este commit

1. Hacer backup manual de los overrides locales útiles:
   - `runtime/adapters.json`
   - `runtime/mcp_servers.json`
   - `runtime/model_catalog.json`
   - `runtime/provider_accounts.json`
   - `runtime/provider_ops.json`
   - cualquier otro `runtime/*.json` que contenga configuración manual importante
2. Guardarlos fuera del repo, por ejemplo en:
   - `C:\Users\she__\Documents\Antigravity Projects\orch01_runtime_backup_2026_04_02\`
3. Hacer `git status --short runtime .aiteam_snapshots ide-frontend/build_errors.txt ide-frontend/tsc_errors.txt`
4. Si hay modificaciones locales en esos archivos trackeados, no forzar nada a ciegas:
   - comparar primero con el backup
   - dejar que el `pull` elimine del repo esos paths
5. Tras el `pull`, ejecutar:
   - `.\scripts\prepare_dev_env.bat`
6. Restaurar solo overrides locales necesarios, no todo `runtime/` entero

## Commit 3 — `feat/orchestrator-routing-visibility`

Aquí sí va el resto del trabajo funcional, tests y docu.

### Backend core

- `aiteam/adapters/api.py`
- `aiteam/adapters/base.py`
- `aiteam/adapters/subscription.py`
- `aiteam/chat_policy.py`
- `aiteam/cli.py`
- `aiteam/config.py`
- `aiteam/context_curator.py`
- `aiteam/lead_control.py`
- `aiteam/orchestrator.py`
- `aiteam/profiles.py`
- `aiteam/router.py`
- `aiteam/runtime.py`
- `aiteam/taskboard.py`
- `aiteam/types.py`
- `aiteam/evidence_gate.py`
- `aiteam/sim_mode.py`
- `aiteam/sqlite_store.py`

### API

- `api/main.py`
- `api/routers/aiteam.py`
- `api/utils.py`
- `api/chat_delegate.py`
- `api/chat_logic.py`
- `api/chat_models.py`
- `api/chat_observability.py`
- `api/chat_preplan.py`
- `api/chat_quality.py`
- `api/chat_replan.py`

### Frontend

- `ide-frontend/src/components/AgentLane.tsx`
- `ide-frontend/src/components/AgentPanel.tsx`
- `ide-frontend/src/components/StatusPanel.tsx`
- `ide-frontend/src/components/TeamChat.tsx`
- `ide-frontend/src/components/TeamLogOutputViewer.tsx`
- `ide-frontend/src/components/RoutingCatalogPanel.tsx`
- `ide-frontend/src/styles/team.css`

### Config compartida

- `config/routing_policy.example.json`

### Tests

- `tests/conftest.py`
- `tests/test_api_adapter_live.py`
- `tests/test_api_aiteam_state.py`
- `tests/test_api_team_chat.py`
- `tests/test_chat_policy.py`
- `tests/test_e2e_multiagent.py`
- `tests/test_evidence_gate.py`
- `tests/test_finops_anomaly.py`
- `tests/test_integration_cli.py`
- `tests/test_lcp_directives.py`
- `tests/test_lead_control.py`
- `tests/test_mid_run_clarify.py`
- `tests/test_orchestrator.py`
- `tests/test_parallel_taskboard.py`
- `tests/test_policy_defaults.py`
- `tests/test_scout_preflight.py`
- `tests/test_taskboard.py`

### Documentación viva

- `README.md`
- `AGENTS.md`
- `CLAUDE.md`
- `GEMINI.md`
- `HANDOFF.md`
- `task.md`
- `walkthrough.md`
- `docs/ARCHITECTURE_PLAN.md`
- `docs/INDEX.md`
- `docs/PRODUCTION_ROLLOUT_RUNBOOK.md`
- `docs/ROUTING_CATALOG_VIEW.md`
- `docs/TASKS_2026_03_28.md`

### Documentación histórica marcada como histórica

- `PLAN_AGENTIDAD.md`
- `PLAN_MEJORAS.md`
- `ROADMAP_FLUJOS_Y_AGENTES.md`
- `ROADMAP_PRODUCCION_AITEAM.md`
- `TASKS.md`

## Qué NO debe entrar en ningún commit

- `venv/`
- `ide-frontend/node_modules/`
- `runtime/` local por máquina
- `.claude/settings.local.json`
- logs, outputs temporales, caches
- backups manuales de `runtime/`

## Orden recomendado de pull en la otra máquina

### Si los commits se publican uno a uno

Orden más seguro:

1. pull de `chore/dev-env-portability`
2. ejecutar `.\scripts\prepare_dev_env.bat`
3. validar arranque mínimo:
   - `start_ide.bat`
   - `.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py -q --tb=line -x`
4. hacer backup local de `runtime/` si la máquina todavía conserva overrides manuales
5. pull de `chore/stop-tracking-local-state`
6. ejecutar `.\scripts\prepare_dev_env.bat`
7. pull de `feat/orchestrator-routing-visibility`
8. ejecutar:
   - `.\scripts\pytest_local.bat tests -q --tb=short`

### Si se decide publicar todo de una vez

No es lo ideal, pero si se hace:

1. backup previo de overrides locales en `ORCH-01`
2. `git pull`
3. `.\scripts\prepare_dev_env.bat`
4. revisar `runtime/` y restaurar solo overrides necesarios
5. validar arranque y tests

## Criterio final

La frontera correcta para que el `pull` sea soportable entre máquinas es esta:

- Git transporta código, tests, scripts, templates y documentación
- cada máquina reconstruye su `venv/`
- cada máquina rehidrata su `runtime/`
- ningún estado operativo efímero vuelve a estar trackeado

Si se respeta esa frontera, el próximo cambio de máquina debería volver a ser:

- `git pull`
- `.\scripts\prepare_dev_env.bat`
- seguir programando
