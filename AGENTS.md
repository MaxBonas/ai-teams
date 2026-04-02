# AI Team Hybrid Orchestrator

<!-- layer: system-development | audiencia: agentes de desarrollo (Codex, Claude Code, Gemini) | NO es artefacto de producto -->

Sistema de orquestacion multi-agente para desarrollo y entrega de software.
Nombre del paquete: `aiteam-hybrid` (v0.1.0). Estado: operativo para orquestacion, observabilidad y continuidad por proyecto.
Validacion mas reciente: `2026-04-02`, `MAX-GAMINGPC`, `776 passed`.

## Stack

- **Backend**: Python 3.10+ con FastAPI (launcher por defecto en puerto 8010)
- **Frontend**: React 19 + TypeScript 5.9 + Vite (launcher por defecto en puerto 9490)
- **Persistencia**: SQLite para `tasks` y `workflow_state`, JSONL para ledger/eventos y compatibilidad JSON residual solo en tests/constructores legacy
- **Tests**: pytest (`776 passed` en `MAX-GAMINGPC`, 2026-04-02)

## Estructura del proyecto

```
aiteam/           Nucleo del orquestador (33 modulos Python)
api/              Backend FastAPI
ide-frontend/     Frontend React + TypeScript
tests/            Suite de pruebas pytest
config/           Templates de configuracion y catalogos
docs/             28 archivos de documentacion
scripts/          Scripts utilitarios (benchmark, ingestion)
runtime/          Estado de runtime: memoria, eventos, mailbox
```

## Comandos de desarrollo

```bash
# Arranque rapido (Windows)
start_ide.bat                    # levanta backend + frontend con health checks
stop_ide.bat                     # detiene procesos del IDE

# Manual
scripts\python_local.bat -m uvicorn api.main:app --reload --port 8010   # backend
cd ide-frontend && npm run dev -- --port 9490                           # frontend

# Tests
scripts\pytest_local.bat tests -q --tb=line

# CLI
scripts\python_local.bat -m aiteam.cli <comando>
# Comandos principales: init, plan, run, status, dashboard, system-check
```

## Arquitectura

Workflow base por fases: `lead_intake -> dynamic phases -> lead_close`

6 roles: Team Lead (R5), Scout (R2/R3), Researcher (R3), Engineer (R4), Reviewer (R4), QA (R4).

- **Router**: Pro-first (suscripciones OpenAI/Anthropic/Google) con fallback a API.
- **Observabilidad de routing**: pestaña `Routing` en `StatusPanel` y endpoint `/api/aiteam/routing/catalog` para ver primario/fallbacks efectivos por rol.
- **Quality gates**: Review + QA obligatorios antes de cerrar tareas de Engineer.
- **Compliance**: guardrails para operaciones sensibles, redaccion de secretos, doble aprobacion en `prod`.
- **FinOps**: presupuesto diario/mensual, señal de presion, ledger de costos.

## Archivos clave

| Archivo | Funcion |
|---------|---------|
| `aiteam/orchestrator.py` | Motor principal de orquestacion |
| `aiteam/router.py` | Logica de ruteo hibrido Pro-first + API |
| `aiteam/taskboard.py` | Gestion de tareas y dependencias |
| `aiteam/compliance.py` | Guardrails de seguridad y compliance |
| `aiteam/finops.py` | Presupuesto y control de costos |
| `aiteam/memory.py` | Memoria persistente por agente |
| `aiteam/execution.py` | Ejecucion de comandos con sandbox |
| `api/main.py` | Entrada de la app FastAPI |
| `ide-frontend/src/` | Interfaz web React |

## Glosario de capas (evitar colisiones de lenguaje)

Este sistema construye otros sistemas. Los mismos terminos tienen significados diferentes segun la capa.
Ver investigacion completa en `docs/NAMING_COLLISION_INVESTIGATION.md`.
Ver guia de comunicacion para desarrolladores en `docs/COMMUNICATION_GUIDE_FOR_DEVS.md`.

| Termino | En este repo (desarrollo) | En el orquestador (producto) |
|---|---|---|
| `agent` | Agente de desarrollo: Codex, Claude Code, Gemini | Rol LLM interno: Lead, Engineer, Reviewer, QA |
| `task` | Tarea en `task.md` (backlog de desarrollo) | `WorkTask`: unidad ejecutable del orquestador |
| `handoff` | Traspaso de sesion de desarrollo (`HANDOFF.md`) | Failover automatico de adapter en el orquestador |
| `run` | Sesion de trabajo de desarrollo | Ejecucion de chat completa (lead_intake → lead_close) |
| `phase` | Fase de desarrollo del sistema (B7, B8, B9) | Etapa del WORKFLOW_PLAN (build, review, qa) |
| `checkpoint` | Punto de revision en el proceso de desarrollo | Tarea especial del Lead (`lead_report_*`, `lead_preflight_*`) |
| `plan` | Planificacion del sistema | WORKFLOW_PLAN emitido por el Lead |
| `workspace` | Directorio del repo Ai_Teams | Directorio raiz del proyecto externo gestionado |
| `project` | El sistema Ai_Teams / el repo | Un proyecto externo gestionado por AI Teams |
| `agent` (sin calificar) | **AMBIGUO** — calificar siempre | **AMBIGUO** — calificar siempre |

**Norma critica**: AI Teams nunca crea archivos `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` ni similares en proyectos externos. Todos los artefactos de producto van bajo `.aiteam/` del proyecto, y las instrucciones persistentes del usuario para el Lead viven en `.aiteam/instructions.md`. Ver `docs/NAMING_COLLISION_INVESTIGATION.md` seccion "Colision 1".

## Convenciones

- Documentacion del proyecto en **espanol**.
- Variables de entorno en `.env` (copiar de `.env.example`). Nunca commitear `.env` ni API keys.
- Templates de configuracion usan sufijo `.example` (ej. `adapters.example.json`).
- Estado de runtime va en directorios `runtime/` o `runtime_<entorno>/`.
- Entorno de desarrollo: Windows 11, shell PowerShell o bash, venv en `venv/` (Python 3.12).
- Raiz base de proyectos: `C:\Users\<usuario>\Documents\Antigravity Projects\` (varia por maquina).
- Activar venv: `source venv/Scripts/activate` (bash). Bootstrap recomendado: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_local_venv.ps1`
- Wrappers locales:
  - Python: `.\scripts\python_local.bat`
  - Pytest: `.\scripts\pytest_local.bat`
  - Reanudacion tras pull: `.\scripts\prepare_dev_env.bat`
- Tests actuales: **776 passed** (2026-04-02, `MAX-GAMINGPC`). Antes de cualquier cambio, verificar que pasan.
- Smoke test rapido (2s): `.\scripts\pytest_local.bat tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x`

## Infraestructura — dos maquinas

Este proyecto se desarrolla en un setup de dos maquinas en red local.

| Maquina | Rol | Notas |
|---------|-----|-------|
| **MAX-GAMINGPC** | Principal (desarrollo activo) | Windows 11, usuario activo: `she__` |
| **ORCH-01** (DESKTOP-SR6CQA1) | Secundaria (orquestacion) | Online en LAN, acceso via RustDesk |

**Syncthing** sincroniza la carpeta `Antigravity Projects` entre ambas maquinas.
- **NUNCA sincronizar**: `venv/`, `node_modules/`, `__pycache__/`, `.git/`, `runtime/`
- Si un venv fue sincronizado desde otra maquina, los paths internos en `pyvenv.cfg` estan rotos.
  Recrear siempre con: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ensure_local_venv.ps1 -ForceRecreate`
- Si el launcher de `venv/Scripts/python.exe` falla en una maquina sincronizada, tratar el venv como no confiable y recrearlo localmente antes de depurar codigo.
- `start_ide.bat` ya intenta validar o reparar `venv/` antes de arrancar backend/frontend.
- `runtime/` debe tratarse como estado local por maquina. Si falta config runtime tras un pull, rehidratar con `.\scripts\prepare_dev_env.bat`.
- **GitHub** (`github.com/MaxBonas/ai-teams`, privado) es la fuente de verdad para codigo.

**Red**: NordVPN en max-gamingpc puede interferir con conexiones LAN directas (RustDesk usa UDP).

## Protocolo De Desarrollo Entre Maquinas

Objetivo principal: poder avanzar en una maquina, cambiar a la otra y seguir programando rapido sin reparaciones manuales largas.

Regla de oro:

- Git comparte codigo y plantillas
- Cada maquina mantiene su propio `venv/`
- Cada maquina mantiene su propio `runtime/`
- Cada maquina mantiene su propio `node_modules/`

Flujo obligatorio al cambiar de maquina:

1. `git commit` + `git push` en la maquina origen
2. `git pull` en la maquina destino
3. `.\scripts\prepare_dev_env.bat`
4. continuar con `.\scripts\python_local.bat` y `.\scripts\pytest_local.bat`

## Estrategia de commit entre maquinas

Cuando haya una tanda grande de cambios, no hacer un commit monolítico si mezcla:

- portabilidad del entorno
- limpieza de artefactos locales
- funcionalidad de backend/frontend
- documentación

Estrategia recomendada:

1. `chore/dev-env-portability`
2. `chore/stop-tracking-local-state`
3. `feat/...` con funcionalidad, tests y docu

Regla práctica:

- el commit 1 debe dejar `pull -> .\scripts\prepare_dev_env.bat` funcionando
- el commit 2 debe sacar del repo `runtime/`, snapshots y artefactos locales históricamente trackeados
- el commit 3 debe llevar el producto

Documento de referencia para este corte:

- `docs/COMMIT_STRATEGY_2026_04_02.md`

Configuracion compartida:

- editar `config/*.example.json`
- no usar `runtime/*.json` como fuente de verdad compartida
- `prepare_dev_env` solo refresca desde plantilla si el archivo local seguia sincronizado; si detecta override local, lo conserva

Excepcion permitida en `runtime/`:

- `runtime/ollama/Modelfile.aiteam-qwen-coder`

## Diagnostico antes de cualquier fix

**Regla: listar 3 causas probables ANTES de tocar nada. Arreglar de una en una y verificar.**

### MCP servers que no arrancan — orden obligatorio

1. **Env vars** — Codex Desktop en Windows no hereda el PATH del shell. Verificar primero.
2. **Venv integridad** — ¿existe local? ¿`pyvenv.cfg` apunta a esta maquina?
3. **Paths con espacios** — `Antigravity Projects` tiene un espacio; usar comillas siempre.
4. **Puerto/protocolo** — verificar puerto correcto y TCP vs UDP antes de tocar firewall.
5. Solo si 1-4 estan OK: investigar DLL / pywin32. Usar skill `/fix-mcp`.

### Problemas de red / conectividad — orden obligatorio

1. Verificar si **NordVPN esta activo** — puede bloquear trafico LAN directo.
2. Verificar **protocolo correcto** (RustDesk = UDP, no TCP).
3. Verificar **puerto correcto** antes de modificar reglas de firewall.
4. Solo entonces: revisar reglas de firewall.

### Python / venv en Windows

- Multiples versiones instaladas. Usar siempre `py -3.12` o el venv del proyecto.
- En bash (Git Bash): paths con `/c/Users/...` no con `C:\Users\...`.
- Comillas obligatorias en paths con espacios: `"/c/Users/she__/Documents/Antigravity Projects/..."`.
- `UnicodeDecodeError cp1252` en subprocesos externos: ignorar, es un warning del SO, no del codigo.
