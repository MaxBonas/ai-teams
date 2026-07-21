# Fuentes de orquestación multi-modelo

Última revisión: `2026-07-21`

Registro canónico de fuentes usadas por [ORCHESTRATION.md](ORCHESTRATION.md). Verificar de nuevo información temporalmente inestable antes de cambiar código.

## Calidad

| Grado | Significado |
|---|---|
| A | Documentación oficial o especificación primaria vigente |
| B | Paper/preprint original con metodología inspeccionable |
| C | Informe de ingeniería del proveedor sobre su propio sistema |
| D | Fuente secundaria/practitioner; útil como hipótesis, no como cifra normativa |

## OpenAI

### OAI-1 Agent orchestration

- URL: https://openai.github.io/openai-agents-python/multi_agent/
- Calidad: A.
- Cubre: orquestación por LLM/código, agents-as-tools, handoffs, structured outputs, evaluator loops y paralelismo.
- Uso local: taxonomía de patrón; no exige adoptar Agents SDK.

### OAI-2 Agents and hooks

- URL: https://openai.github.io/openai-agents-python/agents/
- Calidad: A.
- Cubre: instrucciones dinámicas, hooks y configuración de agentes.

### OAI-3 Handoffs

- URL: https://openai.github.io/openai-agents-python/handoffs/
- Calidad: A.
- Cubre: cambio de ownership, input types, filtros y contexto.

### OAI-4 Running agents and HITL

- URLs:
  - https://openai.github.io/openai-agents-python/running_agents/
  - https://openai.github.io/openai-agents-python/human_in_the_loop/
- Calidad: A.
- Cubre: runs, sesiones, estado, interrupciones y aprobación humana.

### OAI-5 Codex skills and eval workflows

- URL: https://developers.openai.com/codex/use-cases
- Calidad: A.
- Cubre: skills, workflows repetibles, objetivos durables y evals.

### OAI-6 Modelos GPT-5.6

- URLs:
  - https://developers.openai.com/api/docs/models
  - https://developers.openai.com/api/docs/guides/latest-model
  - https://developers.openai.com/api/reference/resources/models
- Calidad: A.
- Revisión: `2026-07-20`.
- Cubre: IDs `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, capacidades,
  ventanas, herramientas y precios; Sol=frontier, Terra=equilibrio y Luna=alto
  volumen sensible a coste.
- Evidencia de canal local: Codex CLI `0.128.0` expone los tres slugs en
  `~/.codex/models_cache.json`, con ventana efectiva declarada de 258.400 tokens.

## Anthropic

### ANTH-1 Building effective agents

- URL: https://www.anthropic.com/research/building-effective-agents
- Calidad: C.
- Cubre: workflows frente a agents, routing, paralelismo, orchestrator-workers y evaluator-optimizer.
- Limitación: guía del proveedor, no benchmark independiente.

### ANTH-2 Multi-agent research

- URL: https://www.anthropic.com/engineering/multi-agent-research-system
- Calidad: C.
- Cubre: arquitectura real de research, delegación, coordinación, prompts, evaluación y coste de tokens.
- Limitación: resultados internos de un dominio concreto; no generalizar porcentajes a AI Teams.

### ANTH-3 Claude Code MCP por run

- URL: https://code.claude.com/docs/en/cli-usage
- Calidad: A.
- Cubre: `--mcp-config` para cargar configuración MCP y
  `--strict-mcp-config` para ignorar configuraciones MCP ajenas a la invocación.
- Uso local: traducción efímera del grant neutral de AI Teams al CLI de Claude;
  no altera la autoridad del rol ni implica que Claude sea el Lead.

### ANTH-4 Modelos Claude actuales, precios y Fable

- URLs:
  - https://platform.claude.com/docs/es/about-claude/models/overview
  - https://platform.claude.com/docs/en/api/models/list
  - https://platform.claude.com/docs/es/about-claude/pricing
  - https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback
  - https://platform.claude.com/docs/en/manage-claude/api-and-data-retention
  - https://code.claude.com/docs/en/model-config
- Calidad: A.
- Revisión: `2026-07-20`.
- Cubre: IDs, capacidad, latencia, contexto y precios de Fable 5, Opus 4.8,
  Sonnet 5 y Haiku 4.5; selección/pinning en Claude Code; refusals/fallback y
  retención obligatoria de 30 días para Fable.
- Limitación local: Claude CLI no está instalado y el perfil sigue
  `blocked_by_provider`; la matriz es política objetivo, no health demostrado.

## Google y modelos locales

### GOOG-1 Gemini 3 y precios

- URLs:
  - https://ai.google.dev/gemini-api/docs/models
  - https://ai.google.dev/api/models
  - https://ai.google.dev/gemini-api/docs/pricing
- Calidad: A.
- Revisión: `2026-07-22`.
- Cubre: `gemini-3.1-pro-preview`, `gemini-3.5-flash` estable y
  `gemini-3.1-flash-lite` estable, además de precios y tramos por longitud.
- Evidencia de canal local: `agy 1.1.5 models` confirmó 11 IDs slug: los ocho
  anteriores de Gemini 3.1/3.5, Claude Opus/Sonnet 4.6 y GPT-OSS 120B, más tres
  Gemini 3.6 sujetos a probe exacto. No confirmó usage comparable por run.

### LOCAL-1 Qwen y Gemma

- URLs:
  - https://ollama.com/library/qwen3-coder
  - https://ai.google.dev/gemma/docs/core
  - https://ollama.com/library/gemma4
- Calidad: A para documentación del fabricante/registry oficial.
- Revisión: `2026-07-20`.
- Cubre: Qwen3 Coder 30B como modelo agentic de coding y tamaños/capacidad de
  Gemma 4. Los nombres instalados se verifican localmente; la publicación no
  autoriza descarga ni promoción automática.

## Catálogo MCP oficial

Revisión local: `2026-07-20`. Las versiones son pins del catálogo, no una orden
de descarga ni una política de actualización automática.

### MCP-CAT-1 GitHub MCP Server

- URLs:
  - https://github.com/github/github-mcp-server
  - https://github.com/github/github-mcp-server/releases
- Calidad: A.
- Pin revisado: distribución y `serverInfo` `1.6.0`.
- Uso local: binario `github-mcp-server`, subcomando stdio, read-only y toolsets
  acotados; requiere solo el nombre de `GITHUB_PERSONAL_ACCESS_TOKEN`.

### MCP-CAT-2 Playwright MCP

- URLs:
  - https://github.com/microsoft/playwright-mcp
  - https://www.npmjs.com/package/@playwright/mcp
- Calidad: A.
- Pin revisado: distribución y `serverInfo` `0.0.78`.
- Uso local: shim previamente instalado `playwright-mcp`, headless e isolated;
  no se usa el ejemplo oficial con `npx`/`latest`.

### MCP-CAT-3 Filesystem MCP

- URLs:
  - https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem
  - https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem
- Calidad: A.
- Pin revisado: distribución `2026.7.10`; el servidor declara `0.2.0` mediante
  `serverInfo`, que es el pin operativo del health actual.
- Uso local: shim previamente instalado `mcp-server-filesystem` y único argumento
  resuelto al workspace. Incluye tools de escritura/destructivas, por lo que la
  allowlist empieza vacía y cada tool requiere decisión owner.

## Papers

### PAPER-1 RouteLLM

- Título: *RouteLLM: Learning to Route LLMs with Preference Data*.
- URL: https://arxiv.org/abs/2406.18665
- Calidad: B.
- Cifra conservada: el paper reporta más de 2× de reducción de coste manteniendo 95% del rendimiento de GPT-4 en sus benchmarks.
- Limitación: no predice el ahorro de AI Teams ni calibra sus thresholds.

### PAPER-2 FrugalGPT

- Título: *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance*.
- URL: https://arxiv.org/abs/2305.05176
- Calidad: B.
- Cubre: selección, aproximación de prompts y cascadas.
- Limitación: datasets y APIs estudiados no equivalen a coding agents persistentes.

### PAPER-3 Self-Preference

- Título: *Self-Preference Bias in LLM-as-a-Judge*.
- URL: https://arxiv.org/abs/2410.21819
- Calidad: B.
- Cifra conservada: hasta aproximadamente 10% de ventaja de win-rate en configuraciones estudiadas.
- Limitación: no demuestra sesgo idéntico en todos los modelos ni que cross-provider sea una corrección completa.

### PAPER-4 AgentBench

- Título: *AgentBench: Evaluating LLMs as Agents*.
- URL: https://arxiv.org/abs/2308.03688
- Calidad: B.
- Cubre: evaluación interactiva de agentes.
- Uso local: antecedente metodológico, no suite directa para AI Teams.

## Fuentes degradadas

- Cifras genéricas de “50–60% de ahorro” procedentes de vendors: no usar como expectativa local sin fuente primaria y condiciones comparables.
- “17x error trap” de artículos practitioner: conservar solo el principio de error compounding, no la cifra.
- Thresholds 5%/50% de tasa de escalado: tratar como heurísticas de diagnóstico, no como política canónica.

## OpenCode Zen y modelos gratuitos

### FREE-1 Gateway, catálogo y privacidad

- URLs:
  - https://opencode.ai/docs/zen
  - https://opencode.ai/docs/cli
  - https://opencode.ai/docs/permissions
  - https://opencode.ai/docs/config
- Calidad: A, documentación oficial.
- Revisión: `2026-07-21`.
- Cubre: IDs exactos, endpoint compatible con OpenAI, gratuidad temporal,
  login/API key, inventario CLI, configuración inline y condiciones de uso de
  datos. Limitación: no publica una cuota estable que AI Teams pueda prometer.

### FREE-2 Capacidades de los modelos

- URLs:
  - https://api-docs.deepseek.com/quick_start/pricing/
  - https://mimo.mi.com/docs/en-US
  - https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard
  - https://docs.cohere.com/docs/north-mini-code-1.0
- Calidad: A para especificaciones y resultados declarados por cada fabricante.
- Revisión: `2026-07-21`.
- Cubre: contexto, tool use, structured output, arquitectura y orientación
  agentic/coding. Limitación: la puntuación de
  `MODELOS_GRATUITOS_OPENCODE.md` es screening de integración; no sustituye los
  benchmarks locales por contrato de rol.

### FREE-3 CLI, MCP, sesiones y telemetría

- URLs:
  - https://opencode.ai/docs/tools
  - https://opencode.ai/docs/agents
  - https://opencode.ai/docs/mcp-servers
  - https://opencode.ai/docs/server
  - https://opencode.ai/docs/sdk
  - https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts
  - https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/processor.ts
- Calidad: A, documentación y código fuente oficial.
- Revisión: `2026-07-21`.
- Cubre: permisos por wildcard y agente, nombres `servidor_tool`, configuración
  MCP local/remota, rechazo headless sin `--auto`, eventos JSONL `step_finish`,
  tokens/coste por paso, sesiones, OpenAPI/SSE y salida SDK con JSON Schema.
  Limitación: describe capacidad del transporte, no demuestra aislamiento de
  escritura ni estabilidad del servicio Zen; ambos requieren canario local.

### FREE-4 API keys gratuitas y límites

- URLs:
  - https://ai.google.dev/gemini-api/docs/billing
  - https://ai.google.dev/gemini-api/docs/rate-limits
  - https://console.groq.com/docs/rate-limits
  - https://console.groq.com/docs/models
  - https://console.groq.com/docs/api-reference
  - https://console.groq.com/docs/structured-outputs
  - https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models
  - https://docs.github.com/en/rest/models/catalog
  - https://docs.github.com/en/rest/models/inference
  - https://openrouter.ai/docs/faq
  - https://openrouter.ai/docs/guides/overview/models
  - https://openrouter.ai/docs/api/reference/errors-and-debugging
  - https://docs.cohere.com/v2/docs/rate-limits
  - https://huggingface.co/docs/inference-providers/en/pricing
  - https://api-docs.deepseek.com/quick_start/pricing/
  - https://mimo.mi.com/docs/en-US/price/pay-as-you-go
- Calidad: A, documentación oficial de cada servicio.
- Revisión: `2026-07-22`.
- Cubre: existencia y alcance de free tiers, unidades/headers de cuota,
  requisitos de key, límites de prototipo y precios directos vigentes. La
  ausencia de una cuota NVIDIA pública verificable se trata como desconocida,
  no como prueba de gratuidad o de pago.

## Regla de actualización

Para añadir una afirmación cuantitativa:

1. enlazar fuente primaria;
2. registrar fecha y calidad;
3. describir dataset/condiciones;
4. separar resultado publicado de threshold local;
5. retirar o degradar la cifra si no puede auditarse.
