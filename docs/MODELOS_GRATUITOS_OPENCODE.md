# Modelos gratuitos de OpenCode Zen

Revisión: `2026-07-22`. Esta evaluación clasifica capacidad e integración para
AI Teams; no convierte benchmarks del fabricante en calibración local.

## Conclusión

Los cuatro modelos de la captura son IDs oficiales actuales de OpenCode Zen y
usan `opencode/<model-id>`. Se integran de base mediante el perfil
`opencode_zen_free`, pero solo quedan seleccionables cuando el CLI los enumera y
la sesión Zen está conectada. AI Teams no distribuye claves, no copia el archivo
de credenciales de OpenCode y no promete disponibilidad permanente.

El inventario vivo de OpenCode `1.18.4` del `2026-07-21` añadió además
`opencode/laguna-s-2.1-free`. Su primer submit público cumple el contrato y
reporta usage, pero permanece fuera del catálogo aprobado hasta completar la
misma calibración durable por rol que los cuatro modelos originales.
El auditor mensual+evento lo conserva con disposición `pending_calibration`;
`opencode/big-pickle` queda `rejected` por identidad opaca. Ninguno cuenta como
drift desconocido ni como opción productiva.

El canario durable v1 de reviewer conserva cinco recibos diagnósticos en
`benchmarks/results/model_calibration/opencode-durable-review-v1-*.json`.
DeepSeek completa reject→fix→approve en seed 1, pero falla la aprobación en
seed 2; Nemotron falla el contrato estructurado, MiMo no materializa el rechazo
durable y North es denegado correctamente porque su catálogo no admite reviewer.
Ningún brazo alcanza 3/3 y no se autoriza promoción ni cambio de roles.

El runtime integrado es deliberadamente read-only: permite leer, buscar y usar
LSP dentro del workspace, pero deniega shell, edición, subagentes, preguntas,
directorios externos y compartición. Por ello puede asumir Lead, quorum,
arquitectura, review, QA, scouts y curator, nunca Engineer ni otro rol que deba
modificar archivos.

## Método de puntuación

Puntuación preliminar sobre 100: capacidad de razonamiento/código (35), ajuste
agentic y salidas estructuradas (25), contexto (10), accesibilidad/estabilidad
del canal (15) y gobierno/privacidad (15). Los dos últimos apartados penalizan
que la gratuidad sea temporal, la cuota no tenga garantía pública y el uso de
datos sea más permisivo. El tier final representa el rol máximo aconsejable en
AI Teams, no solo el tamaño o un resultado publicado.

| Modelo | Puntos | Tier AI Teams | Uso aconsejado | Decisión |
|---|---:|---|---|---|
| Nemotron 3 Ultra Free | 86 | Tier 1 por capacidad | Lead, arquitectura y quorum read-only | Integrado; requiere sesión Zen y datos no confidenciales |
| DeepSeek V4 Flash Free | 82 | Tier 2 | Review y QA complejos | Integrado; no se asigna a Engineer porque el runtime seguro no escribe |
| MiMo V2.5 Free | 80 | Tier 2 | Review, QA y análisis multimodal | Integrado; pendiente de calibración conductual local |
| North Mini Code Free | 74 | Tier 3 | Scouts y trabajo de código acotado en lectura | Integrado; su huella activa pequeña desaconseja planificación crítica |

Nemotron documenta 550B parámetros totales, 55B activos, contexto de hasta 1M,
razonamiento configurable, tool use y workflows agentic complejos. DeepSeek V4
Flash documenta 1M, JSON y tool calls. MiMo V2.5 documenta 1M, comprensión
omnimodal y ejecución agentic. North Mini Code documenta 30B/3B activos, 256K,
64K de salida, tool use, structured outputs y entrenamiento específico para
coding agentic. Estas propiedades justifican el screening; los tiers no se
promocionarán a política general hasta superar contratos locales de Lead,
review/QA y scouts.

## Matriz provisional de asignación

Esta matriz es un límite de seguridad/producto mientras faltan canarios vivos,
no una afirmación de que todos los roles permitidos tengan ya calidad probada.
`best_for` ordena recomendaciones; el gate modelo×rol pendiente de P0.3 debe
convertir estos límites en denegaciones backend y opciones deshabilitadas con
explicación en Equipo.

| Modelo | Permitido provisionalmente | Bloqueado provisionalmente |
|---|---|---|
| Nemotron 3 Ultra Free | Lead, Team Lead, Architect, quorum y review, siempre read-only | Engineer, Worker, Lead en `solo_lead`, datos confidenciales |
| DeepSeek V4 Flash Free | Reviewer, Code Reviewer, QA y Test Designer | Lead/quorum, roles de escritura y datos confidenciales |
| MiMo V2.5 Free | Reviewer, QA, Test Designer y análisis multimodal de lectura | Lead/quorum, roles de escritura y datos confidenciales |
| North Mini Code Free | File Scout, Web Scout y Context Curator | Lead/quorum, review crítico y roles de escritura |
| Gemini 3.5 Flash Free | Reviewer, Code Reviewer, QA y Test Designer | Lead/quorum hasta calibración y cualquier rol que exija MCP externo |
| Gemini 3.1 Flash-Lite Free | Scouts y Context Curator | Lead/quorum y review crítico |
| GPT-OSS 120B / Groq Free | Reviewer, Code Reviewer, QA y Test Designer | Lead/quorum hasta calibración y MCP externo |
| Qwen 3.6 o GPT-OSS 20B / Groq Free | Scouts y Context Curator | Lead/quorum y review crítico; Qwen no entra en contratos críticos sin repair gobernado |

Los adapters API pueden emitir operaciones estructuradas de archivo que AI
Teams materializa bajo RBAC. Por tanto, “API” no equivale a “solo lectura”. En
cambio, ninguno de los perfiles API gratuitos tiene hoy un loop MCP gobernado,
y Zen sí es deliberadamente read-only aunque su modelo sea capaz de programar.

## Acceso y límites reales

OpenCode Zen exige iniciar sesión, añadir datos de facturación y conectar una
API key. El CLI conserva esa credencial y AI Teams puede reutilizar la sesión
sin configuración adicional propia; en una instalación nueva sigue siendo
necesaria esa acción humana. No existe una forma legítima de ofrecer el gateway
anónimo o de embeber una clave compartida.

Los cuatro endpoints gratuitos están anunciados por tiempo limitado. DeepSeek y
MiMo pueden usar datos recogidos para mejorar el modelo; North puede retenerlos
y OpenCode indica que no se envíen datos personales o confidenciales; Nemotron
es trial, registra uso y exige consentimiento a sus condiciones. Por eso el
perfil declara `non_confidential_only` y nunca se activa solo por existir en el
catálogo.

## Capacidad de gestión en AI Teams

| Superficie | Estado | Qué garantiza hoy |
|---|---|---|
| Catálogo, auth y lifecycle | Fuerte con canario local | Discovery del ID exacto, health por perfil, bloqueo ante `model_unavailable` y fallback solo con aprobación |
| Roles y herramientas nativas | Fuerte para lectura | `read`, `glob`, `grep` y LSP permitidos; shell, edición, subagentes, preguntas, directorios externos y share denegados |
| MCP | Fuerte para grants aprobados | Configuración efímera por run, servidor denegado por wildcard y allowlist positiva `servidor_tool`; secretos solo como referencias de entorno |
| Cuota y contexto consumido | Fuerte en telemetría, parcial en forecast | JSONL agrega tokens de entrada/salida, razonamiento, caché, duración, errores 429/cuota e ID de sesión; no inventa un límite que Zen no publique |
| Salida estructurada | Insuficiente en server/SDK 1.18.4 | DeepSeek, Laguna, MiMo, Nemotron y North devuelven `StructuredOutputError` y no rellenan `info.structured`; el CLI conserva el parser fail-closed de `submit_work` |
| Continuidad de sesión | Transporte validado, producción desactivada | Tres semillas pasan memoria/override/aislamiento con seis IDs únicos, pero no compensa activar reanudación mientras falle el cierre estructurado |
| Escritura segura | Insuficiente | Los permisos de OpenCode no son una frontera de sandbox del sistema operativo; Engineer continúa prohibido |

La mejora inmediata de CLI ya aplicada elimina `--auto`: en headless las
solicitudes no resueltas deben rechazarse, no convertirse en aprobaciones
implícitas. `OPENCODE_CONFIG_CONTENT` neutraliza herramientas y MCP ajenos a la
run y traduce únicamente los grants decididos por AI Teams. El coste marginal
permanece en cero, pero el consumo no: tokens, runs, duración y límites
observados alimentan presión de cuota por el perfil exacto.

La evaluación del adapter opcional `opencode serve`/SDK queda cerrada en 1.18.4.
Sesiones explícitas, cancelación, hang/restart, health MCP local y aislamiento
3×2 funcionan; JSON Schema falla en los cinco modelos gratuitos. Por ello no
sustituye al CLI efímero ni justifica construir un supervisor. Una reevaluación
solo tiene sentido tras cambiar versión o contrato del proveedor. Tampoco
arregla la ausencia de sandbox de escritura.

## Zen frente a API keys gratuitas del usuario

La estrategia recomendada es híbrida. Una API directa es preferible cuando hay
un free tier real: elimina dependencia del CLI, entrega usage y rate-limit
headers atribuibles, fija proveedor/modelo y deja que AI Teams gobierne las
tools. La key debe pertenecer al usuario y vivir en su secret store o entorno;
nunca se embebe, persiste en SQLite ni se incluye en prompts.

| Canal | Valor para AI Teams | Decisión recomendada |
|---|---|---|
| Gemini API Free | Alto; free tier oficial, usage y cuotas por proyecto | Primer perfil BYOK gratuito; mantenerlo separado del Gemini API pagado y de Antigravity |
| Groq Free | Alto para workers acotados; límites y cabeceras explícitas | Integrar gpt-oss-120b/Qwen/Llama solo tras calibración por rol |
| GitHub Models Free | Medio; PAT `models:read`, catálogo amplio | Fallback/scouts: límites altos-model de 50 RPD y 8K input son pequeños para Lead/contexto |
| OpenRouter `:free` | Medio-bajo; OpenAI-compatible y muchos modelos | Opcional con ID exacto; nunca `openrouter/free`, porque el modelo aleatorio rompe provenance y calibración; 50 RPD base |
| Cohere evaluation key | Bajo para producción; 1.000 llamadas/mes | Solo evaluación de North, no capacidad base prometida |
| Hugging Face Free | Muy bajo; 0,10 USD/mes | Descartar como capacidad operativa |
| DeepSeek/MiMo directos | API técnicamente mejor, pero no gratuita estable | Mantener Zen para sus endpoints free; ofrecer API directa como perfil pagado opcional |
| NVIDIA build/NIM trial | Cuota pública estable no demostrada | No prometerlo como free tier hasta poder descubrir entitlement y límites de la cuenta |

Por tanto, `opencode_zen_free` no se reemplaza. AI Teams incorpora el carril
complementario con `gemini_api_free` y `groq_api_free`: onboarding mediante el
vault local, health vivo, catálogo separado, política de privacidad, usage,
errores de cuota y provenance por perfil. Groq usa el runtime neutral
`openai_compatible_api`; GPT-OSS 120B/20B reciben JSON Schema estricto y Qwen
3.6 usa JSON Object Mode más validación `submit_work`, conforme a las
capacidades publicadas de cada modelo. “Gratis” describe el plan seleccionado,
no un nuevo tipo de autoridad ni una garantía permanente.

Gemini pagado y Gemini Free usan slots de secreto distintos (`google` y
`google-free`). Esto evita que una key haga aparecer ambos perfiles como
conectados o que una run pagada se contabilice como gratuita por inferencia.

GitHub Models y OpenRouter exacto permanecen como expansión posterior: antes de
mostrarlos en Equipo deben descubrir un ID ejecutable y su contrato estructurado
con la key real. La auditoría local del 2026-07-22 no encuentra keys para
GitHub Models, OpenRouter, Gemini Free ni Groq; los tokens de `gh` disponibles
carecen además de `models:read`. No se añaden opciones decorativas ni el router
aleatorio. El runtime ya separa los hosts de GitHub Models y OpenRouter en el
governor y conserva RPD/TPM de Groq; los límites de los otros proveedores solo
se persistirán después de observar respuestas autenticadas.

## Modelos o canales descartados

- `Big Pickle`: identidad deliberadamente oculta; no permite gobernar
  provenance, lifecycle ni capacidad y se descarta aunque sea gratuito.
- OpenRouter `:free`, Cerebras, GitHub Models, NVIDIA NIM directo y Cohere
  trial: pueden ser útiles, pero requieren cuenta/clave, tienen cuotas o routing
  variables y no ofrecen una ventaja cero-config sobre Zen. Se valorarán como
  adapters propios, no como credenciales embebidas. Gemini Free y Groq Free ya
  existen como perfiles BYOK separados; continúan sujetos a key del owner,
  health por modelo, cuota y calibración por rol.
- Inferencia pública de Hugging Face y demos comunitarias: sin SLA, identidad de
  serving o garantía de tool protocol suficiente para runs persistentes.
- Pesos locales: son una vía válida y privada, pero requieren descarga y
  hardware; pertenecen a perfiles locales y no a “online sin configurar”.

## Gates pendientes y condiciones de reevaluación

1. Repetir discovery/auth/submit cuando cambie el catálogo o la versión; la
   instalación 1.18.4 y los cinco IDs actuales ya tienen probe local.
2. Ejecutar al menos tres semillas comparables por contrato de rol frente al
   baseline vigente; registrar cierre, calidad oculta, runs y duración.
3. Repetir server/SDK solo tras un cambio de versión/contrato: el A/B, cancelación,
   hang/restart, MCP local y aislamiento ya están medidos; JSON Schema bloquea
   la promoción en OpenCode 1.18.4.
4. Mantener el perfil fuera de proyectos confidenciales; si se desea
   clasificación automática de sensibilidad, diseñarla y auditarla antes.
5. No habilitar roles con escritura hasta disponer de una frontera de sandbox
   demostrable además de permisos de tools.
6. Calibrar Gemini Free y Groq Free por contrato de rol; hasta entonces Groq se
   restringe a review/QA/scouts/curator y no entra como Lead automático.

## Fuentes primarias

- OpenCode Zen: https://opencode.ai/docs/zen
- OpenCode CLI y auth: https://opencode.ai/docs/cli
- OpenCode permissions/config: https://opencode.ai/docs/permissions y
  https://opencode.ai/docs/config
- OpenCode tools, agents, MCP, server y SDK: https://opencode.ai/docs/tools,
  https://opencode.ai/docs/agents, https://opencode.ai/docs/mcp-servers,
  https://opencode.ai/docs/server y https://opencode.ai/docs/sdk
- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/
- Xiaomi MiMo: https://mimo.mi.com/docs/en-US
- NVIDIA Nemotron 3 Ultra:
  https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard
- Cohere North Mini Code:
  https://docs.cohere.com/docs/north-mini-code-1.0
