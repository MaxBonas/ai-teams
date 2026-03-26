# Internal Product Qualities And Roadmap

Este documento interno consolida todas las cualidades que pediste explicitamente y las que se infieren del objetivo del proyecto (AI Team de alto rendimiento, Pro-first y operativo en entorno local).

## Vision de producto

Construir un **AI Team operativo** que funcione como una unidad de ingenieria real:

- roles especializados (Lead, Researcher, Engineer, Reviewer, QA),
- colaboracion asyncrona + sincronizaciones,
- memoria individual y colectiva,
- ejecucion real de planes (entorno, archivos, comandos, browser),
- costo optimizado (Pro-first, API cuando se requiere),
- trazabilidad, seguridad y control de calidad.

## Cualidades no negociables

1. **Colaboracion multiagente real**
   - Mensajeria DM + broadcast.
   - Reuniones (sync meetings) para converger decisiones.
   - Consultas entre pares para tareas complejas/criticas.

2. **Memoria por agente + memoria compartida operativa**
   - Persistencia por agente.
   - Recuperacion de memoria relevante para cada tarea.
   - Minutas de reuniones almacenadas en memoria.

3. **Pro-first + API fallback inteligente**
   - Priorizar canales de suscripcion para minimizar coste marginal.
   - Fallback automatico a API por bloqueo, cuota, complejidad o criticidad.
   - Trazabilidad de decisiones de ruteo.

4. **Ejecucion real de trabajo tecnico**
   - Control de entorno local y sandboxes.
   - Control de archivos con locks para evitar colisiones.
   - Ejecucion de `cmd`, `powershell` y planes secuenciales.

5. **Control agéntico de navegador**
   - Fetch/open basico.
   - Script de automatizacion con Playwright cuando esta disponible.

6. **Calidad y disciplina de entrega**
   - Gates de Review y QA antes de cerrar tareas de implementacion.
   - Dependencias y contract-first.
   - Cierre solo con criterios de salida claros.

7. **Seguridad operacional y guardrails**
   - Politica de comandos (allowlist + patrones bloqueados).
   - Restriccion de workdir al proyecto.
   - Registro de eventos de ejecucion.

8. **FinOps y observabilidad**
   - Presupuesto API diario/mensual.
   - Ledger de costo por decision.
   - Eventos operativos para analitica y tuning.

9. **Integrabilidad con runtimes existentes**
   - Reutilizar programas agenticos ya construidos via adapters externos.
   - Enrutado por rol/capacidad/proveedor/canal.

10. **Escalabilidad organizacional**
   - Arquitectura orientada a equipos de agentes, no agente unico.
   - Base para evolucionar a pipeline de produccion multi-proyecto.

## Requisitos inferidos de alto impacto

- Evitar envenenamiento de contexto con aislamiento y memoria selectiva.
- Evitar sobre-ingenieria con gates y contract-first.
- Habilitar trabajo paralelo seguro (locks + ownership de archivos).
- Soportar supervision humana con trazabilidad completa.
- Diseñar para operar en modo continuo (run por rondas + reuniones por evento).

## Estado actual resumido

- Ya implementado:
  - Router Pro-first + fallback API.
  - Taskboard con dependencias, estados y locks.
  - Mailbox y reuniones de sincronizacion.
  - Memoria por agente y consulta relevante.
  - Ejecucion local (`cmd`, `powershell`, browser fetch/open, browser script opcional).
  - Gates de Review/QA.
  - FinOps + observabilidad.
  - Adapter externo para programas ya existentes.

- En progreso:
  - Politicas de compliance avanzadas y secret handling (base de aprobaciones ya implementada).

- Cerrado en esta iteracion:
  - Automatizacion browser avanzada (playwright multi-step + evidencia).
  - Security gate dedicado para tareas de riesgo (review/qa/security).
  - Dashboard operativo HTML + alertas de degradacion.

## Roadmap por fases

### Fase A - Hardening operativo (corto plazo)

- Integrar 2-3 runtimes reales por rol via `runtime/adapters.json`.
- Activar browser mode `playwright` en entornos que lo soporten.
- Añadir plantillas de `execution_plan` por tipo de tarea.
- Añadir policy profiles: `strict`, `balanced`, `aggressive`.

### Fase B - Calidad y seguridad (corto/medio plazo)

- Security gate dedicado (SAST/secret scan) para tareas de riesgo.
- Politicas de aprobacion humana para comandos sensibles.
- Clasificacion de datos sensibles y redaccion de logs.

### Fase C - Inteligencia colaborativa (medio plazo)

- Reuniones disparadas por eventos complejos (conflictos repetidos, fallos consecutivos).
- Debates estructurados entre agentes (hypothesis vs counter-hypothesis).
- Scoring de calidad de aportes por rol/modelo.

### Fase D - Ejecucion avanzada (medio plazo)

- Browser automation robusta (login flows, formularios, snapshots, evidencias).
- Workflows de CI local/remote controlados por plan.
- Integracion con MCP/tool servers para herramientas externas.

### Fase E - Produccion y gobierno (medio/largo plazo)

- Panel operacional (UI) sobre `events`, `cost`, `tasks`, `memory`.
- Multi-proyecto y multi-tenant.
- Runbooks de incidentes, rollback operativo y auditoria completa.

## KPIs de exito sugeridos

- `% de tareas resueltas en Pro`: objetivo >= 60%.
- `fallback API por causa`: visibilidad y tendencia a la baja.
- `pass rate de gates`: objetivo >= 85%.
- `coste por tarea`: tendencia descendente.
- `MTTR de bloqueos`: tendencia descendente.
- `% tareas con evidencia de reunion/peer-consultation` para casos criticos.

## Principios de diseño para el equipo

- **Autonomia con guardrails**.
- **Colaboracion por defecto**.
- **Coste consciente por diseño**.
- **Ejecucion verificable**.
- **Seguridad antes que velocidad en comandos sensibles**.
