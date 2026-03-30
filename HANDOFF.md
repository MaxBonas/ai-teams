# AI Teams — Handoff para Claude Móvil
> Pega este archivo completo en Claude (móvil o web) para retomar el proyecto con contexto completo.

---

## ¿Qué es este proyecto?

**AI Teams Hybrid Orchestrator** (`github.com/MaxBonas/ai-teams`, privado) — sistema de orquestación multi-agente para desarrollo de software. 5 roles: Team Lead (R5), Researcher (R3), Engineer (R4), Reviewer (R4), QA (R4). Workflow por fases: `lead_intake → discovery → build → review → qa → lead_close`.

**Stack:**
- Backend: Python 3.10+ / FastAPI (puerto 8000)
- Frontend: React 19 + TypeScript + Vite (puerto 9483)
- Arranque: `start_ide.bat` en Windows 11
- Repositorio: `C:\Users\Max\Antigravity Projects\Ai_Teams\`
- Venv: `venv/Scripts/activate` (bash), Python 3.12

---

## Estado actual (2026-03-29/30)

### Lo que funciona bien ✅
- Orquestador ejecuta workflows reales con LLMs (Anthropic claude-sonnet-4-6 via suscripción)
- Streaming SSE del Team Lead al frontend en tiempo real
- Frontend IDE con 2 paneles (chat + workspace), file explorer, terminal
- Chat history: carga chats anteriores desde StatusPanel
- Historial de runs limitado al workspace activo (fix reciente)
- Botón × para borrar proyectos de la lista "Recent Projects"
- Input de Rounds como texto libre (se puede borrar y reescribir sin que revierta)
- Inspector Decision Trace: card visual con modal expandible sin truncar

### Bugs conocidos sin fix ❌

**BUG #1 — CRÍTICO: `require_execution_plan` auto-bloquea `plan_engineering`**
- Archivo: `aiteam/orchestrator.py`
- El validador pre-ejecución exige un `execution_plan` para correr... la fase que crea ese plan
- Resultado: `plan_engineering` falla en 168ms con 0 llamadas LLM, `status: failed`, bloquea build/review/qa
- Ha ocurrido en 3 sesiones distintas: CHAT-B5D99644, CHAT-D08CC854, CHAT-C32F147C
- Fix: excluir la fase `plan_engineering` de la validación `require_execution_plan`, o aplicar esa validación solo a fases ≥ `build`

**BUG #2 — ALTO: Review/QA se ejecutan aunque build esté bloqueado**
- Archivo: `aiteam/orchestrator.py` (scheduler de fases)
- Si `build:blocked` por `dependency_failed`, Review y QA ejecutan igualmente, gastan ~1.7k tokens para rechazar "nada"
- Fix: si fase dependiente está `blocked`, marcar downstream como `skipped` automáticamente

**BUG #3 — MEDIO: `filesystem_mcp` apunta a usuario incorrecto**
- Archivo: `runtime/mcp_servers.json` y configuración Claude Desktop
- Path erróneo: `C:\Users\she__\Documents\Antigravity Projects\`
- Path correcto: `C:\Users\Max\Antigravity Projects\`
- Consecuencia: agentes no pueden leer/escribir archivos reales del workspace

**BUG #4 — MEDIO: `build_synthesis_draft` marca `completed` sin entregar nada**
- Un agente puede terminar con `status: completed` pero output `"BLOCKED – no proceder"`
- Las métricas de éxito quedan infladas (tasa real ≠ tasa reportada)
- Fix: añadir campo `deliverable_produced: bool` en `session_index.jsonl`

**PROBLEMA #5 — DISEÑO: "Continue" crea sesión nueva en vez de retomar fase fallida**
- Al pulsar Continue, el orquestador lanza un nuevo `lead_intake` replanificando desde cero (~1.1k tokens desperdiciados)
- Debería poder reanudar directamente en la fase fallida con contexto ya cargado

### MCPs: estado
- `context7_mcp`: ✅ healthy
- `github_mcp`: ✅ healthy (pero disabled)
- `semgrep_mcp`, `perplexity_mcp`, `playwright_mcp`: ❌ unhealthy (404 NPM)
- `filesystem_mcp`: ❌ unhealthy (ruta incorrecta — BUG #3)
- `git_mcp`: ❌ unhealthy (dependencia `cryptography` rota en uvx)

---

## Proyecto "Prueba AITeams" — Chromashift

Workspace de prueba en `C:\Users\Max\Antigravity Projects\Prueba AITeams\`.

**Lo que el equipo de agentes acordó:**
- Juego: "Chromashift" — puzzle-plataformas con manipulación de colores RGB
- Stack: JavaScript/Canvas API, browser-native, zero setup
- Scope MVP: <500 LOC, abrir HTML y funciona
- GDD y spec técnica mínima: guardados en `runtime/workflow_state.json`

**Lo que NO se implementó** (porque build quedó bloqueado por BUG #1):
- El código del juego real con mecánicas RGB
- `game.js` actual es un prototipo anterior (collector básico, no Chromashift)

**Para continuar Chromashift:** pedir explícitamente "implementa el core loop de Chromashift con Canvas API, mecánica de cambio de color RGB" — no "continue" genérico.

---

## Archivos clave

| Archivo | Función |
|---------|---------|
| `aiteam/orchestrator.py` | Motor principal — aquí están BUG #1 y #2 |
| `api/main.py` | API FastAPI — streaming SSE, endpoints de chat/history |
| `aiteam/adapters/api.py` | Router de modelos LLM |
| `ide-frontend/src/components/TeamChat.tsx` | Chat principal con streaming |
| `ide-frontend/src/components/StatusPanel.tsx` | Panel lateral con historial |
| `ide-frontend/src/components/WorkspaceSelector.tsx` | Selector de proyecto |
| `ide-frontend/src/services/workspaceService.ts` | localStorage de workspaces |
| `runtime/` | Estado de runtime (no commitear) |

---

## Cómo arrancar

```bash
# Windows — doble clic o desde terminal:
start_ide.bat

# Manual:
source venv/Scripts/activate
uvicorn api.main:app --reload --port 8000   # backend
cd ide-frontend && npm run dev -- --port 9483  # frontend

# Tests (smoke, 2s):
venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py -q --tb=line -x
```

---

## Próximas tareas priorizadas

1. **P0** — Fix BUG #1: `require_execution_plan` en `orchestrator.py` no debe aplicarse a `plan_engineering`
2. **P0** — Fix BUG #3: corregir path de `filesystem_mcp` (usuario `she__` → `Max`)
3. **P1** — Fix BUG #2: cortocircuitar Review/QA cuando `build:blocked`
4. **P1** — Implementar Chromashift real (core loop + color switching)
5. **P2** — Fix BUG #4: añadir `deliverable_produced` a sesiones
6. **P2** — Fix PROBLEMA #5: Continue debe retomar fase fallida, no crear sesión nueva

---

## Contexto de dos máquinas

| Máquina | Rol |
|---------|-----|
| `max-gamingpc` | Principal (desarrollo activo), Windows 11 |
| `ORCH-01` (DESKTOP-SR6CQA1) | Secundaria, online en LAN, acceso RustDesk |

Syncthing sincroniza `Antigravity Projects/` entre ambas. **Nunca sincronizar** `venv/`, `node_modules/`, `runtime/`.

Repo en GitHub: `github.com/MaxBonas/ai-teams` (privado) — fuente de verdad para código.
