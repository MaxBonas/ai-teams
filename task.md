# Plan y trabajo vigente

Fecha: `2026-07-22`

Este es el único documento de objetivos, backlog y orden de ejecución. El plan
rector arquitectónico está en `docs/MIGRATION_PAPERCLIP.md`; las decisiones ya
cerradas viven en `docs/HISTORY.md`.

## Objetivo del producto

AI Teams debe ser un control plane Paperclip-like para equipos de programación:

- SQLite como fuente durable única para issues, runs, wakeups, interactions,
  reports, costes, actividad y recovery;
- Lead-first: el usuario entrega un objetivo y el Lead lo mantiene vivo hasta
  cerrarlo o solicitar una decisión humana real;
- perfiles `solo_lead`, `lead_quorum` y `full_team` elegidos de forma
  proporcional al riesgo y a la necesidad de accountability;
- hiring dinámico y económico, reservando modelos fuertes para planificación,
  supervisión y alto riesgo;
- evidencia y revisión suficientes para reducir riesgo, sin burocracia en
  tareas simples;
- adapters API y suscripción independientes, con coste, provenance, health y
  recuperación observables.

## Estado de partida

La migración estructural está completada. El camino activo es
`HeartbeatLoop` → `HeartbeatScheduler` → `RunExecutor` sobre SQLite. Checkout,
dependencias, wakeups, interactions, hiring, reports, gates, cockpit v2,
canarios y benchmarks existen. El trabajo actual es calibrar cuándo compensa
cada perfil, endurecer puntos concretos y terminar extensiones sin reabrir el
orquestador legacy.

Última suite completa registrada (`2026-07-22`): **1189 passed en 133,26 s**.
Esta cifra es evidencia histórica; volver a ejecutar y actualizarla cuando un
cambio material lo justifique.

## Plan de ejecución

### P0 — Calibración antes de cambiar política

- [x] **Ampliar benchmarks del selector** con al menos otra familia media
  reversible y otra compleja de distinta naturaleza, usando varias semillas.
  Comparar calidad oculta, convergencia, runs, tokens, coste y tiempo. Progreso:
  `accessible_checkout_form` añade frontend multisuperficie con dos semillas e
  `inventory_snapshot_diff` añade datos medios reversibles con dos semillas.
- [x] **Decidir con evidencia si relajar el default conservador**: se mantiene.
  No inferir `independent_verification` solo por complejidad aparente;
  conservarla cuando sea requisito explícito de accountability. Las nuevas
  familias no muestran mejora de calidad del equipo y sí sobrecoste/rework.
- [x] **Ampliar el benchmark real de quorum** con Codex subscription y
  Anthropic API: `provider_failover` suma cuatro sesiones aceptadas y
  `multitenant_authorization_v2` tres. Las runs degradadas y la familia SQLite,
  que aún no alcanza la muestra mínima, permanecen como evidencia separada de
  disponibilidad/liveness y no entran en el delta A/B.
- [x] **Establecer criterio de suficiencia estadística** antes de modificar
  thresholds: mínimo tres sesiones aceptadas por familia, dos proveedores
  válidos con provenance completa, degradaciones fuera del delta A/B y reporte
  de mediana más rango. No cambiar política si el signo es inestable, aparece
  efecto techo o el Plan B no supera consistentemente los hard gates.

Decisión: mantener thresholds. En `provider_failover` la mediana del delta es
`+6,52` puntos pero el rango es `-8,70..+8,70`; en
`multitenant_authorization_v2` la mediana es `+8,69` y el rango `0..+8,70`, pero
solo dos de tres Plan B superan el hard gate. La aceptación durable del quorum
demuestra coordinación y provenance, no calidad semántica suficiente por sí
sola.

Criterio de cierre P0: existe evidencia multi-familia suficiente para mantener
o cambiar el selector, y la decisión queda reflejada en tests, documentación y
telemetría sin convertir un caso aislado en regla universal.

### P0.1 — Correcciones de la auditoría independiente

Antes de continuar con nuevas capacidades P2, cerrar en este orden los huecos
confirmados por la auditoría del `2026-07-20`:

- [x] **Separar actividad viva de liveness defectuoso en `loop-health`**. Una
  run `queued/running` o wakeup `claimed/running` reciente es trabajo normal,
  no un zombi. Aplicar una antigüedad coherente con lease/heartbeat/recovery y
  conservar `stranded_nonterminal_roots` como señal pura. Añadir tests de run
  reciente sin alerta y run antigua con alerta.
- [x] **Cerrar la autorización MCP frente a inventarios no confiables o
  cambiantes**. `readOnlyHint` es información para revisión, no una frontera de
  seguridad. Persistir una allowlist positiva aprobada por el owner; tools
  nuevas/no aprobadas quedan denegadas incluso con `repo_write`. Exigir
  enforcement positivo del adapter o denegar el servidor para esa run. Añadir
  TTL de health, pero no tratarlo como sustituto de la allowlist ni como defensa
  completa frente a cambios entre health y ejecución.
- [x] **Corregir el contrato HTTP de health MCP**: inexistente `404`, aún no
  aprobado `409`, contrato inválido `400/422` y probe fallido como resultado
  estructurado/auditado. Cubrir todos los caminos negativos del endpoint.
- [x] **Pinear la identidad del artefacto MCP ejecutado**, no solo la versión
  auto-reportada ni únicamente el binario intérprete. La identidad debe cubrir
  ejecutable resuelto, contrato de argumentos y script/paquete verificable;
  un cambio invalida health y grant hasta nueva aprobación.
- [x] **Documentar por test la identidad del Lead en quorum**. El hallazgo F7
  fue refutado: la capa durable `aiteam.db.quorum_sessions` exige que el agente
  de `synthesis_run_id` sea `issues.assignee_agent_id`; el executor no duplica
  esa política. `test_persistence_rejects_synthesis_not_owned_by_configured_lead`
  protege la invariante con un agente no asignado y una reproducción temporal
  confirma que un segundo `team_lead` tampoco puede aceptar la sesión.
- [x] **Añadir la regresión nominal con un segundo `team_lead` no asignado**.
  No corrige una vulnerabilidad abierta —la persistencia ya lo rechaza—, pero
  alinea el nombre del escenario auditado con la evidencia automatizada y evita
  que una futura refactorización confíe solo en el rol. Cerrado por
  `test_persistence_rejects_second_team_lead_not_assigned_to_issue`.

Criterio de cierre P0.1: la operación normal no genera fatiga de alertas, una
tool MCP no puede ampliar permisos mediante hints o cambios de inventario, los
errores de activación tienen contrato HTTP estable y la identidad del Lead queda
protegida por un test explícito.

### P0.2 — Renovación de modelos y economía por canal

- [x] **Investigar e integrar el catálogo gratuito actual de OpenCode Zen**.
  `docs/MODELOS_GRATUITOS_OPENCODE.md` puntúa y clasifica Nemotron 3 Ultra
  (86/Tier 1), DeepSeek V4 Flash (82/Tier 2), MiMo V2.5 (80/Tier 2) y North
  Mini Code (74/Tier 3). El perfil built-in `opencode_zen_free` reutiliza la
  sesión del CLI, descubre IDs con `opencode models opencode` y nunca embebe ni
  copia credenciales. La integración es read-only y solo admite roles de
  planificación, auditoría, review/QA y scouts; Engineer queda excluido.
- [x] **Calibrar OpenCode Zen Free en ejecución real antes de promoverlo a
  política automática**. OpenCode `1.18.4` está instalado y la sesión OAuth
  local enumera cinco IDs vivos, incluido el nuevo `laguna-s-2.1-free`. El
  screening público de una semilla ya valida transporte, contrato y usage para
  Nemotron, DeepSeek, MiMo y Laguna; North diagnostica el defecto pero devuelve
  cero ops y todavía no supera el cierre durable. El canario durable v1 de
  reviewer descarta la promoción: Nemotron falla el parseo en seed 1, MiMo no
  produce rechazo durable, North queda correctamente denegado por rol y
  DeepSeek pasa seed 1 pero no aprueba la corrección en seeds 2–3. La matriz
  final añade Laguna: 0/3 ciclos completos, un rechazo correcto, dos fallos de
  parseo y un timeout de aprobación; mediana 236,094 s frente a 61,375 s de
  DeepSeek. Ningún modelo alcanza 3/3. Laguna queda visible manual-only y
  probe-gated, sin routing automático; el agregado
  `opencode-durable-review-v1-laguna-vs-deepseek-aggregate.json` conserva
  `default_change_allowed=false`. Repetir solo tras cambio de catálogo/modelo,
  CLI o contrato. Los datos siguen siendo públicos/no confidenciales.
- [x] **Integrar OpenCode en los afinamientos de gobierno existentes**. El
  runtime ya falla cerrado sin `--auto`, traduce grants MCP a una allowlist
  positiva por tool, bloquea MCP ajenos y conserva roles read-only. El JSONL
  agrega input/output, razonamiento, caché, total e ID de sesión; quota pressure
  registra tokens/runs/duración/429 bajo el perfil exacto sin convertir cero
  coste marginal en consumo ilimitado. Engineer continúa excluido: tool
  permissions no equivalen a un sandbox de sistema operativo.
- [x] **Evaluar el transporte OpenCode server/SDK frente al CLI efímero**.
  Medir salida JSON Schema, cancelación, hangs, health MCP, continuidad por ID y
  aislamiento entre issues. No activar reanudación ni daemon compartido hasta
  superar varias semillas de memoria/override/contaminación y recovery; no
  considerar esta mejora una solución al sandbox de escritura. Progreso:
  `benchmark_opencode_transport.py` completa un A/B 3×2 con DeepSeek y datos
  públicos: ambos brazos pasan 3/3, seis sesiones son distintas y attached baja
  la mediana de 7,50 s a 2,92 s con tokens equivalentes (~4,84k). El servidor
  loopback usa Basic Auth, policy sin tools y teardown verificado.
  `benchmark_opencode_server_resilience.py` valida además el SDK oficial 1.18.4:
  observa `busy`, aborta en 260 ms, vuelve a `idle`, conserva health, completa
  una inferencia posterior en la misma sesión, la borra y cierra el servidor.
  JSON Schema falla con `StructuredOutputError` aunque el texto sea JSON válido.
  `benchmark_opencode_server_faults.py` suspende el proceso nativo: el puerto
  queda abierto pero health expira en 532 ms; tras terminarlo, reinicia en el
  mismo puerto, recupera el mismo ID `idle` y completa el marcador en 6,172 s.
  El fixture MCP local completa `initialize` y `tools/list`, conserva dos tools
  en inventario, permite exactamente la aprobada, deniega la otra y no deja
  procesos. La matriz final añade tres semillas de memoria/override y sesión
  fresca: pasa 3/3 con seis IDs únicos, historial aislado, revocación correcta y
  borrado. El mismo schema falla en DeepSeek, Laguna, MiMo, Nemotron y North:
  todos devuelven `StructuredOutputError`, ninguno rellena `info.structured`.
  Se conserva CLI efímero y no se diseña supervisor productivo: el transporte
  queda evaluado con decisión negativa, no pendiente de más runs idénticas.
  Recibo final: `opencode-session-isolation-v1.json`.
  El primer harness terminaba solo el shim `.cmd` y dejó dos procesos hijo;
  quedó corregido usando el binario nativo y gate de puerto cerrado.
- [x] **Comparar Zen con APIs gratuitas BYOK**. La decisión es híbrida, no una
  migración total: una API directa ofrece mejor provenance, usage, cuota y
  control de tools, pero DeepSeek V4 y MiMo V2.5 directos son de pago, Cohere es
  evaluación y NVIDIA no publica una capacidad gratuita estable verificable.
  Conservar Zen para esos endpoints; priorizar Gemini API Free y Groq Free,
  dejar GitHub Models/OpenRouter exacto como fallback de baja frecuencia y
  descartar el router aleatorio y Hugging Face Free como capacidad base.
- [x] **Implementar la base `free_api_byok`**. `gemini_api_free` y
  `groq_api_free` son perfiles built-in separados de pago/CLI; usan referencias
  del vault, health real, modelos exactos, privacy label, usage, 429 y quota
  pressure por perfil. El runtime `openai_compatible_api` soporta JSON Schema
  estricto para GPT-OSS y JSON Object Mode validado para Qwen. Equipo permite
  guardar las keys Groq y Google Free en slots distintos de los perfiles
  pagados; una credencial no activa ambos canales por inferencia.
  `actual_cost_cents=0` no elimina tokens, runs ni duración.
- [ ] **Extender BYOK gratuito solo con catálogo ejecutable demostrado**.
  Evaluar GitHub Models y OpenRouter por ID exacto, nunca router aleatorio;
  descubrir modelos con la key real, verificar salida estructurada y persistir
  límites observados. Añadir rate-limit headers de API a la telemetría cuando el
  helper HTTP pueda conservarlos sin exponer secretos. Calibrar Gemini/Groq por
  rol antes de ampliar `supported_roles` o hacerlos defaults. Auditoría local
  del 2026-07-22: no existen keys de esos cuatro perfiles; los tokens de `gh`
  carecen de `models:read`. No se crean perfiles decorativos. El helper ya
  conserva los headers oficiales RPD/TPM de Groq y normaliza los hosts futuros
  de GitHub Models/OpenRouter para governor y telemetría; catálogo, schema y
  límites propios siguen bloqueados hasta disponer de credenciales válidas.
- [x] **Reinvestigar los catálogos actuales con fuentes oficiales y probes
  locales**. OpenAI queda en Sol/Terra/Luna; Anthropic en Opus 4.8/Sonnet
  5/Haiku 4.5 con Fable 5 como escalado; Gemini en 3.1 Pro Preview/3.5
  Flash/3.1 Flash-Lite. `agy 1.1.5 models` confirma 11 IDs catalogados: los
  ocho anteriores de Gemini/Claude 4.6/GPT-OSS y tres Gemini 3.6 sujetos a
  probe exacto; Equipo conserva etiquetas legibles aparte.
- [x] **Actualizar selección, defaults y FinOps por adapter**. Los tiers se
  resuelven por rol dentro del canal configurado; API conserva precio marginal
  y tramos de contexto, suscripción/local registran 0 céntimos marginales. Los
  adapters locales mantienen el modelo instalado/configurado y nunca descargan
  ni autoascienden por catálogo.
- [x] **Impedir una promoción automática insegura a Fable 5**. Permanece
  visible para selección manual, pero el default Anthropic Tier 1 es Opus 4.8
  hasta gobernar retención obligatoria, refusals/fallback, elegibilidad y coste.
- [x] **Hacer que el catálogo de Equipo represente capacidad ejecutable**.
  Cada opción conserva su nombre exacto por adapter, pero queda deshabilitada
  si el CLI/runtime no la enumera, el perfil está bloqueado o una run devolvió
  `model_unavailable`. Las runs completadas verifican el par perfil+modelo; el
  hiring automático y el guardado de Equipo usan esa misma evidencia. `agy
  models` aporta 11 opciones actuales; las tres Gemini 3.6 permanecen
  manual-only/probe-gated y las otras ocho incluyen `Gemini 3.1 Pro (Low)`.
  Ollama/LM Studio solo habilitan modelos instalados.
- [ ] **Calibrar los modelos nuevos por contrato de rol**, no con benchmarks de
  vendor: Sol/Terra/Luna, Opus/Sonnet/Haiku y Pro/Flash/Flash-Lite deben
  compararse con los baselines locales en tareas de Lead, coding, review y
  scouts antes de modificar gates o cascadas. Progreso: Antigravity 1.1.5 ya
  completa el screening estructural con **27 runs**, tres muestras por par y
  control contra el baseline real de review. Se mantienen Pro High, Flash High
  y Flash Low. Sonnet 4.6/coding pasó después su prueba conductual y es ya el
  default de Engineer; Flash Medium/review (empate, -1,48 s) pasa a validación
  económica. Opus 4.6, Pro Low y GPT-OSS no mejoran sus baselines. El agregado
  declara `default_change_allowed=false`: falta validar esos dos candidatos con
  evidencia conductual independiente y completar el resto de adapters. El
  inventario vivo del `2026-07-21` añadió Gemini 3.6 Flash High/Medium/Low.
  High y Low fueron enumerados pero rechazados por submit; quedan visibles,
  manual-only y no seleccionables hasta un probe exacto. Medium completó review 3/3 con 100 %, igual
  que 3.5 High, pero fue 0,407 s más lento en mediana. Se conserva 3.5 High y
  3.6 Medium pasa al canario durable sin promoción automática; discovery por sí
  solo tampoco lo habilita en Equipo y un probe exacto verificado sí.
- [x] **Validar Sonnet 4.6 contra Flash High en coding conductual**. Ambos pasan
  9/9 tests ocultos en tres semillas. Sonnet cierra 3/3, Ruff limpio 3/3 y
  mediana 51,14 s; Flash cierra 2/3, Ruff limpio 1/3 y mediana 105,48 s. El
  agregado v3 supera la auditoría canónica de matriz y promociona Sonnet para
  `engineer`/`software_engineer` de Antigravity. El primer A/B se repitió tras
  corregir envelopes `text + ops`/stdout ruidoso; esos intentos son diagnóstico
  de transporte y no evidencia de calidad.
- [x] **Validar Flash Medium contra Flash High en review durable** sobre defectos
  causales y aceptación/rechazo real. Exigir al menos tres semillas, mismo diff
  y mismo juez. Medir runs y duración como presión de cuota; no inventar tokens
  ni coste API. Cerrado con el canario v4: ambos brazos rechazan el defecto,
  materializan el fix mediante el Lead y aprueban la corrección en 3/3 semillas.
  Flash Medium tarda 43,078 s medianos frente a 99,999 s de Flash High, pero sin
  tokens ni denominador de cuota la latencia aislada no autoriza promoción; se
  conserva Flash High. Recibo: `antigravity-durable-review-v4-aggregate.json`.
- [ ] **Probar Luna como `context_curator` contra el baseline causal GPT-5.5**
  en auth y queue, con varias semillas, anclas, ratio total, runs y tiempo.
  El primer canario auth del 2026-07-20 no alcanzó la evaluación: Codex CLI
  `0.128.0` rechazó `gpt-5.6-luna` porque el catálogo local requiere un cliente
  `0.145.0`. Se conserva como evidencia diagnóstica, no como resultado A/B, en
  `benchmarks/results/context-curator-auth-codex-luna-seed-1.json`. Hasta
  actualizar el CLI y superar auth+queue, Codex curator conserva `gpt-5.5`
  aunque sea un rol Tier 3.
- [x] **Asignar cadencia y owner al drift de catálogos/modelos**. Ejecutar
  inventario autenticado y matriz hermética al cambiar versión de CLI/provider
  y, como mínimo, en una revisión mensual mientras existan modelos preview o
  gratuitos temporales. Un alta, retirada o cambio de ID debe abrir calibración
  por par perfil+modelo+rol; discovery nunca autoriza defaults por sí solo.
  Owner: `AI Teams maintainer`. `audit_model_catalog_drift.py` fija la cadencia
  mensual + evento, compara IDs exactos de Antigravity/OpenCode, conserva
  exclusiones con disposición explícita y ejecuta la matriz hermética. El
  recibo `model-catalog-drift-2026-07-22.json` pasa 3/3 gates: 11 IDs
  Antigravity, cinco Zen declarados —Laguna manual/probe-gated tras fallar 0/3
  review durable—, Big Pickle `rejected` y Codex `cli_update_required` como
  atención separada.
- [x] **Implementar presión y forecast de cuota por perfil de suscripción**.
  `run_adapter_profiles` congela el perfil real por run y
  `subscription_quota_snapshot` agrega runs, duración, usage disponible y
  errores `subscription_cli_usage_limit`. El forecast solo aparece si el owner
  configura `config.subscription_quota` con `unit`, `limit` y `window_hours`;
  si falta capacidad demostrada devuelve `capacity_unknown`, sin porcentaje ni
  ETA inventados. Codex puede usar tokens durables; Claude hace lo mismo cuando
  su recibo los expone; Antigravity usa únicamente runs/segundos como proxy
  explícito del owner. `loop-health` eleva agotamiento observado o presión
  configurada y lleva a Runs. Ninguna señal se convierte a coste API ni se
  comparten pesos entre perfiles. OpenCode añade tokens/caché/razonamiento desde
  `step_finish` y conserva coste marginal cero; `free_gateway` se normaliza como
  canal durable de suscripción conservando profile/provider/model exactos, sin
  reconstruir la restricción SQLite existente.
- [x] **Cerrar el ciclo de lifecycle de modelos preview/retirados**. Health
  valida el ID, registra `model_unavailable`, impide nuevas selecciones y evita
  que hiring las fije. La issue queda bloqueada y el control plane propone el
  fallback ejecutable menos disruptivo dentro del mismo perfil: preserva
  familia antes que tier, excluye modelos manual-only y explicita ambos cambios.
  Una interacción idempotente exige decisión del owner; aceptar actualiza el
  agente y reencola la issue sin otra llamada LLM, rechazar conserva el bloqueo.
  Si no existe candidato, se despierta al supervisor sin cambiar de adapter.

Criterio de cierre P0.2: cada rol recibe un modelo ejecutable y calibrado en su
canal; API y cuota de suscripción se predicen con unidades separadas; ningún
modelo preview, local no instalado o Fable no gobernado entra silenciosamente.
El lifecycle queda cerrado; la calibración multi-modelo y el canario Luna tras
actualizar Codex CLI siguen siendo trabajo de evaluación, no deuda del fallback.

### P0.3 — Compatibilidad efectiva adapter × modelo × rol

Bloque activo y previo a ampliar catálogos o promover nuevos defaults. La
auditoría del `2026-07-21` confirma que `supported_roles` filtra perfiles en el
hiring automático, pero `best_for` solo ordena modelos. Crear/editar agentes,
el selector de Equipo, la edición de una propuesta y el fallback validan
ejecutabilidad, no compatibilidad dura con el rol. Además, un perfil de Lead
explícito pero incompatible puede acabar silenciosamente en `lead_builtin`.

Orden interno: (1) vocabulario e identidad, (2) decisión pura de
compatibilidad, (3) enforcement backend/pre-run, (4) proyección en Equipo y
(5) catálogo vivo/E2E. No se ampliarán defaults antes de cerrar 1–3.

- [x] **Unificar la taxonomía de roles sin romper proyectos persistidos**.
  `aiteam.policies` es fuente canónica de aliases, tier y estado. `worker` queda
  inequívocamente en Tier 3; `qa` es un guardrail condicional, no miembro del
  `full_team`; `test_runner` es determinista; `researcher` y `quorum_senior`
  quedan legibles como legacy. Ranking y API normalizan aliases sin reescribir
  la identidad almacenada.
- [x] **Separar proveedor, perspectiva y capacidad**. La identidad derivada
  distingue organización del canal, vendor del modelo, transporte, perspectiva
  y pool de cuota. Quorum y review crítico exigen vendor/perspectiva distintos:
  Codex + OpenAI API sobre GPT ya no cuenta como diversidad; Antigravity sobre
  Claude tampoco es independiente de Anthropic. Cuota/rate-limit conserva el
  perfil/key como pool separado y los registros históricos se interpretan sin
  migración destructiva.
- [x] **Dar metadata de gobernanza a perfiles personalizados**. La API conserva
  y valida `supported_roles`, `data_policy`, `privacy_note`, `capabilities`,
  `workspace_mode`, `mcp_transport` y `structured_output`, y proyecta identidad
  normalizada. Los aliases se canonicalizan y un rol desconocido se rechaza.

- [x] **Crear una única decisión pura de compatibilidad y proyectarla por API**
  para `(profile_id, model, role, run_profile, criticality,
  data_class, required_capabilities)`. Debe devolver `allowed`, código estable,
  explicación en español, roles/modos permitidos y alternativas ejecutables.
  `aiteam.model_compatibility` no consulta DB/red y separa tier, capacidades,
  workspace, MCP, salida estructurada, criticidad y datos. El catálogo y
  `POST /api/user-adapters/compatibility` exponen la misma decisión y alternativas
  sin colapsarla en `available`. La metadata admite allow/deny por rol,
  criticidad máxima, salida estructurada y modos de run; `best_for` sigue siendo
  solo recomendación. Perfiles custom pueden declarar un catálogo gobernado.
- [x] **Aplicar el gate en todas las rutas de mutación y justo antes de una
  run**: bootstrap del Lead, `POST/PATCH /api/agents`, aceptación/editado de
  hiring, reconcile, selección automática y lifecycle/fallback. Una selección
  explícita incompatible responde HTTP 422 con el código específico de la
  dimensión fallida (`model_role_incompatible`, `model_tier_insufficient`,
  `workspace_write_required`, etc.; 400 queda reservado a configuración malformada),
  con causa y alternativas; nunca degradar a builtin, cambiar de adapter ni
  esperar a que falle la run. Auditar también configuraciones antiguas al
  cargarlas y bloquear la issue con continuación owner si ya no son válidas.
  Implementado mediante `aiteam.compatibility_service`: el contexto durable se
  hereda por la jerarquía de issues, las propuestas editadas se validan antes
  de abrir su transacción y el preflight registra interaction, actividad y
  `tool_access=denied` sin invocar el modelo.
- [x] **Hacer que Equipo informe en vez de ocultar**. Perfiles y modelos
  incompatibles permanecen visibles pero deshabilitados, con razón concreta
  (`solo lectura`, `sin MCP gobernado`, `no calibrado para criticidad alta`,
  `datos confidenciales`, `modelo ausente`, etc.). El picker de Lead debe
  considerar también `solo_lead`/`lead_quorum`; el de cada miembro debe usar la
  misma respuesta que valida el guardado. Corregir el cache al cambiar rol,
  perfil, criticidad o modo del proyecto. El catálogo por rol conserva también
  modelos no ejecutables, anota compatibilidad sin pisar health y la UI cachea
  por perfil+rol+run profile+criticidad+clasificación. Equipo e hiring muestran
  cada opción deshabilitada con su motivo e impiden guardar/aceptar un deny
  conocido; el backend vuelve a resolverlo como autoridad final.
- [x] **Resolver la semántica real de escritura y MCP por transporte**. Las
  APIs actuales sí materializan `write_file`/`append_file`/`delete_file` bajo
  RBAC, por lo que el warning legacy “API-only no puede escribir” contradice al
  executor y debe retirarse o convertirse en un check de capacidad real. Retirar
  también la penalización fija `-30` y el reconcile que migra juniors de API a
  CLI solo por tipo de transporte; coste/cuota y capacidad se rankean después
  del hard gate. En cambio, OpenCode Zen continúa read-only y queda prohibido para Engineer,
  Worker y Lead en `solo_lead`. Los adapters API no reciben MCP externo hoy:
  bloquear `mcp_operator` o cualquier asignación que requiera `external_mcp`
  hasta implementar un loop gobernado, sin confundir tools del proveedor con
  grants de AI Teams.
- [x] **Codificar la matriz provisional de perfiles gratuitos**:
  Nemotron puede optar a Lead/arquitectura/quorum y revisión read-only;
  DeepSeek V4 Flash y MiMo V2.5 a review/QA; North Mini a scouts/curator.
  Gemini 3.5 Flash Free y GPT-OSS 120B a review/QA; Gemini Flash-Lite, Qwen
  3.6 y GPT-OSS 20B a scouts/curator. Hasta superar canarios locales, bloquear
  Lead/quorum en Gemini/Groq Free y review crítico en los modelos Tier 3. Una
  categoría Tier 1/2/3 describe capacidad/coste, pero no concede por sí sola
  un rol, escritura, MCP ni acceso a datos.
- [ ] **Calibrar con canarios vivos las promociones gratuitas provisionales**.
  Mantener bloqueos actuales hasta demostrar por par exacto perfil+modelo+rol
  que el contrato, la criticidad y la recuperación funcionan; un health del
  perfil o un benchmark de otro transporte no sirve como evidencia.
- [x] **Cerrar catálogo y health por modelo visible**. Los perfiles API no
  deben marcar todo su catálogo como ejecutable solo por ser un ID estático:
  descubrir con la key del owner cuando el proveedor lo permita, intersectar
  con la allowlist aprobada y ejecutar un probe estructurado por modelo antes
  de prometer que funciona. Conservar estados distintos `catalogued`,
  `verified`, `rate_limited`, `retired` e `incompatible`; una prueba del modelo
  default no verifica los demás. Discovery autenticado y probe estructurado se
  persisten por separado; `available` expresa presencia y `selectable` exige
  evidencia ejecutable exacta. OpenAI, Anthropic, Gemini y Groq enumeran con la
  key del owner y paginación acotada; la allowlist viva se intersecta con el
  catálogo gobernado. Equipo permite probar cada modelo y el preflight bloquea
  sin consumo, con continuación owner, si falta evidencia.
- [x] **Endurecer modelos con contrato estructurado parcial**. El boundary API
  valida recursivamente `submit_work` completo: tipos, enums, requeridos,
  mínimos y propiedades extra; recuperar un objeto JSON ya no demuestra el
  contrato. Qwen/JSON Object dispone de un único repair acotado a 12 KB, suma
  el usage de ambos intentos y solo se acepta si conserva exactamente `ops` y
  `status`; nunca puede inventar autoridad. Un segundo fallo queda
  `tool_parse_error`, 429/model removal del repair conservan su clase real y
  los modelos strict nunca reintentan. Qwen permanece limitado a Tier 3 y
  criticidad máxima media.
- [x] **Hacer que fallback, cuota y privacidad respeten el mismo gate**. El
  fallback dentro del perfil debe excluir candidatos incompatibles aunque
  coincidan familia/tier. Groq debe capturar límites por modelo y unidades
  RPM/RPD/TPM/TPD en vez de tratar 1.000 runs/día como capacidad completa;
  Gemini conserva capacidad desconocida si no hay denominador. Separar en UI
  cuota API gratuita de suscripción. `non_confidential_only` y los términos de
  free tier requieren clasificación/confirmación antes de enviar datos, no una
  nota decorativa. El filtrado de fallback y la clasificación fail-closed ya
  consumen el gate común. Groq captura los headers oficiales RPD/TPM por run y
  modelo, incluidos 429, sin congelar cuotas
  comerciales; Gemini declara RPM/TPM/RPD/TPD como dimensiones posibles pero
  conserva `capacity_unknown` porque el denominador efectivo pertenece al
  proyecto en AI Studio. El snapshot separa `api_rate_limit` de
  `subscription_pressure`, las APIs nunca consumen el proxy de runs de una
  suscripción y Equipo etiqueta ambos estados de forma distinta.
- [x] **Añadir la matriz hermética positiva y negativa por modelo**. El auditor
  `scripts/audit_model_flow_matrix.py` recorre los 12 perfiles y 46 modelos
  built-in, valida identidad/metadata, recomendaciones ejecutables, roles
  positivos y negativos, perfiles bloqueados, privacidad fail-closed, MCP API,
  criticidad y JSON Schema. La matriz actual contiene 334 celdas positivas y
  402 negativas sin fallos. GET de Equipo y POST de compatibilidad devuelven la
  misma decisión para cada modelo; onboarding prueba cada modelo API exacto y
  demuestra que no habilita a sus hermanos. Los flujos representativos de
  bootstrap, hiring, edición, dispatch, escritura API, deny OpenCode, retirada,
  429 y fallback permanecen cubiertos por las suites de workspace/executor.
- [x] **Completar canarios vivos por modelo y run profile**. Ejecutar con las
  keys/CLIs del owner creación real en `solo_lead`, `lead_quorum` y `full_team`,
  registrando versión de adapter/modelo, contrato, privacidad, consumo/cuota y
  causa de cualquier deny. Mantener estos recibos fuera de la suite hermética;
  una indisponibilidad externa no debe romper CI. Cada recibo registra versión
  de CLI/API, modelo exacto, rol y fecha; la suite hermética conserva
  catálogo→discovery→probe, bloqueo pre-consumo, ejecución, rate-limit,
  retirada y fallback con o sin candidato.
  Progreso `2026-07-21`: nueve submits estructurales y doce inferencias de review
  durable de Antigravity produjeron una
  matriz review 3×2 completa y tres diagnósticos de catálogo 3.6. Los recibos
  viven en `benchmarks/results/model_calibration/antigravity-1.1.5-gemini-3.6-*`.
  La matriz durable valida rechazo→fix del Lead→aprobación en 3×2, sin runs ni
  wakeups tomados al terminar. Creación viva `solo_lead` pasa con Antigravity
  Pro High en una run y 54,656 s: un agente, archivo materializado, verificación
  de máquina, raíz cerrada y cero trabajo vivo residual. `full_team` pasa en la
  semilla 3 con 12 runs/635,969 s: Lead Codex GPT-5.5, Sonnet Engineer, Flash
  High Reviewer/Test Designer, Flash Low Scout y test runner determinista; la
  raíz cierra tras deny previo a exit 0 y no queda cola. `lead_quorum` cierra en
  seed 4 con 4 runs/305,7 s, Plan A y Plan B estructuralmente válidos, dos
  contribuciones cross-provider válidas y sesión `accepted`.
  Tres canarios quorum degradaron correctamente: seed 1 por auditoría
  Antigravity sin findings, seed 2 tras dos Plan B cuya narrative quedó por
  debajo de 300 palabras y seed 3 por AGENT-REPORT Codex inválido dos veces.
  La instrucción final ahora nombra explícitamente `plan.narrative_markdown` y
  seed 4 valida en vivo la corrección. Los tres perfiles quedan demostrados;
  las promociones de modelos gratuitos conservan su ítem separado y sus gates.

Criterio de cierre P0.3: no existe ninguna ruta que guarde, contrate, ejecute o
proponga como fallback una pareja modelo/rol que el catálogo de Equipo marca
incompatible; toda denegación explica causa y alternativa. Cada opción visible
como habilitada tiene inventario y contrato de ejecución demostrados, y los
tres perfiles de run conservan un Lead realmente capaz de cumplir su contrato.

### P1 — Endurecimiento oportunista

- [x] **Convertir el plan durable en contrato API/SQLite estructurado**. El
  formato `aiteam.plan.v1+json` versiona objetivo, alcance, supuestos,
  arquitectura, work items con reporting/aceptación/evidencia, riesgos y
  rollback, verificación, escalado y riesgos de la siguiente run. Sigue usando
  `issue_documents` y sus revisiones optimistas: no existe una segunda tabla ni
  estado específico de proveedor. `update_plan` acepta el contrato estructurado,
  el cockpit lo proyecta y la API valida que cualquier `run_id` pertenezca al
  Lead asignado. Los comentarios dejaron de materializar planes implícitamente;
  las nuevas revisiones por API exigen estructura. Markdown queda como shim
  transitorio, visible como `legacy_unstructured_plan`, para documentos y
  adapters antiguos mientras se migra su emisión sin romper runs activas.
- [x] **Extraer el contrato de context curator de `RunExecutor`**. El módulo
  `aiteam/context_curator.py` concentra el slice durable, la evaluación del
  presupuesto efectivo, idempotencia del trigger, validación y persistencia del
  artefacto, offsets parciales y transición retry → escalado. `RunExecutor`
  conserva únicamente la integración con la delegación general de issues. Hay
  tests directos del módulo y canarios de integración para payload, recovery,
  auditoría y contratación automática.
- [ ] **Extraer políticas de quorum solo si vuelven a crecer**. No hacer una
  partición mecánica de las 7.608 líneas sin una frontera funcional verificable.
- [x] **Consumir `orchestrator_evals` en el cockpit** mediante el endpoint
  aditivo `loop-health`: raíces stranded elevan atención y llevan a issues
  abiertas; runs/wakeups activos o quorum inconsistente llevan a Runs. No se
  añadió un panel genérico ni se duplicaron las definiciones del harness.
- [ ] **Mantener telemetría comparable para Antigravity** antes de incluir ese
  canal en comparaciones de coste. Verificado de nuevo el `2026-07-21` con
  `agy 1.1.5`: `--help`, `--print` y el changelog no exponen recibos headless
  de tokens por run. La cuota visible en TUI no se parsea ni se sustituye por
  estimaciones sin provenance.
- [x] **Reforzar la fidelidad semántica del context curator sin confundir
  estructura con verdad**. Cada bloque conserva Markdown y un índice causal v1
  con unidades tipadas, provenance por comentario y relaciones compactas. El
  gate verifica forma y enlaces críticos: accountability exige
  owner/deliverable/accepted_by; escalado exige metric/threshold/window/action;
  una opción descartada exige reason. Se mantienen slice, offsets, recovery y
  ratio Markdown ≤30 %; el índice tiene tope independiente de 4 KiB y se mide
  su coste efectivo. La rúbrica oculta evalúa ambos artefactos. Spot-checks
  reales nuevos con `codex_subscription/gpt-5.5`: auth 9/9, Markdown 12,77 %,
  índice 2.956 chars y cierre en una run; queue 9/9, 12,96 %, índice 3.564 chars
  y cierre en una run. El ratio efectivo total fue 47,56 % y 54,37 %: mejora
  trazabilidad, pero no es compresión gratuita ni autoriza cambios de routing.
- [ ] **Robustecer la clasificación de cuotas de suscripción** cuando exista
  señal estructurada del CLI o aparezcan variantes reales. Conservar el fallback
  seguro y añadir fixtures de mensajes/idiomas observados, sin perseguir strings
  hipotéticos ni reintentar una cuota agotada.

Criterio de cierre P1: cada extracción reduce una política mutable del executor
sin cambiar semántica, y cada dato nuevo de UI conduce a una acción operativa.

### P2 — Completar auto-extensión gobernada

Bloque completado sobre skills por proyecto, registry, propuestas Lead-only,
approval/rejection y auditoría:

- [x] **MCP runtime mínimo**: contrato neutral al proveedor con versión pineada,
  handshake `initialize` obligatorio, grants por rol+`external_mcp` y recibos en
  `tool_access`. Codex recibe overrides efímeros; Claude recibe
  `--strict-mcp-config` efímero. Antigravity conserva rol/autoridad pero deniega
  el grant de forma auditable hasta ofrecer inyección aislada por run.
- [x] **Base de seguridad MCP**: secretos fuera del registry/prompt, health y
  `tools/list` con timeout/cap, idempotencia por contrato+versión, cero shell o
  auto-install y denegación de tools no declaradas como lectura. Esta base no
  convierte `readOnlyHint`, `serverInfo.version` ni un inventario cacheado en
  evidencia confiable: el endurecimiento por allowlist aprobada, identidad de
  artefacto y frescura vive en P0.1. Cada decisión se registra por tool.
- [x] **Detección de necesidad**: `capability_gap` explícito o un bloqueo
  realmente no verificable genera sugerencia; señales ambiguas requieren dos
  runs distintas de la misma capacidad. El reconciler agrupa por raíz+capacidad,
  persiste evidencia como comentario, reutiliza la ruta viva del Lead y crea una
  wakeup solo si hace falta. Es una sugerencia: el Lead investiga y el owner
  conserva los gates de propuesta, health, allowlist y activación.
- [x] **Skills aprendidas gobernadas**: el Lead puede proponer conocimiento
  reutilizable solo con evidencia y queda `proposed`, fuera del prompt, hasta
  aprobación del owner. Se aplican topes de 24 skills vivas, 8 aprendidas,
  8 KB por aprendida y 48 KB de presupuesto activo. Config expone origen,
  evidencia y consumo; el owner puede corregir, activar, retirar o borrar sin
  perder provenance. Las directivas del usuario conservan precedencia explícita
  y comprobada sobre cualquier skill local.
- [x] **Health y retiro operables**: endpoint y UI permiten probar/activar,
  aprobar tools, retirar y reactivar. El loop ejecuta como máximo un probe
  vencido por tick, aplica backoff y retira tras tres fallos consecutivos. Un
  contrato rechazado o ya existente suprime propuestas repetidas con comentario
  correctivo y auditoría; reactivar vuelve a `approved` y exige health nuevo.
- [x] **Catálogo curado inicial**: tres descriptores oficiales revisados
  (`github-readonly`, `playwright-browser`, `filesystem-workspace`) nombran solo
  ejecutables ya instalados, con versiones exactas, riesgos y fuentes. El Lead
  puede referenciarlos por `catalog_id`; el control plane rellena y bloquea el
  contrato antes de crear la interacción normal. En paquetes Node resuelve el
  entrypoint real y verifica `package.json`, evitando sellar solo el shim. El catálogo no instala,
  ejecuta, aprueba ni autoriza tools y cualquier sustitución del descriptor se
  deniega de forma auditable.

Criterio de cierre P2: una extensión aprobada puede pasar propuesta → pin →
health → grant → uso auditado → retiro, y ninguna extensión puede ampliar por
sí sola el privilegio de un rol.

### P3 — Economía y rendimiento posteriores

- [ ] **Informe de coste por entrega/proyecto**: coste real, ahorro estimado,
  latencia y calidad por perfil; solo construirlo cuando haya volumen suficiente
  para no presentar estimaciones como hechos.
- [x] **Evaluar sesiones persistentes de CLI** con un experimento acotado. Las
  columnas `session_id_before/after` existen, pero solo activar reanudación si
  reduce re-derivación sin introducir contaminación de contexto o recovery
  frágil. Instrumentación offline completada: `aiteam/session_continuity.py`
  exige opt-in e identidad exacta de agente+issue+adapter+perfil+provider+modelo+
  canal+workspace; prohíbe `--last`/`--continue` y extrae el ID explícito de
  Codex. `scripts/benchmark_cli_sessions.py` prueba capacidades sin consumo y
  audita A/B stateless/resumed con memoria, override, contaminación, tokens y
  duración. Probe real: Codex 0.128.0 y Antigravity 1.1.5 soportan ID explícito;
  Claude no está instalado. Canario Codex GPT-5.5 completado con dos semillas:
  ambos brazos conservan memoria, override y ausencia de la instrucción
  revocada, pero resumed casi duplica tokens brutos (mediana de ahorro
  `-99,75 %`) y solo mejora `3,74 %` la duración. Antigravity 1.1.5 reanuda por
  conversation UUID explícito y conserva el marcador, pero carece de usage
  comparable. Decisión: mantener producción stateless; el harness conserva
  `production_enabled=false` y no se usan `--last`/`--continue`.
**Paralelismo por canal — validación reabierta.** El default secuencial y
`AITEAM_PARALLEL_CHANNELS` opt-in no cambian hasta completar estos bloques:

- [x] **Registrar el baseline histórico sin inferir promoción.** El auditor
  offline revisó siete SQLite `full_team`: 75 runs registradas, 72 temporizadas
  y tres excluidas. Todas las muestras tenían una sola raíz y un solo proveedor;
  por tanto, cero esperas elegibles solo significa que el histórico no ejercita
  el selector paralelo. Recibo:
  `benchmarks/results/parallel_channels/parallel-channel-capacity-v1.json`.
- [x] **Persistir la elegibilidad exacta de cada candidato al despachar.**
  `dispatch_candidate_decisions` guarda por batch y wakeup el modo, raíz, pool
  de capacidad efectivo, rol/work slot, `requested_at`, primera observación
  `ready_at`, decisión y motivo estable. El mismo contrato cubre dispatch
  secuencial y paralelo; los rechazos no necesitan crear una run. El scheduler
  distingue dependencia y checkout activo, y selecciona por pool de capacidad,
  no por vendor aparente. La tabla es aditiva/idempotente para DB existentes y
  los tests cubren dependencia, checkout, mismo agente, misma raíz, mismo pool,
  segundo work slot y estabilidad de `ready_at`.
- [x] **Hacer que el auditor consuma esa provenance exacta.** El loop secuencial
  fotografía el prefijo completo de candidatos antes de reclamar: el primero
  listo queda `selected`, los demás listos `sequential_mode`, y dependencia o
  checkout permanecen `not ready`. El auditor v2 separa espera total, espera
  desde readiness y espera paralelizable; usa raíz/pool/work slot persistidos,
  deduplica por wakeup y sólo una fuente `exact` puede abrir el trigger. DB sin
  decisiones son `approximate` y batches singleton anteriores al contrato son
  `partial_exact`. Fixtures positivos/negativos cubren calidad, cobertura y
  exclusiones. El recibo histórico v2 conserva 7 DB/75 runs como `approximate`
  y cero trigger exacto: `benchmarks/results/parallel_channels/parallel-channel-capacity-v2.json`.
- [x] **Construir un A/B hermético sobre el `HeartbeatLoop` real.** El harness
  clona cuatro raíces/cuatro pools en flag off/on: Engineer y Reviewer compiten
  por el único work slot; dos scouts pueden acompañar al primero y uno falla de
  forma intencional. El brazo secuencial no solapa; el paralelo registra sólo
  los tres pares del batch admitido, rechaza Reviewer por `second_work_slot`,
  aísla el fallo y conserva paridad exacta de runs, wakeups e issues terminales.
  Ambos brazos terminan con cobertura de dispatch 100 % y cero runs, wakeups o
  checkouts vivos. El recibo niega expresamente conclusión de rendimiento,
  trigger vivo o cambio de default:
  `benchmarks/results/parallel_channels/parallel-heartbeat-hermetic-v1.json`.
- [ ] **Obtener un trigger vivo representativo antes de gastar modelos.** Retener
  al menos un proyecto comparable con múltiples raíces y pools cuya provenance
  exacta muestre espera paralelizable mayor que cero. Sin ese trigger, no abrir
  el A/B de proveedores y mantener la tarea pendiente sin cambiar política.
  Inventario read-only del 2026-07-22: 71 SQLite descubiertas, 70 auditables y
  una vacía; cero errores de discovery y cero fuentes `exact` porque todas las
  runs retenidas preceden la instrumentación. `audit_parallel_trigger_inventory.py`
  descarta evidencia aproximada, menos de dos raíces/pools, espera nula y
  adapters herméticos. Resultado: cero candidatos, `live_ab_allowed=false` y
  tarea correctamente abierta; `--require-trigger` falla cerrado como gate del
  futuro A/B. Recibo:
  `benchmarks/results/parallel_channels/parallel-live-trigger-inventory-v1.json`.
- [ ] **Ejecutar el A/B vivo secuencial/paralelo.** Sólo tras el trigger anterior:
  misma cola y workspace inicial, varias semillas y canales ejecutables distintos.
  Comparar makespan, espera, calidad/aceptación, runs, tokens/coste disponible,
  errores de cuota, checkout, wakeups y liveness; publicar mediana y rango.
- [ ] **Decidir y documentar la política final.** Activar o ampliar límites sólo
  si el A/B muestra mejora consistente sin regresiones de calidad, cuota,
  aislamiento o liveness. En cualquier otro resultado conservar el opt-in y
  cerrar con evidencia negativa explícita.
- [x] **Fortalecer el instrumento de benchmark antes de conclusiones nuevas**.
  `scripts/benchmark_integrity.py` audita offline la matriz brazo×semilla,
  duplicados, suites comparables, evidencia independiente, muestra, providers,
  provenance, hard gates, estabilidad del signo, mediana+rango y riesgo de
  Goodhart. Las runs incompletas quedan fuera del delta sin perderse como
  liveness. El harness de código v4 declara suite oculta+Ruff y usa GPT-5.5 como
  baseline por defecto; quorum añade el contrato estructural de profundidad sin
  alterar su score léxico. La auditoría histórica acepta
  `accessible_checkout_form` (2×2 balanceado) y niega una conclusión nueva en
  `provider_failover`: cuatro sesiones aceptadas y dos incompletas, mediana
  `+6,52`, rango `-8,70..+8,70`, signo inestable y resultados legacy sin
  contrato estructural. La evidencia previa sigue siendo direccional.
- [x] **Reducir dependencia de rúbricas léxicas en familias nuevas**. Conservar
  los scores históricos solo para comparabilidad; cada promoción nueva debe
  añadir al menos una evidencia independiente ejecutable o causal —hidden
  tests, invariantes de estado, análisis estático, juez determinista o revisión
  humana muestreada— y declarar explícitamente los constructos que no mide.
  Cerrado: `audit_evaluation_contract` deriva la independencia de una allowlist
  de clases de evaluador y ya no confía en un booleano autorreportado. El auditor
  separa `conclusion_allowed` de `promotion_allowed`: evidencia legacy con suite
  oculta conserva valor direccional, pero no puede cambiar defaults sin contrato
  explícito, riesgo de Goodhart y `constructs_not_measured`. Los tres recibos
  conductuales de Sonnet ya declaran esos límites; el agregado canónico conserva
  su promoción acotada con ambos gates en `true`.
- [ ] **Medir los flujos de orientación del usuario antes de ampliar frontend**.
  Definir una prueba E2E y una métrica mínima para Bandeja, selección consciente
  de `solo_lead`/`lead_quorum`/`full_team`, lectura del coste/riesgo y CTA desde
  plan aceptado a nueva tarea. No inferir adopción o claridad por la mera
  presencia de componentes; registrar abandono, errores y pasos innecesarios.
  Progreso: `orientation-flow.spec.ts` añade un contrato Playwright reproducible
  con API simulada. Chromium pasa Bandeja en 1 acción, cada perfil en 1 acción y
  plan aceptado → tarea adjunta en 2 acciones, con 0 errores y 0 abandonos
  sintéticos. La UI expone guía cualitativa de coste operativo y riesgo sin
  inventar precios. El recibo `orientation-flow-v1.json` declara explícitamente
  que adopción y claridad reales siguen sin medirse; mantener este task abierto
  hasta disponer de sesiones observadas o analítica consentida.

### Mantenimiento no bloqueante

- [x] **Hacer la limpieza de pytest segura ante concurrencia y locks de
  Windows**. Mantener sesiones por PID, aislar también user config por sesión y
  no abortar `pytest_configure` si un directorio stale devuelve
  `PermissionError`: conservarlo con warning para limpieza posterior. Añadir
  tests de sesión hermana viva y árbol stale bloqueado. Unificar el wrapper
  canónico con las garantías ya presentes en `pytest_local_stable.py`. Cerrado:
  el probe Windows usa `OpenProcess`/`GetExitCodeProcess` en vez de
  `os.kill(pid, 0)`, workspace y user config comparten un ID de sesión por PID,
  los locks stale solo generan warning y dos suites concurrentes terminan 9/9 y
  5/5 sin eliminarse entre sí. Ambos wrappers conservan el exit code.
- [x] **Consolidar Git al cerrar cada bloque material**. Cerrado el 2026-07-21
  tras barrido de secretos/tamaños/runtime, `1161 passed` y tres commits
  temáticos: `1b3650e` runtime/control plane, `66304c8` benchmarks/recibos y
  `c695661` documentación/retirada legacy. Los 16 commits locales acumulados se
  publicaron en `origin/master`; el árbol quedó limpio. Repetir esta disciplina
  al terminar cada bloque para que Git siga siendo la fuente de verdad.

- [ ] Eliminar temporales retenidos por Windows después de reinicio o liberación
  de handles, verificando primero rutas exactas. `.pytest-workspace-tmp` y
  `.pytest-user-config-tmp` ya no existen. Quedan solo
  `.tmp_pytest/tmpi0cx_njg` y `.tmp_pytest/tmpmzgfjkhr`, creados el 2026-04-02:
  son directorios temporales ignorados, ordinarios y sin reparse point, pero sus
  ACL privadas niegan incluso enumeración al owner actual. El borrado exacto fue
  bloqueado por la política del entorno antes de ejecutarse; no se tocó ningún
  archivo ni se deben borrar `.pytest_cache`, `.ruff_cache` o caches de runtime.
- [x] Reconciliar este documento y `HANDOFF.md` después de cada bloque material.
  Reconciliados el 2026-07-21 tras endurecer el benchmark, auditar temporales,
  evaluar sesiones persistentes y corregir los IDs de Antigravity 1.1.5.
  No crear roadmaps paralelos fechados; reabrir este control en el siguiente
  bloque material.

## Decisiones vigentes

- `lead_quorum` solo se activa por perfil explícito; nunca mediante
  `AITEAM_AUTO_QUORUM` ni scoring oculto.
- Una tarea simple no requiere quorum ni review pesado.
- Un quorum de un solo senior es degradación disponible, no equivalencia a dos
  proveedores.
- Compresión de contexto no implica retención semántica; se mide por familia.
- `readOnlyHint` y `serverInfo.version` son declaraciones del servidor MCP, no
  fronteras de confianza. El owner aprueba la allowlist y la identidad del
  artefacto; cada adapter debe imponerlas o denegar el grant.
- `RunExecutor` se divide por fronteras funcionales, no por objetivo de líneas.
- No reintroducir `[WORKFLOW_PLAN]`, rondas, router multifactor, JSONL primario,
  `TaskBoard` ni prompts raíz de proveedor. `lead_executor` sigue activo como
  brazo Tier 1 del Lead para ejecución directa y hereda siempre su adapter.
- AI Teams nunca crea `AGENTS.md`, `CLAUDE.md` o `GEMINI.md` en proyectos
  externos; usa `.aiteam/instructions.md`.
- Lead, Engineer, Reviewer y demás roles son autoridades del producto, no
  aliases de proveedor. Un adapter puede carecer temporalmente de un transporte
  MCP sin perder ownership ni ser sustituido silenciosamente por otro.
- El modelo se elige dentro del adapter del rol. API optimiza coste por token;
  suscripción optimiza capacidad frente a presión de cuota; local optimiza
  capacidad frente a health/recursos. `test_runner` no consume un LLM.
- Fable 5 es escalado manual, no default Tier 1. Antigravity con un modelo
  Claude sigue siendo el mismo canal/cuota Antigravity y no cuenta por sí solo
  como diversidad independiente de proveedor.

## Evidencia ya disponible

- Selector determinista: 31/31 sobre siete familias etiquetadas; mide
  consistencia de política, no optimalidad LLM.
- `sqlite_job_queue`: equipo 10/10 frente a `solo_lead` 9/10, con 1,84× tokens
  y 1,73× tiempo.
- `config_redactor`: empate 3/3; `solo_lead` fue más caro/lento que Codex directo.
- `release_notes_indexer`: empate 7/7; `full_team` consumió muchas más runs y no
  convergió dentro de 15 minutos.
- `deployment_wave_planner`: ambos 16/16 en dos semillas; equipo cerró 1/2 y
  consumió bastante más tiempo y entrada.
- `tenant_authorizer`: `full_team` 2/5 frente a Codex directo 4/5.
- `accessible_checkout_form`, dos semillas: `solo_lead` y `full_team` pasan
  siempre 10/10. Solo cierra 2/2 en una run (122,7–147,8 s; 143.026–166.639
  tokens de entrada); equipo cierra 1/2 en 10–12 runs (629,1–826,5 s;
  787.619–1.259.330 tokens). Equipo promedia 5,38× el tiempo y 6,61× la entrada.
  La run incompleta conserva continuación durable y liveness sano. Codex directo
  tiene una semilla 10/10 de 374,8 s/699.317 tokens.
- `inventory_snapshot_diff`, dos semillas: ambos perfiles pasan siempre 20/20.
  `solo_lead` cierra 2/2 en una run (178,6–283,8 s; 296.683–864.627 tokens de
  entrada); `full_team` cierra 0/2 dentro de 12 runs (517,1–832,6 s;
  623.564–1.599.277 tokens), promedia 2,92× tiempo/1,91× entrada y deja un F401
  en la segunda semilla. Codex directo obtiene 20/20 en 159,3 s/241.922 tokens.
- Quorum Anthropic aceptado: cambios observados +4,35, −8,70 y +8,70; la media
  pequeña y la varianza no justifican todavía una política de calidad.

## Verificación mínima por bloque

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\e2e_canary.py
.\scripts\python_local.bat scripts\e2e_quorum_canary.py
.\scripts\python_local.bat scripts\e2e_solo_lead_canary.py
Set-Location ide-frontend
npm run test:e2e:orientation
```

Ejecutar únicamente la verificación proporcional al cambio durante iteración;
reservar la suite completa y los canarios relevantes para el cierre del bloque.
