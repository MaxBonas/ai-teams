# Estado actual y siguientes pasos

Fecha: `2026-07-17`

Plan rector activo: `docs/MIGRATION_PAPERCLIP.md`.

- [x] Generalizar la autoridad del quorum: Lead configurable por el usuario al
  crear el proyecto o desde Equipo; ningún proveedor queda codificado como Lead
  y Codex puede participar como senior auditor.
- [x] Congelar de forma efectiva el objetivo quorum: un prompt posterior debe
  crear Nueva tarea; Chat no puede mutar una sesión existente. Persistencia
  valida ownership Lead y que Plan B nazca en la misma run de síntesis.

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
- [x] **P1 - Higiene del workspace de desarrollo**: retirados 11,1 GB de temporales pytest y runtime JSON/JSONL legacy; los wrappers aíslan cada sesión, limpian desde un proceso posterior al cierre de SQLite, desactivan cache/bytecode y dejan cero carpetas temporales tras la suite.
- [x] **Selector proporcional backend v1**: `POST /api/issues` decide únicamente `solo_lead` frente a `full_team` con criticidad, ambigüedad, verificación independiente, ramas y reversibilidad; override explícito prevalece y `lead_quorum` nunca se autoselecciona.
- [x] **Matriz de calibración determinista v4**: 30 casos frontera etiquetados en siete familias, 30/30 correctos, cero falsos `solo_lead` y cero sobreuso respecto a las etiquetas vigentes en `scripts/profile_selector_evals.py`; valida consistencia de política, no que sus etiquetas maximicen rendimiento LLM.
- [x] **Primer caso empírico medio/alto**: `sqlite_job_queue` confirma la selección conservadora de `full_team`: equipo 10/10 frente a `solo_lead` 9/10, a cambio de 1,84× tokens y 1,73× tiempo. La run de equipo descubrió y motivó corregir el autocierre omitido de `test_designer`.
- [x] **Juez oculto resistente a shadowing**: harness v3 importa pytest en modo aislado antes de exponer el workspace; un `pytest.py` candidato ya no puede sustituir el runner ni producir falsos verdes.
- [ ] **Calibrar selector con más benchmarks reales**: añadir casos medios reversibles y complejos de otra naturaleza, además de nuevas semillas, para medir varianza antes de relajar el default conservador o conectarlo a más superficies.
- [x] **Segunda familia empírica reversible**: `config_redactor` pasa 3/3 en ambos brazos; `solo_lead` consumió 4,68× input tokens y 5,17× tiempo frente a Codex directo. La run descubrió y corrigió colección pytest sobre temporales CLI bloqueados.
- [x] **Tercera familia empírica reversible**: `release_notes_indexer` pasa 7/7 con `solo_lead`, `full_team` y Codex directo. `solo_lead` cerró en 215,4 s/1 run/417.104 tokens de entrada; Codex directo en 243,9 s/697.158; `full_team` alcanzó la misma calidad pero agotó 15 min en estado `in_progress`, con 7 runs y 1.811.217 tokens. El juez de Ruff dejó de contar el mensaje de éxito como una incidencia.
- [x] **Segunda familia empírica compleja**: `deployment_wave_planner` pasa 16/16 en los tres brazos. `solo_lead` cerró en 130,4 s/1 run/184.370 tokens; Codex directo en 317,8 s/1.037.305; `full_team` detectó y corrigió manejo de salida CLI, pero quedó `in_progress` tras 821,9 s/12 runs/1.497.923 tokens por faltar el recibo final de Test Runner. Es evidencia negativa contra usar verificación independiente como disparador suficiente cuando la tarea sigue siendo reversible y un solo artefacto.
- [x] **Primera familia empírica de seguridad**: `tenant_authorizer` obtuvo 2/5 con `full_team` frente a 4/5 con Codex directo; equipo usó 0,76× input tokens pero no justificó su coordinación. Se conserva como evidencia negativa, no como victoria del selector.
- [x] **Harness específico de planificación quorum**: compara Plan A/Plan B con rúbrica oculta determinista, hard gates, diversidad, provenance, tokens, coste y latencia; incluye casos de datos, seguridad y failover y diagnostica runs incompletas.
- [ ] **Calibración real del benchmark quorum**: `provider_failover` tiene tres semillas aceptadas con Codex + Anthropic (+4,35, −8,70 y +8,70; media +1,45). `sqlite_online_migration` suma una sesión aceptada Codex + Antigravity Flash con regresión −8,69. `multitenant_authorization` aporta una primera sesión degradada diagnóstica y dos aceptadas tras el fix de adapters: con la rúbrica v2 puntúan 91,30→100 (+8,70) y 100→100 (0), media +4,35. La tercera cerró en cuatro runs, dos aportes válidos al primer intento, 258,5 s y 27 céntimos API. La v1 marcaba falsamente −8,69 por no reconocer «frontera de enforcement», `policy checks por recurso` y formulaciones españolas; se conservan ambos resultados para trazabilidad. La varianza y el efecto techo siguen altos; faltan más semillas de seguridad y datos antes de concluir.
- [x] **Equipo - contratación canónica de quorum**: las tarjetas Quorum Auditor 1/2 usan `POST /api/agents/quorum/reconcile`, crean IDs canónicos provider-diversos de forma idempotente y desaparecen de “Disponibles” al existir; no pasan por el hiring conversacional `full_team`.
- [x] **Canales Google actualizados**: Gemini API conserva su API key como canal independiente; Gemini CLI legacy fue desinstalado y retirado. `antigravity_subscription` usa `agy` 1.1.4: auth, payload largo, permisos headless, normalización de envelopes y contribución quorum real están verificados. Hiring enruta `quorum_auditor` a Gemini 3.1 Pro High; `agy --print` aún no expone usage comparable.
- [x] **Contexto causal acotado**: contrato durable, recovery, offsets y activación dinámica están cerrados. Calibración: en `auth_migration`, Codex mini quedó 0/3, `gpt-5.5` 2/2 y Anthropic Haiku 3/3; en `queue_rollout`, Codex mini alcanzó 3/3 y senior/Antigravity 1/1 cada uno. Todas las nuevas SQLite auditadas están sanas. Anthropic recibe ahora el target completo y el harness imprime UTF-8 correctamente en Windows.
- [x] **Recovery acotado del context curator**: el primer artefacto ausente/inválido conserva la issue `in_progress`, persiste contador y diagnóstico, y encola una única run correctiva idempotente; el segundo fallo deja estado `escalated`, bloquea y despierta al Lead. La auditoría detecta tanto retries como escalados sin continuación durable.
- [x] **Comentario indivisible sobredimensionado**: los comentarios de más de 24.000 caracteres se dividen por offsets durables `[start,end)` sin truncar. Cada bloque conserva IDs, offsets y tamaño exactos; `synthesized_through_comment_id` solo avanza al consumir el último segmento y el cursor parcial se elimina entonces.
- [x] **Activación por presupuesto cómodo de contexto**: perfiles con capacidad declarada calculan `payload base + hilo no sintetizado` en tokens estimados y activan el curador antes de superar su zona cómoda, reservando salida y herramientas. Codex obtiene la ventana del `models_cache.json` local; otros canales pueden declarar `context_window_tokens`, ratio, reservas y `chars_per_token`. Perfiles sin metadata conservan el fallback seguro de 8.000 caracteres. La decisión queda persistida en issue y `activity_log`.
- [x] **Criterio de promoción del curador**: descartado el budget universal. Codex mini pierde anclas semánticas válidas sin activar recovery, por lo que nuevas contrataciones `context_curator` sobre `codex_subscription` usan `gpt-5.5`; Anthropic conserva Haiku tras 3/3 en auth. La selección sigue respetando proveedor y cambios explícitos del usuario; no reescribe agentes existentes configurados manualmente.
- [ ] **Reducir concentración de `RunExecutor`**: 7.608 líneas; extraer solo políticas tocadas por una necesidad funcional. La nueva frontera justificada es encapsular el contrato de context curator y, si vuelve a crecer, evaluación/transiciones/continuaciones de quorum.
- [x] **Integración de evals en health**: `GET /api/loop-health` expone aditivamente `orchestrator_evals` con economía, dieta de contexto, salud quorum y liveness/stranded roots usando las mismas definiciones de `scripts/orchestrator_evals.py`.
- [x] **Frontend - pedir cambios en hiring**: la Bandeja ofrece feedback obligatorio y outcome durable `changes_requested`; no aplica hiring, despierta al Lead con la nota y conserva la propuesta resuelta en el historial. La siguiente propuesta debe crear una interacción nueva.
- [x] **Frontend - validar QuorumStepper capa-2**: sesión real con Codex subscription + Ollama/Qwen local verificada en navegador. El modelo local omitió dos veces el reporte estructurado: la sesión degradó de forma durable, el Lead recibió wakeup, el stepper muestra `1/2`, gate pendiente y motivo, y `audit_project_db.py` dejó todos los invariantes en verde. Sigue faltando una run aceptada para calibrar Plan A/B.
- [x] **Frontend - lint de ThreadView**: retirada la actualización síncrona de estado invocada desde `useEffect`; carga compacta asíncrona cancelable y remount por `issueId`, sin cambiar placeholder ni comportamiento visible.
- [x] **Contrato durable Lead + Quorum**: `lead_quorum` es solo planificación multicultural; prefiere dos aportes provider-diversos y se adapta a uno si es el único senior contratado, registra síntesis/disposiciones y termina con `accepted_plan`, sin ejecutar código.
- [x] **Quorum profundo Lead-owned**: objetivo y Plan A se congelan; Plan A/Plan B tienen gate estructural de profundidad; seniors entregan `QUORUM-AUDIT` con razonamiento, justificación, recomendación y trade-offs; RBAC los limita a informar al Lead; Plan B exige rationale por finding. Dos seniors son el objetivo, pero un equipo aceptado de uno produce quorum reducido válido.
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
