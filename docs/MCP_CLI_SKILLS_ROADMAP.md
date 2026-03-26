# Roadmap Profundo MCP + CLI + Skills (v2)

Este plan traduce el informe de `3.txt` a una hoja de ruta ejecutable para `Ai_Teams`.

## Objetivo

Pasar de un orquestador local v1 a una plataforma de agentes que:

- descubre herramientas externas,
- integra MCP servers por rol,
- adquiere CLIs/skills cuando faltan capacidades,
- mantiene seguridad tipo zero-trust con human-in-the-loop.

## Principios de arquitectura (extraidos de 3.txt)

1. **MCP como capa estandar de interoperabilidad**
   - separar razonamiento (LLM) de ejecucion (tool servers).
2. **Skills != MCP**
   - Skills para "como trabajar" (reglas/reutilizacion).
   - MCP para "con que operar" (acciones/datos transaccionales).
3. **Pro-first + Tool-first controlado**
   - priorizar suscripciones y activar herramientas externas solo cuando aporten capacidad.
4. **Security-by-design**
   - permisos granulares, approval para herramientas sensibles y auditoria total de acciones.

## Fase 1 - Foundation (2 semanas)

### 1.1 Catalogo de fuentes de herramientas

- Definir catalogo versionado (`config/tool_sources.catalog.json`) con:
  - nombre, tipo (`cli|mcp|skill`), fuente (`npm|pip|git|url`),
  - capacidades/roles,
  - sensibilidad y si requiere aprobacion.

### 1.2 Integrador automatico

- Integrar `AutoToolIntegrator` en el ciclo de tareas:
  - `tool_requirements` por tarea,
  - registro automatico en `runtime/adapters.json`,
  - registro de MCP servers en `runtime/mcp_servers.json`,
  - materializacion de skills en `.cloud/skills/<name>/skill.md`.

### 1.3 Descubrimiento por capacidad faltante

- Si una tarea falla por `no_eligible_adapter`, buscar en catalogo por `required_capabilities`.
- Integrar candidatos y reintentar una vez.

## Fase 2 - Ecosistema MCP de productividad (2-3 semanas)

### 2.1 MCP core de ingenieria

- Priorizar integraciones:
  - `github_mcp`, `postgres_mcp`, `supabase_mcp`, `context7_mcp`, `semgrep_mcp`, `playwright_mcp`.

### 2.2 MCP de operaciones de negocio (opt-in)

- Integraciones sensibles (con approval obligatorio):
  - `notion_mcp`, `stripe_mcp`, `slack_mcp`.

### 2.3 Modelo de activacion

- Estado por herramienta:
  - `disabled` (registrada no activa),
  - `enabled_stage`,
  - `enabled_prod`.

## Fase 3 - Skills operativas y AEO (2 semanas)

### 3.1 Biblioteca de skills reutilizables

- Skills iniciales:
  - `remotion_skill`,
  - `n8n_skill`,
  - `security_review_skill`,
  - `release_guardrails_skill`.

### 3.2 Skill discovery

- Integrar `fine_skills` como opcion secundaria para descubrir nuevas skills.

### 3.3 Agent Engine Optimization (AEO)

- Priorizar Markdown denso para contexto y skills.
- Evitar prompts masivos de HTML sin conversión.

## Fase 4 - CI remota y workflows cerrados (3 semanas)

### 4.1 Plantillas de pipelines remotos

- Definir `execution_plan` tipo:
  - `lint/test/build`,
  - `security scan` (semgrep),
  - `artifact validation`.

### 4.2 Integracion con runners externos

- Ejecutar flujos remotos via MCP/CLI con sandbox y aprobacion humana en pasos irreversibles.

### 4.3 Evidencia obligatoria

- Registrar evidencias (logs, screenshots, artefactos) en `runtime/evidence/`.

## Fase 5 - Gobernanza y seguridad avanzada (continuo)

### 5.1 Zero-trust para tools

- Clasificar herramientas por riesgo (`low|medium|high`).
- Requerir doble aprobacion en `prod` para `high`.

### 5.2 Anti-prompt-injection (web/tools)

- Sanitizar entradas de herramientas web.
- Restringir acciones de escritura en sistemas sensibles.

### 5.3 Auditoria y transparencia

- Eventos dedicados:
  - `tool_integration`,
  - `tool_auto_discovery`,
  - `mcp_invocation`.

## KPIs v2

- `% tareas desbloqueadas por auto-tool-discovery`.
- `% integraciones MCP exitosas sin intervención manual`.
- `MTTR de tareas bloqueadas por falta de herramienta`.
- `% de ejecuciones sensibles con approval correcta`.
- `% de tasks con evidencia completa de ejecución`.

## Riesgos y mitigaciones

1. **MCP/CLI malicioso**
   - Mitigar con allowlist de fuentes + approval + sandbox.
2. **Exfiltracion de secretos**
   - Mitigar con redaction, bloqueo de comandos y segregacion de credenciales.
3. **Deriva de costos por tools externas**
   - Mitigar con FinOps signal + caps por tool/canal.
4. **Dependencia excesiva de API externa**
   - Mitigar con Pro-first estricto y alertas de API share.

## Comandos operativos añadidos

- `python -m aiteam.cli tool-catalog`
- `python -m aiteam.cli tool-sync --tool-request-file runtime/tool_requests.json`
- `python -m aiteam.cli inventory-tools --inventory-output runtime/tool_inventory.json`
- `python -m aiteam.cli dashboard --dashboard-output runtime/dashboard.html`

## Resultado esperado

Al cerrar v2, `Ai_Teams` pasa de ser un orquestador local robusto a una plataforma de agentes
con adquisicion y orquestacion de herramientas externas (CLI/MCP/Skills) de forma segura,
auditada y orientada a producción.

## Estado implementado (v2-base)

- `AutoToolIntegrator` integrado al orquestador (`aiteam/autotools.py`).
- Auto-discovery por capacidades faltantes con reintento de ruteo.
- Catalogo de fuentes en `config/tool_sources.catalog.json`.
- Biblioteca de skills en `config/skills.library.json` + sync a `.cloud/skills/`.
- Registro runtime de MCP en `runtime/mcp_servers.json`.
- Comandos operativos nuevos:
  - `tool-catalog`, `tool-sync`
  - `skills-library`, `skills-sync`
  - `mcp-status`, `mcp-doctor`, `skills-coverage`
- Guardrail activo: adquisiciones opcionales fallidas se auto-desactivan.
- Continuidad operativa: handoff entre agentes por rol con transferencia de memoria contextual.
- Visibilidad de cuentas/modelos Pro y API con `provider-status`.
- Preflight unificado con `system-check` y recuperación local con `snapshot-create/restore`.
