# AI Team Hybrid Orchestrator

Este proyecto inicia un sistema de AI Team con enfoque **Pro-first + API fallback**:

> Objetivo principal: **programar y entregar software con el AI Team**. Tambien puede ejecutar tareas auxiliares cuando se solicite, pero la prioridad operativa es desarrollo de software.

- Prioriza primero tus **3 canales de suscripcion** (OpenAI, Anthropic, Google) para reducir coste marginal.
- Hace fallback a **APIs GPT de costo contenido** cuando hay bloqueo, cuota, timeout o requisito tecnico.
- Orquesta perfiles (Lead, Researcher, Engineer, Reviewer, QA) sobre una task board compartida.

## Estado actual (Mar 2026)

La app esta en estado **operativo para orquestacion, observabilidad y continuidad por proyecto**.

Estado verificado a 2026-03-26:

- Suite completa: **282 tests passing** (`venv/Scripts/python.exe -m pytest tests/ -q --tb=short`)
- Roadmap activo de mejora de flujo/agentes: `ROADMAP_FLUJOS_Y_AGENTES.md`
- Backlog operativo consolidado: `TASKS.md`

Backend / orquestador:

- Workflow chat **lead-first**: `lead_intake -> discovery -> build -> review -> qa -> lead_close`.
- Task board con dependencias, estados (`ready/pending/claimed/blocked/completed/failed`) y recovery de tareas stale.
- Gobernanza por rol con charter formal (rango de decision, personalidad operativa, roles a consultar, justificacion obligatoria).
- Reuniones de sincronizacion por ronda + reuniones por evento critico (fallo, quality gate, compliance).
- Memoria por agente (`runtime/memory/*.jsonl`) filtrable por proyecto + continuidad por proyecto para retomar sesiones previas.
- Mensajeria interna (`runtime/mailbox.jsonl`) y eventos (`runtime/events.jsonl`) con trazabilidad completa.
- Threads conversacionales persistentes por agente/proyecto (`runtime/sessions/threads/`) para continuidad real en handoffs y feedback del Team Lead.
- Quality gates automáticos para tareas de Engineer (Review + QA antes de cierre final).
- Compliance para ejecucion local (guardrails, redaccion de secretos, aprobaciones sensibles en `prod`).
- FinOps (budget diario/mensual, señal dinamica, share API vs suscripcion, recomendaciones de tuning).
- Integracion de herramientas externas (adapters/MCP/skills) con inventario y auto-discovery controlado.
- Bridge NotebookLM con estado/sync (`notebooklm-connect`, `notebooklm-sync`, endpoint API de sync).

IDE frontend:

- Workspace por ventana/proyecto (sin mezcla de conversaciones, archivos ni estado entre proyectos).
- Selector con **recientes (top 10)**, **pinned**, filtro, abrir en nueva ventana y **Proyecto Nuevo**.
- Chat AI Team + visores dedicados de:
  - conversaciones del equipo,
  - logs de eventos,
  - outputs de tareas.
- Incluye tambien **inputs del usuario** en conversaciones/logs/outputs para auditoria end-to-end.
- Panel de continuidad de proyecto (resumen de sesiones anteriores del mismo workspace).
- Panel de estado NotebookLM (con `Sync now`).

## Estructura

- `docs/TASKS_AI_TEAM.md`: plan profundo de implementacion (19 frentes).
- `docs/ARCHITECTURE.md`: arquitectura implementada y gaps a produccion.
- `docs/INTERNAL_QUALITIES_ROADMAP.md`: cualidades objetivo y hoja de ruta interna.
- `docs/NOTEBOOKLM_STRATEGY.md`: estrategia de integracion con NotebookLM (6 notebooks).
- `docs/PROJECT_LEARNING_GUIDE.md`: guia para registrar learnings y capturar conocimiento.
- `docs/LEARNING_REGISTRY_SCHEMA.md`: esquema de datos para la Learning Registry.
- `docs/EXTERNAL_TOOLS_INVENTORY.md`: inventario de herramientas externas y prioridad.
- `docs/SECURITY_COMPLIANCE.md`: reglas de aprobacion y redaccion para operaciones sensibles.
- `docs/PRODUCTION_ROLLOUT_RUNBOOK.md`: despliegue gradual, incidentes y rollback operativo.
- `docs/MCP_CLI_SKILLS_ROADMAP.md`: roadmap v2 para MCP/CLI/Skills e integraciones profundas.
- `docs/LLM_CONNECTION_SYSTEM.md`: guia completa para conectar LLMs API y no API.
- `aiteam/`: nucleo del orquestador.
- `tests/`: pruebas base de router y taskboard.

## Uso rapido

```bash
python -m aiteam.cli init
python -m aiteam.cli plan
python -m aiteam.cli contract-first --epic-id EPIC-101 --title "Refactor auth"
python -m aiteam.cli run --rounds 12
python -m aiteam.cli demo
python -m aiteam.cli meeting --topic "Sprint Sync"
python -m aiteam.cli memory --agent eng-1 --limit 5
python -m aiteam.cli exec --shell powershell --command-text "Write-Output 'hi'"
python -m aiteam.cli adapters
python -m aiteam.cli catalog-tools
python -m aiteam.cli inventory-tools --inventory-output runtime/tool_inventory.json
python -m aiteam.cli tool-catalog
python -m aiteam.cli tool-sync --tool-request-file runtime/tool_requests.json
python -m aiteam.cli tool-sync --runtime-dir runtime_stage --tool-request-file config/tool_requests.pro.json
python -m aiteam.cli skills-library --show-content
python -m aiteam.cli skills-sync
python -m aiteam.cli skills-pull --skills-batch gpt-system --skills-max-items 2
python -m aiteam.cli skills-pull --skills-batch gpt-curated-core,claude-core --skills-max-items 6
python -m aiteam.cli skills-export --skills-targets cloud,agents,claude
python -m aiteam.cli skills-doctor
python -m aiteam.cli mcp-status
python -m aiteam.cli mcp-doctor --doctor-timeout 20
python -m aiteam.cli provider-status --environment stage
python -m aiteam.cli provider-connect --runtime-dir runtime_stage_mcp
python -m aiteam.cli provider-doctor --runtime-dir runtime_stage_mcp
python -m aiteam.cli autotune-doctor --environment stage --autotune-window-hours 6
python -m aiteam.cli system-check --environment stage --strict
python -m aiteam.cli snapshot-create --snapshot-label "before-major-change"
python -m aiteam.cli snapshot-list
python -m aiteam.cli snapshot-restore --snapshot-id <id>
python -m aiteam.cli skills-coverage
python -m aiteam.cli dashboard --dashboard-output runtime/dashboard.html
python -m aiteam.cli status
python -m aiteam.cli pilot-check --environment stage
python -m aiteam.cli learning --help
python -m aiteam.cli learning list
python -m aiteam.cli learning summary
python -m aiteam.cli learning record-failure --learning-title "Atomic write race condition" --learning-description "Fix needed"
python -m aiteam.cli learning record-insight --learning-title "Exponential backoff works better" --learning-description "Performance improved 60%"
python -m aiteam.cli learning record-team --learning-title "Team learned about async patterns" --learning-description "From sprint retro"
python -m aiteam.cli learning record-feedback --learning-title "User feedback on UI" --learning-description "Onboarding too long"
python -m aiteam.cli learning export --learning-format markdown
python scripts/ingest_learnings.py
python -m aiteam.cli notebooklm-connect --runtime-dir runtime
python -m aiteam.cli notebooklm-sync --runtime-dir runtime --notebooklm-format markdown
python scripts/benchmark_parallel_throughput.py --tasks 24 --delay-ms 80 --parallel 4
```

Learning Registry:

La carpeta `learning_registry` captura fallos, insights, learnings y feedback para síntesis con NotebookLM:

```bash
# Registrar un fallo de proyecto
aiteam learning record-failure --learning-title "API timeout" --learning-description "Rate limiting issue"

# Registrar un insight del sistema
aiteam learning record-insight --learning-title "Retry logic" --learning-description "Exponential backoff works"

# Registrar un learning del equipo
aiteam learning record-team --learning-title "Async patterns" --learning-description "From sprint retro"

# Ver todos los learnings
aiteam learning list
aiteam learning summary

# Exportar para ingestion diaria a NotebookLM
python scripts/ingest_learnings.py --format markdown
python scripts/ingest_learnings.py --format json

# Bridge opcional (auto-sync via endpoint/comando)
python -m aiteam.cli notebooklm-connect --runtime-dir runtime
python -m aiteam.cli notebooklm-sync --runtime-dir runtime --notebooklm-format markdown
# Sin endpoint/comando configurado, se usa bridge local por defecto
# y deja markdown listo en runtime/notebooklm_ready/
```

Opcional: usa Playwright para `browser_script` con `--browser-mode playwright`.
`init` crea `runtime/adapters.json` como template para enchufar tus runtimes externos por rol.

Setup rapido de Playwright (Python):

```bash
python -m pip install playwright
python -m playwright install chromium
```

Variables de entorno:

- Copia `.env.example` a `.env` y ajusta cuentas/proveedores.
- La CLI carga `.env` automaticamente si existe.
- Puedes habilitar paralelismo controlado con `AITEAM_MAX_PARALLEL_TASKS` (recomendado iniciar en 2 para stage).
- Puedes activar auth API con `AITEAM_API_KEY`; el IDE envia ese valor si guardas `localStorage.AITEAM_API_KEY` en el navegador.
- Puedes definir limites por entorno con `AITEAM_MAX_PARALLEL_TASKS_STAGE` y `AITEAM_MAX_PARALLEL_TASKS_PROD`.
- Puedes habilitar autoajuste de paralelismo con `AITEAM_PARALLEL_AUTOTUNE`, `AITEAM_MIN_PARALLEL_TASKS`, `AITEAM_PARALLEL_TARGET_LATENCY_MS` y `AITEAM_PARALLEL_MAX_FAILURE_RATE`.
- Puedes endurecer gobernanza por entorno con `AITEAM_ENFORCE_ROLE_MODEL_PREFERENCES` y `AITEAM_STRICT_ROLE_POLICY_ENVS`.
- Bridge NotebookLM opcional: `NOTEBOOKLM_INGEST_ENDPOINT` o `NOTEBOOKLM_INGEST_COMMAND` para sync automatizado.
- Para usar llamadas API reales en adapters internos, define `AITEAM_ENABLE_LIVE_API=1` y una key valida (`OPENAI_API_KEY` o `GROQ_API_KEY`).

## Como funciona AI Team (flujo real)

Cuando llamas `POST /api/aiteam/chat`, el sistema crea una cadena de tareas por fases:

1. `lead_intake`: Team Lead escucha solicitud, define objetivo y plan minimo viable.
2. `discovery`: Researcher levanta restricciones, riesgos y evidencia accionable.
3. `build`: Engineer implementa propuesta tecnica (o plan de implementacion si aplica).
4. `review`: Reviewer valida calidad, seguridad y mantenibilidad.
5. `qa`: QA valida regresion, cobertura y criterio de salida.
6. `lead_close`: Team Lead sintetiza y responde al usuario.

Puntos clave del runtime:

- Cada decision relevante queda registrada (`decision_justification`, provider/model/canal, attempts).
- El sistema ejecuta consultas entre pares antes del cierre de decision.
- Hay reuniones por ronda y tambien reuniones por evento (por ejemplo fallos o bloqueos).
- Las fases se retoman por proyecto usando historial en `runtime/` (continuidad de contexto).
- Presupuesto de rondas para chat se ajusta por `complexity/criticality` (override con `AITEAM_CHAT_MAX_ROUNDS`).

## Roles, charters y reuniones

Roles base:

- `team_lead` (R5): estrategia, tradeoffs finales, orden de entrega.
- `researcher` (R3): evidencia, opciones, riesgos, supuestos.
- `engineer` (R4): implementacion segura y testeable, compatibilidad.
- `reviewer` (R4): calidad estructural, mantenibilidad, seguridad.
- `qa` (R4): validacion funcional/regresion y criterio de release.

Cada rol tiene:

- personalidad operativa,
- ambito de decision autorizado,
- lista de roles que debe escuchar,
- salida estructurada (propuesta, evidencia, aportes considerados, decision final).

Reuniones:

- **Sync por ronda**: resumen multi-rol para mantener alineacion continua.
- **Sync por evento**: disparada por fallos, quality gates, compliance o bloqueos.
- Minutas se guardan en mailbox/memory y son visibles en los visores del IDE.

## Notas

- Los adapters de suscripcion (`SubscriptionAdapter`) son simulados por defecto para validar arquitectura y gobernanza.
- El adapter API (`ApiAdapter`) puede operar en modo real con `AITEAM_ENABLE_LIVE_API=1` y credenciales validas.
- La integracion real con tus programas agenticos se implementa via el contrato `ModelAdapter`.
- La politica Pro-first se ajusta en `aiteam/router.py` y `aiteam/config.py`.
- El presupuesto API se evalua antes de cada intento en canal API.
- En defaults, API fallback usa `gpt-4.1-mini` (razonamiento), `gpt-4o-mini` (multimodal) y `llama-3.3-70b-versatile` via Groq (razonamiento/coding rapido).
- Stack Pro por defecto: `gpt-5.3-codex` (senior #1), `gemini-3.1-pro` (senior #2), `claude-code` (senior #3).
- El ejecutor permite workdirs dentro de `Ai_Teams` y `Antigravity Projects` (configurable con `AITEAM_SHARED_TOOLS_ROOT`).
- `mcp-doctor` verifica salud de MCPs y marca estado en `runtime/mcp_servers.json`.
- Si una adquisicion opcional falla, la tool queda auto-desactivada (`enabled=false`).
- Failover de agentes por rol con handoff de memoria para continuidad cuando hay fallos tecnicos/límites.
- `init` genera `runtime/provider_accounts.json` y `provider-status` permite revisar conectividad de cuentas Pro/API.
- Gobernanza de equipo activa: cada rol tiene rango de decision, personalidad operativa, consulta entre pares y justificacion registrada (`decision_justification`).

### Limitacion importante actual

Si trabajas solo con adapters simulados, el equipo puede completar fases de orquestacion sin generar entregables reales de codigo.
Para avance productivo en proyectos, combina:

- adapters reales (o runtimes externos reales),
- tareas con `execution_plan`/acciones de archivo,
- validacion de artefactos en review/qa (no solo texto de respuesta).

### Estado de implementacion

- **Implementado**: evidence gate mock robusto, bloqueo explicito por dependencias fallidas, rounds/sub-iteraciones visibles, meetings con menos ruido, mailbox conversacional basico, aislamiento de contexto por proyecto y observabilidad de flujo.
- **Parcial**: agentes conversacionales multi-turn ya tienen thread persistente y consumo de mailbox, pero los adapters aun no trabajan nativamente con `messages[]`.
- **Planificado**: adapters plenamente conversacionales, mailbox mas profundo TL <-> agentes y cierre E2E/documental del modelo multi-LLM.

## IDE Frontend (chat + viewer)

El editor web esta orientado a programar con el AI Team e incluye:

- Dashboard operativo live con timeline de flujo, rounds/sub-iteraciones, bloqueos, handoffs y eventos conversacionales.
- Chat con AI Team (`/api/aiteam/chat`) con intake obligatorio por Team Lead senior y delegacion automatica al resto del equipo.
- Viewer de estado (`/api/aiteam/state`) con tasks/eventos/latencia/recomendaciones, continuidad de proyecto y estado NotebookLM.
- Viewer de conversaciones (`/api/aiteam/conversations`) con mensajes de equipo y entradas de usuario.
- Viewer de logs/outputs (`/api/aiteam/logs`) con eventos recientes y salidas por tarea.
- Aislamiento por workspace por ventana (sin mezcla de contexto entre proyectos).
- Selector de workspace con recientes (10), pinned, filtro, abrir en nueva ventana y "Proyecto Nuevo".
- Terminal websocket atado al workspace activo.

Para levantarlo localmente:

```bash
# Opcion 1 (Windows): arranque automatico con health checks
start_ide.bat

# Para detener procesos del IDE
stop_ide.bat

# Opcion 2: manual
# Terminal 1: backend API
uvicorn api.main:app --reload --port 8000

# Terminal 2: frontend
cd ide-frontend
npm run dev -- --port 9483 --strictPort
```

Abre `http://localhost:9483`.
