# Arquitectura objetivo (v0.2)

Este documento resume la arquitectura implementada y su alineacion con Agent Teams.

## Componentes implementados

- **Shared Task List** (`aiteam/taskboard.py`)
  - Estados: `pending`, `ready`, `claimed`, `blocked`, `completed`, `failed`.
  - Dependencias por tarea y persistencia local.
- **Mailbox** (`aiteam/mailbox.py`)
  - Mensajes directos y broadcast en `jsonl`.
- **Memoria por agente** (`aiteam/memory.py`)
  - Almacen de memoria persistente por agente.
  - Recuperacion reciente + relevante por tarea.
  - Filtro por tipo de memoria para evitar `context poisoning` (ej. excluir `meeting_minutes`).
- **Comunicacion y reuniones** (`aiteam/communication.py`)
  - Sync meetings con minutos compartidos.
  - DMs entre agentes para desbloquear dependencias.
  - Standups compactos para evitar crecimiento recursivo del contexto.
- **Router Pro-first + API fallback** (`aiteam/router.py`)
  - Priorizacion por canal/proveedor.
  - Fallback controlado por intentos y presupuesto.
  - Filtro por `role_targets` para enrutar runtimes por rol.
- **FinOps** (`aiteam/finops.py`)
  - Presupuesto diario/mensual API.
  - Ledger de costo por decision de ruteo.
  - Señal de presion de presupuesto para downgrade (`max_api_tier`, intentos API sugeridos).
- **Runtime aislado** (`aiteam/runtime.py`)
  - Workspace por agente/tarea.
  - Locks de archivos para evitar colisiones.
- **Control de entorno y navegador** (`aiteam/execution.py`)
  - Ejecucion `cmd` y `powershell` con politica de seguridad.
  - Navegacion agéntica basica (`browser_fetch`, `browser_open`).
  - `browser_script` con Playwright (multi-step + evidencias).
- **Compliance guardrails** (`aiteam/compliance.py`)
  - Aprobacion requerida para comandos/adapters sensibles.
  - Doble aprobacion en `prod` para operaciones sensibles.
  - Redaccion de secretos en contexto operativo.
- **Registro de adapters externos** (`aiteam/adapters/registry.py`)
  - Carga runtimes propios desde `runtime/adapters.json`.
  - Inventario de herramientas externas con `aiteam/tool_inventory.py`.
- **AutoTool Integrator** (`aiteam/autotools.py`)
  - Integra `cli|mcp|skill` desde metadata de tarea.
  - Auto-discovery por capacidades faltantes con catalogo (`config/tool_sources.catalog.json`).
  - Persistencia en `runtime/tool_registry.json` y `runtime/mcp_servers.json`.
- **Quality gates** (`aiteam/orchestrator.py`)
  - Tareas de Engineer abren gates de Review + QA.
  - En tareas de riesgo se agrega gate de Security.
  - La tarea padre se libera al completar todas las gates.
- **Observabilidad** (`aiteam/observability.py`)
  - Event log y resumen por tipo de evento.
  - Alertas operativas de degradacion + dashboard HTML de operaciones.

## Flujo operativo actual

1. Team Lead crea tarea o pipeline contract-first.
2. Taskboard calcula readiness por dependencias.
3. Orquestador asigna tarea por rol y reclama lock de archivos.
4. Router selecciona canal/modelo con politica Pro-first.
5. Si faltan capacidades, auto-discovery integra tools desde catalogo y reintenta.
6. El agente usa memoria + mailbox para razonar con contexto de equipo.
   - El contexto operativo se compacta y evita mensajes de sync completos.
7. Si hay `execution_plan`, valida compliance y luego ejecuta pasos locales bajo guardrails.
8. Si Engineer finaliza, se abren gates de Review y QA (y Security en casos de riesgo).
9. Al completar gates, se cierra la tarea padre.
10. Se ejecuta sync meeting por ronda y reuniones por evento critico.
11. Todos los eventos y costos quedan auditados en `runtime/`.
12. `pilot-check` evalua salida operativa con umbrales de calidad/costo/compliance.

## Gap hacia un sistema de nivel "produccion"

- Integrar mas MCP servers reales con credenciales empresariales por entorno.
- Conectar CI remota y aprobación por pasos irreversibles.
- Endurecer deteccion de secretos/PII y politicas zero-trust para tools externas.
