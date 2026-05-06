# Rescate Selectivo De Legacy

Fecha: `2026-05-04`

Esta carpeta conserva piezas antiguas con valor de diseño o implementacion. No son fuente viva, no deben importarse y no deben restaurarse enteras sin una migracion explicita al control plane v2.

Los snapshots estan en `source_snapshots/` con extension `.txt` para mantenerlos fuera del grafo de imports.

## Criterio De Rescate

Se rescata una pieza si contiene alguna de estas cosas:

- una politica de producto que sigue vigente;
- un algoritmo o heuristica aprovechable;
- un contrato operativo que conviene portar a tablas v2;
- prompts o roles trabajados que pueden convertirse en skills markdown;
- UI/observabilidad reutilizable como concepto.

No se rescatan como activos:

- parsers de directives como contrato principal;
- JSONL como persistencia primaria;
- router con scoring multifactor;
- gates pesados por defecto;
- runtime round-based.

## Piezas De Alto Valor

### FinOps Y Delegacion Economica

Snapshots:

- `source_snapshots/aiteam__finops.py.txt`
- `source_snapshots/docs__MODEL_POLICY.md.txt`
- `source_snapshots/docs__MULTIMODEL_ROUTING_REFERENCE.md.txt`

Valor:

- `BudgetPolicy`, `ApiBudgetSignal` y presion de presupuesto.
- Estimacion de coste por modelo y forecast mensual.
- Deteccion simple de anomalias por z-score.
- Tiers `senior_cloud`, `advanced_api`, `budget_api`, `local`.
- Politica: Lead/seniors arriba; workers baratos para tareas acotadas.

Port v2:

- mover ledger a `cost_events`;
- mover catalogo a `agents.adapter_config` o tabla `adapter_catalog`;
- calcular `estimated_savings_cents` y `actual_cost_cents` en `runs`;
- usar presion de presupuesto como input del Lead, no como router magico.

### Compliance, Seguridad Y Sandbox

Snapshots:

- `source_snapshots/aiteam__compliance.py.txt`
- `source_snapshots/aiteam__execution.py.txt`
- `source_snapshots/docs__SECURITY_COMPLIANCE.md.txt`

Valor:

- patrones de comandos sensibles: deploy, release, prod, terraform, kubectl, docker push, mensajeria.
- redaccion de secretos comunes.
- aprobacion por entorno, con doble aprobacion en `prod`.
- idea de workdirs permitidos y bloqueo de operaciones irreversibles.

Port v2:

- convertir approvals en `issue_thread_interactions(kind='request_confirmation')`;
- guardar decisiones en `activity_log`;
- convertir comandos sensibles en checks previos de `runs`;
- evitar gates globales: aplicar solo si el run toca acciones de riesgo.

### MCP, Tools Y Skills

Snapshots:

- `source_snapshots/aiteam__mcp_manager.py.txt`
- `source_snapshots/aiteam__tool_inventory.py.txt`
- `source_snapshots/aiteam__tool_dispatch.py.txt`
- `source_snapshots/aiteam__tool_lock.py.txt`
- `source_snapshots/docs__MCP_CLI_SKILLS_ROADMAP.md.txt`
- `source_snapshots/docs__EXTERNAL_TOOLS_INVENTORY.md.txt`

Valor:

- distincion correcta: skills = como trabajar; MCP/tools = con que operar.
- catalogo de capacidades canonicas por herramienta.
- riesgo por herramienta y aprobacion.
- descubrimiento de tools por capability faltante.
- lock/ownership de herramientas para evitar uso concurrente peligroso.
- gestion MCP stdio con handshake, `tools/list` y `tools/call`.

Port v2:

- `tool_access` como tabla primaria;
- `run_events` para invocaciones MCP;
- `agents`/`issues` declaran capacidades necesarias;
- el Lead delega tareas de uso de tools a workers baratos si son simples;
- approvals solo para tools de riesgo medio/alto o escritura externa.

### Lead, Quorum Y Planificacion

Snapshots:

- `source_snapshots/aiteam__lead_control.py.txt`
- `source_snapshots/aiteam__quorum.py.txt`
- `source_snapshots/docs__LEAD_QUORUM_PROJECT_CONTEXT_VISION.md.txt`
- `source_snapshots/docs__RUN_PROFILES_PLAN.md.txt`
- `source_snapshots/docs__INTERNAL_QUALITIES_ROADMAP.md.txt`

Valor:

- taxonomia de delegaciones: repo scan, browser repro, LSP impact, test run, MCP probe.
- `WAIT_POLICY`: `all`, `best_effort`, `quorum`.
- idea fuerte: el Lead conserva soberania; quorum senior solo mejora plan.
- fases ideales de quorum: ingesta comun, razonamiento independiente, puesta en comun, consolidacion final del Lead.
- directriz de planificacion detallada: objetivo, supuestos, riesgos, delegaciones, revision.

Port v2:

- no recuperar bracket directives como contrato;
- convertir esas intenciones en issues estructuradas y `issue_dependencies`;
- quorum como perfil `lead_quorum` que crea `consultation` sub-issues;
- registrar por issue: quien ejecuta, quien revisa, a quien reporta.

### Contexto, Memoria Y Aprendizaje

Snapshots:

- `source_snapshots/aiteam__context_curator.py.txt`
- `source_snapshots/aiteam__lead_memory.py.txt`
- `source_snapshots/aiteam__learning_registry.py.txt`
- `source_snapshots/aiteam__snapshots.py.txt`

Valor:

- heuristicas de presion de contexto y estimacion de tokens ahorrados.
- separacion de contexto por proyecto/chat.
- compactacion de request working set.
- memoria del Lead y aprendizaje operacional.

Port v2:

- `learning_facts` como tabla primaria;
- summaries como `issue_comments` o `run_events`;
- tareas de compresion delegables a workers baratos;
- evitar memoria opaca global: cada hecho debe tener fuente, run y caducidad.

### Observabilidad, Health Y Auditoria

Snapshots:

- `source_snapshots/aiteam__observability.py.txt`
- `source_snapshots/aiteam__metrics.py.txt`
- `source_snapshots/aiteam__run_health.py.txt`
- `source_snapshots/aiteam__audit_trail.py.txt`
- `source_snapshots/aiteam__provider_ops.py.txt`
- `source_snapshots/aiteam__model_catalog.py.txt`
- `source_snapshots/docs__ROUTING_CATALOG_VIEW.md.txt`

Valor:

- resumen operativo de eventos, alertas y tasas de exito.
- estado de providers/modelos con degradacion y cambios.
- health report de run: fases completadas, errores de routing, skips, gate iterations.
- auditoria de decisiones.

Port v2:

- `run_events`, `activity_log` y `runs` sustituyen JSONL;
- liveness debe mirar issues no terminales sin owner/run/wakeup/dependency sana;
- provider health debe alimentar al Lead y a adapter registry, no a scoring opaco;
- UI debe mostrar timeline de runs e issues, no logs JSONL sueltos.

### Adapters Y Canales

Snapshots:

- `source_snapshots/aiteam__adapters__base.py.txt`
- `source_snapshots/aiteam__adapters__api.py.txt`
- `source_snapshots/aiteam__adapters__subscription.py.txt`
- `source_snapshots/aiteam__adapters__external_program.py.txt`
- `source_snapshots/aiteam__routing_overrides.py.txt`
- `source_snapshots/aiteam__chat_policy.py.txt`

Valor:

- contrato `invoke`/`invoke_stream`.
- normalizacion de mensajes.
- separacion de subscription/API/external program.
- manejo de errores compactados y missing API keys.
- overrides manuales por routing/adapters.

Port v2:

- mantener `AdapterRegistry` simple por `adapter_type`;
- cada adapter real implementa build env, execute, parse stdout, usage/cost;
- fallback como lista ordenada por adapter, no scoring multifactor;
- errores van a `runs.error`, `run_events` y `activity_log`.

### UX Y Cockpit

Snapshots:

- `source_snapshots/docs__CHAT_UX_COMPACT_FLOW_VISION.md.txt`
- `source_snapshots/docs__ROUTING_CATALOG_VIEW.md.txt`

Valor:

- ideas de cockpit compacto: estado, timeline, routing/capabilities visibles.
- conviene recuperar concepto de operador observando agentes, no IDE full.

Port v2:

- UI nueva centrada en projects/issues/runs/interactions;
- no recuperar `TeamChat` viejo entero si depende de `/api/aiteam/*`;
- rescatar patrones visuales solo tras existir endpoints v2.

## Backlog De Rescate

1. Reimplementar FinOps v2 sobre `cost_events` y campos economicos de `runs`.
2. Reimplementar approvals sensibles como `issue_thread_interactions`.
3. Disenar `tool_access` + MCP invocation sobre tablas v2.
4. Convertir taxonomia de delegaciones del Lead en tipos de sub-issue.
5. Crear liveness checker usando `issues`, `runs`, `wakeup_requests` y `issue_dependencies`.
6. Crear skills markdown del Lead, quorum, worker barato, reviewer y QA usando las notas de este rescate.

## Regla

Si una pieza legacy parece util, copiar solo la idea o una funcion pequeña, con test v2 nuevo. No restaurar modulo entero para "volver a tenerlo".
