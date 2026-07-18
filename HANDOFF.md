<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-07-17`

AI Teams ya no está en reconstrucción inicial. Es un control plane multiagente Paperclip-like funcional, centrado en SQLite, y se encuentra en fase de endurecimiento operativo, validación con proveedores reales y medición frente a un agente único.

El quorum profundo tiene defensa en profundidad: objetivo congelado frente a
Chat, nuevos objetivos mediante Nueva tarea y aceptación SQLite limitada a un
Plan B creado en la misma run por el Lead configurado.

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

Completado en este bloque: fotografia y limpieza documental, canario deny → correccion → recuperacion, conocimiento canonico compartido en `docs/ORCHESTRATION*.md`, benchmarks/evals SQL, retirada del `TaskBoard` huerfano, gate contra reejecuciones identicas de Test Runner, gobernanza determinista de `solo_lead`, contrato durable de quorum, activacion backend de perfiles y primer caso empirico medio/alto del selector. La matriz determinista del selector cubre ahora 28 fronteras en siete familias (28/28, sin falsos `solo_lead` ni sobreuso). Existe ademas un harness especifico de calidad Plan A → Plan B para `lead_quorum`, con tres rubricas ocultas y provenance economica. El intento inicial con Gemini CLI legacy quedo incompleto y descubrio huecos de activacion, contexto, contribuciones e identidad; el reemplazo Antigravity ya supera auth y contrato headless, pero aun no una run quorum real.

Siguiente orden:

1. Ejecutar nuevas semillas reales del selector en casos medios reversibles y tareas complejas de otra naturaleza. Las 28 fronteras deterministas protegen la política, pero no sustituyen varianza de LLM.
2. Repetir las familias del benchmark quorum con más semillas. `provider_failover` cerró tres semillas Codex + Anthropic (+4,35, −8,70 y +8,70); `sqlite_online_migration` añadió una aceptada Codex + Antigravity Flash (−8,69). La primera semilla `multitenant_authorization` degradó y reveló que Anthropic API truncaba el payload de quorum y Codex agotaba salida con auditorías sin límite. Tras el fix, dos sesiones aceptadas puntúan con la rúbrica v2 91,30→100 (+8,70) y 100→100 (0), media +4,35; ambas produjeron dos aportes válidos en primer intento. La tercera costó 27 céntimos API y tardó 258,5 s. La evidencia confirma operatividad, pero el techo de la rúbrica y la muestra pequeña impiden concluir consistencia estadística.
3. Mantener Anthropic API como segundo proveedor operativo: usage, coste y provenance ya estan verificados. Para Google, `gemini_api` y `antigravity_subscription` son canales distintos; el Gemini CLI legacy fue desinstalado y retirado del producto activo.
4. Vigilar la política ya calibrada del curador: `auth_migration` dejó Codex mini 0/3, `gpt-5.5` 2/2 y Anthropic Haiku 3/3; `queue_rollout` dejó Codex mini 3/3. Nuevos curadores Codex usan `gpt-5.5`, mientras otros proveedores conservan su modelo propio. Recovery, offsets y activación por zona cómoda están cerrados; ampliar familias solo como seguimiento, no como bloqueo del routing.
5. Consumir en frontend, cuando aporte valor, el nuevo campo aditivo `orchestrator_evals` de `GET /api/loop-health`; backend ya comparte economía, contexto, quorum y liveness con el harness SQL offline.
6. Extraer piezas de `RunExecutor` solo de forma oportunista; actualmente concentra 7.608 líneas.

## Riesgos conocidos

- `RunExecutor` concentra muchas políticas; el orden de preflights y gates requiere tests dirigidos.
- El gate profundo valida cobertura y presupuesto, no verdad ni calidad semántica. Debe seguir calibrándose con `benchmark_quorum_plans.py`; no elevar más thresholds basándose en una sola familia.
- El gate productivo de context curator verifica artefacto, provenance, rango y compresión, pero no conoce la rúbrica oculta de cada proyecto. Auth demuestra que tres síntesis mini pueden pasar el 30% y aun perder enlaces owner/aceptador; colas demuestra que ese fallo no es universal. No presentar compresión como retención semántica ni promover un modelo para todas las familias con esta muestra.
- La rúbrica `multitenant_authorization_v1` produjo un falso −8,69 en la tercera semilla: no reconocía equivalentes válidos como «frontera de enforcement», `policy checks por recurso`, `Deny-by-default` o «pruebas negativas». La v2 añade esas anclas y tests dirigidos; los resultados v1 y v2 se conservan separados. El 100→100 de v2 es efecto techo, no prueba de que Plan B sea idéntico ni de calidad perfecta.
- Anthropic API debe recibir `quorum_review` completo: su builder genérico resumía el payload a 800 caracteres y ocultaba el contrato. Las auditorías quedan acotadas a 1-3 findings para preservar profundidad sin agotar el cierre JSON/AGENT-REPORT; la semilla multitenant posterior verificó ambos proveedores al primer intento.
- Un quorum de un senior es una degradación de redundancia aceptada por disponibilidad, no equivalente empíricamente a dos proveedores. Exponerlo claramente en UI/telemetría si se usa con frecuencia.
- El bloque principal quedó consolidado en `codex/orchestration-hardening`; `.claude/skills/aiteams-frontend/` permanece sin seguimiento y fuera de los commits por origen no atribuido.
- La telemetria de usage de `antigravity_subscription` debe verificarse antes de comparar costes: `agy --print` autentica y responde, pero no entrega usage comparable en su salida normal.
- Antigravity CLI 1.1.4 es un segundo proveedor operativo para quorum: existe una sesión aceptada cross-provider y una contribución válida con Gemini 3.1 Pro High. El adapter transporta payloads largos mediante archivo temporal autorizado, conserva plan+sandbox y normaliza solo los envelopes observados. Sigue sin usage/cost_event comparable y el cumplimiento de `AGENT-REPORT` presenta varianza en ambos proveedores.
- El blueprint debe conservar el rol semántico `quorum_auditor` aunque la sub-issue sea `reviewer`; de lo contrario el selector baja erróneamente a Flash. Pro es el modelo canónico de hiring para Antigravity quorum.
- Nuevas anclas reales: `config_redactor` empata 3/3 pero `solo_lead` cuesta 4,68× tokens de entrada y 5,17× tiempo; `tenant_authorizer` favorece a Codex directo 4/5 frente a `full_team` 2/5. El default conservador no debe relajarse ni presentarse como calibrado con estas semillas.
- `benchmarks/results/quorum-sqlite-seed-1.json` es evidencia de una run incompleta, no un resultado A/B: Plan A obtuvo 91,3 % y el segundo auditor falló con `subscription_cli_not_found`.
- `benchmarks/results/quorum-provider-failover-local-seed-1.json` es una segunda evidencia incompleta pero útil: Plan A obtuvo 78,26 %, Codex aportó una auditoría válida y Qwen 32B consumió 4.100 tokens de entrada/164 de salida en dos intentos sin cumplir `AGENT-REPORT`; la sesión terminó `degraded` con escalado durable. El runtime reintenta una sola vez, excluye ese reintento del guard de evidencia idéntica y cancela wakeups sobrantes al degradar.
- `benchmarks/results/quorum-provider-failover-gemma-seed-1.json` confirma que Gemma 4 local tampoco es todavía un segundo auditor utilizable: Codex produjo el único aporte válido; Gemma terminó primero `skipped` y después `failed` por selección de herramienta. El runtime continúa ahora auditores `skipped`/`failed`, normaliza fallos declarados sin código a `agent_reported_failure` y degrada/escalada de forma durable al agotar el reintento. Es evidencia de failover, no una semilla A/B aceptada.
- Anthropic API ya es segundo proveedor operativo. `quorum-provider-failover-anthropic-seed-2.json` mejora 60,87→65,22 (+4,35); seed 3 regresa 86,96→78,26 (−8,70). Ambas sesiones terminaron `accepted`, con dos aportes provider-diversos y 14 céntimos atribuibles al auditor Anthropic. Seed 1 es un diagnóstico incompleto: el health ranking eligió Anthropic también como Lead y agotó sus 4.096 tokens antes de crear sesión.
- Seed 4 mejora 91,30→100 (+8,70), termina en cuatro runs sin intervención y atribuye 19 céntimos al auditor Anthropic. El selector por rol usa `claude-sonnet-4-5` si Anthropic ocupa el Lead principal y `claude-opus-4-5` para `quorum_auditor`; las tres runs reales de auditor confirmaron Opus 4.5.
- El apartado Equipo aprovisiona ahora Quorum Auditor 1/2 mediante un endpoint canónico idempotente, no mediante un prompt `full_team`; conserva los IDs que consume el runtime y oculta las tarjetas cuando ya están contratados.
- Corregido un hueco descubierto por seed 2: cuando Codex entregaba `AGENT-REPORT` dentro de `add_comment`, la contribución se persistía después del auto-wakeup y el gate quedaba `reviewing`. Cada contribución válida evalúa ahora inmediatamente la continuación durable.
- El bootstrap de quorum asigna ahora proveedores distintos por construcción cuando existen perfiles suficientes; antes ambos auditores elegían silenciosamente el mismo primer perfil senior y la diversidad solo fallaba al evaluar el gate.
- `benchmarks/context_quality/auth_migration_*` aporta el primer canario causal: la referencia conserva 9/9 anclas obligatorias con ratio 26,57 %. El primer intento (35,84 %) fue rechazado por presupuesto, confirmando que retención y compresión son gates independientes.
- El QuorumStepper fue comprobado contra esa SQLite real: distingue ahora `degraded` de “No requerido”, expone `1/2` aportes, gate pendiente, causa y provenance del aporte válido. Evidencia visual local en `output/playwright/quorum-stepper-degraded.png` (no versionada).
- El benchmark ya tiene resultados versionados y juez oculto aislado (harness v3); faltan más semillas y familias de tarea antes de extraer conclusiones estadísticas.
- La higiene local quedó endurecida después de encontrar 11,1 GB en `.pytest-workspace-tmp`: `pytest_local.bat` y el wrapper estable crean sesiones aisladas, limpian en un proceso posterior al cierre de handles SQLite, desactivan cache/bytecode y preservan el exit code de pytest. `scripts/cleanup_test_artifacts.py` permite el barrido manual.
- Los documentos históricos de migración pueden contener estados de fase ya superados; el banner del documento indica cómo leerlos.
- Prompts externos o antiguos que mencionen `AITEAM_AUTO_QUORUM` están obsoletos: el único disparador vivo es el perfil explícito `lead_quorum`.
- Windows puede retener handles de SQLite o temporales de pytest.

## Verificación

Suite completa verificada el `2026-07-18`:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# 959 passed in 112.62s
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
