<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-07-30`

AI Teams ya no está en reconstrucción inicial. Es un control plane multiagente Paperclip-like funcional, centrado en SQLite, y se encuentra en fase de endurecimiento operativo, validación con proveedores reales y medición frente a un agente único.

El quorum profundo tiene defensa en profundidad: objetivo congelado frente a
Chat, nuevos objetivos mediante Nueva tarea y aceptación SQLite limitada a un
Plan B creado en la misma run por el Lead configurado.

La cobertura Tier 1 distingue ya `lead_ready` de `quorum_ready` sin crear
subtiers ni rebajar calidad. El recibo
`benchmarks/results/model_evaluation_coverage/model-tier-coverage-2026-07-24.json`
cuenta únicamente pares automáticos, ejecutables, no archivados y calibrados.
Gemini 3.1 Pro High queda revalidado en Antigravity 1.1.8 por dos contratos
independientes: Lead 6/6 y quorum 6/6, dos familias por tres semillas. Cada
agregado sella CLI 1.1.8, prompt v2, fuentes y hashes sin heredar evidencia
desde QA ni el otro carril. `lead_ready` y `quorum_ready` vuelven a `covered`
2/2 con Sol/Codex, dos perspectivas y dos pools.

P0.g queda cerrado el `2026-07-30`. El sistema publica ahora una ruta canónica
por perfil+modelo+rol con siete gates ordenados: configuración/auth,
catálogo+versión, health, probe de contrato, canario, calibración multi-familia
y promoción. Backend, API y pestaña Modelos comparten la proyección
`model_calibration_gate_board_v1`; React no decide gates. La auditoría viva
recorre 98 candidatos/1.666 celdas y pasa 10/10 invariantes: ningún adapter rojo
abre una acción avanzada, un verde sin prueba se detiene antes del canario,
evidencia antigua queda histórica y una promoción exige siete `passed`.
El recibo redacted es
`benchmarks/results/model_catalog_read_model/model-calibration-gate-board-2026-07-30.json`.
Verificación: 22 tests del bloque, 104 tests integrados, suite global 1.764
passed / 2 skipped, TypeScript, ESLint, Stylelint y 3/3 E2E. Permanece un solo
warning conocido de deprecación Starlette/httpx. La suite global tarda hoy
805,24 s en esta máquina; usar un timeout superior a cinco minutos. P0.f queda
dividido en cuatro unidades tras observar 51 identidades todavía `normal`;
ninguna es accionable hoy, pero esa seguridad circunstancial no equivale a la
directiva durable `low`. P0.f.1 queda cerrado con
`model-residual-policy-inventory-2026-07-30.json`: 98 identidades visibles,
47 explícitas y 51 pendientes —39 Gemini Free y 12 Groq—. Diez slugs aparecen
en más de un perfil y permanecen separados. Los ocho invariantes pasan y las
867 filas pendientes tienen cero acciones, permisos proactivos o promociones;
aun así el recibo conserva `policy_complete=false`. P0.f.2 y P0.f.3 quedan
también cerrados. La reconciliación local añadió 51
entradas `low` en una sola escritura, preservó literalmente las 47 anteriores
y fue idempotente: 98 preferencias explícitas —13 `high`, 79 `low`, 6
archivadas—. El recibo es
`model-residual-preference-reconcile-2026-07-30.json`. Además, cualquier modelo
nuevo `source=default` queda `owner_unclassified` aunque sea normal y nominado:
no abre backlog ni inferencia hasta clasificación explícita. Verificación:
76 tests de política y 49 de selección/API/defaults. Siguiente unidad P0.f.4,
paridad transversal y cierre. P0.d/Groq, P0.e/OpenCode y P0.h.2d.5b
permanecen correctamente event-gated.

Se añade P0.N al plan: inteligencia durable de cambios de proveedores. Cubre
releases y compatibilidad de CLI, servidores MCP, SDK/API, adapters internos y
diffs autenticados del catálogo/model metadata. Se divide en contrato/fuentes,
detectores semánticos, persistencia+scheduler, workflow con rollback,
inbox/avisos al developer y aceptación portable. Detectar una versión nueva no
autoriza actualizar: toda mutación, credencial, canario o promoción requiere
aprobación; offline/auth/rate-limit quedan `unknown`. P0.f.4 continúa siendo la
siguiente unidad inmediata y P0.N entra después en el orden P0.

P0.f queda cerrado de extremo a extremo con
`model-residual-policy-parity-2026-07-30.json`: 10/10 invariantes, 98
identidades explícitas, 1.666 celdas en paridad, backlog residual cero y 1.568
decisiones automáticas sin fallos. API/UI, hiring/defaults/fallback,
reactivación y asignaciones existentes comparten la política; la auditoría no
muta estado ni ejecuta inferencias. Verificación: 76 tests transversales, 3 del
auditor y Ruff.

Se añade además P0.K: rediseño del primer uso y Nuevo proyecto como asistente
guiado, adaptativo, persistido y reanudable. Se divide en contrato/state
machine, entrevista de necesidades, preparación de adapters, cobertura
progresiva, proyecto/equipo Lead-first, preflight, diseño accesible e
integración/aceptación para instalaciones nuevas o actualizadas. El objetivo es
configurar el máximo de canales realmente útiles con consentimiento, no
instalarlo todo: local runtimes y CLIs no elegidos siguen opcionales. El
preflight reutiliza doctor, catálogo, health, probes y selector canónico y se
adapta a `software/research/operations/mixed` para no crear bucles de tests en
proyectos teóricos. P0.K.1 es ahora la siguiente unidad inmediata y P0.K entra
antes de P0.N en el orden de ejecución.

P0.K.1 queda cerrado con `guided_setup_v1`. La máquina de estados durable
publica 6 pasos de onboarding de máquina, 7 de proyecto y 4 de reparación,
persistidos en SQLite global de configuración para funcionar antes de elegir
workspace. Create/resume es idempotente; dependencias, revisión optimista,
drafts, blocked→resume y reset confirmado fallan cerrados. `secret_ref` es
válido, pero valores de claves/password/tokens se rechazan. API
contract/create/get/transition/reset conectada. Recibo 10/10:
`benchmarks/results/guided_setup/guided-setup-contract-2026-07-30.json`;
15 tests y Ruff verdes.

P0.K.2 queda cerrado con `guided_setup_needs_v1`: entrevista adaptativa de 12
preguntas, recomendaciones explicadas, `unknown` explícito y drafts
reanudables. Clasifica el caso de estudio de empresa de limpieza como
`research` pendiente de confirmación, recomienda el perfil y canales sin
activar local salvo opt-in y sella el assessment. SQLite lo recalcula antes de
completar el paso para impedir bypass o manipulación. API autenticada conectada;
recibo `guided-setup-needs-2026-07-30.json` 10/10, 25 tests focalizados y Ruff
verdes. Siguiente unidad: P0.K.3.

P0.K.3 se dividió en cinco cierres manejables. K.3.1 ya está verde:
`guided_setup_preparation_v1` proyecta entrevista+doctor en requisitos y seis
fases fail-closed; instalación no equivale a auth/catálogo/health/contrato,
Lead exige evidencia completa y local solo aparece tras opt-in. No muta la
máquina. Verificación transversal: 36 tests y Ruff. Siguiente unidad:
P0.K.3.2, API durable y persistencia de evidencia.

P0.K.3.2 queda cerrado. La API autenticada genera doctor y plan en servidor y
solo recibe `expected_revision`; inventarios o `provider_evidence` del cliente
son inválidos. `guided_setup_preparation_receipts` guarda hashes, readiness y
bloqueadores con FK al paso. Un recibo no listo bloquea completion y el último
recibo server-side reemplaza evidencia forjada. Auditoría 10/10 en
`guided-setup-preparation-persistence-2026-07-30.json`; 23 tests focalizados y
Ruff verdes. Siguiente unidad: P0.K.3.3, guías por proveedor y acciones
humanas.

P0.K.3.3 queda cerrado con `guided_setup_provider_guidance_v1`. La respuesta de
preparación incluye acciones manuales para Codex, Antigravity, OpenCode y API
personal, limitadas a los canales elegidos. Expone versiones, riesgos, login y
evidencia esperada; no ejecuta comandos y completar una acción no vuelve verde
el adapter. OpenCode declara key personal/oferta temporal/política de datos; la
API usa el endpoint de secretos y conserva solo `secret_ref`; local sigue
opt-in. Auditoría `guided-setup-provider-guidance-2026-07-30.json` 10/10, 17
tests y Ruff verdes. Siguiente unidad: P0.K.3.4.

P0.K.3.4 queda cerrado con `guided_setup_provider_evidence_v1`. API y doctor
comparten la misma carga redacted de perfiles; auth, catálogo, health y
contrato se proyectan por separado. Discovery/run completada no equivale a
contrato: JSON requiere recibo relativo, ≤30 días y versión CLI exacta;
health/auth y catálogo API persistido exigen ≤24 h. Stale, mismatch, falta de
recibo o ruta insegura quedan `not_checked`. Los probes remotos siguen
manuales, con confirmación de cuota, y no se ejecutaron. Auditoría
`guided-setup-provider-evidence-2026-07-30.json` 10/10, 23 tests y Ruff verdes.
Siguiente unidad: P0.K.3.5.

P0.K.3.5 y, con él, P0.K.3 quedan cerrados. El owner puede seleccionar IDs de
perfiles API existentes; el servidor valida que sean API y conserva autoridad
sobre catálogo/health/contract. Las APIs ligan probe a versión de transporte y
las suscripciones a CLI. La aceptación
`guided-setup-adapter-repair-acceptance-2026-07-30.json` pasa 10/10: limpia,
parcial, CLI antiguo, auth ausente, catálogo incompatible, API válida,
offline/rate-limit, opt-in local, frontera cliente y resume durable sin
reinstalar. 24 tests focalizados y Ruff verdes; no hubo comandos, logins,
inferencia o cuota. Siguiente unidad: P0.K.4.

P0.K.4 se divide en contrato, API canónica, recomendaciones, visualización y
aceptación. K.4.1 queda cerrado con `guided_setup_coverage_v1`: cobertura solo
por pares auto-elegibles del selector; Lead obligatorio, quorum con dos
perspectivas/pools y full team Lead+Engineer+Reviewer. Filtra adapters no
preparados y expone score/gates/privacidad/capacidades/economía; local y
suscripción son coste marginal cero. Auditoría
`guided-setup-coverage-contract-2026-07-30.json` 10/10, 33 tests y Ruff verdes.
K.4.2 queda también cerrado: el endpoint autenticado `/coverage` comparte la
reconstrucción server-side de preparación, usa el read model una vez y llama al
selector contextual para los cuatro roles de equipo y un Worker informativo.
Filtra perfiles no
preparados y devuelve hash/contexto explicable. Una prueba conserva idénticos
la revisión y `adapter_setup`, rechaza revisión stale y evidencia del cliente,
y confirma que no se crean proyectos ni cambian defaults. 10 tests focalizados
y Ruff verdes. K.4.3 queda cerrado con recomendaciones progresivas: un único
camino Lead con alternativas, quorum/full team según perfil y Worker económico
opcional al final; nunca reinstala un adapter verde ni cambia defaults.
Auditoría `guided-setup-recommendations-2026-07-30.json` 10/10, 17 tests y Ruff
verdes. K.4.4 queda cerrado con el componente `GuidedSetupCoverage`: perfiles,
acción siguiente y matriz de roles con candidatos elegibles y bloqueados
separados, score/gates/economía/privacidad/capacidades y causas server-side.
Corrige la proyección para devolver `excluded_candidates`, que antes solo
contaba alternativas bloqueadas aunque el contrato exigía mantenerlas visibles.
Pasa 2 tests React, 19 backend del delta, tipos, linters, límites, build y
presupuesto de bundle. K.7 lo insertará en el shell completo del wizard.
K.4.5 y el bloque K.4 quedan cerrados con
`guided-setup-coverage-acceptance-2026-07-30.json`, 10/10: sin Lead, Lead
único, quorum sin diversidad, full team parcial/completo, local gratuito, API
limitada, override manual, paridad con selector+adapter preparado e inputs
inmutables. No instaló, leyó secretos, creó proyectos, infirió, consumió cuota
ni cambió defaults. Siguiente unidad: P0.K.5, configuración de proyecto y equipo
Lead-first.

P0.K.5 queda dividido en cinco cierres. K.5.1 y K.5.2 ya están cerrados.
`guided_setup_project_proposal_v1` genera un preview Lead-first sellado con
identidad create/import, objetivo, ecosistemas, `.aiteam/instructions.md`,
perfil, asignaciones, diversidad, presupuesto, accountability, degradaciones y
save gate. Overrides manuales no saltan compatibilidad/preparación ni se
convierten en cobertura. La API `/project-proposal` reconstruye todo server-side,
confina paths a `projects_root` y no crea carpetas, DB, agentes o wakeups.
Revisión stale y payloads forjados se rechazan. Verificación: 61 tests, Ruff y
diff check verdes.

P0.K.5.3 queda cerrado con `/project-commit`. El endpoint recompone el preview
server-side y compara su hash antes de mutar; no acepta propuesta, inventario ni
evidence del cliente. Create se prepara en un sibling temporal e import solo en
`.aiteam-staging-*`; el publish es rename y el rollback borra únicamente lo
creado por la operación. El nuevo camino no llama al selector durante el
guardado: persiste exactamente perfil/modelo/candidato del preview. Objetivo,
intake, Lead primero, equipo, blueprint, assignments y el único wakeup se crean
en una transacción SQLite mientras la DB sigue invisible en staging. El recibo
global único por sesión da replay idempotente y detecta receipt stale. Create
mantiene Git gestionado opcional; import preserva contenido e historia ajenos.
Verificación específica y transversal: 68 tests guided-setup, Ruff y diff check
verdes.

P0.K.5.4 queda cerrado con un wizard canónico de cuatro etapas y revisión
sellada. Create/import, objetivo, instrucciones, perfil y adapters preparados
se convierten en sesión+needs server-side; la mesa final muestra el equipo
Lead-first exacto, modelos/canales, scores, gates, tier, economía, presupuesto,
ecosistemas, degradaciones y hash. Override invalida el preview y Guardar exige
`save_gate.allowed`. El frontend dejó de invocar `/api/projects/new`; su panel
manual queda plegado solo como diagnóstico y la acción legacy está retirada.
También se corrigieron el falso onboarding durante bootstrap y la carrera que
vaciaba `projects_root`. Validación: 12 tests React, build, ESLint, Stylelint,
límites de módulo/bundle y Playwright real desktop+móvil sin errores. Siguiente
unidad completada: P0.K.5.5, auditoría y aceptación end-to-end del bloque.

P0.K.5.5 y el padre P0.K.5 quedan cerrados. El auditor
`guided_setup_project_acceptance_v1` ejecuta 13 invariantes con filesystem
temporal y SQLite real: create/import, preservación de archivos ajenos,
confinamiento/colisión, detección truncada, cobertura, diversidad, overrides,
revisión stale, rollback y reanudación/idempotencia. El fixture de estudio de
empresa de limpieza persiste `objective_kind=research`, contrata únicamente al
Lead, crea una sola wakeup y no materializa roles de programación o tests. El
recibo con hash anti-manipulación está en
`benchmarks/results/guided_setup/guided-setup-project-acceptance-2026-07-30.json`.
Verificación: 13/13 checks, 89 tests guided-setup, 3 tests propios del auditor,
Ruff y test unitario del wizard verdes. No se tocaron proyectos/configuración
del usuario ni se usaron secretos, inferencia o cuota. La siguiente unidad
inmediata es P0.K.6, preflight y prueba proporcional antes de entrar al
proyecto.

P0.K.6 ya está dividido en cinco cierres y K.6.1 queda completado. El contrato
`guided_setup_project_preflight_v1` recompone de forma pura seis gates:
propuesta, ruta, runtimes, adapters seleccionados, toolchains y fixture
proporcional. Research/operations no ejecutan tests; software exige smoke;
mixed solo lo exige con superficie software detectada. Discovery no concede
readiness, la ruta no confinada falla cerrada y ningún receipt puede ocultar
remote calls, cuota o mutación. La salida mantiene
`enter_project_allowed=false` hasta persistencia y el validador recalcula
resumen/hash para detectar manipulación. El auditor durable pasa 10/10 checks
en `benchmarks/results/guided_setup/guided-setup-project-preflight-contract-2026-07-30.json`;
17 tests focales, 106 guided-setup y Ruff están verdes. Siguiente unidad:
P0.K.6.2, endpoint server-side que recomponga propuesta por revisión/hash,
observe ruta e inventario de forma confinada y no confíe en evidence del
navegador.

P0.K.6.2 queda completado. El nuevo
`POST /api/guided-setup/sessions/{id}/project-preflight` recompone todos los
inputs desde la sesión y exige revisión+proposal hash exactos. Ruta y permisos
se observan en servidor; doctor recibe la raíz objetivo para que imports usen
sus manifests reales. La propia `projects_root` se rechaza como proyecto.
Pydantic prohíbe inventario/path/evidence inline y solo admite referencias
SHA-256 deduplicadas; mientras no exista el store de K.6.3/K.6.4, cualquier
referencia ausente falla explícitamente. El endpoint distingue la composición
pura de los probes read-only de versión/puertos del doctor y no ejecuta tests,
inferencia, probes remotos ni cuota. La matriz cubre software no-go, research
go sin tests, spoofing 422, stale 409 y create/import/root; 23 tests focales,
106 guided-setup y Ruff verdes. Siguiente unidad inmediata: P0.K.6.3, executor
acotado y consentido para el probe/fixture proporcional exacto.

P0.K.6.3 queda cerrado. `guided_setup_project_preflight_execution_plan_v1`
sella como máximo un fixture local y un probe adapter+modelo exacto, en orden
económico, un intento y consentimientos separados. El runner local reutiliza
fixtures allowlisted en copia temporal con timeout/redacción/cleanup y produce
evidence SHA-256 sin tocar el proyecto; `python_pytest` fue ejecutado realmente
y pasó. El probe exacto valida perfil/modelo/structured output/timeout antes de
credenciales, exige doble consentimiento remoto+cuota y devuelve solo código,
tokens/coste y flags redacted; no se ejecutó contra ningún proveedor en esta
run. `project-preflight-execute` recompone y compara proposal, preflight y plan,
valida toda autorización antes del primer runner y devuelve receipt+preflight
posterior sin persistir sesión, DB, health, catálogo, defaults o workspace.
Verificación: 30 pruebas focales, 129 guided-setup y Ruff verdes. Siguiente:
P0.K.6.4, persistencia durable/idempotente, invalidación stale y bloqueo de
`project-commit` hasta el último preflight `go`; solo entonces podrá crearse la
wakeup y entrar al cockpit.

P0.K.6.4 queda cerrado. El endpoint de ejecución persiste ahora
`guided_setup_project_preflight_receipt_v1` y artifacts SHA-256 confinados a la
sesión; repetir cualquier plan exacto devuelve su intento anterior sin volver
a ejecutar fixture/probe. El resolver rehashea contenido y evidencia
normalizada, rechaza referencias de otra sesión y falla cerrado ante corrupción.
`project-commit` exige el último receipt `go` y recompone todos los inputs
server-side antes de materializar: ausencia/no-go, proposal o path/doctor/
adapter/toolchain stale y evidence manipulada bloquean antes de crear target,
SQLite de proyecto, agentes o wakeup. El auditor hermético pasa 6/6 en
`benchmarks/results/guided_setup/guided-setup-project-preflight-persistence-2026-07-30.json`;
54 pruebas focales, 132 guided-setup y Ruff están verdes, sin inferencia, red
ni cuota. Siguiente
unidad inmediata: P0.K.6.5, UI del preflight durable y aceptación end-to-end.

P0.K.6.5 y el padre P0.K.6 quedan cerrados. El wizard carga el preflight
server-side tras sellar la propuesta y `ProjectPreflightPanel` separa
consentimiento de fixture local, probe remoto y cuota. Research no presenta
tests; blocked/no-go no presenta ejecución o entrada, y solo un receipt durable
`go` con hash vigente muestra “Entrar al proyecto”. Los 409 stale invalidan el
preview; offline/429 preservan diagnóstico. El E2E Chromium real pasa sin
errores ni overflow y el auditor 10/10 queda en
`benchmarks/results/guided_setup/guided-setup-project-preflight-ui-acceptance-2026-07-30.json`.
Validación: 20 unitarios frontend, build, linters, typecheck, límites,
1 E2E, 135 tests guided-setup, 3 tests de auditor y Ruff verdes; cero llamada a
proveedor, inferencia o cuota. Siguiente unidad: P0.K.7, diseño visual y
accesibilidad integral del asistente.

P0.K.7 se dividió en cinco cierres y K.7.1 queda completado.
`ProjectSetupProgress` muestra y anuncia posición/estado sin depender del
color; solo permite volver a pasos completados. `useWizardStageFocus` no roba
el autofocus inicial y enfoca la región etiquetada al avanzar, volver, generar
la propuesta o invalidar el preflight. La acción principal referencia su
condición de readiness y los errores describen el paso activo. Pasan 11 tests
focales, typecheck, ESLint, límites, build/bundle y E2E Chromium con foco real,
sin errores ni overflow. Siguiente unidad: P0.K.7.2, teclado, foco visible y
errores próximos al campo.

P0.K.7.2 queda cerrado. La acción primaria solo se deshabilita durante `busy`;
Enter valida y enfoca el primer error adyacente con `aria-invalid` y
`aria-describedby`. Modo, perfil y adapters publican `aria-pressed`; la región,
inputs, selects y botones tienen foco visible. Un 409 stale retorna con foco a
Recursos y el no-go conserva su revisión explícita. `ProjectIdentityStep` y
`invalidProjectStepControls` mantienen módulos y orden de corrección acotados.
Pasan 16 tests focales, 26 unitarios frontend, E2E Chromium con teclado,
typecheck, linters, módulos, build y bundle. Siguiente: K.7.3, empezando por
recuperar presupuesto sin elevar límites; quedan 321 B JS y 136 B CSS.

P0.K.7.3 queda cerrado. El configurador legacy duplicado ya no se monta y el
minificador lo excluye del bundle; K.8 debe borrar físicamente la fuente y su
estado. El wizard usa mapa móvil 2×2, protocolo vertical y preflight responsive.
El E2E prueba desktop, 768, 390 y reflow a 320 CSS px, acciones/readiness
visibles, cero overflow, contraste AA sin violaciones, foco de teclado ≥3:1 y
reduced motion ≤0,011 ms; se corrigió el ordinal de manifiesto que daba 2,74:1.
Capturas inspeccionadas. Pasan 26 unitarios, typecheck, linters, límites, build,
bundle y E2E. Bundle final sin elevar límites: JS 400.925/116.129 B raw/gzip;
CSS 122.316/22.057 B. Siguiente: P0.K.7.4, lectores de pantalla y auditoría
automática completa.

P0.K.7.4 queda cerrado con Axe WCAG 2 A/AA en seis estados del flujo: Proyecto
y Objetivo limpios/con error, Recursos y Revisión+preflight pendiente. Resultado:
cero violaciones de cualquier impacto. El E2E prueba `main`, navegación,
región activa, headings sin saltos, consentimiento nombrado y lista ordenada
del protocolo. Se corrigieron un hover blanco/verde de 2,62:1 y el anuncio
excesivo de todo el preflight: solo el sello durable mantiene
`aria-live=polite` atómico. Pasan 26 unitarios, tipos, linters, límites, build,
bundle y E2E. Bundle: JS 400.942/116.144 B raw/gzip; CSS 122.514/22.108 B.
Siguiente: P0.K.7.5, matriz visual y durable pending/GO/NO-GO con hashes y
auditoría anti-manipulación.

P0.K.7.5 y P0.K.7 quedan cerrados. El E2E recorre los cuatro pasos y la
secuencia `pending → NO-GO → Recursos → nueva propuesta/preflight → GO`.
`preflightExecutionAuthorizesCommit` comparte el guard de panel y commit:
preflight posterior, plan y execution receipt deben enlazar exactamente con el
durable GO. Se corrigieron la proyección del preflight previo tras ejecutar, la
cadena incompleta y un hover de paso completado a 4,41:1. Un GO inconsistente
falla cerrado y vuelve a Recursos.

El receipt
`guided-setup-project-visual-acceptance-2026-07-30.json` pasa 10/10 con seis
PNG ligados a viewport y hashes de autoridad; el auditor reabre los binarios y
rechaza imagen, matriz, cadena o informe manipulados. Dos repeticiones conservan
visual hash `810fc439…45cdba5` y report hash `3cce823b…0072a53`; auditor y
tests quedan también sellados. Pasan 27
unitarios frontend, 38 backend/auditor focales, Axe NO-GO/GO, tipos, linters,
Ruff, límites, build, bundle y E2E. Bundle: JS 401.517/116.261 B raw/gzip; CSS
122.564/22.115 B. Siguiente: P0.K.8, integración, actualización y aceptación
portable del asistente.

Esta separación es ahora un P0 transversal, no un cierre local del auditor de
cobertura. `task.md` P0.h.4 exige integrarla en read model y migración, scoring
como hard gate externo, API/Catálogo/UI y todos los consumidores de selección y
lifecycle. El criterio de cierre prohíbe inferir autoridad desde tier,
`best_for`, nombre o score y exige tests negativos de no-escalado
`quorum_ready`→Lead y `lead_ready`→auditor sin calibración exacta.

P0.h.4a está cerrado: `model_catalog_read_model_v2` proyecta y hashea
`tier1_authority` por rol exacto; selección contextual y snapshots nuevos lo
transportan. Los snapshots legacy siguen legibles, pero una autoaplicación
Tier 1 sin gate versionado se rechaza. El recibo compacto
`model-catalog-read-model-2026-07-24-tier1-authority-v2.json` audita 98
identidades/1.666 celdas, seis habilitaciones, cero candidatos automáticos y
cero fallos. P0.h.4b queda también cerrado: Lead y quorum publican contratos de calibración
versionados con constructos distintos; el auditor impide que la autoridad se
mezcle en `model_role_score_v2`. Un test cubre score alto con calibración Lead
parcial y confirma que continúa bloqueado. Verificación conjunta: 84 tests,
lint dirigido y auditor vivo verdes. P0.h.4c queda cerrado: API y Catálogo
publican `tier1_coverage`, filtran por autoridad y muestran badges, huecos de
cobertura y el contrato/bloqueo exacto sin recalcularlo en React. Su cierre
añade 34 tests backend dirigidos, 7 unitarios frontend y 3 E2E, además de tipos,
linters, límites y build verdes. P0.h.4d queda también cerrado: selector,
override del owner, onboarding, Equipo/hiring, defaults, quorum, fallback y
reconcile exigen el carril exacto; el executor revalida antes del LLM y
persiste bloqueo, interacción y deny para asignaciones legacy, stale o
manipuladas, cubriendo dispatch, retry, recovery y liveness. Verificación:
executor 118/118, 176 tests dirigidos de consumidores y suite backend
1688/1688. P0.h.4e y el contrato padre quedan cerrados con
`tier1_authority_parity_audit_v1`: 490 celdas read-model/API, 235 decisiones
activas, 20 decisiones de snapshot, cinco invariantes frontend y cuatro ataques
negativos, sin divergencias. El recibo durable es
`benchmarks/results/model_catalog_read_model/tier1-authority-parity-2026-07-24.json`.
Verificación final: backend 1692/1692, frontend 8/8, Ruff, ESLint, typecheck,
build y diff check verdes.

P0.h.3 queda cerrado. `candidate_is_automation_eligible` exige el score
contextual automático completo —incluidas calibración y frescura— para
defaults, hiring/reconcile, fallback, escalado y recovery; `owner_selectable`
se reserva a decisiones manuales explícitas y no concede cobertura. Shadow y
recommend dejan la plaza sin resolver si el selector legacy no supera el gate.
El fallback de proveedor por `adapter_type` aislado queda denegado y remite al
recovery gobernado. El recibo
`model-automation-enforcement-2026-07-24.json` pasa 1.568 celdas, 1.560 gates
rojos sin bypass, wiring 5/5 y matriz hermética 4/4. Verificación final:
1697 tests backend.

P0.h.2b queda cerrado: Gemini Pro High/Lead pasa 6/6 en CLI 1.1.6 con
schema exacto, 12 anclas en auth y 9 en queue, fuentes y respuestas hasheadas.
El harness rechaza ahora agregados con versión CLI ausente o mezclada.
`lead_ready` pasa a 2/2; P0.h.2c queda satisfecho por diversidad real
Codex/Antigravity. Verificación: 64 tests dirigidos, 1698 backend y los
auditores de read model, paridad y enforcement verdes. Siguiente unidad:
P0.h.2d.1 queda cerrado: Terra/Reviewer pasa 3/3 ciclos durables en Codex
`0.146.0-alpha.6`, con seis llamadas, 12 runs, versión única y fuentes
hasheadas. El agregado v2 y el validador fallan ante versión ausente, mezclada
o manipulada. Reviewer Tier 2 queda 2/2 con Gemini API Free, dos perspectivas
y dos pools, sin cambiar defaults. P0.h.2d continúa dividido por rol.
Read model conserva cero candidatos automáticos y enforcement cero defaults o
fallbacks; cierre verificado con 39 tests focalizados y 1.700 backend
(un skip de toolchain).

P0.h.2d.2 queda cerrado como resultado negativo válido, no como cobertura:
Terra/Engineer en Codex `0.146.0-alpha.6` pasa 9/9 tests ocultos de
`cli_conversor`, pero falla el gate Ruff con dos incidencias y detiene las otras
cinco ejecuciones. Sonnet/Engineer conserva su screening actual 1.1.6 (3/3
ocultos, siete incidencias Ruff), así que no se consume otro run idéntico.
Ambos diagnósticos validan profundamente recibo, identidad, versión, familia y
score, prevalecen sobre evidencia histórica stale y quedan
`deferred_until_material_change`. Engineer permanece 0/2. Verificación final:
103 tests transversales y 1.702 backend
verdes, con un skip de toolchain; read model y enforcement conservan cero
candidatos automáticos, defaults y fallbacks.

P0.h.2d.3 queda cerrado. Terra/QA en Codex `0.146.0-alpha.6` y Flash High/QA
en Antigravity `1.1.8` completan dos familias por tres semillas cada uno:
6/6 muestras y 66/66 gates por modelo. El contrato familiar v4 corrige la
ambigüedad `tenant`/`tenant_id` y separa la inferencia QA de un test runner
determinista que persiste la prueba post-fix; así, un venv inaccesible desde el
sandbox del proveedor no produce un falso diagnóstico de calidad. El agregado
v5 exige versión única y valida profundamente las seis muestras por hash.
QA Tier 2 queda 2/2 con perspectivas OpenAI/Google y pools Codex/Antigravity,
sin habilitar defaults. Los intentos v3 permanecen como diagnóstico y no se
contabilizan. Recibos:
`p0h2d3-terra-qa-diversity-v5-cli-0.146.0.json` y
`p0h2d3-flash-high-qa-diversity-v5-cli-1.1.8.json`. Verificación: 245 tests
transversales, 1.704 backend con dos skips de toolchain, 18 recibos JSON
válidos sin patrones de secretos, Ruff F/I del bloque y diff check verdes.
La regresión corrigió únicamente el snapshot temporal del 23/07, que no puede
consumir evidencia QA creada el 29/07; no se relajó el gate productivo.

P0.h.2c.1 queda cerrado. El harness crítico de Antigravity incorpora
`--sandbox --mode plan` y una prueba de la línea de comando; las matrices
Gemini Pro High/Lead y `quorum_auditor` completan 12/12 muestras sin retries.
Los dos agregados son version-bound, enlazan las fuentes por hash y mantienen
`default_change_allowed=false`. El recibo vivo
`model-tier-coverage-2026-07-30-tier1-restored.json` registra Lead y quorum
2/2; QA y Reviewer permanecen 2/2. Read model, enforcement y paridad pasan con
cero divergencias, defaults o fallbacks automáticos. La próxima unidad es
P0.h.2d.4 Test Designer. Verificación del cierre: 320 tests transversales,
1.706 backend con dos skips de toolchain, 14 recibos JSON válidos sin patrones
de secretos, Ruff F/I del bloque y diff check verdes.

P0.h.2d.4 queda cerrado. Terra/Test Designer en Codex `0.146.0-alpha.6` y
Gemini 3.5 Flash High/Test Designer en Antigravity `1.1.8` completan dos
familias por tres semillas: 6/6 muestras, 48/48 gates y 30/30 mutantes por
modelo. Los agregados `independent_test_designer_two_family_v3` ligan versión,
familias y fuentes por hash y mantienen `default_change_allowed=false`.
El runner observa la versión antes de inferir, usa un Lead Sol fijo para aislar
el rol y ejecuta baseline/mutantes fuera del sandbox del proveedor. Un primer
Terra seed 2 quedó como diagnóstico: la suite y los 5/5 mutantes pasaron, pero
las claves traducidas del `AGENT-REPORT` no eran válidas. El contrato se hizo
literal y el executor exige ahora reporte durable también a `test_designer`,
por lo que no puede cerrar silenciosamente; el nuevo intento pasó 8/8.
El auditor baja a las doce muestras y comprueba hash, identidad, versión,
familia, baseline, cinco mutantes, artefacto único y reporte. El recibo
`model-tier-coverage-2026-07-30-tier2-test-designer.json` deja el rol
`covered` 2/2 con perspectivas OpenAI/Google y pools Codex/Antigravity. Read
model y enforcement conservan cero candidatos automáticos, defaults y
fallbacks. Verificación final: 183 tests transversales y 1.712 backend pasan,
con dos skips de toolchain; 23 recibos nuevos son JSON válidos sin patrones de
secretos, Ruff F/I del bloque y diff check quedan verdes. El único warning es
la deprecación Starlette/httpx ya existente.

P0.h.2d.5a queda cerrado y P0.h.2d.5b permanece condicionado. Terra/MCP
Operator en Codex `0.146.0-alpha.6` completa dos familias por tres semillas:
6/6 muestras, 72/72 gates y single-attempt. Los gates demuestran fallo de
versión y recovery activo, allow read/deny write, una llamada MCP permitida
real, ninguna write y reporte durable. El agregado liga contrato, versión y
hashes hasta cada muestra; el auditor rechaza cualquier gate MCP manipulado.
`model-tier-coverage-2026-07-30-tier2-mcp-operator.json` deja el rol
`single_point` 1/2 con perspectiva OpenAI y pool Codex, sin defaults ni
fallbacks nuevos. Sol comparte perspectiva/pool y no rellena diversidad. No
hay segundo brazo ejecutable hoy: Ollama está ausente, los modelos LM Studio
están archivados, OpenCode 1.18.4 no habilita el rol/structured output y
Antigravity/APIs carecen de loop MCP gobernado. P0.h.2d.5b solo se reabre ante
un cambio material de uno de esos canales. Verificación: 205 tests focales,
Ruff F/I, 13 receipts JSON válidos sin patrones de secretos, diff check y
1.715 tests backend pasan; quedan los dos skips de toolchain y la deprecación
Starlette/httpx ya conocida.

El siguiente bloque ejecutable es I.10. La auditoría inicial confirma que la
guía ya describe el gate final, pero doctor y runtime todavía no demuestran que
resuelvan el mismo binario. I.10 queda dividido en autoridad canónica,
resolución compartida, auditor fail-closed, integración durable y aceptación
clean/update. I.10.1 introduce `provider_cli_version_contract_v1` dentro de
`installation_support.v1.json`: referencia las filas de adapter para no
duplicar comandos y fija suelos validados/canales para Codex, Antigravity y
OpenCode. Una versión validada no sustituye health, catálogo ni calidad.
I.10.2a unifica después la resolución con
`platform_runtime.resolve_provider_cli`: doctor y runtime comparten shims
Windows y la ubicación conocida de Antigravity. El doctor vivo observa
Codex `0.146.0-alpha.6`, Antigravity 1.1.8 y OpenCode 1.18.4 con nombres de
ejecutable, nunca paths. Pasan 119 tests integrados y 106 al repetir el núcleo.

I.10.2b queda después cerrado. `provider_cli_fingerprint` liga resolución
normalizada y contenido bajo un dominio versionado, pero solo expone basename
y digest SHA-256. Cambia por ruta o contenido, es estable para el mismo archivo
y devuelve `null` si no puede verificarlo. `machine_doctor_v1` publica ya
fingerprints no nulos para Codex `0.146.0-alpha.6`, Antigravity 1.1.8 y
OpenCode 1.18.4 sin rutas personales. Pasan 122 tests integrados, Ruff F/I y
diff check. Siguiente unidad: I.10.3, auditor fail-closed de identidad, versión,
canal y catálogo/cache.

I.10.3 queda cerrado con `provider_cli_version_audit_v1`. Compara doctor,
runtime, SemVer/prerelease, basename, fingerprint y guía; Codex usa cache vivo
y Antigravity/OpenCode evidencia de catálogo fresca ligada a versión. Una
alternativa primaria puede faltar, ambas no; OpenCode sigue opcional. El recibo
`provider-cli-version-audit-2026-07-30.json` deja identidad, catálogo,
documentación y promoción del gate CLI en `true`, cero fallos, cero paths y
cero secretos. Esto no concede health ni calibración de modelos. Pasan 70
tests integrados, Ruff F/I y diff check.

I.10.4 queda cerrado. `machine_doctor_v1` proyecta resultado+hash del auditor
y falla estricto con `provider_cli_version_gate_failed`. El recibo
`machine-doctor-i10-4-2026-07-30.json` queda `ready`, mutation guard verde y
sin paths/secretos. Los harnesses Windows/POSIX crean tres shims efímeros y
cache Codex dentro del fixture, sin instalación global ni login; el contrato de
release añade `provider_cli_version_gate` como paso 18. El fixture descubrió y
corrigió una divergencia real: doctor sustituía el comando configurado `agy`
por la lista genérica y podía observar otro ejecutable que runtime. Pasan 61
tests dirigidos, ambos harnesses compilan, Ruff E402/F/I y diff check quedan
verdes; la regresión backend completa queda en 1735 passed, 2 skipped. Ambos
recibos son JSON válido, no contienen rutas personales ni patrones de secreto,
y el hash canónico del reporte de doctor coincide. El doctor completo continúa
con bloqueos locales ajenos a este gate; la proyección CLI sí queda `ready`.

I.10.5 queda cerrado con
`provider-cli-update-acceptance-2026-07-30.json`. El fixture read-only demuestra
que `clean_clone` y `existing_checkout_after_fast_forward` convergen al mismo
SHA-256 de matriz y quedan promocionables. El checkout existente conserva antes
del update un preflight bloqueado por `minimum_version` y
`fingerprint_matches`. Cinco canarios bloquean como se espera binario
duplicado, upgrade requerido, prerelease implícita, documentación obsoleta y
catálogo/matriz desincronizados; OpenCode ausente continúa válido como opcional.
El recibo no contiene rutas personales ni patrones de secreto. Verificación
dirigida: 60 tests integrados y Ruff E402/F/I verdes; regresión backend completa
posterior al cambio: 1738 passed, 2 skipped.

M.8 y el bloque P0.M quedan cerrados. La última deuda era mantenimiento
continuo: `model_catalog_maintenance_v1` persiste en SQLite un histórico
append-only al cambiar modelo/CLI/precio/cuota/prompt/tool/contrato o al
comenzar un mes. Repetir el mismo input/mes no escribe; cada fila conserva
métricas, tendencias, razones y hash válido, nunca candidatos o secretos. El
servicio lo reconcilia al reconstruir el catálogo y la API ofrece
`GET /api/model-catalog/maintenance`. El receipt
`model-catalog-maintenance-2026-07-30.json` pasa los siete triggers y la cadencia
mensual sin paths/secretos. M.9.1–M.9.6 ya estaban completos; se corrige su
checkbox padre sin cambiar preferencias locales. Verificación: 106 tests
integrados, Ruff E402/F/I y diff check verdes; regresión backend completa:
1752 passed, 2 skipped.

## Autoridad documental

Orden de prioridad:

1. `AGENTS.md`: reglas de desarrollo y producto objetivo.
2. `task.md`: estado y backlog resumido.
3. `docs/MIGRATION_PAPERCLIP.md`: plan rector e historial de la migración.
4. `docs/PAPERCLIP_GUIDE.md`: patrones Paperclip adaptados.
5. `docs/RUN_PROBLEMS_REGISTRY.md`: fallos observados y mitigaciones.
6. Código activo y tests.

`AGENTS.md` es la única instrucción raíz compartida. Las skills activas viven en
`.agents/skills/` y nunca prevalecen sobre `AGENTS.md`. No reintroducir
`CLAUDE.md`, `GEMINI.md` ni prompts raíz específicos de proveedor.

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

El canario exacto **Antigravity 1.1.6 GPT-OSS/Worker** queda cerrado. La seed 1
reproduce en 18,219 s `subscription_cli_parse_error` /
`submit_work JSON object not found`: modelo y catálogo son ejecutables, pero no
hay artefacto ni `AGENT-REPORT`; el workspace permanece intacto y se aplica
fail-fast sin consumir seeds 2–3. Es diagnóstico de contrato/transporte, no de
calidad del modelo ni motivo de retirada. La cobertura separa ahora 17 cierres
negativos vigentes mediante
`deferred_until_material_change`; solo difieren con política explícita, recibo,
edad y versión coincidentes. Cambio o versión desconocida los reabre. Ya no hay
`requires_canary` ni `requires_tool_fixture` accionables en la fotografía viva.
Recibo:
`benchmarks/results/model_calibration/antigravity-1.1.6-gpt-oss-worker-v2-seed-1.json`.
Flash Low/Web Scout quedó cerrado
negativamente en Antigravity 1.1.6: el canal carece de MCP gobernado, el rol lo
exige ahora en la política común y no se ejecutaron seeds 2–3 ni se atribuyó el
fallo al modelo. El watchdog de AI Teams vence antes que el timeout interno de
`agy`, evitando hijos huérfanos y errores de limpieza que oculten la causa.
El catálogo conserva el diagnóstico aunque el rol deje de estar nominado:
799 celdas, 697 incompatibles, 102 compatibles, cero auto-elegibles y auditoría
verde. Pasan 119 tests focales, 1634 globales y el check frontend completo
(lint, CSS, límites, 6 unitarios, build y 12 E2E), además de Ruff F/E9 y diff
check.
El inventario del 2026-07-24 descubre Ling 3.0 Flash
Free en OpenCode. El probe exacto confirma ejecución, pero OpenCode 1.18.4
devuelve el resultado correcto como pseudo-tool textual y marca
`StructuredOutputError` con `structured=null`. Ling queda `catalog_only`, sin
roles, quality ni selección; no repetir hasta cambio material. El teardown del
probe quedó como warning separado, sin proceso residual observable; el control
start/stop sin inferencia cerró en 0,25 s. Recibo:
`opencode-ling-3.0-flash-catalog-probe-v1.json`.
RUN-024 corrige además una fuga de aislamiento de tests: pytest dejaba
`AITEAM_PROJECTS_ROOT` vacío y el API creaba fixtures en el padre real del
repositorio. La sesión usa ahora una raíz temporal y workspace la refuerza por
test. Los directorios históricos no se eliminan automáticamente porque están
mezclados con proyectos reales. El cierre conjunto pasa 100 tests focales,
22/22 de workspace, 1639/1639 backend globales, el check frontend completo,
Ruff F/E9 y las auditorías de catálogo/cobertura.
El inventario read-only ampliado del `2026-07-30` confirma la escala del
incidente en la raíz personal configurada: 2.716 carpetas de primer nivel,
2.366 nombres con sufijo numérico y `.aiteam/aiteam.db`, 2.029 de ellos con
`.git`. Las familias dominantes son Demo (998), OrgChart (333), Reconcile (333),
Quorum (244), Solo (236) y AnthropicLead (215). No se movió ni borró nada. El
código conserva dos causas legacy verificadas: `_allocate_project_path` genera
siblings `Nombre 2…Nombre 999` y el fallback de borrado crea
`.aiteam-deleted-*` en la misma raíz. P0.K.8 queda desglosado para atribuir
contenido antes de proponer limpieza, eliminar esas fuentes de contaminación,
usar cuarentena reversible y exigir doble aprobación para cualquier purgado.
El owner aclara que esto no debe convertirse en deuda de limpieza futura:
K.8 exige ahora cero basura por construcción en cualquier instalación y
prohíbe daemon, tarea programada, cleanup de startup, TTL destructivo,
tombstones hermanos y sufijos automáticos. La remediación queda separada,
manual y exclusiva para raíces legacy; clone/bootstrap/create/retry/restart/
upgrade deben dejar un footprint exacto comprobado. La siguiente unidad concreta
era P0.K.8.2, prevención.
P0.K.8.2 queda cerrado: allocator, `/api/projects/new`, panel/estado React,
`aiteam project create` y fallback `.aiteam-deleted-*` fueron extirpados.
API/CLI no inicializan carpetas al seleccionarlas. El commit guiado exige parent
existente, colisión exacta, cleanup estricto y footprint pre/post; retry no crea
siblings y una entrada concurrente se conserva. Borrado bloqueado devuelve 423
sin rename ni cleanup pendiente. Pasan 159 tests backend dirigidos, 27
unitarios frontend, un E2E Chromium, Ruff, TypeScript, ESLint, Stylelint,
límites y build Vite. K.8.1 queda cerrado después con auditor portable
read-only, receipt fuera de la raíz, SQLite immutable, Git/remotos redacted,
handles opt-in y clasificación fail-safe. La pasada real corregida cubrió 2.716
carpetas: 2.359 candidatas de las seis familias legacy conocidas, 342 a
preservar/migrar y 15 personales protegidas; cero acciones autorizadas o
ejecutadas. Siguiente: K.8.3 dry-run manual e inmutable; todavía no mover ni
borrar el histórico.
K.8.3 queda cerrado con generador y CLI de manifiesto manual: revalidación viva,
targets hijos directos exactos, denegaciones fail-closed, output local exclusivo
y doble sello de integridad. El dry-run real propone 2.359 paths
(766.901.650 bytes), cero denegados dentro del batch y cero operaciones. Hash
del manifiesto `3aadd5a9828c1f8bf8544c578d9ff4463136fb35fcf5e8c59010ed782fc6fcfc`;
batch `8a1be67c6e1057b82b95931b3f3d6d65e6b90a8125f6f380d173d5a18da2debb`.
K.8.4 permanece bloqueado hasta revisión humana explícita; aprobar el documento
no equivale a ejecutar y toda acción futura debe revalidar.
K.8.4.1 queda implementado y probado solo en fixtures: doble sello, reauditoría
viva con handles, checksum de árboles, mismo filesystem, rename atómico,
journal sellado, rollback inverso y restore con preflight total. Se corrigió el
handle SQLite read-only usando cierre explícito; era la causa del
`PermissionError` de rename en Windows. Pasan 13 tests propios, incluido
apply→restore CLI real sobre un fixture. No existe purga. K.8.4.2 sigue
bloqueado: `continúa` no aprueba los 2.359 paths ni sus dos hashes.
P0.K.8.5 queda cerrado sin tocar la raíz real. `project_hygiene_v1` comparte una
observación ligera, redacted y read-only entre machine doctor, API, primer uso,
Nuevo proyecto y Configuración. Una ruta editada debe recomprobarse antes de
guardar; el doctor solo avisa y nunca instala cleanup. La API reconoce
`AITEAM_PROJECTS_ROOT` como configuración efectiva y los tests demuestran que
cambiar la raíz conserva preferencias y almacenamiento de adapters. Contrato
humano/IA en `docs/PROJECT_ROOT_HYGIENE.md`. Verificación: 69 tests backend del
bloque K.8 y 32 unitarios frontend, Ruff, tipos, linters, límites, build y
bundle verdes. Siguiente unidad ejecutable: K.8.6; K.8.4.2 continúa requiriendo
aprobación owner exacta.
Codex npm estable sigue en 0.145.0, pero la prerelease oficial
0.146.0-alpha.6 reconcilia la caché 0.146.0 y mantiene auth ChatGPT. Antigravity
1.1.6 conserva catálogo, vuelve stale Sonnet/Engineer 1.1.5 y su screening de
revalidación pasa 3/3 hidden pero falla Ruff con 7 incidencias; no repetir hasta
otro cambio material. El owner ha despriorizado por ahora I.8.4c/d Linux/macOS,
Containers, Mobile nativo y PHP/Ruby/Swift. El descriptor v0.1.0 conserva
`publish.enabled=false`: no crear el tag sin evidencia independiente.
I.8.1 e I.9 ya están cerrados.
El owner ha fijado una prioridad explícita de catálogo. Deben archivarse de
forma reversible los tres modelos de `local_gem4_lmstudio` y las identidades
GPT-OSS 120B de Antigravity y GPT-OSS 120B/20B de Groq, conservando su historial
pero excluyéndolos de selección y mantenimiento. Son prioridad alta Sol Tier 1
en Codex, Antigravity Gemini 3.6 High/Medium/Low, Gemini API Free, Groq Qwen
3.6 y los seis modelos OpenCode Free. El resto queda en prioridad baja, no
retirado.
M.9.1 implementa `model_owner_preferences_v1` en configuración local:
identidad exacta perfil+modelo, estados high/normal/low/archived, razón, fecha,
escritura atómica y fallo cerrado. Pasa 9 tests propios y 69 focales conjuntos.
M.9.2 ya lo convierte en un gate contextual efectivo: el read model proyecta
la preferencia y la hashea; archivados no pueden ser selección nueva, default,
hiring ni fallback, sin alterar el score técnico o borrar receipts. El backlog
de mantenimiento prioriza `high` y no propone trabajo proactivo para `low` o
`archived`; la caché se invalida con el archivo local y corrupción falla
cerrado. Verificación: 59 pruebas focales, 201 integradas con API/executor y
Ruff F/I/E402 del delta.

M.9.3 está implementado: el executor pausa antes de inferencia una asignación
existente archivada, bloquea su issue y persiste una interacción idempotente.
No muta el agente hasta que el owner acepta; entonces revalida selección,
preferencia, adapter y compatibilidad antes de actualizar y reencolar. Rechazo,
stale, corrupción o falta de alternativa permanecen bloqueados. Las nuevas
selecciones explícitas y la escalada senior tampoco pueden elegir archivados.
M.9.4 añade API local GET/PUT y controles de prioridad, archivo y reactivación
en Modelos, más badges/filtros y opciones archivadas visibles pero
deshabilitadas en Equipo. El doble check conjunto M.9.3–M.9.4 pasa 152 tests
backend relevantes, TypeScript, ESLint, Stylelint y límites frontend.
M.9.5 queda cerrado con persistencia entre procesos, máquina limpia y matriz de
onboarding, Equipo, hiring, quorum, fallback y defaults. Corrige el bypass del
selector legacy en rollout `shadow`, la causa falsa de tier en onboarding y el
orden de diagnóstico en PATCH de Equipo; añade defensa para quorum/defaults
stale. Pasa 308 tests backend integrados y 7 unitarios frontend, además de los
gates estáticos del delta.
M.9.6 y P0.a quedan cerrados: la configuración local clasifica 47 identidades
exactas en 6 archivadas, 13 altas y 28 bajas mediante reemplazo validado y
atómico. El read model pasa con 47 candidatos/799 filas y cero fallos; una
matriz sobre los 17 roles confirma cero archivados seleccionables/default. La
cobertura durable conserva 124 pares, seis filas archivadas sin mantenimiento
y backlog vacío. Las preferencias personales siguen fuera de Git, por lo que
un clon nuevo comienza en `normal`.
P0.b queda cerrado: Sol es ejecutable/selectable y sus cinco roles críticos
(`architect`, `lead`, `lead_executor`, `quorum_auditor`, `team_lead`) pasan
30/30 muestras, dos familias × tres semillas, con CLI exacto
0.146.0-alpha.6. Los cinco agregados quedan registrados y la cobertura los
declara calibrados. El juez añadió el sinónimo general `indivis` y reevaluó la
misma respuesta, sin re-roll. Sol sigue sin auto-promoverse; Terra/Luna
0.145.0 quedan stale. Sustituir la prerelease cuando npm publique 0.146.0
estable y revalidar transporte. El siguiente lote ejecutable es P0.c,
Antigravity Gemini 3.6 High/Medium/Low.
P0.I añade I.10 como gate final pendiente: antes de cerrar instalación/release
debe coincidir la versión o rango declarado en la guía, el ejecutable que
resuelve el adapter y la versión publicada por `machine_doctor_v1`. Codex,
Antigravity y OpenCode son el mínimo; drift, prerelease no declarada o
CLI/cache incompatibles fallan cerrado y producen receipt redacted.
P0.c queda cerrado sobre Antigravity 1.1.6: catálogo autenticado de 11 modelos
y submits exactos verdes para High/Lead+coding, Medium/review y Low/scout. Los
tres están verificados y seleccionables manualmente, pero siguen
`manual_only`; las muestras 93,3 %, 72,7 %, 100 % y 100 % son screening
estructural de una semilla, no autorización de default. El siguiente lote del
owner es P0.d: readiness separado de Gemini API Free y Groq Free.
P0.d.1 completa el preflight sin secretos: ninguna de las referencias
`secret:google-free:default`/`secret:groq:default` existe aún. Ambos perfiles
persisten `blocked/api_key_ref_missing`, catálogo `not_checked` y no se hizo
ninguna llamada API. P0.d.2 requiere que el owner cree y guarde localmente sus
keys; después se ejecutan por separado discovery, health y structured output
de Gemini y Groq antes de calibrar.
El trabajo de modelos debe respetar además el
gate único configuración/auth → catálogo/versionado → adapter verde → contrato
estructurado/tools → canario → calibración multi-familia → promoción. Un
adapter rojo es deuda de integración y nunca un resultado de calidad.
PHP/Ruby queda pausado por prioridad del owner. I.6.2 está cerrado con la run
`30085247826`: 18 receipts/27 celdas ligadas a `775e72e` y agregado durable;
Web moderno amplía la matriz a 30/30 en `30085680374` sobre `8888dfe`.
`support_claim=false` impide promoción automática. M.8 queda abierto como
mantenimiento por evento/mes; sus 25 pares
calibrados ya tienen quality exacta, 21 abren diversidad y los cuatro restantes
no deben repetirse hasta un cambio material.

## Trabajo reciente

- I.8.3 acepta el ZIP desde un wrapper externo mediante 17 gates canónicos:
  verificación/extracción, bootstrap ×2, audit, tests, start/health/stop,
  fixture, backup/restauración SQLite exacta y retirada completa. La run real
  descubrió que un venv Python 3.12 carece de setuptools: ahora setuptools
  83.0.0 y wheel 0.47.0 están fijados con hashes. También se eliminó la cabecera
  dependiente de ruta de `uv export`, que invalidaba el `cmp` de CI. El recibo
  local redacted `release-preview-local-f69f8e7.json` pasa 17/17 sobre 1164
  archivos; sigue no promocionable por worktree sucio/host no independiente.
  `release-acceptance` queda como matriz obligatoria Windows/Linux/macOS y
  dependencia de `publish`; el wrapper elige harness Windows o POSIX y PR/manual
  prueban previews sin promocionarlos. I.8.4a/b pasa 50/50 pruebas de
  release/instalación, el gate polyglot 17/17 y la suite backend 1611/1611;
  Ruff/diff/YAML verdes. Faltan los receipts reales hosted y físicos.
- I.8.2b añade `release_descriptor_v1`, notas v0.1.0 y una guía de
  upgrade/rollback side-by-side con backup/restauración SQLite. El verificador
  comprueba checksum externo, rutas/duplicados, cobertura interna completa y
  manifiesto promocionable. CI construye read-only; un job separado bajo
  `github-release` recibe `contents: write`, revalida, crea draft, exige cinco
  assets y publica sin overwrite. El candidato sigue bloqueado hasta I.8.4.
  Pasan 26/26 pruebas focalizadas, 1600/1600 backend, Ruff y parseo YAML; un
  preview integral de 1162 archivos se construyó y verificó como no
  promocionable por suciedad.
- I.8.2a resuelve los dos blockers decididos por el owner. Apache-2.0 usa el
  texto oficial exacto, `NOTICE` atribuye copyright 2026 a Max Bonas Fuertes y
  ningún identificador fiscal se versiona. `uv.lock` 0.11.31 fija 58 paquetes
  para Windows/Linux/macOS × x86-64/ARM64; exports runtime/dev con hashes
  mantienen `pip` como bootstrap interoperable. CI verifica lock y exports,
  bootstrap exige hashes y el SBOM consume versiones Python bloqueadas.
  Resolución universal, bootstrap, `pip --dry-run`, 29/29 focalizadas,
  1588/1588 backend, frontend build y audit cero pasan. RUN-020 conserva la
  advertencia upstream Starlette/httpx2 sin migración especulativa.
- I.8.1 añade `release_artifact_v1`, esquema y generador reproducible:
  solo Git, ZIP stored, orden/timestamps/modos estables, manifiesto y hashes por
  archivo, checksum externo, CycloneDX 1.6 e informe de licencias. Rutas
  runtime/dependencias/SQLite/secretos fallan cerrado; los falsos positivos
  conocidos usan dos literales exactos. La workflow crea previews auditables,
  pero un tag exige `promotion_allowed=true` y nunca publica una GitHub Release.
  El preview local previo empaquetó 1032 archivos y registró 367 dependencias
  npm. Los blockers de licencia/lock se cierran en I.8.2a. Pasan 10/10 tests propios,
  18/18 con documentación, Ruff y 1588/1588 backend.
- I.9.3 cierra el hardening web previsto. Vitest + React Testing Library
  aportan 6/6 pruebas de componente para estados, errores y teclado en Chat,
  Issues y Runs. Playwright mantiene los nueve recorridos completos en Chromium
  escritorio y ejecuta el smoke crítico del cockpit en Chromium móvil, Firefox
  y WebKit; son 12/12 ejecuciones, sin declarar cobertura exhaustiva fuera de
  Chromium escritorio. Axe y overflow están en la matriz; el primer móvil
  reveló y permitió corregir el timeline desplazable no enfocable. El gate
  completo añade presupuestos fail-closed: JS 366071 B raw/107539 gzip bajo
  400/120 KiB, CSS 101679/18106 B bajo 120/25 KiB. ESLint, Stylelint, tamaño,
  unitarias, typecheck/build, E2E y audit cero pasan.
- I.9.2c cierra la separación estructural prevista: `ChatPanel`, `IssuePanel`,
  `IssuePipeline` y `RunsPanel` tienen contratos y hojas propias; tipos de
  cockpit y markdown ya no viven en `App.tsx`. El nuevo `lint:size` aplica
  límites 600 TS/TSX y 500 CSS, con ratchets explícitos para los tres módulos
  legacy mayores. Las hojas aisladas reactivan `no-descending-specificity`.
  `App.tsx` baja 3984→3546 e `index.css` 1692→1246. ESLint, Stylelint, tamaño,
  build, 9/9 E2E —incluido Chat→Detalle→Runs— y audit cero.
- I.9.2b3 cierra Configuración y Bandeja. `ConfigurationWorkspace` compone
  credenciales, CLIs, adapters, sistema y zona de peligro; el hook
  `useConfigurationData` concentra estado, cargas y mutaciones de Config sin
  duplicar scoring ni fetches. Workspace, navegación y confirmación destructiva
  permanecen en `App.tsx`. CSS de conexiones, `InfoTip` y Equipo queda aislado.
  Resultado final de b: `App.tsx` 4682→3984, `index.css` 2143→1692; ESLint,
  Stylelint, build, 8/8 E2E y audit cero.
- I.9.2b2 separa `SkillsSettings`, `McpSettings` y el detalle tipado de hiring
  de Bandeja. Las vistas reciben estado y callbacks explícitos: no recalculan
  scoring, no conceden tools MCP y no deciden aceptación. El gate de
  compatibilidad de hiring y las transiciones owner permanecen en `App.tsx`.
  `App.tsx` baja de 4931 a 4682 líneas; `index.css` permanece en 2143. ESLint,
  Stylelint, build, 8/8 E2E y audit cero. Siguiente corte: I.9.2b3,
  configuración global/sistema y zona de peligro.
- I.9.2b1 separa los shells y superficies menos acopladas de Configuración y
  Bandeja. `ConfigurationPanel`, Proyecto, Autonomía, Orientación, `InfoTip` y
  la lista/selección de `InboxPanel` tienen módulos tipados; CSS y responsive
  viven con su dominio. Los formatters compartidos preservan la semántica UTC de
  SQLite. `App.tsx` pasa de 5141 a 4931 líneas e `index.css` de 2552 a 2143.
  ESLint, Stylelint, build, 8/8 E2E y audit cero.
- I.9.2a completa el primer corte estructural seguro. `ModelCatalog`,
  `ModelRoleSelector` y `QuorumStepper` poseen CSS propio; el quorum sale de
  `App.tsx` hacia un hook keyed por issue, abortable y tipado, y su vista y
  formatters quedan en módulos independientes. Las cargas iniciales diferidas
  evitan el doble fetch de StrictMode y se corrigió el fixture de retry para
  exigir una sola caída real. No quedan excepciones `set-state-in-effect`.
  `index.css` pasa de 2974 a 2552 líneas y `App.tsx` de 5298 a 5141. ESLint,
  Stylelint, build, 8/8 E2E y audit cero.
- I.9.1 endurece el frontend principal: React 19.2.8, Vite 8.1.5, ESLint 10,
  plugins actuales y TypeScript 5.9.3 por compatibilidad declarada de
  `typescript-eslint`. `npm audit` pasa de diez vulnerabilidades a cero. El gate
  único `npm run check` cubre ESLint, Stylelint, build y 8 E2E; Axe WCAG 2.1 AA,
  viewport móvil y ausencia de overflow horizontal quedan protegidos. Se
  corrigieron contraste, CSS deprecado/duplicado y funciones React no hoisted.
  La workflow `frontend-quality.yml` lo reproduce con Node 24 y lockfile.
- I.6.1 e I.6.3 quedan cerrados sin sobrepromover ecosistemas.
  `ecosystem_validation_receipt_v1` ejecuta fixtures Python/npm mínimos y un
  monorepo multi-language desde rutas temporales con espacios/Unicode, valida
  artefactos, timeout y errores, y registra OS, arquitectura, SHA, dirty bit y
  versión del runtime sin rutas absolutas. Los planes denegados exponen
  `capability_gap_v1` con owner y remediación. Python/npm pasan 4/4 celdas;
  Java/Maven añade package+JUnit verde y .NET queda bloqueado correctamente
  porque el host tiene runtime sin SDK. Los receipts ya redaccionan rutas
  absolutas. Pasan 30 tests focalizados, 190 de integración y 1578/1578
  globales, pero `support_claim=false` por worktree sucio. La workflow
  Windows/Linux/macOS cubre ya nueve casos. Go/Rust tienen fixtures build/test;
  en Windows local ambos devuelven gap de runtime y no se instalaron. I.6.2 no
  cierra hasta auditar artifacts. C/C++ añade `configure` al contrato y fuerza
  `configure → build → test`; Windows bloquea las tres fases en cascada sin
  ejecutar fuera de orden. PHP/Ruby son la siguiente unidad de I.6.4.
- P0.J queda cerrado. `objective_classification_v1` persiste `software`,
  `research`, `operations` o `mixed` desde creación de proyecto/tarea y lo
  muestra en el cockpit, plan y wake payload. Hiring/delegación rechazan roles
  de programación para research/operations; mixed exige hijos `software`.
  Quality/test gates ya no bloquean entregables documentales. El fixture e2e
  exacto del estudio de empresa de limpieza cierra con Lead, scout y curator,
  sin tests ni manifests. Pasan 228 pruebas dirigidas, 1561/1561 backend,
  lint/diff, typecheck frontend y 10/10 focalizados posteriores al hardening
  contra propuestas editadas.
- I.5 queda cerrado como contrato, no como afirmación global de soporte.
  `ecosystem_registry_v1` contiene doce descriptores, detector read-only
  acotado, planner fail-closed y proyección común a doctor, Lead/hiring, wake
  payload y runner determinista. Solo pytest/npm conservan estado
  `legacy_enabled`; todo comando `planned` queda bloqueado hasta I.6. Pasan 28
  pruebas focalizadas, 116 de `RunExecutor` y 1550/1550 backend globales;
  ningún descriptor emite `support_claim=true`.
- I.4 queda cerrado. I.4.3 añade diez casos versionados de recovery y registra
  cada proceso inmediatamente después de su spawn. Preflight falla antes de
  mutar ante inputs ausentes; los batch usan UTF-8. Los canarios Windows cubren
  ruta con espacios/ñ/japonés, puerto ajeno, start repetido, pérdida parcial,
  pérdida total/stale y reinicio 200/200. Pasan 27 tests focalizados,
  Ruff/Node/diff y 1537/1537 backend. POSIX conserva estado preview hasta
  aceptación independiente.
- I.4.2 fija `requirements-dev.lock`, exige `package-lock.json` + `npm ci` y
  elimina upgrades/fallbacks abiertos. El bootstrap queda serializado por lock
  exclusivo en Windows y lockdir con PID en POSIX. Start/stop comparten
  `dev_process_registry_v1`: validan PID, create time y firma, no matan por
  puerto y fallan cerrados ante identidad discrepante. El canario Windows
  devuelve health 200/200, libera solo sus árboles, conserva un proceso ajeno
  en 8010 y confirma segunda pasada sin cambiar estado. Pasan 32 tests
  focalizados, Ruff/Node y 1531/1531 backend; POSIX sigue pendiente de recibo
  independiente e I.4.3 conserva fallos/interrupción/recovery.
- I.4.1 añade `dev_lifecycle_v1`, fuente versionada de las acciones
  prepare/start/stop/test/migrate y sus frontends Windows/POSIX. El proyector
  falla cerrado, conserva paths dentro del checkout y publica gaps. Los wrappers
  POSIX usan venv/node_modules locales y sesión foreground; no usan PowerShell
  ni instalaciones globales. POSIX sigue preview/planned: no hay `sh` ni recibo
  independiente en esta máquina, y locks/PIDs/recovery quedan en I.4.2–I.4.3.
  La doble ejecución Windows no cambia CLIs ni hashes de estado tras evitar
  reescrituras de timestamps/baselines; pasan 37 tests focalizados, Ruff y
  1527/1527 backend.
- I.3 queda cerrado. I.3.4 añade un recibo determinista que sella
  `machine_doctor_v1` y compara metadata de checkout/config e inventario de CLIs
  sin abrir secretos. La escritura del recibo requiere output explícito y
  consentimiento de overwrite. La remediation vive en otro comando, consume el
  recibo sellado y solo produce un plan `guided_manual`, `applied=false`.
  El flujo real conserva las tres superficies y queda hash-bound; una frontera
  UTF-8 común evita fallos cp1252 en Windows. Dos ejecuciones reales producen
  el mismo `receipt_id`; pasan 38 tests focalizados, Ruff y 1518/1518 backend.
- I.3.3 añade diagnóstico determinista a `machine_doctor_v1`: estados
  `absent`, `not_authenticated`, `incompatible`, `unverified` y `degraded`,
  severidad, fuente y siguiente acción. La máquina queda `blocked` solo por
  no existir una vía primaria con auth+health durables; `--strict` devuelve 2.
  Los perfiles opcionales no bloquean y ninguna acción se ejecuta desde doctor.
  Pasan 49 tests focalizados, Ruff y 1509/1509 tests backend.
- I.3.2 amplía `machine_doctor_v1` con 11 señales de toolchain y todos los
  perfiles adapter redactados. Manifest, binario, versión, auth y health quedan
  como estados ortogonales; los runtimes locales se observan aparte del CLI de
  transporte. El doctor real ve 12 perfiles y manifests Python/JS sin login,
  secret store, catálogo vivo, instalación ni inferencia; no muta los tres
  archivos de configuración locales. Pasan 46 tests focalizados, Ruff y
  1506/1506 tests backend.
- I.3.1 queda cerrado con `machine_doctor_v1`: JSON Schema fail-closed,
  inventario de host, Python, Node/npm, Git, PowerShell, SQLite, puertos
  loopback y permisos del checkout. Los comandos de versión reciben solo entorno
  allowlisted; la salida elimina paths y declara que no leyó secretos ni
  credenciales. El doctor real devuelve inventario completo; 29 tests
  focalizados, Ruff en alcance y 1502 tests backend pasan.
- I.2 queda cerrado. I.2.3 añade `aiteam.platform_runtime` para semántica de
  paths, shims ejecutables, layout de venv, UTF-8 y teardown de árboles de
  proceso; adapters, MCP, notifier, CLI y probes usan la frontera. El notifier
  deja `shell=True` y las utilidades NordVPN eliminan paths personales y pasan
  a dry-run con `-Apply`/backup. `scripts/audit_platform_portability.py`
  produjo `ok=true` en Windows x86_64 sin promocionar soporte; 107 pruebas
  dirigidas, suite backend 1493/1493, typecheck frontend y Ruff acotado a
  superficies cambiadas pasan. El siguiente bloque es I.3.
  El smoke-clone posterior detectó y corrigió que `pip install -e .` podía
  heredar el cwd del invocador y enlazar otro checkout: el helper fija ahora
  su propio root como working directory y existe regresión específica.
- I.2.2 añade `aiteam_portable_config_v1` y
  `scripts/config_portability.py export|inspect|import`. El paquete hasheado
  conserva settings allowlisted, perfiles custom y política estructurada
  opcional; excluye paths, secretos, health, sesiones, runtime, dependencias,
  DB y estado vivo. Import hace preflight salvo `--apply`, mergea sin borrar
  configuración ajena e invalida health hasta probe local. Matriz I.2 conjunta
  80/80, suite backend 1478/1478, Ruff/typecheck limpios y exportación real
  efímera válida.
- I.2.1 queda cerrado: `configuration_layers_v1` fija cinco capas con owner y
  provenance, separa secretos/estado y conecta la precedencia real a settings,
  autonomía y adapters. La actualización Windows usa `pull --ff-only`, rechaza
  worktrees sucios y fusiona defaults en JSON heredado sin perder overrides; un
  JSON inválido se conserva y bloquea. El recorrido para instalaciones
  anteriores al script está documentado. 74 tests focalizados incluyen remote
  Git real, bootstrap, preservación local, merge de tres vías, idempotencia y
  fail-closed. Suite backend 1472/1472, Ruff y typecheck frontend limpios.
- I.1 queda cerrado. El run independiente
  `https://github.com/MaxBonas/ai-teams/actions/runs/30023876549` prueba la
  revisión exacta `f2a20ed`: cinco runtimes listos, 10/10 pasos, bootstrap
  43,906 s→2,109 s, health backend/frontend, fixture de una issue/26 tablas,
  stop limpio y cero CLIs añadidos. El recibo redacted versionado es
  `benchmarks/results/installation_acceptance/windows-clean-room-f2a20ed.json`
  (SHA-256 versionado
  `b45b9c285bec86ba356ce36a747b24d2ba9d503d51d5ec34291cc5ebf5c6111d`;
  artefacto original
  `b8b714f97b103ba602419849c0bccdeb18362de49e2bbae8e2533f7e37d20806`).
  `windows_native_x86_64` y `git_checkout` pasan a `verified` solo para control
  plane; adapters vivos, releases, ARM64 y POSIX conservan gates separados.
- I.1.4.1–I.1.4.2 añaden un harness Windows fail-closed y un workflow sobre
  `windows-latest`. Comprueban revisión, bootstrap doble, auditoría, start/stop,
  health, proyecto SQLite fixture, puertos liberados y que no aparezcan CLIs
  globales implícitos. Un recibo local se etiqueta `local_existing_host` y no
  permite promoción. La ejecución local integral pasa 10/10 pasos, una issue y
  26 tablas; corrigió un bloqueo por handles heredados en el primer intento.
  La auditoría del artefacto independiente exigió además checkout del head SHA
  exacto y versiones redacted de runtimes antes de permitir promoción. Config
  añade una guía expandible de OpenCode Zen con enlace a
  la key personal, login en terminal, `opencode auth list` y probe posterior,
  sin recoger la credencial. Verificación: 41 tests focalizados, Ruff y
  typecheck limpios, 1461 tests backend y teardown sin listeners residuales.
- I.1.1–I.1.3 fijan `installation_support_v1` como fuente única de plataformas,
  runtimes, distribución y clases de adapter. El bootstrap ejecuta un auditor
  read-only que recomienda sin instalar: hace falta un solo canal Lead-capable
  verde; Codex/Antigravity son opciones primarias, OpenCode es economía opcional
  con API key personal y Ollama/LM Studio son locales opcionales. La máquina
  La auditoría local inicial reportó Windows x86_64 `preview`, control plane listo, Codex 0.145.0,
  Antigravity 1.1.5 y OpenCode 1.18.4 presentes, sin fingir auth/health. El caso
  externo del 2026-07-22 queda documentado y resuelto en RUN-018; la parte no
  programativa está cerrada por P0.J. Verificación: bootstrap
  completo, 37 tests dirigidos, Ruff limpio y 1456 tests backend. El contrato
  de release exige versión, SHA-256, SBOM/licencias, migración y rollback; su
  materialización permanece en I.8.
- M.8.3.1–M.8.3.2 separan evidencia con `model_evidence_taxonomy_v1` y elevan
  el scorer a `model_role_score_v2`. Benchmarks generales, canarios de rol y
  fixtures de tools no se sustituyen; cuatro `research_score` quedan visibles
  pero fuera del score. `case_diversity` exige dos familias independientes para
  automática: 21/25 calibraciones cumplen y 4 conservan quality pero quedan
  bloqueadas por mono-familia/Goodhart material. Catálogo: 23 canarios de rol,
  2 fixtures exactos, cero auto y cero fallos. El snapshot v2 conserva
  `recommend`. M.8.3.3–M.8.3.4 quedan cerrados; Sonnet/Engineer, Flash High/QA,
  Flash High/Test Designer y Flash Low/File Scout fueron evaluados pero siguen
  mono-familia hasta cambio material. Verificación: 117 tests focalizados, Ruff
  limpio y 1451 tests backend.
- M.8.3.3 Coding queda ejecutado con una segunda familia `config_redactor`.
  Terra/Engineer pasa tres seeds, 9/9 tests ocultos y Ruff; su agregado
  diversity-aware enlaza 6/6 muestras entre dos familias y abre el gate.
  Sonnet/Engineer pasa 3/3 ocultos en seed 1 pero falla Ruff por un import
  `pytest` sin usar; fail-fast detiene seeds 2–3, conserva diagnóstico y no abre
  diversidad. No se parcheó la salida ni se promovió ningún default.
- M.8.3.3 QA usa una segunda familia de firma, expiración y replay de webhooks.
  Terra/QA pasa tres seeds y 30/30 gates; su agregado enlaza 6/6 muestras y abre
  diversidad. Flash High/QA pasa el ataque de seed 1, pero la reverificación
  termina en `subscription_cli_timeout` tras 240 s; fail-fast detiene seeds 2–3.
  Es diagnóstico operacional, no fallo de calidad ni permiso de promoción.
- M.8.3.3 Test Designer usa una máquina de estados como segunda familia. Terra
  pasa tres seeds, 24/24 gates y 15/15 mutantes; su agregado enlaza 6/6 muestras
  y abre diversidad. Flash High pasa seed 1; seed 2 mata 5/5 mutantes pero
  expira antes del reporte durable, por lo que fail-fast detiene seed 3 y el
  gate permanece cerrado.
- M.8.3.3 Tier 3 añade familias por función. Luna/Worker, Flash Medium/Worker y
  Luna/Web Scout completan 3/3 seeds y agregados de 6/6 muestras, por lo que
  abren diversidad. Flash Low/File Scout falla seed 1 con
  `subscription_cli_parse_error` antes de un submit estructurado; fail-fast
  detiene seeds 2–3 y mantiene la calibración anterior sin abrir el gate.
- M.8.3.3 MCP Operator añade política de dependencias como segundo dominio.
  Terra completa 3/3 seeds y 36/36 gates de recovery y gobernanza; el agregado
  enlaza 6/6 muestras y abre diversidad. Los receipts antiguos se versionaron
  por reevaluación determinista, sin repetir proveedor.
- M.8.2 queda cerrado: 46 candidatos × 17 roles canónicos =
  782 celdas. `CANONICAL_ROLES` excluye aliases; 666 incompatibilidades quedan
  explicadas y sin score. La automática ahora exige política global y nominación
  exacta en `best_for`: 71 combinaciones compatibles no nominadas permanecen
  manuales, sin perder compatibilidad. De las 45 nominadas, no queda ninguna
  ruta operativa sin evidencia. Luna/File Scout y Flash Low/Worker ya tenían
  agregados íntegros de tres semillas; sus resultados parciales no se repiten ni
  reciben quality. El auditor falla ante matriz incompleta, score incompatible,
  política rol divergente o ruta automática operativa sin recibo. Hay 25
  calibraciones exactas, cero scores completos, cero auto-elegibles, cero
  fallos y un warning stale. No se consumieron inferencias nuevas.
  Verificación: 84 tests dirigidos, Ruff limpio y 1434 tests backend.
- M.8.1 conecta por fin calibraciones y read model productivo mediante
  `model_normalized_metrics_v1`. Las 25 celdas calibradas reciben calidad
  normalizada y metadata de evidencia; parciales/negativas/no probadas quedan
  unknown. No se inventan capability, fiabilidad, economía o velocidad: siguen
  cero candidatos auto-elegibles. El fallback de versión usa únicamente el
  último drift autenticado fresco y conserva provenance. Recibo vivo: 46
  candidatos, 25 métricas exactas, cero fallos y deuda explícita por celda.
  Verificación: 48 tests dirigidos, Ruff en alcance, 1429 backend y dos JSON
  válidos/sin secretos.
- M.7.4 queda cerrado con promoción solo a `recommend`, nunca `auto`. El
  snapshot vivo persiste 14 roles × 46 candidatos: hashes válidos, cero
  `auto_applied` y cero mutaciones. Economía declarada 644/644, pero únicamente
  17 normalizadas; 392 adapters rojos y capacidad desconocida/no-data en las
  644 observaciones impiden ganador. La matriz negativa falla cerrado para
  health, incompatibilidad, precio, cuota, stale, empate y override. El rollout
  revalida el ganador y los empates exactos ya exigen owner. `.env.example`
  propone `recommend`; `shadow` sigue siendo fallback y rollback. Verificación:
  124 tests dirigidos, Ruff en alcance, 1424 backend y recibo válido/sin
  secretos.
- M.7.3 queda cerrado como pool no bloqueante y sin promociones. OpenCode 1.18.4
  conserva catálogo/transportes y se cierra sin nuevas inferencias mediante un
  recibo con hashes; DeepSeek Reviewer sigue `partial` 1/3. GPT-OSS 120B falla
  `submit_work` en los tres roles exactos y se detiene por fail-fast. En Ollama
  0.32.1, Qwen 14B y Gemma E4B fallan sus contratos; Gemma 26B Engineer queda
  `partial` 1/3 y Reviewer/Test Designer fallan. Cada celda mantiene diagnóstico
  durable. La economía local queda aclarada: coste/API/tokens/cuota externos
  siempre 0 y cuota ilimitada; recursos, energía y latencia son capacidad del
  host separada. El scorer asigna economía local conocida 100/100 sin inventar
  calidad. Cobertura: 25 calibrados, 5 parciales, 15 canarios, 4 fixtures, 3
  manuales y 79 bloqueados. Verificación: 140 tests dirigidos, Ruff en alcance,
  1420 backend y 18 JSON válidos/sin secretos. Próxima unidad: M.7.4 snapshot
  vivo de promoción.
- M.7.2.3 y la cohorte M.7.2 quedan cerradas como evaluación, no como promoción
  automática. El harness Tier 3 es ahora multiperfil y no atribuye esfuerzo ni
  tokens inexistentes a Antigravity. Flash Medium/`worker` pasa 3/3 (mediana
  70,640 s); Flash Low/`file_scout` pasa 3/3 (80,080 s); Low/`context_curator`
  pasa auth+queue 6/6 (96,300 s; 42,300–169,700). Low/`worker` queda parcial
  2/3: una célula sufre timeout de 240 s, requiere recovery y repite el hecho
  prohibido “jueves”. No se re-rollea ni se promociona. Los agregados enlazan
  recibos, fixtures y hashes, el auditor detecta tampering y usage queda
  `unknown`. Cobertura: 25 calibrados, 4 parciales, 16 canarios, 4 fixtures, 3
  manuales y 79 bloqueados. Verificación: 20 tests dirigidos, 1409 backend,
  Ruff limpio en el alcance y 19 JSON activos válidos/sin secretos; Ruff global
  conserva 137 incidencias fuera de esta unidad. Próxima unidad: M.7.3 o, para
  la cohorte principal, M.7.4 snapshot vivo de promoción.
- M.7.2.2 calibra Gemini 3.5 Flash High en los dos contratos Tier 2 pendientes
  sin extrapolar Reviewer. Los harnesses de Terra aceptan ahora perfil+modelo
  manteniendo casos y jueces: QA pasa 3/3 ciclos ataque→fix→verificación y
  30/30 gates (mediana 130,733 s); Test Designer pasa 3/3, mata 15/15 mutantes
  y supera 24/24 gates (mediana 55,266 s). El juez QA amplía `active=False` a
  constructores y la superficie authored ignora únicamente caches Python; las
  muestras se reevaluaron sin rerun. Usage Antigravity sigue `unknown`.
  Agregados con fuentes+hashes y tampering test dejan ambos pares calibrados.
  Cobertura: 22 calibrados, 4 parciales, 19 canarios, 4 fixtures, 3 manuales y
  79 bloqueados. Verificación: 17 tests dirigidos, Ruff, 1403 tests backend y
  8 artefactos activos íntegros/sin secretos. Próxima unidad: M.7.2.3, Flash
  Medium/Low Tier 3.
- M.7.2.1 mejora Luna Tier 3 sin subir esfuerzo: un contrato causal/report v2 y
  una skill ausente de `worker` corrigen fallos reales de producto; el canario
  de `file_scout` deja de pedir review/recomendaciones incompatibles con su rol.
  Luna `low` completa `worker` 3/3 en una run y `web_scout` 3/3 con MCP
  gobernado; ambos quedan calibrados. `file_scout` retiene calidad 3/3, pero
  solo cierra en una run 1/3 y queda parcial. Los agregados enlazan fuentes y
  hashes y el auditor detecta tampering. Cobertura: 20 calibrados, 4 parciales,
  21 canarios, 4 fixtures, 3 manuales y 79 bloqueados. Próxima unidad:
  M.7.2.2, Flash 3.5 High en QA y Test Designer. Verificación: 109 tests
  dirigidos, Ruff, 1396 tests backend y 12 artefactos JSON íntegros/sin secretos.
- M.7.1 queda cerrada: el contrato productivo Tier 1 incorpora una pasada de
  retención causal compartida por adapters y por el prompt consolidado Codex.
  El screening pareado v1→v2 mejora las cinco familias débiles de 1/3 o 2/3 a
  3/3; los casos complementarios también pasan, para 30/30 muestras v2 y cinco
  matrices nuevas 6/6. Sol y Gemini 3.1 Pro High quedan calibrados en los cinco
  roles exactos (`architect`, `lead`, `lead_executor`, `quorum_auditor` y
  `team_lead`). El harness rechaza versiones mezcladas y el registro valida
  `prompt_version`, fuentes y hashes. Cobertura: 18 calibrados, 4 parciales, 23
  canarios, 4 fixtures, 3 manuales y 79 bloqueados. Los recibos v1 negativos se
  conservan como historial, no como diagnóstico vigente. Verificación: 110
  tests dirigidos, Ruff, 1392 tests backend y auditoría de los 40 artefactos v2
  sin JSON inválido, mezcla de versión ni patrones de secretos.
- M.7.1/`architect` completa 6/6 en Sol y 6/6 en Gemini 3.1 Pro High.
  Los agregados enlazan seis fuentes exactas y hashes de respuesta; cobertura
  recalcula identidad, rol, caso, semilla, versión, resultado y hash, y un test
  demuestra que manipular una muestra degrada el par a `partial`. Ambos pares
  quedan `calibrated` como calidad por rol, pero `default_change_allowed=false`
  hasta M.7.4. El snapshot 2026-07-23 pasa drift 6/6 y deja 10 calibrados,
  5 parciales, 30 canarios, 4 fixtures, 3 manuales y 79 bloqueados. `lead`
  permanece fuera: Sol 4/6 y Pro High 5/6, visibles como diagnóstico.
  Verificación: 38 tests dirigidos, Ruff y 1386 tests backend verdes.
- M.7.1 queda dividido en unidades verificables y dispone de un harness común
  para Sol y Gemini 3.1 Pro High: cinco roles exactos, dos familias causales y
  tres semillas por familia, sin extrapolar `lead` a aliases. La primera unidad
  viva (`lead`) produce resultados negativos útiles: Sol 4/6 por omitir la
  ventana de 10 minutos en dos incidentes; Pro High 5/6 por omitir el aceptador
  en una migración. Ninguno se calibra ni autoriza defaults. Los recibos
  pre-fix se preservan como diagnósticos; el juez nombra anclas ausentes y puede
  reevaluar sin repetir inferencia. Codex 0.145.0 expone tokens de suscripción;
  Antigravity 1.1.5 los mantiene unknown.
- M.6.3 y el bloque completo M.6 quedan reauditados y cerrados con doble check.
  La intención owner heredada se vuelve a vincular al candidato canónico incluso
  si perfil/modelo no cambian; onboarding deja de confiar en el candidate ID de
  React. Filas, propuestas o altas manipuladas fallan antes de mutar y un
  `default` sólo nace desde snapshot M.7 sellado. Evidencia: 240 tests dirigidos,
  Ruff, TypeScript, ESLint, build, 8/8 E2E y 1378 tests backend globales.
- M.6.2 reauditado y cerrado con doble check. Create/PATCH de agentes validan
  ahora las capabilities efectivas, no sólo las declaradas aparte por el
  cliente; alta directa y quorum transportan `issue_id`; proposal inicial,
  quorum automático y liveness heredan profile, criticidad, data class y tools
  antes de elegir. Los `[0]` restantes son exclusivamente probes manuales y el
  GET legacy no tiene consumidores productivos. Evidencia: 275 tests dirigidos,
  Ruff, TypeScript, ESLint, build, 8/8 E2E, smoke Playwright Python sin errores
  de página y 1372 tests backend globales.
- M.6.1 reauditado: la lectura de presupuesto diario es read-only y falla
  cerrado; una SQLite ausente/corrupta conserva gasto desconocido en vez de
  inventar cero. Cuota o presupuesto desconocidos dejan el gate automático en
  `null`, mientras solo agotamiento/límite observado bloquea la elección manual.
  El contexto une capabilities de toda la ascendencia de la issue y conserva la
  criticidad más cercana. Economía solo cambia con política owner completa y
  normalizada; los empates explican evidencia, calidad, magnitudes comparables o
  identidad canónica. Evidencia: 113 tests dirigidos, build/lint, 4 E2E del
  selector, 8/8 E2E frontend, smoke Playwright Python y 1367 tests globales.
- Cerrada la mutación silenciosa residual de M.6.2: review cross-provider de
  criticidad alta y recovery cross-adapter ahora proponen una asignación
  contextual mediante interaction durable y bloquean sin tocar `agents`.
  Accept revalida el catálogo vivo y persiste `owner_explicit`; reject conserva
  el bloqueo; cambios manuales concurrentes válidos prevalecen y alternativas
  que rompen el gate se rechazan. Las resoluciones son transiciones
  deterministas exentas de gates de inferencia. Evidencia dirigida: 10 tests.
- Retirado el último consumidor productivo de
  `GET /api/user-adapters/models`: onboarding, Equipo y hiring derivan ahora su
  estado auxiliar del mismo `POST /api/model-catalog/selection` que
  `ModelRoleSelector`, y solo cargan el perfil actualmente asignado. El GET
  permanece como compatibilidad externa sin gobernar decisiones; el probe vive
  separado en `POST /api/user-adapters/test`. TypeScript y ESLint verdes; E2E
  confirma al menos un POST contextual y cero requests al GET legacy.
- Preflight M.7 vivo regenerado sin inferencias: drift pasa sus 6/6 gates con
  Codex 0.145.0 y el inventario Antigravity actual. Cobertura: 46 modelos y 131
  pares modelo×rol; 8 calibrados, 5 parciales, 32 requieren canario, 4 fixture
  de tools y 79 permanecen bloqueados. La matriz pendiente queda separada en
  cohortes premium Codex+Antigravity, económica/tools y pools experimentales;
  estos últimos no deben bloquear ni autorizar el rollout de defaults.
  Reauditoría: Codex participa directamente en inventario+cobertura y los
  recibos promovidos se validan por contenido; Luna ausente o evidencia
  manipulada hacen fallar cerrado el preflight.
- M.1 reauditado: la proyección de identidad ya no permite que histórico viejo
  sobrescriba catálogo/config/discovery, asigna provenance por estado, rechaza
  identidades conflictivas y comparte una única enumeración con la API.
- M.2 reauditado: confidence falla cerrado ante provenance/evidencia material
  incompleta, ranking valida versión+rol+identidad y selección contextual consume
  las constantes canónicas. Smoke shadow: 46 candidatos/124 pares, cero auto.
- M.3 reauditado: el colector read-only deduplica aliases de una misma SQLite,
  cada fila publica los inputs exactos y el auditor recalcula hash+score aunque
  se vuelva a sellar el payload exterior. Snapshots rechazan versión/rol
  explícitos contradictorios y conservan compatibilidad con filas envolventes.
  Auditor real: 46 candidatos, 124 pares, cero auto y cero fallos; 76 tests
  dirigidos, Ruff y 1360 tests globales verdes.
- M.4 reauditado: la caché del catálogo devuelve copias aisladas y ya no puede
  contaminarse por mutación de un consumidor; `/candidates` distingue el score
  base y enlaza el selector contextual vigente. OpenAPI, filtros, orden, detalle
  y shim legacy mantienen paridad. Smoke real: 48 candidatos, 12 perfiles, 13
  reviewer, 0 auto, 28 configurados, 20 no configurados y 5 bloqueados; 145
  tests dirigidos, Ruff y 1360 tests globales verdes.
- M.5 reauditado en navegador: detalle con foco confinado/restaurado, unknown
  visible como `—`, once estados filtrables, tarjetas con configurados/verdes y
  metadata de M.6 vigente. Los fixtures Modelos y orientación consumen ya el
  POST contextual actual. Build/lint verdes, 3 E2E M.5 y 7/7 frontend; capturas
  desktop/móvil inspeccionadas y smoke Python Playwright con `networkidle`, foco
  correcto y cero errores de página; 1360 tests backend globales verdes.
- El informe económico por entrega/proyecto sigue deliberadamente sin
  construirse: el proyecto activo no aporta volumen y el nuevo auditor read-only
  `scripts/audit_cost_report_readiness.py` falla cerrado hasta que una misma
  SQLite tenga cinco entregas terminales por perfil y 80 % de cobertura de
  latencia, `cost_events` y calidad con provenance. El inventario revisa 71 DB,
  audita 70 y encuentra cero proyectos listos; aunque hay 2 entregas terminales
  `full_team`, 9 `lead_quorum` y 8 `solo_lead`, ninguna DB contiene más de una
  del mismo perfil. No sumar semillas inconexas. Recibo:
  `benchmarks/results/cost_reporting/cost-report-readiness-v1.json`.
- Tres primeros bloques de la validación reabierta de paralelismo completados.
  `dispatch_candidate_decisions` persiste cada candidato considerado en modo
  secuencial/paralelo con raíz, pool efectivo, work slot, primera readiness
  observada y razón estable de selección/rechazo. El loop secuencial fotografía
  el prefijo de cola antes de reclamar y distingue `sequential_mode` de
  dependencia/checkout. `audit_parallel_channels.py` v2 consume raíz/pool/work
  slot exactos, separa espera total/lista/paralelizable, deduplica por wakeup y
  declara cobertura y calidad `exact`/`partial_exact`/`approximate`. Sólo la
  primera puede abrir el trigger. El recibo v2 mantiene las siete DB históricas
  como aproximadas y sin trigger. El A/B hermético del `HeartbeatLoop` clona
  cuatro raíces/pools, restringe dos roles a un work slot, solapa sólo Engineer
  con dos scouts, aísla un fallo intencional y deja ambos brazos con estados
  terminales idénticos y cero huérfanos. El recibo niega cualquier conclusión
  de rendimiento. Ahora toca obtener un trigger vivo multi-raíz/multi-pool antes
  de consumir modelos. El inventario read-only ya examina automáticamente todo
  `runtime`: 71 DB descubiertas, 70 auditables, una vacía, cero errores y cero
  provenance exacta porque las runs retenidas son anteriores a la instrumentación.
  El trigger queda correctamente abierto y el A/B vivo bloqueado; no crear una
  señal sintética para cerrarlo. El default continúa secuencial y el flag opt-in.
  Verificación completa: `1200 passed` en 154,36 s; Ruff dirigido limpio.
- OpenCode server permanece experimental. El A/B de transporte v1 con DeepSeek
  pasa 3/3 direct y 3/3 attached, conserva seis sesiones aisladas y reduce la
  mediana 7,50→2,92 s con tokens equivalentes. El servidor está autenticado en
  loopback y termina sin procesos residuales. El canario SDK v1 observa `busy`,
  confirma aborto de servidor en 260 ms, retorno a `idle`, health, recuperación
  posterior, borrado de sesión y teardown. El SDK oficial 1.18.4 queda probado
  en una semilla; JSON Schema devuelve `StructuredOutputError` pese a texto JSON
  correcto. El fault injector suspende el proceso nativo, detecta health colgado
  en 532 ms y recupera mismo puerto, ID y sesión `idle`; el marcador posterior
  completa en 6,172 s. El fixture MCP confirma `initialize`, `tools/list`, deny
  por namespace, allow exacto y reap de ambos procesos. Producción sigue efímera
  tras cerrar la evaluación: memoria/override/contaminación pasa 3/3 con seis
  IDs únicos e historiales limpios, pero el mismo JSON Schema falla en los cinco
  modelos Zen gratuitos (`StructuredOutputError`, sin `info.structured`). No se
  construye supervisor para un transporte que incumple el contrato de cierre.
  Recibo final: `opencode-session-isolation-v1.json`. Verificación final:
  `1182 passed` en 128,71 s el 2026-07-22.
- Consolidación Git completada el 2026-07-21: runtime/control plane, evidencia
  de calibración y documentación se separaron en `1b3650e`, `66304c8` y
  `c695661`; junto con los commits locales previos se publicaron 16 commits en
  `origin/master`. El barrido no encontró secretos ni artefactos runtime y la
  suite previa al cierre terminó con `1161 passed`.
- Cerrada la cimentación de P0.3: `aiteam.policies` normaliza aliases, tier y
  estado de roles sin reescribir proyectos; `worker` es Tier 3, QA es un gate
  condicional y `test_runner` continúa determinista. La identidad de routing
  separa organización, vendor de modelo, perspectiva, transporte y pool de
  capacidad. Quorum y review crítico ya no cuentan Codex+OpenAI/GPT como dos
  perspectivas, aunque sus cuotas puedan seguir separadas. Los perfiles custom
  conservan metadata de roles, datos, workspace, MCP y salida estructurada.
  Verificación del estado completo: `1237 passed` el 2026-07-22.
- Sonnet 4.6 es ahora el modelo automático de Engineer dentro de Antigravity;
  Flash High conserva review/QA. En tres semillas de `cli_conversor`, ambos
  pasan 9/9 ocultos, pero Sonnet cierra 3/3, queda Ruff limpio 3/3 y tarda
  51,14 s medianos frente a 2/3, 1/3 y 105,48 s de Flash. El agregado v3 usa
  `benchmark_integrity.audit_ab_series`; no atribuye tokens ni coste API.
- El benchmark reveló envelopes distintos por modelo en `agy 1.1.5`. El parser
  soporta ahora ops limpios, `text + ops` y JSON seguido de ruido, priorizando
  siempre los ops estructurados. Los intentos previos fallidos se conservan como
  diagnóstico de transporte y no entran en la matriz de calidad.
- Calibración estructural Antigravity 1.1.5 completada con 27 runs stateless y
  tres muestras por comparación. Se detectó y corrigió que review debía usar
  Flash High —no Flash Medium— como baseline vivo. Pro High conserva Lead y
  Flash Low conserva scout por empate de cobertura con mucha menor latencia que
  Opus/GPT-OSS. Sonnet 4.6 avanza a benchmark conductual de coding (+9,1 puntos
  medianos, +12,42 s) y Flash Medium a validación económica de review (empate,
  -1,48 s). Esa fase no cambió defaults por sí sola; el A/B conductual posterior
  es el que promociona Sonnet. `agy` sigue sin entregar tokens headless.
- Sesiones CLI persistentes evaluadas y descartadas por ahora. El A/B Codex
  GPT-5.5 de dos semillas conserva memoria/override/aislamiento, pero resume
  casi duplica tokens brutos (ahorro mediano `-99,75 %`) y solo reduce duración
  `3,74 %`. Antigravity 1.1.5 reanuda correctamente por conversation UUID
  obtenido mediante `--log-file`, pero no entrega usage comparable. Producción
  sigue stateless; IDs implícitos `--last`/`--continue` permanecen prohibidos y
  Claude no está instalado.
- Corregido el catálogo de Equipo para Antigravity 1.1.5: `agy models` devuelve
  11 slugs, no las antiguas etiquetas humanas. Las ocho opciones originales y
  tres Gemini 3.6 coinciden con Equipo; estas últimas siguen manual-only y
  probe-gated. Las etiquetas guardadas se normalizan antes de ejecutar sin
  perder el nombre legible en UI.
- Instrumento de benchmark endurecido antes de nuevas calibraciones:
  `scripts/benchmark_integrity.py` audita balance brazo×semilla, duplicados,
  contratos de evaluación, evidencia independiente, muestra, diversidad de
  providers, provenance, hard gates, estabilidad, mediana+rango y Goodhart. El
  harness de código sube a v4 y GPT-5.5; quorum añade profundidad estructural en
  paralelo al score léxico. La auditoría real acepta el 2×2 de checkout y niega
  una nueva conclusión en failover por rango de signo inestable y metadatos
  estructurales legacy ausentes, sin borrar su valor diagnóstico.
- Catálogos de modelos renovados con fuentes oficiales y disponibilidad real
  por adapter: OpenAI Sol/Terra/Luna, Anthropic Opus 4.8/Sonnet 5/Haiku 4.5,
  Gemini Pro 3.1 Preview/Flash 3.5/Flash-Lite 3.1 y opciones que `agy 1.1.5`
  enumera. Fable 5 queda manual por coste, retención y fallback; locales no se
  cambian si el modelo no está instalado/validado.
- Equipo presenta ahora un catálogo ejecutable por perfil: deshabilita modelos
  bloqueados, ausentes del runtime o rechazados como `model_unavailable`, y
  muestra la causa. El backend rechaza guardados inconsistentes y el hiring usa
  exactamente el mismo conjunto. Las runs completadas verifican el par
  perfil+modelo sin que un health check posterior borre la evidencia. El probe
  de `agy models` añadió la opción real `Gemini 3.1 Pro (Low)`.
- El primer canario Luna/auth quedó como diagnóstico de la instalación antigua:
  Codex CLI `0.128.0` no podía ejecutar el catálogo cacheado para `0.145.0`.
  El CLI ya está actualizado a `0.145.0`, el cache enumera Sol/Terra/Luna y un
  probe efímero read-only de Luna devuelve `LUNA_OK`. El A/B causal auth+queue
  ya terminó: GPT-5.5 queda como control histórico y Luna `medium` es el Tier 3
  activo de Context Curator.
- Lifecycle de modelos completado: `model_unavailable` bloquea la issue y crea
  una propuesta idempotente del mejor modelo ejecutable del mismo perfil,
  indicando cambios de familia/tier. Solo el owner puede aceptarla; la
  aplicación y reencolado son deterministas y no consumen otra llamada LLM.
  Rechazar mantiene el bloqueo, un cambio manual más reciente prevalece y la
  ausencia de fallback despierta al supervisor sin cambiar de adapter.
- FinOps distingue coste API de presión de cuota: suscripciones y local siguen
  en 0 céntimos marginales. `run_adapter_profiles` congela el perfil ejecutado y
  el snapshot de suscripción agrega usage, runs, duración y límites observados.
  Solo una política `subscription_quota` declarada por el owner habilita
  utilización/forecast; sin denominador conserva `capacity_unknown`. El cockpit
  lleva a Runs ante agotamiento observado o presión configurada.

- Nueva familia media reversible `inventory_snapshot_diff`: 20/20 siempre;
  `solo_lead` cerró 2/2 en una run y `full_team` 0/2 dentro de 12, con 2,92×
  tiempo/1,91× entrada medios. Se mantiene el default conservador del selector.
- Nueva familia frontend `accessible_checkout_form`: dos semillas 10/10 para
  `solo_lead` y `full_team`; solo cerró 2/2 en una run, equipo 1/2 en 10–12 runs
  y promedió 5,38× tiempo/6,61× entrada. La run abierta conserva continuación.
- El baseline de orientación frontend ya tiene un E2E Playwright hermético:
  Bandeja requiere 1 acción, cada perfil 1 y plan aceptado → tarea adjunta 2;
  Chromium termina el recorrido principal sin errores ni abandonos; dos probes
  adicionales validan abandono activo y explícito. La guía visible compara
  coste operativo y riesgo de los tres perfiles. No tratar este contrato como
  evidencia de adopción o claridad real. El backend consentido ya persiste solo
  sesión, flow, evento allowlisted y perfil canónico en SQLite; bloquea eventos
  sin consentimiento, soporta revocación/borrado y prohíbe texto, rutas e IDs de
  proyecto. Config ya ofrece opt-in, revocación, borrado y resumen; el cockpit
  instrumenta Bandeja, perfiles y plan → tarea. El E2E registra 9 eventos del
  recorrido y 3 adicionales en dos pruebas de abandono controlado, sin campos
  fuera de `flow`, `event` y `profile` opcional ni atribuir lectura o
  comprensión al clic. Las filas históricas `guidance_viewed` quedan fuera de
  los conteos vigentes sin borrado silencioso. Una sesión
  vacía tampoco se marca completada. La observación humana consentida conserva
  esa frontera. Su protocolo v1 ya está prerregistrado con ocho sesiones, dos
  estratos, órdenes contrabalanceados, rúbrica, gates y parada por privacidad;
  la enmienda previa a observación fija una fila participante×flujo y el auditor
  rechaza cambios post hoc. El template de resultados sigue vacío. Ahora toca
  reclutar/ejecutar la muestra sin exponer participantes a la UI o al protocolo
  de antemano. Los conteos no autorizan conclusiones de comprensión universal.
- El auditor de benchmarks separa conclusión de promoción. Ya no acepta un
  booleano de independencia si las clases de evaluador son solo léxicas, y una
  promoción nueva exige `constructs_not_measured` más riesgo de Goodhart. Los
  recibos legacy conservan valor direccional; el A/B conductual de Sonnet fue
  anotado con sus límites y mantiene `promotion_allowed=true` sin alterar scores.
- `orchestrator_evals` recorre descendientes al decidir si una raíz está
  stranded; un wakeup o interacción viva en un hijo mantiene viva la raíz.
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

- El bloque backend/pre-run de P0.3 ya está cerrado. La decisión pura de
  `aiteam.model_compatibility` se resuelve sobre asignaciones reales mediante
  `aiteam.compatibility_service` y gobierna bootstrap del Lead, create/update,
  hiring y propuestas editadas, reconcile, delegación, escalado, recovery,
  fallback y dispatch. Un deny manual devuelve HTTP 422; una configuración
  persistida inválida bloquea la issue, crea continuación owner y no consume el
  modelo. Equipo ya consume esa decisión con contexto, conserva las opciones
  visibles y deshabilita perfil/modelo con la misma causa; el cache incluye rol,
  run profile, criticidad y clasificación. Catálogo y health también quedan
  separados por modelo: discovery autenticado demuestra presencia; solo un
  probe estructurado o una run completada marca `selectable`. Se conservan
  estados catalogued/verified/rate_limited/retired, y una ausencia de fallback
  crea continuación owner. JSON Object/Qwen queda endurecido con validación
  completa y un único repair que no puede modificar ops/status; Qwen sigue
  limitado a Tier 3 y criticidad media. La matriz hermética ya audita los 47
  modelos built-in —337 celdas positivas y 415 negativas—, paridad Equipo/API
  y probes exactos de onboarding. La telemetría de capacidad ya separa API free
  de suscripción: Groq persiste
  RPD/TPM observados en headers por modelo y Gemini queda sin porcentaje cuando
  el proyecto no aporta denominadores. Los canarios vivos de los tres run
  profiles están cerrados; el siguiente frente de P0.3 es calibrar OpenCode Zen
  y las promociones BYOK gratuitas por par exacto perfil+modelo+rol, siempre
  fuera de CI y solo cuando exista catálogo ejecutable demostrado.
  El primer bloque vivo encontró tres slugs Gemini 3.6 en Antigravity 1.1.5:
  High/Low aparecen en inventario pero fallan el submit, y Medium pasa review
  estructural 3/3 sin superar a 3.5 High. Permanecen manual-only y no
  seleccionables hasta un probe exacto; discovery no equivale a ejecución.
- Cerrado el canario durable de review Antigravity v4. Flash High y Gemini 3.6
  Medium rechazan el defecto, crean el fix mediante el Lead y aprueban la
  corrección en 3/3 semillas. Medium baja la mediana de 99,999 a 43,078 s, pero
  sin tokens ni denominador de cuota no desplaza el default. Los canarios vivos
  posteriores cerraron `solo_lead`, `lead_quorum` y `full_team`.
- Primer canario vivo de run profile cerrado: `solo_lead` con Antigravity Pro
  High completa en una run/54,656 s, materializa el archivo, pasa verificación
  de máquina y termina sin hijos ni trabajo vivo. En ese punto validaba 1/3
  perfiles; los resultados posteriores se describen a continuación.
- `full_team` vivo pasa en seed 3 con 12 runs/635,969 s y routing exacto: Codex
  GPT-5.5 Lead, Sonnet Engineer, Flash High Reviewer/Test Designer, Flash Low
  Scout y runner local. La raíz solo cierra después de pytest exit 0 y termina
  sin cola. Un intento previo descubrió que Antigravity Lead podía escribir el
  workspace directamente pese al deny de ops; los roles read-only Antigravity
  ejecutan ahora desde cwd efímero y reciben archivos solo por payload.
- `lead_quorum` cerró en seed 4 con 4 runs/305,7 s, Plan A y Plan B profundos,
  dos contribuciones válidas Codex GPT-5.5 + Antigravity Pro High, sesión
  `accepted` y raíz `done`. Las tres semillas anteriores degradaron por auditoría vacía,
  síntesis narrativa demasiado corta y AGENT-REPORT Codex inválido. La segunda
  sí obtuvo dos contribuciones cross-provider válidas. El prompt de Plan B ya
  exige explícitamente ≥300 palabras en `plan.narrative_markdown`; seed 4 valida
  esa corrección. Con `solo_lead` y `full_team`, los tres perfiles vivos quedan cerrados.
- Corregir a la vez la contradicción de transporte: los adapters API sí pueden
  materializar operaciones de archivo bajo RBAC; OpenCode Zen es el canal
  read-only. Las APIs gratuitas no tienen todavía MCP externo gobernado. La
  matriz provisional limita Nemotron a Lead/quorum/review de lectura,
  DeepSeek/MiMo y Gemini Flash/GPT-OSS 120B a review/QA, y North/Flash-Lite/
  Qwen/GPT-OSS 20B a scouts/curator hasta completar canarios. `task.md` contiene
  el orden, las rutas y la matriz E2E de cierre.

- OpenCode Zen Free queda integrado como perfil built-in read-only con catálogo
  descubierto por el CLI: Nemotron 3 Ultra (Tier 1), DeepSeek V4 Flash y MiMo
  V2.5 (Tier 2), North Mini Code (Tier 3). OpenCode `1.18.4` está instalado,
  reutiliza una sesión OAuth local y enumera además Laguna S 2.1 Free, visible
  como manual/probe-gated. El screening público de una semilla pasa transporte,
  contrato y usage con Nemotron, DeepSeek, MiMo y Laguna; North responde sin
  ops y no supera todavía el cierre durable. El canario durable v1 confirma que
  no hay promoción: Nemotron falla parseo, MiMo no crea el rechazo durable,
  North queda denegado por rol y DeepSeek completa solo 1/3. La matriz final
  Laguna vs DeepSeek es exacta 2×3: Laguna completa 0/3, con dos parse errors y
  un approve timeout; no hay candidato manual ni cambio de default. El primer
  preflight Laguna falló `model_not_catalogued`; quedó corregido declarando la
  opción `requires_probe` sin hacerla automática. No presentar “integrado”
  como gateway anónimo: Zen exige login/API key y su oferta gratuita es temporal
  y solo apta para datos no confidenciales. Ver
  `docs/MODELOS_GRATUITOS_OPENCODE.md`.
  El transporte ya falla cerrado sin `--auto`, impone allowlist MCP positiva y
  registra tokens/caché/razonamiento/sesión para presión de cuota con coste
  marginal cero. Sigue limitado a lectura: permisos de tools no son un sandbox.
  La evaluación server/SDK ya está cerrada con decisión negativa; no promociona
  OpenCode ni habilita Engineer.
  La ruta complementaria BYOK ya incluye perfiles separados para Gemini Free y
  Groq Free, vault local, health, modelos, usage y cuota. GPT-OSS usa schema
  estricto; Qwen JSON Object Mode validado. GitHub Models/OpenRouter exacto
  quedan como siguiente expansión, nunca como router aleatorio. La auditoría
  local del 2026-07-22 no encuentra keys y los tokens de `gh` no incluyen
  `models:read`; no se crean perfiles hasta ejecutar catálogo y schema reales.
  El governor ya normaliza `models.github.ai` y `openrouter.ai`; Groq conserva
  RPD/TPM desde headers. No reemplazar Zen, porque DeepSeek/MiMo directos son de
  pago y Cohere/NVIDIA no aportan capacidad gratuita estable demostrada.

Objetivo, pendientes, orden de ejecución y criterios de cierre viven únicamente en
`task.md`. El drift de catálogos ya tiene owner, cadencia mensual+evento, auditor
determinista y recibo 3/3; el bloque activo vuelve a las calibraciones que no
dependan de credenciales ausentes. No
mantener una segunda lista de tareas en este handoff.

La auditoría independiente del 2026-07-22 reejecutó 1211 tests, tres canarios,
matriz, preregistro, Playwright, build y lint en verde. Confirmó los cierres de
autoridad/MCP/concurrencia y abrió solo mantenimiento: medir primero la
amplificación acotada de `dispatch_candidate_decisions`, diseñar retención por
tabla, registrar frescura de calibración y publicar el bloque local. El E2E ya
estaba en el gate; ahora `build` y `lint` son también obligatorios cuando cambia
frontend. No aplicar un TTL global a telemetría durable o datos consentidos.

El benchmark posterior cerró el hallazgo de crecimiento sin habilitar poda.
Tres repeticiones 1/25/100/1000 verifican la fórmula acotada; a 1000 registra
24.700 decisiones, 20,60 MB, 8,30 ms medianos por planificación y consultas
≤0,030 ms. Todos los thresholds pasan y el recibo devuelve
`retention_implementation_allowed=false`; conservar el log aditivo y repetir
solo ante cambios de schema, índices, scheduler o límite de snapshot.

La frescura de calibración queda implementada sin mezclarse con health. El
registro canónico contiene tres pares promovidos: Luna/`context_curator` y
Sonnet 4.6/`engineer`+`software_engineer`, con fecha, versión y recibos. El
auditor de drift pasa 6/6 gates con Codex 0.145.0. El A/B causal auth+queue deja
GPT-5.5 sin override de esfuerzo queda como control histórico 6/6 y promueve
Luna `medium` 6/6 como Tier 3 para `context_curator`; Luna original 3/6 y prompt
v2 4/6, también sin override, quedan como fallos preservados y no como evidencia
causal de un esfuerzo `low`.
El inventario vivo de cobertura conductual separa la matriz estructural de la
evidencia real: 47 modelos/124 destinos semánticos, con 8 calibrados, 17
parciales, 17 diferidos hasta cambio material, cero canarios o fixtures
pendientes, 3 manuales y 79 bloqueados. Los lotes quedan divididos por
Codex, Antigravity, local, OpenCode y APIs bloqueadas en `task.md`; no se retira
un modelo solo por ser antiguo.
El primer bloque Codex Tier 3 alineó `worker` como rol read-only de reporting y
cerró el hueco que permitía a worker/scouts/test runner marcar `done` sin
`AGENT-REPORT`: hay una corrección y después bloqueo+escalado durable. Luna
`file_scout` low/medium conserva 3/6 anclas; Luna `worker` conserva 7/7, pero
low usa un `result` inválido y medium omite el informe. Los cuatro screenings
quedan como diagnóstico negativo, no como promoción ni evidencia parcial.
Luna `web_scout` completa además 3 semillas sobre MCP gobernado: 3/3 usan la
tool read aprobada, respetan la denegación write y conservan 8/8 anclas; 2/3
cierran en una run. Se registra `partial`, no promoción.
Terra `medium` queda calibrado exactamente para Reviewer (3/3 ciclos durables;
mediana 64,0 s), Engineer (27/27 tests ocultos, Ruff limpio, 3/3; mediana
62,921 s), QA (3/3 ciclos adversariales, 30/30 checks; mediana 116,048 s) y Test
Designer (3/3 suites, 24/24 checks, 15/15 mutantes; mediana 73,172 s) y MCP
Operator (3/3, 36/36 checks de allow/deny, health y recovery; mediana 42,359 s).
Codex aporta usage comparable de suscripción, no coste API. Los cinco pares
Terra tienen capacidades explícitas y evidencia exacta; no se extrapolan.

El siguiente objetivo transversal P0.M está ya registrado en `task.md` y en las
fases 5.7/contrato de orquestación: catálogo universal de todos los proveedores
y modelos, estadísticas y score versionado por rol, pestaña `Modelos` y ranking
global en creación/edición de equipos. El `role_score` actual sigue siendo una
heurística transitoria de tier+caps+`best_for`; el nuevo selector debe aplicar
primero hard gates de adapter/modelo/compatibilidad/evidencia, funcionar en
shadow y persistir la explicación antes de gobernar plazas nuevas. No debe
mutar agentes existentes ni convertir score en autoridad.
QA condicional, Test Designer y MCP Operator recuperan skills propias alineadas
con el runtime. QA requiere escritura acotada a tests adversariales; por ello se
retiraron dos recomendaciones QA de OpenCode sin retirar sus modelos.
El bloque completo, incluido `65eb862`, quedó publicado en `origin/master`
mediante `c9dd733` tras 1229 tests en verde y revisión de secretos/diff.

## Riesgos conocidos

- `RunExecutor` concentra muchas políticas; el orden de preflights y gates requiere tests dirigidos.
- El gate profundo valida cobertura y presupuesto, no verdad ni calidad semántica. Debe seguir calibrándose con `benchmark_quorum_plans.py`; no elevar más thresholds basándose en una sola familia.
- Corregido P0.1/F1: `loop-health` conserva actividad reciente como telemetría y solo eleva runs/wakeups con más de 30 minutos; una run activa también cuenta como continuación durable de su raíz. Hay tests separados para trabajo reciente y estancado.
- Runtime MCP mínimo completado con contrato provider-neutral: el rol conserva autoridad y el adapter solo traduce grants. Se exige owner approval, versión, `initialize` stdio, health vigente, rol+`external_mcp` y recibo `tool_access`. Codex usa overrides efímeros, OpenCode configuración inline y allowlist exacta, y Claude `--strict-mcp-config`; Antigravity registra deny hasta soportar aislamiento por run, sin cambiar de Lead ni hacer fallback. Fuentes shell/`npx -y` no se ejecutan.
- Cerrado el ciclo MCP operativo: `readOnlyHint` no concede acceso; Config permite probar, aprobar tools, retirar y reactivar; health caduca en 24 h y el heartbeat prueba como máximo uno vencido por tick, con backoff y retiro al tercer fallo. Contratos rechazados, retirados o ya existentes suprimen propuestas equivalentes. Ejecutable, argumentos y scripts quedan sellados por digest, y cada adapter impone la allowlist o deniega el servidor.
- Detección MCP completada sin auto-instalación: `capability_gap` y bloqueos no verificables generan una sugerencia durable al Lead; señales débiles exigen dos runs de la misma capacidad. El detector no combina huecos distintos, no pisa wakeups existentes y solo el Lead puede elevar la sugerencia al gate del owner.
- Skills aprendidas gobernadas completadas con contrato neutral al proveedor: solo el rol Lead propone y debe adjuntar evidencia; la propuesta no se inyecta hasta aprobación explícita. Hay límites de cantidad, tamaño y presupuesto activo, provenance preservada y controles owner para editar, activar, retirar o borrar. Las directivas del usuario prevalecen por contrato y por orden de prompt.
- P2 de auto-extensión queda completo con un catálogo inicial de tres descriptores oficiales. El catálogo es informativo y rellena propuestas Lead por `catalog_id`; no instala ni aprueba. Los contratos canónicos no admiten overrides y siguen pasando interacción owner, health, digest y allowlist antes de cualquier grant.
- El plan ya tiene contrato durable neutral al proveedor: `aiteam.plan.v1+json`
  vive en las revisiones existentes de `issue_documents`, explicita
  accountability, evidencia, riesgos, rollback, escalado y continuidad, y el
  cockpit lo consume como estructura. Un `run_id` solo puede revisar el plan si
  corresponde al Lead asignado a esa issue. Los comentarios ya no son una vía
  implícita de escritura y la API exige estructura para nuevas revisiones. El
  Markdown de documentos, builtins y adapters antiguos sigue funcionando como
  shim transitorio y se identifica como no estructurado.
- El supuesto hueco de identidad del Lead en quorum está cerrado en persistencia:
  `accept_quorum_synthesis` enlaza run, revisión e issue, exige
  `run.agent_id == issue.assignee_agent_id` y conserva idempotencia/inmutabilidad
  terminal. `test_persistence_rejects_second_team_lead_not_assigned_to_issue`
  protege ya el escenario nominal que antes faltaba; no duplicar la política en
  el executor.
- El context curator persiste ahora Markdown más un índice causal v1. Producción
  valida provenance y completitud relacional, no verdad: accountability requiere
  owner/deliverable/accepted_by, escalado metric/threshold/window/action y una
  opción descartada reason. El Markdown conserva el gate histórico ≤30 %; el
  índice tiene cap separado de 4 KiB y la rúbrica oculta lee ambos. Los primeros
  spot-checks reales pasan auth y queue 9/9 en una run cada uno, pero el artefacto
  total ocupa 47,56 % y 54,37 % al contar JSON/UUID. Registrar ese overhead y no
  presentar estructura como retención semántica demostrada universalmente.
- Claude subscription recibe el wake payload variable por stdin; schema y
  system prompt permanecen como argumentos. Esto evita superar el límite de
  `CreateProcess` en Windows al crecer los contratos estructurados, sin cambiar
  parsing, sandbox ni transporte MCP por run.
- El contrato operativo del context curator ya no vive en `RunExecutor`:
  `aiteam/context_curator.py` posee construcción del slice, presupuesto del
  trigger, validación/persistencia, offsets parciales y recovery acotado. El
  executor solo materializa la issue delegada y consume la transición devuelta;
  no mantiene una segunda definición de ratio, rangos o reintentos.
- La rúbrica `multitenant_authorization_v1` produjo un falso −8,69 en la tercera semilla: no reconocía equivalentes válidos como «frontera de enforcement», `policy checks por recurso`, `Deny-by-default` o «pruebas negativas». La v2 añade esas anclas y tests dirigidos; los resultados v1 y v2 se conservan separados. El 100→100 de v2 es efecto techo, no prueba de que Plan B sea idéntico ni de calidad perfecta.
- La calibración P0 de quorum ya tiene criterio y muestra mínima en las dos familias pendientes: tres sesiones aceptadas por familia, dos proveedores válidos, provenance completa, degradaciones fuera del A/B y mediana+rango. Failover: mediana `+6,52`, rango `-8,70..+8,70` (n=4). Multi-tenant v2: mediana `+8,69`, rango `0..+8,70` (n=3), pero solo 2/3 Plan B superan hard gate. Se mantienen thresholds; `accepted` en SQLite prueba cierre del protocolo, no aprobación semántica externa.
- Anthropic API debe recibir `quorum_review` completo: su builder genérico resumía el payload a 800 caracteres y ocultaba el contrato. Las auditorías quedan acotadas a 1-3 findings para preservar profundidad sin agotar el cierre JSON/AGENT-REPORT; la semilla multitenant posterior verificó ambos proveedores al primer intento.
- Un quorum de un senior es una degradación de redundancia aceptada por disponibilidad, no equivalente empíricamente a dos proveedores. Exponerlo claramente en UI/telemetría si se usa con frecuencia.
- El bloque principal quedó consolidado en `codex/orchestration-hardening`; `.claude/skills/aiteams-frontend/` permanece sin seguimiento y fuera de los commits por origen no atribuido.
- La telemetria de usage de `antigravity_subscription` debe verificarse antes de comparar costes: `agy --print` autentica y responde, pero no entrega usage comparable en su salida normal.
- Revalidado el 2026-07-21 con `agy 1.1.5`: `--help` y el changelog no ofrecen salida headless estructurada de tokens por run. La cuota existe en el TUI, pero no es un recibo atribuible; no parsear almacenes internos ni fabricar estimaciones.
- Antigravity CLI 1.1.4 es un segundo proveedor operativo para quorum: existe una sesión aceptada cross-provider y una contribución válida con Gemini 3.1 Pro High. El adapter transporta payloads largos mediante archivo temporal autorizado, conserva plan+sandbox y normaliza solo los envelopes observados. Sigue sin usage/cost_event comparable y el cumplimiento de `AGENT-REPORT` presenta varianza en ambos proveedores.
- El blueprint debe conservar el rol semántico `quorum_auditor` aunque la sub-issue sea `reviewer`; de lo contrario el selector baja erróneamente a Flash. Pro es el modelo canónico de hiring para Antigravity quorum.
- Nuevas anclas reales: `config_redactor` empata 3/3; `tenant_authorizer` favorece a Codex directo 4/5 frente a `full_team` 2/5; `release_notes_indexer` empata 7/7 y `deployment_wave_planner` empata 16/16 en dos semillas. En deployment, equipo promedia 3,73× la entrada y 4,39× el tiempo de solo, converge 1/2 y conserva accountability independiente; esa garantía puede ser requerida aunque no mejore el juez, pero no es una ventaja de calidad demostrada.
- `benchmarks/results/quorum-sqlite-seed-1.json` es evidencia de una run incompleta, no un resultado A/B: Plan A obtuvo 91,3 % y el segundo auditor falló con `subscription_cli_not_found`.
- `benchmarks/results/quorum-provider-failover-local-seed-1.json` es una segunda evidencia incompleta pero útil: Plan A obtuvo 78,26 %, Codex aportó una auditoría válida y Qwen 32B consumió 4.100 tokens de entrada/164 de salida en dos intentos sin cumplir `AGENT-REPORT`; la sesión terminó `degraded` con escalado durable. El runtime reintenta una sola vez, excluye ese reintento del guard de evidencia idéntica y cancela wakeups sobrantes al degradar.
- `benchmarks/results/quorum-provider-failover-gemma-seed-1.json` confirma que Gemma 4 local tampoco es todavía un segundo auditor utilizable: Codex produjo el único aporte válido; Gemma terminó primero `skipped` y después `failed` por selección de herramienta. El runtime continúa ahora auditores `skipped`/`failed`, normaliza fallos declarados sin código a `agent_reported_failure` y degrada/escalada de forma durable al agotar el reintento. Es evidencia de failover, no una semilla A/B aceptada.
- Anthropic API ya es segundo proveedor operativo. `quorum-provider-failover-anthropic-seed-2.json` mejora 60,87→65,22 (+4,35); seed 3 regresa 86,96→78,26 (−8,70). Ambas sesiones terminaron `accepted`, con dos aportes provider-diversos y 14 céntimos atribuibles al auditor Anthropic. Seed 1 es un diagnóstico incompleto: el health ranking eligió Anthropic también como Lead y agotó sus 4.096 tokens antes de crear sesión.
- Seed 5 de provider failover es diagnóstica, no A/B: Plan A puntúa 91,30, Anthropic aporta válido por 29 céntimos y Codex subscription falla por cuota agotada. El error ya no se colapsa en `subscription_cli_nonzero_exit` ni consume un reintento inmediato; degrada con `auditor_provider_usage_limit` y wakeup durable al Lead.
- Seed 4 mejora 91,30→100 (+8,70), termina en cuatro runs sin intervención y
  atribuye 19 céntimos al auditor Anthropic. Esa evidencia histórica se obtuvo
  con Sonnet/Opus 4.5; la política actual selecciona Opus 4.8 para Lead/quorum,
  Sonnet 5 para Tier 2 y Haiku 4.5 para Tier 3. Los modelos nuevos aún requieren
  calibración equivalente antes de atribuirles una mejora.
- Seed 6 de provider failover mejora 82,61→91,30 (+8,69), supera el hard gate y cierra en cuatro runs: 89.588 tokens de entrada, 10.555 de salida, 237,1 s y 28 céntimos. La nueva seed 4 multi-tenant mejora 82,61→91,30 (+8,69), con ambos auditores válidos y 29 céntimos, pero conserva el fallo duro `tenant_boundary`; el root queda `in_progress` con wakeup durable y `orchestrator_evals` confirma liveness sano.
- El apartado Equipo aprovisiona ahora Quorum Auditor 1/2 mediante un endpoint canónico idempotente, no mediante un prompt `full_team`; conserva los IDs que consume el runtime y oculta las tarjetas cuando ya están contratados.
- Corregido un hueco descubierto por seed 2: cuando Codex entregaba `AGENT-REPORT` dentro de `add_comment`, la contribución se persistía después del auto-wakeup y el gate quedaba `reviewing`. Cada contribución válida evalúa ahora inmediatamente la continuación durable.
- El bootstrap de quorum asigna ahora proveedores distintos por construcción cuando existen perfiles suficientes; antes ambos auditores elegían silenciosamente el mismo primer perfil senior y la diversidad solo fallaba al evaluar el gate.
- `benchmarks/context_quality/auth_migration_*` aporta el primer canario causal: la referencia conserva 9/9 anclas obligatorias con ratio 26,57 %. El primer intento (35,84 %) fue rechazado por presupuesto, confirmando que retención y compresión son gates independientes.
- El canario causal v1 añade dos recibos reales:
  `context-curator-auth-codex-causal-v1-seed-3.json` y
  `context-curator-queue-codex-causal-v1-seed-1.json`. Ambos conservan 9/9,
  cierran al primer intento y separan ratio Markdown de overhead del índice.
- El QuorumStepper fue comprobado contra esa SQLite real: distingue ahora `degraded` de “No requerido”, expone `1/2` aportes, gate pendiente, causa y provenance del aporte válido. Evidencia visual local en `output/playwright/quorum-stepper-degraded.png` (no versionada).
- El benchmark ya tiene resultados versionados y juez oculto aislado. El harness
  de código v4 declara suite conductual oculta, Ruff y evaluación estructural
  independiente; `scripts/benchmark_integrity.py` impide concluir con matrices
  brazo×semilla incompletas, evidencia no comparable o quorum sin muestra,
  provenance, hard gates y signo estables. La serie histórica
  `accessible_checkout_form` supera el contrato 2×2; `provider_failover` no lo
  supera (cuatro sesiones aceptadas, dos incompletas y signo inestable).
- La higiene local quedó endurecida después de encontrar 11,1 GB en `.pytest-workspace-tmp`: `pytest_local.bat` y el wrapper estable crean sesiones aisladas, limpian en un proceso posterior al cierre de handles SQLite, desactivan cache/bytecode y preservan el exit code de pytest. `scripts/cleanup_test_artifacts.py` permite el barrido manual.
- Los documentos históricos de migración pueden contener estados de fase ya superados; el banner del documento indica cómo leerlos.
- La portabilidad y el soporte poliglota ya tienen contrato explícito en P0.I y
  `docs/INSTALLATION_AND_INTEGRATION.md`: Windows es hoy el único bootstrap
  verificado. Linux/macOS, `doctor --json`, releases y cada ecosistema requieren
  fixtures/recibos antes de anunciarse como soportados; Git transporta código,
  nunca secrets, sesiones, `runtime/`, `venv/` o `node_modules/`.
- P0.M.1 está cerrado con `model_catalog_identity_v1` en
  `aiteam/model_catalog_projection.py`: identidad operacional separada por
  perfil/canal/pool, cuatro fuentes de inventario y once estados ortogonales con
  provenance. No cambia routing; M.2 añadió el scorer por rol y M.3 conectó
  runs/SQLite a la proyección.
- P0.M.2 está cerrado en shadow con `model_role_score_v1`: pesos 40/15/15/20/10,
  confidence separada, unknowns como rango, economía específica por canal, 13
  hard gates y desempate estable solo sobre unidades comparables. No está
  conectado aún a defaults; M.3 aporta el read model y provenance real.
- P0.M.3 está cerrado con `model_catalog_read_model_v1`, colector SQLite
  read-only, auditor CLI y snapshots hasheados/idempotentes. El baseline local
  proyecta 46 candidatos/124 pares, 0 automáticos, 0 fallos y 20 warnings de
  cobertura. Métricas runtime crudas nunca se normalizan implícitamente y los
  inputs de benchmark no pueden anular hard gates. M.7 conectará la persistencia
  cuando active defaults.
- P0.M.4 está cerrado con `/api/model-catalog` y
  `/api/model-catalog/candidates`: filtros globales, agrupación por perfil/canal,
  ranking por rol, breakdown/confianza/métricas/recibos y deny reason proceden
  del mismo read model, sin activar routing. El endpoint legacy por perfil
  conserva campos y compatibilidad contextual pero delega identidad, score y
  orden. El smoke con la DB activa devuelve 48 candidatos, 12 perfiles/canales,
  13 reviewer y 0 auto-elegibles.
- P0.M.5 está cerrado con una pestaña global `Modelos`: proveedores/canales,
  filtros, matriz modelo×rol y ficha lateral de score, confianza, evidencia,
  receipts, estados y hard gates. El read model expone gobernanza redacted del
  perfil y economía para evitar fuentes paralelas. El E2E demuestra que un
  bloqueado con score 95 no adelanta al elegible, además de loading/error/empty,
  adapter verde y responsive. React consume el orden backend y no calcula score.
  M.6 está en curso: existe `POST /api/model-catalog/selection`, con gates
  contextuales antes del ranking, pares sin score visibles, score base inmutable
  y ausencia explícita de default cuando nadie es auto-elegible. Un
  `ModelRoleSelector` compartido ya sustituye los selectores divergentes en
  onboarding/bootstrap, edición, hiring propuesto, alta directa de Equipo,
  quorum y fallback. La composición backend única vive en
  `contextual_model_selection`: deriva issue, tools, cuota y presupuesto y
  alimenta tanto el POST como lifecycle. Quorum conserva diversidad de
  perspectiva y recovery prohíbe cruzar de adapter desde su selector.
  La elección del componente ya guarda `model_selection_intent_v1` dentro de
  `adapter_config`; reconcile conserva intactos perfil, modelo, candidate id y
  modo `owner_explicit`. Create/update y aceptación de hiring validan ya la
  identidad canónica, rechazan IDs falsificados y un E2E cubre guardado → recarga
  de estado → recarga de UI. `mode=default` solo nace desde un snapshot M.7
  sellado y auto-elegible; ningún cliente owner puede fabricarlo.
  M.6.1 deriva ahora cuota/capacidad y presupuesto desde SQLite/configuración:
  agotamiento bloquea antes del ranking, unknown sigue unknown y solo una
  política de cuota completa puede sustituir economía con provenance. Cuatro
  E2E cubren orden, deny por cuota, elección owner, reload, explicación de
  empate y ausencia segura de default. Las tools específicas se unen desde la
  issue y todos sus ancestros mediante `issue_compatibility_context`.
  Onboarding, alta directa, quorum y fallback
  conservan el modelo exacto elegido con `owner_explicit`. M.6.2 queda pendiente
  solo de retirar defaults residuales/primer-modelo y delegar gradualmente el
  endpoint legacy por perfil. M.6.3 distingue ya `owner_explicit` de `default`:
  sólo M.7 crea el segundo desde snapshot sellado y ningún cliente owner puede
  fabricarlo; falta únicamente completar el reload visual específico de default.
  M.7 dispone ya de evaluación shadow durable y endpoint explícito. El smoke
  local persistió seis snapshots idempotentes de 48 candidatos, obtuvo seis
  `no_winner` y confirmó cero cambios en `agents`. El constructor de
  `mode=default` recalcula el hash y exige snapshot `auto_applied` con ganador
  elegible. `AITEAM_MODEL_DEFAULT_ROLLOUT` aporta `shadow|recommend|auto`, con
  fallback de valores inválidos a shadow y rollback sin mutar agentes. Las
  cohortes conectadas son plazas dinámicas de issues/liveness, bootstrap Lead,
  Tier 3 y quorum. Recommend conserva el selector vigente y auto aplica solo el
  ganador sellado; sin ganador Tier 3/quorum persisten `default_unresolved` sobre
  `role_builtin`, protegido frente a reconcile, y bootstrap Lead aborta+limpia.
  Quorum excluye perspectivas ya usadas cuando existen alternativas y confirma
  cada alta antes del snapshot siguiente para evitar locks SQLite; ensure sigue
  siendo idempotente ante caída parcial. Los canarios herméticos cubren dos
  canales, no-winner, pin owner y persistencia, pero falta la matriz viva completa.
  Verificación: 238 tests dirigidos y 1329 tests globales en verde.
- Prompts externos o antiguos que mencionen `AITEAM_AUTO_QUORUM` están obsoletos: el único disparador vivo es el perfil explícito `lead_quorum`.
- Windows puede retener handles de SQLite o temporales de pytest. El 2026-07-21
  se confirmó que `.pytest-workspace-tmp` y `.pytest-user-config-tmp` están
  ausentes; quedan dos directorios de `.tmp_pytest` del 2026-04-02 con ACL
  privadas que impiden enumerarlos. El intento de borrado delimitado fue
  rechazado por la política del entorno antes de ejecutarse; no se eliminó nada.

## Verificación

Suite completa verificada el `2026-07-23` después de reauditar M.6.3 y cerrar M.6:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
# 1378 passed in 241.04s
```

Después de retirar el duplicado sombreado `GET /api/runs/{run_id}`, los 77 tests
API pasan sin warnings OpenAPI.

Frontend M.5–M.6.2 verificado:

```powershell
Set-Location ide-frontend
npm run build
npm run lint
npm run test:e2e
# 8 passed
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

## Continuación P0.K.8 — `2026-07-30`

K.8.6 se dividió para separar evidencia hermética, reparación del runner,
evidencia Windows independiente y plataformas aplazadas. K.8.6.1 queda cerrado
con `project_portability_acceptance_v1`: compone los contratos canónicos de
reparación de adapters, commit guiado, preflight proporcional y equivalencia
clean/update, ejecuta el fixture local React+TypeScript y añade una raíz
temporal adversa con proyecto personal numerado, Git limpio/dirty/remoto
redacted, SQLite corrupta, staging interrumpido y symlink/reparse no seguido.
Pasa 9/9 checks; 88 tests dirigidos pasan y 2 se omiten por capacidades
opcionales ajenas al gate. Ruff E/F/I y diff check están verdes. Receipt:
`benchmarks/results/guided_setup/project-portability-acceptance-2026-07-30.json`;
evidence hash
`865bf7bd544861b5a6a090c3ce68cf9e34657f190030de8683f2b56ae0d2aef2`;
SHA-256 de archivo
`c6cae54eb332effc20c8d9771186ada95c39372af0e00a517359ee4e28caa483`.
No se inspeccionó ni mutó la raíz real.

Siguiente unidad: K.8.6.2. `scripts/accept_windows_clean_room.py` todavía llama
al comando `aiteam project create`, retirado en K.8.2 para impedir siblings
numerados. Hay que reemplazar esa etapa por el commit guiado actual, sellar su
footprint en clone/retry/restart/update y demostrar que no instala tareas,
daemons, TTL ni utilidades de limpieza. No reintroducir el comando legacy.

K.8.6.2 queda cerrado. `accept_windows_clean_room.py` ya no contiene ni invoca
`aiteam project create`: materializa una propuesta sellada Lead-first de
investigación mediante `guided_setup_project_commit_v1` y un adapter fixture
que no infiere. El runner exige un único nuevo directorio, retry por colisión
sin siblings, árbol idéntico tras bootstrap/update, dos ciclos start/health/stop
y rollback SQLite byte a byte sin dejar el backup. También sella cinco
entrypoints y falla si registran scheduled tasks, servicios o startup; higiene
mantiene limpieza automática/startup/TTL en falso. La provenance conserva hash
del harness y dirty state, y un checkout Git dirty no puede ser independiente.
Pasan 32 tests dirigidos, Ruff E/F/I y diff check. La ejecución local completó
24/24 pasos en 42 s, dejó ambos puertos libres y se registró honestamente como
`local_existing_host`, dirty y no promocionable. Receipt:
`benchmarks/results/installation_acceptance/windows-clean-room-k8-6-2-local-2026-07-30.json`;
SHA-256
`739daa80be69fb418f48b17274401b55bc83c47c0df0c3366db524c782d91913`.
Las raíces temporales creadas durante los intentos fueron eliminadas.

Siguiente unidad: K.8.6.3. Ejecutar el runner actualizado en un checkout Windows
limpio independiente y en una instalación existente actualizable, conservar
ambos receipts SHA-bound y no confundir el receipt local dirty con evidencia
de promoción.

K.8.6.3 queda dividido. K.8.6.3a está implementado: el workflow Windows usa una
matriz `clean-clone`/`existing-checkout-updated`; la segunda celda bootstrappea
`HEAD^`, rechaza cambios tracked y avanza al SHA objetivo. El runner sella
`installation_state`, revisión anterior, dirty state y harness. El nuevo
`audit_windows_clean_room_matrix.py` agrega los dos receipts y exige 9/9 gates:
ambos independientes/promocionables/clean, misma revisión+harness, baseline
distinto, commit guiado+retry, restart/update+rollback y cero lifecycle
persistente. Un receipt local dirty queda rechazado. Pasan 25 tests, parse YAML,
Ruff E/F/I y diff check.

K.8.6.3 no está cerrado: 3b requiere publicar un SHA coherente —el worktree
actual contiene muchas dependencias nuevas aún untracked, por lo que un commit
parcial sería inválido— y recoger los tres artefactos reales de Actions. 3c
requiere consentimiento y ejecución redacted en otra instalación ya existente.
No se hizo commit, push ni se inventó evidencia CI en esta unidad.

K.8.6.3b queda cerrado. El estado coherente se publicó en
`6145567c8fb7393dce7479d6fdbf3180a2826533` y el
[run 30563841249](https://github.com/MaxBonas/ai-teams/actions/runs/30563841249)
terminó verde con las celdas `clean-clone` y `existing-checkout-updated` más el
auditor agregado. Cada celda completó 24/24 pasos y quedó
`independent_machine=true`, `promotion_allowed=true` y
`working_tree_dirty=false`; la actualización comenzó en
`6fd5e421d9ad9da6ec31314604cf6358422004c8`. Ambas comparten el harness
`5c2c183c48f9b3341b59db542f09b233796d9a10eedd3bba61d8238265b669cc` y la
matriz pasa 9/9 gates. Se versionó el receipt agregado en
`benchmarks/results/installation_acceptance/windows-clean-room-matrix-6145567.json`
(SHA-256
`444d55db1a55176b6b9a1ee451cd140c486893cdd4a68a3f7900c2984813b156`).
K.8.6.3 permanece abierto solo por 3c: actualización redacted en la instalación
física de otro usuario con su consentimiento. El siguiente bloque local es
P0.N.1, contrato canónico y fuentes de cambios de proveedor.

P0.N.1 queda cerrado con `provider_change_intelligence_v1`. El contrato
canónico separa `cli_package`, `mcp_server`, `sdk_api`, `internal_adapter` y
`model_catalog`; cada componente conserva como hechos independientes versión
instalada, pin soportado y última versión conocida con estado y provenance.
El inventario derivado cubre 12 perfiles, 42 componentes y tres MCP. Todo dato
no sondeado permanece `unknown` sin valor ni timestamp; discovery autenticado
no puede fijar soporte ni conceder routing. El receipt
`benchmarks/results/provider_change_contract/provider-change-contract-2026-07-30.json`
pasa 7/7 y sella el inventario
`8efbe929662c159dce37cb6bb7ccd6d8385af7b788a26b15daf75c3c8dc7acd2`.
Pasan 10 tests focalizados. Siguiente unidad: P0.N.2, probes read-only y diff
semántico sobre este contrato; no implementar todavía persistencia/scheduling
de N.3 ni actualizaciones automáticas.

P0.N.2 queda cerrado con `provider_change_snapshot_v1` y
`provider_change_diff_v1`. Los readers provider-specific se inyectan en un
probe neutral de una sola llamada; la normalización rechaza campos secretos o
fuera de dimensión. Timeout/offline/429/auth/fallo quedan `unknown`.
La comparación distingue upgrade, downgrade, prerelease, incompatibilidad,
deprecación/retirada y release; una versión opaca no se ordena artificialmente.
MCP, API/schema, adapter y catálogo tienen diffs propios. Catálogo detecta
alta, baja, rename/alias y cambios de contexto, tools, structured output,
precio, cuota y lifecycle. `newer_available` no recomienda por sí solo y toda
salida conserva `automatic_update_allowed=false` y
`routing_change_allowed=false`. El receipt
`benchmarks/results/provider_change_detection/provider-change-detection-2026-07-30.json`
pasa 19/19 casos y 8/8 gates (SHA-256
`2789df9e2cfa9bd8f8aa8bf70e37d830db64ee502016b79249f23ca08bac3f56`).
Pasan 21 tests focalizados y Ruff. Siguiente unidad: P0.N.3, persistencia,
dedupe y scheduling durable; no saltar todavía a workflow de actualización.

P0.N.3 queda cerrado con `provider_change_persistence_v1` sobre la SQLite de
máquina `guided_setup.db`. Schema y runtime comparten cinco tablas para
snapshots, diffs, eventos, triggers exactos y schedules. Los eventos deduplican
por identidad+dimensión+before/after, conservan primera/última observación,
contador y ciclo `open/acknowledged/snoozed/resolved`; recurrencia reabre y la
recuperación resuelve indisponibilidad. Los triggers de M.8/P0.g quedan
pendientes y acotados al modelo/dimensión, sin invalidar evidencia todavía.
El scheduler usa lease, cadencia, jitter y backoff, registra 42 componentes y
sólo ejecuta 23 readers locales seguros. Startup lo mantiene vivo y doctor
expone un resumen read-only sin crear estado ausente. No hay red, secretos,
login, inferencias, update ni routing automáticos. Receipt 9/9:
`benchmarks/results/provider_change_persistence/provider-change-persistence-2026-07-30.json`
(SHA-256
`e3ae2f44e288e440217ece58a6b133619fea51621f03f5e7aa23c57fd7ad813d`).
Pasan 45 tests focalizados y Ruff. Siguiente unidad: P0.N.4, workflow durable
de gestión y rollback que consume triggers con aprobación explícita.

P0.N.4 queda cerrado con `provider_change_workflow_v1`. Los triggers N.3 crean
expedientes globales idempotentes en `guided_setup.db`; cada uno conserva diff,
impacto, alcance exacto, recomendación, comandos guiados, riesgo, rollback,
revisión optimista e historial append-only. Confirmación, clasificación,
approval, aplicación registrada, doctor/probe, recalibración, aceptación,
retry, rechazo y rollback son transiciones deterministas. Aprobar activa un
overlay por perfil+modelo+rol que vuelve stale solo la evidencia afectada y,
si la política es `block_affected`, impide nuevas selecciones manuales y
automáticas. Nunca reescribe assignments. Aceptar restaura el overlay y consume
el trigger; revertir restaura y lo reabre. La API autenticada expone reconcile,
listado, detalle y transición. Ningún comando se ejecuta, ningún secreto se
guarda y no hay inferencia/update/routing automático. Receipt 9/9:
`benchmarks/results/provider_change_workflow/provider-change-workflow-2026-07-30.json`
(SHA-256
`caadefd8a28741ec2712714f1c4f40b91114efd48c3445b45cbd2b2b176cee1a`).
Pasan 43 tests focalizados y Ruff. Siguiente unidad: P0.N.5, inbox/banner y
gestión visual sin duplicar autoridad en React.

Regresión base separada: la fotografía histórica de cobertura del 2026-07-24
espera 15 calibrados, pero el código actual sin overlay N.4 produce 13 porque
Terra/Reviewer y Gemini Free/Reviewer señalan receipt inválido. No se modificó
la evidencia ni la expectativa; requiere auditoría propia.
La ampliación también confirma aislado el fallo previo de
`test_contextual_selection_endpoint_is_explicitly_shadow_only`: su fixture
espera cargar capacidades de un issue con SQLite inexistente, mientras el
contrato productivo hace fail-closed y usa defaults de rol. Debe decidirse el
contrato API/fixture en una unidad separada; N.4 no atraviesa esa ruta.

P0.N.5.1–N.5.3 quedan cerrados sobre la rama
`codex/provider-change-notifications`. `provider_change_inbox_v1` proyecta
casos+eventos read-only con contador, banner, severidad, edad, alcance,
evidencia, siguiente acción y agrupación. El caso N.4 sigue siendo la
interacción y su history la actividad. Acknowledge, snooze y gestionar usan
revisión optimista y una sola transacción; stale devuelve 409. Configuración
incluye el centro completo y Modelos el banner compacto, sin recalcular
autoridad ni ejecutar remediaciones. Pasan 46 tests backend y 34 unitarios
frontend, tipos, linters, límites, build y bundle. Bundle final:
409.595/118.309 B JS raw/gzip y 126.949/22.925 B CSS. Quedan N.5.4
(entrega externa opt-in real, redacted y anti-spam) y N.5.5
(aceptación visual/portable); hasta entonces la UI declara correctamente cero
canales externos activos.
