# Sistema de conexion de LLMs (API y no API)

Este documento describe como `Ai_Teams` conecta modelos por suscripcion (no API) y por API,
como se monitorizan, y como se mantiene continuidad cuando un proveedor falla o agota limites.

## 1) Capas de conexion

### 1.1 Suscripcion / no API (Pro CLIs)

Objetivo: usar primero cuentas Pro sin coste marginal por token (cuando aplica).

Stack actual por defecto:

- OpenAI senior #1: `gpt-5.3-codex`
- Google senior #2: `gemini-3.1-pro`
- Anthropic senior #3: `claude-code`

Conexion tecnica:

- adapters de canal `subscription`
- comando CLI por proveedor (si existe)
- alta automatica con `provider-connect`

### 1.2 API fallback

Objetivo: continuidad cuando suscripciones fallan o no cubren una capacidad.

Fallback default:

- `gpt-4.1-mini` (razonamiento/coding eficiente)
- `gpt-4o-mini` (multimodal)
- `llama-3.3-70b-versatile` via Groq (razonamiento/coding rapido)

Control:

- FinOps por presupuesto diario/mensual
- señal de presion (`max_api_tier`, `suggested_api_attempts`)
- `AITEAM_REQUIRE_API_KEYS=1` para exigir claves reales

## 2) Configuracion de entorno

Se carga `.env` automaticamente al iniciar CLI.

Variables clave:

- `AITEAM_SUBSCRIPTION_<PROVIDER>_ENABLED`
- `AITEAM_SUBSCRIPTION_<PROVIDER>_LIMIT_REACHED`
- `AITEAM_PROVIDER_<PROVIDER>_DEGRADED`
- `AITEAM_REQUIRE_API_KEYS`
- `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`

Comandos Pro opcionales por proveedor:

- `AITEAM_OPENAI_PRO_COMMAND`
- `AITEAM_GEMINI_PRO_COMMAND`
- `AITEAM_CLAUDE_PRO_COMMAND`

Formato soportado:

- JSON array: `[
  "claude","-p","{prompt}"
]`
- shell-like string: `claude -p "{prompt}"`

## 3) Flujo de conexion recomendado

1. `python -m aiteam.cli init --runtime-dir runtime_stage`
2. Configurar `.env` (claves y comandos Pro)
3. `python -m aiteam.cli provider-connect --runtime-dir runtime_stage`
4. `python -m aiteam.cli provider-doctor --runtime-dir runtime_stage`
5. `python -m aiteam.cli provider-status --runtime-dir runtime_stage --environment stage`
6. `python -m aiteam.cli system-check --runtime-dir runtime_stage --environment stage --strict`

`provider-connect` genera/actualiza `runtime/provider_accounts.json` con estado de conexion por cuenta.

## 3.1 Autenticacion OpenAI Pro (no API)

`Ai_Teams` usa Codex CLI para OpenAI Pro:

- comando base: `npx -y @openai/codex`
- login status: `npx -y @openai/codex login status`
- login interactivo (si hace falta): `npx -y @openai/codex login`

En `.env`:

`AITEAM_OPENAI_PRO_COMMAND=["npx","-y","@openai/codex","exec","--skip-git-repo-check","{prompt}"]`

## 3.2 Autenticacion Gemini Pro (CLI)

Comando usado por defecto:

`AITEAM_GEMINI_PRO_COMMAND=["npx","-y","@google/gemini-cli","-p","{prompt}"]`

Si aparece `gemini_auth_missing`, configurar una de estas opciones:

- `GEMINI_API_KEY=...`
- o metodo auth en `C:\Users\Max\.gemini\settings.json`

Luego ejecutar:

- `python -m aiteam.cli provider-connect --runtime-dir runtime_stage`
- `python -m aiteam.cli provider-doctor --runtime-dir runtime_stage`

## 4) Integracion MCP y Skills (contexto de agentes)

- Catalogo de tools: `config/tool_sources.catalog.json`
- Biblioteca skills: `config/skills.library.json`
- Fuentes remotas de skills por lotes: `config/skills.sources.json`
- Sync tools: `tool-sync`
- Pull skills (GPT/Claude): `skills-pull --skills-batch <lotes>`
- Export cross-model: `skills-export --skills-targets cloud,agents,claude`
- Salud MCP: `mcp-doctor`
- Cobertura de uso de skills: `skills-coverage`

El orquestador inyecta en cada tarea:

- skills aplicables,
- MCP recomendados,
- MCP activos.

Los skills sincronizados quedan en:

- `.cloud/skills/*` para runtime interno,
- `.agents/skills/*` para flujos Codex/GPT,
- `.claude/skills/*` para flujos Claude Code.

## 5) Continuidad y sustitucion de agentes

Cuando un proveedor/adapter falla por limites o problemas tecnicos:

- se intenta handoff a sustituto del mismo rol,
- se transfiere memoria relevante (`handoff_context`),
- se notifica por mailbox,
- la tarea se reintenta (hasta `max_handoff_retries`).

Esto evita bloqueos por depender de un solo modelo/cuenta.

## 6) Versionado y recuperacion

`Ai_Teams` incluye snapshots locales de proyecto:

- `snapshot-create`
- `snapshot-list`
- `snapshot-restore`

Recomendado: snapshot antes de cambios de config masivos o activacion de nuevas tools.

## 7) Diagnostico rapido

### 7.1 Provider doctor fallido

- revisar comando Pro en `.env`
- verificar CLI instalada (`where <command>`)
- revisar login del proveedor

Ejemplo de salida real:

- OpenAI Pro: `command_missing` (falta CLI configurado, por ejemplo `opencode`)
- Gemini Pro: `gemini_auth_missing` (CLI encontrada pero sin auth)
- Claude Pro: `claude_logged_in:pro`

### 7.2 Gemini no autenticado

Si aparece `Please set an Auth method...`:

- configurar auth de Gemini CLI o `GEMINI_API_KEY`
- reintentar `provider-doctor` y `provider-connect`

### 7.3 MCP acquisition fallida

- autenticar npm (`npm adduser` o token)
- re-ejecutar `tool-sync` y `mcp-doctor`
- el sistema auto-desactiva tools opcionales fallidas para no romper tareas

## 8) Estado esperado en prod

- `provider-doctor` healthy para providers requeridos
- `system-check --strict` pass
- `mcp-doctor --enable-healthy` ejecutado sin `--enable-sensitive` por defecto
- snapshots recientes disponibles para rollback rapido
