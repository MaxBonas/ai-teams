# Arquitectura objetivo (v0.2)

Este documento resume la arquitectura implementada y su alineacion con Agent Teams.

> **Nota de vigencia (2026-03-31):**
> este documento ya no refleja por completo el comportamiento real del flujo de chat
> adaptativo. La fuente de verdad mas fiel para LCP, scouts, pausa/reanudacion y
> backlog de arquitectura es actualmente `docs/TASKS_2026_03_28.md`.
> `ARCHITECTURE.md` sigue siendo util como mapa base del sistema, pero esta
> desactualizado respecto a varias capacidades ya implementadas y varias
> desalineaciones conocidas.

## Componentes implementados

- **Shared Task List** (`aiteam/taskboard.py`)
  - Estados: `pending`, `ready`, `claimed`, `blocked`, `completed`, `failed`.
  - Dependencias por tarea y persistencia local.
- **Mailbox** (`aiteam/mailbox.py`)
  - Mensajes directos y broadcast en `jsonl`.
- **Memoria por agente** (`aiteam/memory.py`)
  - Almacen de memoria persistente por agente.
  - Recuperacion reciente + relevante por tarea.
  - Filtro por tipo de memoria para evitar `context poisoning` (ej. excluir `meeting_minutes`).
  - Compactacion fisica simple por numero de entradas, pero todavia sin una capa
    semantica barata de condensacion por proyecto.
- **Comunicacion y reuniones** (`aiteam/communication.py`)
  - Sync meetings con minutos compartidos.
  - DMs entre agentes para desbloquear dependencias.
  - Standups compactos para evitar crecimiento recursivo del contexto.
- **Router Pro-first + API fallback** (`aiteam/router.py`)
  - Priorizacion por canal/proveedor.
  - Fallback controlado por intentos y presupuesto.
  - Filtro por `role_targets` para enrutar runtimes por rol.
- **FinOps** (`aiteam/finops.py`)
  - Presupuesto diario/mensual API.
  - Ledger de costo por decision de ruteo.
  - Señal de presion de presupuesto para downgrade (`max_api_tier`, intentos API sugeridos).
- **Runtime aislado** (`aiteam/runtime.py`)
  - Workspace por agente/tarea.
  - Locks de archivos para evitar colisiones.
- **Control de entorno y navegador** (`aiteam/execution.py`)
  - Ejecucion `cmd` y `powershell` con politica de seguridad.
  - Navegacion agéntica basica (`browser_fetch`, `browser_open`).
  - `browser_script` con Playwright (multi-step + evidencias).
- **Compliance guardrails** (`aiteam/compliance.py`)
  - Aprobacion requerida para comandos/adapters sensibles.
  - Doble aprobacion en `prod` para operaciones sensibles.
  - Redaccion de secretos en contexto operativo.
- **Registro de adapters externos** (`aiteam/adapters/registry.py`)
  - Carga runtimes propios desde `runtime/adapters.json`.
  - Inventario de herramientas externas con `aiteam/tool_inventory.py`.
- **AutoTool Integrator** (`aiteam/autotools.py`)
  - Integra `cli|mcp|skill` desde metadata de tarea.
  - Auto-discovery por capacidades faltantes con catalogo (`config/tool_sources.catalog.json`).
  - Persistencia en `runtime/tool_registry.json` y `runtime/mcp_servers.json`.
- **Quality gates** (`aiteam/orchestrator.py`)
  - Tareas de Engineer abren gates de Review + QA.
  - En tareas de riesgo se agrega gate de Security.
  - La tarea padre se libera al completar todas las gates.
- **Lead Control Protocol (LCP)** (`api/main.py`)
  - El Team Lead puede emitir directivas estructuradas (`DIRECT_ANSWER`, `REJECT`,
    `ABORT_PHASES`, `RUN_MODE`, `ESCALATE`, `SKIP`, `ADD_PHASE`, `EXTEND_BUDGET`,
    `DELEGATE`, `CLARIFY`) durante `lead_intake`.
  - `aiteam/lead_control.py` ya actua como fuente comun para parsing e iteracion
    de directivas/checkpoints LCP consumidos desde `api/main.py`.
  - Estas directivas se procesan actualmente sobre todo antes de crear las fases dinamicas,
    aunque varias ya operan tambien sobre checkpoints mid-run.
- **Scout layer** (`api/main.py`, `aiteam/profiles.py`)
  - Scouts baratos pre-flight para resumir estado del proyecto e historial reciente.
  - Delegacion on-demand del Lead para investigacion adicional antes de planificar.
- **Pausa y reanudacion conversacional** (`api/main.py`, `aiteam/orchestrator.py`)
  - `WAITING_USER` para `lead_intake` y para fases mid-run.
  - Persistencia de estado pendiente en `runtime/pending_clarification_<chat>.json`.
- **Peer consultation** (`aiteam/orchestrator.py`)
  - Consulta cruzada entre roles con una o dos rondas de opinion.
  - Aporta deliberacion multi-rol y ahora intenta diversidad real de familias de
    proveedor por ronda cuando hay oferta compatible.
  - Si no existe proveedor alternativo viable, relaja la restriccion y emite
    `peer_diversity_fallback` para dejar trazabilidad del compromiso.
- **Observabilidad** (`aiteam/observability.py`)
  - Event log y resumen por tipo de evento.
  - Alertas operativas de degradacion + dashboard HTML de operaciones.
- **Cobertura E2E** (`tests/test_e2e_multiagent.py`)
  - Existe ya una suite vertical con adapters fake para delegacion, quorum,
    `REPLAN` y `FORCE_GATE` sin depender de APIs reales.
  - La suite combina integración directa `api/main.py + orchestrator` con un
    caso HTTP/SSE para mantener cobertura vertical sin penalizar el tiempo de CI.

## Principio operativo de coste y contexto

El sistema objetivo no debe tratar a todos los modelos como si fueran operadores de
herramientas equivalentes. La regla arquitectonica deseada es:

- **el Team Lead y otros modelos caros deciden**
- **los especialistas baratos operan herramientas pesadas**
- **el Lead recibe informes compactos, no transcripts crudos de tool use**

Motivacion:

- El uso intensivo de `MCP`, `CLI`, `LSP`, navegador, logs, tests o exploracion de
  repositorios consume mucho contexto y muchos tokens.
- Ese coste no aporta tanto valor en un modelo avanzado si el trabajo es mecanico,
  repetitivo o de exploracion estructurada.
- Conviene descargar ese trabajo en subagentes baratos o gratuitos que usen tools,
  skills y LSPs, y devolver al Lead solo evidencia resumida, diffs, riesgos y
  recomendaciones.

## Context Curator objetivo

Hay una extension natural de esta misma idea: no solo delegar browser/tests/MCPs,
sino tambien delegar **compactacion de contexto y mantenimiento de memoria viva del
proyecto**.

Razon:

- En Codex, OpenAI documenta que los subagentes ayudan a evitar `context pollution`
  y `context rot` moviendo trabajo ruidoso fuera del hilo principal y devolviendo
  **summaries en vez de output crudo**.
- En nuestro sistema ya existe una base parcial:
  - `AgentMemoryStore` compacta por limite de entradas
  - `agent_session.py` resume turnos viejos en un turno `summary`
  - `project_continuity` ya construye una vista resumida del historial reciente
- Lo que todavia falta es una capa **semantica, economica y persistente** que
  convierta ejecuciones largas en memoria util para reanudacion, handoff y trabajo
  multiagente.
- Avance actual:
  - existe `aiteam/context_curator.py` con schema `project_context_v1`
  - persiste contexto compactado en `runtime/context/projects/` y
    `runtime/context/chats/`
  - `api/main.py` ya alimenta esa memoria con `scout_context_curator` y la
    salida limpia de `lead_intake`
  - `api/main.py` ya calcula `preplan_context_pressure` leyendo, si aplica, la
    corrida previa (`delegate_batches`, `phase_context_summaries`,
    `specialist_reports`, `invalidations`) para decidir si conviene volver a
    compactar antes incluso de planificar
  - `api/utils.py::_build_project_continuity_context()` ya injecta un resumen
    de esa memoria en el bloque de continuidad
  - `aiteam/orchestrator.py` ya actualiza esa memoria de forma incremental
    durante la run y guarda `phase_context_summaries` en `workflow_state`
  - `aiteam/orchestrator.py` ya recalcula `context_pressure` durante la run y
    la persiste en `workflow_state`, de modo que la compactacion barata pueda
    dispararse por volumen real de contexto y no solo por reglas fijas
  - `_build_dependency_output_context()` ya prioriza esos resúmenes compactos
    antes del output crudo cuando una fase depende de otra
- `REPLAN` y `FORCE_GATE` ya marcan invalidaciones sobre esa memoria para que
  fases reabiertas o sustituidas no sigan pareciendo contexto vigente
- `api/utils.py` y `api/routers/aiteam.py` ya exponen esa capa en `last_chat_run`
  con:
  - `context_pressure`
  - `freshness_status`
  - conteos por capa
  - timestamps de actualizacion
  - resumen compacto curado
  - ahorro estimado de contexto:
    - `estimated_context_chars_saved`
    - `estimated_context_tokens_saved`
    - `raw_context_chars` / `compact_context_chars`
    - `compression_ratio`

### Especialista propuesto: `context_curator`

- Ya existe como especializacion formal barata en `aiteam/tool_specialists.py`.
- Su funcion es preparar y mantener memoria compactada util para el Lead, no
  arbitrar estrategia ni producto.
- Puede activarse explicitamente o aparecer en continuations del Team Lead donde
  el contexto historico pesa mas que la ejecucion directa.
- La siguiente capa ya esta activa:
  - el ROI de compactacion no solo se visualiza; tambien gobierna comportamiento
  - cuando el runtime detecta que el contexto bruto acumulado supera claramente
    al contexto curado disponible, marca `context_compaction_priority_boost`
  - esa señal puede hacer que `context_curator` suba al frente del roster de
    especialistas baratos para compactar durante la run antes de que el Lead
    tenga que releer outputs largos o historico redundante
- Ademas ya puede activarse por `context_pressure` cuando la run acumula:
  - muchos `delegate_batches`
  - muchos `specialist_reports`
  - muchas `phase_context_summaries`
  - invalidaciones recientes o varias preguntas abiertas

No deberia ser un rol senior nuevo, sino otro especialista barato dentro de la tool
fabric, alineado con el principio:

- los modelos caros deciden
- los especialistas baratos condensan contexto

Funcion esperada:

- leer de forma economica:
  - `agent memory`
  - `session summaries`
  - `delegate_batches`
  - `specialist_reports`
  - `lead summaries`
  - `workflow_state`
  - eventos recientes y artefactos compactos
- escribir una memoria por capas para cada proyecto/chat root:
  - `working_set`: estado activo de corto plazo
  - `durable_facts`: hechos estables y decisiones vigentes
  - `open_questions`: huecos pendientes o dependencias externas
  - `invalidations`: cosas que antes eran verdad pero ya no
  - `next_actions`: siguientes pasos o checkpoints recomendados

### Reglas de diseño

- El curador no debe inventar verdad nueva; debe **destilar** y referenciar fuentes.
- Debe registrar procedencia (`source_task_ids`, `updated_at`, `confidence`,
  `supersedes`) para que una compactacion barata no degrade la fiabilidad del sistema.
- Debe poder invalidar resumenes viejos cuando un `REPLAN`, `FORCE_GATE`, nuevo
  diff o nueva evidencia contradigan memoria previa.
- Su output al Lead debe ser pequeno y estable: un briefing por capas, no una
  narrativa larga.

### Disparadores recomendados

- al cerrar una run (`lead_close`)
- antes de `resume` o `continuation`
- despues de un `delegate_batch` grande
- cuando `agent_session` o `project_continuity` superen umbral de chars/turnos
- tras `REPLAN`, `FORCE_GATE` o cambios sensibles en el grafo
- en background, con budget barato, para proyectos activos

### Disparadores ya activos

- `lead_intake` ya arranca con `scout_context_curator` pre-plan
- continuations largas ya cargan `preplan_context_pressure`
- durante la run, el orquestador ya recalcula `context_pressure` tras nuevas
  fases compactadas y puede activar `context_curator` en prefetch de
  especialistas aunque la tarea no declare tools pesadas de forma explicita

### Visibilidad operativa ya activa

- `TeamViewer.tsx` ya muestra un bloque `Curated context` para el ultimo run
- el operador puede ver:
  - si la memoria curada esta `fresh/warm/stale`
  - el `score/level` de `context_pressure`
  - las señales que dispararon compactacion
  - cuantos items hay por capa en el chat
  - cuantas invalidaciones/preguntas abiertas siguen activas
  - cuanto contexto crudo se estima que se evito reenviar al Lead

### Eleccion de modelo

- Para esta tarea conviene un modelo barato orientado a subagentes/resumen:
  - `gpt-5.4-mini` encaja bien cuando haya tools/contexto estructurado
  - `gpt-5-nano` o equivalente barato encaja para clasificacion/sumarizacion pura
- El Lead solo deberia intervenir si el curador detecta contradicciones, baja
  confianza o necesidad de arbitraje.

## Tool Execution Fabric objetivo

La arquitectura objetivo v0.3 debe asumir que AI Teams tendra **muchos MCPs, CLIs y
LSPs** disponibles al mismo tiempo, no uno o dos conectores puntuales.

Eso implica modelar una capa explicita de ejecucion de herramientas:

- **Catalogo de herramientas de primer nivel**
  - `MCP servers`, `CLIs`, `skills`, `LSPs`, navegadores automatizados,
    runners de tests, conectores de repositorio y utilidades del sistema
  - metadatos por capacidad, coste, latencia, riesgo y entorno

- **Agentes especialistas por familia de herramienta**
  - `repo_scout`: inspeccion de archivos, git, estructura y contexto historico
  - `lsp_navigator`: simbolos, referencias, rename impact, diagnosticos
  - `browser_operator`: MCP/browser/playwright, reproduccion y evidencias UI
  - `test_runner`: ejecucion de suites, captura de fallos, resumen de regresiones
  - `mcp_operator`: integraciones externas o verticales especificas
  - `skill_worker`: uso intensivo de skills o pipelines locales concretos

- **Contrato de salida estructurado hacia el Lead**
  - resumen ejecutivo corto
  - evidencia relevante
  - artefactos generados
  - riesgos / incertidumbres
  - recomendacion accionable
  - referencia a `provider/model/toolset` usado

- **Aislamiento de contexto**
  - los transcripts largos de tool use, trazas, HTML, logs y dumps de LSP no deben
    subir completos al Lead salvo excepcion
  - el Lead consume briefings compactos y pide ampliacion solo cuando haga falta

- **Enrutado por economia de tokens y riesgo**
  - si una tarea es principalmente "usar herramientas y resumir", la ruta por
    defecto debe ser un especialista barato
  - si una tarea exige juicio, arbitraje, trade-offs o decisiones de producto,
    debe escalar al Lead o a un modelo avanzado

## Funcion tecnico deseado para MCP, CLI, LSP y skills

La capa de herramientas no debe vivir solo como "auto-discovery". Debe convertirse en
una malla operativa explicita:

- Los `MCPs` se tratan como buses de capacidades remotas o locales que pueden ser
  asignados a especialistas concretos.
- Los `CLIs` se tratan como herramientas de ejecucion y observacion de bajo coste:
  tests, linters, buscadores, utilidades de repo, builds y scripts de diagnostico.
- Los `LSPs` se tratan como fuente estructurada de navegacion semantica del codigo:
  definiciones, referencias, impactos, diagnosticos, refactors.
- Los `skills` se tratan como playbooks operativos reutilizables que un subagente
  especialista puede invocar sin exigir al Lead que cargue toda esa logica en prompt.
- Si en la maquina ya existen MCPs integrados en otro IDE/agente como `opencode`,
  conviene tratarlos como bootstrap operativo del inventario y de la configuracion
  de especialistas, no como conocimiento que haya que reconstruir desde cero.

Caso canonico:

1. El Lead decide que necesita evidencia de navegador, repo y tests.
2. Delega a un `browser_operator`, un `repo_scout` y un `test_runner`.
3. Esos agentes usan `MCP/browser`, `CLI` y `LSP/skills` con modelos baratos.
4. Cada uno devuelve un informe compacto.
5. El Lead compara informes, consulta peers si hace falta y decide.

Avance estructural ya aplicado para `skills` y `LSP`:

- Una tarea ya puede declarar `skill_targets` y `lsp_targets` como parte de su
  contrato operativo.
- Esos targets ya se normalizan y persisten en `effective_tool_inventory`.
- Ademas, el sistema ya deriva de ellos capacidades canonicas como
  `skill_run`, `lsp_symbols` y `lsp_references`, para que no sean solo metadata
  descriptiva sino senales utilizables por especialistas e inventario.
- `tool_specialists` ya puede inferir `skill_worker` y `lsp_navigator`
  directamente desde esos targets, y el prompt del especialista ya expone
  `Skills objetivo` / `Objetivos LSP`.

Economia operativa ya registrada:

- Cada `delegate_batch` puede producir un bloque `delegate_economics_v1`
  heuristico, pensado para estimar el ahorro de contexto caro del Lead.
- La estimacion ya viaja como:
  - `delegate_economics_estimated` en `events.jsonl`
  - `delegate_economics_summary` en `workflow_state`
  - `delegate_economics` en `TeamChatResponse`, `chat/progress`,
    `/api/aiteam/state` y `/api/aiteam/conversations`
- La UI operativa del IDE ya consume esa señal para mostrar:
  - ahorro neto estimado
  - ratio de `quorum_met`
  - especialistas con mayor impacto relativo
- Las cifras actuales son **estimadas**, no facturacion exacta por proveedor.
  Su objetivo es observabilidad comparativa y tuning de routing.
- Primer uso activo de esta señal:
  - `HybridRouter` ya puede leer `delegate_economics_estimated` recientes y
    subir temporalmente a `advanced_api` para un especialista concreto cuando
    la ruta barata muestra bajo `quorum` o poco ahorro neto.

Contrato operativo ya iniciado:

- El Lead puede emitir intents especializados como `DELEGATE_REPO_SCAN`,
  `DELEGATE_BROWSER_REPRO`, `DELEGATE_LSP_IMPACT`, `DELEGATE_TEST_RUN` y
  `DELEGATE_MCP_PROBE`.
- Cada delegacion puede declarar `WAIT_POLICY` (`all`, `best_effort`, `quorum`) y
  `DELEGATE_BUDGET` propio para no mezclar el coste operativo de tools con el
  budget principal del Lead.
- En `quorum`, el sistema ya puede lanzar varios especialistas complementarios
  para un mismo intent y devolver al Lead un informe agregado con
  `quorum_target/quorum_met`, en vez de un unico transcript operativo.
- Esa misma semantica ya no vive solo en `lead_intake`: checkpoints mid-run del
  Lead como `lead_report_*` o `lead_failure_*` tambien pueden delegar, esperar
  informes especializados y reanudar su decision con el briefing agregado.
- Ademas, el Lead ya puede declarar un `EVIDENCE_PLAN` estructurado por fase;
  el sistema lo convierte en tareas especialistas dependientes del workflow,
  en lugar de esperar a que el Lead emita una directiva textual ad-hoc mas tarde.
- La capa de guidance de tools ya distingue entre **coordinacion** y
  **operacion**: el Team Lead recibe una nota compacta con `skill_targets`,
  `lsp_targets` y MCP sugeridos para delegar, mientras que el operador barato
  recibe el playbook mas accionable.
- Para superficie UI/browser pesada, la politica por defecto ya empuja evidencia
  a especialistas baratos: `delegate_browser_repro` puede materializar
  `browser_operator`, `mcp_operator`, `test_runner` y `repo_scout`, y los
  operadores browser/MCP trabajan bajo un contrato compacto `operator_report_v1`
  en vez de devolver transcripts largos.
- Cuando la maquina ya dispone de OpenCode, el runtime puede bootstrapear MCPs
  conocidos desde la salida local de `opencode mcp list` (`mcp_list.txt`) hacia
  `runtime/mcp_servers.json`, en lugar de exigir redescubrir todos los servidores
  manualmente.
- Ademas, `MCPServerManager` ya puede reparar rutas legacy de otro usuario al
  cargar `runtime/mcp_servers.json` y reintentar de forma conservadora servidores
  `unhealthy` habilitados antes de descartarlos para seleccion de especialistas.
- El fabric MCP ya no solo informa salud: tambien cruza runtime + catalogo para
  declarar reemplazos viables por `skill/CLI` cuando un MCP sigue
  `package_unavailable`, y la UI muestra esos fallbacks de forma explicita en
  `MCP Fabric`.
- Ademas, esa senal ya no vive solo en observabilidad: `AutoToolIntegrator`
  puede preferir automaticamente replacements por `skill/CLI` cuando el catalogo
  marca un MCP como `prefer_skill_or_cli`, reduciendo friccion en auto-discovery.
- Ese rewiring ya tambien alcanza la seleccion operativa de especialistas:
  `orchestrator.py` persiste hints `tool_rewiring_*` y `tool_specialists.py`
  puede promover replacements compatibles y suprimir `mcp_operator` cuando el
  catalogo indique que esa no es la mejor ruta por defecto.
- Ese mismo rewiring ya tiene contrato de observabilidad y producto:
  `orchestrator.py` emite `tool_rewiring_applied`, `EventLogger.summary()`
  agrega KPIs por especialista, y `api/main.py` / `api/routers/aiteam.py`
  exponen `tool_rewiring_summary` en `last_chat_run` para UI y estado del IDE.
- Ademas, el rewiring ya no se queda en diagnostico o metadata:
  `api/main.py` lo aplica directamente al planner de delegacion y al
  `EVIDENCE_PLAN`, de modo que una corrida puede sustituir un `mcp_operator`
  reemplazable por `skill_worker` o `browser_operator` antes de lanzar la tarea.
- Ese fallback ya es mas rico: cuando el Lead no emite `EVIDENCE_PLAN`, la
  heuristica base detecta superficies de `browser`, `security` y
  `research/docs`, y activa intents baratos por fase que luego el catalogo
  puede rewirear hacia skills/CLI mas estables.
- Si el Lead no lo declara, `api/main.py` ya puede sintetizar un fallback
  conservador de evidencia por fase para corridas de build estandar, de forma
  que `build/review/qa` sigan recogiendo señales baratas sin depender
  completamente de la obediencia del prompt.

## Flujo operativo actual

1. `api/main.py` crea un `chat_root` y, en modo chat, puede lanzar scouts pre-flight baratos.
   - `scout_project_state`
   - `scout_session_history`
   - `scout_context_curator` (briefing compacto orientado por superficies)
2. Se ejecuta `lead_intake`; el Team Lead puede emitir `WORKFLOW_PLAN` y directivas LCP.
   - Ya recibe un bloque `PREPLAN_SIGNALS` con superficies detectadas
     (`browser/security/research`) y pistas de delegacion barata antes de planificar.
3. `api/main.py` procesa las directivas del Lead antes de crear fases:
   - retorno temprano (`DIRECT_ANSWER`, `REJECT`, `ABORT_PHASES`)
   - seleccion de preset de corrida (`RUN_MODE: planning_only | team_decision`)
   - ajuste de budget/severidad (`ESCALATE`, `EXTEND_BUDGET`)
   - mutacion inicial del plan (`SKIP`, `ADD_PHASE`)
   - pausa (`CLARIFY`) o investigacion adicional (`DELEGATE`)
4. Se crean fases dinamicas como `WorkTask` y el Taskboard calcula readiness por dependencias.
5. El orquestador asigna tarea por rol, reclama locks, construye contexto y ejecuta peer consultation cuando aplica.
6. El router selecciona canal/modelo con politica Pro-first y presupuesto.
7. Si faltan capacidades, auto-discovery integra tools desde catalogo y reintenta.
8. Si hay `execution_plan`, valida compliance y ejecuta pasos locales bajo guardrails.
9. Cualquier fase no-scout puede pausar mid-run con `[CLARIFY]` y quedar en `waiting_user`.
10. El cierre del chat se evalua en `api/main.py` con scoring, evidence gate agregado y politicas de rechazo/cierre.
11. `workflow_state` persiste `phase_evidence_plan` y `delegate_batches`, y la API
    de chat/progress ya los expone para UI, resume y observabilidad.
12. Todos los eventos y costos quedan auditados en `runtime/`.

## Gap hacia un sistema de nivel "produccion"

- Integrar mas MCP servers reales con credenciales empresariales por entorno.
- Conectar CI remota y aprobación por pasos irreversibles.
- Endurecer deteccion de secretos/PII y politicas zero-trust para tools externas.

## Desalineaciones conocidas con la vision objetivo

- **Soberania parcial del Team Lead**
  - Hoy el Lead puede decidir mucho, pero la mayoria de directivas LCP se procesan
    solo tras `lead_intake` y antes de crear fases.
  - Todavia no puede mutar el flujo "en cualquier momento" de la run con la misma
    autoridad con la que hoy decide al principio.

- **LCP Fase 2 ya iniciada, pero aun no cerrada**
  - `[REPLAN]` y `[FORCE_GATE]` ya tienen MVP funcional, pero falta endurecerlos
    como mecanismos de reorquestacion plenamente soberanos del Lead.
  - El sistema ya replanifica y reabre gates, pero todavia conviven con
    decisiones post-hoc en la capa de API.
  - En concreto, hoy la aplicacion real de ambos vive sobre todo en
    `api/main.py`: `REPLAN` ya soporta reconstruccion completa o parcial del
    tramo pendiente, y `FORCE_GATE` ya puede reabrir gates de una fase
    completada; lo pendiente es consolidar esa logica en una capa canonica de
    control del Lead y normalizar mejor la observabilidad/casos borde.

- **Deliberacion multimodelo incompleta**
  - La peer consultation actual consulta otros roles, pero no fuerza diversidad
    de proveedores o familias de modelos.
  - El sistema es multimodelo potencial, no multimodelo deliberativo garantizado.

- **Rechazo post-hoc demasiado fuerte en la API**
  - `strict_mode`, `live_mode_required`, `low_productivity` y el evidence gate
  agregado a nivel chat viven en `api/main.py`.
  - Esto puede bloquear o rechazar una run incluso cuando el Lead ya tomo una
    decision razonable con la informacion disponible.

- **Capa de herramientas aun no modelada como "tool fabric"**
  - Ya existen `autotools`, inventory, browser automation, skills y algunos hooks
    de integracion, pero el sistema aun no prioriza explicitamente que los usos
    intensivos de `MCP/CLI/LSP/browser` los operen subagentes baratos.
  - `orchestrator.py` ya puede predelegar especialistas baratos antes de una
    tarea principal mediante `select_specialists_for_task()`, persistiendo un
    `specialist_roster_applied` e inyectando informes compactos en contexto.
  - Ese prefetch ya no es solo informativo: el runtime calcula quorum real y
    puede bloquear la tarea principal con `specialist_quorum_not_met` si no
    recibe la cobertura minima exigida por el roster.
  - Las delegaciones especializadas del Lead ya pueden nacer no solo en
    `lead_intake`, sino tambien desde checkpoints mid-run como
    `lead_report_*`, `lead_failure_*` y `lead_close`, quedando registradas en
    `workflow_state.delegate_batches`.
  - Ya hay un primer desacople de contexto: `skill_targets` / `lsp_targets`
    pueden viajar hasta el especialista sin obligar al Lead a cargar el mismo
    bloque operativo completo.
  - La politica por defecto ya empieza a tratar browser/MCP pesado como caso
    delegado, pero aun falta convertirlo en una fabric completa con inventario
    vivo de MCPs/CLIs/LSPs disponibles por entorno.
  - Falta una capa formal de agentes especialistas de herramientas con contratos
    de informe compactos hacia el Lead.
  - El contrato `SpecialistReport` ya no vive solo en metadata interna:
    `chat/progress/state` ya exponen resúmenes compactos de informes de
    especialistas, y la UI operativa ya puede mostrar su conteo, validez y
    resumen por especialista sin subir transcripts crudos.

- **Observabilidad de evidencia delegada aun parcial**
  - `phase_evidence_plan` y `delegate_batches` ya viven en `workflow_state` y se
    exponen por API/progreso, pero aun faltan metricas mas finas de ahorro de
    tokens, ratio de quorum y coste operativo por especialista.

- **Control partido entre API y orquestador**
  - En flujo chat, las fases dinamicas se crean con `interactive_chat=True`,
    `skip_quality_gates=True` y `skip_evidence_gate=True`.
  - Luego el cierre se vuelve a decidir con un evidence gate agregado a nivel chat.
  - Funciona, pero conceptualmente el control queda dividido entre dos capas.

- **Documento base desactualizado**
  - `ARCHITECTURE.md` ya no es la descripcion mas fiel del sistema.
  - El backlog activo en `docs/TASKS_2026_03_28.md` refleja mejor el estado real
    y debe considerarse la referencia principal hasta una reescritura de esta arquitectura.

- **Readiness cross-machine aun incompleto, aunque ya visible**
  - El runtime MCP ya distingue mejor entre servidores `portable` y `user_bound`,
    y la API/UI ya muestran `machine_profile` y `portability_counts`.
  - Eso permite saber mejor que parte del fabric esta lista para ORCH-01 o para
    la maquina principal, pero aun no sustituye la validacion real de salud en
    ambas maquinas con credenciales y binarios disponibles.
