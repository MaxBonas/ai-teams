# Orquestación multi-modelo en AI Teams

Actualizado: `2026-07-23`

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

### Política de modelos por tier y adapter (revisión 2026-07-22)

El tier describe la responsabilidad del rol, no el proveedor. El modelo se
resuelve dentro del adapter que el owner eligió y nunca cambia la autoridad del
Lead. Los aliases de producto tampoco son intercambiables entre canales: un ID
de API, un slug de Codex y una etiqueta aceptada por Antigravity son contratos
distintos.

El tier no se deduce de un único precio. La política
`capability_economy_speed_v1` conserva tres ejes auditables: banda de capacidad
y roles compatibles; economía según precio API, presión de cuota de suscripción
o recursos locales; y velocidad oficial o medida por canal. Un valor
`unknown` de velocidad queda explícito y exige medición, nunca una estimación.
Las herramientas y la autoridad siguen siendo hard gates separados: un Tier 1
no obtiene escritura/MCP y un Tier 3 no los pierde solo por su precio.

Roles canónicos:

- Tier 1: `lead`, `team_lead`, `lead_executor`, `architect` y
  `quorum_auditor`; `quorum_senior` solo se conserva para proyectos legacy;
- Tier 2: `engineer`/`software_engineer`, `reviewer`/`code_reviewer`, `qa`,
  `test_designer` y `mcp_operator`; `qa` solo se materializa como gate
  adversarial proporcional al riesgo, no en el `full_team` por defecto;
- Tier 3: `worker`, `file_scout`, `web_scout` y `context_curator`;
  `researcher` solo se acepta al leer proyectos legacy;
- `test_runner` es determinista: ejecuta procesos y no recibe modelo.

`worker` es aquí un ejecutor barato de análisis/reporting, no un Engineer barato:
es read-only, recibe `repo_read`, no ocupa work slots de implementación y debe
devolver el trabajo material a un owner Tier 2. `worker`, scouts y test runner no
pueden cerrar `done` sin un `AGENT-REPORT` válido de la run y del assignee. El
runtime permite una corrección de formato; un segundo incumplimiento bloquea y
despierta al supervisor, sin aceptar summaries como evidencia.

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
| Gemini API | `gemini-3.1-pro-preview` | `gemini-3.6-flash` | `gemini-3.5-flash-lite` | Pro sigue preview; 3.6 Flash y 3.5 Flash-Lite son estables y sustituyen los defaults anteriores. |

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
| Antigravity subscription 1.1.6 | `gemini-3.1-pro-high` | `claude-sonnet-4-6` coding; `gemini-3.5-flash-high` review/QA | `gemini-3.5-flash-low` | IDs ejecutables confirmados por `agy models`; Equipo muestra etiquetas legibles, pero persiste y ejecuta estos slugs. Sin recibo headless comparable de tokens. La actualización conserva el catálogo, pero vuelve stale la calibración 1.1.5 de Sonnet. |
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
2026-07-22 pasa 6/6 gates. Codex 0.145.0 y su cache 0.145.0 contienen los tres
IDs declarados Sol/Terra/Luna sin duplicados; esa cobertura consume el snapshot
actual y no health histórico. El registro comprueba además el contenido de los
recibos promovidos: identidad, versión, rol, esfuerzo, matriz, resultado y
fuentes exactas. Un modelo declarado ausente o un agregado manipulado falla
cerrado. Nada de ello promueve defaults por discovery.

La cobertura conductual se audita por separado mediante
`scripts/audit_model_evaluation_coverage.py`. Deduplica aliases a roles
semánticos, exige calibración fresca exacta para declarar `calibrated`, conserva
screenings o resultados incompletos como `partial`, separa los contratos que
necesitan `external_mcp` como `requires_tool_fixture` y no crea deuda automática
para candidatos manual/probe-gated. En el baseline del 2026-07-22 hay 46 modelos
y 131 destinos `best_for`: 8 calibrados, 5 parciales, 32 canarios ejecutables,
4 fixtures de tools, 3 candidatos manuales y 79 bloqueados por health/canal. La
antigüedad por sí sola nunca retira un modelo; sí puede volver stale su evidencia.

M.7.1 usa `scripts/benchmark_critical_default_roles.py` para la cohorte premium:
cada par exacto de Sol o Gemini 3.1 Pro High con `architect`, `lead`,
`lead_executor`, `quorum_auditor` o `team_lead` necesita dos familias causales
por tres semillas. El juez determinista queda fuera del prompt, exige schema
exacto, nombra cada ancla ausente y puede reevaluar recibos sin inferencia.
La primera matriz v1 de `lead` no promovió candidatos: Sol obtuvo 4/6 y Pro High
5/6 por omisiones causales. Esos recibos se conservan como historial y no se
mezclan con los agregados v2.

La segunda matriz exacta, `architect`, completa 6/6 tanto en Sol como en Gemini
3.1 Pro High. Cada agregado vincula las seis fuentes y el SHA-256 de cada
respuesta; el auditor de cobertura vuelve a comprobar identidad, caso, semilla,
versión, resultado y hash. Una mutación degrada la evidencia a `partial`.
Los dos pares quedan calibrados para calidad por rol, pero sus agregados
mantienen `default_change_allowed=false`: M.7.4 debe observar health, cuota,
economía y snapshot antes de habilitar un default. La cobertura del 2026-07-23
queda en 10 calibrados, 5 parciales, 30 canarios, 4 fixtures, 3 manuales y 79
bloqueados.

La matriz `lead_executor` evita exigir que el modelo repita su etiqueta de rol:
valida schema, pasos no vacíos, evidencia material, hechos causales y ausencia
de afirmaciones de ejecución/delegación ficticias. Tras reevaluar las mismas
respuestas sin otra inferencia, Sol completa 6/6 y queda calibrado; Gemini 3.1
Pro High queda 5/6 porque una muestra omite la ventana de 10 minutos. El segundo
se conserva como diagnóstico y no se reintenta hasta obtener un pase. La
cobertura pasa a 11 calibrados, 5 parciales y 29 canarios pendientes.

El contrato productivo Tier 1 añade una pasada silenciosa y común de retención
causal: scope/cohorte, tenant/autorización, métricas con valor+unidad+ventana+
acción, owner+aceptador, dependencias, rollback y escalado. El screening pareado
v1→v2 mejora las cinco familias débiles de 1/3 o 2/3 a 3/3. Después se ejecutan
los casos complementarios, no un re-roll selectivo: 30/30 muestras v2 pasan y
los cinco pares antes incompletos alcanzan matrices 6/6.

Así, los diez pares exactos de Sol y Gemini 3.1 Pro High con `architect`,
`lead`, `lead_executor`, `quorum_auditor` y `team_lead` quedan calibrados. El
harness impide agregar versiones mezcladas y la cobertura comprueba versión de
prompt, identidad, fuentes y hashes. El snapshot 2026-07-23 queda en 18
calibrados, 4 parciales, 23 canarios, 4 fixtures, 3 manuales y 79 bloqueados.
Esto valida calidad por rol; no autoriza todavía cambiar defaults, que siguen
sujetos al snapshot vivo y los gates de M.7.4.

El screening v1 de Codex Luna descubrió tres defectos de contrato antes que una
limitación concluyente del modelo: `worker` no tenía skill cargable, el prompt
consolidado no exigía el `AGENT-REPORT` exacto y `file_scout` recibía una tarea
de diagnóstico/recomendación incompatible con su frontera neutral. El contrato
v2 añade cobertura causal y cierre exacto para Tier 3, crea la skill de worker y
reformula file scout como preguntas de evidencia, sin ampliar autoridad.

Con Luna `low`, `worker` completa 3/3 en una run. `web_scout` completa 3/3 en
una run, conserva 8/8 hechos, llama `release_advisory_lookup` mediante MCP local
aprobado y health-checked y no puede llamar la tool de escritura denegada. Ambos
pares quedan calibrados. `file_scout` retiene el contrato semántico 3/3, pero
solo 1/3 cierra en una run; las otras dos necesitan la única corrección de
formato. Queda `partial`, sin otro ajuste dirigido a las mismas semillas. Los
agregados v2 vinculan tres fuentes y hashes de artefacto, y la cobertura detecta
tampering. El snapshot pasa a 20 calibrados, 4 parciales y 21 canarios.

Terra `medium` completa cinco contratos Tier 2 exactos sobre Codex 0.145.0.
Reviewer pasa 3/3 ciclos durables de rechazo→fix→aprobación, con mediana 64,0 s
(62,844–93,094), 113.509 tokens de entrada y 8.230 de salida. Engineer pasa
27/27 tests ocultos, Ruff limpio y una run por semilla, con mediana 62,921 s
(50,563–70,797) y 116.800/8.812 tokens. QA pasa 3/3 ciclos adversariales y
30/30 checks: materializa tests que fallan contra el defecto, preserva
producción, persiste `changes_requested` y aprueba/retira los tests tras el fix;
mediana 116,048 s (115,953–133,938), 773.932 tokens input y 12.946 output.
Test Designer pasa 3/3 suites,
24/24 checks y 15/15 ejecuciones contra cinco mutantes ocultos; mediana 73,172 s
(71,235–92,328), 404.062 input y 7.956 output. MCP Operator pasa 3/3 y 36/36
checks con allow/deny reales, llamada read, cero write, fallo de versión y
recovery activo; mediana 42,359 s (27,859–49,593), 342.171 input y 4.424 output.
Los cinco pares quedan calibrados sin extrapolación. El coste marginal de suscripción es 0 y
esos tokens expresan presión de cuota, no precio API.

La frescura de catálogo/health y la frescura de calidad son contratos distintos.
`aiteam.model_calibration` registra por par exacto perfil+modelo+rol la fecha,
versión y recibos que autorizaron Luna para `context_curator` y Sonnet 4.6 para
Engineer. El auditor mensual falla cerrado
para promociones no registradas y marca `stale` al superar 30 días, encontrar
fecha futura, cambiar/no observar versión o perder evidencia. El recibo anterior
encuentra las tres entradas frescas. Tras actualizar Codex a 0.145.0, el A/B
auth+queue recalibró Luna/`context_curator`; el recibo actual pasa los seis gates
incluida la matriz de tiers. `stale` abre
recalibración, pero conserva
`existing_default_action=unchanged`: la edad por sí sola no demuestra que un
default sea inejecutable ni autoriza degradarlo silenciosamente a `manual-only`.
Eso requiere una regresión de catálogo, health o calidad comparable.

El follow-up durable v4 ejercita el runtime productivo sobre el mismo defecto y
la misma corrección. Flash High y 3.6 Medium completan 3/3 ciclos
`changes_requested` → fix materializado por el Lead → `approved`, sin runs ni
wakeups tomados al terminar. Medium tarda 43,078 s medianos frente a 99,999 s;
la mejora de latencia no equivale a ahorro de tokens ni a capacidad de cuota,
por lo que Flash High conserva el default. El recibo agregado es
`antigravity-durable-review-v4-aggregate.json`.

Los contratos Tier 2 de Flash High ya no se extrapolan desde Reviewer. Los
harnesses conductuales de Terra se generalizan por perfil+modelo sin cambiar
casos ni jueces. Gemini 3.5 Flash High completa QA 3/3: cada semilla crea tests
adversariales que fallan contra producción rota, preserva el archivo productivo,
reporta `changes_requested`, verifica el fix, retira los tests y aprueba; suma
30/30 gates y mediana 130,733 s. Test Designer completa 3/3, pasa producción y
mata los cinco mutantes ocultos por semilla: 15/15 mutantes, 24/24 gates y
mediana 55,266 s.

Dos defectos del instrumento se corrigen sin repetir inferencia: el juez QA
acepta `active=False` en constructores, además de diccionarios, y la superficie
de archivos authored excluye `__pycache__`/`.pyc` generados por ejecutar pytest.
Los agregados enlazan fuentes y hashes y un test de tampering degrada evidencia.
Antigravity no expone tokens headless, por lo que usage permanece `unknown`.
Ambos pares quedan calibrados para calidad por rol, con
`default_change_allowed=false`; la cobertura pasa a 22 calibrados, 4 parciales
y 19 canarios.

El cierre económico Tier 3 usa el mismo contrato causal/report v2 para
Medium/`worker`, Low/`worker` y Low/`file_scout`, ya sin hardcodes de Codex ni
un `reasoning_effort` ficticio. Medium/`worker` completa 3/3 en un intento
(mediana 70,640 s) y Low/`file_scout` 3/3 (80,080 s). Low/`worker` completa
solo 2/3 (54,660 s): una semilla agota el timeout de 240 s, converge en el
segundo dispatch y repite una opción explícitamente prohibida. Por tanto queda
`partial`; el resultado negativo se conserva y no se sustituye por un re-roll.

Low/`context_curator` se mide con un contrato distinto y adecuado al rol:
`auth_migration` y `queue_rollout`, tres semillas por caso, rúbrica oculta y
persistencia SQLite real. Pasa 6/6 en un intento, con mediana 96,300 s y rango
42,300–169,700. El agregado exige exactamente las seis células, fuentes
distintas y hashes coincidentes de thread, rúbrica y artefacto. En los cuatro
pares Antigravity los tokens siguen `unknown`; los ceros de SQLite no se
presentan como consumo observado. El snapshot queda en 25 calibrados, 4
parciales, 16 canarios, 4 fixtures, 3 manuales y 79 bloqueados, siempre con
`default_change_allowed=false`.

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
- Local: el coste monetario/API, el consumo de tokens externos y la presión de
  cuota externa son siempre 0. Es una ventaja económica real y explícita del
  ranking (`zero_external_cost_local_compute`, `quota_unlimited=true`), no una
  métrica desconocida. Salud, RAM/VRAM, energía, latencia y throughput se miden
  aparte como capacidad del host: pueden impedir ejecutar o rebajar velocidad,
  pero nunca se convierten en consumo de cuota/tokens de proveedor. Una opción
  nueva no se usa hasta que el owner la instale y pruebe.

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

No se sustituyen calibraciones locales solo por novedad comercial. La matriz
auth+queue de Codex 0.145.0 usa dos casos y tres repeticiones por caso: GPT-5.5
sin override de esfuerzo queda como control histórico 6/6; Luna original obtiene
3/6 y prompt v2 4/6, también sin override. Luna `medium` v3 obtiene 6/6, 36,55 s
medianos frente a 37,65 s y reduce las medianas de entrada/salida de
22.753/1.784 a 21.495/1.700 tokens. Las ramas no fijadas no permiten atribuir
causalmente el resultado a `low`. Solo la configuración Luna `medium` exacta
hereda `context_curator`; otros roles mantienen su selección propia.

### Catálogo universal y ranking por rol (objetivo P0.M)

AI Teams debe convertir su inventario multi-proveedor en una superficie de
producto consultable y en la fuente única de recomendaciones para crear y editar
equipos. El catálogo incluye todos los modelos declarados, descubiertos,
configurados o observados históricamente, aunque estén bloqueados, retirados,
manual-only o sin calibrar. Estar visible nunca equivale a poder ejecutarse.

La unidad operativa del ranking no es el nombre comercial aislado, sino el par
exacto `(adapter_profile_id, channel/pool, model_id, canonical_role)`. El mismo
modelo vía API y suscripción comparte fabricante/perspectiva, pero difiere en
credenciales, health, tools, privacidad, coste/cuota y recovery. La UI puede
agrupar esas entradas bajo un modelo lógico; el selector no puede fusionarlas.

La proyección de catálogo debe componer, sin duplicar fuentes de verdad:

- identidad de proveedor/fabricante/perspectiva/canal/pool;
- catálogo declarado y discovery autenticado;
- configuración, health y probe del modelo exacto;
- compatibilidad de rol, run profile, criticidad, data class y capabilities;
- tier, contexto, structured output, workspace y MCP gobernado;
- calibraciones, diagnósticos negativos, samples, jueces, Goodhart y frescura;
- latencia/throughput, liveness, retries, convergencia y errores;
- precio API, presión de cuota de suscripción o recursos locales, con provenance.

Los estados permanecen ortogonales y explicables: `catalogued`, `configured`,
`adapter_green`, `model_verified`, `selectable`, `compatible`, `calibrated`,
`stale`, `manual_only`, `blocked` y `retired`. El endpoint y la pestaña Modelos
deben exponer razón, fuente, versión y fecha; no reducirlos a `available=true`.

El contrato puro M.1 vive en `aiteam.model_catalog_projection` con versión
`model_catalog_identity_v1`. Su `candidate_id` deriva de la identidad operacional
completa —perfil, organización proveedora, fabricante/perspectiva, canal, pool y
slug exacto—, por lo que API y suscripción nunca colisionan aunque transporten el
mismo modelo. Enumera fuentes `declared_catalog`, `configured_profile`,
`authenticated_discovery` y `historical_run`; cada estado publica `value`,
`reason`, `source`, `version` y `observed_at`. Discovery deja
`model_verified=false`; `compatible`, `calibrated` y `stale` permanecen
desconocidos hasta recibir rol, contexto y evidencia exactos. La conexión con
SQLite/read model corresponde a M.3 y no se finge dentro de esta función leaf.
La fusión es determinista y aplica autoridad explícita por campo:
`authenticated_discovery` > `configured_profile` > `declared_catalog` >
`historical_run`; dentro de la misma fuente vence la observación más reciente.
Una run histórica nunca degrada política o disponibilidad vigentes y cada
estado conserva la provenance del campo que produjo su valor. Duplicar un
`profile_id` con definición distinta o reutilizar un par histórico
perfil+modelo con otra identidad operacional falla cerrado. La API importa la
misma enumeración de estados del contrato y no mantiene una segunda lista.

`model_role_score_v2` es una hipótesis de política versionada que se validará en
shadow antes de modificar defaults. Produce un breakdown 0–100:

- calidad conductual e idoneidad para el contrato exacto del rol: 40 %;
- capacidad y headroom demostrados para contexto/tools/output: 15 %;
- fiabilidad, liveness y convergencia: 15 %;
- economía contextual del canal: 20 %;
- velocidad end-to-end o throughput comparable: 10 %.

El score compuesto es la suma ponderada de esos cinco componentes normalizados.
`confidence` no se esconde mediante una multiplicación opaca: se muestra aparte
y actúa como gate de auto-elegibilidad. En la versión inicial, una plaza nueva
solo puede elegir automáticamente evidencia exacta `calibrated` y no stale; los
pares `partial`, sin test o con evidencia materialmente incompleta siguen
ordenables para comparación/manual, pero no pueden ganar el default. Durante la
migración, si ningún candidato alcanza ese gate se conserva el default explícito
actual o se pide decisión al owner.

La puntuación publica por separado `confidence`, clase de evidencia, número de
semillas/casos, cobertura de tools, frescura, constructos no medidos y riesgo de
Goodhart. Un dato desconocido no se convierte en cero ni en una ventaja: queda
`unknown`, reduce confianza y puede impedir auto-selección si afecta un eje
material. Los pesos son internos y prerregistrados, no cifras de un vendor; su
activación requiere contrastarlos con calidad/coste propios y versionar todo
cambio posterior.

La economía nunca compara euros, cuota y hardware como unidades crudas. API usa
coste esperado por tarea aceptada; suscripción usa presión de la cuota del perfil
y señales de agotamiento; local usa recursos y throughput observados. La
normalización se hace sobre candidatos elegibles del mismo contexto y conserva
su fuente. El catálogo muestra el score base del rol; `selection_score` puede
aplicar el contexto del proyecto/issue —criticidad, privacidad, tools,
presupuesto y presión de capacidad— sin reescribir la evaluación base.
`selection_score` conserva el mismo breakdown y pesos, sustituyendo únicamente
los componentes contextuales por sus valores para ese proyecto/issue; no añade
bonificaciones secretas ni mezcla `confidence` dentro de la cifra.

Antes de puntuar se aplican hard gates. Un candidato automático necesita adapter
conectado y verde, modelo exacto verificado/selectable, compatibilidad completa,
política automática y evidencia fresca suficiente. Privacidad, tools,
structured output, workspace, `manual_only`, retirada, stale material o cuota
agotada pueden excluir aunque la nota sea alta. El orden de desempate es estable:
evidencia/calidad del rol, menor carga económica comparable, menor latencia y
finalmente identidad canónica.

La implementación pura vive en `aiteam.model_role_scoring`. Si los cinco ejes
están observados, `score` es la suma ponderada; si falta alguno, `score=null` y
se publica el rango mínimo/máximo posible junto con el peso conocido. Así un
unknown no se imputa como cero ni puede ganar como si fuera gratuito. La
economía exige basis específico por canal: coste API por tarea aceptada, presión
de cuota de suscripción, recursos+throughput local o presión del gateway; los
desempates económicos solo se aplican dentro del mismo grupo comparable.

`confidence` se calcula aparte con estado/clase de evidencia, semillas, casos,
cobertura de tools, frescura/version/fecha, recibos, constructos no medidos y
riesgo de Goodhart. La completitud métrica actúa como cap explícito, no
multiplica el score. Para auto se exigen al menos 3 semillas, 2 casos, clase de
juez superior a self-report, recibo durable y provenance temporal/versionada;
Goodhart material/alto o tools requeridas sin cubrir fallan cerrados. Los
constructos no medidos aplican un cap visible y acumulable, no una penalización
secreta dentro del score. El mínimo shadow para auto-
elegibilidad es 75/100, además de evidencia `calibrated`, fresca y todos los
hard gates: configuración, health, verificación exacta, selectable,
compatibilidad, política automática, privacidad, tools, workspace, structured
output y capacidad disponible. Un score 100 no puede eludir un deny. La salida
declara `rollout=shadow_only`; M.3–M.7 deben integrar, comparar y autorizar el
rollout antes de afectar una contratación.

`rank_model_role_scores` solo acepta `model_role_score_v2`, un único rol
canónico y candidate IDs no duplicados. La selección contextual reutiliza las
constantes canónicas de versión, pesos y umbral; no mantiene números paralelos.
Una métrica numérica sin fuente se convierte en `unknown` y una identidad
operacional incompleta no puede puntuarse.

M.3 materializa esa composición en `aiteam.model_catalog_read_model` con versión
`model_catalog_read_model_v1`. El entrypoint local une perfiles redacted,
opciones/tiers, discovery y health, compatibilidad, cobertura de evaluación,
runs, `run_adapter_profiles` y `cost_events`. Las SQLite se leen read-only con
introspección: una DB legacy o parcial produce diagnóstico, no una excepción que
borre el resto. Coste, tokens y latencia históricos quedan como métricas crudas;
solo una normalización explícita y con provenance puede alimentar los cinco
componentes del scorer. Los normalized metrics tampoco pueden sobreescribir los
hard gates derivados del catálogo y la compatibilidad real.

Cada candidato y fila por rol conserva hashes de contenido/entrada, los inputs
exactos del scorer, receipts, runs y fuentes. El colector normaliza y deduplica
rutas SQLite equivalentes antes de sumar observaciones. El auditor recalcula el
hash de entrada y el score completo, por lo que volver a sellar un payload
exterior manipulado no oculta divergencias internas. Además detecta perfiles,
modelos o roles declarados ausentes, scores automáticos incompletos, métricas
conocidas sin fuente, evidencia stale y divergencia de consumidores. El baseline
local revalidado el 2026-07-23 enumera 46 candidatos y la matriz completa de 17
roles canónicos (782 celdas), con cero candidatos auto-elegibles y cero fallos.
El evento de catálogo del 2026-07-24 eleva la proyección a 47 candidatos y 799
celdas. Ling añade 17 incompatibilidades explícitas
`model_role_unclassified`; no altera las 116 celdas compatibles ni crea un
default. La auditoría descubrió y corrigió además que volver stale una evidencia
no debe borrar su diversidad histórica: `calibrated`/`fresh` cierran promoción,
pero `case_diversity` sigue describiendo los receipts. El read model vuelve a
pasar con 0 auto-elegibles y 0 fallos.
M.8.1 añade
`model_normalized_metrics_v1`: los 25 pares calibrados reciben únicamente la
tasa de éxito observable de su contrato exacto como componente `quality=100`,
con semillas, casos, clase de juez, Goodhart, recibos, fecha, versión y grupo de
comparación. Los parciales, negativos y no probados no reciben calidad. Esto no
atribuye capability, fiabilidad, economía o velocidad no medidas, por lo que
ningún candidato se vuelve auto-elegible. El read model audita que el registro
superior coincida exactamente con la provenance de cada fila y detecta
eliminaciones o inyecciones.

M.8.2 convierte la taxonomía de `ROLE_STATUS` en el contrato ordenado
`CANONICAL_ROLES`; los aliases solo normalizan identidades antiguas y nunca
crean columnas duplicadas. Cada modelo proyecta todos los roles, aunque no
aparezcan en `best_for`, cobertura o runtime. Una incompatibilidad prevalece
sobre evidencia histórica para la decisión actual: conserva su código, razón,
receipts y estado previo, pero el score final permanece `None`. Una celda
compatible no calibrada que pudiera entrar en selección automática declara
`run_exact_canary` o `run_exact_tool_fixture`; nunca hereda calidad de otro rol.
El auditor rechaza taxonomías o matrices incompletas, scores incompatibles y
deuda automática sin acción exacta. El recibo vigente contiene 666 celdas
incompatibles y 116 compatibles: 25 calibradas y 91 aún sin score.
M.8.2 queda cerrado con una corrección adicional: `manual_only=false` es política
del modelo, no permiso para todos los roles compatibles. Una ruta automática
requiere además nominación exacta en `best_for`. Así, 71 celdas compatibles no
nominadas siguen disponibles para configuración manual, pero no generan deuda
ni promoción automática. Hay 45 celdas nominadas; ninguna ruta operativa carece
de evidencia. Las dos operativas no calibradas —Luna/File Scout y Flash
Low/Worker— ya conservan agregados íntegros de tres semillas y resultado
parcial, por lo que fijan `no_rerun_until_material_change` y no reciben quality.
El auditor rechaza una política automática rol divergente o una ruta operativa
sin recibo exacto.

M.8.3 introduce `model_evidence_taxonomy_v1`. Un benchmark general de capacidad,
un canario exacto de rol y un fixture exacto de tools son evidencias distintas
y no pueden sustituirse entre sí. Los `research_score` declarados se muestran
como generales, no normalizados y no alimentan el scorer. Cada métrica exacta
publica `evidence_kind`, familias de casos y riesgo de Goodhart.

`model_role_score_v2` añade el hard gate `case_diversity`: para automática exige
al menos dos familias independientes, no simplemente más seeds o más checks
sobre el mismo fixture. Una calibración mono-familia conserva su quality exacta
visible, pero su riesgo Goodhart pasa a material y no puede promocionarse. El
baseline actual separa 23 canarios de rol y 2 fixtures de tools: 21 pares son
multi-familia y 4 conservan una sola familia válida tras un screening fallido.
cohortes de Coding, QA, Test Designer, Tier 3 y MCP Operator.

La cohorte Coding usa `config_redactor` como segunda familia frente a
`cli_conversor`. Terra/Engineer completó tres seeds, 9/9 tests ocultos y Ruff
limpio; el agregado `coding_hidden_suite_two_family_v4` enlaza y hashea ambos
agregados exactos, seis muestras y dos familias. Sonnet/Engineer pasó los tres
tests ocultos de seed 1, pero dejó un import `pytest` sin usar (`F401`): fail-fast
detuvo las otras seeds. Su calibración anterior sigue visible, pero continúa
mono-familia y sin permiso automático; la salida del modelo no se parcheó.

La cohorte QA añade `webhook_replay_boundary` a la familia de autorización:
valida firma inválida, expiración y replay con estado. Terra/QA completó tres
seeds, 30/30 gates y un agregado enlazado de 6/6 muestras, por lo que abre
`case_diversity`. Flash High/QA completó correctamente el ataque del primer seed,
pero Antigravity agotó 240 s en la reverificación. Se conserva como diagnóstico
operacional `subscription_cli_timeout`, se detienen las demás seeds por
fail-fast y no se altera su calibración anterior ni se abre diversidad.

La cohorte Test Designer añade una máquina de estados de jobs a las fronteras
aritméticas de pricing. Terra/Test Designer completó tres seeds, 24/24 gates y
15/15 mutantes; el agregado de 6/6 muestras abre `case_diversity`. Flash High
completó el primer seed con 5/5 mutantes. En el segundo volvió a matar 5/5, pero
agotó 240 s antes del reporte y cierre durable; fail-fast detuvo el tercero.
Queda como diagnóstico operacional y no abre diversidad.

La cohorte Tier 3 usa familias distintas por función: triaje causal de incidente
para Worker, idempotencia de pagos para File Scout y un segundo advisory
gobernado para Web Scout. Luna/Worker, Flash Medium/Worker y Luna/Web Scout
completaron tres seeds exactas y agregados enlazados de 6/6 muestras. Flash
Low/File Scout falló el primer submit con `submit_work JSON object not found`;
fail-fast detuvo las otras seeds y mantiene el gate cerrado. Las reformulaciones
equivalentes detectadas en Worker/Web Scout se añadieron al juez y se
reevaluaron sobre receipts existentes, sin nueva inferencia.

MCP Operator añade `dependency_policy_lookup`/`publish_policy` como segundo
dominio gobernado, independiente del advisory. Terra completó tres seeds y
36/36 gates: fallo y recuperación de health, allow/deny, llamada permitida,
ausencia de write, reporte durable y single-attempt. El agregado enlaza 6/6
muestras. Con ello M.8.3.3–M.8.3.4 quedan cerrados: 21/25 pares abren diversidad;
los cuatro restantes conservan quality anterior, diagnóstico exacto y bloqueo
automático hasta cambio material.

La cobertura vigente queda registrada en
`benchmarks/results/model_evaluation_coverage/model-evaluation-coverage-2026-07-23.json`:
46 modelos, 131 destinos semánticos, 25 calibrados, 5 parciales, 15 canarios,
4 fixtures de tools, 3 candidatos manuales y 79 bloqueados. Estos estados son
de cobertura conductual; no sustituyen los 782 estados de compatibilidad de la
matriz modelo×rol ni conceden selección automática.

Cuando health no conserva versión, el entrypoint puede reutilizar el último
`model_catalog_drift_audit` de hasta 30 días como evidencia durable. Solo acepta
un recibo `ok`, con todos sus gates verdaderos, catálogo `current` y cobertura
exacta; health vivo prevalece. La fuente y fecha quedan en
`evaluation_version_evidence`. Un recibo futuro, stale, inválido o con cualquier
gate falso no rehabilita calibraciones.

`model_role_score_snapshots` y `aiteam.db.model_score_snapshots` guardan de forma
idempotente el set completo de candidatos, versiones, ganador, razón y hash. Un
ganador `auto_applied` debe pertenecer al set y ser auto-elegible; una mutación
posterior invalida `hash_valid`. Una versión o rol explícitos dentro de un
candidato deben coincidir con el snapshot; su ausencia sigue siendo válida para
consumidores envolventes que ya versionan el set. La tabla está lista, pero no recibe selecciones
productivas sin un snapshot vivo elegible. La identidad canónica mantiene un
orden de presentación determinista, pero si score, evidencia, calidad, economía
y velocidad siguen exactamente empatados, `default.reason=unresolved_exact_tie`
falla cerrado y exige owner.

M.4 expone el mismo read model mediante `GET /api/model-catalog` y
`GET /api/model-catalog/candidates?role=...`. La primera ruta conserva candidatos
bloqueados/inactivos y filtra por rol, proveedor, canal, tier, estado y
configuración; la segunda usa directamente `rank_model_role_scores` y publica
score, breakdown, confianza, compatibilidad, métricas, evidencia, provenance y
`selection_reason`. Ninguna ruta recalcula gates ni activa el ganador: el
contexto base sigue siendo criticidad medium, datos públicos y capacidades
canónicas del rol; `selection_score` contextual pertenece a M.6.
La respuesta de candidatos identifica esta vista como `base_role_score` y
enlaza `POST /api/model-catalog/selection` como superficie contextual; no afirma
ya que M.6 esté pendiente. La caché conserva una copia privada y devuelve copias
aisladas, de modo que ningún consumidor puede mutar el read model compartido.
El timestamp de caché se toma después de construir la proyección y las claves
siguen incorporando configuración y SQLite.

`/api/user-adapters/models` conserva por compatibilidad externa `role_score` y los campos
anteriores, pero añade `catalog_candidate_id`, `model_role_score` y razón
canónica y ordena primero los pares presentes en la proyección global. Su
compatibilidad contextual existente se calcula contra el orden persistido del
perfil, igual que el POST de preflight, para mantener paridad exacta. Una caché
local de 30 segundos se separa por configuración/SQLite y se invalida en
mutaciones de perfil, secret y health; no ejecuta discovery ni canarios.
Ningún consumidor productivo del cockpit usa ya este GET: onboarding, Equipo y
hiring consultan `POST /api/model-catalog/selection`; el probe manual conserva
su contrato independiente en `POST /api/user-adapters/test`. Así, inventario y
diagnóstico local no vuelven a convertirse accidentalmente en ranking.

La selección automática usa una sola función backend compartida por onboarding,
bootstrap Lead, hiring, Equipo, quorum y propuestas de fallback. Persiste el
conjunto de candidatos, score version, breakdown, confianza, hard gates y razón
del ganador. La opción explícita del owner siempre prevalece y no se reescribe en
reconcile. Sin candidato elegible se conserva un default explícito o se escala al
owner; nunca se cambia silenciosamente de adapter.

El `role_score` actual de `aiteam.user_config.model_options_for_role` es
transitorio: ordena por tier, capabilities declaradas y `best_for`, pero no
representa todavía calidad durable, fiabilidad, economía/velocidad observadas ni
confianza. Debe delegar gradualmente en la nueva proyección. Durante rollout, el
nuevo ranking funciona primero en shadow, después como recomendación visual y
solo finalmente como default para plazas nuevas sin modelo fijado. Agentes ya
existentes no se migran automáticamente.

La pestaña independiente `Modelos` muestra proveedores/canales y una matriz
modelo×rol con score, confianza, tier, calidad, economía, velocidad, estado y
evidencia. Los detalles enlazan recibos y métricas; filtros y comparación no
reimplementan scoring en React. En crear/editar equipo, cada rol recibe la lista
global de pares modelo+adapter ordenada por la misma API; los no elegibles siguen
visibles, deshabilitados y con causa.

M.5 materializa esa pestaña en `ide-frontend/src/components/ModelCatalog`. El
read model publica metadata redacted del perfil —política/nota de datos,
workspace, MCP, structured output y clase económica— para que React no consulte
una segunda fuente ni convierta ausencia de privacidad en permiso. La vista
presenta tarjetas perfil+canal, filtros, matriz desplazable y ficha de detalle
con estados, hard gates, breakdown, confianza, métricas y receipts. Al elegir un
rol obtiene el orden de `/api/model-catalog/candidates`; un candidato bloqueado
con score superior permanece detrás del elegible y se explica como deny. El
rollout visual continúa `shadow_only`: la pestaña no contrata ni muta defaults.
La reauditoría del 2026-07-23 mantiene `unknown` separado de cero también en la
presentación: score o confianza ausentes usan `—`. El filtro enumera los once
estados canónicos, las tarjetas publican conteos configurado/verde con estado
accesible y el detalle incluye los inputs exactos del score. Su diálogo mueve el
foco al abrir, lo confina, cierra con Escape y devuelve el foco al control de
origen. El footer enlaza conceptualmente el selector contextual ya activo, sin
texto de rollout obsoleto ni cálculo local en React.

M.6 empieza con `POST /api/model-catalog/selection` y la función pura
`build_contextual_model_selection`. Esta proyección enumera todos los pares del
catálogo para un rol —también los que aún no tienen fila de score—, recalcula la
compatibilidad exacta con run profile, criticidad, data class y capacidades y
aplica esos hard gates antes de llamar al ranking canónico. Conserva por separado
`base_score` y `selection_score`: mientras no existan métricas contextuales
normalizadas y comparables, el valor numérico no cambia y solo cambian gates.
Si no hay auto-elegibles, `default.action` exige conservar una selección
explícita o preguntar al owner; nunca recomienda simplemente la primera fila.

`ModelRoleSelector` consume ese POST en onboarding, edición, hiring propuesto,
alta directa de Equipo, quorum y fallback. Agrupa por proveedor/canal, mantiene
el orden backend y permite al owner elegir candidatos compatibles/selectables
aunque no sean auto-elegibles; los demás permanecen visibles con su deny reason.
Quorum añade diversidad y fallback restringe el perfil, sin reimplementar el
ranking. M.6.2 queda cerrado: cada frontera que crea o modifica una asignación
reconstruye el contexto desde `issue_id` y une las capabilities efectivas del
agente con las requeridas por la issue y sus ancestros. Proposal inicial,
quorum y liveness pasan el mismo contexto al selector gobernado; no basta con
que la UI hubiera filtrado previamente un candidato.

Los accesos `model_options?.[0]` que permanecen en React son probes manuales de
conexión y no escriben asignaciones. `GET /api/user-adapters/models` permanece
como compatibilidad externa sin consumidores productivos; el inventario local
no gobierna el ranking ni sustituye a `POST /api/model-catalog/selection`.
Los caminos legacy que aún validan clientes antiguos o reparan metadata perdida
son fronteras explícitas, no defaults silenciosos.

Los cambios de una asignación existente cruzando perfil o adapter nunca son un
fallback automático. Review high/critical con la misma perspectiva que el
engineer y recovery tras agotar liveness crean una `request_confirmation`
durable con el par exacto recomendado por `contextual_model_selection`; la issue
queda `blocked` y el agente conserva byte a byte su asignación. Al aceptar, el
executor recalcula catálogo, conexión, `owner_selectable`, compatibilidad y el
gate original (`different_perspective` o `different_adapter`) antes de escribir
`selection_intent.mode=owner_explicit`. Rechazar mantiene el bloqueo. Si Team se
editó mientras la tarjeta estaba pendiente, la edición más nueva prevalece y
solo reabre trabajo si también supera esa revalidación. Estas transiciones son
control plane determinista: no consumen otra inferencia ni quedan bloqueadas por
gates de coste/compliance destinados a ejecutar modelos.

Una elección manual del componente se persiste dentro de `adapter_config` como
`selection_intent` versión `model_selection_intent_v1`, modo `owner_explicit`,
con la identidad canónica del candidato. Es metadata no secreta y viaja con el
par exacto perfil+modelo. Reconcile solo puede reparar placeholders o identidad
de transporte perdida; no debe reinterpretar esta marca como autorización para
optimizar o reemplazar el modelo. El modo durable `default` queda reservado para
M.7, cuando exista un candidato auto-elegible y snapshot reproducible.

`aiteam.model_selection_intent` normaliza las fronteras autorizadas por el owner.
Create, update y aceptación de hiring vinculan el `candidate_id` al perfil y
modelo exactos; una identidad falsificada o un `mode=default` enviado por un
cliente falla antes de escribir. Un PATCH del mismo par hereda la intención
existente, pero vuelve a contrastar su candidato canónico para que una fila
legacy o manipulada no eluda el vínculo por conservar perfil/modelo. Onboarding
también pasa por este normalizador backend: un `candidate_id` falso aborta y
limpia el proyecto parcial. GET/reload proyecta la marca sin reinterpretarla.
Solo M.7 crea `mode=default` desde un snapshot automático reproducible.

M.7 inicia sin mutación mediante `aiteam.model_default_rollout` y
`POST /api/model-catalog/selection/shadow`. Cada evaluación persiste el conjunto
contextual completo, eleva `auto_eligible` desde el `selection_score` ya
gobernado, marca la asignación actual y registra `matches`, divergencia o
`no_winner`. El snapshot es idempotente y sellado. Generar
`selection_intent/mode=default` exige recalcular ese hash, `auto_applied=true` y
un ganador elegible con identidad operacional completa; un snapshot shadow no
puede usarse para contratar. El smoke inicial conserva seis asignaciones porque
ninguno de los 48 candidatos por rol supera todos los gates.

El rollout de proceso usa `AITEAM_MODEL_DEFAULT_ROLLOUT=shadow|recommend|auto`.
Un valor ausente o inválido cae a `shadow`, de modo que volver a `shadow` es el
rollback y no reescribe agentes. `recommend` persiste la misma decisión pero
mantiene el selector vigente. `auto` solo puede materializar una plaza nueva sin
pin a partir de un snapshot `auto_applied`, hash válido y ganador elegible; si no
hay ganador conserva `role_builtin` con estado durable `default_unresolved`, que
reconcile no puede convertir después mediante la heurística legacy. Las cohortes
conectadas cubren agentes dinámicos de issues/liveness, bootstrap Lead, Tier 3 y
quorum. Un Lead sin ganador aborta la creación y elimina el árbol parcial; Tier 3
conserva builtin explicado; quorum resuelve secuencialmente y excluye perspectivas
ya usadas cuando hay alternativas. Las altas de lote se confirman una a una para
liberar el write lock antes de persistir el snapshot siguiente; el ensure
idempotente completa una caída parcial. Canarios herméticos prueban dos canales,
no-winner, pins y reconcile, pero no sustituyen la matriz viva de health, cuota y
calidad. El recuento de suite se actualiza al cerrar esta cohorte.
La verificación resultante deja 238 tests dirigidos y 1329 globales en verde.

M.7.4 persiste el snapshot vivo del 2026-07-23 para 14 roles y los 46 candidatos
del catálogo: los 14 hashes son válidos y ninguna evaluación shadow modifica
asignaciones o marca `auto_applied`. Las 644 observaciones candidato×rol tienen
economía declarada, pero solo 17 disponen de economía normalizada; 392 muestran
adapter rojo y ninguna tiene capacidad suficientemente demostrada (`no_data` o
`capacity_unknown`). Por eso el rollout operativo avanza únicamente a
`recommend`, que no asigna, y `auto` permanece denegado. La plantilla documenta
`recommend`; una variable ausente o inválida sigue cayendo a `shadow`, que es
también el rollback inmediato. La matriz de autoridad confirma que adapter rojo,
incompatibilidad, precio desconocido, presión de cuota, stale, empate exacto y
override no crean una asignación. Además, el rollout vuelve a comprobar que el
ganador de la proyección siga siendo auto-elegible antes de persistir autoridad.

La presión económica contextual de M.6 se deriva en el servidor. Para cada
perfil, `subscription_quota_snapshot` conserva estados y provenance: solo
`exhausted_observed` o `limit_reached` cierran `capacity_available` y deshabilitan
la elección; `capacity_unknown` permanece desconocido (`passed=null`) e impide
el default automático sin fingir cero ni bloquear por sí solo una elección
manual del owner. Si una política explícita
del owner aporta unidad, límite, ventana y utilización, el selector puede
sustituir únicamente el componente `economy` de una suscripción por su headroom
normalizado, con basis `subscription_quota_pressure`; no compara unidades entre
perfiles. Para API, el cap diario se deriva de `cost_events` y
`daily_cost_cap_cents`: al alcanzarse bloquea candidatos API, pero no canales de
suscripción cuyo coste marginal registrado es cero. Si la SQLite no puede leerse,
el gasto y el restante quedan desconocidos: el lector usa modo read-only, no crea
una DB vacía ni convierte el fallo en gasto cero. Un forecast owner incompleto,
no finito o sin unidad/límite/ventana válidos tampoco reescribe economía. El
frontend no envía ni autorreporta estas presiones.

El selector envía `issue_id`, no una afirmación de tools. El backend hereda
`required_capabilities` de la issue y sus ancestros mediante el mismo
`issue_compatibility_context` que usa dispatch, y las une con las capacidades
del agente editado y los defaults del rol. Así una necesidad `external_mcp`,
repo write o structured output puede cerrar el mismo hard gate en hiring y en
ejecución sin confiar en estado React. Las listas se unen en toda la ascendencia,
no se sustituye una por otra; para campos escalares prevalece el valor más
cercano a la issue, incluida una criticidad `medium` explícita.

La explicación del default refleja el orden real. Un score distinto publica su
delta; un empate identifica si lo resolvieron evidencia, calidad, economía o
latencia dentro del mismo grupo comparable y, como último recurso estable, la
identidad canónica. React muestra esa causa junto al breakdown sin recalcular el
ranking.

Onboarding usa ya el mismo `ModelRoleSelector`. La creación del proyecto envía
perfil, modelo y candidate id exactos; `choose_adapter_for_role` acepta ese
modelo preferido solo si pertenece al perfil, es seleccionable y supera la
compatibilidad Lead. Bootstrap no vuelve a escoger otro modelo por tier: guarda
el par y `model_selection_intent_v1/owner_explicit`. La ausencia de
`lead_model` mantiene temporalmente el contrato anterior para clientes que aún
no han migrado.

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

Agotar liveness en el adapter completo aplica la misma frontera: puede generar
una recomendación cross-adapter, pero no reasignar ni reabrir la issue hasta que
el owner acepte y el candidato siga siendo ejecutable. La interaction es
idempotente por issue/agente/causa y su resolución deja activity y wakeup
durables; inventario o catálogo por sí solos nunca autorizan la mutación.

FrugalGPT aporta evidencia primaria de que combinar selección, generación y cascadas puede reducir coste manteniendo calidad en sus datasets. No prescribe thresholds universales. Fuente: [PAPER-2](ORCHESTRATION_SOURCES.md#paper-2-frugalgpt).

Vigilar `cascade pile-up`: degradación o rate limit del barato puede trasladar toda la carga al caro. En AI Teams lo mitigan `provider_governor`, recovery acotado y cap diario.

## Sesiones CLI persistentes: experimento, no política productiva

Las columnas `runs.session_id_before/after` existen, pero el runtime productivo
continúa stateless: Claude usa `--no-session-persistence`, Codex `--ephemeral` y
Antigravity `--new-project`. El probe local del 2026-07-21 confirma reanudación
por ID explícito en Codex 0.145.0 y Antigravity 1.1.5; Claude no está instalado
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

La clasificación empieza por el entregable, no por asumir programación. Un
estudio empresarial, investigación, memo o análisis sin artefacto ejecutable no
crea Engineer, Test Designer, QA ni Test Runner, y no inventa archivos o suites
para satisfacer gates de software. Usa scouts/curator y, cuando reduzca un
riesgo concreto, revisión independiente de fuentes o método. Su evidencia es
cobertura, citas fechadas, supuestos, cálculos y una conclusión accionable. En
trabajo `mixed`, solo el sub-issue realmente ejecutable activa build/tests.
Esto ya no depende solo del prompt. `objective_classification_v1` es el contrato
canónico persistido en metadata y proyectado en UI, plan y wake payload:

- `software`: admite roles y gates de programación proporcionales al riesgo;
- `research`: evidencia documental, fuentes y síntesis;
- `operations`: procedimientos, comprobaciones y recibos operativos;
- `mixed`: hereda el workflow no programativo y solo admite roles/gates de
  programación en sub-issues clasificados explícitamente `software`.

El clasificador determinista es conservador: ante ambigüedad elige `software`;
un override explícito del owner tiene precedencia. Hiring y delegación rechazan
de forma determinista cualquier propuesta incompatible, aunque un LLM ignore
estas instrucciones.

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
7-8/9 anclas en tres semillas (0/3); el control GPT-5.5 y Luna `medium` obtienen
6/6 en la matriz actual, mientras Anthropic Haiku conserva 3/3 en su canario;
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
`codex_subscription` usan `gpt-5.6-luna` con esfuerzo `medium`; otros perfiles conservan su selección propia
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

El snapshot secuencial observa como máximo 25 candidatos por dispatch: al drenar
N wakeups el crecimiento queda acotado por `25*N`, no por `N²`, pero sigue siendo
aditivo. El benchmark hermético v1 midió tres repeticiones de colas 1/25/100/1000.
En 1000 obtuvo exactamente 24.700 filas, 20,60 MB adicionales, mediana de 8,30 ms
por planificación y consultas medianas de 0,030/0,016 ms. Ningún threshold
prerregistrado fue superado, por lo que `retention_implementation_allowed=false`:
se conserva el log aditivo y se repite el benchmark si cambian schema, índices,
scheduler o límite de snapshot. Una retención futura será específica por tabla;
no se aplicará una purga global a `activity_log`, `run_events` u orientación
consentida, cuyas obligaciones de auditoría y borrado son diferentes. Recibo:
`benchmarks/results/dispatch_decision_growth/dispatch-decision-growth-v1.json`.

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

El cuarto bloque no se puede fabricar a partir del fixture anterior. El
inventario read-only `scripts/audit_parallel_trigger_inventory.py` recorre el
runtime retenido, poda únicamente directorios efímeros conocidos y conserva
errores de discovery/DB como diagnóstico. Una fuente sólo abre el A/B vivo si
tiene snapshots `exact`, al menos dos raíces y dos pools, espera paralelizable
positiva y ningún adapter hermético.

La ejecución del 2026-07-22 descubre 71 SQLite: 70 son auditables, una está
vacía y no hay errores de discovery. Las 70 fuentes útiles son `approximate`;
ninguna contiene snapshots porque todas las runs reales retenidas son anteriores
a la instrumentación. Por tanto el recibo
`benchmarks/results/parallel_channels/parallel-live-trigger-inventory-v1.json`
devuelve cero candidatos y `live_ab_allowed=false`. Este resultado no cierra el
trigger: evita gastar modelos sobre contención inventada. La tarea permanece
abierta hasta que una ejecución natural posterior deje provenance multi-raíz y
multi-pool positiva.

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
La revalidación 2026-07-23 confirma OpenCode 1.18.4, el mismo catálogo de cinco
modelos declarados y Big Pickle todavía rechazado. El cierre enlaza y hashea los
recibos previos sin gastar una nueva inferencia: DeepSeek/Reviewer conserva
`partial` 1/3 y el pool solo se reabre si cambia versión, catálogo, transporte
o contrato structured output.
El canario solo demuestra recoverability manual y health de un MCP local, no de
MCPs externos. `serve` tampoco aporta el sandbox necesario para Engineer.
Fuentes: [FREE-1](ORCHESTRATION_SOURCES.md#free-1-gateway-catálogo-y-privacidad)
y [FREE-3](ORCHESTRATION_SOURCES.md#free-3-cli-mcp-sesiones-y-telemetría).

La vía gratuita es híbrida. `opencode_zen_free` declara seis modelos. Ling 3.0
Flash Free se descubrió el 2026-07-24 y queda `catalog_only`, manual, probe-gated
y sin roles aprobados hasta clasificar capacidad y superar un contrato exacto; su
tier `standard` es una banda provisional de presentación y no concede autoridad. Laguna
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

La composición viva única es `contextual_model_selection`: superpone read model,
contexto de issue, tools, health/selectable, presión de cuota y presupuesto antes
de ordenar. `POST /api/model-catalog/selection`, onboarding, Equipo, hiring,
quorum y lifecycle consumen esa misma proyección. Cada flujo añade únicamente
su restricción de dominio: quorum exige diversidad de perspectiva; fallback
permanece en el mismo perfil, prioriza continuidad de familia/tier y presenta
al owner el selector restringido antes de mutar la asignación.

La matriz provisional de los canales gratuitos, pendiente de canarios vivos,
es deliberadamente más estrecha que su capacidad comercial:

| Perfil/modelo | Roles máximos provisionales | Bloqueos obligatorios |
|---|---|---|
| Zen / Nemotron 3 Ultra | Lead, arquitectura, quorum y review read-only | Engineer/Worker y Lead `solo_lead`; datos confidenciales |
| Zen / DeepSeek V4 Flash o MiMo V2.5 | Reviewer read-only | Lead/quorum, QA, test_designer, escritura y datos confidenciales |
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

Los screenings exactos del pool no bloqueante 2026-07-23 tampoco autorizan
defaults. GPT-OSS 120B de Antigravity no produjo un `submit_work` válido en
`file_scout`, `worker` ni en el único retry justificable de `web_scout`; el
primer intento de web fue infraestructura saturada y no se contó como calidad.
Se aplicó fail-fast tras el primer fallo contractual reproducible, en vez de
gastar tres semillas incapaces de superar el gate de transporte.

El cambio Antigravity 1.1.5→1.1.6 reabre por evento únicamente la frescura de
Sonnet 4.6/Engineer. El screening comparable `config_redactor` del 2026-07-24
completa 3/3 tests ocultos en una run, pero deja 7 incidencias Ruff y tarda
296,297 s. Se aplica fail-fast y no se consumen las otras cinco celdas de la
matriz de dos familias. El default existente no se cambia por edad o por este
screening aislado, pero la pareja no puede obtener una promoción nueva hasta
recalibrarse con una versión/contrato materialmente distinto.

En Ollama 0.32.1, Qwen 2.5 Coder 14B no cierra los contratos exactos de
`file_scout` ni `context_curator`; Gemma 4 E4B tampoco supera
`file_scout`/`context_curator`/`worker`. Gemma 4 26B pasa Engineer solo 1/3 y
queda `partial`; Reviewer no supera el rechazo durable y Test Designer mata
mutantes pero falla la baseline, por lo que ese resultado no se presenta como
éxito de mutación. Estos fallos se conservan como diagnósticos por par exacto.
La ventaja económica local permanece intacta —cero coste y cuota externos—,
pero no sustituye calidad, fiabilidad ni compatibilidad.

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
OpenCode se mantiene read-only por política expresa. El QA condicional escribe
únicamente tests adversariales que demuestran defectos, por lo que requiere
`repo_write` igual que Engineer, Test Designer y Lead en `solo_lead` y no se
recomienda en OpenCode. `mcp_operator` y cualquier necesidad
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
| Catálogo, scoring y calibración de modelos | `aiteam/model_catalog_projection.py`, `aiteam/model_role_scoring.py`, `aiteam/model_catalog_read_model.py`, `aiteam/model_catalog_api.py`, `aiteam/model_catalog_service.py`, `aiteam/db/model_score_snapshots.py`, `aiteam/model_calibration.py`, `api/routers/model_catalog.py`, `ide-frontend/src/components/ModelCatalog/`, scripts de auditoría de catálogo |
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
