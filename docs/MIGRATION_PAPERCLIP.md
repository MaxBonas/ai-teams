# Migracion Paperclip Patterns — Plan Interno

Fecha original: `2026-05-04`
Estado actualizado: migración estructural completada; documento conservado como plan rector e historial de decisiones.
Decision: AI Teams conserva SQLite y el frontend Vite/React.

> **Lectura actual (`2026-07-19`)**: el runtime activo ya ejecuta adapters mediante
> `HeartbeatLoop`/`RunExecutor`, reconcilia runs y wakeups, persiste interactions,
> reports, costes y actividad en SQLite, y dispone de cockpit v2, canario e2e y
> benchmark frente a un agente único. Los párrafos `Estado:` de cada fase son
> fotografías históricas de la migración y pueden describir pendientes ya resueltos.
> Consultar `HANDOFF.md`, `task.md`, código y tests para el estado operativo vigente.
> En particular, ninguna frase futura como «se conectará después» constituye backlog
> vigente. El backlog accionable está únicamente en las casillas abiertas de
> `task.md`; este documento conserva esas frases como historial de la transición.

Este documento reemplaza como guia de arquitectura activa al roadmap incremental anterior. La documentacion antigua fue retirada de la fuente viva; si hace falta contexto historico, usar Git.

## North Star funcional

El objetivo final es que AI Teams funcione casi como Paperclip en lo que Paperclip hace mejor: control plane durable, issues vivos, heartbeats, runs auditables, checkout atomico, interactions y recuperacion.

Pero AI Teams no debe convertirse en una "empresa autonoma" generica. Debe ser un **software team control plane**: un cockpit para formar, dirigir, supervisar y auditar equipos de programacion con modelos heterogeneos.

### Lo que se adopta de Paperclip

- Control plane basado en issues, agentes, wakeups, runs, comments e interactions.
- Lead-first: el sistema puede empezar con un Lead y crear el equipo despues de entender el proyecto.
- Heartbeats durables en DB, no rondas en memoria.
- Checkout atomico y conflictos explicitos.
- Adapters aislados por contrato uniforme.
- Skills markdown como instrucciones legibles por rol/adaptador.
- Recuperacion por liveness, watchdog y reconciliation.

### Lo que AI Teams conserva y potencia

- Orientacion a equipos de programacion, no a companias ficticias.
- Flujo de entrada estilo Paperclip: el usuario propone una nueva tarea para un proyecto, el Lead la recibe como issue/objetivo vivo y se encarga de planificar, delegar, revisar y continuar por heartbeats hasta conseguirla o pedir desbloqueo explicito.
- Perfiles de ejecucion:
  - `solo_lead`: solo responde/actua el Lead.
  - `lead_quorum`: Lead + quorum de seniors/modelos avanzados para decisiones complejas.
  - `full_team`: Lead forma equipo, delega, supervisa, review y QA.
- Directrices fuertes de rol: cada flujo debe empezar con una planificacion detallada, las delegaciones previstas, los riesgos que pueden romper esta run o la siguiente, y los puntos que deben revisarse antes de cerrar.
- Delegacion economica: el Lead y el quorum se reservan para planificacion, supervision y tareas complejas; modelos mas baratos ejecutan lectura, compresion de contexto, investigacion simple, uso de MCPs sencillos y tareas de programacion bien especificadas.
- Accountability explicita: cada agente debe saber a quien reporta, que debe entregar, que evidencias debe producir y quien revisa su resultado, siguiendo el patron de Paperclip de ownership y rendicion de cuentas por issue.
- Router hibrido por suscripciones o APIs: Claude/Codex/Gemini/local/API deben poder convivir sin obligar a un unico proveedor.
- Quality gates proporcionados: review, QA, evidencia, diffs, artefactos y trazabilidad solo cuando reducen riesgo real. Evitar ruido innecesario, ceremonias vacias y gates demasiado fuertes que bloqueen trabajo simple o bien especificado.

### Directrices de planificacion y accountability

El Lead no es solo un dispatcher. Su trabajo principal es convertir una peticion del usuario en un flujo ejecutable, observable y revisable. Antes de delegar o ejecutar debe producir estado estructurado suficiente para que el sistema pueda continuar por heartbeats aunque cambie de run.

Cada plan debe capturar:

- objetivo y criterio de cierre;
- desglose de issues/sub-issues;
- delegaciones previstas y razon de cada una;
- supuestos, dependencias y bloqueos probables;
- que puede salir mal en esta run;
- que puede quedar roto para la siguiente run si no se revisa;
- evidencias esperadas por rol;
- reviewer/supervisor responsable de aceptar o rechazar cada resultado;
- condiciones para escalar al Lead, quorum o usuario.

Cada delegacion debe dejar claro:

- quien hace el trabajo;
- a quien reporta;
- que contexto recibe;
- que no debe tocar;
- que coste/tier justifica su uso;
- que salida debe entregar;
- quien revisa y que criterios aplica.

El objetivo del sistema de heartbeats es emular el buen patron de Paperclip: el usuario no microgestiona cada paso. El usuario crea o solicita una tarea; el Lead y el control plane la mantienen viva con wakeups, runs, comments, interactions y reviews hasta terminarla, bloquearla explicitamente o pedir decision humana.

### Ruido y gates

La planificacion detallada no debe convertirse en burocracia. AI Teams debe prestar atencion a como Paperclip aplica checks, ownership e interactions: lo suficiente para que el trabajo sea durable y auditable, pero sin crear friccion artificial.

Reglas:

- cada gate debe justificar que riesgo reduce;
- tareas simples no necesitan quorum, review pesado ni cadenas largas de aprobacion;
- si una delegacion esta bien delimitada, el control debe ser ligero y verificable;
- las interactions deben usarse para desbloqueos reales, no para preguntar por defecto;
- el Lead debe eliminar ruido antes de pasarlo al usuario o a otro agente;
- los reviewers deben buscar roturas, riesgos y evidencias, no imponer formalismo por formalismo.

### Principio economico

El sistema debe medir si delegar ahorra tokens/coste sin degradar calidad. La analogia de producto es un equipo real: un Senior Team Lead no hace cada tarea junior si puede explicar, delegar, revisar y cerrar con calidad. Por tanto:

- El Lead descompone y clasifica complejidad.
- El Lead contrata/asigna agentes adecuados al proyecto.
- Las tareas simples o bien delimitadas se derivan a modelos baratos.
- Las tareas de alto riesgo vuelven al Lead/quorum/senior.
- Las runs deben registrar coste estimado, coste real, ahorro estimado, motivo de delegacion y resultado.

Esta economia no es un nice-to-have: es parte central del producto.

## Diagnostico

AITeams funciona en flujos simples, pero los flujos complejos acumulan bloqueos, retries y rechazos porque el sistema todavia se comporta como un orquestador central que empuja rondas de trabajo. Paperclip demuestra un patron mas estable: un control plane persistente donde issues, wakeups, runs, interacciones, costes y eventos son entidades durables.

Las tres causas estructurales a corregir son:

- Estado fragmentado: SQLite contiene `tasks` y `workflow_state`, pero gran parte del runtime sigue en JSONL o payload JSON opaco.
- Ejecucion acoplada: `aiteam/orchestrator.py` concentra claim, ejecucion, streaming, gates, retries, consultas y recuperacion.
- Recuperacion debil: no existe una entidad durable de run + wake reason + liveness que permita reanudar o diagnosticar de forma simple tras crash, timeout o rechazo.

## Principios

- SQLite se queda como motor. Postgres es innecesario para el modo single-tenant local.
- El frontend Vite/React se conserva. Solo se adaptan endpoints y estados.
- Migracion agresiva por fases. Cada fase debe dejar la suite verde o, si rompe demasiado, partirse, pero el target es sustituir el orquestador viejo, no convivir indefinidamente.
- Primero paralelo, luego sustitucion. Desde `2026-05-04`, los proyectos antiguos creados por AI Teams y los tests de dogfooding ya no son requisito de compatibilidad; se pueden limpiar si bloquean el control plane nuevo.
- Cero nuevos JSONL como fuente primaria. Los JSONL existentes se mantienen solo durante la transicion.
- No copiar Paperclip literalmente. Se extraen patrones: cola durable, checkout atomico, runs de primera clase, env context, interactions y adapter contract.
- Observar especialmente como Paperclip equilibra liveness, ownership y gates para mantener avance sin ruido. AI Teams debe adoptar ese equilibrio, no sobrerregular cada paso.

## Patron objetivo

### Entidades principales

- `issues`: unidad de trabajo normalizada y fuente primaria del control plane activo.
- `agents`: rol interno + adapter fijo + politica de heartbeat/budget.
- `team_blueprints`: equipo propuesto por el Lead para un proyecto/run.
- `agent_assignments`: contratacion/asignacion efectiva de agentes a issues, con razon y politica de coste.
- `runs`: cada invocacion de agente, con status, timestamps, uso, resultado, error, liveness, log refs y context snapshot.
- `wakeup_requests`: cola durable con coalescing e idempotencia.
- `issue_comments`: hilo durable de contexto.
- `issue_thread_interactions`: preguntas, confirmaciones y sugerencias que pausan el flujo sin polling.
- `run_events`, `cost_events`, `activity_log`, `tool_access`: sustitutos tabulares de los JSONL.

### Reglas de ejecucion

- Checkout atomico por `UPDATE ... WHERE ... RETURNING` o equivalente SQLite.
- Si el checkout devuelve conflicto, la API responde `409` y el agente no reintenta esa issue.
- El agente siempre despierta con contexto explicito: `AITEAM_RUN_ID`, `AITEAM_TASK_ID`, `AITEAM_WAKE_REASON`, `AITEAM_WAKE_COMMENT_ID`, `AITEAM_API_URL`.
- El Lead crea o propone sub-issues estructurados via API. El bloque textual `[WORKFLOW_PLAN]` queda como compatibilidad de transicion, no como contrato futuro.
- El Lead puede proponer el equipo: roles, adapter_type, tier/coste, responsabilidades y politica de supervision.
- Pausar para usuario se modela como `issue_thread_interactions`, no como bracket directive persistente.
- Cada delegacion debe tener `delegation_reason`, `complexity`, `cost_policy`, `supervisor_run_id` y resultado observable.
- Cada issue debe poder responder quien es owner, quien revisa, a quien reporta el agente ejecutor y que riesgo queda pendiente para la siguiente wake/run.
- Los gates deben ser proporcionales a complejidad, riesgo y coste; el default debe favorecer progreso observable con controles ligeros.

## Fases

### Fase 0 — Preparacion documental

Objetivo: fijar el plan rector y reducir ruido legacy.

- Crear este documento como fuente activa.
- Actualizar `task.md` para que el backlog apunte a la migracion.
- Actualizar `docs/INDEX.md` para que solo apunte a fuentes vivas.
- Retirar docs antiguas, archivo historico interno y prompts raiz desalineados.

Estado: completada y reforzada con limpieza agresiva el `2026-05-04`.

### Fase 1 — Schema v2 paralelo

Objetivo: introducir tablas nuevas sin cambiar comportamiento.

- Crear `aiteam/db/schema.sql`.
- Crear migrador idempotente `scripts/migrate_to_v2.py` con `--dry-run`.
- Crear backup automatico de `runtime/` antes de primera migracion real.
- Ingerir `tasks` y `workflow_state` actuales hacia `issues`/`goals` en modo paralelo.
- Incluir desde el primer schema: `agents`, `team_blueprints`, `agent_assignments`, `runs`, `wakeup_requests`, `run_events`, `cost_events`.
- Tests: creacion de schema, migracion idempotente y lectura legacy intacta.

Estado: implementada como camino paralelo en `aiteam/db/schema.sql`, `aiteam/db/migration.py` y `scripts/migrate_to_v2.py`. El dry-run contra `runtime/aiteam.db` proyecta 25 tareas legacy a 25 issues, 6 agentes, 1 blueprint y 45 dependencias sin escribir en la DB. No borrar nada en esta fase.

### Fase 1.5 — Product model: Lead-first y perfiles

Objetivo: fijar pronto el contrato funcional diferencial de AI Teams antes de implementar scheduler.

- Modelar perfiles `solo_lead`, `lead_quorum`, `full_team`.
- Modelar `team_blueprints`: el Lead propone que agentes hacen falta para este proyecto.
- Modelar "hiring" como creacion/asignacion de agentes de programacion, no como organigrama empresarial.
- Guardar por agente: rol, seniority/tier, adapter_type, coste esperado, capacidades, supervisor.
- Mantener compatibilidad con los 6 roles actuales, pero permitir equipos dinamicos por proyecto.
- Tests: un run `full_team` empieza con Lead, genera blueprint y crea/asigna agentes adaptados al objetivo.

Estado: implementado. `aiteam/run_profiles.py` define perfiles canónicos, blueprints y política de delegación; el scheduler/executor los transporta y gobierna. `POST /api/projects/new` acepta un override canónico, lo persiste en goal/issue/wakeup y aprovisiona auditores al iniciar `lead_quorum`.

### Fase 2 — Checkout atomico

Objetivo: eliminar la causa mas fragil de bloqueo en Windows.

- Implementar `aiteam/db/issues.py::checkout(...)`.
- Exponer endpoint `POST /api/issues/{id}/checkout`.
- Reescribir tests de `FileLockRegistry` hacia checkout concurrente con SQLite WAL.
- Mantener `TaskBoard.claim_task()` solo como shim temporal durante la migración. Cumplido y retirado `2026-07-16` tras confirmar cero consumidores activos.
- Eliminar `runtime/file_locks.json` y el registro de locks de archivo como mecanismo de concurrencia.

Regla: `409` no se reintenta.

Estado histórico: primitiva DB implementada en `aiteam/db/issues.py` con `UPDATE ... RETURNING`, conflicto como `None` e idempotencia para mismo agente/run. Endpoint `POST /api/issues/{id}/checkout` montado en `api/routers/control_plane.py`; conflicto HTTP `409`. `FileLockRegistry` fue retirado del camino principal y el shim `TaskBoard` quedó temporalmente conectado al checkout v2. Estado actual (`2026-07-16`): el shim fue eliminado y `issues` es el camino activo.

### Fase 3 — Runs durables

Objetivo: que cada invocacion tenga vida propia y sea auditable.

- Crear helper de `runs`: `create_run`, `mark_running`, `append_event`, `finish_run`.
- Hacer que `_run_task` cree/cierre `runs` aunque el scheduler viejo siga activo.
- Persistir `adapter`, `model`, `usage`, `error`, `session_ref`, `liveness_state`.
- Persistir `profile`, `delegation_reason`, `complexity`, `cost_policy`, `supervisor_run_id`, `estimated_cost_cents`, `actual_cost_cents`, `estimated_savings_cents`.
- UI/API pueden leer runs reales sin depender solo de `events.jsonl`.

Estado: helpers DB implementados en `aiteam/db/runs.py`: `create_run`, `mark_run_running`, `append_run_event` y `finish_run`, incluyendo contexto, uso, resultado, canal, coste y ahorro estimado. Pendiente enganchar `_run_task`/scheduler y migrar writers JSONL.

FinOps v2: `aiteam/db/finops.py` implementa `record_cost`, `check_budget`, `BudgetStatus` y periodo mensual. `RunExecutor` registra `actual_cost_cents` en `cost_events`, actualiza `agents.spent_monthly_cents` y bloquea ejecuciones si el agente ya supero `budget_monthly_cents`, creando `request_confirmation` con titulo `Budget exceeded`. El presupuesto `0` significa sin limite. Pendiente portar senales avanzadas de presion, forecast y anomalias desde `docs/legacy_rescue/` si aportan valor.

### Fase 4 — Wakeup queue paralela

Objetivo: reemplazar rondas por cola durable, sin cortar compatibilidad.

- Implementar `aiteam/heartbeat/scheduler.py`.
- `tick_timers(now)` encola wakeups por agente/politica.
- `dispatch_next()` reclama wakeup, crea run y llama adapter.
- `run_until_idle()` queda retirado; cualquier llamada legacy falla explicitamente en el stub de compatibilidad.
- Startup reconciliation escanea wakeups/runs/issue locks atascados.

Estado: primitiva DB implementada en `aiteam/db/wakeups.py`: enqueue con idempotency/coalescing, claim atomico de siguiente wakeup y cierre terminal. Endpoints basicos montados en `api/routers/control_plane.py`: crear, reclamar y cerrar wakeups. `aiteam/heartbeat/scheduler.py` ya encola timers por agente y hace dispatch durable wakeup -> run, todavia sin ejecutar adapters. El `run_until_idle()` legacy fue retirado y `aiteam/orchestrator.py` queda como stub de compatibilidad que falla de forma explicita. Pendiente reconciliation de startup y loop persistente.

### Fase 5 — Adapter contract + suscripciones/API

Objetivo: reducir el router algoritmico a un runtime contract auditable sin perder el soporte hibrido de suscripciones y APIs.

- Crear `aiteam/adapters/registry.py`.
- Cada agente tiene `adapter_type` fijo y fallback ordenado por config.
- Extraer build env, execute, parse stdout y estimate cost por adapter.
- Mantener `RoutingDecision` como compatibilidad, pero simplificar construccion.
- Preservar independencia entre subscription adapters y API adapters.
- La seleccion principal vive en el agente/equipo; el fallback vive en config ordenada, no en scoring opaco.
- Tests: un mismo rol puede correr por subscription o API segun config, y la run registra el canal usado.

Estado actual (`2026-07-22`): la política de modelos se renovó por adapter y
tier. OpenAI usa Sol/Terra/Luna; Anthropic usa Opus 4.8/Sonnet 5/Haiku 4.5;
Gemini usa Pro 3.1 Preview/Flash 3.5/Flash-Lite 3.1. Antigravity conserva los
nombres que su propio CLI enumera y los modelos locales conservan el pin del
owner. Fable 5 se ofrece solo como escalado manual hasta implementar sus gates
de retención/refusal. La presión de cuota ya se calcula por perfil desde
provenance durable: usage cuando existe, runs/duración como proxies explícitos y
errores de límite observados. Solo una capacidad configurada por el owner habilita
porcentaje y forecast; un límite opaco permanece `capacity_unknown`. El
lifecycle automático de modelos preview/retirados ya está cerrado. Equipo cruza catálogo con
inventario/health por adapter, deshabilita IDs no ejecutables, rechaza su
guardado y evita su contratación automática. Las runs completadas o
`model_unavailable` actualizan evidencia por perfil+modelo. Ante retirada, el
control plane bloquea la issue y propone un fallback ejecutable del mismo perfil
mediante interacción owner; aceptar lo aplica y reencola sin LLM, rechazar
mantiene el bloqueo y la ausencia de candidato escala al supervisor.
Antigravity 1.1.5 completa además un screening estructural de 27 runs. El A/B
conductual posterior promociona Sonnet 4.6 para Engineer tras tres semillas
9/9, mejor convergencia, Ruff y latencia que Flash High; review conserva Flash
High. El backlog de review y calibración del resto de adapters vive en `task.md`.

Estado de integración (`2026-07-22`): la disponibilidad de catálogo y la
compatibilidad modelo×rol ya son invariantes separadas. La decisión pura se
resuelve sobre el perfil/modelo efectivos y se aplica en bootstrap del Lead,
hiring, create/update, propuestas editadas, reconcile, lifecycle y dispatch.
Las configuraciones persistidas inválidas se bloquean antes de consumir modelo
con continuación owner y recibos de auditoría. Equipo consume el mismo contexto,
mantiene el catálogo visible y deshabilita perfiles/modelos con la causa que
devolvería el backend. El health API ya no se hereda del perfil: discovery
autenticado y probe estructurado del modelo exacto producen recibos separados.
Un ID catalogado permanece visible pero no `selectable`; rate-limit, retirada e
incompatibilidad conservan estados propios. Permanecen los canarios gratuitos,
el endurecimiento JSON Object/Qwen y la matriz E2E completa.

### Fase 5.5 — Delegacion economica

Objetivo: convertir el ahorro de tokens/coste en comportamiento medible, no promesa.

- Implementar clasificador de delegabilidad: simple, well_scoped, long_read, context_compression, web_research, mcp_simple, code_change, high_risk.
- Implementar politica de asignacion: cheap_worker, standard_worker, senior, quorum.
- Registrar eventos de economia: `delegation_planned`, `delegation_accepted`, `delegation_rejected`, `delegation_savings_estimated`, `delegation_savings_realized`.
- El Lead supervisa resultados baratos y escala solo si hay riesgo o baja calidad.
- La UI debe mostrar: coste del Lead/quorum, coste delegado, ahorro estimado/real y razones.
- Tests: una tarea simple se delega a modelo barato; una tarea ambigua o riesgosa queda en senior/quorum.

Economía vigente: API mide coste por token; suscripción registra coste marginal
cero pero debe medir presión de cuota; local registra cero céntimos y gobierna
health/recursos. Estas unidades no se convierten entre sí ni se agregan como si
fueran el mismo consumo.

Gobierno continuo: los catálogos de modelos son inventario mutable, no
configuración terminada. Cada cambio de CLI/provider y una cadencia periódica
deben repetir discovery, compatibilidad y canarios antes de ampliar roles o
defaults. La matriz declara quién puede ejecutar; no demuestra por sí sola
calidad, estabilidad ni economía. El owner es `AI Teams maintainer`; la
cadencia mínima es mensual más evento y
`scripts/audit_model_catalog_drift.py` deja un recibo durable con inventarios
autenticados, exclusiones explícitas y matriz hermética.

### Fase 6 — Planificacion estructurada

Objetivo: matar el parser ciego de `[WORKFLOW_PLAN]`.

- El Lead crea sub-issues via API.
- En `full_team`, el Lead tambien crea o actualiza `team_blueprint` antes de delegar.
- En `lead_quorum`, el quorum revisa plan/equipo/politica de coste antes de ejecutar.
- Si falla la validacion, recibe feedback estructurado y no cae silenciosamente a defaults.
Estado: `workflow_planner.py`, prompt profiles legacy, lead directives legacy, tool specialists, evidence gate antiguo, router/scoring viejo, JSONL ledgers, `AtomicFileWriter`, MCP/tooling legacy, memoria/mailbox y politicas de chat antiguas fueron eliminados de la fuente viva. La planificación nueva converge al contrato provider-neutral `aiteam.plan.v1+json` sobre las revisiones SQLite de `issue_documents`; `update_plan` y la API son las vías formales, y los comentarios ya no materializan estado de plan. Markdown queda únicamente como shim transitorio para documentos, builtins y adapters antiguos y se proyecta como no estructurado.

Frontend: la UI Vite queda reducida a cockpit minimo de control plane v2: health, workspace, wakeups y lookup de runs. TeamChat, routing UI legacy, MCP panels, logs JSONL, Monaco, xterm y layout IDE viejo fueron retirados para evitar depender de `/api/aiteam/*`.

Adapters: los adapters legacy REST/subscription/external y el probe de providers fueron retirados. La fuente viva conserva un `AdapterRegistry` minimo basado en `adapter_type`, `channel`, `provider`, `model` y `cost_tier`; la ejecucion real se conectara despues sobre este contrato, no sobre scoring.

Config: las plantillas legacy de router, MCP, model catalog, skills library y tool catalogs fueron retiradas. `prepare_dev_env` solo rehidrata `runtime/control_plane.json` y `runtime/agents.json` desde plantillas v2.

### Fase 7 — Interactions para usuario

Objetivo: implementar pausa/reanudacion sin polling ni bracket directives.

- `POST /api/issues/{id}/interactions`.
- `PATCH /api/interactions/{id}` resuelve y encola wakeup `interaction_resolved`.
- UI muestra preguntas/confirmaciones inline en el hilo.

Estado: `issue_thread_interactions` esta implementado en `aiteam/db/interactions.py` y `api/routers/interactions.py`. `RunExecutor` usa `request_confirmation` como approval gate para issues de `criticality` `high` o `critical`: crea una interaction idempotente antes de arrancar el adapter, deja el run en `queued`, cierra el wakeup actual como `skipped/approval_required`, ejecuta cuando la interaction esta `accepted` y falla con `approval_rejected` si esta `rejected`.

La presencia de UI no cierra por sí sola la orientación. Bandeja, elección de
perfil, explicación de coste/riesgo y transición desde un plan aceptado deben
tener E2E y métricas de comprensión/abandono antes de ampliar superficies.

### Fase 8 — Consolidar logs

La verificabilidad del control plane incluye también su proceso de entrega:
tests concurrentes no deben destruir temporales de otra sesión ni abortar por
locks stale, y cada bloque material debe quedar consolidado en Git después de
sus gates. `task.md` conserva los pendientes operativos concretos.

Objetivo: una sola fuente durable de observabilidad.

- `events.jsonl` -> `run_events`.
- `cost_ledger.jsonl` -> `cost_events`.
- `audit_trail.jsonl` -> `activity_log`.
- `tool_access.jsonl` -> `tool_access`.
- `learning_registry.jsonl` -> `learning_facts`.
- Mantener export JSONL solo como compatibilidad/backup.

### Fase 9 — Trocear orchestrator/API

Objetivo: reducir superficie de mantenimiento despues de mover estado/ejecucion.

- `aiteam/runtime/streaming.py`
- `aiteam/runtime/run_executor.py`
- `aiteam/consultation.py`
- `api/routers/{issues,runs,interactions,agents}.py`

### Fase 10 — Limpieza destructiva

Solo cuando el nuevo camino este verde:

- borrar restos de `TaskBoard` legacy cuando `issues` sea fuente primaria — completado `2026-07-16` tras confirmar cero consumidores activos
- mantener eliminado `workflow_planner.py`
- sustituir `router.py` por registry simple
- borrar JSONL como writers primarios
- reducir `orchestrator.py` y `api/main.py`

### Adapter gratuito gobernado — OpenCode Zen

El catálogo base incorpora `opencode_zen_free` sin credenciales embebidas. La
disponibilidad se deriva del inventario real `opencode models opencode` y el CLI
mantiene su propia sesión. El runtime está limitado a lectura del workspace y
no puede asignarse a Engineer: su propósito inicial es Lead/quorum, review/QA y
scouts con datos no confidenciales. Nemotron 3 Ultra se clasifica Tier 1 por
capacidad; DeepSeek V4 Flash y MiMo V2.5, Tier 2; North Mini Code, Tier 3. La
clasificación sigue siendo screening documental hasta completar canarios locales
multi-semilla. Contrato, puntuación, privacidad y descartes viven en
`MODELOS_GRATUITOS_OPENCODE.md` y el trabajo restante en `../task.md`.

El adapter aplica la misma gobernanza neutral que el resto: permisos headless
fail-closed, MCP efímero con allowlist positiva por tool y telemetría de
tokens/caché/sesión para presión de cuota aunque el coste marginal sea cero. El
CLI efímero es la ruta estable. El A/B `serve`/attached ya pasa 3×2 y un canario
del SDK 1.18.4 confirma cancelación durable `busy`→abort→`idle`, health,
recuperación en la misma sesión, borrado y teardown. Sigue sin promoción: JSON
Schema produce `StructuredOutputError` aunque el texto sea válido. Un segundo
canario suspende el proceso nativo, detecta health colgado, reinicia en el mismo
puerto y recupera el mismo ID; un MCP local supera `initialize`/`tools/list` con
allowlist exacta y teardown de ambos procesos. La matriz final completa tres
semillas y seis sesiones sin contaminación, pero los cinco modelos gratuitos
fallan JSON Schema con `StructuredOutputError` y `structured=null`. La evaluación
queda cerrada con decisión negativa: no se implementa el supervisor mientras
ese contrato falle. Esta vía nunca sustituye el sandbox para roles con escritura.

La alternativa BYOK gratuita funciona en paralelo, no como reemplazo: perfiles
separados `gemini_api_free` y `groq_api_free`, secretos del owner en vault local,
health/usage/cuota por perfil y runtime OpenAI-compatible para Groq. El free
tier nunca se fusiona con el perfil API pagado del mismo proveedor ni habilita
fallback silencioso. Nuevos agregadores solo entrarán con modelo exacto y
contrato estructurado demostrado.

La compatibilidad se gobierna por modelo además de por perfil. Provisionalmente,
Nemotron cubre Lead/arquitectura/quorum read-only; DeepSeek/MiMo, review/QA;
North Mini, scouts/curator. Gemini 3.5 Flash Free y GPT-OSS 120B se limitan a
review/QA, y Flash-Lite/Qwen/GPT-OSS 20B a scouts/curator, hasta calibración.
Zen queda excluido de cualquier rol de escritura y de Lead `solo_lead`; los
adapters API sí pueden materializar ops de archivo bajo RBAC, pero carecen de
MCP externo gobernado. Tier, escritura, MCP, criticidad y privacidad son gates
independientes. El contrato y la matriz E2E pendientes viven en P0.3 de
`../task.md`.

## Riesgos

| Riesgo | Mitigacion |
|---|---|
| Romper suite grande | fases pequenas, shims y tests dirigidos |
| Perder runtime local | backup automatico antes de migracion real |
| Frontend roto por endpoints | mantener endpoints viejos durante dos fases |
| Copiar bugs de Paperclip | adoptar patrones, no su implementacion completa |
| Confusion con docs legacy | `docs/INDEX.md` marca una sola guia activa |
| Tratar un modelo gratuito temporal como infraestructura estable | discovery/health por CLI, bloqueo si desaparece, sin fallback silencioso ni claves compartidas |

## Fuentes revisadas

- Paperclip (clonar localmente desde https://github.com/paperclip-ai/paperclip).
- `packages/db/src/schema/{issues,heartbeat_runs,agent_wakeup_requests,issue_thread_interactions}.ts`.
- `server/src/services/{heartbeat,issues,issue-thread-interactions}.ts`.
- Docs publicas de Paperclip: heartbeats, env vars, checkout `409`, session persistence.
- Issues publicas de Paperclip sobre fallos de workspace en timer wakes y loops de permisos; se usan como advertencia para no copiar sin adaptar.
