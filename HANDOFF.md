<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-07-21`

AI Teams ya no está en reconstrucción inicial. Es un control plane multiagente Paperclip-like funcional, centrado en SQLite, y se encuentra en fase de endurecimiento operativo, validación con proveedores reales y medición frente a un agente único.

El quorum profundo tiene defensa en profundidad: objetivo congelado frente a
Chat, nuevos objetivos mediante Nueva tarea y aceptación SQLite limitada a un
Plan B creado en la misma run por el Lead configurado.

## Autoridad documental

Orden de prioridad:

1. `AGENTS.md`: reglas de desarrollo y producto objetivo.
2. `task.md`: estado y backlog resumido.
3. `docs/MIGRATION_PAPERCLIP.md`: plan rector e historial de la migración.
4. `docs/PAPERCLIP_GUIDE.md`: patrones Paperclip adaptados.
5. `docs/RUN_PROBLEMS_REGISTRY.md`: fallos observados y mitigaciones.
6. Código activo y tests.

`AGENTS.md` es la única instrucción raíz compartida. Las skills activas viven en
`.agents/skills/` y nunca prevalecen sobre `AGENTS.md`. No reintroducir
`CLAUDE.md`, `GEMINI.md` ni prompts raíz específicos de proveedor.

## Estado técnico

Implementado y activo:

- SQLite como motor único del control plane: issues, agents, assignments, runs, wakeups, interactions, reports, costes, actividad y acceso a herramientas.
- `HeartbeatLoop` + `HeartbeatScheduler` + `RunExecutor` como camino real de ejecución, con reconciliation y liveness en cada tick.
- Checkout atómico, dependencias, wakeups durables y continuación de padres al cerrar hijos.
- Adapters reales para canales API y suscripción, con allowlist por proyecto, health probes y recovery/escalado.
- Lead-first, hiring dinámico y perfiles `solo_lead`, `lead_quorum` y `full_team`.
- Delegación económica por tier/capacidad, quality cascade y límite diario de coste.
- Reports estructurados con provenance, receipts Git, revisión anclada al diff, aceptación independiente y `test_runner` determinista.
- Cross-provider review vinculante en criticidad alta y quorum para decisiones complejas.
- Context diet, focus files, payload delta y memoria operativa mediante `learning_facts`.
- Cockpit Vite/React sobre APIs v2, timeline durable, decisiones humanas, equipo, runs y costes.
- Canario e2e sin LLM y benchmark A/B contra `codex exec` único.
- Canario Lead + Quorum sin LLM con gate de aportes, síntesis y cierre durable de planificación, sin ejecución.

La compatibilidad legacy ya no gobierna el runtime. Persisten únicamente shims o migraciones aisladas que deben eliminarse solo tras confirmar consumidores reales.

## Trabajo reciente

- OpenCode server permanece experimental. El A/B de transporte v1 con DeepSeek
  pasa 3/3 direct y 3/3 attached, conserva seis sesiones aisladas y reduce la
  mediana 7,50→2,92 s con tokens equivalentes. El servidor está autenticado en
  loopback y termina sin procesos residuales. Producción sigue efímera porque
  cancelación, hangs/recovery, health MCP y SDK no están validados.
- Consolidación Git completada el 2026-07-21: runtime/control plane, evidencia
  de calibración y documentación se separaron en `1b3650e`, `66304c8` y
  `c695661`; junto con los commits locales previos se publicaron 16 commits en
  `origin/master`. El barrido no encontró secretos ni artefactos runtime y la
  suite previa al cierre terminó con `1161 passed`.
- Cerrada la cimentación de P0.3: `aiteam.policies` normaliza aliases, tier y
  estado de roles sin reescribir proyectos; `worker` es Tier 3, QA es un gate
  condicional y `test_runner` continúa determinista. La identidad de routing
  separa organización, vendor de modelo, perspectiva, transporte y pool de
  capacidad. Quorum y review crítico ya no cuentan Codex+OpenAI/GPT como dos
  perspectivas, aunque sus cuotas puedan seguir separadas. Los perfiles custom
  conservan metadata de roles, datos, workspace, MCP y salida estructurada.
  Verificación del estado completo: `1164 passed` el 2026-07-21.
- Sonnet 4.6 es ahora el modelo automático de Engineer dentro de Antigravity;
  Flash High conserva review/QA. En tres semillas de `cli_conversor`, ambos
  pasan 9/9 ocultos, pero Sonnet cierra 3/3, queda Ruff limpio 3/3 y tarda
  51,14 s medianos frente a 2/3, 1/3 y 105,48 s de Flash. El agregado v3 usa
  `benchmark_integrity.audit_ab_series`; no atribuye tokens ni coste API.
- El benchmark reveló envelopes distintos por modelo en `agy 1.1.5`. El parser
  soporta ahora ops limpios, `text + ops` y JSON seguido de ruido, priorizando
  siempre los ops estructurados. Los intentos previos fallidos se conservan como
  diagnóstico de transporte y no entran en la matriz de calidad.
- Calibración estructural Antigravity 1.1.5 completada con 27 runs stateless y
  tres muestras por comparación. Se detectó y corrigió que review debía usar
  Flash High —no Flash Medium— como baseline vivo. Pro High conserva Lead y
  Flash Low conserva scout por empate de cobertura con mucha menor latencia que
  Opus/GPT-OSS. Sonnet 4.6 avanza a benchmark conductual de coding (+9,1 puntos
  medianos, +12,42 s) y Flash Medium a validación económica de review (empate,
  -1,48 s). Esa fase no cambió defaults por sí sola; el A/B conductual posterior
  es el que promociona Sonnet. `agy` sigue sin entregar tokens headless.
- Sesiones CLI persistentes evaluadas y descartadas por ahora. El A/B Codex
  GPT-5.5 de dos semillas conserva memoria/override/aislamiento, pero resume
  casi duplica tokens brutos (ahorro mediano `-99,75 %`) y solo reduce duración
  `3,74 %`. Antigravity 1.1.5 reanuda correctamente por conversation UUID
  obtenido mediante `--log-file`, pero no entrega usage comparable. Producción
  sigue stateless; IDs implícitos `--last`/`--continue` permanecen prohibidos y
  Claude no está instalado.
- Corregido el catálogo de Equipo para Antigravity 1.1.5: `agy models` devuelve
  ocho slugs, no las antiguas etiquetas humanas. Las ocho opciones coinciden
  ahora exactamente y están habilitadas; configuraciones guardadas con etiquetas
  se normalizan antes de ejecutar sin perder el nombre legible en UI.
- Instrumento de benchmark endurecido antes de nuevas calibraciones:
  `scripts/benchmark_integrity.py` audita balance brazo×semilla, duplicados,
  contratos de evaluación, evidencia independiente, muestra, diversidad de
  providers, provenance, hard gates, estabilidad, mediana+rango y Goodhart. El
  harness de código sube a v4 y GPT-5.5; quorum añade profundidad estructural en
  paralelo al score léxico. La auditoría real acepta el 2×2 de checkout y niega
  una nueva conclusión en failover por rango de signo inestable y metadatos
  estructurales legacy ausentes, sin borrar su valor diagnóstico.
- Catálogos de modelos renovados con fuentes oficiales y disponibilidad real
  por adapter: OpenAI Sol/Terra/Luna, Anthropic Opus 4.8/Sonnet 5/Haiku 4.5,
  Gemini Pro 3.1 Preview/Flash 3.5/Flash-Lite 3.1 y opciones que `agy 1.1.5`
  enumera. Fable 5 queda manual por coste, retención y fallback; locales no se
  cambian si el modelo no está instalado/validado.
- Equipo presenta ahora un catálogo ejecutable por perfil: deshabilita modelos
  bloqueados, ausentes del runtime o rechazados como `model_unavailable`, y
  muestra la causa. El backend rechaza guardados inconsistentes y el hiring usa
  exactamente el mismo conjunto. Las runs completadas verifican el par
  perfil+modelo sin que un health check posterior borre la evidencia. El probe
  de `agy models` añadió la opción real `Gemini 3.1 Pro (Low)`.
- El primer canario Luna/auth es solo diagnóstico: Codex CLI `0.128.0` no puede
  ejecutar el catálogo cacheado para `0.145.0`. No se obtuvo summary ni score;
  GPT-5.5 continúa como Context Curator calibrado y Luna queda deshabilitado en
  Equipo hasta actualizar el CLI y repetir auth+queue.
- Lifecycle de modelos completado: `model_unavailable` bloquea la issue y crea
  una propuesta idempotente del mejor modelo ejecutable del mismo perfil,
  indicando cambios de familia/tier. Solo el owner puede aceptarla; la
  aplicación y reencolado son deterministas y no consumen otra llamada LLM.
  Rechazar mantiene el bloqueo, un cambio manual más reciente prevalece y la
  ausencia de fallback despierta al supervisor sin cambiar de adapter.
- FinOps distingue coste API de presión de cuota: suscripciones y local siguen
  en 0 céntimos marginales. `run_adapter_profiles` congela el perfil ejecutado y
  el snapshot de suscripción agrega usage, runs, duración y límites observados.
  Solo una política `subscription_quota` declarada por el owner habilita
  utilización/forecast; sin denominador conserva `capacity_unknown`. El cockpit
  lleva a Runs ante agotamiento observado o presión configurada.

- Nueva familia media reversible `inventory_snapshot_diff`: 20/20 siempre;
  `solo_lead` cerró 2/2 en una run y `full_team` 0/2 dentro de 12, con 2,92×
  tiempo/1,91× entrada medios. Se mantiene el default conservador del selector.
- Nueva familia frontend `accessible_checkout_form`: dos semillas 10/10 para
  `solo_lead` y `full_team`; solo cerró 2/2 en una run, equipo 1/2 en 10–12 runs
  y promedió 5,38× tiempo/6,61× entrada. La run abierta conserva continuación.
- `orchestrator_evals` recorre descendientes al decidir si una raíz está
  stranded; un wakeup o interacción viva en un hijo mantiene viva la raíz.
- Health de perfiles locales basado en runtime y modelo, no en autenticación de Codex.
- Corrección de intención de edición para delegaciones `Fix` asignadas a roles read-only.
- Context diet y harness de benchmark frente a Codex solo.
- Métricas deterministas de calidad y pasada QA adversarial.
- Tests de aceptación independientes y review anclada al diff.
- Garantía de wakeup al padre cuando un hijo cierra.
- Notificaciones de escalado y métrica de latencia de decisiones.
- Feedback de salud de proveedores hacia el routing.
- Memoria operativa entre proyectos.
- Canario e2e de convergencia completa.
- Revisión cross-provider, Git receipts, quality cascade, paralelismo opt-in y cap diario de coste.

## Prioridades vigentes

- El bloque backend/pre-run de P0.3 ya está cerrado. La decisión pura de
  `aiteam.model_compatibility` se resuelve sobre asignaciones reales mediante
  `aiteam.compatibility_service` y gobierna bootstrap del Lead, create/update,
  hiring y propuestas editadas, reconcile, delegación, escalado, recovery,
  fallback y dispatch. Un deny manual devuelve HTTP 422; una configuración
  persistida inválida bloquea la issue, crea continuación owner y no consume el
  modelo. Equipo ya consume esa decisión con contexto, conserva las opciones
  visibles y deshabilita perfil/modelo con la misma causa; el cache incluye rol,
  run profile, criticidad y clasificación. Catálogo y health también quedan
  separados por modelo: discovery autenticado demuestra presencia; solo un
  probe estructurado o una run completada marca `selectable`. Se conservan
  estados catalogued/verified/rate_limited/retired, y una ausencia de fallback
  crea continuación owner. JSON Object/Qwen queda endurecido con validación
  completa y un único repair que no puede modificar ops/status; Qwen sigue
  limitado a Tier 3 y criticidad media. La matriz hermética ya audita los 43
  modelos built-in, paridad Equipo/API y probes exactos de onboarding. El
  La telemetría de capacidad ya separa API free de suscripción: Groq persiste
  RPD/TPM observados en headers por modelo y Gemini queda sin porcentaje cuando
  el proyecto no aporta denominadores. Los canarios vivos de los tres run
  profiles están cerrados; el siguiente frente de P0.3 es calibrar OpenCode Zen
  y las promociones BYOK gratuitas por par exacto perfil+modelo+rol, siempre
  fuera de CI y solo cuando exista catálogo ejecutable demostrado.
  El primer bloque vivo encontró tres slugs Gemini 3.6 en Antigravity 1.1.5:
  High/Low aparecen en inventario pero fallan el submit, y Medium pasa review
  estructural 3/3 sin superar a 3.5 High. Permanecen manual-only y no
  seleccionables hasta un probe exacto; discovery no equivale a ejecución.
- Cerrado el canario durable de review Antigravity v4. Flash High y Gemini 3.6
  Medium rechazan el defecto, crean el fix mediante el Lead y aprueban la
  corrección en 3/3 semillas. Medium baja la mediana de 99,999 a 43,078 s, pero
  sin tokens ni denominador de cuota no desplaza el default. Los canarios vivos
  posteriores cerraron `solo_lead`, `lead_quorum` y `full_team`.
- Primer canario vivo de run profile cerrado: `solo_lead` con Antigravity Pro
  High completa en una run/54,656 s, materializa el archivo, pasa verificación
  de máquina y termina sin hijos ni trabajo vivo. En ese punto validaba 1/3
  perfiles; los resultados posteriores se describen a continuación.
- `full_team` vivo pasa en seed 3 con 12 runs/635,969 s y routing exacto: Codex
  GPT-5.5 Lead, Sonnet Engineer, Flash High Reviewer/Test Designer, Flash Low
  Scout y runner local. La raíz solo cierra después de pytest exit 0 y termina
  sin cola. Un intento previo descubrió que Antigravity Lead podía escribir el
  workspace directamente pese al deny de ops; los roles read-only Antigravity
  ejecutan ahora desde cwd efímero y reciben archivos solo por payload.
- `lead_quorum` cerró en seed 4 con 4 runs/305,7 s, Plan A y Plan B profundos,
  dos contribuciones válidas Codex GPT-5.5 + Antigravity Pro High, sesión
  `accepted` y raíz `done`. Las tres semillas anteriores degradaron por auditoría vacía,
  síntesis narrativa demasiado corta y AGENT-REPORT Codex inválido. La segunda
  sí obtuvo dos contribuciones cross-provider válidas. El prompt de Plan B ya
  exige explícitamente ≥300 palabras en `plan.narrative_markdown`; seed 4 valida
  esa corrección. Con `solo_lead` y `full_team`, los tres perfiles vivos quedan cerrados.
- Corregir a la vez la contradicción de transporte: los adapters API sí pueden
  materializar operaciones de archivo bajo RBAC; OpenCode Zen es el canal
  read-only. Las APIs gratuitas no tienen todavía MCP externo gobernado. La
  matriz provisional limita Nemotron a Lead/quorum/review de lectura,
  DeepSeek/MiMo y Gemini Flash/GPT-OSS 120B a review/QA, y North/Flash-Lite/
  Qwen/GPT-OSS 20B a scouts/curator hasta completar canarios. `task.md` contiene
  el orden, las rutas y la matriz E2E de cierre.

- OpenCode Zen Free queda integrado como perfil built-in read-only con catálogo
  descubierto por el CLI: Nemotron 3 Ultra (Tier 1), DeepSeek V4 Flash y MiMo
  V2.5 (Tier 2), North Mini Code (Tier 3). OpenCode `1.18.4` está instalado,
  reutiliza una sesión OAuth local y enumera además Laguna S 2.1 Free, aún fuera
  del catálogo aprobado. El screening público de una semilla pasa transporte,
  contrato y usage con Nemotron, DeepSeek, MiMo y Laguna; North responde sin
  ops y no supera todavía el cierre durable. El canario durable v1 confirma que
  no hay promoción: Nemotron falla parseo, MiMo no crea el rechazo durable,
  North queda denegado por rol y DeepSeek pasa seed 1 pero falla la aprobación
  en seed 2. Ninguno alcanza 3/3; se conservan cinco recibos diagnósticos y el
  baseline no cambia. No presentar “integrado”
  como gateway anónimo: Zen exige login/API key y su oferta gratuita es temporal
  y solo apta para datos no confidenciales. Ver
  `docs/MODELOS_GRATUITOS_OPENCODE.md`.
  El transporte ya falla cerrado sin `--auto`, impone allowlist MCP positiva y
  registra tokens/caché/razonamiento/sesión para presión de cuota con coste
  marginal cero. Sigue limitado a lectura: permisos de tools no son un sandbox.
  El siguiente experimento específico es CLI efímero frente a server/SDK, no
  una promoción automática a Engineer.
  La ruta complementaria BYOK ya incluye perfiles separados para Gemini Free y
  Groq Free, vault local, health, modelos, usage y cuota. GPT-OSS usa schema
  estricto; Qwen JSON Object Mode validado. GitHub Models/OpenRouter exacto
  quedan como siguiente expansión, nunca como router aleatorio. No
  reemplazar Zen, porque DeepSeek/MiMo directos son de pago y Cohere/NVIDIA no
  aportan una capacidad productiva gratuita estable demostrada.

Objetivo, pendientes, orden de ejecución y criterios de cierre viven únicamente en
`task.md`. El bloque activo es completar matriz E2E y contrato JSON Object de
P0.3, antes de ampliar defaults. No
mantener una segunda lista de tareas en este handoff.

## Riesgos conocidos

- `RunExecutor` concentra muchas políticas; el orden de preflights y gates requiere tests dirigidos.
- El gate profundo valida cobertura y presupuesto, no verdad ni calidad semántica. Debe seguir calibrándose con `benchmark_quorum_plans.py`; no elevar más thresholds basándose en una sola familia.
- Corregido P0.1/F1: `loop-health` conserva actividad reciente como telemetría y solo eleva runs/wakeups con más de 30 minutos; una run activa también cuenta como continuación durable de su raíz. Hay tests separados para trabajo reciente y estancado.
- Runtime MCP mínimo completado con contrato provider-neutral: el rol conserva autoridad y el adapter solo traduce grants. Se exige owner approval, versión, `initialize` stdio, health vigente, rol+`external_mcp` y recibo `tool_access`. Codex usa overrides efímeros, OpenCode configuración inline y allowlist exacta, y Claude `--strict-mcp-config`; Antigravity registra deny hasta soportar aislamiento por run, sin cambiar de Lead ni hacer fallback. Fuentes shell/`npx -y` no se ejecutan.
- Cerrado el ciclo MCP operativo: `readOnlyHint` no concede acceso; Config permite probar, aprobar tools, retirar y reactivar; health caduca en 24 h y el heartbeat prueba como máximo uno vencido por tick, con backoff y retiro al tercer fallo. Contratos rechazados, retirados o ya existentes suprimen propuestas equivalentes. Ejecutable, argumentos y scripts quedan sellados por digest, y cada adapter impone la allowlist o deniega el servidor.
- Detección MCP completada sin auto-instalación: `capability_gap` y bloqueos no verificables generan una sugerencia durable al Lead; señales débiles exigen dos runs de la misma capacidad. El detector no combina huecos distintos, no pisa wakeups existentes y solo el Lead puede elevar la sugerencia al gate del owner.
- Skills aprendidas gobernadas completadas con contrato neutral al proveedor: solo el rol Lead propone y debe adjuntar evidencia; la propuesta no se inyecta hasta aprobación explícita. Hay límites de cantidad, tamaño y presupuesto activo, provenance preservada y controles owner para editar, activar, retirar o borrar. Las directivas del usuario prevalecen por contrato y por orden de prompt.
- P2 de auto-extensión queda completo con un catálogo inicial de tres descriptores oficiales. El catálogo es informativo y rellena propuestas Lead por `catalog_id`; no instala ni aprueba. Los contratos canónicos no admiten overrides y siguen pasando interacción owner, health, digest y allowlist antes de cualquier grant.
- El plan ya tiene contrato durable neutral al proveedor: `aiteam.plan.v1+json`
  vive en las revisiones existentes de `issue_documents`, explicita
  accountability, evidencia, riesgos, rollback, escalado y continuidad, y el
  cockpit lo consume como estructura. Un `run_id` solo puede revisar el plan si
  corresponde al Lead asignado a esa issue. Los comentarios ya no son una vía
  implícita de escritura y la API exige estructura para nuevas revisiones. El
  Markdown de documentos, builtins y adapters antiguos sigue funcionando como
  shim transitorio y se identifica como no estructurado.
- El supuesto hueco de identidad del Lead en quorum fue refutado: `accept_quorum_synthesis` enlaza la run con la issue y exige `run.agent_id == issue.assignee_agent_id`. Falta un test negativo con un segundo `team_lead`, no otra política duplicada en el executor.
- El context curator persiste ahora Markdown más un índice causal v1. Producción
  valida provenance y completitud relacional, no verdad: accountability requiere
  owner/deliverable/accepted_by, escalado metric/threshold/window/action y una
  opción descartada reason. El Markdown conserva el gate histórico ≤30 %; el
  índice tiene cap separado de 4 KiB y la rúbrica oculta lee ambos. Los primeros
  spot-checks reales pasan auth y queue 9/9 en una run cada uno, pero el artefacto
  total ocupa 47,56 % y 54,37 % al contar JSON/UUID. Registrar ese overhead y no
  presentar estructura como retención semántica demostrada universalmente.
- Claude subscription recibe el wake payload variable por stdin; schema y
  system prompt permanecen como argumentos. Esto evita superar el límite de
  `CreateProcess` en Windows al crecer los contratos estructurados, sin cambiar
  parsing, sandbox ni transporte MCP por run.
- El contrato operativo del context curator ya no vive en `RunExecutor`:
  `aiteam/context_curator.py` posee construcción del slice, presupuesto del
  trigger, validación/persistencia, offsets parciales y recovery acotado. El
  executor solo materializa la issue delegada y consume la transición devuelta;
  no mantiene una segunda definición de ratio, rangos o reintentos.
- La rúbrica `multitenant_authorization_v1` produjo un falso −8,69 en la tercera semilla: no reconocía equivalentes válidos como «frontera de enforcement», `policy checks por recurso`, `Deny-by-default` o «pruebas negativas». La v2 añade esas anclas y tests dirigidos; los resultados v1 y v2 se conservan separados. El 100→100 de v2 es efecto techo, no prueba de que Plan B sea idéntico ni de calidad perfecta.
- La calibración P0 de quorum ya tiene criterio y muestra mínima en las dos familias pendientes: tres sesiones aceptadas por familia, dos proveedores válidos, provenance completa, degradaciones fuera del A/B y mediana+rango. Failover: mediana `+6,52`, rango `-8,70..+8,70` (n=4). Multi-tenant v2: mediana `+8,69`, rango `0..+8,70` (n=3), pero solo 2/3 Plan B superan hard gate. Se mantienen thresholds; `accepted` en SQLite prueba cierre del protocolo, no aprobación semántica externa.
- Anthropic API debe recibir `quorum_review` completo: su builder genérico resumía el payload a 800 caracteres y ocultaba el contrato. Las auditorías quedan acotadas a 1-3 findings para preservar profundidad sin agotar el cierre JSON/AGENT-REPORT; la semilla multitenant posterior verificó ambos proveedores al primer intento.
- Un quorum de un senior es una degradación de redundancia aceptada por disponibilidad, no equivalente empíricamente a dos proveedores. Exponerlo claramente en UI/telemetría si se usa con frecuencia.
- El bloque principal quedó consolidado en `codex/orchestration-hardening`; `.claude/skills/aiteams-frontend/` permanece sin seguimiento y fuera de los commits por origen no atribuido.
- La telemetria de usage de `antigravity_subscription` debe verificarse antes de comparar costes: `agy --print` autentica y responde, pero no entrega usage comparable en su salida normal.
- Revalidado el 2026-07-21 con `agy 1.1.5`: `--help` y el changelog no ofrecen salida headless estructurada de tokens por run. La cuota existe en el TUI, pero no es un recibo atribuible; no parsear almacenes internos ni fabricar estimaciones.
- Antigravity CLI 1.1.4 es un segundo proveedor operativo para quorum: existe una sesión aceptada cross-provider y una contribución válida con Gemini 3.1 Pro High. El adapter transporta payloads largos mediante archivo temporal autorizado, conserva plan+sandbox y normaliza solo los envelopes observados. Sigue sin usage/cost_event comparable y el cumplimiento de `AGENT-REPORT` presenta varianza en ambos proveedores.
- El blueprint debe conservar el rol semántico `quorum_auditor` aunque la sub-issue sea `reviewer`; de lo contrario el selector baja erróneamente a Flash. Pro es el modelo canónico de hiring para Antigravity quorum.
- Nuevas anclas reales: `config_redactor` empata 3/3; `tenant_authorizer` favorece a Codex directo 4/5 frente a `full_team` 2/5; `release_notes_indexer` empata 7/7 y `deployment_wave_planner` empata 16/16 en dos semillas. En deployment, equipo promedia 3,73× la entrada y 4,39× el tiempo de solo, converge 1/2 y conserva accountability independiente; esa garantía puede ser requerida aunque no mejore el juez, pero no es una ventaja de calidad demostrada.
- `benchmarks/results/quorum-sqlite-seed-1.json` es evidencia de una run incompleta, no un resultado A/B: Plan A obtuvo 91,3 % y el segundo auditor falló con `subscription_cli_not_found`.
- `benchmarks/results/quorum-provider-failover-local-seed-1.json` es una segunda evidencia incompleta pero útil: Plan A obtuvo 78,26 %, Codex aportó una auditoría válida y Qwen 32B consumió 4.100 tokens de entrada/164 de salida en dos intentos sin cumplir `AGENT-REPORT`; la sesión terminó `degraded` con escalado durable. El runtime reintenta una sola vez, excluye ese reintento del guard de evidencia idéntica y cancela wakeups sobrantes al degradar.
- `benchmarks/results/quorum-provider-failover-gemma-seed-1.json` confirma que Gemma 4 local tampoco es todavía un segundo auditor utilizable: Codex produjo el único aporte válido; Gemma terminó primero `skipped` y después `failed` por selección de herramienta. El runtime continúa ahora auditores `skipped`/`failed`, normaliza fallos declarados sin código a `agent_reported_failure` y degrada/escalada de forma durable al agotar el reintento. Es evidencia de failover, no una semilla A/B aceptada.
- Anthropic API ya es segundo proveedor operativo. `quorum-provider-failover-anthropic-seed-2.json` mejora 60,87→65,22 (+4,35); seed 3 regresa 86,96→78,26 (−8,70). Ambas sesiones terminaron `accepted`, con dos aportes provider-diversos y 14 céntimos atribuibles al auditor Anthropic. Seed 1 es un diagnóstico incompleto: el health ranking eligió Anthropic también como Lead y agotó sus 4.096 tokens antes de crear sesión.
- Seed 5 de provider failover es diagnóstica, no A/B: Plan A puntúa 91,30, Anthropic aporta válido por 29 céntimos y Codex subscription falla por cuota agotada. El error ya no se colapsa en `subscription_cli_nonzero_exit` ni consume un reintento inmediato; degrada con `auditor_provider_usage_limit` y wakeup durable al Lead.
- Seed 4 mejora 91,30→100 (+8,70), termina en cuatro runs sin intervención y
  atribuye 19 céntimos al auditor Anthropic. Esa evidencia histórica se obtuvo
  con Sonnet/Opus 4.5; la política actual selecciona Opus 4.8 para Lead/quorum,
  Sonnet 5 para Tier 2 y Haiku 4.5 para Tier 3. Los modelos nuevos aún requieren
  calibración equivalente antes de atribuirles una mejora.
- Seed 6 de provider failover mejora 82,61→91,30 (+8,69), supera el hard gate y cierra en cuatro runs: 89.588 tokens de entrada, 10.555 de salida, 237,1 s y 28 céntimos. La nueva seed 4 multi-tenant mejora 82,61→91,30 (+8,69), con ambos auditores válidos y 29 céntimos, pero conserva el fallo duro `tenant_boundary`; el root queda `in_progress` con wakeup durable y `orchestrator_evals` confirma liveness sano.
- El apartado Equipo aprovisiona ahora Quorum Auditor 1/2 mediante un endpoint canónico idempotente, no mediante un prompt `full_team`; conserva los IDs que consume el runtime y oculta las tarjetas cuando ya están contratados.
- Corregido un hueco descubierto por seed 2: cuando Codex entregaba `AGENT-REPORT` dentro de `add_comment`, la contribución se persistía después del auto-wakeup y el gate quedaba `reviewing`. Cada contribución válida evalúa ahora inmediatamente la continuación durable.
- El bootstrap de quorum asigna ahora proveedores distintos por construcción cuando existen perfiles suficientes; antes ambos auditores elegían silenciosamente el mismo primer perfil senior y la diversidad solo fallaba al evaluar el gate.
- `benchmarks/context_quality/auth_migration_*` aporta el primer canario causal: la referencia conserva 9/9 anclas obligatorias con ratio 26,57 %. El primer intento (35,84 %) fue rechazado por presupuesto, confirmando que retención y compresión son gates independientes.
- El canario causal v1 añade dos recibos reales:
  `context-curator-auth-codex-causal-v1-seed-3.json` y
  `context-curator-queue-codex-causal-v1-seed-1.json`. Ambos conservan 9/9,
  cierran al primer intento y separan ratio Markdown de overhead del índice.
- El QuorumStepper fue comprobado contra esa SQLite real: distingue ahora `degraded` de “No requerido”, expone `1/2` aportes, gate pendiente, causa y provenance del aporte válido. Evidencia visual local en `output/playwright/quorum-stepper-degraded.png` (no versionada).
- El benchmark ya tiene resultados versionados y juez oculto aislado. El harness
  de código v4 declara suite conductual oculta, Ruff y evaluación estructural
  independiente; `scripts/benchmark_integrity.py` impide concluir con matrices
  brazo×semilla incompletas, evidencia no comparable o quorum sin muestra,
  provenance, hard gates y signo estables. La serie histórica
  `accessible_checkout_form` supera el contrato 2×2; `provider_failover` no lo
  supera (cuatro sesiones aceptadas, dos incompletas y signo inestable).
- La higiene local quedó endurecida después de encontrar 11,1 GB en `.pytest-workspace-tmp`: `pytest_local.bat` y el wrapper estable crean sesiones aisladas, limpian en un proceso posterior al cierre de handles SQLite, desactivan cache/bytecode y preservan el exit code de pytest. `scripts/cleanup_test_artifacts.py` permite el barrido manual.
- Los documentos históricos de migración pueden contener estados de fase ya superados; el banner del documento indica cómo leerlos.
- Prompts externos o antiguos que mencionen `AITEAM_AUTO_QUORUM` están obsoletos: el único disparador vivo es el perfil explícito `lead_quorum`.
- Windows puede retener handles de SQLite o temporales de pytest. El 2026-07-21
  se confirmó que `.pytest-workspace-tmp` y `.pytest-user-config-tmp` están
  ausentes; quedan dos directorios de `.tmp_pytest` del 2026-04-02 con ACL
  privadas que impiden enumerarlos. El intento de borrado delimitado fue
  rechazado por la política del entorno antes de ejecutarse; no se eliminó nada.

## Verificación

Suite completa verificada el `2026-07-21`:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# 1164 passed in 133.58s
```

Canario e2e:

```powershell
.\scripts\python_local.bat scripts\e2e_canary.py
.\scripts\python_local.bat scripts\e2e_quorum_canary.py
.\scripts\python_local.bat scripts\e2e_solo_lead_canary.py
```

Auditoría de un proyecto capa 2:

```powershell
.\scripts\python_local.bat scripts\audit_project_db.py "<workspace>"
```

No sustituir una ejecución actual por la cifra de este documento: registrar fecha y resultado cuando cambie sustancialmente la suite.
