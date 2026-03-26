# Plan Profundo de Producción - AI Teams (Consolidado)

Estado de actualización: **Marzo 2026**

Este documento unifica y expande el `ROADMAP_PRODUCCION_AITEAM.md` y el `SPRINT_ROADMAP_Q1_2026.md` en un único mega-plan de tareas accionables para llevar el proyecto AI Teams desde el estado funcional local actual hasta una versión Enterprise/SaaS nivel Producción.

## Resumen del Estado Actual
- Orquestador Core v1 funcional (taskboard, perfiles, router híbrido Pro-first + API fallback).
- Evidencia base y métricas FinOps completadas (Iteración 1 / Sprint 1 finalizado, 108 tests).
- Persistencia atómica de Ledger implementada.
- IDE Frontend básico operativo.

---

## Listado Profundo de Tareas a Producción

### Fase 1: Hardening de Observabilidad, Compliance y Configuración (Sprint 2 - En curso)
*Objetivo: Estabilidad garantizada, métricas hiper-precisas y auditoría total.*

- [ ] **1.1 Métricas de Latencia Dinámicas**: Implementar cálculo de percentiles de latencia (p50/p95/p99) evaluables en ventanas de tiempo configurables (5m/1h/24h).
- [ ] **1.2 Categorización de Errores**: Ampliar el clasificador de fallos para separar `api_error`, `timeout`, y `budget_block` en el Event Log y Summary.
- [ ] **1.3 Políticas de Alertas Dinámicas**: Crear clase `AlertPolicy` para desvincular umbrales hardcodeados, permitiendo inyectar configuraciones de degradación mediante CLI/env.
- [ ] **1.4 Audit Trail Restringido**: Añadir marcas de tiempo, identidad del aprobador y regla aplicada a todas las aprobaciones/rechazos del `ComplianceGuard`.
- [ ] **1.5 Validación de Esquema de Configuración**: Definir validadores JSON Schema para `routing_policy`, `tool_catalog` y `skills_library` e implementarlos en la carga inicial del CLI.
- [ ] **1.6 Limpieza Cíclica**: Aplicar recolección de basura / archivo en el ledger histórico (archivar en chunks diarios/semanales).

### Fase 2: Integración Robusta de Herramientas, Tests E2E y Caos (Sprint 3)
*Objetivo: Comportamiento determinista del sistema y resiliencia ante cortes externos.*

- [ ] **2.1 Version Pinning Activo**: Implementar `ToolLockManager` para crear y adherirse estrictamente a `runtime/tool_lock.json` al descargar y usar herramientas (NPM/Python).
- [ ] **2.2 Resilience de Adquisición de Tools**: Incorporar reintentos con backoff exponencial (1s, 2s, 4s) al aprovisionar herramientas transitorias.
- [ ] **2.3 Madurez de Skills Playbooks**: Elevar todos los playbooks manuales (`database_ops_skill`, `playwright_qa_skill`, etc.) a un formato canónico con guardrails formales y recuperación de fallos.
- [ ] **2.4 Suite E2E de Integración**: Crear `tests/test_integration_cli.py` probando los verdaderos flujos del ciclo de vida (`init` -> `provider-connect` -> `plan` -> `run`).
- [ ] **2.5 Chaos Testing (Inyección de Fallos)**: Escribir tests específicos que corrompan el ledger, simulen caídas de NPM y recorten cuotas de FinOps en vivo para validar auto-recuperación.

### Fase 3: Runtime de Ejecución, Calidad de Evidencia y UX (Del Roadmap Original)
*Objetivo: Exigir trabajo "real" a los agentes y proveer visibilidad al usuario operativo.*

- [ ] **3.1 Evidence Gates Estrictos**: Bloquear permanentemente el paso a estado `completed` si la fase (build/review/qa) carece de impacto en archivos o validación real (logs de test).
- [ ] **3.2 Erradicar Placeholders**: Añadir heurísticas en el pipeline que rechacen respuestas como "I have simulated the test" y devuelvan las tareas a `failed` para su reintento por ejecución verdadera.
- [ ] **3.3 Medición Real de Slices**: Capturar el tiempo cronometrado preciso por fase/tarea sumado al tiempo general de run.
- [ ] **3.4 Trazabilidad Diff Completa**: Registrar `git diff` o diff de archivos en el evento por fase para mostrar explícitamente qué cambió el Engineer o refactorizó el Reviewer.
- [ ] **3.5 Barras de Evidencia UI**: Expandir el IDE Frontend para mostrar un badge enriquecido que incluya: métricas de pruebas pasadas, comandos ejecutados explícitos, fallos en caliente y si operó en modo Simulado o Live.
- [ ] **3.6 Mensajes Accionables UI**: Al fallar un "evidence gate", proveer un mensaje claro en la UI del IDE explicando por qué fue rechazado (ej. "Código no modificado").
- [ ] **3.7 Diferencial Bootstrap vs Build UI**: Mostrar en el panel lateral de forma clara el aporte del Bootstrap original vs los aportes orgánicos del AI Team.

### Fase 4: Escalabilidad, Seguridad Aislada y FinOps Avanzado
*Objetivo: Listo para despliegues a gran escala y entornos Cloud Zero-Trust.*

- [ ] **4.1 Sandboxing Severo de Comandos**: Envolver ejecuciones de la máquina en contenedores transitorios (Docker/gVisor) o un subsistema de aislamiento restrictivo, con cuotas RAM/CPU calculadas.
- [ ] **4.2 Detección de Secretos Universal**: Extender la redacción paramétrica más allá del Event Log. Redactar secretos expuestos dentro de la memoria de los propios agentes (LLM Context Poisoning provocado).
- [ ] **4.3 Auditoría de Seguridad Operativa**: Generar y persistir un PDF/Markdown de "Compliance Summary" al final de cada Run para adjuntarlo como artefacto (útil para auditorías SOC2).
- [ ] **4.4 Versionamiento de Prompts (Prompt-Git)**: Sacar los prompts del sistema hardcodeados en código y pasarlos a un gestor externo (config) que permita versionado para probar su eficacia mediante tests (A/B testing local).
- [ ] **4.5 Forecasting Predictivo de Costes**: Usar medias móviles en FinOps para advertir al usuario si el proytecto está en "zona roja" de agotar su presupuesto del mes en X días.
- [ ] **4.6 Retries Cognitivos Múltiples**: Hand-off activo entre proveedores si un modelo `lead` es incapaz de pasar los Quality Gates tras 3 intentos.

### Fase 5: Pruebas de Flujos de Juego / Loops de Larga Duración
*Objetivo: Garantizar que el orquestador soporte proyectos sostenidos sin degradarse en bucles inútiles.*

- [ ] **5.1 Slices de Iteración Modulares**: Separar obligatoriamente la fase "Bootstrap" de las fases de iteración regular para desarrollo continuado (ej. "gameplay/UX").
- [ ] **5.2 Control de Estado Viciado (Stale Loops)**: Detectar si un proceso cíclico (Bugs <-> Fixes) supera 5 iteraciones y lanzar un bloqueo manual esperando input humano.
- [ ] **5.3 Handoff a Notebook LM Optimizada**: Mejorar el conector con NotebookLM para extraer automáticamente resúmenes retrospectivos al final del sprint que actúen de "Guia de Aprendizaje" en sprints posteriores.

### Fase 6: Go-Live y Despliegue Empresarial
*Objetivo: Entrega a usuarios finales de producción.*

- [ ] **6.1 Dashboard Grafana/Datadog**: Proveer exportadores de las métricas (Prometheus) de orquestación, budget block, ruteo para infraestructuras empresariales.
- [ ] **6.2 Staging & Canary Tests**: Definir un entorno con métricas sombra (Shadow DOM equivalentes) antes del paso final de Prod.
- [ ] **6.3 Pruebas de Carga a Concurrencia**: Escalar pruebas locales a un throughput definido garantizando latencias operativas normales sin race-conditions.
- [ ] **6.4 Runbooks L1/L2**: Desarrollar guías de Troubleshooting e Incidentes ante bloqueos de modelos core (OpenAi Down, Groq Down).
- [ ] **6.5 Sign-off Técnico de Producción**: Check final de la suite y habilitación de `--strict-prod`.
