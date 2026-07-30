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
- Tier 1 es una sola banda de máxima calidad con gates de rol independientes:
  `lead_ready` para gobernar el ciclo completo y `quorum_ready` para auditoría
  crítica estructurada, potencialmente read-only. Aprobar quorum no concede
  autoridad de Lead. Catálogo, hiring y defaults deben conservar esta
  separación y mostrar cobertura/diversidad real por perspectiva y pool.
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

### Fase 0.7 — Portabilidad, distribución e integración poliglota

Objetivo: que AI Teams pueda descargarse, configurarse y verificarse en una
máquina nueva sin conocimiento tribal, y que los equipos operen repositorios de
distintos lenguajes mediante capacidades demostradas.

- La portabilidad es un contrato de producto, no una afirmación genérica:
  plataformas y toolchains se clasifican como `verified`, `preview`, `planned` o
  `unsupported`, con recibo fechado para cada promoción.
- Git y los artefactos versionados transportan código y defaults; `runtime/`,
  `venv/`, `node_modules/`, bases activas, sesiones CLI y secretos son estado
  local y nunca se trasladan como parte de la instalación.
- `machine_doctor_v1` ya publica el inventario base read-only de OS,
  arquitectura, runtimes, SQLite, puertos, permisos, señales de toolchain y
  perfiles adapter sin revelar secretos ni convertir discovery en soporte,
  autenticación o health. El diagnóstico publica estado, severidad, fuente y
  siguiente acción sin ejecutarla. Un recibo hash-bound prueba no mutación sobre
  superficies gobernadas; la remediation es otro comando y permanece plan-only.
- El bootstrap debe ser idempotente y tener frontends Windows/POSIX equivalentes;
  no instalará runtimes o CLIs globales ni dependerá de asociaciones de shell.
- `dev_lifecycle_v1` fija ya la superficie prepare/start/stop/test/migrate y
  frontends por plataforma. Locks de dependencias/bootstrap, ownership
  incremental y diez casos de recovery están implementados; POSIX permanece
  preview/planned hasta completar su aceptación independiente.
- El soporte de lenguajes se modela mediante `ecosystem_registry_v1`: doce
  descriptores versionados de
  detección, manifests, comandos permitidos, artefactos y requisitos. Reconocer
  archivos no equivale a soportar un ecosistema: se exige fixture build/test por
  OS y recepción durable.
- Doctor, Lead, hiring, wake payload, tools y gates consumen el mismo perfil de proyecto;
  una carencia se expresa como `capability_gap_v1`, con descriptor, owner,
  razón y remediación, no mediante comandos inventados.
- I.6 valida los descriptores con `ecosystem_validation_receipt_v1`: copia cada
  fixture a una ruta temporal con espacios/Unicode, ejecuta sin shell, comprueba
  timeout y artefactos y registra provenance/runtime sin rutas absolutas. Un
  pass queda elegible para revisión, pero nunca promueve soporte por sí mismo.
- El entregable se clasifica y persiste mediante `objective_classification_v1`
  (`software`, `research`, `operations`, `mixed`). Research/operations usan
  evidencia documental y no activan roles ni gates de programación. Mixed exige
  sub-issues `software` explícitos antes de habilitar toolchain o tests. El
  override del owner prevalece sobre la recomendación determinista.
- `docs/INSTALLATION_AND_INTEGRATION.md` es el onboarding canónico para personas
  y agentes de IA y separa estrictamente lo operativo hoy de los objetivos.

Estado: I.1–I.5, I.6.1 e I.6.3 cerrados el `2026-07-23`. I.5 entrega catálogo, detector
read-only, planner fail-closed y proyección común sin promover ningún ecosistema
a `supported`. I.6 tiene cuatro celdas Python/npm y una Java/Maven verdes solo
en Windows local; .NET conserva un gap correcto porque el host no tiene SDK.
Go/Rust ya tienen fixtures, pero esta máquina solo conserva gaps de runtime.
C/C++ incorpora dependencias de acción descriptor-bound para
`configure → build → test`; esta máquina conserva un gap de CMake. La workflow
de tres OS cubre nueve casos y sigue pendiente de artifacts; el
resto del catálogo conserva la validación build/test por OS. El run
independiente `30023876549` y el
recibo `windows-clean-room-f2a20ed.json` verifican Windows x86_64 para clone,
bootstrap, audit, start/stop y fixture sin instalar CLIs globales. Git checkout
y ese alcance del control plane pasan a `verified`; adapters vivos, releases,
ARM64, Linux/macOS y la matriz poliglota conservan gates separados. El
onboarding de OpenCode Zen guía la API key personal en el CLI sin copiarla a AI
Teams. I.9.1 fija React/Vite/ESLint y el gate web reproducible; I.9.2a inicia la
modularización sin big bang: catálogo, selector y quorum poseen componentes/CSS
propios, el quorum usa un hook abortable keyed por issue y no quedan excepciones
`set-state-in-effect`. I.9.2b1 separa además shells/CSS de Configuración y
Bandeja, junto a Proyecto, Autonomía, Orientación y selección de decisiones;
I.9.2b2 separa Skills, MCP y el detalle de hiring mediante props tipadas sin
mover scoring, approvals ni gates de autoridad a las vistas. I.9.2b3 cierra el
dominio: `ConfigurationWorkspace` y `useConfigurationData` aíslan credenciales,
CLIs, adapters, sistema y zona de peligro; `App.tsx` queda por debajo de 4000
líneas e `index.css` por debajo de 1800. I.9.2c separa Chat, Issues y Runs,
extrae tipos/markdown compartidos y añade un ratchet de tamaño al gate
reproducible; las hojas aisladas recuperan especificidad estricta. I.9.3 añade
Vitest/React Testing Library para estados, errores y teclado, conserva la suite
completa en Chromium escritorio y ejecuta un smoke crítico con Axe/overflow en
Chromium móvil, Firefox y WebKit. El primer móvil detectó y corrigió el timeline
de Runs desplazable pero no enfocable. El build queda protegido por presupuestos
fail-closed de JS/CSS raw y gzip. I.9 queda cerrado sin confundir esa matriz
smoke con soporte exhaustivo multinavegador.
I.8.1 introduce el contrato `release_artifact_v1`: fuente exclusivamente Git,
ZIP stored con orden/timestamp/modos estables, hashes internos/externos,
manifiesto, CycloneDX 1.6 y reporte de licencias. El generador falla cerrado
ante suciedad salvo preview explícito, symlinks, runtime, dependencias
reconstruibles, SQLite y secretos. CI sube previews y bloquea tags no
promocionables; no publica releases. I.8.2a resuelve los dos blockers: el owner
elige Apache-2.0 con copyright 2026 Max Bonas Fuertes, sin versionar su
identificador fiscal, y `uv.lock` 0.11.31 fija 58 paquetes para seis
combinaciones OS/arquitectura. Exports con hashes mantienen interoperabilidad
con `pip`; CI verifica lock y regeneración exacta. I.8.2b añade descriptores por
versión, notas, verificación completa del ZIP y upgrade/rollback side-by-side
con restauración SQLite. La publicación separa el job read-only del único job
con `contents: write`: revalida un tag anotado, crea un draft con cinco assets y
solo después lo publica. I.8.3 añade aceptación externa del ZIP con 17 gates:
bootstrap idempotente, audit/tests/start-stop, fixture, migración/backup,
restauración SQLite exacta y retirada completa. La run local detecta y corrige
la ausencia de setuptools en venv Python 3.12 y una cabecera no reproducible de
`uv export`. `v0.1.0` permanece deshabilitada hasta la aceptación independiente
y multiplataforma I.8.4.
I.10.4 amplía ese contrato sin reescribir su evidencia histórica: la aceptación
actual exige un gate 18 que verifica de forma redacted identidad, versión y
evidencia de catálogo de los CLIs de proveedor. `machine_doctor_v1` proyecta su
estado y el SHA-256 del informe, y el modo estricto bloquea promoción con
`provider_cli_version_gate_failed`. Los harnesses de release lo ejercitan con
shims efímeros dentro del fixture, sin instalar CLIs globales ni requerir login.
I.10.5 añade una aceptación de actualización separada: clone limpio y checkout
tras fast-forward deben converger al mismo hash de matriz. El recibo conserva
el preflight bloqueado de una versión antigua con binario duplicado y canarios
fail-closed de prerelease, documentación y catálogo; una ausencia opcional no
se convierte artificialmente en fallo.
I.2.1 fija el contrato
`configuration_layers_v1`, provenance, merge
conservador de defaults y actualización Windows `pull --ff-only` para
instalaciones activas sin copiar ni borrar estado local. I.2.2 añade un paquete
hasheado con perfiles/política no secreta,
preflight y aplicación explícita que invalida health hasta retest local. No
transporta DB ni assignments. I.2.3 cierra el bloque con una frontera común de
filesystem/procesos, ejecución UTF-8, teardown del árbol, resolución portable
de ejecutables y un auditor local que no promociona plataformas. El siguiente
bloque es I.3, `doctor` de máquina read-only con salida humana/JSON estable.

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

Estado histórico: los perfiles y el lifecycle durable continúan activos, pero
la materialización mediante `POST /api/projects/new` fue retirada en P0.K.8.2.
El único writer de proyectos es ahora el commit sellado del asistente.

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

Gate del informe económico (`2026-07-22`):
`scripts/audit_cost_report_readiness.py` impide construir comparativas por
entrega/proyecto antes de tener, dentro de una misma SQLite, cinco entregas
terminales por perfil y coberturas mínimas del 80 % para latencia, provenance de
coste y calidad confiable. El primer inventario audita 70 de 71 DB retenidas y
no abre el gate: ninguna contiene más de una entrega terminal del mismo perfil.
Las semillas de proyectos distintos no se agregan como si fueran una muestra
operativa. Recibo: `benchmarks/results/cost_reporting/cost-report-readiness-v1.json`.

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
La evidencia de calidad promovida vive en `aiteam.model_calibration` con
`calibrated_at`, versión y recibos por par exacto perfil+modelo+rol. El auditor
mensual bloquea promociones nuevas no registradas o stale y abre atención tras
30 días, fecha futura, versión cambiada/no observada, recibo ausente o contenido
de evidencia inconsistente con el par promovido. El snapshot Codex actual forma
parte de los gates de inventario y cobertura; health histórico no puede ocultar
un modelo retirado. No
convierte por sí solo un default sano en `manual-only`: esa transición exige
evidencia separada de catálogo, health o calidad.

El mantenimiento M.8 usa SQLite como histórico único: cada reconstrucción del
catálogo compara dimensiones versionadas de modelo, CLI, precio, cuota, prompt,
tools y contrato. Un cambio o el primer cálculo del mes añade un snapshot
append-only con métricas, tendencia, causas y hash; el mismo input dentro del
mes no duplica filas. La antigüedad abre deuda o recalibración mediante los
gates existentes, pero nunca borra por sí sola modelos o evidencia. La API
publica únicamente este resumen redacted.

P0.N extiende este mantenimiento con detección activa de cambios del proveedor.
Compara instalación CLI/MCP, pins o rangos soportados, releases oficiales,
contratos API/adapter y discovery autenticado de modelos. Persiste snapshots,
diffs y alertas deduplicadas en SQLite, clasifica novedad frente a
incompatibilidad y avisa al developer mediante actividad, interacción e inbox.
La remediación requiere aprobación y rollback: nunca actualiza herramientas,
credenciales, modelos o defaults automáticamente. Un cambio confirmado vuelve
stale únicamente el alcance afectado y reabre sus gates M.8/P0.g.
La persistencia global vive en `guided_setup.db`, no se replica por proyecto:
cinco tablas guardan snapshots, diffs, eventos, triggers y schedules. Startup
registra todo el inventario, pero sólo lectores locales allowlisted pueden
ejecutarse automáticamente; doctor es estrictamente read-only. Los triggers
exactos son intención durable para el workflow, no autorización para invalidar
evidencia ni cambiar routing. Cadencia, lease, jitter y backoff evitan bucles y
duplicados; indisponibilidad permanece `unknown`.
Los triggers se consumen mediante expedientes globales owner-gated, no mediante
issues de un workspace arbitrario. Cada expediente conserva revisión
optimista, impacto exacto, approval, aplicación externa, validación,
recalibración y outcome reversible. Su overlay de invalidaciones vuelve stale
solo perfil+modelo+rol aprobado y puede bloquear nuevas selecciones sin tocar
assignments. Aceptación o rollback restauran el overlay de forma explícita; el
control plane nunca ejecuta por sí mismo comandos de update.
La atención del maintainer se proyecta desde esos mismos expedientes mediante
un inbox global local. Configuración presenta el centro completo y Modelos un
banner compacto; acknowledge/snooze conservan revisión e historial durables.
No se crea una máquina de estados React ni se habilita entrega externa por
defecto. La frontera externa usa destinos webhook HTTPS opt-in con URL en
vault, `secret_ref` en SQLite, consentimiento, health previo, revisión
optimista, severidad mínima, urgente/digest, cooldown, outbox deduplicado y
recibos redacted. Tres fallos desactivan el destino; ningún envío muta routing,
evidencia o workflow.

P0.g convierte esa evidencia en un recorrido operativo único por
`(profile_id, model_id, role)`: configuración/auth, catálogo+versión, health,
probe de contrato, canario de rol, calibración multi-familia y promoción. El
primer gate no superado determina bloqueador, owner y próxima acción. El
tablero es derivado y fail-closed: no sustituye SQLite/read model ni ejecuta
inferencias; un estado posterior solo puede quedar `historical` o `waiting` si
un gate anterior falla. La API y Catálogo consumen la misma proyección, y
`scripts/audit_model_calibration_gate_board.py` verifica cobertura exacta,
secuencia, paridad y ausencia de bypass antes de aceptar cambios.

### Fase 5.7 — Catálogo universal y selección explicable de modelos

Objetivo: hacer del catálogo multi-proveedor una capacidad de producto y la
fuente única de ranking para creación/edición de equipos, sin reintroducir el
router multifactor opaco retirado.

- Construir una proyección provider-neutral de todos los modelos declarados,
  descubiertos, configurados o históricos, incluidos inactivos y bloqueados.
- Conservar separadas identidad del modelo, fabricante/perspectiva,
  organización proveedora, adapter profile, canal/pool y slug ejecutable.
- Derivar estados ortogonales de catálogo, configuración, health, verificación,
  compatibilidad, calibración, frescura y elegibilidad automática.
- Completar como P0 la separación Tier 1 de extremo a extremo. `lead_ready`,
  `quorum_ready` y las aptitudes Tier 1 de soporte se derivan por par exacto de
  calibraciones frescas y recibos verificables. Son hard gates independientes
  del score: ni tier, `best_for`, discovery, health verde o una nota alta
  conceden autoridad.
- Sustituir el `role_score` heurístico por `model_role_score_v2`, versionado y
  explicable, alimentado por calidad del rol, capacidad, fiabilidad, economía y
  velocidad; publicar confianza/provenance aparte y aplicar hard gates antes.
- Persistir snapshot, candidatos, breakdown, score version y razón de toda
  contratación automática. Un override del owner es estable y prevalece.
- Exponer una API global por rol y una pestaña `Modelos` con matriz visual,
  filtros, comparación, estados y drilldown de recibos/estadísticas.
- Reutilizar esa API en onboarding, Equipo, hiring, edición, Lead/quorum y
  lifecycle. Los modelos no elegibles se muestran con causa, no desaparecen.
- P0.K sustituye las vistas monolíticas de primer uso/Nuevo proyecto por
  `guided_setup_v1`, un wizard persistido y reanudable. Separa preparación de
  máquina, entrevista adaptativa, adapters, proyecto/equipo y preflight final.
  Recomienda máxima cobertura útil con consentimiento, no máxima instalación:
  runtimes locales y CLIs no elegidos siguen opcionales. Reutiliza doctor,
  discovery, health, probe y selección canónicos; conserva secretos fuera del
  flujo, adapta fixtures a objetivos no programativos y funciona también para
  reparar o ampliar instalaciones existentes.
  P0.K.1 queda implementado con `guided_setup_v1`: sesiones y pasos SQLite,
  tres scopes, dependencias, revisión optimista, drafts, bloqueo/reanudación y
  reset explícito. La DB de configuración de máquina permite reanudar antes de
  seleccionar workspace; API y schema comparten el contrato y los payloads
  prohíben valores secretos. P0.K.2 queda implementado con
  `guided_setup_needs_v1`: entrevista adaptativa determinista, borradores
  reanudables, clasificación sugerida que exige confirmación, recomendación de
  equipo/canales y sello recalculado por backend contra bypass. Local
  permanece estrictamente opt-in. P0.K.3.1 añade una proyección read-only que
  separa instalación, versión, autenticación, catálogo, health y contrato:
  ningún gate se infiere de otro y Lead requiere evidencia completa. P0.K.3.2
  la expone por API server-side y persiste solo un recibo SQLite compacto
  ligado al paso; un cliente no puede aportar evidencia verde ni completar
  `adapter_setup` sin un recibo listo. P0.K.3.3 proyecta guías manuales por
  proveedor con consentimiento, riesgo y evidencia esperada; ninguna acción
  instala, autentica, acepta términos o concede readiness por sí misma.
  P0.K.3.4 consume catálogo, health y probe como gates independientes y
  frescos; contrato estructurado requiere recibo seguro y versión de transporte
  exacta. P0.K.3.5 acepta 10 escenarios de reparación, incluida API concreta
  seleccionada por ID y reanudación sin reinstalar, y cierra P0.K.3.
  P0.K.4.1 inicia cobertura progresiva con una matriz derivada del selector
  canónico: Lead, quorum diverso y equipo base solo cuentan pares
  auto-elegibles sobre adapters preparados. P0.K.6.1–K.6.3 separan composición
  pura, observación server-side y ejecución consentida del fixture/probe.
  P0.K.6.4 completa la frontera durable: SQLite conserva receipts y artifacts
  SHA-256 por sesión, el replay exacto nunca repite ejecución y
  `/project-commit` recompone todos los inputs contra el último preflight `go`.
  Un receipt ausente/no-go/stale/corrupto bloquea antes de crear proyecto,
  agentes o wakeup. K.6.5 proyecta ese contrato mediante un panel server-driven:
  consentimiento local, remoto y cuota permanecen separados, research no
  inventa tests y “Entrar al proyecto” solo existe para el receipt durable `go`
  cuyo hash coincide con el preflight posterior. 409 invalida el preview;
  offline/429 conservan diagnóstico. Su auditor 10/10 y E2E Chromium cierran
  P0.K.6 sin inferencias ni consumo de proveedor. P0.K.7.1 añade progreso
  textual/semántico, foco de región después de cada transición y readiness
  relacionado con la acción sin robar el autofocus inicial. K.7.2 conserva la
  acción primaria en el teclado, muestra errores adyacentes y enfoca la primera
  corrección; 409/no-go regresan a Recursos sin perder el contexto. K.7.3
  retira del render/bundle el configurador legacy duplicado, valida reflow en
  768/390/320 CSS px sin overflow ni acciones ocultas, corrige contraste AA y
  demuestra reduced motion computado. La fuente legacy queda señalada para
  extirpación física en K.8. K.7.4 audita seis estados WCAG 2 A/AA con cero
  violaciones, orden de headings y landmarks explícitos; limita la live region
  al sello durable atómico, convierte el protocolo en lista y corrige un hover
  de 2,62:1. K.7.5 cierra el bloque con seis capturas SHA-256 y una secuencia
  durable pending→NO-GO→reparación→GO. El panel proyecta el preflight posterior
  y un guard compartido exige coincidencia de preflight, plan y execution
  receipt antes de mostrar o ejecutar entrada. El auditor 10/10 reabre los PNG
  y rechaza matriz, autoridad, binarios o informe manipulados. P0.K.7 queda
  cerrado; K.8 continúa con integración, actualización e higiene portable.
  RUN-024 y el inventario local del `2026-07-30` demostraron una contaminación
  histórica material de la raíz de proyectos: 2.366 carpetas numeradas contienen
  `.aiteam/aiteam.db`, de las cuales 2.029 contienen también `.git`. El hallazgo
  se atribuye a AI Teams, pero no autoriza borrado: un repositorio, archivos
  personales o trabajo no publicado pueden vivir dentro de una carpeta creada
  por el producto. K.8 se divide por ello en inventario/atribución read-only,
  prevención de nuevos siblings, remediación legacy manual, cuarentena
  reversible, UX/doctor y aceptación hermética. La arquitectura objetivo no
  necesita limpiar lo que crea: no admite daemon, tarea programada, hook de
  startup, TTL destructivo ni doctor con escritura. Clone, bootstrap, creación,
  retry, restart y actualización deben conservar un footprint exacto y
  declarado en cualquier máquina soportada. La limpieza histórica solo puede
  seguir a aprobaciones humanas sobre paths exactos y queda fuera del lifecycle
  normal del producto.
  K.8.2 cierra ya la prevención: allocator por sufijo, endpoint API, comando CLI,
  panel/estado React y fallback de tombstone fueron retirados. API/CLI no
  inicializan una carpeta elegida; el commit guiado exige parent existente y
  comprueba un footprint exacto tras publish o rollback. La aceptación portable
  completa permanece en K.8.6.
  K.8.1 cierra después el inventario read-only con un contrato portable que
  exige raíz absoluta, no sigue symlinks/reparse points, abre SQLite immutable,
  observa Git y handles sin exponer paths/credenciales y escribe el receipt
  fuera del árbol auditado. La pasada real del `2026-07-30` clasificó 2.716
  carpetas: 2.359 candidatas legacy de seis familias conocidas, 342 a
  preservar/migrar y 15 personales protegidas. Cero acciones quedaron
  autorizadas o ejecutadas. K.8.3 será un dry-run separado, manual e inmutable;
  no puede convertir estas cifras agregadas en targets.
  K.8.3 materializa después un dry-run separado: reaudita el estado vivo, no
  acepta globs/prefijos/raíces, genera solo paths hijos directos exactos y
  deniega personal, ambiguo, activo, referenciado, Git con trabajo/remoto,
  DB inválida, reparse, inventario incompleto o handles abiertos. El manifiesto
  local se crea sin overwrite y sella documento y batch por separado. La pasada
  real propone 2.359 paths/766.901.650 bytes con cero operaciones; K.8.4 sigue
  no disponible hasta revisión humana explícita y deberá revalidar de nuevo.
  El motor hermético K.8.4.1 ya está implementado y probado solo en fixtures:
  exige aprobación de los dos sellos, reaudita con handles, valida filesystem y
  checksums, usa rename atómico y journal durable, y revierte cuarentena o
  restore parciales. No contiene purge ni cleanup automático. Durante su
  aceptación se corrigió el cierre no garantizado de la conexión SQLite
  read-only, que en Windows podía retener un lock de directorio. K.8.4.2, el
  batch real, permanece bloqueado hasta aprobación owner explícita.
  K.8.5 cierra la capa de guía sin abrir ese bloqueo: primer uso, Nuevo proyecto
  y Configuración comparten una tarjeta de higiene y exigen que el preview
  corresponda a la ruta visible antes de guardarla. `project_hygiene_v1`
  devuelve solo fingerprint, estado y contadores; no abre DB, no invoca Git,
  no sigue enlaces y no muta. Machine doctor incorpora la misma proyección y
  solo emite warnings con acciones no mutantes. La configuración por
  `AITEAM_PROJECTS_ROOT` cuenta como efectiva y cambiar la raíz conserva las
  demás preferencias y archivos de adapters. El protocolo humano/IA vive en
  `PROJECT_ROOT_HYGIENE.md`. K.8.6 conserva la aceptación hermética y de
  máquina soportada.
- La automatización usa un gate más estricto que la selección manual:
  `candidate_is_automation_eligible` exige `selection_score.auto_eligible` y,
  por tanto, calibración y frescura además de los demás hard gates. Defaults,
  hiring/reconcile, fallback, escalado y recovery lo comparten. Un override
  explícito del owner puede seguir escogiendo un par compatible y seleccionable,
  pero nunca lo convierte en default ni en cobertura calibrada. El fallback
  legacy por `adapter_type` aislado falla cerrado porque no identifica el par
  perfil+modelo+rol.
- La cobertura Tier 1 alcanzó 2/2 en Antigravity 1.1.6, quedó temporalmente
  1/2 por el drift a 1.1.8 y vuelve a 2/2 tras revalidar Gemini Pro High por
  separado como Lead y quorum en la versión nueva: dos familias por tres
  semillas en cada carril. No se hereda autoridad desde inventario, QA ni el
  otro carril. El agregado versionado rechaza CLI ausente o mezclado y no
  permite por sí solo cambiar defaults.
- Propagar las habilitaciones Tier 1 por el read model, snapshots/migración,
  API, pestaña Modelos, selector de Equipo, hiring, defaults, quorum, fallback,
  reconcile, dispatch, recovery y liveness. La UI muestra score/confianza y
  habilitación como conceptos distintos. Tests negativos deben impedir que
  `quorum_ready` escale a Lead o que `lead_ready` se use como auditor sin su
  calibración exacta.
  P0.h.4a queda implementado en `model_catalog_read_model_v2`: la autoridad por
  rol se deriva y se sella en cada celda; snapshots v1 permanecen auditables
  pero fallan cerrados para autoaplicación Tier 1. P0.h.4c queda también
  implementado: API y pestaña Modelos exponen filtros, badges, contratos,
  bloqueos y cobertura/diversidad desde esa misma proyección, sin inferencias
  en React. P0.h.4d aplica después el gate canónico en selección, onboarding,
  Equipo/hiring, defaults, quorum, fallback y reconcile. El executor lo
  revalida antes de toda inferencia y persiste bloqueo+interacción cuando una
  asignación legacy, stale o manipulada carece de la autoridad exacta; así
  dispatch, recovery y liveness fallan cerrados. P0.h.4e cierra la integración
  con un auditor durable de read model, API, UI, snapshots y decisiones reales:
  compara 490 celdas canónicas, 235 decisiones activas y 20 decisiones de
  snapshot, además de los casos score alto/carril incorrecto, stale, archivado
  y adapter rojo. El recibo `tier1-authority-parity-2026-07-24.json` queda
  verde y sin divergencias.
- Añadir preferencias locales del owner por identidad perfil+modelo:
  alta/normal/baja/archivada. La prioridad ordena trabajo, no altera score; un
  archivado conserva historia pero queda fuera de selección, hiring, fallback,
  defaults y recalibración hasta reactivación explícita.
  M.9.1 aporta contrato validado y persistencia local atómica/fail-closed;
  M.9.2 ya aplica el gate único en read model, selección contextual y backlog
  de mantenimiento sin reimplementar ni contaminar el scoring técnico. M.9.3
  pausa asignaciones existentes antes de inferencia y exige una interacción
  owner-confirmed revalidada para sustituirlas o reanudarlas. M.9.4 expone
  lectura/escritura local validada y controles en Modelos; Equipo mantiene las
  opciones archivadas visibles con su causa, pero no seleccionables. M.9.5
  demuestra portabilidad entre procesos y máquina limpia y cierra las fronteras
  de onboarding, Equipo, hiring, quorum, fallback y defaults, incluido el
  selector legacy de rollout `shadow`. M.9.6 aplica en la máquina del owner las
  47 preferencias exactas —6 archivadas, 13 altas y 28 bajas— sin incorporarlas
  a los defaults del repositorio. El read model queda auditado con 47
  candidatos/799 filas de rol y cero fallos; 17 roles confirman cero selección
  o default archivado y la cobertura de 124 pares no genera mantenimiento para
  las seis identidades archivadas.
  P0.b desbloquea después Sol Tier 1 con Codex CLI oficial
  `0.146.0-alpha.6` frente a caché `0.146.0`: auth y catálogo quedan verdes,
  el modelo exacto es seleccionable y sus cinco roles críticos completan
  30/30 muestras en matrices 2×3 versionadas. Los agregados no autorizan un
  default por sí solos. Terra/Reviewer se renueva después en
  `0.146.0-alpha.6`. Terra/Engineer se revalida en esa versión: pasa 9/9 gates
  ocultos de la primera familia, falla Ruff con dos incidencias y activa
  fail-fast sin ejecutar las otras cinco celdas. Su diagnóstico y el de
  Sonnet/Engineer 1.1.6 se difieren hasta cambio material y prevalecen sobre la
  evidencia histórica; los demás pares Terra y Luna conservan la evidencia
  `0.145.0` como stale.
  P0.c confirma después en Antigravity 1.1.6 que
  `gemini-3.6-flash-{high,medium,low}` ejecutan sus submits exactos. Los tres
  quedan verificados y seleccionables solo de forma manual; no se nominan ni
  puntúan automáticamente hasta completar calibración comparable.
  P0.h.2d.3 renueva después QA en las versiones vivas: Terra sobre Codex
  `0.146.0-alpha.6` y Flash High sobre Antigravity `1.1.8` completan cada uno
  6/6 muestras de dos familias y 66/66 gates. El contrato v4 corrige el campo
  causal `tenant_id` y delega la ejecución post-fix a un test runner
  determinista, sin dar al modelo autoridad fuera de su rol. El agregado v5
  liga versión y hashes de las seis muestras. QA queda 2/2 con dos
  perspectivas y pools; no cambia defaults.
  P0.h.2d.4 renueva después Test Designer en las mismas versiones vivas.
  Terra y Flash High completan cada uno dos familias por tres semillas:
  6/6 muestras, 48/48 gates y 30/30 mutantes. El juez determinista ejecuta
  baseline y mutantes fuera del proveedor; el agregado liga versión y hashes
  hasta cada muestra. El executor rechaza ahora el cierre `done` de
  `test_designer` sin `AGENT-REPORT` válido. La cobertura queda 2/2 con
  perspectivas OpenAI/Google y pools Codex/Antigravity, sin cambiar defaults.
  P0.h.2d.5 renueva después Terra/MCP Operator en Codex
  `0.146.0-alpha.6`: dos familias por tres semillas, 6/6 muestras y 72/72
  gates de recovery y gobernanza MCP real. La cobertura queda
  `single_point` 1/2, perspectiva OpenAI y pool Codex. Sol no aporta
  diversidad por compartir ambos. El segundo cupo permanece condicionado a
  un canal materialmente nuevo y ejecutable con loop MCP gobernado; no cuentan
  APIs sin tools, Antigravity sin ese transporte, OpenCode 1.18.4 incompatible,
  Ollama ausente ni los modelos LM Studio archivados.
- Separar obligatoriamente readiness de evaluación: configuración/auth →
  catálogo+versión exactos → adapter verde → probe de structured output/tools
  → canario del rol → calibración multi-familia → promoción. Un fallo anterior
  bloquea los pasos posteriores y se registra como deuda de integración, no
  como evidencia negativa de capacidad del modelo.
- Desplegar shadow → recomendación → default solo para plazas nuevas sin pin;
  no migrar agentes existentes ni cruzar adapters silenciosamente.
- Cerrar portabilidad con un gate final read-only que compare las versiones
  aceptadas en la guía de instalación, los ejecutables resueltos por adapters y
  el inventario de `machine_doctor_v1`; cualquier drift o prerelease no
  declarada bloquea la aceptación del release.

Criterio de cierre: 100 % del inventario conocido es visible o está excluido con
causa; cada ruta automática usa un candidato verde, compatible y con evidencia
fresca, y su decisión se puede reproducir desde SQLite/recibos. La fórmula,
desglose, confianza y unidades de economía son idénticos en backend, API,
catálogo visual y Equipo. El backlog ejecutable M.1–M.9 vive en `task.md`.

Estado intermedio `2026-07-22`: M.1–M.5 están implementados en shadow. La
identidad, scorer, read model, snapshots hasheados, API canónica y pestaña
`Modelos` existen. El
auditor local base proyecta 46 candidatos/124 pares sin fallos ni candidatos
automáticos; la API suma el histórico de la SQLite activa y en el smoke actual
expone 48 candidatos/12 perfiles-canal. `/api/model-catalog` filtra inventario y
estados; `/api/model-catalog/candidates` ordena por rol sin recalcular gates. El
endpoint legacy por perfil delega identidad, score y orden manteniendo su
contrato. La UI global compara proveedores/canales y pares por rol, conserva
unknown/bloqueados y abre breakdown, evidencia, receipts y hard gates sin
reimplementar el score. Crear/editar equipos y activar defaults permanecen en
M.6–M.7.

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
El backend local ya dispone de consentimiento, sesiones, eventos con allowlist,
revocación y borrado en SQLite. El contrato prohíbe texto libre, rutas, títulos,
IDs de issue/workspace y transmisión externa; su resumen declara explícitamente
que los conteos no miden adopción, claridad, satisfacción ni causalidad. Falta
observar sesiones humanas consentidas: Config ya ofrece opt-in, revocación,
borrado y resumen local, y el cockpit instrumenta los tres flujos mediante la
allowlist. El E2E Chromium verifica 9 eventos del recorrido y 3 adicionales en
dos pruebas de abandono controlado, todos sin campos extra; no infiere lectura
desde la selección ni cuenta sesiones vacías como completadas; esta
evidencia sigue siendo técnica, no una prueba de comprensión. El estudio humano
v1 queda prerregistrado antes de observar datos: ocho sesiones, dos estratos,
orden contrabalanceado, rúbrica categórica, gates y reglas de parada congelados.
El resultado se escribirá en otro recibo; no se modificarán estos umbrales tras
ver la muestra.

### Fase 8 — Consolidar logs

La verificabilidad del control plane incluye también su proceso de entrega:
tests concurrentes no deben destruir temporales de otra sesión ni abortar por
locks stale, y cada bloque material debe quedar consolidado en Git después de
sus gates. `task.md` conserva los pendientes operativos concretos.

La retención se decide por tabla y por obligación, no mediante un TTL global.
El benchmark v1 de `dispatch_candidate_decisions` mide hasta 1000 wakeups y no
supera sus thresholds prerregistrados: se conserva el log aditivo y no se
habilita poda ni cambio de `loop-health`. Debe repetirse ante cambios de schema,
índices, scheduler o límite. `activity_log`, `run_events` y orientación
consentida requieren políticas separadas para no borrar evidencia o contradecir
consentimiento/revocación.

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
capacidad; DeepSeek V4 Flash, MiMo V2.5 y Laguna S 2.1, Tier 2; North Mini Code,
Tier 3. Laguna se declara solo manual/probe-gated: el canario durable termina
0/3 frente a 1/3 de DeepSeek y no autoriza routing automático. Contrato,
puntuación, privacidad y descartes viven en
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
North Mini, scouts/curator. Gemini 3.6 Flash Free y GPT-OSS 120B se limitan a
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
