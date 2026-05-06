# Estado actual y siguientes pasos

Fecha: `2026-05-04`

Plan rector activo: `docs/MIGRATION_PAPERCLIP.md`.

## Limpieza profunda

- [x] Retirar documentacion legacy, archivo historico interno y docs desalineados.
- [x] Retirar `CLAUDE.md`, `GEMINI.md` y `walkthrough.md`.
- [x] Retirar tests legacy que protegian dogfooding, JSONL, router antiguo, `[WORKFLOW_PLAN]`, gates antiguos y flujos round-based.
- [x] Limpiar runtime local antiguo: DB, JSONL, memoria, sesiones, sandboxes y configs runtime no fuente de verdad.
- [x] Retirar `AtomicFileWriter`/tests JSONL y plantillas legacy de router/MCP/model catalog.
- [x] Rescatar piezas legacy valiosas como snapshots aislados y notas de port v2 en `docs/legacy_rescue/`.
- [ ] Eliminar restos temporales bloqueados por Windows tras reinicio o liberacion de handles.
- [x] Extirpar codigo legacy ahora que la suite vieja ya no lo protege.

## Migracion Paperclip Patterns

- [x] **Fase 0 - Preparacion documental**: plan rector registrado y docs activas reducidas.
- [x] **Fase 1 - Schema v2 paralelo agresivo**: `aiteam/db/schema.sql`, migrador idempotente y tests para issues, agents, blueprints, assignments, runs, wakeups, events y costes.
- [x] **Fase 1.5 - Lead-first + perfiles**: `solo_lead`, `lead_quorum`, `full_team`, hiring dinamico y politica de delegacion economica.
- [x] **Fase 2 - Checkout atomico**: primitiva SQLite + endpoint `POST /api/issues/{id}/checkout`; `FileLockRegistry` retirado del camino principal.
- [x] **Fase 3 - Runs durables con economia**: enganchar ejecucion real a `runs`, coste, canal subscription/API, supervisor y ahorro estimado/real.
- [x] **Fase 3.1 - FinOps v2 rescatado**: `record_cost`/`check_budget` sobre `cost_events`, periodo mensual y `budget_monthly_cents`; `RunExecutor` registra coste real y bloquea presupuesto excedido con `request_confirmation`.
- [x] **Fase 4a - Retirar round-based legacy**: `api/main.py` reducido a control plane, `api/chat_*` y `/api/aiteam` legacy retirados, `aiteam/orchestrator.py` convertido en stub explicito sin `process_once()`/`run_until_idle()`.
- [x] **Fase 4a.1 - Poda de modulos legacy no activos**: retirados router/scoring viejo, JSONL ledgers, MCP/tooling legacy, memoria/mailbox, policies de chat y scripts de ingestion antiguos.
- [x] **Fase 4a.2 - Frontend v2 minimo**: Vite reducido a cockpit de control plane; retirados TeamChat, routing UI legacy, MCP panels, logs JSONL, Monaco/xterm/layout IDE viejo y dependencias asociadas.
- [x] **Fase 5a - Adapter registry minimo**: retirados adapters legacy REST/subscription/external; queda contrato `AdapterRegistry`/`AdapterDescriptor` por `adapter_type`.
- [x] **Fase 5a.1 - Config v2 minima**: `prepare_dev_env` ya solo rehidrata `control_plane.json` y `agents.json`; retiradas plantillas legacy de router, MCP, skills library y tool catalogs.
- [x] **Fase 5a.2 - TaskBoard shim reducido**: retiradas reglas legacy de support pre-phase/gates blandos; queda solo dependencias basicas, estados y checkout v2 temporal.
- [x] **CLI v2 minimo**: `aiteam.cli` ya no importa router/adapters legacy; comandos vivos `system-check`, `migrate-to-v2`, `budget-status`.
- [x] **Fase 4b - Wakeup queue real**: loop persistente, adapter execution y reconciliation.
- [x] **Fase 5b - Adapter execution real**: conectar registry v2 a procesos/API reales con env context y coste.
- [x] **Fase 6 - Interactions**: reemplazar pausa por bracket directives con `issue_thread_interactions`.
- [x] **CRUD endpoints + activity_log**: issues, agents, goals, runs list; `activity_log` helper; pending_interactions inline en GET /api/issues/{id}.
- [x] **Fase 6.1 - Approvals sensibles v2**: `RunExecutor` crea/bloquea por `request_confirmation` en issues `high`/`critical`; acepta ejecuta, rechaza falla con `approval_rejected`, sin arrancar adapter antes de approval.
- [x] **Fase 6.2 - Lead-first funcional**: `lead_builtin` propone equipo/backlog por `suggest_tasks`, aceptar crea agentes/issues/wakeups, roles reportan al Lead y el Lead pide confirmacion ligera de cierre.
- [x] **Fase 6.3 - Cockpit operativo v2**: primera apertura, timeline, issue thread, runs, equipo, pendientes y nueva tarea para el Lead dentro del proyecto activo.
- [x] **Fase 6.4 - Timeline backend**: `/api/timeline` ordena eventos desde SQLite y el frontend lo usa como fuente durable de cronologia.
- [x] **Fase 8.0 - Timeline observability base**: `/api/timeline` incluye `activity_log`, `cost_events` y `tool_access` ademas de issues/comments/interactions/runs.
- [x] **Fase 8.1 - Activity logging base**: issues/comments/interactions y `RunExecutor` escriben `activity_log` para que el cockpit vea acciones humanas/API e internas.
- [x] **Fase 8.2 - Activity logging control-plane**: goals, agents, wakeups, checkout y `run-once` escriben actividad durable.
- [x] **Fase 8.3 - Tool access base**: `RunExecutor` registra adapters como `tool_access` y `GET /api/tool-access` expone auditoria directa.
- [x] **Fase 7 - Skills markdown base**: promptaje por skills legibles (`lead`, `engineer`, `reviewer`, `qa`, `quorum_senior`) e inyeccion por env en `RunExecutor`.
- [x] **Fase 6.5 - Liveness Lead/no-op**: wake manual sin trabajo pendiente queda como `skipped/no_pending_lead_work`; proyectos sucios con cierre aceptado pero padre abierto se reconcilian a `done`.
- [x] **Fase 6.6 - Cockpit runs recientes**: timeline descendente, banda de ultima run y etiquetas humanas para distinguir progreso real de no-op.
- [x] **Fase 7.1 - Skills desde rescate**: convertir taxonomia de delegacion, quorum y planificacion detallada en skills markdown.
- [x] **Fase 7.2 - Plan documents v2**: `issue_documents` + revisiones para que el plan del Lead viva como artefacto estable, con conflicto `base_revision_id` estilo Paperclip.
- [x] **Fase 8 - SQLite logs**: mover events/cost/audit/tool access a tablas.
- [x] **Fase 8.1 - Tools/MCP v2**: `aiteam/tools/catalog.py` con catálogo canónico de capacidades, `capabilities_json` por agente, gate de capacidades en el executor, `GET /api/tools/catalog`, org chart en el equipo, chips de capacidades en el form de agente.
- [x] **Fase 8.2 - Hiring v2**: perfiles `solo_lead`/`lead_quorum`/`full_team` dinámicos desde `run_profiles.py`, selector de perfil en nueva tarea, panel de hiring editable con adapters por miembro, `resolution_data` desde el frontend al Lead.
- [x] **Fase 8.3 - Config de usuario para adapters**: perfiles locales de usuario, vault DPAPI para API keys, refs `secret:provider:name`, CLI status en cockpit, perfiles Codex/Gemini/Claude, y modelos locales Qwen/Gemma via Codex OSS.
- [x] **Fase 8.4 - Adapters por proyecto + borrado seguro**: crear proyecto exige al menos un perfil de adapter, `.aiteam/project_config.json` limita hirings a esos perfiles, seniors reciben modelos avanzados, workers baratos/locales cuando hay, y el cockpit permite borrar proyecto con confirmacion `DELETE` y vuelta a primera apertura.
- [x] **Fase 8.5 - Paperclip como guia viva**: `docs/PAPERCLIP_GUIDE.md` documenta los patrones consultados en Paperclip y como adaptarlos sin perder Lead-first, hiring dinamico y delegacion economica.
- [x] **Fase 8.6 - Onboarding de conexiones**: la creacion de proyecto muestra adapters conectados, permite conectar mas via API key o login de suscripcion, y el login Windows usa launcher `.cmd` para evitar quoting roto en `WindowsApps`.

## Objetivo funcional

AI Teams debe parecerse a Paperclip en robustez operativa:

- una tabla por concepto;
- cola durable en DB;
- runs como entidad central;
- checkout atomico;
- wake reasons explicitos;
- recovery/liveness auditable.

Pero debe conservar identidad propia:

- equipos de programacion, no empresas genericas;
- Lead-first y hiring dinamico;
- entrada de tarea estilo Paperclip: el usuario propone una tarea para un proyecto y el Lead la convierte en issues/runs/wakeups hasta conseguirla o pedir decision humana;
- perfiles `solo_lead`, `lead_quorum`, `full_team`;
- planificacion detallada obligatoria por flujo: objetivo, sub-issues, delegaciones, riesgos, posibles roturas para la siguiente run y criterios de revision;
- accountability por rol: quien ejecuta, a quien reporta, que evidencia entrega, quien revisa y quien acepta/rechaza;
- bajo ruido operativo: gates proporcionales al riesgo, checks con utilidad clara y evitando approvals/quorum/reviews pesados para trabajo simple;
- ahorro de tokens por delegacion economica;
- seniors/quorum para planificacion y supervision;
- workers baratos para tareas simples, lectura, investigacion, compresion y herramientas sencillas;
- suscripciones y APIs como canales independientes.

## Tests vivos

La suite activa debe proteger el nuevo control plane, no el sistema viejo.

Mantener como base:

- `tests/test_migration_paperclip.py`
- `tests/test_run_profile_model.py`
- `tests/test_issue_checkout.py`
- `tests/test_runs_db.py`
- `tests/test_wakeups_db.py`
- `tests/test_control_plane_api.py`
- `tests/test_heartbeat_scheduler.py`
- `tests/test_taskboard.py`
- `tests/test_run_executor.py`
- `tests/test_comments.py`
- `tests/test_skills.py`

## Riesgos

- [x] `api/main.py` y `aiteam/orchestrator.py` ya no contienen el flujo legacy activo; la poda de modulos no activos dejo la fuente viva centrada en control plane v2.
- [ ] Quedan restos temporales bloqueados por Windows fuera de Git; borrar tras reinicio si molestan.
- [ ] `TaskBoard` y `sqlite_store` siguen como puente minimo hasta que `issues` sea fuente primaria completa.
- [ ] Windows puede dejar temporales bloqueados hasta reinicio.
