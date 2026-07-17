# Estado actual y siguientes pasos

Fecha: `2026-07-17`

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
- [x] **Fase 5a.2 - TaskBoard retirado**: tras confirmar cero consumidores en runtime/API/scripts, se eliminaron el shim y su suite exclusiva; `issues` + checkout v2 son el camino activo.
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
- [x] **Canario e2e honesto**: un único `HeartbeatLoop.run_once()` demuestra Lead denegado antes del test runner, comentario correctivo, evidencia exit 0 y cierre recuperado; checks e información están separados.
- [x] **Conocimiento de orquestación compartido**: `docs/ORCHESTRATION.md` + `docs/ORCHESTRATION_SOURCES.md` son fuente canónica; Claude/Codex usan adaptadores finos.
- [x] **Primer benchmark vs Codex solo**: `cli_conversor` pasa 9/9 en ambos brazos; `full_team` usa 3,31× input tokens y 3,82× tiempo en la semilla exploratoria. Harness v2 limita una run por iteración.
- [x] **Evals SQL del orquestador**: `scripts/orchestrator_evals.py` mide economía, wakeups, rework, contradicción reviewer/test, dieta de contexto y liveness sin ejecutar LLMs.
- [x] **Antiloop de verificación**: un Test Runner fallido no puede reejecutarse contra el mismo digest; el Lead recibe una corrección durable y debe delegar el arreglo antes de repetir tests.
- [x] **Gobernanza efectiva de `solo_lead`**: un único `role:lead` todopoderoso planifica, edita, verifica y cierra directamente; cualquier creación de sub-issues se rechaza en código.
- [x] **Serie mínima limpia de `solo_lead`**: semillas 3, 4 y 5 pasan 9/9 tests ocultos; la tercera confirma que calidad de artefacto y convergencia del control plane deben medirse por separado.
- [x] **Corregir el contrato causante del deadlock de `solo_lead`**: retirado el falso manager + `lead_executor`; el Lead conserva escritura y cierre directo, recibe una skill no jerárquica y verifica mecánicamente antes de cerrar, sin esperar roles inexistentes.
- [x] **Canario `solo_lead` sin tokens**: `scripts/e2e_solo_lead_canary.py` prueba un único agente con escritura, cierre terminal, cero hijos, rechazo de delegación y cola vacía.
- [x] **Validación real `solo_lead` v3**: la semilla 7 produce los tres entregables y pasa 9/9 tests ocultos como un único agente; el gate corregido reutiliza pytest determinista y recupera la raíz a `done` sin crear Test Runner.
- [x] **Validación limpia `solo_lead` v3**: semilla 8 termina `done` en una sola run, pasa 9/9 tests ocultos, registra verificación mecánica, no crea hijos ni deja wakeups activos. Los proyectos nuevos del perfil contienen únicamente `role:lead`.

## Siguiente bloque activo

- [x] **P0 - Máquina de estados terminal del quorum**: `accepted`, `degraded` y `failed` son absorbentes; contribuciones tardías se rechazan, la reevaluación terminal no reactiva el gate y repetir la misma aceptación es idempotente. Cubierto por tests dirigidos.
- [x] **P1 - Auditoría de liveness del quorum**: `scripts/audit_project_db.py` observa sesiones y contribuciones mediante rutas vivas (auditor/run/wakeup/síntesis/interacción) y detecta `ready` sin continuación, `degraded` sin escalado, `accepted` con issue abierta y provenance incompleta.
- [x] **P1 - Coste quorum e2e determinista**: una run de auditor atraviesa scheduler + `RunExecutor`, registra contribución y deja `cost_event` enlazado con provider/model/channel y tokens de suscripción aunque el coste sea cero. La validación con CLIs/proveedores externos sigue bajo telemetría comparable.
- [x] **P1 - Consolidación segura del bloque actual**: bloque separado en tres commits coherentes sobre `codex/orchestration-hardening` (runtime/quorum, benchmarks/evals y documentación/skills); `.claude/skills/aiteams-frontend/` quedó fuera por origen no atribuido.
- [x] **P2 - Limpieza y actualización documental**: auditadas fuentes vivas, skills y prompts; `AITEAM_AUTO_QUORUM` solo queda como advertencia o archivo legacy, no hay enlaces Markdown locales rotos, cifras/estados activos se reconciliaron y `legacy_rescue/` permanece no normativo.
- [x] **Selector proporcional backend v1**: `POST /api/issues` decide únicamente `solo_lead` frente a `full_team` con criticidad, ambigüedad, verificación independiente, ramas y reversibilidad; override explícito prevalece y `lead_quorum` nunca se autoselecciona.
- [x] **Matriz de calibración determinista v2**: 28 casos frontera etiquetados en siete familias, 28/28 correctos, cero falsos `solo_lead` y cero sobreuso en `scripts/profile_selector_evals.py`; valida política, no rendimiento LLM.
- [x] **Primer caso empírico medio/alto**: `sqlite_job_queue` confirma la selección conservadora de `full_team`: equipo 10/10 frente a `solo_lead` 9/10, a cambio de 1,84× tokens y 1,73× tiempo. La run de equipo descubrió y motivó corregir el autocierre omitido de `test_designer`.
- [x] **Juez oculto resistente a shadowing**: harness v3 importa pytest en modo aislado antes de exponer el workspace; un `pytest.py` candidato ya no puede sustituir el runner ni producir falsos verdes.
- [ ] **Calibrar selector con más benchmarks reales**: añadir casos medios reversibles y complejos de otra naturaleza, además de nuevas semillas, para medir varianza antes de relajar el default conservador o conectarlo a más superficies.
- [x] **Harness específico de planificación quorum**: compara Plan A/Plan B con rúbrica oculta determinista, hard gates, diversidad, provenance, tokens, coste y latencia; incluye casos de datos, seguridad y failover y diagnostica runs incompletas.
- [ ] **Calibración real del benchmark quorum**: ejecutar al menos tres semillas por familia con dos proveedores operativos y comparar distribución A/B; la primera run real llegó a Plan A (91,3 %) pero no a Plan B porque Gemini CLI no está instalado.
- [ ] **Telemetría CLI comparable**: verificar `usage` real de Gemini/Claude subscription antes de comparar economía entre proveedores.
- [ ] **Contexto causal acotado**: activar resumen durable cuando el hilo exceda presupuesto y medir pérdida de decisiones relevantes.
- [ ] **Reducir concentración de `RunExecutor`**: 7.059 líneas; extraer solo políticas tocadas por una necesidad funcional. La siguiente frontera justificada es encapsular evaluación/transiciones/continuaciones de quorum si esa superficie vuelve a crecer.
- [ ] **Integración de evals en health**: exponer stranded roots, salud quorum y economía resumida cuando se toque `loop-health`.
- [x] **Contrato durable Lead + Quorum**: `lead_quorum` es solo planificación multicultural; exige dos aportes válidos y provider-diversos, registra síntesis/disposiciones y termina con `accepted_plan`, sin ejecutar código.
- [x] **Canario quorum sin tokens**: `scripts/e2e_quorum_canary.py` protege plan A → auditores → gate → plan B → transición y liveness sobre SQLite.
- [x] **Integración quorum en runtime**: aceptación multicanal crea sesión/issues sin replay LLM; reports generan contribuciones; `quorum_ready` inyecta findings; el Lead emite plan B + disposiciones y el executor cierra la planificación. Degradaciones y síntesis inválidas escalan con cap.
- [x] **Activación backend de perfiles al crear proyecto**: `POST /api/projects/new` valida el perfil canónico, lo persiste en goal/issue/wakeup y aprovisiona los dos auditores cuando se solicita `lead_quorum`; el benchmark usa este mismo bootstrap sin parche SQL.
- [x] **Retirada de quorum legacy**: eliminado `aiteam/quorum.py` tras confirmar cero consumidores; la activación automática por entorno y los prompts encadenados quedan sustituidos por sesiones, reports, gates y continuación durable SQLite.

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
- `tests/test_run_executor.py`
- `tests/test_comments.py`
- `tests/test_skills.py`

## Riesgos

- [x] `api/main.py` y `aiteam/orchestrator.py` ya no contienen el flujo legacy activo; la poda de modulos no activos dejo la fuente viva centrada en control plane v2.
- [ ] Quedan restos temporales bloqueados por Windows fuera de Git; borrar tras reinicio si molestan.
- [x] `TaskBoard` retirado tras confirmar cero consumidores activos. `sqlite_store` permanece únicamente donde soporta fixtures/migración legacy explícita.
- [ ] Windows puede dejar temporales bloqueados hasta reinicio.
