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
- Entorno de desarrollo: Windows 11, shell bash, venv en `venv/`.
- Raiz de herramientas compartidas: `C:\Users\Max\Antigravity Projects`.
