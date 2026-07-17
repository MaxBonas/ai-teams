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

Completado en este bloque: fotografía y limpieza documental, canario deny → corrección → recuperación, conocimiento canónico compartido en `docs/ORCHESTRATION*.md`, benchmarks/evals SQL, retirada del `TaskBoard` huérfano, gate contra reejecuciones idénticas de Test Runner, gobernanza determinista de `solo_lead`, contrato durable de quorum, activación backend de perfiles y primer caso empírico medio/alto del selector. La matriz determinista del selector cubre ahora 28 fronteras en siete familias (28/28, sin falsos `solo_lead` ni sobreuso). Existe además un harness específico de calidad Plan A → Plan B para `lead_quorum`, con tres rúbricas ocultas y provenance económica. La primera ejecución real produjo Plan A pero quedó incompleta al no estar instalado Gemini CLI; ese intento descubrió y motivó correcciones de activación, contexto, contribuciones e identidad durable de proveedor.

Siguiente orden:

1. Ejecutar nuevas semillas reales del selector en casos medios reversibles y tareas complejas de otra naturaleza. Las 28 fronteras deterministas protegen la política, pero no sustituyen varianza de LLM.
2. Ejecutar tres semillas aceptadas de cada rúbrica `lead_quorum` con proveedores que respeten el contrato estructurado; Codex + Ollama ya validó diversidad, coste, degradación y UI, pero Qwen 32B no produjo un aporte válido.
3. Verificar `usage` real de Gemini subscription durante esas runs. La identidad provider/channel ya se resuelve desde el perfil durable en vez del descriptor CLI compartido.
4. Construir el eval semántico de pérdida de decisiones para el resumen causal. La activación durable por presupuesto (8.000 caracteres no sintetizados), los bloques incrementales y la sustitución en wake payload ya están implementados y cubiertos.
5. Consumir en frontend, cuando aporte valor, el nuevo campo aditivo `orchestrator_evals` de `GET /api/loop-health`; backend ya comparte economía, contexto, quorum y liveness con el harness SQL offline.
6. Extraer piezas de `RunExecutor` solo de forma oportunista; actualmente concentra 7.059 líneas.

## Riesgos conocidos

- `RunExecutor` concentra muchas políticas; el orden de preflights y gates requiere tests dirigidos.
- El bloque principal quedó consolidado en `codex/orchestration-hardening`; `.claude/skills/aiteams-frontend/` permanece sin seguimiento y fuera de los commits por origen no atribuido.
- La telemetría de usage de CLIs no Codex, especialmente `gemini_subscription`, debe verificarse antes de comparar costes entre proveedores. En esta máquina no hay claves API en el vault y el canal OAuth de Gemini no es elegible.
- Gemini CLI 0.51.0 quedó instalado, pero el OAuth existente es rechazado por Google con `UNSUPPORTED_CLIENT`/`IneligibleTierError` y exige migración a Antigravity; por tanto continúa sin ser un segundo proveedor utilizable para benchmarks.
- Nuevas anclas reales: `config_redactor` empata 3/3 pero `solo_lead` cuesta 4,68× tokens de entrada y 5,17× tiempo; `tenant_authorizer` favorece a Codex directo 4/5 frente a `full_team` 2/5. El default conservador no debe relajarse ni presentarse como calibrado con estas semillas.
- `benchmarks/results/quorum-sqlite-seed-1.json` es evidencia de una run incompleta, no un resultado A/B: Plan A obtuvo 91,3 % y el segundo auditor falló con `subscription_cli_not_found`.
- `benchmarks/results/quorum-provider-failover-local-seed-1.json` es una segunda evidencia incompleta pero útil: Plan A obtuvo 78,26 %, Codex aportó una auditoría válida y Qwen 32B consumió 4.100 tokens de entrada/164 de salida en dos intentos sin cumplir `AGENT-REPORT`; la sesión terminó `degraded` con escalado durable. El runtime reintenta una sola vez, excluye ese reintento del guard de evidencia idéntica y cancela wakeups sobrantes al degradar.
- `benchmarks/results/quorum-provider-failover-gemma-seed-1.json` confirma que Gemma 4 local tampoco es todavía un segundo auditor utilizable: Codex produjo el único aporte válido; Gemma terminó primero `skipped` y después `failed` por selección de herramienta. El runtime continúa ahora auditores `skipped`/`failed`, normaliza fallos declarados sin código a `agent_reported_failure` y degrada/escalada de forma durable al agotar el reintento. Es evidencia de failover, no una semilla A/B aceptada.
- El QuorumStepper fue comprobado contra esa SQLite real: distingue ahora `degraded` de “No requerido”, expone `1/2` aportes, gate pendiente, causa y provenance del aporte válido. Evidencia visual local en `output/playwright/quorum-stepper-degraded.png` (no versionada).
- El benchmark ya tiene resultados versionados y juez oculto aislado (harness v3); faltan más semillas y familias de tarea antes de extraer conclusiones estadísticas.
- La higiene local quedó endurecida después de encontrar 11,1 GB en `.pytest-workspace-tmp`: `pytest_local.bat` y el wrapper estable crean sesiones aisladas, limpian en un proceso posterior al cierre de handles SQLite, desactivan cache/bytecode y preservan el exit code de pytest. `scripts/cleanup_test_artifacts.py` permite el barrido manual.
- Los documentos históricos de migración pueden contener estados de fase ya superados; el banner del documento indica cómo leerlos.
- Prompts externos o antiguos que mencionen `AITEAM_AUTO_QUORUM` están obsoletos: el único disparador vivo es el perfil explícito `lead_quorum`.
- Windows puede retener handles de SQLite o temporales de pytest.

## Verificación

Suite completa verificada el `2026-07-17`:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# 910 passed in 313.44s
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
