# Plan profundo de montaje (Pro-first + API fallback)

Estado global: **Completado (v1 local operativa)**

1. **Definir objetivos operativos y KPIs** - Completado
   - KPI de ahorro: % de tareas resueltas en canal Pro.
   - KPI de calidad: pass rate de tests y review.
   - KPI de estabilidad: tasa de fallback, latencia, reintentos.

2. **Inventariar tus programas agenticos existentes** - Completado
    - Catalogar capacidades por programa (coding, research, QA, PR review).
    - Identificar interfaces de entrada/salida para crear adapters.
    - Clasificar prioridad por herramienta (`primary` vs `secondary`).
    - Inventario automatico disponible con `inventory-tools`.

3. **Formalizar contrato unico de adapters** - Completado
   - Estandarizar `available()`, `invoke()`, capacidades y metadatos.
   - Separar canal de ejecucion: `subscription` vs `api`.

4. **Implementar router hibrido con politica Pro-first** - Completado
   - Priorizar adapters de suscripcion (3 proveedores base).
   - Aplicar fallback a API con razon auditable.
   - Registrar intentos fallidos y decision final.

5. **Montar Task Board compartida con dependencias (DAG)** - Completado
   - Estados: `pending`, `ready`, `claimed`, `blocked`, `completed`, `failed`.
   - Persistencia JSON para trazabilidad local.

6. **Montar Mailbox inter-agente** - Completado
   - Registro de mensajes tipo DM/Broadcast.
   - Historial por tarea para auditoria.

7. **Definir perfiles de equipo y plantillas de prompt por rol** - Completado
   - Team Lead, Researcher, Engineer, Reviewer, QA.
   - Instrucciones de estilo y salida esperada por rol.

8. **Integrar runtime de ejecucion por agente (sandbox/worktree)** - Completado
   - Aislamiento por agente para evitar colisiones de archivo.
   - Politica de ownership de ficheros por tarea.

9. **Agregar gates de calidad (review + test + seguridad)** - Completado
    - Gate obligatorio antes de `completed`.
    - Rama de Security/Perf bajo heuristicas de riesgo.
    - Gate `security` agregado para tareas de alto riesgo.

10. **Implementar FinOps real-time** - Completado
    - Presupuesto diario/mensual por proyecto.
    - Politicas de downgrade/upgrade por consumo.
    - Fallback API centrado en modelos GPT costo-eficientes.
    - Señal dinamica de presupuesto (`max_api_tier`, `suggested_api_attempts`).

11. **Observabilidad integral y panel de operaciones** - Completado
    - Metricas por rol/modelo/canal.
    - Alertas de degradacion y errores recurrentes.
    - KPI operativo automatizable con `pilot-check`.
    - Dashboard HTML generado con comando `dashboard`.

12. **Piloto controlado + tuning de reglas** - Completado
    - Ejecutar set de tareas reales (bugs, features, refactors).
    - Ajustar pesos de ruteo, tiempos de timeout y fallback.
    - Gate operativo automatizado con `pilot-check`.

13. **Politicas de cumplimiento y seguridad** - Completado
    - Reglas de secretos, datos sensibles, y auditoria de prompts.
    - Politicas por entorno (dev/stage/prod).
    - Aprobacion humana para comandos y adapters sensibles.

14. **Despliegue progresivo en produccion** - Completado
    - Habilitar por equipos/proyectos gradualmente.
    - Runbooks de incidentes y rollback operativo.
    - Runbook inicial documentado (`docs/PRODUCTION_ROLLOUT_RUNBOOK.md`).

15. **Memoria individual y colaborativa por agente** - Completado
    - Memoria persistente por agente (`runtime/memory`).
    - Recuperacion de memoria reciente y relevante antes de ejecutar tareas.

16. **Reuniones de sincronizacion (team sync meetings)** - Completado
    - Standups automaticos por ronda.
    - Minutas compartidas via mailbox y guardadas en memoria de cada agente.

17. **Ejecucion agéntica de entorno/sistema/browser** - Completado
    - Motor de ejecucion con `cmd`, `powershell`, `browser_fetch`, `browser_open`.
    - Guardrails por politica de comandos (allowlist + blocked patterns).
    - `browser_script` multi-step con evidencias (Playwright opcional).
    - Validado en runtime real (`runtime_playwright`) con evidencia PNG.

18. **Integracion de runtimes externos por rol** - Completado
    - Carga de adapters desde `runtime/adapters.json`.
    - Filtro por `role_targets` en el router.
    - Consulta de herramientas en `Antigravity Projects` habilitada para ejecucion controlada.
    - Inventario + sugerencias de adapters via `runtime/tool_inventory.json`.

19. **Reuniones por evento critico** - Completado
    - Reuniones auto-disparadas por fallo, conflicto de archivos o apertura de quality gates.

## Entregables ya iniciados en este repo

- Nucleo de orquestacion (`aiteam/`)
- Router Pro-first con fallback API
- Task Board + Mailbox persistentes
- CLI para bootstrap, plan, demo y estado
- Pruebas base de router y taskboard
- Registro de locks de archivos (`runtime/file_locks.json`)
- Workspaces por agente (`runtime/sandboxes/`)
- Ledger de costos API (`runtime/cost_ledger.jsonl`)
- Eventos operativos (`runtime/events.jsonl`)
- Memoria de agentes (`runtime/memory/*.jsonl`)
- Ejecutor local con guardrails (`aiteam/execution.py`)
- Registro de adapters externos (`aiteam/adapters/registry.py`)

## Siguiente etapa (v2)

- Ver `docs/MCP_CLI_SKILLS_ROADMAP.md` para la capa de integraciones profundas:
  - MCP/tool servers,
  - auto-adquisicion de CLI/skills,
  - workflows CI remotos con gobernanza.
