# Orquestación multi-modelo en AI Teams

Actualizado: `2026-07-18`

Fuente canónica para diseñar, revisar y evaluar routing, delegación, adapters, cascadas, quorum, verificación, contexto, liveness y economía multi-LLM. Las skills de Claude y Codex deben apuntar aquí, no copiar este contenido.

Consultar [ORCHESTRATION_SOURCES.md](ORCHESTRATION_SOURCES.md) antes de usar cifras externas o afirmar comportamiento actual de un SDK, proveedor o modelo.

## Objetivo

Optimizar simultáneamente:

- calidad verificable;
- coste monetario y tokens por canal;
- latencia y throughput;
- liveness y recuperación;
- accountability;
- facilidad de diagnóstico.

Más agentes no implican mejor resultado. Toda complejidad nueva debe demostrar valor frente a un baseline más simple.

`Lead` es una autoridad (`role:lead`), no una marca ni un proveedor. El usuario
elige su perfil al crear el proyecto y puede cambiar adapter/modelo después en
Equipo. OpenAI/Codex, Anthropic, Antigravity u otro canal compatible pueden
ocupar el Lead o una plaza senior del quorum. Plan A, el wakeup de síntesis y
Plan B pertenecen siempre al agente que ocupa `role:lead`.

## Flujo de decisión

1. Inspeccionar código, tests y estado durable; no confiar en documentos históricos sin contraste.
2. Clasificar el trabajo como:
   - código o herramienta determinista;
   - agente único;
   - especialista invocado como herramienta;
   - handoff con cambio real de ownership;
   - equipo con ramas independientes.
3. Definir owner, supervisor, autoridad, contexto mínimo, output, evidencia, aceptación, presupuesto, escalado y continuación durable.
4. Mantener invariantes en código. Reservar al LLM planificación abierta, síntesis y juicio contextual.
5. Diseñar fallos y recuperación antes del happy path.
6. Medir contra la alternativa mínima viable.

## Selección de patrón

| Patrón | Usar cuando | Evitar cuando |
|---|---|---|
| Código determinista | La transición, validación o política es verificable | Se necesita juicio abierto |
| Agente único | Una autoridad y un contexto son suficientes | La verificación independiente reduce riesgo material |
| Especialista como herramienta | El Lead conserva ownership y sintetiza | El especialista debe asumir la conversación o issue |
| Handoff | Cambia realmente el owner activo | Solo se necesita una consulta auxiliar |
| Evaluator–optimizer | Hay rúbrica y criterio de parada | Cada vuelta repite opinión sin nueva evidencia |
| Paralelismo | Las ramas son independientes | Comparten dependencias inmediatas o superficie de escritura |
| Quorum | La decisión es crítica y ambigua | El trabajo es simple, reversible o mecánico |

OpenAI distingue manager con agents-as-tools de handoffs y recomienda combinar orquestación por LLM con flujos explícitos en código. Esto respalda el diseño Lead-first, pero no introduce una acción pendiente por sí mismo. Fuente: [OAI-1](ORCHESTRATION_SOURCES.md#oai-1-agent-orchestration).

## Manager y accountability

Mantener el Lead como manager cuando deba:

- conservar la relación con el usuario;
- mantener el plan durable;
- integrar evidencia heterogénea;
- resolver conflictos entre especialistas;
- aceptar, rechazar o escalar el resultado.

Cada delegación debe responder:

- quién ejecuta y a quién reporta;
- qué puede y no puede tocar;
- qué contexto recibe;
- qué entrega y en qué formato;
- qué evidencia produce;
- quién revisa;
- qué activa escalado o intervención humana.

## Routing por capacidad y economía

Preferir el modelo más barato capaz de cumplir el contrato. Evaluar:

- tipo, complejidad y criticidad;
- herramientas y acceso al workspace;
- salud, límites y latencia del proveedor;
- canal API o suscripción;
- coste marginal;
- rendimiento histórico en tareas comparables.

RouteLLM demuestra en benchmarks propios que puede conservar el 95% del rendimiento de GPT-4 reduciendo más de 2× el coste frente a usar GPT-4 uniformemente. Es evidencia de que el routing puede funcionar, no un ahorro garantizado para AI Teams. Fuente primaria preprint: [PAPER-1](ORCHESTRATION_SOURCES.md#paper-1-routellm).

No usar porcentajes de vendors como thresholds locales. `AITEAM_PROVIDER_ESCALATION_THRESHOLD=0.25` es una decisión operativa del proyecto que debe calibrarse con telemetría propia.

### Selector de perfil de ejecución

El selector automático solo decide entre `solo_lead` y `full_team` y usa
señales explícitas persistidas: criticidad, ambigüedad, necesidad de
verificación independiente, número de ramas y reversibilidad. No suma scores:
una señal material de equipo selecciona `full_team`; señales incompletas también
usan ese default seguro. `solo_lead` exige trabajo acotado, reversible y de una
sola rama. Un override explícito siempre prevalece.

`lead_quorum` nunca es resultado automático porque no ejecuta: solo puede
seleccionarse explícitamente para producir un plan multicultural aceptado. Cada
decisión del selector se guarda como `profile_selection` con fuente, razón y
señales para evaluación posterior.

La calibración debe medir calidad y convergencia, no solo la etiqueta. En la
familia reversible `release_notes_indexer` los tres brazos pasan 7/7: `solo_lead`
cierra en 215,4 s y 417.104 tokens de entrada, Codex directo en 243,9 s y
697.158, mientras `full_team` alcanza la misma calidad pero agota 15 minutos
todavía `in_progress`, tras 7 runs y 1.811.217 tokens. Esta semilla apoya
`solo_lead` para trabajo acotado; no demuestra que `full_team` sea inútil en
tareas complejas ni autoriza a relajar el default seguro.

Dos semillas complejas de `deployment_wave_planner` refuerzan la necesidad de
separar calidad, economía y accountability. `solo_lead` y `full_team` pasan
16/16 siempre; solo cierra 2/2 en una run (130,4–154,0 s) y equipo 1/2 en 10–12
runs (426,0–821,9 s), con 3,73× tokens y 4,39× tiempo medios. El equipo detectó
y corrigió un defecto CLI en seed 1, pero dejó un F401 en seed 2 y no mejoró el
juez oculto. Aun así, `independent_verification=true` expresa una garantía de
accountability que un agente único no puede satisfacer: debe conservarse cuando
el usuario o el contrato la exijan. Lo que estos datos desaconsejan es inferir
esa necesidad solo por complejidad aparente cuando el cambio es reversible,
acotado y concentra la escritura en un único artefacto.

## Cascadas y recovery

Una cascada barata → cara necesita:

- criterio de escalado explícito;
- límite de saltos;
- idempotencia;
- circuit breaker consciente de 429/infra;
- cap de coste;
- tasa de escalado y calidad posterior.

Una cuota de suscripción agotada no es un fallo de formato ni un retry
inmediato útil. Los CLIs deben clasificarla como `subscription_cli_usage_limit`;
si impide alcanzar el mínimo de quorum, la sesión degrada con diagnóstico y
wakeup al Lead en vez de repetir el mismo auditor o gastar otro proveedor en
una sesión que ya no puede satisfacer su gate.

FrugalGPT aporta evidencia primaria de que combinar selección, generación y cascadas puede reducir coste manteniendo calidad en sus datasets. No prescribe thresholds universales. Fuente: [PAPER-2](ORCHESTRATION_SOURCES.md#paper-2-frugalgpt).

Vigilar `cascade pile-up`: degradación o rate limit del barato puede trasladar toda la carga al caro. En AI Teams lo mitigan `provider_governor`, recovery acotado y cap diario.

## Trabajo determinista

No enviar a un LLM trabajo que una herramienta pueda ejecutar y verificar mejor. Ejemplos:

- ejecutar tests;
- validar schemas;
- consultar SQLite;
- comprobar diffs y exit codes;
- aplicar transiciones e idempotencia;
- calcular límites y presupuestos.

En AI Teams, `test_runner` debe seguir siendo builtin/subprocess. El LLM puede interpretar un fallo, pero no sustituir su recibo.

## Planner, maker y verifier

- Planner: define objetivo, riesgos y aceptación.
- Maker: produce el cambio o artefacto.
- Verifier: contrasta spec, diff y evidencia.

Preferir evidencia en este orden:

1. resultado de herramienta determinista;
2. diff o artefacto inspeccionable;
3. registro durable estructurado;
4. veredicto LLM con rúbrica y referencias;
5. narración libre.

El trabajo sobre self-preference de LLM-as-a-judge reporta, en las configuraciones estudiadas, ventajas de win-rate de hasta aproximadamente 10% para textos percibidos como propios. Es un preprint y no prueba que todo judge tenga ese sesgo ni que cross-provider lo elimine. Justifica diversidad de método/proveedor en criticidad alta, no quorum universal. Fuente: [PAPER-3](ORCHESTRATION_SOURCES.md#paper-3-self-preference).

## Contrato de `lead_quorum`

`lead_quorum` es una fase de planificación, no un equipo de implementación. Su
owner sigue siendo el Lead y su artefacto de salida es un plan aceptado. Cuando
el gate se satisface, la issue termina con `planning_status=accepted_plan`. No
transiciona ni despierta `full_team`: ejecutar ese plan requiere que el usuario
cree después una tarea de ejecución explícita.

Contrato mínimo:

- revisión A del documento `plan` como base común e inmutable de la sesión;
- dos contribuciones válidas e independientes como objetivo canónico; si el
  equipo aceptado solo contiene un senior además del Lead, el gate se adapta a
  una contribución y registra explícitamente el quorum reducido;
- identidad, adapter, provider, modelo, canal, run, evidencia y coste persistidos;
- gate determinista sobre cantidad, validez, diversidad exigible y presupuesto;
- revisión B sintetizada por el Lead, con disposición `aceptar`, `matizar` o
  `descartar` para cada hallazgo material;
- degradación con `skipped_reason`, comentario correctivo y continuación durable;
- cierre auditable de la planificación o escalado humano con cap.

El runtime implementa este contrato mediante `quorum_sessions`, contribuciones
derivadas de reports validados, wakeups `quorum_ready`/`quorum_degraded` y la
operación Lead-only `accept_quorum_synthesis`. Esta operación sólo se acepta en
la misma run que crea una nueva revisión del plan y después de disponer todos
los findings. Dos rechazos degradan la sesión y crean una única interacción.

La máquina de estados es monotónica: `reviewing → ready → accepted`, con salida
a `degraded` o `failed` desde estados activos. `accepted`, `degraded` y `failed`
son absorbentes salvo una futura operación explícita de reapertura. Las
contribuciones tardías se rechazan y reevaluar una sesión terminal solo devuelve
métricas: nunca reactiva el gate ni emite otro wakeup.

La auditoría estándar debe considerar una sesión viva solo cuando conserva una
ruta durable: auditor/run/wakeup activo, síntesis pendiente o interacción humana.
También debe enlazar `quorum_contributions.run_id` con `runs` y `cost_events` para
verificar provenance y economía por auditor, provider, modelo y canal.

Los auditores reciben la misma revisión A y no ven inicialmente las respuestas
de otros auditores. La síntesis corresponde al Lead; encadenar opiniones antes
de la primera evaluación reduce independencia y puede crear consenso aparente.
El bootstrap asigna proveedores distintos a los auditores cuando hay perfiles
suficientes; si no puede, conserva perfiles distintos como segundo mejor
aislamiento y deja que el gate durable diagnostique la diversidad insuficiente.

Antes de abrir la sesión, el control plane congela también el objetivo vigente
de la issue (título, descripción y revisión base). Cada prompt nuevo de este
modo debe vivir en su propia issue de planificación; aclaraciones anteriores al
freeze se incorporan al objetivo, pero no mutan silenciosamente una sesión ya
iniciada. Chat rechaza con `409` mensajes posteriores sobre una issue cuyo
quorum ya existe y dirige al usuario a Nueva tarea.

Plan A y Plan B deben superar un contrato determinista de profundidad: mínimo
300 palabras y cobertura explícita de objetivo/alcance, estado actual,
supuestos/restricciones, arquitectura y alternativas, fases/dependencias/owners,
riesgos/rollback, verificación/evidencia, preguntas/escalado y continuación. El
gate solo valida estructura; la calidad abierta sigue correspondiendo a los LLM
y al benchmark.

Cada senior entrega al Lead un bloque `QUORUM-AUDIT` estructurado con evaluación
ejecutiva, fortalezas, supuestos cuestionados y findings. Cada finding conserva
severidad, razonamiento causal, justificación, recomendación y trade-offs. La
contribución no cuenta si esa argumentación falta o es superficial. Los
auditores solo pueden comentar y cerrar su issue: RBAC les prohíbe editar,
delegar, preguntar al usuario o aceptar la síntesis.

El Lead recibe los informes completos una vez satisfecho el gate. Para aceptar
Plan B debe disponer todos los findings mediante `accept`, `qualify` o `discard`,
con rationale sustantiva, y publicar en la misma run una revisión final que
vuelva a superar el contrato de profundidad. La persistencia comprueba además
que esa revisión pertenece a la run de síntesis y que su agente es el Lead
configurado y asignado a la issue.

La implementación imperativa legacy de quorum fue retirada. El único camino vivo
es el contrato durable SQLite: no hay activación por `AITEAM_AUTO_QUORUM`, prompts
encadenados fuera del scheduler ni consolidación que eluda sesiones, evidencias y
disposiciones persistidas.

## Gates

Cada gate debe:

- identificar el riesgo que reduce;
- usar evidencia apropiada;
- dejar diagnóstico accionable;
- crear continuación durable o escalar;
- tener cap contra loops;
- evitar interacción humana si la recuperación es operativa.

Un gate que solo registra un log crea deadlock silencioso. El canario `scripts/e2e_canary.py` protege deny → comentario correctivo → test runner → cierre.

## Context engineering

Construir cada payload con:

- objetivo y estado causal;
- archivos o entidades focales;
- decisiones ya tomadas;
- dependencias y blockers;
- autoridad permitida/prohibida;
- aceptación y evidencia;
- riesgo y escalado.

Evitar transcripts completos cuando basten resumen estructurado y referencias durables. No eliminar resultados de herramientas o decisiones que puedan cambiar el curso del receptor.

La calidad de una síntesis se evalúa en dos ejes independientes: debe respetar
el presupuesto de compresión y conservar todas las anclas causales ocultas
(decisiones, restricciones, riesgos, evidencia, owners y escalados). El harness
determinista vive en `scripts/context_summary_evals.py`; una síntesis corta que
pierde una decisión y una síntesis completa que excede presupuesto fallan.

El contrato está implementado mediante delegation metadata, focus paths, RBAC,
user directives, dieta de payload y resumen causal incremental. El curador recibe
un rango exacto del hilo padre y solo él puede emitir `append_context_summary`; el
executor verifica rango, issue y ratio antes de persistir y deniega el cierre sin
artefacto. El canario real `scripts/benchmark_context_curator.py` muestra una
frontera dependiente de modelo/proveedor: en `auth_migration`, Codex mini conserva
7-8/9 anclas en tres semillas (0/3), `gpt-5.5` obtiene 2/2 y Anthropic Haiku 3/3;
en `queue_rollout`, Codex mini obtiene 3/3 y senior/Antigravity 1/1 cada uno.
El recovery estructural no detecta una síntesis comprimida pero semánticamente
incompleta. Por ello, nuevas contrataciones de curador sobre
`codex_subscription` usan `gpt-5.5`; otros perfiles conservan su selección propia
y el usuario puede cambiarla. No se promueve globalmente todo proveedor barato.

El recovery productivo está acotado a una sola corrección automática. Ante el
primer artefacto ausente o inválido, la issue conserva `in_progress`, persiste
contador, error y run de origen, añade el diagnóstico al hilo y encola una wakeup
idempotente al mismo curador. Un segundo fallo bloquea la issue y despierta al
Lead. `audit_project_db.py` considera bug tanto un retry sin run/wakeup vivo como
un recovery agotado sin wakeup al Lead.

Un comentario individual que supera 24.000 caracteres no se envía entero ni se
trunca. El target lo divide mediante offsets durables `[start_char_offset,
end_char_offset)` y cada bloque persiste ese rango. Mientras reste contenido,
el documento conserva `partial_comment_id` y `partial_char_offset` y no avanza
`synthesized_through_comment_id`; solo el segmento final mueve el cursor de
comentario y limpia el estado parcial. Así el límite de payload no falsea la
provenance ni oculta texto que el curador nunca vio.

La activación del curador usa el presupuesto efectivo del agente que continuará
el trabajo cuando el perfil lo declara. Estima tokens de `payload base + hilo no
sintetizado` con `chars_per_token`, aplica `comfortable_context_ratio` y resta
reservas explícitas de salida y herramientas. Solo compacta si además existen al
menos 8.000 caracteres recuperables. Codex subscription obtiene
`context_window_tokens` del `models_cache.json` local y respeta su porcentaje
efectivo; no se congelan ventanas comerciales en el código. Otros adapters pueden
declarar `context_window_tokens`, `comfortable_context_ratio`,
`reserved_output_tokens`, `reserved_tool_tokens` y `chars_per_token`. Si falta
capacidad normalizada se conserva el fallback de 8.000 caracteres. Cada trigger
persiste política, estimación, reservas y umbral cómodo en metadata y activity.

## Paralelismo

Paralelizar por independencia informativa, no por disponibilidad de roles:

`valor ≈ latencia evitada + cobertura adicional - coste de ejecución - síntesis - riesgo de conflicto`

No ejecutar en paralelo agentes que escriban la misma superficie sin aislamiento. Cada rama necesita owner, scope, evidencia y aceptación.

Anthropic describe mejoras grandes en su sistema de research multiagente, pero también un consumo de tokens muy superior y sensibilidad alta a coordinación, prompts y herramientas. Es evidencia de ingeniería de un vendor sobre su sistema, no una garantía general para coding agents. Fuente: [ANTH-2](ORCHESTRATION_SOURCES.md#anth-2-multi-agent-research).

## Fallos obligatorios de diseño

- wakeup perdido o run zombie;
- child cerrado que no despierta al padre;
- gate sin continuación;
- reporte sin artefacto;
- reviewer sin diff;
- interacción duplicada o huérfana;
- churn o delegación circular;
- 429 y cascade pile-up;
- contexto que desplaza la spec;
- modelo barato fuera de capacidad;
- modelo caro para trabajo determinista;
- telemetría ausente en un canal.

## Evaluación del orquestador

Comparar progresivamente:

1. agente único competente;
2. agente único con verificación mecánica;
3. manager + reviewer;
4. equipo reducido económico;
5. equipo completo cuando la tarea lo justifique.

Mantener spec, workspace inicial, límites y suite oculta. Repetir ejecuciones no deterministas.

### Métricas

- Resultado: aceptación, tests ocultos, defectos, completitud e intervención humana.
- Economía: tokens por proveedor/canal, coste, tiempo y coste por tarea aceptada.
- Coordinación: runs, wakeups, rework, escalado, reasignación, contradicciones y churn.
- Liveness: zombies, recovery, parent wakeups, tiempo bloqueado y convergencia por tick.

Cada check de un canario debe poder fallar. Separar garantías que alimentan `ok` de contadores informativos.

### Benchmark de planes `lead_quorum`

Evaluar el valor incremental sobre el mismo artefacto: revisión A congelada,
auditorías independientes y revisión B aceptada. La rúbrica específica permanece
fuera del contexto de los modelos y mide cobertura, hard gates, diversidad,
tokens, coste, latencia y provenance. No usar tests de código como métrica
principal ni concluir con una semilla. El harness vive en
`scripts/benchmark_quorum_plans.py`; los casos versionados cubren migración
SQLite, autorización multi-tenant y failover de proveedores.

Una ejecución incompleta también es un resultado de liveness: debe conservar
Plan A, estado de sesión, contribuciones y errores de runs. No se puede declarar
calibrado el quorum hasta repetir cada familia con al menos dos proveedores
operativos y varias semillas.

## Mapa del repositorio

| Decisión | Código principal |
|---|---|
| Ejecución y gates | `aiteam/heartbeat/executor.py` |
| Scheduling y loop | `aiteam/heartbeat/scheduler.py`, `aiteam/heartbeat/loop.py` |
| Runs y wakeups | `aiteam/db/runs.py`, `aiteam/db/wakeups.py` |
| Issues y dependencias | `aiteam/db/issues.py`, `aiteam/db/dependencies.py` |
| Interactions y reports | `aiteam/db/interactions.py`, `aiteam/db/agent_reports.py` |
| Coste | `aiteam/db/finops.py`, `aiteam/pricing.py` |
| Políticas | `aiteam/policies.py` |
| Hiring | `aiteam/run_profiles.py`, `aiteam/hiring_economics.py` |
| Adapters | `aiteam/project_adapters.py`, `aiteam/adapters/` |
| Provider health | `aiteam/provider_governor.py` |
| Quorum | `aiteam/db/quorum_sessions.py`, `aiteam/run_profiles.py`, `aiteam/lead_intake.py`, integración en `RunExecutor` |
| Contrato | `aiteam/adapters/work_contract.py` |

## Reglas locales

- `aiteam/policies.py` es la fuente de reglas de rol/flujo; no esconder políticas en prompts.
- Extraer piezas de `RunExecutor` solo oportunistamente por necesidad funcional y con tests.
- Toda feature de adapter debe responder qué registra en `cost_events`.
- API y suscripción son canales distintos aunque compartan proveedor/modelo.
- Verificar usage de `antigravity_subscription` antes de usarlo en comparaciones economicas: `agy --print` no expone todavia telemetria comparable a Codex.
- En Antigravity, los payloads largos se entregan mediante archivo temporal efímero con `--add-dir`; `--mode plan` y `--sandbox` siguen activos. El auditor debe conservar el rol `quorum_auditor` para seleccionar tier premium y usar exclusivamente resultados `approved`, `changes_requested` o `blocked` en su `AGENT-REPORT`.
- No reintroducir router legacy, rondas, JSONL primario ni `[WORKFLOW_PLAN]`.
- No convertir hallazgos externos en thresholds sin calibración local.
