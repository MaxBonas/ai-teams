# Orquestación multi-modelo en AI Teams

Actualizado: `2026-07-22`

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

### Política de modelos por tier y adapter (revisión 2026-07-20)

El tier describe la responsabilidad del rol, no el proveedor. El modelo se
resuelve dentro del adapter que el owner eligió y nunca cambia la autoridad del
Lead. Los aliases de producto tampoco son intercambiables entre canales: un ID
de API, un slug de Codex y una etiqueta aceptada por Antigravity son contratos
distintos.

Roles canónicos:

- Tier 1: `lead`, `team_lead`, `lead_executor`, `architect` y
  `quorum_auditor`; `quorum_senior` solo se conserva para proyectos legacy;
- Tier 2: `engineer`/`software_engineer`, `reviewer`/`code_reviewer`, `qa`,
  `test_designer` y `mcp_operator`; `qa` solo se materializa como gate
  adversarial proporcional al riesgo, no en el `full_team` por defecto;
- Tier 3: `worker`, `file_scout`, `web_scout` y `context_curator`;
  `researcher` solo se acepta al leer proyectos legacy;
- `test_runner` es determinista: ejecuta procesos y no recibe modelo.

La diversidad de proveedor no se decide comparando el ID de perfil. AI Teams
deriva cinco dimensiones: organización que presta el canal, fabricante del
modelo, perspectiva cognitiva, transporte y pool de capacidad. Quorum y review
crítico comparan perspectiva/vendor; cuota, pacing y recovery comparan pools de
capacidad. Por ello Codex y OpenAI API pueden tener límites independientes pero
no aportan dos perspectivas si ambos ejecutan GPT. Del mismo modo, Claude vía
Antigravity comparte perspectiva con Claude vía Anthropic. La proyección vive
en `aiteam.provider_identity` y puede calcularse sobre registros históricos sin
reescribir SQLite.

Los perfiles personalizados deben declarar suficiente gobernanza para entrar
en hiring: roles soportados, política/nota de datos, capacidades, modo de
workspace, transporte MCP y nivel de salida estructurada. La API conserva estos
campos, normaliza aliases de rol y rechaza roles desconocidos; declarar la
metadata no concede todavía compatibilidad, que corresponde al hard gate P0.3.

| Adapter API | Tier 1 | Tier 2 | Tier 3 | Decisión |
|---|---|---|---|---|
| OpenAI API | `gpt-5.6-sol` | `gpt-5.6-terra` | `gpt-5.6-luna` | La familia publica exactamente las bandas flagship, balance y alto volumen. |
| Anthropic API | `claude-opus-4-8` | `claude-sonnet-5` | `claude-haiku-4-5` | Opus es el default complejo; Sonnet ofrece el equilibrio; Haiku cubre subagentes. |
| Gemini API | `gemini-3.1-pro-preview` | `gemini-3.5-flash` | `gemini-3.1-flash-lite` | Pro sigue siendo preview; Flash y Flash-Lite son estables. El health debe detectar retirada/cambio. |

`claude-fable-5` queda visible como escalado manual excepcional, no como default
automático de Tier 1. Es el Claude más capaz para trabajo muy largo, pero cuesta
el doble que Opus 4.8, exige retención de datos de 30 días y puede responder con
un refusal que requiere fallback específico. AI Teams no debe seleccionarlo
automáticamente hasta gobernar elegibilidad de retención, refusal/fallback y
presupuesto. Fuentes oficiales de capacidades, IDs y precios:
[OAI-6](ORCHESTRATION_SOURCES.md#oai-6-modelos-gpt-56),
[ANTH-4](ORCHESTRATION_SOURCES.md#anth-4-modelos-claude-actuales-precios-y-fable)
y [GOOG-1](ORCHESTRATION_SOURCES.md#goog-1-gemini-3-y-precios).

| Adapter de suscripción/local | Tier 1 | Tier 2 | Tier 3 | Límite operativo |
|---|---|---|---|---|
| Codex subscription | `gpt-5.6-sol` | `gpt-5.6-terra` | `gpt-5.6-luna` | Slugs del catálogo; Equipo solo los habilita si el CLI instalado puede ejecutarlos. Coste marginal 0, cuota no ilimitada. |
| Claude Code subscription | Opus 4.8 | Sonnet 5 | Haiku 4.5 | Perfil bloqueado en esta máquina; Fable solo manual y sujeto a plan/créditos. |
| Antigravity subscription 1.1.5 | `gemini-3.1-pro-high` | `claude-sonnet-4-6` coding; `gemini-3.5-flash-high` review/QA | `gemini-3.5-flash-low` | IDs ejecutables confirmados por `agy models`; Equipo muestra etiquetas legibles, pero persiste y ejecuta estos slugs. Sin recibo headless comparable de tokens. |
| Ollama/LM Studio | ninguno por defecto | Qwen3 Coder 30B o Gemma 4 26B tras eval | modelo pequeño configurado | Nunca descargar ni cambiar automáticamente: usar solo modelo instalado y health aprobado. |
| builtin/manual | no aplica | no aplica | no aplica | Código determinista u operador humano, no routing LLM. |

Antigravity ofrece además Claude Opus/Sonnet 4.6 y GPT-OSS 120B. Son
alternativas dentro del mismo transporte y la misma frontera de cuota; cambiar
la familia del modelo dentro de Antigravity no demuestra diversidad de proveedor
ni crea un segundo canal independiente para quorum. El inventario real de
`agy 1.1.5` incluye también `gemini-3.1-pro-low`: permanece visible como
selección manual y no sustituye automáticamente a Pro High o Flash.

El CLI 1.1.5 cambió la salida de `agy models` a IDs slug. Las etiquetas antiguas
como `Gemini 3.1 Pro (High)` no son ya la identidad ejecutable del catálogo:
AI Teams las conserva solo como texto de interfaz y normaliza configuraciones
guardadas al slug antes de construir `--model`. Las 11 opciones enumeradas por
el runtime coinciden con Equipo: ocho opciones originales y tres Gemini 3.6
manual-only/probe-gated, incluida `gemini-3.6-flash-medium`.

El inventario vivo del `2026-07-21` añadió `gemini-3.6-flash-high`, `medium` y
`low`. Equipo los muestra manual-only y no seleccionables hasta disponer de evidencia exacta de
submit: con CLI 1.1.5 High y Low fueron enumerados pero rechazados al ejecutar;
Medium completó review 3/3. Medium empató 100 % con 3.5 High y fue 0,407 s más
lento en mediana (9,172 frente a 8,765 s), por lo que 3.5 High conserva el
default. Discovery no equivale a ejecutabilidad; el probe por modelo sigue
siendo el gate.

El drift tiene owner `AI Teams maintainer` y cadencia mensual más evento de
versión/catálogo. `scripts/audit_model_catalog_drift.py` compara inventarios
autenticados con IDs declarados y ejecuta la matriz hermética. El recibo del
2026-07-22 pasa los tres gates; declara cinco modelos Zen, conserva Laguna
manual/probe-gated tras fallar 0/3 review durable, Big Pickle como `rejected` y
la incompatibilidad de versión Codex como atención. Nada de ello promueve
defaults.

El follow-up durable v4 ejercita el runtime productivo sobre el mismo defecto y
la misma corrección. Flash High y 3.6 Medium completan 3/3 ciclos
`changes_requested` → fix materializado por el Lead → `approved`, sin runs ni
wakeups tomados al terminar. Medium tarda 43,078 s medianos frente a 99,999 s;
la mejora de latencia no equivale a ahorro de tokens ni a capacidad de cuota,
por lo que Flash High conserva el default. El recibo agregado es
`antigravity-durable-review-v4-aggregate.json`.

El primer canario vivo de creación por perfil usa `solo_lead` con Antigravity
Pro High sobre una tarea reversible. Completa en una run y 54,656 s, conserva un
solo agente, materializa el archivo, supera la verificación de máquina y cierra
sin hijos, runs vivas ni wakeups tomados. Este recibo valida solo `solo_lead`;
no se extrapola a `lead_quorum` ni `full_team`.

El canario vivo `full_team` seed 3 cierra en 12 runs y 635,969 s. Usa Codex
GPT-5.5 como Lead, Sonnet 4.6 como Engineer, Flash High para Reviewer/Test
Designer, Flash Low para scout y el runner determinista local. El quality gate
niega el primer cierre sin exit 0, luego acepta pytest y termina sin runs ni
wakeups vivas. Un intento anterior reveló que `agy --sandbox` no garantiza
workspace read-only: un Lead Antigravity escribió directamente aunque RBAC
descartó sus ops. Los roles no editores de ese transporte ejecutan desde un cwd
efímero y solo reciben el contenido del proyecto mediante wake payload.

`lead_quorum` demostró degradación fail-closed en tres canarios: auditoría sin
findings, dos síntesis cuya narrativa no alcanzó el gate de profundidad y dos
AGENT-REPORT Codex inválidos. Una semilla obtuvo dos contribuciones válidas de
proveedores distintos antes de fallar síntesis. El contrato final explicita que
`plan.narrative_markdown` debe contener por sí solo el Plan B completo de al
menos 300 palabras. Seed 4 valida la corrección: cuatro runs/305,7 s, Plan A y
Plan B estructuralmente válidos, dos contribuciones cross-provider válidas,
sesión `accepted` y raíz `done`. Junto a `solo_lead` y `full_team`, quedan
demostrados los tres perfiles vivos sin convertir los fallos previos en éxitos.

La calibración local de Antigravity del 2026-07-21 ejecutó 27 runs stateless:
tres muestras por cada par rol/modelo y un control adicional para comparar
review contra su baseline vivo, Flash High. El harness
`scripts/benchmark_antigravity_role_models.py` valida forma JSON, cobertura de
anclas causales y ruido prohibido; no ejecuta código generado ni constituye un
juez factual independiente. Los recibos agregados viven en
`benchmarks/results/model_calibration/antigravity-1.1.5-role-calibration-aggregate.json`.

El screening estructural conservó inicialmente los defaults. Pro High y Opus empatan en Lead (93,3 %), pero Opus
tarda 33,75 s medianos frente a 7,67 s. Flash Low y GPT-OSS empatan en scout
(100 %), con ventaja de Flash Low (5,59 s frente a 7,59 s). Flash High mantiene
review; Flash Medium empata 100 % y reduce 1,48 s, por lo que avanza a validación
económica. Sonnet 4.6 supera a Flash High en la rúbrica de coding (81,8 % frente
a 72,7 %), pero tarda 12,42 s más y solo avanza a benchmark conductual oculto.
Pro Low no mejora review. Ninguna de estas señales autoriza cambiar routing:
Antigravity no expone tokens headless y una rúbrica estructural tiene riesgo de
Goodhart material.

El follow-up conductual de coding sí cambia una selección acotada. En
`cli_conversor`, Sonnet 4.6 y Flash High pasan 9/9 tests ocultos en tres semillas,
pero Sonnet cierra 3/3 issues, obtiene Ruff limpio 3/3 y tarda 51,14 s medianos;
Flash cierra 2/3, deja un warning Ruff en 2/3 y tarda 105,48 s. El agregado v3
supera `benchmark_integrity.audit_ab_series` y promociona Sonnet únicamente para
`engineer`/`software_engineer` de Antigravity. Flash High conserva
review/QA y sigue visible para selección manual de coding. No se afirma ahorro
monetario o de tokens: el CLI no expone usage atribuible. El recibo canónico es
`antigravity-coding-cli-conversor-v4-aggregate-v3.json`; los intentos v1-v3
anteriores se conservan como diagnóstico del envelope observado, no entran en
el A/B.

La calibración descubrió tres variantes reales de salida de `agy 1.1.5`: ops
limpios, `text + ops` y JSON seguido de ruido de transporte. El parser prioriza
ops autoritativos y solo usa `text` como fallback; los objetos recuperados de
stdout ruidoso vuelven a pasar por el normalizador específico. Esto evita que
una respuesta válida quede `subscription_cli_parse_error` por diferencias de
envelope entre modelos.

El apartado Equipo consume un catálogo anotado por perfil, no la lista comercial
sin contexto. Un modelo queda habilitado por inventario del runtime, por catálogo
API vigente o por una run completada del par exacto perfil+modelo. Un perfil
bloqueado, un modelo local no instalado o un `model_unavailable` quedan visibles
con su causa pero no se pueden seleccionar ni contratar. El hiring usa la misma
lista; nunca elige una opción que la UI marque como no ejecutable. La evidencia
de run prevalece sobre un cache de catálogo desfasado y los health checks del
perfil no borran esa evidencia. Para APIs, `available` solo significa presencia
en catálogo; `selectable` exige el probe estructurado o una run completada del
par perfil+modelo. Discovery autenticado, verificación, rate-limit, retirada e
incompatibilidad se persisten como estados distintos. El test de perfil recibe
modelo explícito: probar el default no habilita sus hermanos.

Si una run demuestra `model_unavailable`, el lifecycle no cambia de canal ni
reintenta a ciegas. Bloquea la issue y calcula dentro del mismo perfil el
candidato ejecutable menos disruptivo: misma familia antes que mismo tier,
orden de adecuación al rol como desempate y exclusión de opciones `manual-only`.
La tarjeta muestra si la propuesta cambia familia o tier y siempre requiere una
decisión del owner. Aceptar actualiza el modelo y reencola la issue mediante una
transición determinista; rechazar conserva el bloqueo. Si el owner ya cambió
Equipo mientras la tarjeta estaba pendiente, prevalece esa decisión más nueva.
Si no existe candidato, se despierta al supervisor y nunca se salta a otro
adapter silenciosamente.

La economía depende del canal:

- API: `cost_events` usa precio por token y aplica los tramos de contexto de
  GPT-5.6 (>272K) y Gemini 3.1 Pro (>200K). Sonnet 5 se estima con su tarifa
  estándar durable, no con la promoción temporal de lanzamiento.
- Suscripción: el coste marginal de cada run sigue siendo 0; tokens, duración y
  runs sirven para estimar presión de cuota, no para inventar euros. Cada perfil
  necesita forecast propio porque los límites y pesos no son equivalentes.
- Local: el coste monetario marginal se registra como 0, pero salud, RAM/VRAM,
  latencia y throughput forman parte de capacidad; una opción nueva no se usa
  hasta que el owner la instale y pruebe.

La provenance de cuota vive en `run_adapter_profiles`, separada de la
configuración mutable del agente. `subscription_quota_snapshot` siempre puede
mostrar runs, duración y límites observados; solo suma tokens cuando el adapter
los entregó. Un forecast requiere que el owner declare en el perfil una política
operativa explícita, por ejemplo
`"subscription_quota": {"unit": "tokens", "limit": 1000000,
"window_hours": 168}`. Las unidades válidas son `tokens`, `runs` y `seconds`:
son propias de ese perfil y no expresan equivalencia entre proveedores. Sin los
tres campos válidos el estado es `capacity_unknown` y no se calcula utilización,
runs restantes ni ETA. Un `subscription_cli_usage_limit` posterior a la última
run completada marca `exhausted_observed`; una run posterior completada demuestra
recuperación.

Las APIs gratuitas no usan el forecast unitario de suscripción. Groq persiste
por run/modelo los headers oficiales `x-ratelimit-*`: RPD para requests y TPM
para tokens, con límite, restante y reset; un 429 conserva esos datos si el
proveedor los envía. No se congela la tabla comercial en código. Gemini puede
aplicar RPM, TPM, RPD y en ciertos modelos TPD, pero los valores dependen del
proyecto/tier y se consultan en AI Studio; sin denominador demostrado su estado
permanece `capacity_unknown`. La proyección devuelve `api_rate_limit` o
`subscription_pressure` y la interfaz no presenta una como si fuera la otra.
Fuente: [FREE-4](ORCHESTRATION_SOURCES.md#free-4-api-keys-gratuitas-y-límites).

No se sustituyen calibraciones locales solo por novedad comercial. En particular,
`context_curator` sobre Codex conserva `gpt-5.5`: superó los canarios causales
donde el mini anterior falló. `gpt-5.6-luna` debe vencer ese baseline en auth y
queue antes de heredar ese rol. El canario auth del 2026-07-20 no produjo una
muestra comparable: Codex CLI `0.128.0` rechazó Luna porque el cache de modelos
fue generado para cliente `0.145.0`. Equipo marca Sol/Terra/Luna como no
ejecutables en esa instalación y mantiene GPT-5.5 habilitado por runs previas;
la calibración se reanuda después de actualizar el CLI, sin contar este fallo
de transporte como fallo de calidad.

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

Dos semillas frontend de `accessible_checkout_form` muestran el mismo patrón en
una superficie distinta. `solo_lead` y `full_team` pasan siempre 10/10 checks
ocultos; solo cierra 2/2 en una run (122,7–147,8 s; 143.026–166.639 tokens de
entrada), mientras equipo cierra 1/2 en 10–12 runs (629,1–826,5 s;
787.619–1.259.330 tokens). Equipo promedia 5,38× el tiempo y 6,61× la entrada.
La run incompleta conserva un fix y wakeup durable, así que no está stranded:
falló convergencia dentro del presupuesto, no liveness. Codex directo suma una
semilla 10/10 de 374,8 s/699.317 tokens. La muestra aún es pequeña, pero refuerza
que multisuperficie no equivale automáticamente a ventaja de equipo.

La familia media reversible de datos `inventory_snapshot_diff` completa la otra
frontera solicitada. En dos semillas, `solo_lead` y `full_team` pasan siempre
20/20; solo cierra 2/2 en una run (178,6–283,8 s; 296.683–864.627 tokens de
entrada), mientras equipo cierra 0/2 dentro de 12 runs (517,1–832,6 s;
623.564–1.599.277 tokens), promedia 2,92× el tiempo y 1,91× la entrada y deja un
F401 en la segunda semilla. Codex directo obtiene 20/20 en su primera semilla
con 159,3 s/241.922 tokens. Esta evidencia apoya mantener `solo_lead` para
trabajo acotado, reversible y de una sola rama; no apoya relajar el selector
hacia más equipo.

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

La retirada o incompatibilidad de un modelo tampoco usa el recovery genérico de
adapter: `model_unavailable` abre el gate de lifecycle descrito arriba. El modo
autónomo no puede resolverlo porque cambiar modelo/familia/tier es una decisión
de producto, no un default operacional.

FrugalGPT aporta evidencia primaria de que combinar selección, generación y cascadas puede reducir coste manteniendo calidad en sus datasets. No prescribe thresholds universales. Fuente: [PAPER-2](ORCHESTRATION_SOURCES.md#paper-2-frugalgpt).

Vigilar `cascade pile-up`: degradación o rate limit del barato puede trasladar toda la carga al caro. En AI Teams lo mitigan `provider_governor`, recovery acotado y cap diario.

## Sesiones CLI persistentes: experimento, no política productiva

Las columnas `runs.session_id_before/after` existen, pero el runtime productivo
continúa stateless: Claude usa `--no-session-persistence`, Codex `--ephemeral` y
Antigravity `--new-project`. El probe local del 2026-07-21 confirma reanudación
por ID explícito en Codex 0.128.0 y Antigravity 1.1.5; Claude no está instalado
en esta máquina.

`aiteam/session_continuity.py` define el gate experimental. Una sesión solo puede
cruzar a otra run si coinciden agente, issue, adapter, perfil, provider, modelo,
canal y workspace, la run anterior terminó y existe opt-in explícito. Se prohíben
selectores globales como `--last` o `--continue`: podrían capturar conversaciones
ajenas. `scripts/benchmark_cli_sessions.py probe` inspecciona capacidades sin
llamar a modelos; `audit` exige un A/B stateless/resumed balanceado con varias
semillas, usage y duración comparables, recuerdo del hecho inicial, aplicación
de la instrucción nueva y ausencia de la instrucción revocada.

El canario real Codex GPT-5.5 de dos semillas mantiene calidad e aislamiento en
ambos brazos, pero resumed usa 45.850-45.858 tokens de entrada frente a
22.954-22.958 stateless: mediana de ahorro `-99,75 %`. La duración solo mejora
`3,74 %` (16,14-18,67 s frente a 16,83-19,33 s). Incluso los tokens no cacheados
son ligeramente mayores en resume. Por tanto no se activa producción.

Antigravity 1.1.5 demostró el transporte: un `--log-file` explícito entrega el
conversation UUID y `--conversation <UUID> --print` recordó el marcador en
2,913 s. No expone usage comparable, así que no puede probar ahorro de cuota y
también permanece stateless. Claude no está instalado. Los recibos viven en
`benchmarks/results/cli_sessions/`; cualquier reevaluación futura debe repetir
el A/B tras cambios de CLI/modelo, mantener IDs explícitos y exigir fallback
stateless, caps de edad/turnos y los mismos gates de contaminación.

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

El contrato operativo vive en `aiteam/context_curator.py`; `RunExecutor` solo
integra la creación de la issue delegada y aplica la transición resultante. El
resto se implementa mediante delegation metadata, focus paths, RBAC, user
directives, dieta de payload y resumen causal incremental. El curador recibe
un rango exacto del hilo padre y solo él puede emitir `append_context_summary`;
el módulo verifica rango, issue y ratio antes de persistir, y el executor aplica
la transición devuelta y deniega el cierre sin
artefacto. El canario real `scripts/benchmark_context_curator.py` muestra una
frontera dependiente de modelo/proveedor: en `auth_migration`, Codex mini conserva
7-8/9 anclas en tres semillas (0/3), `gpt-5.5` obtiene 2/2 y Anthropic Haiku 3/3;
en `queue_rollout`, Codex mini obtiene 3/3 y senior/Antigravity 1/1 cada uno.
Cada bloque añade un índice causal v1 con unidades tipadas y provenance mínima
por comentario. Producción valida la forma de relaciones que no deben perderse:
owner/deliverable/accepted_by, metric/threshold/window/action y motivo de
descarte. Esto no detecta una afirmación plausible pero falsa ni obliga a
inventar unidades si el slice carece de hechos causales; la rúbrica oculta sigue
siendo el spot-check semántico. El Markdown conserva el ratio histórico ≤30 % y
el índice tiene un cap separado de 4 KiB. Los primeros recibos reales v1 pasan
auth y queue 9/9 en una run, con ratios Markdown 12,77 % y 12,96 %, pero ratios
totales 47,56 % y 54,37 % al contar JSON y UUID: la trazabilidad tiene coste de
contexto y debe seguir midiéndose. Por ello, nuevas contrataciones de curador sobre
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

## Skills aprendidas gobernadas

Una observación recurrente no se convierte directamente en autoridad. Solo el
rol Lead puede emitir `propose_skill`, con nombre estable, cuerpo conciso, roles
afectados y evidencia concreta. El registry la persiste con origen `learned` y
estado `proposed`; por tanto no entra en `AITEAM_AGENT_SKILL` ni afecta ninguna
run hasta que el owner la active desde Config.

El gobierno es independiente del proveedor que transporte al Lead. El control
plane impone 24 skills vivas por proyecto, 8 aprendidas, 8 KB por aprendida y
48 KB totales de contexto activo. El owner puede editar, activar, retirar o
borrar; editar conserva origen, evidencia y run fuente. Activar añade aprobación
explícita. Las skills locales refinan el contrato del rol, pero
`payload.user_directives` aparece después en el prompt y prevalece ante cualquier
contradicción.

## Paralelismo

Paralelizar por independencia informativa, no por disponibilidad de roles:

`valor ≈ latencia evitada + cobertura adicional - coste de ejecución - síntesis - riesgo de conflicto`

No ejecutar en paralelo agentes que escriban la misma superficie sin aislamiento. Cada rama necesita owner, scope, evidencia y aceptación.

La auditoría offline del 2026-07-22 establece un baseline, no cierra la
validación. El instrumento
`scripts/audit_parallel_channels.py` inspeccionó siete SQLite `full_team`: 75
runs registradas, 72 con intervalo temporal comparable y tres excluidas por no
tenerlo. Las siete muestras son de una sola raíz y un solo proveedor; no aparece
espera serial elegible entre raíces/proveedores distintos, solapamiento elegible
ni error de rate limit. Esto no demuestra que el paralelismo carezca de valor:
demuestra que el histórico retenido no ejercita una oportunidad que el selector
pudiera admitir. La validación sigue abierta en bloques pequeños: provenance
exacta de elegibilidad, auditor sin aproximaciones, A/B hermético de corrección,
trigger vivo multi-raíz/multi-pool, A/B vivo equivalente y decisión final.
`AITEAM_PARALLEL_CHANNELS` continúa opt-in, con batch máximo efectivo 3 y los
invariantes de agente, raíz, pool de capacidad y work slot. Ningún canario vivo
consume modelos antes de observar contención elegible real. Recibo del baseline:
`benchmarks/results/parallel_channels/parallel-channel-capacity-v1.json`.

El primer bloque de la validación abierta ya persiste provenance exacta en
`dispatch_candidate_decisions`. Cada snapshot secuencial o paralelo conserva
batch, wakeup, raíz, pool de capacidad efectivo, rol/work slot, timestamps,
decisión y razón estable; un rechazo existe aunque nunca llegue a crear una run.
`ready_at` significa la primera observación real de readiness por el scheduler:
es una frontera conservadora para no atribuir a la cola tiempo que pudo estar
bloqueado antes. Dependencias y checkout activo son `not ready`; entre candidatos
listos se distinguen mismo agente, raíz, pool, segundo work slot y límite de
batch. La tabla es aditiva y la aplica el schema idempotente al abrir DB antiguas.
Este bloque mejora observabilidad/corrección, pero no prueba beneficio de
latencia ni autoriza el default paralelo.

El segundo bloque elimina la inferencia en DB nuevas. Antes de cada reclamación
secuencial, el loop persiste el prefijo de cola considerado con contrato
`candidate_queue_prefix_v1`: primer candidato listo seleccionado, restantes
listos rechazados por `sequential_mode`, y candidatos bloqueados sin `ready_at`.
El auditor v2 calcula por separado espera total desde `requested_at`, espera
lista desde la primera observación del scheduler y la fracción paralelizable
entre una oportunidad elegible y la selección posterior del mismo wakeup. Usa
la raíz, pool y work slot persistidos; no carga dependencia ni checkout a esa
fracción y deduplica oportunidades repetidas por wakeup.

La calidad declarada es `exact` sólo cuando existe el contrato de snapshot,
`partial_exact` para decisiones antiguas aisladas y `approximate` cuando no hay
provenance. Sólo espera positiva `exact` puede abrir el trigger del canario vivo;
una señal aproximada se informa aparte. El recibo
`benchmarks/results/parallel_channels/parallel-channel-capacity-v2.json`
reprocesa las siete DB históricas: siguen siendo `approximate`, con 75 runs y
cero espera paralelizable inferida. El default permanece secuencial. El próximo
bloque es el A/B hermético sobre el `HeartbeatLoop`, que valida corrección sin
consumir proveedores.

El tercer bloque ejecuta ese A/B con `scripts/benchmark_parallel_heartbeat.py`.
Ambos brazos parten de la misma cola de cuatro raíces y cuatro pools. Engineer
y Reviewer son los dos candidatos de work slot; dos scouts son ramas de lectura
y uno falla deliberadamente. El secuencial completa cuatro runs sin solapamiento.
El paralelo selecciona Engineer más ambos scouts, registra exactamente los tres
pares de solapamiento de ese batch y rechaza Reviewer por `second_work_slot`;
después ejecuta Reviewer aunque una rama anterior terminara `failed`.

Los brazos producen los mismos estados terminales: tres runs completadas, una
fallida, tres wakeups `finished`, una `failed`, tres issues `done` y una
`blocked`. Ambos conservan cobertura exacta de dispatch del 100 % y cero runs,
wakeups o checkouts activos. El recibo
`benchmarks/results/parallel_channels/parallel-heartbeat-hermetic-v1.json`
marca `correction_validated=true`, pero niega afirmación de rendimiento, trigger
vivo y cambio de default. El siguiente gate sigue siendo observar contención
exacta en un proyecto real comparable antes de consumir modelos.

Anthropic describe mejoras grandes en su sistema de research multiagente, pero también un consumo de tokens muy superior y sensibilidad alta a coordinación, prompts y herramientas. Es evidencia de ingeniería de un vendor sobre su sistema, no una garantía general para coding agents. Fuente: [ANTH-2](ORCHESTRATION_SOURCES.md#anth-2-multi-agent-research).

## Extensiones MCP gobernadas

La autoridad pertenece al rol configurado, nunca al adapter. El mismo descriptor
MCP se filtra por estado `active`, versión pineada, health vigente, rol y
capability `external_mcp`; después cada adapter traduce el grant a su transporte.
La falta de transporte aislado produce un deny auditable para esa run, no cambia
el Lead, no contrata otro proveedor y no amplía privilegios silenciosamente.

La activación ejecuta un `initialize` MCP real sobre stdio con timeout, sin shell
y con entorno mínimo. Una fuente que implique instalación (`npx -y`, pipes o
comandos compuestos) no puede activarse. El registry conserva nombres de
variables requeridas, nunca sus valores. Codex usa overrides `-c` efímeros;
Claude usa un archivo temporal `--mcp-config` junto con
`--strict-mcp-config`, contrato documentado por Anthropic en [ANTH-3](ORCHESTRATION_SOURCES.md#anth-3-claude-code-mcp-por-run).

El health enumera `tools/list` completo con paginación acotada. Nombres
duplicados, cursores repetidos o inventarios vacíos fallan cerrados. Las
annotations del servidor se muestran como información, pero no conceden acceso:
después del probe el owner aprueba una allowlist positiva y clasifica cada tool
como lectura o escritura. Un rol sin `repo_write` solo recibe las aprobadas como
lectura; una tool nueva, no aprobada o aprobada como escritura queda denegada.
El health caduca a las 24 horas.

Codex recibe `enabled_tools`. OpenCode recibe una configuración inline efímera:
deniega `servidor_*` y permite solo cada `servidor_tool` aprobada, además de
denegar globalmente shell, edición, subagentes, directorios externos y share.
Claude no dispone de una allowlist positiva MCP equivalente y por ello el
servidor completo se omite para esa run, incluso si el inventario cacheado
parecía seguro. `tool_access` registra la decisión del servidor y cada tool
individual. El probe sella además ejecutable, argumentos y archivos ejecutados;
un cambio de digest invalida el grant aunque el servidor siga declarando la
misma versión.

Las anotaciones pertenecen al servidor aprobado y no demuestran por sí mismas
que su implementación sea honesta; el owner sigue siendo la frontera de
confianza. Pinning, health y allowlists impiden ampliaciones accidentales entre
runs, no convierten código de terceros en código confiable.

Config expone el ciclo operativo al owner: probar/activar, clasificar tools,
comprobar, retirar y reactivar. Reactivar vuelve a `approved`, elimina el health
anterior y obliga a probar de nuevo. El heartbeat comprueba como máximo un
servidor vencido por tick; los fallos usan backoff y el tercero retira el
servidor. Propuestas idénticas ya rechazadas, retiradas o existentes se suprimen
con comentario al Lead y activity log, evitando loops sin impedir que el owner
reactive explícitamente desde Config.

Esta vertical cubre propuesta → aprobación → pin → health → allowlist →
grant → uso auditado → recovery/retiro.

OpenCode Zen se gestiona como gateway gratuito, no como API sin coste infinito.
Su JSONL aporta tokens de entrada/salida, razonamiento, caché e ID explícito de
sesión; AI Teams los agrega junto con runs, duración y errores de cuota bajo el
perfil exacto, aunque `actual_cost_cents` sea cero. Como Zen no publica una
capacidad estable, no se calcula porcentaje ni ETA salvo que el owner configure
un límite demostrado. El ID de sesión se conserva como telemetría, no autoriza
reanudar: continuidad exige el mismo scope durable y un A/B favorable.

La superficie `serve`/SDK de OpenCode permite eventos y sesiones explícitas. El
A/B vivo v1 de `serve` + `run --attach` frente a CLI directo pasa 3/3 por brazo
con seis sesiones aisladas; attached reduce la mediana 7,50→2,92 s y mantiene
tokens equivalentes. Un canario adicional con el SDK oficial 1.18.4 observa la
sesión `busy`, confirma `POST /session/:id/abort`, vuelve a `idle`, mantiene
health, completa otra inferencia en la misma sesión, la elimina y garantiza el
teardown. No se confunde cancelar la petición cliente con abortar la sesión.
El gate JSON Schema falla: algún modelo puede devolver el objeto textual exacto,
pero el servidor lo marca `StructuredOutputError` y no expone structured
output. Por
separado, el canario de fallos suspende el proceso nativo: el puerto permanece
abierto pero health expira; después termina ese PID, reinicia en el mismo puerto,
recupera el mismo ID como `idle` y completa otra inferencia. El health MCP local
prueba proceso vivo, `initialize`, `tools/list`, inventario de una tool aprobada
y otra no aprobada, deny del namespace y allow exacto; servidor e hijo terminan.
Los endpoints experimentales de tools 1.18.4 solo devolvieron built-ins pese a
`/mcp=connected`, por lo que no se usan como evidencia del inventario MCP.
Producción conserva CLI efímero. La matriz final completa la repetición exigida:
tres semillas, seis sesiones distintas, override y revocación exactos,
historiales frescos sin marcadores ajenos y cleanup total.
Sin embargo, DeepSeek, Laguna, MiMo, Nemotron y North fallan el mismo JSON Schema
con `StructuredOutputError` y `structured=null`; que algunos produzcan JSON
textual válido de forma no estable no satisface el contrato del proveedor. La
evaluación termina con decisión negativa: no se construye autorecovery
productivo sobre esta versión.
El canario solo demuestra recoverability manual y health de un MCP local, no de
MCPs externos. `serve` tampoco aporta el sandbox necesario para Engineer.
Fuentes: [FREE-1](ORCHESTRATION_SOURCES.md#free-1-gateway-catálogo-y-privacidad)
y [FREE-3](ORCHESTRATION_SOURCES.md#free-3-cli-mcp-sesiones-y-telemetría).

La vía gratuita es híbrida. `opencode_zen_free` declara cinco modelos; Laguna
permanece manual/probe-gated tras fallar 0/3 y Big Pickle sigue `rejected`.
`gemini_api_free` reutiliza una key Google del owner y
`groq_api_free` usa un runtime OpenAI-compatible con key Groq propia. Son
perfiles distintos de sus equivalentes pagados aunque compartan proveedor:
health, modelo, privacy label, usage, cuota, 429 y provenance nunca se mezclan.
El vault local inyecta el secreto solo al proceso; SQLite y prompts conservan
únicamente la referencia. GPT-OSS usa schema estricto; modelos Groq sin soporte
estricto usan JSON Object Mode y el mismo validador `submit_work`. Recuperar
JSON desde un envelope no basta: todas las APIs validan recursivamente el
contrato neutral antes de materializar ops. Qwen dispone de un solo repair de
formato; el segundo objeto debe conservar exactamente las ops y el status del
primero, suma su usage y falla cerrado si modifica autoridad. Los modelos con
schema estricto no reciben repair local. Fuente:
[FREE-4](ORCHESTRATION_SOURCES.md#free-4-api-keys-gratuitas-y-límites).

Los adapters API no heredan MCP configurado en un CLI. Devuelven únicamente el
contrato neutral y no reciben autoridad directa sobre filesystem o servidores;
un loop MCP/API futuro deberá pasar los mismos grants, allowlist y auditoría que
Codex/OpenCode, no invocar tools remotas del proveedor por comodidad.

### Compatibilidad modelo, rol y modo de ejecución

El tier y `best_for` son señales de ranking, no permisos. La selección efectiva
debe evaluar como un único contrato el perfil, modelo, rol, criticidad, perfil
de run, clasificación de datos y capacidades requeridas. Esa decisión debe ser
idéntica en bootstrap, hiring, Equipo, guardado, reconcile, dispatch y fallback.
Una incompatibilidad se bloquea antes de consumir modelo y devuelve un código,
una explicación para el owner y alternativas ejecutables; nunca se corrige con
un cambio silencioso de adapter o `lead_builtin`.

La matriz provisional de los canales gratuitos, pendiente de canarios vivos,
es deliberadamente más estrecha que su capacidad comercial:

| Perfil/modelo | Roles máximos provisionales | Bloqueos obligatorios |
|---|---|---|
| Zen / Nemotron 3 Ultra | Lead, arquitectura, quorum y review read-only | Engineer/Worker y Lead `solo_lead`; datos confidenciales |
| Zen / DeepSeek V4 Flash o MiMo V2.5 | Reviewer y QA read-only | Lead/quorum, test_designer, escritura y datos confidenciales |
| Zen / North Mini Code | Scouts y context curator | Lead/quorum, review crítico y escritura |
| Gemini Free / 3.5 Flash | Reviewer, QA y test design | Lead/quorum hasta calibración; MCP externo |
| Gemini Free / Flash-Lite | Scouts y context curator | Lead/quorum y review crítico |
| Groq Free / GPT-OSS 120B | Reviewer, QA y test design | Lead/quorum hasta calibración; MCP externo |
| Groq Free / Qwen 3.6 o GPT-OSS 20B | Scouts y context curator | Lead/quorum y review crítico; Qwen usa repair único sin cambio de autoridad y máximo medium |

OpenCode es read-only por diseño actual. Las APIs, en cambio, sí pueden producir
operaciones estructuradas de archivo que el executor materializa bajo RBAC; no
deben clasificarse genéricamente como incapaces de escribir. Lo que no poseen es
un loop MCP gobernado. Capacidad de workspace, transporte MCP, privacidad y
calidad de modelo son ejes independientes y deben producir razones de deny
distintas.

Una prueba del perfil o del modelo default no verifica todo el catálogo. Una
opción habilitada en Equipo necesita ID descubierto o vigente, compatibilidad
con el rol y un contrato estructurado demostrado para ese modelo. Los estados
de catálogo, verificación viva, rate limit, retirada e incompatibilidad no se
colapsan en un único booleano `available`. El backlog de enforcement y pruebas
de esta auditoría vive en `task.md`, bloque P0.3.

La decisión pura ya vive en `aiteam.model_compatibility`. Recibe perfil, opción
de modelo, rol, run profile, criticidad, clasificación de datos y capacidades
requeridas; devuelve `allowed`, código estable, explicación española,
capacidades efectivas y alternativas ejecutables del mismo perfil. No consulta
DB, filesystem, secretos ni red. `POST /api/user-adapters/compatibility` y el
catálogo por rol proyectan ese resultado sin modificar `available`: estar
inventariado no equivale a estar autorizado. Los perfiles custom pueden
declarar `model_options` con tier, capacidades y allow/deny por rol.

El contrato considera que las APIs pueden materializar file ops estructuradas.
OpenCode se mantiene read-only por política expresa. QA puede operar como
revisión adversarial de lectura, mientras Engineer, Test Designer y Lead en
`solo_lead` requieren escritura. `mcp_operator` y cualquier necesidad
`external_mcp` exigen transporte marcado `governed`; las tools nativas del
proveedor no satisfacen esa condición. Los canales `non_confidential_only` o
`provider_free_tier` fallan cerrados sin clasificación y deniegan datos
confidenciales/restringidos.

La detección de necesidad vive en `aiteam/mcp_needs.py` y consume solo
`agent_reports` válidos emitidos por el assignee. Un `capability_gap` explícito o
un report bloqueado cuyo item sea realmente no verificable basta para sugerir;
señales de menor confianza necesitan dos runs distintas con la misma capacidad.
El dedupe es por raíz+capacidad: huecos distintos no se suman y una sugerencia
antigua no silencia otra nueva. La evidencia se persiste como comentario. Si el
Lead ya tiene run/wakeup vivo se reutiliza; si no, se crea una wakeup
`mcp_need_suggested`. El payload declara `suggestion_only`: obliga a probar
primero herramientas existentes y nunca salta propuesta, owner gate, health ni
allowlist.

El catálogo curado vive en `aiteam/mcp_catalog.py` y empieza con tres contratos
oficiales: GitHub en modo read-only, Playwright headless/isolated y Filesystem
confinado al workspace. Solo enumera ejecutables que deben existir previamente;
no contiene `npx`, Docker, shell, auto-install ni `latest`. Distingue versión de
distribución de la versión declarada por `serverInfo`, porque el health valida
esta última y el digest sella el artefacto real. Para paquetes Node, la propuesta
resuelve el shim instalado al entrypoint JavaScript, valida la versión de
`package.json` y pasa ese archivo explícitamente a `node`; así el digest no queda
reducido al wrapper `.cmd`/shell.

Un Lead puede enviar `catalog_id` más justificación. El executor expande nombre,
source, versión, argumentos, entorno y roles desde el descriptor y rechaza toda
sustitución. Después crea la misma interacción pendiente que una propuesta
ad-hoc. Aceptar deja el servidor en `approved`: todavía debe superar health y
recibir una allowlist de tools del owner. Config solo lista el catálogo y enlaza
sus fuentes; no ofrece una acción de instalación o aprobación rápida. Fuentes y
fecha de revisión: [MCP-CAT-1](ORCHESTRATION_SOURCES.md#mcp-cat-1-github-mcp-server),
[MCP-CAT-2](ORCHESTRATION_SOURCES.md#mcp-cat-2-playwright-mcp) y
[MCP-CAT-3](ORCHESTRATION_SOURCES.md#mcp-cat-3-filesystem-mcp).

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

Antes de publicar una conclusión, `scripts/benchmark_integrity.py` audita la
colección completa, no un JSON aislado. En benchmarks de código exige una matriz
brazo×semilla completa, sin duplicados, mismo caso y suite oculta comparable,
evidencia conductual determinista y análisis estático. En quorum excluye sesiones
incompletas del delta, exige el mínimo de sesiones y providers, provenance por
contribución, hard gates consistentes, contrato estructural de profundidad y
signo estable; produce mediana y rango. Todo reporte declara además riesgo de
Goodhart. Un resultado puede conservar valor de liveness o diagnóstico y, a la
vez, obtener `conclusion_allowed=false`.

La auditoría no confía en el booleano autorreportado de independencia. Deriva
la evidencia de clases admitidas —tests conductuales deterministas, análisis
estático, contrato estructural, invariantes de estado, juez causal o revisión
humana muestreada— y rechaza contratos únicamente léxicos aunque afirmen lo
contrario. `conclusion_allowed` conserva evidencia histórica comparable;
`promotion_allowed` es más estricto y exige además riesgo de Goodhart y una
lista no vacía de constructos no medidos. Los harnesses que cambian defaults
deben consumir este segundo gate.

El harness de código v4 explicita como evaluadores la suite oculta conductual y
Ruff; esa evidencia histórica sigue siendo válida aunque los JSON anteriores no
incluyeran el bloque de metadatos. El scorer de quorum continúa midiendo la misma
rúbrica léxica para no mover el baseline, pero añade en paralelo
`plan_depth_contract`. No existe aún un juez factual independiente: incluso una
serie admisible conserva riesgo de Goodhart `material`, no certeza semántica.

El cockpit consume las mismas definiciones de `orchestrator_evals` mediante
`/api/loop-health`. Solo eleva señales con acción operativa: una raíz sin run,
wakeup ni interacción abre el filtro de issues pendientes; runs o wakeups
activos durante más de 30 minutos y quorum inconsistente dirigen a Runs para
inspeccionar evidencia y provenance. La actividad reciente se conserva como
telemetría informativa y no activa atención.
No mantiene una segunda implementación de liveness ni presenta contadores sin
una decisión asociada.

### Benchmark de planes `lead_quorum`

Evaluar el valor incremental sobre el mismo artefacto: revisión A congelada,
auditorías independientes y revisión B aceptada. La rúbrica específica permanece
fuera del contexto de los modelos y mide cobertura, hard gates, diversidad,
tokens, coste, latencia y provenance. No usar tests de código como métrica
principal ni concluir con una semilla. El harness vive en
`scripts/benchmark_quorum_plans.py`; los casos versionados cubren migración
SQLite, autorización multi-tenant y failover de proveedores.

Una ejecución incompleta también es un resultado de liveness: debe conservar
Plan A, estado de sesión, contribuciones y errores de runs. Para modificar
thresholds se exigen al menos tres sesiones aceptadas por familia, dos
proveedores válidos con provenance completa y mediana más rango del delta. Las
runs degradadas cuentan para disponibilidad/liveness, pero no para el A/B. No se
cambia política si el signo es inestable, existe efecto techo o Plan B no supera
consistentemente los hard gates.

La calibración de julio de 2026 mantiene los thresholds. `provider_failover`
obtuvo mediana `+6,52` y rango `-8,70..+8,70` en cuatro sesiones aceptadas;
`multitenant_authorization_v2`, mediana `+8,69` y rango `0..+8,70` en tres, pero
solo dos Plan B superaron su hard gate. Una sesión `accepted` prueba que el
contrato durable de quorum se completó; no equivale a que una rúbrica semántica
externa acepte el plan. SQLite conserva valor diagnóstico de liveness, pero aún
no alcanza la muestra mínima para una conclusión A/B.

La auditoría automática reproduce esa decisión: `provider_failover` encuentra
cuatro sesiones aceptadas, dos incompletas, dos providers, mediana `+6,52` y
rango `-8,70..+8,70`, pero devuelve `conclusion_allowed=false` por signo
inestable y por carecer los resultados históricos del contrato estructural
nuevo. En cambio, `accessible_checkout_form` conserva una matriz 2×2 completa y
evidencia conductual independiente, por lo que su conclusión direccional sí es
admisible con riesgo residual de sobreajuste a la suite oculta.

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
| Adapters | `aiteam/project_adapters.py`, `aiteam/adapters/` (incluye Zen y BYOK OpenAI-compatible) |
| Provider health | `aiteam/provider_governor.py` |
| MCP gobernado | `aiteam/mcp_runtime.py`, `aiteam/extensions.py`, `aiteam/mcp_catalog.py`, traducción Codex/OpenCode en adapters |
| Detección MCP | `aiteam/mcp_needs.py`, reconciliación en `aiteam/heartbeat/loop.py` |
| Context curator | `aiteam/context_curator.py`, proyección en `aiteam/db/wake_payload.py` |
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
