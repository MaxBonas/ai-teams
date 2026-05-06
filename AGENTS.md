# AI Teams - instrucciones para agentes de desarrollo

<!-- layer: system-development | audiencia: Codex, Claude Code, Gemini y agentes de desarrollo -->

AI Teams es un control plane multi-agente para equipos de programacion. El repo esta en una limpieza y reconstruccion profunda: se abandona la compatibilidad con dogfooding/proyectos antiguos y se converge hacia un sistema Paperclip-like sobre SQLite.

## Prioridad vigente

Trabajar siempre contra el plan rector:

- `docs/MIGRATION_PAPERCLIP.md`
- `task.md`
- `docs/INDEX.md`
- `HANDOFF.md`

La documentacion legacy, los tests legacy y los prompts raiz de proveedor fueron retirados de la fuente viva. No reintroducir `CLAUDE.md`, `GEMINI.md`, docs de archivo antiguo ni suites que protejan el flujo viejo salvo que el usuario lo pida explicitamente.

## Producto objetivo

AI Teams debe funcionar casi como Paperclip a nivel de control plane, pero con identidad propia:

- orientado a equipos de programacion, no a empresas genericas;
- Lead-first: se crea primero el Lead;
- flujo Paperclip-like: el usuario propone una tarea de proyecto, el Lead la recibe y la mantiene viva mediante issues, heartbeats, delegaciones, revisiones e interactions hasta cerrarla o pedir desbloqueo;
- hiring dinamico: el Lead forma el equipo despues de entender el proyecto;
- perfiles canonicos: `solo_lead`, `lead_quorum`, `full_team`;
- planificacion detallada como obligacion de rol: objetivo, sub-issues, delegaciones, riesgos, posibles roturas de la siguiente run, criterios de revision y condiciones de escalado;
- accountability explicita: cada agente debe saber a quien reporta, que entrega, que evidencia produce y quien acepta/rechaza su resultado;
- bajo ruido operativo: gates proporcionales al riesgo, pocas approvals, pocas preguntas al usuario y ningun quorum/review pesado para tareas simples;
- delegacion economica: seniors/quorum planifican y supervisan, workers baratos ejecutan tareas simples para ahorrar tokens/coste;
- suscripciones LLM y APIs son canales independientes;
- SQLite queda como motor unico.

## Arquitectura objetivo

Patron Paperclip sobre SQLite:

- `issues`: trabajo estructurado, dependencias y checkout;
- `agents`: identidad, rol, adapter fijo, presupuesto y heartbeat;
- `team_blueprints` y `agent_assignments`: hiring dinamico y composicion del equipo;
- `runs`: entidad central de telemetria, liveness, coste y recovery;
- `wakeup_requests`: cola durable en DB;
- `issue_thread_interactions`: pausa/reanudacion persistente;
- `run_events`, `cost_events`, `activity_log`, `tool_access`: reemplazo de JSONL.

## Codigo activo y deuda

Mantener funcionando los shims necesarios mientras se extirpa el sistema viejo.

Objetivos de extirpacion:

- parser `[WORKFLOW_PLAN]`;
- round-based `process_once()`/`run_until_idle()`;
- router algoritmico con scoring multifactor;
- writers JSONL como fuente primaria;
- promptaje antiguo de Lead/roles;
- tests que solo validan comportamiento legacy.

No borrar modulos importados sin reemplazar primero sus rutas activas.

## Comandos

```powershell
.\scripts\python_local.bat -m uvicorn api.main:app --reload --port 8010
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\migrate_to_v2.py --json
```

Frontend:

```powershell
Set-Location ide-frontend
npm run dev -- --port 9490
node_modules\.bin\tsc.cmd -b
```

## Reglas de trabajo

- Documentacion del proyecto en espanol.
- No commitear `.env`, API keys, `venv/`, `node_modules/` ni runtime local.
- Templates compartidos en `config/*.example.json`.
- Runtime por maquina en `runtime/`.
- Usar `rg` para busquedas.
- Usar `apply_patch` para ediciones manuales.
- No revertir cambios no propios.
- Antes de tocar un bug o limpieza delicada, listar 3 causas probables y arreglar de una en una.

## Naming critico

AI Teams nunca debe crear `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` ni equivalentes en proyectos externos. Todo artefacto del producto va bajo `.aiteam/`, y las instrucciones persistentes del usuario viven en `.aiteam/instructions.md`.

Evitar ambiguedades:

- `agent` de desarrollo: Codex, Claude Code, Gemini.
- `agent` del producto: Lead, Engineer, Reviewer, QA.
- `run` de desarrollo: sesion humana/agente sobre este repo.
- `run` del producto: ejecucion persistida en SQLite.

## Maquinas

Git es la fuente de verdad. Cada maquina mantiene su propio entorno:

- `venv/`
- `runtime/`
- `node_modules/`

Al cambiar de maquina:

```powershell
git pull
.\scripts\prepare_dev_env.bat
```
