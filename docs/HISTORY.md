# Historial condensado

Este documento reúne decisiones, migraciones y planes ya cerrados. No es un
backlog: el trabajo vigente vive exclusivamente en `../task.md`.

## 2026-07-21 — Pytest concurrente seguro en Windows

- El probe de PID deja de usar `os.kill(pid, 0)` y pasa a
  `OpenProcess`/`GetExitCodeProcess`; workspace y user config quedan aislados
  por sesión, y los temporales stale bloqueados se conservan con warning sin
  abortar la suite.

## 2026-07-20 — Auto-extensión gobernada

- El runtime incorporó propuestas de skills aprendidas exclusivas del Lead,
  obligatoriamente respaldadas por evidencia y sin activación automática.
- Se fijaron cuotas de cantidad, tamaño individual y presupuesto total activo;
  Config pasó a mostrar consumo, provenance y evidencia y permite al owner
  corregir, aprobar, retirar o borrar.
- Se preservó la autoridad del usuario: sus directivas se presentan después de
  las skills locales y prevalecen explícitamente si existe contradicción.
- El catálogo MCP inicial añadió GitHub read-only, Playwright aislado y
  Filesystem confinado al workspace. Los entries usan ejecutables locales y pins
  revisados; no contienen instalación automática. Referenciar un `catalog_id`
  bloquea el contrato canónico, pero conserva owner approval, health y allowlist.

## 2026-07-19 — Consolidación documental

- Se absorbieron en este historial los diseños, migraciones y checklists
  fechados que habían quedado dispersos en la raíz de `docs/`.
- `task.md` pasó a reunir objetivo, estado, pendientes y orden de ejecución.
- Los contratos activos (`EXECUTION_SEMANTICS.md`, `ORCHESTRATION.md`, guía
  Paperclip y registro de problemas) permanecen separados porque gobiernan
  comportamiento o conservan evidencia operativa viva.

### Primera familia frontend del selector

- `accessible_checkout_form` añadió una familia multisuperficie con rúbrica
  oculta de validación, semántica, accesibilidad y responsive CSS.
- En dos semillas, `solo_lead` y `full_team` alcanzaron siempre 10/10. Solo cerró
  2/2 en una run (122,7–147,8 s; 143.026–166.639 tokens de entrada); equipo
  cerró 1/2 en 10–12 runs (629,1–826,5 s; 787.619–1.259.330 tokens). Equipo
  promedió 5,38× el tiempo y 6,61× la entrada. Codex directo obtuvo 10/10 en su
  primera semilla con 374,8 s y 699.317 tokens.
- Equipo conservó un fix hijo y wakeup queued, por lo que su liveness era sano
  aunque no convergió. El hallazgo destapó y corrigió un falso positivo de
  `orchestrator_evals`: ahora una continuación en cualquier descendiente mantiene
  viva la raíz mediante recorrido recursivo.
- La primera versión de la rúbrica exigía `createElement('a')`; se corrigió para
  aceptar cualquier link observable válido en el resumen de errores. El juez no
  debe imponer una técnica interna cuando el contrato solo exige comportamiento.

### Familia media reversible de datos

- `inventory_snapshot_diff` añadió validación estricta, diff semántico,
  inmutabilidad, orden estable, cantidad neta y CLI atómica; la matriz del
  selector pasó a 31/31 respecto a sus etiquetas.
- En dos semillas ambos perfiles obtuvieron 20/20. `solo_lead` cerró 2/2 en una
  run; `full_team` cerró 0/2 dentro de 12 runs, promedió 2,92× tiempo y 1,91×
  entrada y dejó un F401 en la segunda. Ambas raíces abiertas conservaron
  continuación durable y liveness sano.
- Codex directo logró 20/20 con menos tiempo/contexto en su primera semilla.
  La decisión resultante fue mantener el selector conservador: esta clase de
  trabajo sigue en `solo_lead`.
- La rúbrica v1 contenía un error aritmético en `quantity_delta` y confundía
  UTF-8 con caracteres no escapados; se corrigió antes de comparar perfiles.

## 2026-07-06 a 2026-07-18 — Endurecimiento y calibración

### Control plane y cockpit

- El frontend sustituyó el fan-out de polling por `GET /api/project/state` y
  eliminó la principal deuda de requests observada en la auditoría del 6 de julio.
- Se corrigieron estados visuales, rutas Windows y avisos de desviación de coste.
- Criterios de aceptación estructurados, `test_runner`, receipts Git y reports
  con provenance pasaron a formar parte del cierre verificable.
- Wakeups coalescen por agente e issue; autonomía, cost breaker y escalados son
  durables e idempotentes.

### Economía de hiring

- Se unificaron precios y estimaciones ex ante, se registran coste real, ahorro
  estimado y desviaciones de política, y el cockpit expone coste por rol/canal.
- Workers baratos o locales son preferidos cuando existen; el sistema avisa si
  un worker cae en un canal premium.
- El cost breaker pausa subárboles que gastan sin avance de workspace.
- El informe comparativo de coste por entrega quedó como mejora posterior y se
  conserva, si sigue aportando valor, en `task.md`.

### Enforcement de roles

- RBAC bloquea operaciones fuera del rol y registra `role.op_denied`.
- Roles no editores no pueden materializar cambios ni por CLI ni mediante ops API.
- Los bloques `AGENT-REPORT` se validan y persisten como artefactos con provenance.
- Las transiciones de estado se restringen por ownership/rol y el cierre crítico
  fuerza revisión independiente o cross-provider cuando corresponde.
- Las políticas convergieron en `aiteam/policies.py`; no se adoptaron swarms ni
  frameworks externos porque romperían el modelo Lead-first y DB-céntrico.

### Auto-extensión

- Se implementaron skills por proyecto, composición con la skill base, registry
  `.aiteam/extensions.json`, propuesta exclusiva del Lead y approval/rejection
  durable con auditoría `extension.*`.
- Quedaron para fases posteriores: ejecución MCP real, health y grants por rol;
  detección de necesidades; catálogo curado y retiro automático. Estos pendientes
  se trasladaron al único backlog vigente.

### Perfiles y benchmarks

- `solo_lead` quedó gobernado como un solo agente con escritura, verificación y
  cierre directo; se retiró el diseño intermedio `lead_executor`.
- `lead_quorum` quedó limitado a planificación multicultural Lead-owned, con
  Plan A congelado, aportes auditables, Plan B y estados terminales durables.
- La matriz determinista del selector alcanzó inicialmente 30/30 y después
  31/31 al incorporar la familia de inventario.
- Benchmarks reales demostraron que más agentes no implican más calidad:
  `sqlite_job_queue` favoreció levemente al equipo; `config_redactor` y
  `release_notes_indexer` empataron; `deployment_wave_planner` empató en calidad
  con mucha más latencia/entrada para el equipo; `tenant_authorizer` favoreció
  al agente directo. Por eso el selector conserva un default conservador.
- El quorum cross-provider quedó operativo con Anthropic y Antigravity, pero la
  muestra mantiene varianza alta y algunos resultados degradados por cuota o por
  incumplimiento del reporte estructurado.
- La calibración de julio de 2026 fijó un criterio reproducible: tres sesiones
  aceptadas por familia, dos proveedores válidos, provenance completa y
  mediana+rango, separando degradaciones de los deltas A/B. Failover quedó en
  `+6,52` de mediana con rango `-8,70..+8,70`; autorización multi-tenant v2 en
  `+8,69` con rango `0..+8,70`, pero solo 2/3 Plan B pasaron el hard gate. Se
  conservaron los thresholds y se documentó que aceptación durable no implica
  calidad semántica externa.
- El cockpit pasó a consumir `orchestrator_evals` desde `loop-health`: raíces
  stranded, ejecución pendiente y quorum inconsistente elevan atención y
  enlazan con la superficie operativa correspondiente, sin duplicar el cálculo
  offline ni crear un dashboard de métricas pasivas.
- La primera vertical MCP gobernada quedó neutral al proveedor: aprobación con
  versión, handshake stdio, grants por rol/capability y recibos `tool_access`.
  Codex y Claude traducen el mismo descriptor a configuración efímera; un
  adapter sin transporte aislado registra deny sin perder la autoridad Lead ni
  provocar fallback silencioso. Fuentes shell/auto-install y versiones no
  coincidentes fallan cerradas.
- La seguridad MCP añadió inventario paginado de tools y autorización
  fail-closed: `readOnlyHint=true` es obligatorio para roles sin `repo_write`,
  Codex recibe allowlists, Claude rechaza servidores mixtos cuando no puede
  expresar el mismo subconjunto y `tool_access` conserva una decisión por tool.
  Reaprobar el mismo contrato+versión preserva health; cambiarlo lo invalida.
- La auditoría independiente posterior cerró P0.1: actividad reciente dejó
  de contarse como zombi; MCP pasó de confiar en `readOnlyHint` a una allowlist
  positiva clasificada por el owner, health con TTL y sello del ejecutable,
  argumentos y scripts. Claude deniega el servidor mientras no pueda imponer
  la misma allowlist. El supuesto hueco de identidad del Lead fue refutado y
  quedó protegido por un test negativo.
- El ciclo MCP pasó a ser operable desde Config: health, allowlist del owner,
  retiro y reactivación. El heartbeat limita probes periódicos a uno por tick,
  aplica backoff y retira después de tres fallos; contratos rechazados o ya
  existentes suprimen nuevas propuestas equivalentes con evidencia durable.
- La detección MCP quedó separada de la instalación: reports durables con
  `capability_gap`, items no verificables o límites repetidos sugieren al Lead
  investigar una capacidad. El reconciler agrupa por raíz+señal, preserva
  wakeups existentes y nunca atraviesa automáticamente el gate del owner.

### Context curator

- Evolucionó de bloques por caracteres a dieta de contexto durable con offsets,
  payload delta, recovery acotado y activación según ventana cómoda del modelo.
- La calibración mostró que compresión válida no garantiza retención semántica:
  Codex mini perdió anclas en `auth_migration`; `gpt-5.5` y Anthropic Haiku
  mostraron mejores resultados en esa familia, sin justificar una política
  universal para todos los proveedores o tareas.

## 2026-05-12 — Arquitectura de tiers, routing y compresión de contexto

### Fase 1 — Tier discipline

- Skills actualizados con fronteras explícitas (Tier 2: engineer, reviewer; Tier 3: file_scout, web_scout, context_curator, test_runner).
- `filter_forbidden_ops_for_role()` en `work_contract.py` + filtrado en executor al aplicar acciones.
- Reviewer skill: patrón `result: blocked + blocker: needs_scouting_for_<topic>`.

### Fase 2 — Eliminación de QA Tier 2 + test_runner Tier 3

- `skills/qa.md` eliminado. Reviewer absorbe QA estático.
- Nuevo `skills/test_runner.md`: ejecuta comandos, reporta stdout/exitcode, sin decisiones.
- `requires_qa_gate` marcado como deprecated (siempre False, mantenido para compatibilidad API).
- `test_runner` añadido a `_WORKSPACE_READER_ROLES` y `_TIER3_ROLES`.
- Las bases legacy que conservaban agentes `qa` se migraban reasignándolos a
  reviewer o eliminándolos si estaban huérfanos. `requires_qa_gate` quedó
  deprecado durante la transición.

### Fase 3 — Action routing (Lead-as-Evaluator)

- Nuevo módulo `aiteam/action_routing.py`: `route_action(criticality, complexity, action_type) → Routing`.
- `criticality=critical` siempre → LEAD_SELF. `test_exec/scout_*` siempre → TIER_3.
- Integrado en `_create_delegated_issue`: override automático del rol propuesto por el LLM.
- Log de actividad `action.routed` por cada decisión.

### Fase 4 — lead_executor

- Nuevo rol Tier 1: `lead_executor` (seniority=senior, hereda adapter del Lead).
- `skills/lead_executor.md`: ejecución de acciones críticas/complejas por `action_type`.
- `_ensure_role_agent` crea el agente con los atributos correctos.
- `pick_role_for_routing(LEAD_SELF, action_type)` → `lead_executor`.

### Fase 5 — Context curator basado en bloques de chars

- Umbral cambia de 8 comentarios → 8 000 chars no sintetizados.
- `append_summary_block()` y `get_context_summary()` en `documents.py`.
- `_maybe_spawn_context_curator` reescrito: calcula chars sin sintetizar desde `synthesized_through_comment_id`; curator done no bloquea re-spawn.
- Wake payload inyecta `context_summary.blocks` y filtra comentarios anteriores al punto sintetizado.
- API: `POST /api/issues/{id}/context-summary/blocks` (validación ≤ 30% ratio) + `GET /api/issues/{id}/context-summary`.
- `skills/context_curator.md` reescrito para modelo de bloques.

### Fase 6 — Thread compact/full view

- API: `GET /api/issues/{id}/thread?view=compact|full`.
- UI: componente `ThreadView` en `ide-frontend/src/components/ThreadView/`.
  - Compact: bloques de síntesis colapsables + comentarios recientes.
  - Full: modal con todos los comentarios vía `?view=full`.
- App.tsx wired to `<ThreadView issueId={...} preloadedComments={...} />`.

**Tests finales: 567 (baseline 534 al empezar).**

El scoring de acciones y `lead_executor` pertenecen a esta etapa histórica.
Fueron retirados durante la migración Paperclip-like: la selección vigente usa
perfiles proporcionales y `solo_lead` ejecuta directamente sin un agente espejo.

---

## 2026-05-04

Reorientacion del producto:

- AI Teams converge hacia un control plane estilo Paperclip sobre SQLite.
- Se conserva el foco en equipos de programacion.
- Se fijan perfiles canonicos: `solo_lead`, `lead_quorum`, `full_team`.
- Se adopta Lead-first con hiring dinamico.
- La delegacion economica pasa a ser objetivo central de producto.
- Suscripciones LLM y APIs se tratan como canales independientes.

Implementado en la reconstruccion:

- schema v2 paralelo;
- migrador dry-run/apply;
- run profiles y team blueprints;
- checkout atomico;
- runs y wakeups basicos;
- scheduler inicial;
- endpoints de control plane;
- retirada de `FileLockRegistry` del camino principal.

Limpieza:

- eliminada documentacion legacy;
- eliminados prompts raiz `CLAUDE.md` y `GEMINI.md`;
- eliminada suite legacy no alineada con el objetivo nuevo;
- limpiado runtime local antiguo.
- realizado rescate selectivo de piezas antiguas valiosas en `docs/legacy_rescue/`, con snapshots aislados y notas de port v2.

## Antes de 2026-05-04

El historial detallado de bloques antiguos queda en Git. No usarlo como roadmap activo.
