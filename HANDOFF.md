<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-05-04`

AI Teams esta en limpieza profunda y reconstruccion Paperclip-like. La compatibilidad con proyectos antiguos, dogfooding y la suite legacy deja de ser objetivo.

## Fuentes vivas

- `task.md`
- `docs/MIGRATION_PAPERCLIP.md`
- `docs/PAPERCLIP_GUIDE.md`
- `docs/INDEX.md`
- `docs/HISTORY.md`
- `AGENTS.md`

## Estado tecnico

Actualizacion reciente:

- Paperclip queda documentado como guia practica en `docs/PAPERCLIP_GUIDE.md`: consultar su codigo local para liveness, wakeups, interactions, adapters y recovery; adaptar patrones sin perder Lead-first, hiring dinamico y delegacion economica.
- Crear proyecto nuevo exige seleccionar al menos un adapter de usuario. La allowlist queda en `.aiteam/project_config.json`.
- El Lead inicial y el hiring dinamico usan solo adapters disponibles en el proyecto: seniors/quorum/reviewers prefieren modelos avanzados; workers/QA prefieren modelos baratos o locales cuando existen.
- El panel de hiring permite corregir perfil y modelo por agente antes de aceptar.
- El cockpit incluye borrado de proyecto actual con confirmacion exacta `DELETE`; el backend solo borra carpetas dentro de la raiz de proyectos, rechaza symlinks y vuelve a workspace no configurado.

Implementado en la sesion anterior (pre-resumen):

- schema v2 SQLite en `aiteam/db/schema.sql`
- migrador dry-run/apply en `scripts/migrate_to_v2.py`
- modelo de perfiles en `aiteam/run_profiles.py`
- checkout atomico en `aiteam/db/issues.py`
- runs durables y wakeups en `aiteam/db/runs.py`, `aiteam/db/wakeups.py`
- scheduler en `aiteam/heartbeat/scheduler.py`
- `SubprocessAdapterRuntime` en `aiteam/adapters/subprocess_adapter.py`
- `RunExecutor` + `HeartbeatLoop` en `aiteam/heartbeat/executor.py` y `loop.py`
- `reconcile_stale_runs` en startup via lifespan en `api/main.py`
- `issue_thread_interactions` (create/list/get/resolve) en `aiteam/db/interactions.py` y `api/routers/interactions.py`
- `activity_log` en `aiteam/db/activity_log.py`
- CRUD: goals, agents, issues, runs en `aiteam/db/` + `api/routers/`

Implementado en esta sesion:

- **`aiteam/db/comments.py`** — `create_comment`, `list_comments`, `get_comment` sobre tabla `issue_comments`, `ORDER BY created_at ASC, rowid ASC`
- **`api/routers/comments.py`** — 3 endpoints: `POST /api/issues/{issue_id}/comments`, `GET /api/issues/{issue_id}/comments`, `GET /api/comments/{comment_id}`; ya enganchado en `api/main.py`
- **`aiteam/skills.py`** — `load_skill(role, skills_dir=None)` y `list_skills()`; mapea alias de rol a archivo en `skills/`; devuelve `None` si no existe
- **Skills markdown** en `skills/`: `lead.md`, `engineer.md`, `reviewer.md`, `qa.md`, `quorum_senior.md`
- **`RunExecutor`** actualizado: `_agent_info()` lee `adapter_type` Y `role`; inyecta `AITEAM_AGENT_ROLE` y `AITEAM_AGENT_SKILL` (contenido markdown) como vars de entorno al proceso agente; `StaticAdapterRuntime.build_env()` expone ambas vars
- **Fase 6.1 — Approvals sensibles v2**: `RunExecutor` bloquea issues con `criticality` `high` o `critical` antes de `mark_run_running()`. Si no hay approval, crea `request_confirmation` idempotente (`compliance:{issue_id}:criticality`), deja el run en `queued` y cierra el wakeup actual como `skipped/approval_required`; `resolve_interaction()` ya encola el wakeup de continuacion. Si la interaction esta `accepted`, ejecuta; si esta `rejected`, marca run/wakeup como `failed` con `error_code='approval_rejected'`.
- **Fase 3.1 — FinOps v2**: `aiteam/db/finops.py` implementa `record_cost`, `check_budget`, `BudgetStatus` y periodos mensuales. `cost_events` incluye `period` e indice `(agent_id, period)`. `RunExecutor` registra `actual_cost_cents` en `cost_events`, actualiza `agents.spent_monthly_cents` y bloquea agentes con presupuesto mensual excedido mediante `request_confirmation` titulada `Budget exceeded`.
- **CLI v2 minimo**: `aiteam/cli.py` ya no importa router/adapters legacy; mantiene `system-check`, `migrate-to-v2` y `budget-status`. Se retiro `scripts/benchmark_parallel_throughput.py` por depender del router eliminado.
- **Lead builtin Paperclip-like**: `lead_builtin` propone equipo/backlog via `suggest_tasks`; aceptar crea agentes `engineer/reviewer/qa`, issues hijas y wakeups de asignacion; los roles `role_builtin` reportan al Lead; el Lead resume child reports y pide una confirmacion ligera para cerrar el ciclo.
- **Cockpit frontend v2**: Vite ya no muestra una vista unica cruda de wakeups/runs. Tiene primera apertura de proyecto, timeline cronologico, vista de issue/thread, runs, equipo, decisions pendientes y alta de nueva tarea para el Lead dentro del proyecto activo.
- **Nuevas tareas por proyecto**: el usuario puede crear una issue nueva asignada al Lead; el cockpit encola `new_task`, drena el control plane y el Lead genera sub-issues con IDs derivados de la issue padre, no `issue:intake:*`.
- **Timeline backend**: `GET /api/timeline` compone issues, comments, interactions creadas/resueltas y runs desde SQLite en orden cronologico; el frontend ya consume esta fuente durable en vez de reconstruir la timeline solo en React.
- **Timeline Fase 8 parcial**: `/api/timeline` tambien incluye `activity_log`, `cost_events` y `tool_access`, preparando la observabilidad unificada del cockpit.
- **Activity logging sistematico base**: routers de issues/comments/interactions y `RunExecutor` registran `activity_log` para creacion/actualizacion de issues, comments, interactions y cambios de estado producidos por agentes. La timeline ya puede mostrar acciones humanas/API y trabajo interno del executor.
- **Activity logging control-plane**: routers de goals, agents, wakeups, checkout y `run-once` tambien escriben `activity_log`. La timeline ya cubre objetivos, hiring/config de agentes, cola de wakeups y ejecuciones manuales del control plane.
- **Tool access v2 base**: `aiteam/db/tool_access.py` registra/lista decisiones de herramientas. `RunExecutor` registra el adapter usado como `tool_access` (`allowed`) y adapters no registrados como `denied` antes de fallback manual. Nuevo endpoint `GET /api/tool-access` para auditoria directa; `/api/timeline` ya muestra estos eventos.
- **MCP node_repl**: si `node_repl` falla por conflicto de versiones de Node, verificar que la version de Node en `PATH` sea la correcta y que no haya otra instalacion anterior solapando. `node_repl` debe responder con la version activa.
- **Recovery/no-op de Lead**: si un proyecto viejo tiene la confirmacion de cierre aceptada pero la issue padre sigue abierta, el Lead reconcilia y marca `done`. Si se despierta al Lead sin trabajo pendiente, la run queda `skipped/no_pending_lead_work` en vez de `completed` vacio.
- **Cockpit legible para runs recientes**: la timeline carga lo mas reciente primero, `/api/timeline` etiqueta runs en espanol y la UI muestra una banda de ultima run con resumen humano.
- **Fase 7.1 — Skills desde rescate**: las skills de Lead, Engineer, Reviewer, QA y Quorum ahora codifican contrato de heartbeat inspirado en Paperclip: checkout/409, progreso durable, no polling, blockers explicitos, bajo ruido y delegacion economica.
- **Delegation payload enriquecido**: `lead_intake` incluye por sub-issue `delegation_type`, `cost_tier`, `report_to`, `reviewed_by`, `evidence_required` y `risk_checks`; se persiste en metadata de issue y payload de wakeup.
- **Fase 7.2 — Plan documents v2**: nueva tabla `issue_documents` + `issue_document_revisions`, helpers `aiteam/db/documents.py` y endpoints `GET/PUT /api/issues/{issue_id}/documents/{key}` con conflicto `base_revision_id` (`409`) estilo Paperclip. `GET /api/issues/{id}` incluye `plan_document` si existe.
- **Tests**: coverage nueva en `tests/test_run_executor.py`, `tests/test_finops_db.py`, `tests/test_cli_v2.py` y `tests/test_issue_documents.py` — suite completa: **126 tests, todos en verde**

## Siguiente prioridad

Por orden de impacto:

1. **Lead LLM/API real sobre documentos**: sustituir el Lead builtin determinista por Lead LLM/API que escriba/actualice el documento `plan`, pida confirmacion sobre esa revision y cree sub-issues via API usando la skill, perfiles y politica de coste.

2. **Fase 8 — SQLite logs consolidation**: refinar la UI para filtrar timeline por tipo/issue/actor y extender `tool_access` cuando existan wrappers MCP/herramientas reales.

3. **Frontend cockpit v2.1**: seguir mejorando navegacion por timeline/runs, filtros por status/owner, y accion clara para crear/cambiar proyecto sin depender del workspace guardado. Ya existe banda de ultima run y timeline descendente.

4. **Budget policy ampliada**: si hace falta mas adelante, portar desde `docs/legacy_rescue/` senales de presion, forecast mensual y anomalias; el gate basico ya existe.

## Verificacion recomendada

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# debe salir: 126 passed
```

```python
# smoke check skills
from aiteam.skills import load_skill, list_skills
assert load_skill("lead") is not None
assert "AITEAM_AGENT_SKILL" in __import__('aiteam.adapters.registry', fromlist=['StaticAdapterRuntime']).StaticAdapterRuntime(
    __import__('aiteam.adapters.registry', fromlist=['AdapterDescriptor']).AdapterDescriptor(adapter_type='manual', channel='manual')
).build_env(run_id='r1', wake_context={'agent_role': 'lead', 'agent_skill': 'x'})
```

Si Windows bloquea temporales de pytest, limpiar manualmente al reiniciar la sesion o usar un `--basetemp` nuevo.
