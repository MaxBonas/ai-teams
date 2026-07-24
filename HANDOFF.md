<!-- layer: system-development | audiencia: sesiones de desarrollo -->

# Handoff actual

Fecha: `2026-07-24`

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

Siguiente unidad: **canario exacto Antigravity 1.1.6 GPT-OSS/Worker**. La
cobertura ya separa 16 cierres negativos vigentes mediante
`deferred_until_material_change`; solo difieren con política explícita, recibo,
edad y versión coincidentes. Cambio o versión desconocida los reabre. El único
`requires_canary` actual es GPT-OSS/Worker porque su diagnóstico pertenece a
Antigravity 1.1.5 y el transporte observado es 1.1.6; no implica promoción.
Flash Low/Web Scout quedó cerrado
negativamente en Antigravity 1.1.6: el canal carece de MCP gobernado, el rol lo
exige ahora en la política común y no se ejecutaron seeds 2–3 ni se atribuyó el
fallo al modelo. El watchdog de AI Teams vence antes que el timeout interno de
`agy`, evitando hijos huérfanos y errores de limpieza que oculten la causa.
El catálogo conserva el diagnóstico aunque el rol deje de estar nominado:
799 celdas, 697 incompatibles, 102 compatibles, cero auto-elegibles y auditoría
verde. Pasan 100 tests focales, 1633 globales y el check frontend completo
(lint, CSS, límites, 6 unitarios, build y 12 E2E), además de Ruff F/E9 y diff
check.
El inventario del 2026-07-24 descubre Ling 3.0 Flash
Free en OpenCode: ya está visible como `catalog_only`, sin roles ni selección
automática. Codex 0.145.0 sigue siendo la última versión publicada en npm, pero
su cache exige 0.146.0, así que el auditor lo bloquea correctamente. Antigravity
1.1.6 conserva catálogo, vuelve stale Sonnet/Engineer 1.1.5 y su screening de
revalidación pasa 3/3 hidden pero falla Ruff con 7 incidencias; no repetir hasta
otro cambio material. El owner ha despriorizado por ahora I.8.4c/d Linux/macOS,
Containers, Mobile nativo y PHP/Ruby/Swift. El descriptor v0.1.0 conserva
`publish.enabled=false`: no crear el tag sin evidencia independiente.
I.8.1 e I.9 ya están cerrados.
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
parciales, 16 diferidos hasta cambio material, un canario ejecutable, cero
fixtures pendientes, 3 manuales y 79 bloqueados. Los lotes quedan divididos por
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
