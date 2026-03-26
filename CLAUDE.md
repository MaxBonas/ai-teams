# AI Team Hybrid Orchestrator

Sistema de orquestacion multi-agente para desarrollo y entrega de software.
Nombre del paquete: `aiteam-hybrid` (v0.1.0). Estado: operativo para orquestacion, observabilidad y continuidad por proyecto.

## Stack

- **Backend**: Python 3.10+ con FastAPI (puerto 8000)
- **Frontend**: React 19 + TypeScript 5.9 + Vite (puerto 9483)
- **Persistencia**: archivos JSONL en `runtime/`
- **Tests**: pytest (30+ archivos en `tests/`)

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
uvicorn api.main:app --reload --port 8000          # backend
cd ide-frontend && npm run dev -- --port 9483      # frontend

# Tests
pytest tests/

# CLI
python -m aiteam.cli <comando>
# Comandos principales: init, plan, run, status, dashboard, system-check
```

## Arquitectura

Workflow por fases: `lead_intake -> discovery -> build -> review -> qa -> lead_close`

5 roles: Team Lead (R5), Researcher (R3), Engineer (R4), Reviewer (R4), QA (R4).

- **Router**: Pro-first (suscripciones OpenAI/Anthropic/Google) con fallback a API.
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

## Convenciones

- Documentacion del proyecto en **espanol**.
- Variables de entorno en `.env` (copiar de `.env.example`). Nunca commitear `.env` ni API keys.
- Templates de configuracion usan sufijo `.example` (ej. `adapters.example.json`).
- Estado de runtime va en directorios `runtime/` o `runtime_<entorno>/`.
- Entorno de desarrollo: Windows 11, shell bash, venv en `venv/` (Python 3.12).
- Raiz base de proyectos: `C:\Users\<usuario>\Documents\Antigravity Projects\` (varia por maquina).
- Activar venv: `source venv/Scripts/activate` (bash). Tests: `venv/Scripts/python.exe -m pytest tests/ -q --tb=short`.
- Tests actuales: **271 passing** (2026-03-26). Antes de cualquier cambio, verificar que pasan.
- Smoke test rapido (2s): `venv/Scripts/python.exe -m pytest tests/test_orchestrator.py tests/test_taskboard.py tests/test_router.py tests/test_api_adapter_live.py -q --tb=line -x`

## Infraestructura — dos maquinas

Este proyecto se desarrolla en un setup de dos maquinas en red local.

| Maquina | Rol | Notas |
|---------|-----|-------|
| **max-gamingpc** | Principal (desarrollo activo) | Windows 11, usuario activo: `she__` |
| **ORCH-01** (DESKTOP-SR6CQA1) | Secundaria (orquestacion) | Online en LAN, acceso via RustDesk |

**Syncthing** sincroniza la carpeta `Antigravity Projects` entre ambas maquinas.
- **NUNCA sincronizar**: `venv/`, `node_modules/`, `__pycache__/`, `.git/`, `runtime/`
- Si un venv fue sincronizado desde otra maquina, los paths internos en `pyvenv.cfg` estan rotos.
  Recrear siempre con: `py -3.12 -m venv venv --clear && venv/Scripts/pip install -r requirements.txt`
- **GitHub** (`github.com/MaxBonas/ai-teams`, privado) es la fuente de verdad para codigo.

**Red**: NordVPN en max-gamingpc puede interferir con conexiones LAN directas (RustDesk usa UDP).

## Diagnostico antes de cualquier fix

**Regla: listar 3 causas probables ANTES de tocar nada. Arreglar de una en una y verificar.**

### MCP servers que no arrancan — orden obligatorio

1. **Env vars** — Claude Desktop en Windows no hereda el PATH del shell. Verificar primero.
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
