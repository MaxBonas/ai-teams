<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-07-17`

AI Teams ya no está en reconstrucción inicial. Es un control plane multiagente Paperclip-like funcional, centrado en SQLite, y se encuentra en fase de endurecimiento operativo, validación con proveedores reales y medición frente a un agente único.

## Autoridad documental

Orden de prioridad:

1. `AGENTS.md`: reglas de desarrollo y producto objetivo.
2. `task.md`: estado y backlog resumido.
3. `docs/MIGRATION_PAPERCLIP.md`: plan rector e historial de la migración.
4. `docs/PAPERCLIP_GUIDE.md`: patrones Paperclip adaptados.
5. `docs/RUN_PROBLEMS_REGISTRY.md`: fallos observados y mitigaciones.
6. Código activo y tests.

`CLAUDE.md` y `.claude/skills/` orientan sesiones de Claude Code. `.agents/skills/` orienta Codex. Ninguna instrucción específica de proveedor prevalece sobre `AGENTS.md`.

## Estado técnico

Implementado y activo:

- SQLite como motor único del control plane: issues, agents, assignments, runs, wakeups, interactions, reports, costes, actividad y acceso a herramientas.
- `HeartbeatLoop` + `HeartbeatScheduler` + `RunExecutor` como camino real de ejecución, con reconciliation y liveness en cada tick.
- Checkout atómico, dependencias, wakeups durables y continuación de padres al cerrar hijos.
- Adapters reales para canales API y suscripción, con allowlist por proyecto, health probes y recovery/escalado.
- Lead-first, hiring dinámico y perfiles `solo_lead`, `lead_quorum` y `full_team`.
- Delegación económica por tier/capacidad, quality cascade y límite diario de coste.
- Reports estructurados con provenance, receipts Git, revisión anclada al diff, aceptación independiente y `test_runner` determinista.
- Cross-provider review vinculante en criticidad alta y quorum para decisiones complejas.
- Context diet, focus files, payload delta y memoria operativa mediante `learning_facts`.
- Cockpit Vite/React sobre APIs v2, timeline durable, decisiones humanas, equipo, runs y costes.
- Canario e2e sin LLM y benchmark A/B contra `codex exec` único.
- Canario Lead + Quorum sin LLM con gate de aportes, síntesis y cierre durable de planificación, sin ejecución.

La compatibilidad legacy ya no gobierna el runtime. Persisten únicamente shims o migraciones aisladas que deben eliminarse solo tras confirmar consumidores reales.

## Trabajo reciente

- Health de perfiles locales basado en runtime y modelo, no en autenticación de Codex.
- Corrección de intención de edición para delegaciones `Fix` asignadas a roles read-only.
- Context diet y harness de benchmark frente a Codex solo.
- Métricas deterministas de calidad y pasada QA adversarial.
- Tests de aceptación independientes y review anclada al diff.
- Garantía de wakeup al padre cuando un hijo cierra.
- Notificaciones de escalado y métrica de latencia de decisiones.
- Feedback de salud de proveedores hacia el routing.
- Memoria operativa entre proyectos.
- Canario e2e de convergencia completa.
- Revisión cross-provider, Git receipts, quality cascade, paralelismo opt-in y cap diario de coste.

## Prioridades vigentes

Completado en este bloque: fotografía y limpieza documental, canario deny → corrección → recuperación, conocimiento canónico compartido en `docs/ORCHESTRATION*.md`, benchmarks/evals SQL, retirada del `TaskBoard` huérfano, gate contra reejecuciones idénticas de Test Runner, gobernanza determinista de `solo_lead`, contrato durable de quorum, activación backend de perfiles y primer caso empírico medio/alto del selector. El quorum tiene ahora estados terminales absorbentes, auditoría por rutas vivas y provenance económica e2e determinista. El benchmark `sqlite_job_queue` dio 10/10 a `full_team` y 9/10 a `solo_lead`; también destapó el autocierre ausente de `test_designer` y un falso verde por shadowing de pytest, ambos corregidos.

Siguiente orden:

1. Ampliar la calibración empírica del selector con más semillas, casos medios reversibles y tareas complejas de otra naturaleza. Ya hay dos anclas: `cli_conversor` favorece `solo_lead`; `sqlite_job_queue` justifica `full_team` por calidad (10/10 frente a 9/10), aunque consume 1,84× tokens y 1,73× tiempo.
2. Crear un benchmark de calidad de planes para quorum, separado del benchmark de programación.
3. Verificar telemetría de usage en canales CLI no Codex antes de compararlos; esto completará la validación externa de costes del quorum.
4. Activar y evaluar resumen causal cuando el contexto exceda presupuesto.
5. Integrar el resumen de evals SQL en `loop-health` cuando se toque esa superficie.
6. Extraer piezas de `RunExecutor` solo de forma oportunista; actualmente concentra 7.059 líneas.

## Riesgos conocidos

- `RunExecutor` concentra muchas políticas; el orden de preflights y gates requiere tests dirigidos.
- El bloque principal quedó consolidado en `codex/orchestration-hardening`; `.claude/skills/aiteams-frontend/` permanece sin seguimiento y fuera de los commits por origen no atribuido.
- La telemetría de usage de CLIs no Codex, especialmente `gemini_subscription`, debe verificarse antes de comparar costes entre proveedores.
- El benchmark ya tiene resultados versionados y juez oculto aislado (harness v3); faltan más semillas y familias de tarea antes de extraer conclusiones estadísticas.
- Los documentos históricos de migración pueden contener estados de fase ya superados; el banner del documento indica cómo leerlos.
- Prompts externos o antiguos que mencionen `AITEAM_AUTO_QUORUM` están obsoletos: el único disparador vivo es el perfil explícito `lead_quorum`.
- Windows puede retener handles de SQLite o temporales de pytest.

## Verificación

Suite completa verificada el `2026-07-17`:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# 888 passed in 226.10s
```

Canario e2e:

```powershell
.\scripts\python_local.bat scripts\e2e_canary.py
.\scripts\python_local.bat scripts\e2e_quorum_canary.py
.\scripts\python_local.bat scripts\e2e_solo_lead_canary.py
```

Auditoría de un proyecto capa 2:

```powershell
.\scripts\python_local.bat scripts\audit_project_db.py "<workspace>"
```

No sustituir una ejecución actual por la cifra de este documento: registrar fecha y resultado cuando cambie sustancialmente la suite.
