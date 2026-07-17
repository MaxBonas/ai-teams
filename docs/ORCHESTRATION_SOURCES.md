# Fuentes de orquestación multi-modelo

Última revisión: `2026-07-16`

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

## Regla de actualización

Para añadir una afirmación cuantitativa:

1. enlazar fuente primaria;
2. registrar fecha y calidad;
3. describir dataset/condiciones;
4. separar resultado publicado de threshold local;
5. retirar o degradar la cifra si no puede auditarse.
