# Plan y trabajo vigente

Actualizado: `2026-07-24`

Este archivo contiene solo backlog vivo, bloqueadores, criterios de cierre y
orden de ejecución. Los cierres detallados viven en `docs/HISTORY.md`; los
contratos activos, en `docs/MIGRATION_PAPERCLIP.md` y `docs/ORCHESTRATION.md`.

## Objetivo del producto

Construir un control plane Paperclip-like sobre SQLite para equipos de
programación:

- Lead-first y hiring dinámico;
- issues, runs, wakeups, interactions y telemetría durables;
- perfiles canónicos `solo_lead`, `lead_quorum` y `full_team`;
- planificación, accountability, liveness y recovery explícitos;
- routing económico por capacidad demostrada, nunca por marca o discovery;
- catálogo universal y visual de proveedores/modelos, con evaluación y ranking
  explicable por rol sobre evidencia durable;
- distribución reproducible entre máquinas, sin rutas, secretos ni estado local
  embebidos en el repositorio;
- integración poliglota extensible, capaz de detectar y operar toolchains de
  distintos lenguajes sin asumir que todo proyecto es Python o JavaScript;
- creación y edición de equipos que propongan el mejor par modelo+canal
  realmente configurado, compatible y saludable, siempre con override del owner;
- adapters API, suscripción y local independientes;
- gates proporcionales al riesgo y bajo ruido operativo.

No reintroducir parser `[WORKFLOW_PLAN]`, rondas `process_once()`/
`run_until_idle()`, router multifactor legacy, JSONL primario ni prompts raíz de
proveedor. Los artefactos creados en proyectos externos viven bajo `.aiteam/`.

## Estado actual

- El backend/pre-run de compatibilidad perfil+modelo+rol está cerrado y gobierna
  bootstrap, Equipo, hiring, dispatch, fallback y recovery.
- Los tres run profiles tienen canario vivo cerrado.
- La matriz canónica cubre 47 modelos × 17 roles: 799 celdas, 102 compatibles
  y 697 incompatibles con razón explícita. `web_scout` exige MCP gobernado y
  ya no acepta canales API/Antigravity que no puedan proporcionarlo.
- Sonnet 4.6 conserva `best_for=engineer`, pero ninguna ruta es auto-elegible;
  Luna conserva `context_curator` con esfuerzo `medium` y Flash High conserva
  evidencia exacta de review/QA.
- OpenCode Zen sigue read-only y sin promociones automáticas. Gemini 3.6 está
  catalogado, pero bloqueado para routing por su ejecución observada.
- Codex CLI 0.145.0 enumera Sol/Terra/Luna. El A/B causal auth+queue deja
  GPT-5.5 como control histórico 6/6 y promueve Luna `medium` 6/6 como Tier 3;
  el auditor pasa sus 6/6 gates, incluida la matriz capacidad+economía+velocidad.
- Paralelismo continúa opt-in; no existe trigger vivo representativo.
- El informe de coste y las conclusiones de orientación esperan volumen real.

## Orden de ejecución

1. Mantener el contrato y la proyección de catálogo/ranking `model_role_score_v2`
   antes de ampliar canarios: identidad exacta, componentes, confianza, hard
   gates, provenance y paridad entre API, Equipo y hiring; todavía en shadow.
2. Cerrar el contrato de instalación portable, diagnóstico de máquina y registro
   poliglota antes de declarar nuevas plataformas o lenguajes como soportados.
3. Calibrar únicamente pares modelo+rol cuyo canal y credencial estén realmente
   disponibles e ingerir sus recibos en esa proyección; no crear perfiles decorativos.
4. Construir la pestaña Modelos y conectar creación/edición de equipos al mismo
   ranking, primero como recomendación explicable y después como default gated.
5. Ejecutar los estudios condicionados solo cuando aparezca su trigger real:
   entregas, paralelismo, señales de cuota o participantes humanos.
6. Repetir drift/calibraciones por evento y en la fecha programada.

Próxima unidad: **calibración de catálogo modelo+rol con canales realmente
disponibles**. El owner ha despriorizado por ahora I.8.4c/d Linux/macOS,
Containers, Mobile nativo y PHP/Ruby/Swift. Web moderno queda cerrado con
30/30 celdas CI; Docker continúa opcional y nunca requisito.
M.8 permanece
abierto solo por mantenimiento continuo; sus cuatro diagnósticos mono-familia
no se repiten hasta cambio material.

## P0.I — Distribución portable e integración poliglota

- [x] **I.1 Definir la matriz de soporte y el contrato de instalación**.
  - [x] **I.1.1 Matriz canónica**: `config/installation_support.v1.json`
    separa `verified`, `preview`, `planned` y `unsupported` por OS/arquitectura,
    runtimes y distribución. Windows x86_64 nativo queda `verified` en I.1.4;
    ARM64, Linux y macOS siguen `planned` hasta recibos independientes.
  - [x] **I.1.2 Requisitos y bundles sin instalación implícita**: el manifiesto,
    bootstrap, README y guía distinguen runtimes requeridos, una de varias
    opciones Lead-capable, OpenCode económico opcional y runtimes locales
    opcionales. `audit_installation_support.py` es read-only, omite rutas y
    secretos, no confunde binario con auth/health y nunca instala globales.
    OpenCode Zen documenta la API key personal exigida por el gateway incluso
    para modelos temporalmente a precio cero; AI Teams guía el login pero no
    crea cuentas ni acepta condiciones. Verificación: bootstrap real completo,
    37 tests dirigidos, Ruff limpio y 1456 tests backend.
  - [x] **I.1.3 Contrato de distribución y rollback**: Git es la vía
    `verified` actual y exige tag/commit SHA para integridad. La release planificada
    debe contener versión inmutable, SHA-256, SBOM/licencias, notas de migración,
    actualización/rollback y test de ausencia de secretos/estado local. Producir
    ese artefacto pertenece a I.8. Un contenedor puede ser adicional, nunca
    sustituto de CLIs y credenciales del host.
  - [x] **I.1.4 Aceptación independiente**: ejecutar clone/bootstrap dos veces,
    audit, start/stop y proyecto fixture en una máquina Windows x86_64 limpia;
    conservar recibo redacted. Solo entonces decidir si alguna celda puede pasar
    de `preview` a `verified`.
    - [x] **I.1.4.1 Harness y recibo fail-closed**:
      `scripts/accept_windows_clean_room.py` valida revisión, bootstrap
      idempotente, audit, health, start/stop, SQLite fixture, liberación de
      puertos y ausencia de instalación global implícita. El recibo omite rutas,
      usuario, hostname, outputs libres y credenciales. La ejecución local
      integral pasa los diez pasos, crea una issue y 26 tablas, y conserva
      correctamente `promotion_allowed=false`. El primer intento detectó handles
      heredados al capturar stdout de procesos desacoplados; el harness usa ahora
      `DEVNULL` solo en `start` y deja el resto diagnosticable.
    - [x] **I.1.4.2 Frontera independiente automatizada**:
      `.github/workflows/windows-clean-room.yml` ejecuta el harness sobre
      `windows-latest`, con checkout exacto y artefacto durable. Una ejecución
      local se etiqueta `local_existing_host` y no puede promover soporte.
    - [x] **I.1.4.3 Evidencia independiente**: el
      [run 30023876549](https://github.com/MaxBonas/ai-teams/actions/runs/30023876549)
      ejecutó el workflow sobre la
      revisión entregable, descargar y auditar
      `windows-clean-room-receipt.json`, enlazar revisión+run y comprobar
      `ok=true`, `independent_machine=true` y `promotion_allowed=true`. El
      recibo exige además SHA coincidente y provenance completa de GitHub
      Actions. El recibo durable es
      `benchmarks/results/installation_acceptance/windows-clean-room-f2a20ed.json`
      (SHA-256 versionado
      `b45b9c285bec86ba356ce36a747b24d2ba9d503d51d5ec34291cc5ebf5c6111d`;
      artefacto original
      `b8b714f97b103ba602419849c0bccdeb18362de49e2bbae8e2533f7e37d20806`).
      Pasa 14/14 checks de auditoría, cinco runtimes y 10/10 pasos.
    - [x] **I.1.4.4 Decisión de promoción**:
      `windows_native_x86_64` y `git_checkout` pasan a `verified` para clone,
      bootstrap idempotente, audit, start/stop y fixture. La ausencia de CLIs en
      el runner demuestra además que el bootstrap no instala proveedores.
      Auth/health/modelos vivos, releases, ARM64 y POSIX quedan explícitamente
      fuera de esta promoción.
    - Verificación de implementación: 41 tests focalizados, Ruff limpio,
      typecheck frontend limpio, 1461 tests backend y puertos 8010/9490 libres
      tras el teardown.
  - Caso de aceptación registrado: una instalación externa del 2026-07-22
    encontró CLIs ausentes, interpretó la key de Zen como error, instaló
    Ollama/LM Studio como obligatorios y lanzó tests para un estudio empresarial
    no programativo. RUN-018 y P0.J conservan las partes de onboarding y
    orquestación que no pertenecen al contrato de plataforma.
  - Cierre: instalación desde cero y actualización verificadas por plataforma,
    sin pasos implícitos ni afirmaciones de soporte sin evidencia fechada.

- [x] **I.2 Hacer portable la configuración y el estado por máquina**.
  - [x] **I.2.1 Formalizar capas, precedencia, ownership y actualización in-place**.
    - [x] `config/configuration_layers.v1.json` fija defaults → usuario →
      entorno allowlisted → proyecto → run, owners, ubicaciones y límites.
      Secrets se inyectan por referencia después del merge; DB, health,
      sesiones CLI y runtime quedan clasificados como estado, no configuración.
    - [x] `aiteam.configuration_layers` aporta merge profundo y provenance por
      campo. Settings expone la fuente efectiva de `projects_root`; autonomía
      conserva proyecto sobre entorno y los overrides de adapter conservan
      defaults anidados nuevos.
    - [x] `ensure_local_runtime.ps1` actualiza instalaciones heredadas mediante
      merge conservador: valores locales ganan, defaults ausentes se añaden,
      se crea backup inicial y JSON inválido falla sin sobrescritura.
    - [x] `scripts/update_windows.bat` detiene, exige Git limpio, usa
      `pull --ff-only`, ejecuta bootstrap y registra revisión/resultado sin
      stash, reset, migración, login o instalación global. Existe transición
      documentada para checkouts anteriores al script.
    - Verificación: 74 tests focalizados, incluida actualización real contra
      remote Git fixture, rechazo de checkout sucio, preservación de runtime
      local, segunda sincronización idempotente, merge de tres vías y herencia
      de defaults; suite backend completa 1472/1472, Ruff y typecheck frontend
      limpios.
  - [x] **I.2.2 Export/import redacted de configuración operativa**.
    - [x] `aiteam_portable_config_v1` conserva settings allowlisted, perfiles
      custom y política estructurada opcional del proyecto, con SHA-256 sobre
      JSON canónico y provenance de omisiones.
    - [x] Sanitización recursiva retira credenciales inline, formatos de secreto
      conocidos, `env`/headers, rutas absolutas y referencias a
      `runtime`/`venv`/`node_modules`/DB. No lee stores de secretos, health,
      sesiones, bases, assignments, runs, costes ni telemetría.
    - [x] `scripts/config_portability.py` ofrece `export`, `inspect` e `import`.
      Import es preflight por defecto, exige `--apply`, mergea sin borrar
      settings/perfiles ajenos, deja secretos intactos e invalida health de los
      perfiles importados hasta probe local exacto.
    - [x] La política de proyecto requiere destino explícito, no transporta path
      o nombre de origen y nunca crea/copia DB. Asignaciones vivas se rehacen en
      destino mediante hiring/reconcile gated.
    - Verificación: matriz conjunta I.2.1/I.2.2 80/80, suite backend
      1478/1478, Ruff y typecheck frontend limpios; export real de esta máquina
      validado sin persistir el artefacto.
  - [x] **I.2.3 Auditar y aislar diferencias de filesystem/procesos por OS**.
    - [x] `aiteam.platform_runtime` centraliza IDs OS/arquitectura, semántica de
      paths, shims ejecutables, layout de `venv`, streams/entorno UTF-8, grupos
      de proceso y teardown del árbol completo en timeout.
    - [x] Adapters genérico/suscripción, probes MCP, notificaciones, CLI y
      discovery consumen esa frontera. El notifier ya no usa shell; los dos
      scripts NordVPN ya no contienen rutas personales, son opcionales,
      preservan entradas, hacen dry-run y exigen `-Apply` admin con backup.
    - [x] `platform_portability_audit_v1` prueba espacios/Unicode, encoding,
      permisos, case sensitivity y timeout; escanea rutas personales,
      `shell=True` y consumidores críticos. Es read-only fuera del fixture y
      nunca promociona soporte.
    - Verificación: auditor real Windows x86_64 `ok=true` (teardown
      `windows_taskkill_tree`), 107 tests dirigidos y suite backend 1493/1493;
      typecheck frontend y Ruff de las superficies cambiadas limpios. Linux,
      macOS y ARM64 conservan estado `planned` hasta aceptación independiente.
  - Cierre: un checkout limpio reconstruye el entorno y una mudanza conserva
    intención/configuración no secreta sin copiar estado local.

- [x] **I.3 Crear un `doctor` de máquina seguro y legible por humanos/IA**.
  - [x] **I.3.1 Schema e inventario base read-only**: definir
    `machine_doctor_v1` e inventariar OS/arquitectura, Python, Node/npm, Git,
    SQLite, puertos y permisos sin imprimir entorno, secretos ni paths
    personales.
    - [x] `config/machine_doctor.v1.schema.json` falla cerrado sobre diez
      secciones y seis runtimes base; los ejecutables se reducen a basename.
    - [x] El probe usa solo comandos de versión allowlisted con entorno mínimo,
      conexión loopback y `os.access`; no crea archivos ni lee credenciales.
    - [x] `scripts/machine_doctor.py` ofrece salida humana y `--json --strict`
      iniciales. Diagnóstico completo/remediation permanecen en I.3.2–I.3.4.
    - Verificación: doctor real Windows x86_64 con inventario completo, 29 tests
      focalizados, Ruff limpio en alcance y suite backend 1502/1502.
  - [x] **I.3.2 Toolchains y adapters**: observar CLIs, versiones, fuente,
    autenticación/health del par exacto y toolchains del proyecto sin instalar
    ni ejecutar inferencias.
    - [x] Once señales poliglotas separan manifest detectado, binario/version
      observados y soporte demostrado; discovery raíz nunca promociona lenguaje.
    - [x] Los perfiles redactados publican canal, proveedor, CLI/transporte,
      runtime local, auth y health durable por separado. `installed` no implica
      auth, `ok` no se fabrica desde presencia y local usa `not_applicable`.
    - [x] Solo se ejecutan `--version`/equivalentes allowlisted con entorno
      mínimo; no se invocan login, secret store, catálogo vivo ni inferencia.
    - Verificación: doctor real observa 12 perfiles y manifests Python/JS sin
      mutar los tres archivos de configuración locales; 46 tests focalizados,
      Ruff limpio y suite backend 1506/1506.
  - [x] **I.3.3 Diagnóstico y presentación**: salida humana y `--json` estable
    con bloqueo y siguiente acción; distinguir ausente, no autenticado,
    incompatible, no verificado y degradado.
    - [x] Cada toolchain y adapter conserva `diagnostic_state`; los diagnósticos
      publican sujeto, severidad, código, evidencia y siguiente acción sin
      incluir un comando ejecutable ni realizar la remediation.
    - [x] La composición marca `blocked`, `degraded`, `ready_with_unknowns` o
      `ready`. `--strict` falla solo ante blockers y nunca convierte warnings o
      desconocidos en salud inventada.
    - [x] Un perfil opcional ausente queda informativo; runtimes obligatorios,
      permisos incompatibles, toolchain requerida por manifest o falta de una
      vía primaria autenticada+verde bloquean con causa estable.
    - Verificación: doctor real Windows clasifica la máquina como `blocked`
      únicamente por no tener vía primaria durable verificada; `--strict`
      devuelve 2 y no ejecuta login, canarios ni remediaciones. Pasan 49 tests
      focalizados, Ruff y 1509/1509 tests backend.
  - [x] **I.3.4 Recibo y contrato de no mutación**: demostrar que discovery no
    escribe ni instala; cualquier remediation queda en un comando separado,
    explícito y con recibo reproducible.
    - [x] `machine_doctor_receipt_v1` incluye el report validado, hashes
      canónicos y guard sobre metadata de checkout/config más presencia de CLIs;
      el guard no abre contenido de secretos ni emite rutas personales.
    - [x] `scripts/machine_doctor_receipt.py --output ...` es la única escritura
      de este flujo, exige path explícito, no crea el directorio padre y requiere
      `--force` para reemplazar exactamente ese recibo.
    - [x] `scripts/machine_doctor_remediate.py --receipt ... --action ...`
      produce `machine_doctor_remediation_v1`, vinculado al hash y siempre
      `guided_manual`, `applied=false`, `not_executed`; no existe `--apply`.
    - [x] El contrato detecta una escritura fixture, rechaza tampering/acciones
      no diagnosticadas y mantiene salida UTF-8 en Windows.
    - Verificación: recibo real con checkout, user config e inventario CLI sin
      cambios; segunda ejecución con el mismo `receipt_id`; remediation real
      hash-bound y no ejecutada. Pasan 38 tests focalizados, Ruff y 1518/1518
      tests backend.
  - Cierre: una IA puede decidir si la máquina está lista usando solo el JSON y
    puede explicar cada bloqueo sin inferirlo de logs libres.

- [x] **I.4 Unificar bootstrap y ciclo de vida cross-platform**.
  - [x] **I.4.1 Contrato común idempotente**: extraer la lógica de
    `prepare_dev_env.bat`/PowerShell a un contrato
    idempotente con frontends Windows y POSIX equivalentes; mantener comandos de
    start, stop, test y migrate por plataforma.
    - [x] `config/dev_lifecycle.v1.json` define la superficie ordenada
      `prepare/start/stop/test/migrate`, alcance de mutación, idempotencia,
      frontends e invariantes; falla cerrado ante acciones o autoridad extra.
    - [x] `aiteam.dev_lifecycle_contract` proyecta manifests deterministas para
      Windows/Linux/macOS y verifica que cada frontend quede dentro del checkout.
    - [x] Windows conserva sus entrypoints; POSIX añade wrappers locales para
      bootstrap, Python, pytest y sesión foreground Node. No usa PowerShell,
      `sudo`, instalaciones globales, login ni inferencias.
    - [x] POSIX continúa `planned/preview`: no se confunde disponer de scripts
      con soporte aceptado. Locks, ownership y matriz de recovery quedan
      implementados en I.4.2–I.4.3, pero necesitan aceptación POSIX independiente.
    - Verificación: 37 tests focalizados, Ruff, proyecciones Windows/Linux,
      paths Unicode y `node --check` limpios; 1527/1527 tests backend. Dos
      bootstraps Windows consecutivos terminan en 0 sin cambiar CLIs ni hashes
      de estado. Esta máquina no dispone de `sh`, por lo que no aporta recibo
      POSIX.
  - [x] **I.4.2 Entorno y procesos gobernados**: usar entorno local del repo,
    locks/versiones reproducibles y procesos
    hijos explícitos; no depender de asociaciones de `.ps1`, shell interactiva,
    PATH mutable ni instalaciones globales accidentales.
    - [x] `requirements-dev.lock` fija dependencias Python y el bootstrap instala
      primero el lock y después el checkout editable sin dependencias ni build
      isolation. Frontend exige `package-lock.json` + `npm ci`: no actualiza el
      lock ni cae a `npm install`. Python ya no actualiza `pip` implícitamente.
    - [x] Windows ejecuta cada `.ps1` mediante `powershell.exe` explícito y
      serializa bootstrap con `FileShare.None`; POSIX usa un lockdir atómico con
      owner PID y recuperación stale. La segunda pasada no cambia hashes ni
      timestamps de estado y `pip check` queda limpio.
    - [x] `dev_process_registry_v1` registra PID, create time, firma, puertos y
      checkout. Start falla si un puerto está ocupado; stop solo termina árboles
      cuya identidad coincide y nunca busca/mata por puerto o firma global.
    - Verificación local Windows: 32 tests focalizados, Ruff/Node limpios, lock
      concurrente fail-closed, start 200/200, stop completo, proceso ajeno en
      8010 conservado, `pip check` e idempotencia de bootstrap; 1531/1531 tests
      backend. La aceptación POSIX permanece pendiente y no se sobreafirma.
  - [x] **I.4.3 Matriz de fallos y recovery**: probar espacios y Unicode en
    rutas, puertos ocupados, dependencia ausente,
    ejecución repetida, interrupción y limpieza/recovery.
    - [x] `dev_lifecycle_v1.recovery_matrix` fija diez casos, invariant y
      evidencia diferenciada por plataforma. Windows queda verificado en los
      canarios vivos aplicables; POSIX conserva `preview/contract_tested`.
    - [x] El bootstrap hace preflight de todos los inputs versionados antes de
      crear `runtime/`; un lock ausente en una ruta Unicode falla con diagnóstico
      y cero mutación. Los frontends batch fuerzan UTF-8.
    - [x] Backend y frontend se registran inmediatamente después de cada spawn.
      Una interrupción entre ambos deja ownership recuperable; segundo start,
      pérdida parcial, pérdida total con registro stale, registro corrupto y
      stop repetido fallan o recuperan según contrato sin tocar procesos ajenos.
    - [x] Canarios Windows: checkout por junction con espacios/`ñ`/japonés
      completa prepare→start→health 200/200→stop; puerto ajeno se conserva;
      segundo start no altera la sesión; backend perdido limpia frontend y
      reinicia; pérdida total elimina registro stale y reinicia sin PID heredado.
    - Verificación: 27 pruebas focalizadas, Ruff/Node/diff limpios y 1537/1537
      tests backend. No quedan registro, listeners ni fixture Unicode.
  - Cierre: segunda ejecución no rompe ni reinstala innecesariamente; todo fallo
    deja diagnóstico accionable y no una instalación parcial silenciosa.

- [x] **I.5 Construir un registro extensible de ecosistemas/toolchains**.
  - [x] Definir descriptor versionado por ecosistema: detectores, manifests,
    extensiones, binarios/versiones, comandos permitidos de configure/build/
    test/lint/typecheck, dependencias entre acciones, cwd/env, artefactos y
    capacidades requeridas.
  - [x] Priorizar fixtures para Python; JS/TS; Java/Kotlin; Go; Rust; C/C++;
    .NET; PHP; Ruby; Swift; web/mobile y repos con Docker/devcontainers. Añadir
    otros lenguajes mediante plugins/descriptores, no condicionales dispersos.
    Los doce descriptores existen en `config/ecosystems.v1.json`; `planned` no
    equivale a soporte y los fixtures ejecutados siguen perteneciendo a I.6.
  - [x] Separar detectar de ejecutar: la detección es read-only; instalar
    runtimes/dependencias o ejecutar scripts del proyecto requiere política,
    sandbox, timeout y autorización acordes al riesgo.
    El planner falla cerrado por selector, capability, autorización, estado,
    runtime, cwd y timeout; nunca instala. Solo pytest/npm conservan el camino
    legacy ya verificado y los comandos `planned` requieren opt-in explícito.
  - [x] Proyectar el stack detectado al Lead, hiring, prompts, tools y gates para
    que cada rol reciba únicamente comandos y capacidades compatibles.
    `machine_doctor_v1`, wake payload, hiring y el `test_runner` determinista
    consumen el mismo registro; una mera extensión no inventa acciones ni hires.
  - Cierre 2026-07-23: contrato/esquema versionados, escaneo acotado sin
    symlinks/ruido, CLI read-only y proyección común. Ningún lenguaje obtiene
    etiqueta `supported`: cada promoción requiere aún fixture build/test y
    recibo por OS en I.6. Evidencia: 28 pruebas nuevas/doctor, 116 de
    `RunExecutor` y 1550/1550 backend globales, todas verdes; Ruff crítico y
    `diff --check` verdes.

- [ ] **I.6 Validar proyectos poliglotas y entornos heterogéneos**.
  - [x] **I.6.1 Base reproducible**: fixtures mínimos Python/npm y monorepo
    multi-language, ejecutados en copia temporal con espacios y Unicode.
    Validan detección, selector/cwd, comando sin shell, artefactos y errores
    esperados. `ecosystem_validation_receipt_v1` conserva fecha, OS,
    arquitectura, SHA, dirty bit y versión de runtime sin rutas absolutas.
    El canario base Windows local pasa 4/4 celdas. Con Java/.NET, la regresión
    actual pasa 30/30 tests focalizados, 190/190 de integración con
    doctor/wake/runner y 1578/1578 globales.
    Al estar el worktree sucio no autoriza promoción.
  - [x] **I.6.2 Ejecutar la matriz CI por OS/toolchain sin credenciales**.
    `.github/workflows/polyglot-fixtures.yml` ya define Windows/Linux/macOS para
    nueve casos Python/npm/Java/.NET/Go/Rust/C++. El gate agregado descarga los
    18 receipts, exige las 27 celdas exactas, worktree limpio, todos los casos
    `passed`, `support_claim=false` y el mismo SHA; conserva hashes de cada
    fuente en `ecosystem_ci_evidence_v1`. RUN-022 corrige los triggers para la
    rama real `master` y conserva `main` como compatible. La primera run expuso
    RUN-023: Windows 8.3 y `/var` de macOS hacían divergir raíz sin resolver y
    `cwd` canónico; además `--require` no limitaba los casos. Tras corregir
    ambas fronteras, la run `30085247826` pasa 18/18 receipts y 27/27 celdas
    sobre `775e72e`; el agregado durable
    `polyglot-ci-775e72e.json` tiene SHA-256
    `9ce3c81b41817a9a7b3fde78a99ea5753722385f8cb309cfe5b204f802d2fc64`.
    `support_claim=false` permanece deliberadamente: la evidencia cierra el
    gate, no promociona por sí sola el catálogo. Reservar canarios vivos de
    adapters para entornos controlados y registrar provenance separada.
  - [x] **I.6.3 Fallar de forma explicable**: cuando falta soporte devuelve
    `capability_gap_v1` con descriptor, owner y acción; nunca instala, improvisa
    comandos destructivos ni declara éxito parcial. Los comandos `planned`
    solo se desbloquean dentro del validador autorizado y el receipt mantiene
    `support_claim=false`.
  - [ ] **I.6.4 Ampliar fixtures y CI** a Java/Kotlin, Go, Rust, C/C++, .NET,
    PHP, Ruby, Swift, Web, Mobile y Containers, incluyendo build/test,
    timeouts, quoting, artefactos y gaps específicos por OS.
    - [x] Java/Maven: fixture JUnit con package, test y surefire report; Windows
      local y CI Java 17 × tres OS pasan.
    - [x] .NET: fixture xUnit con build/test; Windows local identifica que el
      host tiene runtime pero no SDK mediante `runtime_probe_failed:dotnet`.
      CI SDK 8 × tres OS pasa.
      El receipt redacted no conserva rutas absolutas.
    - [x] Go: fixture sin dependencias con build/test; Windows local devuelve
      `runtime_unavailable:go`. CI `setup-go@v6` con Go 1.25.9 × tres OS pasa.
    - [x] Rust: fixture Cargo `--locked`, test y rlib; Windows local devuelve
      `runtime_unavailable:cargo`. CI usa el Rust preinstalado y pasa × tres OS.
    - [x] C/C++: el contrato añade la acción `configure` y dependencias
      descriptor-bound `configure → build → test`. Fixture CMake/CTest y job
      × tres OS pasa; Windows local bloquea configure por CMake ausente y las
      fases posteriores por `prerequisite_not_satisfied`, sin ejecutarlas.
    - [x] **Web moderno**: fixture Vite + React + TypeScript + CSS detecta
      `web_frontend` y reutiliza, sin duplicarlos, los comandos descriptor-bound
      npm de build/test/lint/typecheck. La calidad real del stack permanece
      cubierta por I.9; este fixture valida detección, routing, cwd, quoting y
      artefacto en una copia portable. Run `30085680374`: 18 receipts y 30/30
      celdas verdes en Windows/Linux/macOS sobre `8888dfe`; agregado SHA-256
      `8a91f9a3be06444c15a9b9285341a5a1fa8ca89e4f47946266f59bfc2644adce`.
    - [ ] **Containers opcionales**: añadir fixture Docker/Compose cuando haya
      runtime controlado. Nunca instalar Docker automáticamente ni convertirlo
      en requisito de AI Teams.
    - [ ] **Mobile nativo pospuesto**: separar Android/Flutter/Xcode antes de
      crear fixtures; no conservar la categoría compuesta `web_mobile`.
    - [ ] PHP, Ruby y Swift, pausados por prioridad.
  - Cierre: matriz pública de cobertura, recibos fechados y regresión automática
    para cada celda anunciada como soportada. Estado visible en
    `docs/ECOSYSTEM_SUPPORT_MATRIX.md`.

- [x] **I.7 Crear onboarding canónico para personas y agentes de IA**. `✅✅`
  Doble comprobación completada el 2026-07-22.
  - [x] Corregir el README raíz: URL real, bootstrap vigente, modelos no
    hardcodeados y límites de plataforma explícitos.
  - [x] Añadir `docs/INSTALLATION_AND_INTEGRATION.md` con configuración,
    traslado entre máquinas, arranque, validación y protocolo de integración IA.
  - [x] Enlazar la guía desde el índice vivo y registrar el contrato en plan y
    handoff. La documentación describe el estado actual; no da por cerrados
    `doctor`, POSIX, releases ni soporte poliglota todavía no implementados.
  - [x] Reauditar I.7 el 2026-07-22 contra código y ejecución real: bootstrap
    Windows verde en dos pasadas consecutivas, migración en dry-run,
    `system-check`, 1335 tests backend y typecheck frontend. Corregidas dos
    sobreafirmaciones vigentes entonces: Windows permanecía `preview` hasta I.1 y
    el artefacto de release seguía pendiente en I.8; además
    `system-check` enumera el registro, pero no prueba auth/conectividad/health.
    `tests/test_installation_docs.py` protege entrypoints, enlaces y límites.

- [ ] **I.8 Preparar release y aceptación en máquina limpia**.
  - [x] Aislar los proyectos creados por pytest dentro de su sesión temporal.
    `AITEAM_PROJECTS_ROOT` ya no queda vacío ni cae sobre el padre real del
    repositorio; la suite de workspace refuerza una raíz propia por test.
    RUN-024 conserva el diagnóstico y prohíbe borrar automáticamente los
    artefactos históricos mezclados con proyectos reales.
  - [x] **I.8.1 Contrato y generador reproducible del artefacto**.
    `release_artifact_v1` empaqueta solo archivos controlados por Git, normaliza
    orden/timestamp/modos y usa ZIP stored para reproducibilidad transversal.
    Rechaza worktree sucio, conflictos, symlinks, rutas runtime no allowlisted,
    dependencias reconstruibles, SQLite, extensiones sensibles y patrones de
    secretos; dos literales de test quedan allowlisted de forma exacta, no por
    directorio.
    - Genera manifiesto con SHA-256 por archivo, `SHA256SUMS` interno, checksum
      externo, CycloneDX 1.6 y reporte de licencias. npm se deriva del lockfile;
      Python se deriva del `uv.lock` universal.
    - La workflow `release-artifact.yml` construye y sube previews auditables en
      PR/manual. Un tag exige tag exacto y `promotion_allowed=true`; no crea una
      GitHub Release.
    - El preview local previo a I.8.2a empaquetó 1032 archivos. Sus blockers de
      licencia/lock ya están resueltos; el worktree actual continúa no
      promocionable por suciedad hasta consolidar el commit.
    - Verificación: 10/10 tests de determinismo, checksums, inventario,
      tag/worktree y rechazo sensible —incluido UTF-16—; 18/18 pruebas conjuntas
      de release/documentación, Ruff limpio, preview real construido y suite
      backend 1588/1588.
  - [x] **I.8.2 Promoción, notas y rollback**.
    - [x] **I.8.2a Licencia y lock Python**: Apache-2.0, titular
      `Max Bonas Fuertes`; el DNI/CIF no se versiona. `LICENSE` coincide con el
      texto oficial y `NOTICE` conserva copyright 2026. `pyproject.toml` y npm
      declaran SPDX.
      - `uv.lock`, generado con uv 0.11.31, fija 58 paquetes mediante resolución
        universal y exige Windows/Linux/macOS × x86-64/ARM64. Los exports
        runtime/dev conservan hashes; bootstrap usa `pip --require-hashes` sin
        hacer `uv` obligatorio en máquinas usuarias.
      - CI comprueba `uv lock --check`, regenera ambos exports y exige igualdad
        byte a byte. El SBOM consume versiones/hashes Python bloqueados.
      - Evidencia: resolución seis entornos y bootstrap canónico verdes,
        `pip --dry-run` acepta el export, 29/29 pruebas focalizadas y 1588/1588
        backend; frontend build y audit cero. La advertencia upstream
        Starlette/httpx2 queda registrada como RUN-020, sin cambio especulativo.
    - [x] **I.8.2b Notas y publicación**: `release_descriptor_v1` alinea SemVer,
      `pyproject`, tag anotado, notas y rollback; rechaza rutas inseguras,
      headings ausentes, worktree sucio, tag ligero y publicación deshabilitada.
      `v0.1.0` tiene notas versionadas y `publish.enabled=false` hasta I.8.4.
      - `UPGRADE_AND_ROLLBACK.md` exige instalación side-by-side, checksum
        externo/interno, dry-run, backup SQLite y restauración antes de volver
        al código anterior. El verificador recalcula el ZIP, rechaza miembros
        inseguros/duplicados y cubre exactamente todo el payload.
      - CI conserva `contents: read` al construir; solo un job `publish` tras
        todos los gates obtiene `contents: write`, bajo environment
        `github-release`. Revalida el mismo artifact, crea draft, exige cinco
        assets y publica sin sobrescribir una Release existente.
      - Evidencia: 26/26 pruebas focalizadas, 1600/1600 backend, Ruff limpio,
        YAML parseable y preview integral de 1162 archivos
        construido/verificado. El preview es correctamente no promocionable por
        worktree sucio; no se creó tag ni Release.
  - [x] **I.8.3 Checklist de aceptación humana/IA**: `release_archive_acceptance_v1`
    valida desde fuera del ZIP 17 pasos canónicos: checksum/extracción, revisión,
    bootstrap dos veces, audit, tests mínimos, start/health/stop, proyecto
    temporal, migración dry-run/apply con backup, restauración SQLite byte a
    byte, puertos libres y retirada externa de fixture/instalación.
    - La primera run real detectó que Python 3.12 ya no aporta setuptools al
      venv. `setuptools==83.0.0` y `wheel==0.47.0` quedan ahora en el lock dev
      con hashes; no se instala build tooling flotante.
    - La auditoría exacta detectó además que la cabecera de `uv export` incluía
      la ruta temporal y hacía imposible `cmp`; los exports y CI usan
      `--no-header`.
    - El wrapper es quien limpia después de terminar el proceso interno,
      evitando que una instalación se auto-certifique como eliminada. El job
      Windows de release precede y bloquea `publish`.
    - Evidencia local redacted:
      `release-preview-local-f69f8e7.json`, SHA-256
      `c965f5c5c54a16eeacf425d613821db471b9f3fc648c59002a0ea5896e5ced74`;
      17/17 gates verdes sobre ZIP de 1164 archivos. Sigue
      `promotion_allowed=false` por preview sucio/máquina no independiente.
      Verificación de código: 50/50 pruebas focalizadas, 1605/1605 backend,
      Ruff, diff y YAML verdes; persiste únicamente RUN-020.
  - [ ] **I.8.4 Aceptación multiplataforma**: probar Windows, Linux y macOS en
    runners limpios y después una máquina real por plataforma antes de promover
    de `preview` a `verified`.
    - [x] **I.8.4a Harness portable**: el wrapper selecciona el harness Windows
      o POSIX. Linux/macOS ejecutan los mismos 17 gates, incluida salud,
      start/stop, fixture SQLite, migración/backup/rollback, ausencia de CLIs
      globales introducidos y limpieza externa. El recibo conserva OS,
      arquitectura, SHA y provenance sin rutas locales.
    - [x] **I.8.4b Gate CI común**: `release-acceptance` descarga el mismo ZIP
      una vez por Windows/Linux/macOS, sube un receipt distinto por celda y
      bloquea `publish` si cualquiera falla. PR/manual admiten preview para
      probar el pipeline, pero solo un tag promocionable puede publicar.
      Verificación local: 50/50 pruebas de release/instalación, 17/17 del gate
      polyglot y 1611/1611 backend; Ruff, YAML y `diff --check` verdes.
    - [ ] **I.8.4c Evidencia hosted**: consolidar un SHA, ejecutar la matriz y
      auditar los tres receipts reales. No marcar soporte de plataforma a
      partir de la mera definición YAML.
    - [ ] **I.8.4d Evidencia física**: repetir el ZIP aceptado en una máquina
      real Windows, Linux y macOS, conservar recibos ligados al mismo SHA y
      solo entonces promover la distribución de `preview` a `verified`.
  - Cierre: una persona o IA sin contexto previo instala siguiendo solo la guía,
    obtiene los mismos checks y deja un recibo auditable de éxito o bloqueo.

- [x] **I.9 Endurecer el stack web principal (React/TypeScript/JavaScript/CSS)**.
  - [x] **I.9.1 Actualizar y fijar una base compatible y segura**: React 19.2.8,
    Vite 8.1.5, plugin React 6, ESLint 10 y plugins vigentes sobre Node
    `>=20.19`; mantener TypeScript 5.9.3 mientras `typescript-eslint` no soporte
    TypeScript 7. `npm audit` queda en cero.
  - [x] Añadir gates reproducibles `typecheck`, ESLint, Stylelint recomendado,
    build y Playwright en `npm run check`; CI limpia con Node 24 y `npm ci`.
  - [x] Corregir funciones React usadas antes de declararse, CSS deprecado,
    selectores duplicados y contraste global. Axe WCAG 2.1 AA y viewport móvil
    quedan integrados en el E2E de orientación; los 8 E2E pasan.
  - [x] **I.9.2 Reducir riesgo estructural por cortes verificables**.
    - [x] **I.9.2a Catálogo, selector y quorum**: `ModelCatalog`,
      `ModelRoleSelector` y `QuorumStepper` poseen hojas propias; quorum usa
      `useQuorum` keyed por issue, abortable y tipado. `QuorumStepper` y los
      formatters salen de `App.tsx`. No queda ninguna excepción
      `react-hooks/set-state-in-effect`; la regla de especificidad vuelve a estar
      activa en las hojas pequeñas aisladas. `index.css` baja de 2974 a 2552
      líneas y `App.tsx` de 5298 a 5141. Evidencia: lint, Stylelint, build,
      8/8 E2E —incluidos retry, Axe AA y móvil— y audit cero.
    - [x] **I.9.2b Configuración y Bandeja**: extraer vistas, estado y cargas por
      dominio; objetivo final `App.tsx < 4000` e `index.css < 1800`, sin duplicar
      fetches ni scoring y con E2E existentes verdes.
      - [x] **I.9.2b1 Shells y superficies de bajo acoplamiento**:
        `ConfigurationPanel`, Proyecto, Autonomía, Orientación, `InfoTip` y la
        lista/selección de `InboxPanel` salen de `App.tsx`. Sus hojas CSS salen
        de `index.css`, incluido responsive; Bandeja reactiva
        `no-descending-specificity`. Los formatters de fecha preservan UTC de
        SQLite en `lib/format.ts`. Resultado: `App.tsx` 5141→4931,
        `index.css` 2552→2143; lint, Stylelint, build, 8/8 E2E y audit cero.
      - [x] **I.9.2b2 Skills/MCP + hiring**: `SkillsSettings`, `McpSettings` y
        `HiringDecisionDetail` son vistas tipadas; sus contratos salen de
        `App.tsx`. El cálculo de ranking permanece en backend y el bloqueo de
        hiring, approvals MCP y transiciones permanecen en el contenedor.
        Resultado acumulado de b: `App.tsx` 4931→4682 líneas; `index.css`
        permanece en 2143. Evidencia: lint, Stylelint, build, 8/8 E2E y audit
        cero.
      - [x] **I.9.2b3 Global y sistema**: `ConfigurationWorkspace` compone las
        vistas tipadas de credenciales, CLIs, adapters, carpeta/sistema y zona
        de peligro. `useConfigurationData` posee su estado, cargas y mutaciones;
        `App.tsx` conserva workspace, navegación y confirmaciones destructivas.
        CSS de conexiones, `InfoTip` y Equipo sale de la hoja global sin alterar
        el bundle visual. Resultado: `App.tsx` 4682→3984 e `index.css`
        2143→1692; lint, Stylelint, build, 8/8 E2E y audit cero.
    - [x] **I.9.2c Chat, issues y runs**: `ChatPanel`, `IssuePanel`,
      `IssuePipeline` y `RunsPanel` poseen contratos y CSS propios; tipos de
      cockpit y markdown salen de `App.tsx`. El ratchet `lint:size` limita
      módulos TS/TSX a 600 líneas y CSS a 500, con tech-debt caps explícitos
      `App.tsx=3600`, `index.css=1300` y `ModelCatalog.tsx=750`.
      `no-descending-specificity` gobierna las nuevas hojas aisladas, pero no
      `ModelCatalog.css` hasta dividir sus subpaneles. Resultado:
      `App.tsx` 3984→3546 e `index.css` 1692→1246; lint, Stylelint, tamaño,
      build, 9/9 E2E —incluido el smoke Chat→Detalle→Runs— y audit cero.
  - [x] **I.9.3 Ampliar cobertura de UI**: pruebas de componente para estados,
    errores y navegación por teclado; matriz dedicada Chromium móvil/escritorio
    y, antes de declarar soporte amplio, Firefox/WebKit.
    - Vitest 4 + React Testing Library sobre jsdom cubren seis casos en
      `ChatPanel`, `IssuePanel` y `RunsPanel`: estados vacíos, envío/foco por
      teclado, decisión pendiente, errores de run y lookup accesible.
    - Playwright separa proyectos: los nueve recorridos completos permanecen en
      Chromium escritorio y el smoke crítico Chat→Detalle→Runs se ejecuta
      también en Pixel 7/Chromium, Firefox y WebKit. Esta matriz prueba
      compatibilidad representativa; no declara cobertura exhaustiva en los
      tres navegadores adicionales.
    - Axe WCAG AA y ausencia de overflow horizontal forman parte del smoke. La
      primera ejecución móvil detectó que el timeline de eventos desplazable no
      era alcanzable por teclado; `RunsPanel` expone ahora región etiquetada y
      `tabIndex=0`.
    - `lint:bundle` aplica presupuestos agregados fail-closed sobre el build:
      JS ≤400 KiB raw/120 KiB gzip y CSS ≤120 KiB raw/25 KiB gzip. Medición de
      cierre: JS 366071/107539 B y CSS 101679/18106 B.
    - Verificación 2026-07-24: `npm run check` verde —ESLint, Stylelint,
      ratchet de módulos, 6/6 unitarias, typecheck/build, presupuesto y 12/12
      ejecuciones E2E—; `npm audit --audit-level=high` devuelve cero.
  - Cierre: dependencias sin vulnerabilidades conocidas, gates verdes en CI,
    cero violaciones Axe AA en rutas críticas y límites de bundle registrados.

## P0.J — Objetivos no programativos y gates proporcionales

- [x] **J.1 Clasificar el tipo de entregable antes del hiring**.
  - [x] Añadir un contrato explícito `software`, `research`, `operations` o
    `mixed` en creación de proyecto/tarea, con recomendación explicable y
    override del owner. No inferir autoridad ni ejecutar por una etiqueta sola.
  - [x] Mitigación inmediata: la skill del Lead prohíbe crear Engineer, Test
    Designer, QA, Test Runner, archivos o tests para estudios empresariales,
    investigación y entregables teóricos sin artefacto ejecutable.
  - Cierre (`2026-07-23`): `objective_classification_v1` se calcula de forma
    determinista y conservadora, acepta override explícito del owner y queda
    persistido en metadata. API, creación de proyecto/tarea, cockpit, wake
    payload y propuesta del Lead consumen el mismo contrato.

- [x] **J.2 Aplicar workflows y evidencia según el entregable**.
  - [x] Research usa scouts/curator y, cuando aporte valor, revisión independiente
    de fuentes/método; acepta cobertura, citas fechadas, supuestos, cálculos y
    decisión final, no `pytest` ni un exit code inventado.
  - [x] Mixed aísla sub-issues ejecutables; solo estos activan toolchain,
    Test Designer y test runner. Software conserva los gates actuales.
  - [x] Reproducir como fixture un estudio de empresa de limpieza sin código:
    debe cerrar sin crear suite, package manifest ni bucle de quality gate.
  - Cierre (`2026-07-23`): hiring y delegación rechazan roles de programación
    en `research`/`operations`; `mixed` solo los admite en hijos clasificados
    `software`; quality/test gates se omiten de forma determinista para trabajo
    no programativo. El fixture exacto de empresa de limpieza cierra con
    evidencia documental y continuación durable. Verificación: 228 tests
    dirigidos, 1561 tests backend globales, lint/diff y typecheck frontend
    limpios; el bypass de una propuesta owner-edited se revalidó después de la
    suite global con 10/10 focalizados.

## P0 — Modelos, catálogos y promociones

- [ ] **Mantener actualizado y evaluar todo el catálogo modelo+rol**.
  - [x] Baseline `2026-07-22`: defaults, opciones, prompts y scripts activos
    usan las familias vigentes; GPT-5.5 queda solo como control histórico y las
    tarifas antiguas solo como compatibilidad FinOps de runs ya persistidas.
  - [x] Las 47 opciones activas exponen banda de capacidad, economía específica
    del canal y clase/fuente de velocidad bajo
    `capability_economy_speed_v1`; un dato desconocido queda explícito y no se
    sustituye por una estimación.
  - [x] La matriz hermética perfil+modelo+rol verifica capacidades, privacidad,
    workspace, salida estructurada, MCP gobernado y roles deterministas. Tier y
    `best_for` orientan ranking, pero nunca conceden herramientas o autoridad.
  - [x] Generar un inventario durable de cobertura conductual por par exacto
    perfil+modelo+rol: `calibrated`, `partial`,
    `deferred_until_material_change`, `requires_canary`,
    `requires_tool_fixture`, `manual_candidate` o `blocked`. Baseline histórico:
    46 modelos/131 destinos semánticos; 25 calibrados, 5 parciales, 15 canarios
    ejecutables pendientes, 4 fixtures de tools pendientes, 3 candidatos
    manuales y 79 bloqueados por canal/health. Recibo:
    `benchmarks/results/model_evaluation_coverage/model-evaluation-coverage-2026-07-23.json`.
    Evento vivo `2026-07-24`: 47 modelos/124 destinos; el preflight proyecta
    8 calibrados, 17 parciales, 17 diferidos hasta cambio material, 0 canarios,
    0 fixtures, 3 manuales y 79 bloqueados. Un diagnóstico solo difiere si
    declara la política, el recibo es válido, no caducó y la versión CLI local
    observada coincide; cambio o versión desconocida reabre la acción.
    Versión, edad e integridad se detectan automáticamente; un cambio semántico
    de prompt, contrato o tooling sin nueva versión debe revisar explícitamente
    el registro diagnóstico en ese mismo cambio.
    No borra evidencia histórica ni cambia defaults. Recibos:
    `model-evaluation-coverage-2026-07-24-ling-probe.json` y
    `model-catalog-read-model-2026-07-24-ling-probe.json`.
  - [x] **Lote A — Codex subscription (14 destinos evaluados)**: Luna para scouts/worker;
    Terra para Engineer/MCP/QA/review/test design; Sol para Lead/arquitectura/
    quorum. Reutilizar harnesses por familia de contrato y registrar por rol
    semántico, sin contar aliases dos veces. Estado: 13 `calibrated` y
    Luna/File Scout `partial`, con bloqueo hasta cambio material.
    - [x] A.1 Alinear `worker` como Tier 3 de solo lectura en políticas, tools,
      sandbox, contrato y scheduler; no ocupa work slots de implementación.
    - [x] A.2 Impedir cierre `done` de worker/scouts/test runner sin
      `AGENT-REPORT` válido: un reintento correctivo y bloqueo+escalado durable
      al segundo fallo; 121 tests dirigidos pasan.
    - [x] A.3 Calibrar Luna `file_scout` y `worker` con contrato v2 y tres
      semillas. Worker corrige skill, prompt/report y completa 3/3 en una run;
      queda `calibrated`. File Scout conserva hechos 3/3 pero solo cierra en una
      run 1/3; queda `partial` y no se reajusta sobre las mismas semillas. Los
      screenings iniciales low/medium permanecen como diagnósticos históricos.
    - [x] A.4 Calibrar Luna `web_scout` con MCP gobernado; discovery o acceso web
      nativo no sustituyen el grant `external_mcp`. El contrato v2 completa 3/3
      con allow/deny y llamada read reales; una segunda familia enlazada eleva
      el agregado a 6/6 muestras y abre `case_diversity`.
    - [x] A.5 Calibrar Terra por contratos Tier 2, reutilizando primero
      harnesses durables existentes.
      - [x] `reviewer`: 3/3 ciclos `changes_requested` → fix → `approved`;
        mediana 64,0 s (62,844–93,094), 113.509 tokens input y 8.230 output.
      - [x] `engineer`: 3/3, 27/27 tests ocultos, Ruff limpio y una run por
        semilla; mediana 62,921 s (50,563–70,797), 116.800 input/8.812 output.
      - [x] Corregir capacidades explícitas: `test_designer` recibe escritura+
        LSP; `mcp_operator`, `external_mcp`+skill. El fallback `repo_read` no
        podía representar sus contratos.
      - [x] Restaurar skills vigentes para QA condicional, Test Designer y MCP
        Operator. QA recibe `repo_write` para materializar solo tests
        adversariales; OpenCode read-only deja de recomendar QA.
      - [x] `qa`: 3/3 ciclos adversariales y 30/30 checks; materializa tests
        que fallan antes del fix, no toca producción, persiste
        `changes_requested` y aprueba/limpia después. Mediana 116,048 s
        (115,953–133,938), 773.932 input/12.946 output. El fallo pre-fix del
        contrato `add_comment` se conserva en recibo separado.
      - [x] `test_designer`: 3/3 suites independientes, 24/24 checks y 15/15
        ejecuciones mutantes ocultas; solo crea el test acordado y no toca
        producción. Mediana 73,172 s (71,235–92,328), 404.062 input/7.956 output.
      - [x] `mcp_operator`: 3/3 y 36/36 checks con allow read, deny write,
        llamada real, fallo de versión 0.9.0 frente al pin 1.0.0 y recovery
        `active`; mediana 42,359 s (27,859–49,593), 342.171 input/4.424 output.
        El contrato pre-fix inválido y la reevaluación determinista de seed 2
        quedan preservados sin repetir inferencia.
    - [x] A.6 Calibrar Sol por contratos Tier 1: `lead`, `lead_executor`,
      `team_lead`, `architect` y `quorum_auditor`, cada uno con evidencia exacta
      y sin extrapolar aliases semánticamente distintos. Los cinco pares quedan
      `calibrated`; sus agregados críticos enlazan dos familias y seis muestras.
  - [x] **Lote B — Antigravity (drift 1.1.6 cerrado sin promoción)**:
    conservar Flash
    High Reviewer como calibrado durable; completar contratos exactos que el
    screening genérico de Lead/scout no demuestra. No repetir review 3/3 ni
    coding Sonnet sin cambio de CLI/modelo/contrato. GPT-OSS quedó cerrado
    negativamente por fallo reproducible de `submit_work` en File Scout,
    Worker y Web Scout. El cambio material a CLI 1.1.6 reabrió Sonnet/Engineer:
    `config_redactor` pasa 3/3 hidden, pero deja 7 incidencias Ruff en 296,297 s;
    fail-fast impide gastar las otras cinco celdas y la calibración 1.1.5 queda
    stale para nuevas promociones. El fixture exacto Flash Low/Web Scout fue
    fail-fast en seed 1: el executor denegó `mcp:web-scout-canary` con
    `mcp_adapter_not_supported`, por lo que no existe evidencia de calidad del
    modelo ni procede gastar seeds 2–3. `web_scout` exige ahora `external_mcp`
    desde el contrato central y Antigravity deja de nominar modelos para ese
    rol hasta ofrecer un loop MCP gobernado. El watchdog externo vence antes
    que el timeout de `agy` para evitar hijos huérfanos que oculten el error
    original. Los candidatos manuales y Gemini 3.6 bloqueados no cuentan como
    calibraciones ejecutables. Verificación: 235 tests dirigidos, 1629 tests
    globales, Ruff F/E9 y diff limpios; matrices de flujo y catálogo verdes,
    con cero auto-elegibles.
  - [x] **Lote C — locales instalados (8 destinos evaluados, cierre negativo)**:
    Gemma 4 E4B/26B y Qwen 2.5 Coder 14B fueron probados sin descargar modelos
    ausentes. Gemma 26B/Engineer queda `partial` 1/3; los otros siete contratos
    conservan diagnóstico exacto y no se repiten hasta un cambio material. El
    coste/cuota externos son cero; RAM/VRAM, energía, latencia y throughput
    permanecen como ejes de host separados.
  - [x] **Lote D — OpenCode (cierre negativo por transporte sin cambio)**:
    catálogo 1.18.4 y hashes revalidados sin nueva inferencia; DeepSeek Reviewer
    queda `partial` 1/3. El catálogo del 2026-07-24 añade Ling 3.0 Flash Free
    como sexta opción. Su probe exacto de una inferencia ejecuta, pero devuelve
    el objeto correcto como pseudo-tool textual, con `structured=null` y
    `StructuredOutputError`. Permanece `catalog_only`, manual/probe-gated,
    denegada para todos los roles, sin quality ni selección. El teardown quedó
    rojo en el recibo, aunque no persistió proceso visible y un control
    start/stop sin inferencia cerró proceso+puerto en 0,25 s; no repetir el
    modelo para corregir esa telemetría. Mantener read-only y no reabrir
    server/SDK hasta un cambio de catálogo, modelo, CLI, transporte o contrato.
    Recibo:
    `benchmarks/results/model_calibration/opencode-ling-3.0-flash-catalog-probe-v1.json`.
    Verificación de cierre: 100 tests focales, 1639 backend globales, check
    frontend completo, Ruff F/E9 y auditorías de cobertura/read-model verdes.
  - [ ] **Lote E — APIs/Claude bloqueados (79 destinos en total junto con otros
    no ejecutables)**: esperar key, CLI, instalación o health exacto; discovery
    comercial no autoriza consumo ni selección.
  - [ ] Ejecutar un canario reproducible de tres semillas por cada destino
    automático `best_for`, contra baseline simple del mismo contrato; registrar
    calidad, mediana+rango, tokens/precio o presión de cuota, duración, liveness
    y riesgo de Goodhart.
  - [ ] Medir velocidad local/canal para las opciones cuyo `speed_source` siga
    `requires_channel_specific_measurement`; no comparar tokens/s oficiales de
    un proveedor con latencia end-to-end de otro como si fueran equivalentes.
  - [ ] Probar las herramientas necesarias del rol por transporte: ops
    estructuradas/workspace, JSON Schema o JSON Object, privacidad y MCP
    gobernado. Una tool nativa del proveedor no equivale a un grant MCP.
  - [ ] Evaluar candidatos manuales/probe-gated solo antes de promoverlos; no
    consumir cuota para demostrar combinaciones que no entrarán en routing.
  - [ ] Repetir inventario y calibraciones cada 30 días y ante cambios de CLI,
    catálogo, precio, cuota, modelo, prompt, contrato de rol o herramientas.
  - Regla de conservación: no retirar un modelo solo por antigüedad. Mantenerlo
    mientras siga disponible y aporte coste, capacidad, velocidad, compatibilidad
    o fallback útiles; bloquear/retirar únicamente con evidencia negativa clara
    de disponibilidad, seguridad, calidad, coste o redundancia sin valor.
  - Cierre: cada ruta automática tiene identidad actual, health exacto,
    compatibilidad de herramientas y evidencia conductual fresca; el auditor
    enumera explícitamente cualquier hueco restante.

- [ ] **P0.M — Catálogo universal, scoring por rol y selección de equipos**.
  Objetivo: convertir el inventario y los benchmarks en una superficie de
  producto única que catalogue todos los proveedores/modelos conocidos, muestre
  sus estadísticas por rol y gobierne las recomendaciones y defaults de Equipo.
  No crear una segunda verdad: la proyección consume catálogos, health,
  compatibilidad, pricing/cuota/recursos, runs y recibos ya canónicos.
  - [x] **M.1 Contrato de identidad y estados**. `✅✅`
    Doble comprobación completada el 2026-07-22.
    - [x] Enumerar todo modelo declarado, descubierto, configurado o visto en
      runs históricas, aunque esté inactivo, bloqueado, retirado o manual-only.
      `build_model_catalog_identity_projection` acepta las cuatro fuentes y
      conserva perfiles históricos ya ausentes; conectarlo a SQLite pertenece
      a M.3, no a este contrato puro.
    - [x] Identificar por separado fabricante/perspectiva del modelo,
      organización proveedora, perfil, canal/pool y slug exacto. Un mismo modelo
      vía API y suscripción son dos candidatos operativos distintos.
    - [x] Definir estados no colapsables: `catalogued`, `configured`,
      `adapter_green`, `model_verified`, `selectable`, `compatible`,
      `calibrated`, `stale`, `manual_only`, `blocked` y `retired`, cada uno con
      razón, fuente, versión y fecha.
    - Cierre: `model_catalog_identity_v1` documentado y cubierto por fixtures
      API, suscripción, local y free gateway; discovery no prueba ejecución,
      estados dependientes de rol quedan `unknown` y ningún `available`
      autoritativo aparece en la proyección. 5 tests dirigidos pasan.
    - [x] Reauditar precedencia y provenance: histórico solo rellena huecos y no
      puede sobrescribir catálogo/config/discovery; cada estado conserva la
      fuente de su campo ganador. Perfiles duplicados e identidades históricas
      conflictivas fallan cerrados, `availability=blocked` impide selección y
      la API importa la enumeración canónica sin mantener una copia paralela.
      Evidencia: 34 tests dirigidos, Ruff y 1344 tests globales verdes.
  - [x] **M.2 Métricas y puntuación versionada por rol**. `✅✅`
    Doble comprobación completada el 2026-07-23.
    - [x] Crear `model_role_score_v1` para el par exacto perfil+modelo+rol, con
      desglose 0–100: calidad conductual/idoneidad del rol 40 %, capacidad y
      headroom del contrato 15 %, fiabilidad/liveness 15 %, economía 20 % y
      velocidad 10 %. Son pesos iniciales prerregistrados: validar en shadow
      antes de permitir que cambien defaults.
    - [x] Publicar por separado `confidence` y estado de evidencia usando clase
      del juez, número de semillas/casos, frescura, versión, cobertura de tools,
      constructos no medidos y riesgo de Goodhart. No ocultar incertidumbre en
      la nota compuesta; `confidence` es gate, no multiplicador secreto.
    - [x] Normalizar economía según canal sin fingir equivalencias: precio API
      por tarea aceptada, presión de cuota para suscripción y recursos+
      throughput para local. Un valor desconocido queda `unknown`, reduce
      confianza/auto-elegibilidad y nunca se interpreta como gratis.
    - [x] Mantener hard gates fuera de la fórmula: privacidad, tools, workspace,
      structured output, health y ejecutabilidad pueden excluir aunque el score
      sea alto.
    - [x] Definir desempate estable: mayor evidencia/calidad del rol, menor
      carga económica comparable, menor latencia y finalmente identidad estable.
    - Cierre: `aiteam.model_role_scoring` es puro, determinista y `shadow_only`;
      score incompleto publica rango en vez de imputar unknowns, confidence queda
      separada y 13 tests cubren pesos, canales, stale, hard gates, unidades no
      comparables y empates. No cambia defaults ni el `role_score` transitorio.
    - [x] Reauditar inputs y consumidores: métricas sin fuente e identidades
      incompletas quedan rechazadas/unknown; tools sin evidencia, versión/fecha
      ausentes, juez insuficiente, menos de 3 semillas/2 casos, falta de recibos
      y Goodhart material/alto capan confidence por debajo de auto. Ranking
      rechaza versión, rol o candidate ID ambiguos y selección contextual importa
      pesos/versión/umbral canónicos. Smoke shadow: 46 candidatos, 124 pares,
      0 auto-elegibles y 0 fallos. Evidencia: 58 tests dirigidos, Ruff y 1357
      tests globales verdes.
  - [x] **M.3 Read model, persistencia y auditoría**. ✅✅ 2026-07-23
    - [x] Crear una proyección backend única que una `model_options`, tiers,
      compatibilidad, provider identity, catálogo/health exacto,
      `model_evaluation_coverage`, pricing/cuota, runs y `cost_events`.
    - [x] Migrar gradualmente `MODEL_ROLE_EVALUATION_EVIDENCE` a registros
      consultables con provenance de recibos sin perder Git como fuente durable;
      conservar diagnósticos negativos y resultados pre-fix.
    - [x] Persistir snapshots/version/hash del score usado en cada contratación
      automática para poder explicar y reproducir por qué ganó un candidato.
      La tabla/repositorio exige set completo, ganador perteneciente y elegible,
      hash e idempotencia; la primera escritura productiva se conecta al activar
      selección automática en M.7, que hoy sigue deshabilitada.
    - [x] Extender el auditor para detectar proveedores/modelos/roles ausentes,
      scores sin evidencia, métricas stale, recibos perdidos y divergencia entre
      catálogo, endpoint, Equipo e hiring.
    - Cierre: `model_catalog_read_model_v1` integra las fuentes sin convertir
      coste/latencia crudos en scores; acepta SQLite parcial/legacy, conserva
      hashes y expone auditor CLI. Baseline local: 46 candidatos, 124 pares,
      cero candidatos automáticos, cero fallos y 20 warnings de deuda visible.
      Los 8 tests nuevos y 77 dirigidos pasan.
    - [x] Reauditar composición y persistencia: las SQLite equivalentes se
      deduplican antes de agregar runs/costes; cada fila conserva los inputs
      exactos del score y el auditor recalcula tanto su hash como el resultado,
      incluso si se vuelve a sellar el payload exterior. Los snapshots rechazan
      versiones o roles explícitos contradictorios sin exigir campos redundantes
      a consumidores legacy. Auditor real: 46 candidatos, 124 pares, 0 auto y
      0 fallos; 76 tests dirigidos, Ruff y 1360 tests globales verdes.
  - [x] **M.4 API canónica del catálogo**. ✅✅ 2026-07-23
    - [x] Exponer inventario de proveedores/canales, modelos y matriz por rol,
      con filtros de rol, proveedor, canal, tier, estado y configuración.
    - [x] Exponer breakdown, confianza, métricas observadas, muestras, fechas,
      versiones, recibos, bloqueos y `selection_reason`; no solo la nota final.
    - [x] Crear un endpoint global de candidatos por rol que ordene pares
      modelo+perfil de todos los adapters y reutilice exactamente los hard gates
      de compatibilidad/pre-run.
    - [x] Mantener compatibilidad del endpoint actual por perfil, delegándolo a
      la nueva proyección hasta retirar su `role_score` heurístico.
    - Cierre: `/api/model-catalog` y `/api/model-catalog/candidates` proyectan
      `model_catalog_read_model_v1`, con contratos OpenAPI, filtros y caché local
      invalidable. El endpoint por perfil conserva sus campos legacy pero ordena
      y anota desde la proyección canónica. Smoke real: 48 candidatos al sumar
      históricos de la SQLite activa, 12 perfiles/canales, 13 pares reviewer y
      0 auto-elegibles. Pasan 77 tests API, 60 dirigidos de catálogo/flujo y la
      suite completa de 1288 tests; se retiró además una ruta OpenAPI duplicada.
    - [x] Reauditar contratos y consumidores: la caché ya no entrega su objeto
      mutable interno y sella el instante al terminar de construir, evitando que
      un consumidor contamine peticiones posteriores. `/candidates` declara
      explícitamente que publica el score base y enlaza el POST contextual actual,
      sin metadata obsoleta de M.6. Filtros, OpenAPI, orden canónico, detalle y
      shim legacy conservan paridad. Smoke real: 48 candidatos, 12 perfiles,
      13 reviewer, 0 auto, 28 configurados, 20 no configurados y 5 bloqueados;
      145 tests dirigidos, Ruff y 1360 tests globales verdes.
  - [x] **M.5 Nueva pestaña `Modelos`**. ✅✅ 2026-07-23
    - [x] Añadir navegación propia, no esconderla dentro de Config o Equipo.
    - [x] Mostrar proveedores y canales con estado configurado/verde, cuota o
      coste disponible, privacidad y recuentos de modelos activos/bloqueados.
    - [x] Mostrar tabla/heatmap modelo×rol con score, confianza, tier, economía,
      velocidad, calidad, estado y badges de evidencia; permitir comparar y
      abrir detalle con breakdown y recibos.
    - [x] Diferenciar visualmente “catalogado”, “disponible”, “configurado”,
      “compatible”, “calibrado” y “seleccionable”; los inactivos siguen visibles.
    - [x] Añadir filtros, orden accesible, estados vacío/error/loading y diseño
      responsive; no codificar scoring de nuevo en React.
    - Cierre: pestaña global `Modelos` implementada como observatorio técnico con
      tarjetas de proveedor/canal, filtros, matriz desplazable modelo×rol y ficha
      accesible de score, confianza, breakdown, evidencia, recibos, estados y
      hard gates. Al filtrar por rol consume el orden de la API; React no puntúa
      ni desempata. El read model añade metadata redacted de privacidad,
      workspace y economía para evitar una segunda fuente. Build y lint pasan;
      3 E2E pasan (loading/error/empty, filtros, orden backend, detalle bloqueado,
      adapter verde y responsive) y la suite Python conserva 1288 tests verdes.
    - [x] Reauditar interfaz y navegador: el detalle mueve, confina y devuelve
      el foco; confianza ausente muestra `—`, nunca 0; todos los estados
      canónicos son filtrables y las tarjetas exponen configurados/verdes con
      estado accesible. Se retiró el texto obsoleto que posponía M.6 y el raw
      incluye `score_inputs`. Dos fixtures globales se actualizaron al POST
      contextual vigente. Evidencia: build y lint verdes, 3 E2E específicos,
      7/7 E2E frontend, capturas desktop/móvil inspeccionadas y smoke Playwright
      Python `networkidle` con foco correcto y 0 errores de página; 1360 tests
      backend globales verdes.
  - [x] **M.6 Crear y editar equipos con ranking global por rol**. ✅✅ 2026-07-23
    - [x] Crear `POST /api/model-catalog/selection` y una proyección pura que
      recalcula compatibilidad/hard gates antes de ordenar, incluye también pares
      sin score exacto y no inventa un ganador cuando no hay auto-elegibles.
      El score base queda inmutable y el rollout continúa `shadow_only`.
    - [x] Para edición de agente y hiring propuesto mostrar todos los pares
      modelo+adapter mediante un componente compartido, agrupados por
      proveedor/canal y ordenados por `selection_score`; no limitar primero al
      perfil elegido. Los no elegibles aparecen deshabilitados con causa.
    - [x] La opción “Default” resuelve desde backend candidato, score, confianza
      y ventaja frente al siguiente; si no existe ganador seguro exige owner. El
      owner puede fijar cualquier alternativa compatible/selectable, incluso si
      no es auto-elegible.
    - [x] **M.6.1 Completar contexto y explicación del selector**. ✅✅ 2026-07-23
      - [x] Mostrar breakdown resumido del ganador y la razón legible por la que
        supera al siguiente. Evidencia/calidad pueden resolver diferencias
        materiales; un empate exacto ordenado solo por identidad exige owner y
        no produce default automático.
      - [x] Derivar en backend presión de cuota/capacidad desde
        `subscription_quota_snapshot` y el presupuesto diario API desde
        `cost_events`+política. Agotamiento observado/límite alcanzado bloquea;
        `capacity_unknown` no se convierte en permiso ni en cero.
      - [x] Sustituir el componente economía únicamente cuando una política de
        cuota del owner aporta utilización normalizada para el perfil exacto;
        conservar base, provenance y pesos. Presupuesto API agotado actúa como
        hard gate y no penaliza canales de suscripción de coste marginal cero.
      - [x] Incorporar tools requeridas por la issue/hiring además de las
        capacidades canónicas del rol y las del agente editado.
      - [x] Añadir E2E del orden backend, candidato bloqueado, ausencia de default
        y cambio owner del par completo.
      - [x] Reauditar fail-closed y explicación: una SQLite ausente/corrupta ya
        no convierte gasto desconocido en cero ni crea una DB al leer; cuota o
        presupuesto desconocidos dejan `capacity_available=null` para auto sin
        impedir la elección manual salvo agotamiento observado. Las capabilities
        se unen desde issue y todos sus ancestros, la criticidad más cercana
        prevalece y una política de cuota incompleta/NaN no reescribe economía.
        Los empates distinguen evidencia, calidad, economía/velocidad comparables
        e identidad, con explicación visible. Evidencia: 113 tests dirigidos,
        Ruff, build y lint verdes; 4 E2E del selector, 8/8 E2E frontend, smoke
        Playwright Python con `networkidle` y 1367 tests backend globales.
      - Cierre: contexto de issue, cuota y economía se resuelven en backend sin
        inventar datos; gates, orden, breakdown, explicación y selección owner
        están protegidos por regresiones unitarias, API y navegador.
    - [x] **M.6.2 Unificar todos los consumidores**. ✅✅ 2026-07-23
      - [x] Edición de agente y hiring propuesto usan el mismo componente y POST.
      - [x] Onboarding usa el selector global y bootstrap Lead persiste el par
        exacto elegido con `owner_explicit`; clientes antiguos sin modelo siguen
        usando la compatibilidad transitoria.
      - [x] Conectar alta desde catálogo de Equipo, quorum y fallbacks
        presentados al owner a la misma función backend. `contextual_model_selection`
        compone catálogo, issue, tools, cuota y presupuesto una sola vez; alta
        directa persiste el par `owner_explicit`, quorum valida el candidato exacto
        y conserva diversidad de perspectiva, y recovery restringe el selector al
        adapter actual antes de aplicar la elección aprobada.
        - Evidencia dirigida: 170 tests Python verdes; TypeScript y ESLint verdes.
      - [x] Retirar los defaults residuales que aún eligen el primer modelo del
        perfil y delegar gradualmente el endpoint legacy por perfil.
        - [x] Inventariar call sites y separar falsos positivos: los `[0]` de
          “Probar conexión” solo inicializan un probe manual y el de onboarding
          solo explica un rechazo; no asignan agentes.
        - [x] Sustituir `_choose_model`/`choose_adapter_for_role` en creación
          automática (`_ensure_role_agent`, liveness, Tier 3 y quorum sin pin)
          por el ganador contextual M.7; mientras no haya auto-elegible debe
          conservarse el shim o exigir owner, nunca tomar el primer candidato.
          Bootstrap Lead sin pin también usa este camino: sin ganador aborta y
          limpia el proyecto parcial; Tier 3/quorum quedan explicados como
          `default_unresolved` y no inventan un adapter.
        - [x] Llevar enforcement cross-provider y recovery cross-adapter a una
          propuesta contextual explícita; no mutar canal/modelo silenciosamente.
          Ambos gates bloquean la issue y crean una `request_confirmation` con
          par exacto y ranking. Accept recalcula catálogo, health, compatibilidad
          y restricción de diversidad antes de persistir `owner_explicit`; reject
          conserva el bloqueo. Una edición manual posterior nunca se sobrescribe
          y solo reabre si continúa conectada, seleccionable y válida.
          Evidencia dirigida: 10 tests sobre propuesta sin mutación, accept,
          reject, idempotencia, override concurrente y alternativa inválida.
        - [x] Retirar `GET /api/user-adapters/models` de los consumidores una vez
          que probes/config legacy tengan contrato propio y el POST global cubra
          todas las asignaciones. `App.tsx` usa ahora exclusivamente
          `POST /api/model-catalog/selection` para onboarding, Equipo y hiring;
          solo carga el perfil asignado y deriva estado, compatibilidad, score y
          razón de la proyección canónica. El GET queda temporalmente como
          compatibilidad externa sin consumidores productivos, separado de
          `POST /api/user-adapters/test`. E2E prueba POST y cero GET legacy.
          No confundir inventario local con ranking global.
        - Dependencia histórica resuelta por M.7: el smoke real del 2026-07-22
          tenía 0 candidatos auto-elegibles. El modo `auto` conserva ahora un
          `default_unresolved` explícito o exige selección del owner; `shadow`
          mantiene el shim sin presentarlo como ranking global.
      - Reauditoría 2026-07-23: se corrigieron tres pérdidas de contexto que las
        comprobaciones anteriores no cubrían. Create/PATCH revalidan también las
        capabilities efectivas del agente; altas y reconciliación explícita
        reciben `issue_id`; quorum, proposal inicial y liveness heredan profile,
        criticidad, clasificación y tools de la issue antes de elegir modelo.
        Los dos `model_options?.[0]` restantes en React son únicamente probes
        manuales de conexión y nunca asignan agentes.
      - Evidencia de doble check: 275 tests dirigidos, Ruff, TypeScript, ESLint y
        build verdes; 8/8 E2E frontend; smoke Playwright Python real con HTTP
        200 y cero `pageerror`; 1372 tests backend globales.
    - [x] **M.6.3 Persistencia de la intención del owner**. ✅✅ 2026-07-23
      - [x] Etiquetar la elección del selector como `owner_explicit` mediante
        `model_selection_intent_v1` dentro del contrato durable de adapter; el
        modo `default` solo puede nacer desde el snapshot gobernado de M.7.
      - [x] Distinguir selección `default` frente a `owner_explicit` en el contrato
        durable de asignación, sin inferirlo solo por presencia de `model`.
        - [x] Normalizar todos los flujos owner mediante
          `model_selection_intent_v1/owner_explicit`, vincular `candidate_id` al
          par canónico exacto y rechazar intentos `default` desde APIs owner.
        - [x] Persistir `mode=default` únicamente desde M.7 con ganador
          auto-elegible y snapshot reproducible; ningún cliente puede fabricarlo.
      - [x] Probar create/update, aceptación de hiring, reconcile y reload: una
        selección explícita nunca se reemplaza y una default solo se resuelve
        cuando existe candidato auto-elegible.
        - [x] Reconcile preserva byte a byte el par y
          `model_selection_intent_v1` de un agente no-placeholder.
        - [x] Cubrir create/update, materialización del hiring y reload de UI;
          PATCH del mismo par hereda la marca byte a byte y candidate IDs
          falsificados fallan antes de persistir. Evidencia: 186 tests dirigidos,
          3 E2E del selector, TypeScript, ESLint y Ruff verdes.
        - [x] Reconcile conserva byte a byte un `mode=default` interno; canarios
          herméticos de Lead y quorum demuestran snapshot `auto_applied` y
          ganador elegible antes de materializarlo.
      - Reauditoría 2026-07-23: el PATCH del mismo par vuelve a vincular la
        intención heredada contra el candidato canónico y rechaza metadata
        antigua/manipulada; onboarding normaliza el `candidate_id` en backend en
        vez de confiar en React. Hiring, fallback y cambios cross-adapter ya
        revalidaban el par exacto antes de mutar, y `default` continúa reservado
        al snapshot sellado. Evidencia: 240 tests dirigidos, Ruff, TypeScript,
        ESLint y build verdes, 8/8 E2E y 1378 tests backend globales.
    - [x] La opción “Default” final muestra candidato ganador, score,
      confianza, breakdown resumido y por qué supera al siguiente; el owner
      puede fijar cualquier alternativa compatible.
    - [x] Usar todo el contexto de proyecto/issue: run profile, criticidad, data class,
      tools, presupuesto y presión de cuota. La nota base del catálogo no cambia;
      el `selection_score` contextual sí puede hacerlo y queda explicado.
    - [x] Aplicar finalmente el mismo componente y endpoint en onboarding, hiring propuesto,
      edición de agente, bootstrap Lead, quorum y fallbacks presentados al owner.
    - Cierre: la primera opción visible coincide con la decisión backend y una
      selección explícita sobrevive reconcile sin ser sobrescrita.
  - [x] **M.7 Default automático y rollout seguro**.
    - [x] Elegibilidad previa al ranking: adapter conectado+verde, modelo exacto
      verificado/selectable, compatibilidad completa, política automática y
      evidencia exacta `calibrated` y no stale. `partial`, sin test,
      `manual_only`, datos/tools incompatibles o cuota agotada quedan fuera del
      default aunque continúen visibles para comparación o selección manual.
      La proyección contextual eleva este resultado verificable al snapshot; no
      deriva elegibilidad de rank, score alto ni presencia en catálogo.
    - [x] Ejecutar primero shadow ranking contra defaults actuales y registrar
      divergencias, calidad esperada, coste/cuota y razones sin cambiar equipos.
      `POST /api/model-catalog/selection/shadow` persiste el set completo con
      hash e idempotencia. Smoke local: 6 roles × 48 candidatos, 6 `no_winner`,
      0 mutaciones y agentes byte a byte intactos; recibo
      `benchmarks/results/model_default_rollout/model-default-shadow-2026-07-22.json`.
    - [x] Activar después solo para nuevas plazas sin modelo fijado; nunca mutar
      agentes existentes ni cambiar de adapter silenciosamente. Sin candidato
      elegible, conservar default explícito o pedir owner, no inventar fallback.
      - [x] Construir `selection_intent/mode=default` solo desde snapshot
        `auto_applied`, ganador elegible y hash recalculado; un snapshot shadow o
        manipulado falla cerrado.
      - [x] Añadir flag/rollback `AITEAM_MODEL_DEFAULT_ROLLOUT` con promoción
        `shadow → recommend → auto`; ausente o inválido cae a `shadow`.
      - [x] Conectar la primera cohorte: plazas dinámicas nuevas creadas desde
        issues o liveness. `recommend` observa sin cambiar; `auto` exige snapshot
        sellado y deja `default_unresolved` si no hay ganador, inmune a reconcile.
      - [x] Extender la cohorte a bootstrap Lead/Tier 3 y quorum con canarios
        herméticos: pin owner intacto, Lead sin ganador aborta+limpia, Tier 3
        conserva builtin explicado y quorum aplica dos snapshots de perspectivas
        distintas cuando existen. Cada alta libera su write lock antes del
        snapshot siguiente; el ensure idempotente recupera una caída parcial.
    - [x] Validar con canarios de todos los roles y al menos dos canales,
      incluyendo adapter rojo, score alto incompatible, precio desconocido,
      quota pressure, evidencia stale, tie y override manual.
      - [x] Cobertura hermética de Lead, Tier 3 y quorum cross-channel, además de
        no-winner, rollback inválido y persistencia/reconcile. Evidencia: 238
        tests dirigidos, Ruff/diff limpios y 1329 tests globales en verde.
      - [x] Regenerar antes el preflight vivo sin inferencia. Drift pasa 6/6
        gates con inventarios Codex 0.145.0 y Antigravity; cobertura exacta:
        46 modelos/131 pares, 8 `calibrated`, 5 `partial`, 32
        `requires_canary`, 4 `requires_tool_fixture` y 79 `blocked`. Recibos
        canónicos del 2026-07-22 actualizados.
      - [x] **M.7.1 Cohorte crítica de defaults, dos canales**: cerrar con tres
        semillas por par los huecos premium de Sol y Gemini 3.1 Pro High para
        `architect`, `lead`, `lead_executor`, `quorum_auditor` y `team_lead`.
        No extrapolar Lead a sus aliases; Pro High/Lead parte de `partial`.
        - [x] **M.7.1.1 Congelar matriz y criterio de cierre**: harness común
          `benchmark_critical_default_roles.py`, cinco roles exactos, dos
          familias causales y tres semillas por familia. El agregado rechaza
          muestras ausentes, duplicadas o mezcladas y nunca autoriza por sí solo
          un cambio de default. Los recibos antiguos de Lead quedan como
          diagnóstico porque no comparten ambas familias ni prueban aliases.
        - [x] **M.7.1.2 Ejecutar Sol**: completar y auditar 6 muestras por rol
          para los cinco roles exactos mediante `codex_subscription`, conservando
          versión, duración, tokens expuestos, respuesta y evaluación.
          - [x] `lead`: v1 quedó 4/6 por omitir la ventana causal; el contrato
            productivo v2 corrige la causa y la matriz nueva completa 6/6.
          - [x] `architect`: 6/6, agregado con seis fuentes hasheadas y evidencia
            exacta registrada como `calibrated`; no activa defaults antes de M.7.4.
          - [x] `lead_executor`: 6/6 tras corregir un falso negativo léxico del
            juez sin repetir inferencias; evidencia exacta `calibrated`.
          - [x] `quorum_auditor`: v1 quedó 4/6 al omitir el rollout; v2 completa
            6/6 y conserva el agregado anterior como historial diagnóstico.
          - [x] `team_lead`: 6/6, agregado sellado y evidencia exacta
            `calibrated`.
        - [x] **M.7.1.3 Ejecutar Gemini 3.1 Pro High**: completar la misma matriz
          mediante `antigravity_subscription`; la ausencia de telemetría de
          tokens se registra como unknown, no como coste cero.
          - [x] `lead`: v1 quedó 5/6 al omitir el aceptador; v2 completa 6/6.
          - [x] `architect`: 6/6, agregado con seis fuentes hasheadas y evidencia
            exacta registrada como `calibrated`; tokens permanecen unknown.
          - [x] `lead_executor`: v1 quedó 5/6 al omitir la ventana; v2 completa
            6/6.
          - [x] `quorum_auditor`: v1 quedó 5/6 al omitir el límite tenant; v2
            completa 6/6.
          - [x] `team_lead`: 6/6, agregado sellado y evidencia exacta
            `calibrated`.
        - [x] **M.7.1.4 Integrar la evidencia**: revisar agregados y fallos,
          registrar solo pares 6/6 como `calibrated`, regenerar cobertura+drift
          y mantener cualquier par incompleto fuera de auto-elegibilidad.
          - [x] El validador recalcula
            identidad, matriz, versión y hashes de las seis respuestas; tampering
            degrada a `partial` y ahora comprueba también `prompt_version`.
            Cobertura regenerada: 18 calibrados, 4 parciales, 23 requieren
            canario, 4 fixture, 3 manuales y 79 bloqueados.
        - [x] **M.7.1.5 Mejorar contratos sin cherry-picking**: añadir al prompt
          productivo Tier 1 una pasada interna de retención causal (cohorte,
          límite de scope/tenant, métrica+valor+ventana+acción, owner+aceptador,
          dependencia y rollback). Medir v1→v2 primero sobre las cinco familias
          débiles; sólo una matriz v2 completa de dos casos × tres semillas puede
          sustituir un diagnóstico o registrar calibración.
          - [x] Screening pareado: las cinco familias suben de 1/3 o 2/3 a 3/3.
            Después se ejecutaron los casos complementarios: 30/30 respuestas v2
            pasan y los cinco pares alcanzan 6/6. Los diez pares Tier 1 de la
            cohorte quedan calibrados; los agregados v1 permanecen versionados
            como historial y ningún agregado mezcla prompts. Evidencia final:
            110 tests dirigidos, Ruff, 1392 tests backend, 30 recibos+5
            agregados+5 comparaciones JSON válidos y cero patrones de secretos.
      - [x] **M.7.2 Cohorte económica y tools**: cerrar Luna en `file_scout`,
        `worker` y el parcial `web_scout`; Flash 3.5 High en QA/Test Designer,
        Medium en Worker y Low en Context Curator/File Scout/Worker. Web Scout
        requiere fixture MCP/tool gobernada y no un prompt sin herramienta.
        - [x] **M.7.2.1 Luna Tier 3, contrato v2**: corregidos tres defectos
          previos al rerun: `worker` carecía de skill, el prompt consolidado no
          exigía el `AGENT-REPORT` exacto y el caso `file_scout` invadía review.
          Con Luna `low`, `worker` completa 3/3 en una run y `web_scout` 3/3 con
          MCP allow/deny y llamada read reales; ambos quedan `calibrated`.
          `file_scout` conserva hechos 3/3 pero solo cierra en una run 1/3, por
          lo que queda `partial` y no se ajusta otra vez el prompt sobre las
          mismas semillas. Agregados enlazan fuentes y hashes; tampering degrada
          evidencia. Cobertura: 20 calibrados, 4 parciales, 21 canarios, 4
          fixtures, 3 manuales y 79 bloqueados. Evidencia: 109 tests dirigidos,
          Ruff, 1396 tests backend y 12 JSON auditados sin secretos ni hashes
          divergentes.
        - [x] **M.7.2.2 Flash 3.5 High Tier 2**: calibrar por separado `qa` y
          `test_designer` con tres semillas, artefacto conductual independiente
          y cierre durable; no extrapolar el 3/3 de Reviewer.
          - [x] Harnesses Terra generalizados por perfil+modelo sin cambiar
            casos ni suites. QA completa 3/3 ciclos ataque→fix→verificación y
            30/30 gates (mediana 130,733 s). Test Designer completa 3/3,
            15/15 mutantes ocultos y 24/24 gates (mediana 55,266 s). Un falso
            negativo de sintaxis `active=False` y caches `__pycache__` se
            corrigieron determinísticamente sin repetir inferencias. Usage
            Antigravity permanece `unknown`; ambos agregados enlazan fuentes y
            hashes y quedan `calibrated`, sin autorizar defaults. Cobertura:
            22 calibrados, 4 parciales y 19 canarios. Evidencia: 17 tests
            dirigidos, Ruff, 1403 tests backend y 8 artefactos activos íntegros,
            sin secretos ni hashes divergentes.
        - [x] **M.7.2.3 Flash económico Tier 3**: evaluar Medium/`worker` y
          Low/`context_curator`, `file_scout`, `worker` con contrato exacto por
          rol; mantener unknown de tokens Antigravity y comparar solo latencia,
          convergencia y calidad.
          - [x] Harness Tier 3 generalizado por perfil+modelo sin inventar
            `reasoning_effort`; agregados exigen tres semillas, fuentes únicas
            y hashes. Medium/`worker` pasa 3/3 en un intento (mediana 70,640 s)
            y Low/`file_scout` 3/3 (80,080 s); ambos quedan `calibrated`.
          - [x] Low/`worker` queda `partial`: 2/3, mediana 54,660 s. La semilla
            2 agota 240 s, converge en el segundo intento y repite la opción
            prohibida “jueves”; el fallo se conserva y no se re-rollea.
          - [x] Low/`context_curator` pasa la matriz causal auth+queue 6/6,
            un intento por célula, mediana 96,300 s y rango 42,300–169,700.
            Fuente, rúbrica, artefacto y recibo quedan vinculados por hash.
            Los cuatro pares conservan usage/tokens `unknown`; ningún resultado
            autoriza defaults. Cobertura final: 25 calibrados, 4 parciales,
            16 canarios, 4 fixtures, 3 manuales y 79 bloqueados. Verificación:
            20 tests dirigidos, 1409 backend, Ruff limpio en el alcance y 19
            JSON activos válidos/sin secretos; Ruff global conserva 137
            incidencias ajenas a esta unidad.
      - [x] **M.7.3 Pools no bloqueantes**: evaluar OpenCode/local y GPT-OSS en
        su backlog propio; sus `partial`/`requires_canary` no autorizan defaults
        ni bloquean la cohorte Codex+Antigravity. Conservar decisiones negativas
        de structured output y no repetirlas sin cambio de transporte/contrato.
        - [x] **M.7.3.1 OpenCode Zen 1.18.4, cierre por no-cambio**:
          revalidar versión, catálogo y hashes de los recibos existentes.
          Mantener DeepSeek Reviewer `partial` 1/3 y el resto sin promoción;
          no repetir inferencias mientras JSON Schema siga terminando en
          `StructuredOutputError`/`structured=null`.
          Cierre 2026-07-23: versión y catálogo siguen exactos, Big Pickle
          continúa rechazado y el recibo de cierre hashea la evidencia con
          `inference_runs=0`; DeepSeek Reviewer permanece `partial` 1/3.
        - [x] **M.7.3.2 GPT-OSS 120B en Antigravity**: sustituir el screening
          scout genérico por contratos durables exactos de `file_scout`,
          `web_scout` con MCP gobernado y `worker`. El screening exacto aplica
          fail-fast: `file_scout` y `worker` fallan en la primera semilla por
          ausencia de `submit_work`; web recibe primero saturación de
          infraestructura y el único retry falla igual al parsear. No se
          consumen semillas adicionales incapaces de superar el hard gate.
          El `partial` scout previo queda acompañado del diagnóstico exacto;
          tokens Antigravity permanecen `unknown`.
          Revalidación por evento 1.1.6: `worker` vuelve a fallar en seed 1 con
          el mismo `submit_work JSON object not found` tras 18,219 s; workspace
          intacto, una sola run y fail-fast sin seeds 2–3. El recibo 1.1.6
          sustituye al 1.1.5 solo para este par exacto y lo difiere hasta otro
          cambio material.
        - [x] **M.7.3.3 Ollama instalado**: evaluar únicamente
          `qwen2.5-coder:14b` en `file_scout`/`context_curator`,
          `gemma4:e4b` en `file_scout`/`context_curator`/`worker` y
          `gemma4:26b` en `engineer`/`reviewer`/`test_designer`. Medir
          calidad, convergencia, latencia y throughput/recursos cuando puedan
          observarse; no descargar `qwen3-coder:30b` ni abrir LM Studio ausente.
          Qwen 14B y Gemma E4B fallan todos sus contratos exactos. Gemma 26B
          Engineer queda `partial` 1/3; Reviewer y Test Designer fallan, este
          último porque su suite no pasa la baseline aunque detecte 5/5
          mutantes. No hay promoción ni default. En todos los modelos locales,
          coste monetario/API, tokens externos y cuota externa son 0: esta
          ventaja se puntúa como economía conocida e ilimitada; RAM/VRAM,
          energía, latencia y throughput quedan como ejes separados de host.
          Cobertura resultante: 25 calibrados, 5 parciales, 15 canarios,
          4 fixtures, 3 manuales y 79 bloqueados. Verificación: 140 tests
          dirigidos, Ruff limpio en el alcance, 1420 tests backend y 18
          recibos/snapshots de esta unidad válidos y sin patrones de secretos.
      - [x] **M.7.4 Snapshot vivo de promoción**: tras M.7.1–M.7.2, observar
        health, cuota/capacidad y precio del par exacto, persistir shadow por rol
        y comprobar que adapter rojo, incompatibilidad, precio desconocido,
        quota pressure, stale, tie y override siguen fallando cerrado. Solo
        entonces decidir `recommend → auto` para plazas nuevas.
        Cierre 2026-07-23: 14 roles × 46 candidatos quedan persistidos en
        snapshots shadow con hash válido, sin `auto_applied` ni cambios de
        asignación. El precio/economía declarados existen en 644/644
        observaciones, pero solo 17 tienen economía normalizada; 392 tienen
        adapter rojo y las 644 observaciones mantienen capacidad `no_data` o
        `capacity_unknown`. La matriz adversarial revalida y cierra health,
        incompatibilidad, precio, cuota, stale, empate exacto y override. Se
        autoriza `recommend` en la plantilla, no `auto`; rollback inmediato:
        `AITEAM_MODEL_DEFAULT_ROLLOUT=shadow`. Un ganador proyectado se vuelve a
        validar antes de sellarlo y la identidad ya no rompe empates exactos con
        autoridad automática. Verificación: 124 tests dirigidos, Ruff limpio en
        el alcance, 1424 tests backend y recibo JSON válido/sin secretos.
    - Cierre: una única función de selección compartida por bootstrap, hiring,
      Equipo y dispatch; snapshot durable y rollback/flag de desactivación.
  - [ ] **M.8 Cobertura completa y mantenimiento continuo**.
    - [x] Hacer que los lotes A–E alimenten las métricas normalizadas para todos
      los proveedores y roles; una celda sin test permanece visible como deuda,
      no recibe una puntuación de calidad inventada.
      `model_normalized_metrics_v1` recorre la cobertura completa y solo
      materializa tasa de calidad+evidencia para una celda exacta `calibrated`,
      fresca, con recibos y validación limpia. Producción consume el registro por
      defecto; 25/25 calibrados reciben calidad conocida y los parciales,
      negativos y no probados permanecen sin ella. Las versiones ausentes en
      health pueden usar el último drift autenticado, fresco y 6/6 como fallback
      con provenance; cualquier gate/fecha inválidos lo rechaza. Read model vivo:
      46 candidatos, 25 métricas normalizadas, cero auto-elegibles y cero
      fallos de auditoría. Recibo:
      `benchmarks/results/model_catalog_read_model/model-catalog-read-model-2026-07-23.json`.
      Verificación: 48 tests dirigidos, Ruff limpio en alcance, 1429 tests
      backend y dos snapshots JSON válidos/sin secretos.
    - [x] Para cada modelo enumerar todos los roles canónicos: las celdas
      incompatibles quedan explicadas y sin score; cada celda compatible que
      pueda entrar en selección automática recibe fixture/canario exacto y
      valoración propia. No extrapolar una prueba de Engineer a Reviewer/Lead.
      - [x] **M.8.2.1 Matriz y taxonomía**: `CANONICAL_ROLES` publica una
        taxonomía ordenada de 17 roles sin duplicar aliases; el read model
        materializa 46 × 17 = 782 celdas.
      - [x] **M.8.2.2 Incompatibilidad y deuda exacta**: las 666 celdas
        incompatibles conservan código/razón y evidencia histórica como
        antecedente, pero nunca score. El auditor falla cerrado ante taxonomía
        divergente, matriz incompleta, score incompatible o deuda automática sin
        acción exacta. Las alternativas se limitan al mismo perfil/proveedor.
      - [x] **M.8.2.3 Canarios de candidatos operativos**: el recuento inicial
        de 29 era incorrecto porque `manual_only=false` se extrapolaba a roles
        no nominados. La política automática ahora exige simultáneamente que el
        modelo la permita y que el rol figure en `best_for`; la compatibilidad
        manual del resto no cambia. Quedan solo dos pares verdes no calibrados:
        Luna/File Scout y Flash Low/Worker. Ambos poseen agregado exacto de tres
        semillas, identidad+contrato estables, fuentes enlazadas y artefactos
        con hash; su resultado parcial impide promoción y fija
        `no_rerun_until_material_change`. No se consumieron runs nuevas.
      - [x] **M.8.2.4 Valoración propia**: normalizar el resultado de cada celda
        aprobada y demostrar que ninguna recibe quality/capability por evidencia
        de otro rol; mantener parciales y fallos visibles sin promoción.
        Los 25 pares calibrados reciben quality solo en su identidad exacta; los
        cinco parciales, incluidos los dos anteriores, no reciben quality.
        El auditor rechaza cualquier ruta automática operativa sin recibo.
      Cierre 2026-07-23: 782 celdas, 666 incompatibles, 116 compatibles; 71
      compatibles no nominadas quedan manuales y 45 nominadas conservan gates
      por rol. Ninguna ruta automática operativa carece de evidencia exacta.
      Recibo vivo con cero fallos, cero auto-elegibles y un warning stale.
      Verificación: 84 tests dirigidos, Ruff limpio y 1434 tests backend.
      Reproyección 2026-07-24: al añadir el requisito central
      `web_scout -> external_mcp` y el modelo 47, la matriz pasa a 799 celdas,
      697 incompatibles y 102 compatibles; 62 son compatibles no nominadas y
      40 nominadas compatibles. Conserva cero auto-elegibles y auditoría verde.
    - [x] Separar benchmarks de capacidad general de los canarios exactos por
      rol/tools y usar varias familias de casos para reducir overfitting.
      - [x] **M.8.3.1 Taxonomía de evidencia**:
        `model_evidence_taxonomy_v1` separa `general_capability_benchmark`,
        `exact_role_canary` y `exact_tool_fixture`, con scopes y prohibiciones
        explícitas. Los cuatro `research_score` declarados quedan visibles como
        generales, no normalizados y prohibidos en el score de rol.
      - [x] **M.8.3.2 Gate anti-overfitting**: `model_role_score_v2` añade
        `case_diversity`; el número de seeds/casos ya no sustituye familias
        independientes. Una sola familia conserva quality exacta visible, eleva
        Goodhart a material y bloquea automática. El auditor rechaza taxonomy,
        evidence kind o gate divergentes y cualquier fuga de benchmark general.
      - [x] **M.8.3.3 Segunda familia por cohorte**, tres semillas y mismo par
        exacto; no reutilizar la primera familia como si fuera diversidad:
        - [x] Coding: Terra/Engineer y Sonnet/Engineer (2 pares).
          Segunda familia `config_redactor`, distinta del `cli_conversor`.
          Terra completó 3/3 seeds, 9/9 tests ocultos agregados y Ruff limpio:
          su agregado de dos familias queda 6/6 muestras y abre
          `case_diversity`. Sonnet seed 1 pasó 3/3 tests ocultos pero falló Ruff
          por `F401 pytest imported but unused`; fail-fast evitó seeds 2–3.
          Conserva quality de la primera familia, diagnóstico exacto y gate de
          diversidad rojo hasta cambio material. No se corrigió su artefacto.
        - [x] QA adversarial: Terra/QA y Flash High/QA (2 pares).
          Segunda familia `webhook_replay_boundary`, causalmente distinta de
          autorización multi-tenant: firma inválida, expiración y replay
          stateful. Terra completó 3/3 seeds y 30/30 gates; su agregado enlaza
          6/6 muestras y abre `case_diversity`. Flash High completó el ataque de
          seed 1 con tres tests rojos, pero la reverificación agotó 240 s con
          `subscription_cli_timeout`; fail-fast detuvo seeds 2–3. Conserva su
          calibración anterior y diagnóstico, sin abrir diversidad ni atribuir
          el timeout a calidad del modelo.
        - [x] Test Designer: Terra y Flash High (2 pares).
          Segunda familia `job_state_machine_mutation`, distinta del cálculo de
          pricing: transiciones, terminales, errores e inmutabilidad. Terra
          completó 3/3 seeds, 24/24 gates y 15/15 mutantes; su agregado enlaza
          6/6 muestras y abre `case_diversity`. Flash High completó seed 1 con
          8/8 gates y 5/5 mutantes; seed 2 también mató 5/5, pero agotó 240 s
          antes de reporte/cierre durable. Fail-fast detuvo seed 3: conserva
          diagnóstico operacional y calibración anterior, sin abrir diversidad.
        - [x] Tier 3: Luna/Worker, Luna/Web Scout, Flash Medium/Worker y Flash
          Low/File Scout (4 pares). Worker añadió triaje causal de incidente,
          File Scout inspección de idempotencia de pagos y Web Scout un segundo
          advisory gobernado. Luna/Worker, Flash Medium/Worker y Luna/Web Scout
          completaron 3/3 seeds, single-attempt y artefactos exactos; sus
          agregados enlazan 6/6 muestras y abren `case_diversity`. Flash
          Low/File Scout falló seed 1 antes de inferencia durable con
          `subscription_cli_parse_error: submit_work JSON object not found`;
          fail-fast detuvo seeds 2–3. Conserva quality anterior y diagnóstico,
          sin abrir diversidad. Dos equivalencias legítimas del juez Worker y
          una traducción de Web Scout se revaluaron sin repetir proveedor.
        - [x] MCP Operator: Terra añadió `dependency_policy_lookup` permitido y
          `publish_policy` denegado, independiente del advisory. Completó 3/3
          seeds y 36/36 gates de health recovery, allow/deny, trace, ausencia de
          write, reporte y single-attempt. El agregado enlaza 6/6 muestras y
          abre `case_diversity`. Los receipts antiguos se versionaron y
          rehashearon mediante reevaluación determinista, sin nueva inferencia.
      - [x] **M.8.3.4 Recalibración diversity-aware**: tras los agregados
        anteriores, registrar nuevas familias/contrato, normalizar y regenerar
        snapshots. Un fallo mantiene quality de la familia antigua pero no abre
        `case_diversity`.
      Cierre 2026-07-23: 25 pares conservan quality exacta; 21 son
      multi-familia y 4 mono-familia con diagnóstico y fail-fast. El catálogo v2
      tiene 23 canarios de rol,
      2 fixtures exactos de tools, cero auto-elegibles y cero fallos. El snapshot
      de promoción v2 conserva `recommend`, nunca `auto`. Verificación: 162
      tests dirigidos históricos; la cohorte Coding añade 82 tests focalizados,
      el cierre deja 117 tests focalizados, Ruff limpio y una suite completa de
      1451 tests backend.
    - [ ] Recalcular por evento de modelo/CLI/precio/cuota/prompt/tool/contrato y
      mensualmente; conservar histórico y tendencias sin retirar por edad sola.
    - Cierre: 100 % del inventario visible, 100 % de rutas automáticas con
      evidencia fresca y cada hueco restante con owner/bloqueador/próxima acción.

- [x] **Desbloquear y probar Luna como `context_curator`**. `✅✅`
  Doble comprobación completada el 2026-07-22.
  - [x] Codex CLI actualizado de 0.128.0 a 0.145.0; cache autenticado con
    `client_version=0.145.0` y probe efímero read-only `LUNA_OK` completado.
  - [x] Comparar Luna con GPT-5.5 en auth y queue, tres semillas por caso,
    mismas anclas, ratio total, runs y duración.
  - [x] GPT-5.5 sin override de esfuerzo: 6/6; Luna sin override: 3/6;
    prompt v2 sin override: 4/6; Luna `medium` v3: 6/6, 36,55 s
    medianos y menos tokens medianos que el control histórico. Las ramas sin
    override no prueban causalmente `low` y no autorizan decisiones por esfuerzo.
  - [x] Recibo agregado:
    `benchmarks/results/model_calibration/context-curator-gpt-tier3-cli-0.145.0-aggregate-v3.json`.
  - Cierre: matriz completa, juez causal/determinista, recibo agregado y decisión
    explícita. Un fallo de versión o catálogo es diagnóstico, no calidad.
  - Evidencia previa:
    `benchmarks/results/context-curator-auth-codex-luna-seed-1.json`.
  - [x] Reauditar el 2026-07-22: configuración `medium` recuperada en modo
    read-only de las seis DB originales, provenance persistida en 30 recibos y
    auditor endurecido para fallar cerrado ante celdas, rol, modelo, canal,
    estado o esfuerzo incorrectos. Evidencia dirigida: 140 tests verdes.

- [ ] **Completar calibraciones nuevas por perfil+modelo+rol**.
  - Sol/Terra/Luna, Opus/Sonnet/Haiku y Pro/Flash/Flash-Lite se comparan contra
    baselines locales antes de cambiar gates o cascadas.
  - Estado vivo: 8 pares `calibrated`, 17 `partial`, 17
    `deferred_until_material_change`, 0 `requires_canary`, 0
    `requires_tool_fixture`, 3 manuales y 79 bloqueados. Ningún candidato es
    auto-elegible; una calibración positiva conserva quality exacta, no concede
    un default. El histórico de 25 calibrados permanece visible aunque versiones
    nuevas vuelvan parciales sus promociones.
  - Antigravity conserva históricamente 12 pares calibrados y 2 parciales; la
    actualización 1.1.6 vuelve stale Sonnet/Engineer para promoción nueva. Sus
    tres pendientes históricos ya no son acciones repetibles. GPT-OSS/Worker
    fue reabierto por Antigravity 1.1.6 y la seed 1 volvió a fallar en 18,219 s
    con `submit_work JSON object not found`; queda diferido contra el recibo
    exacto 1.1.6 y no consume seeds 2–3. File Scout permanece partial y Web
    Scout incompatible.
    Flash Low/Web Scout quedó cerrado negativamente por ausencia de MCP
    gobernado en Antigravity 1.1.6, sin extrapolar calidad. El cambio
    Antigravity 1.1.6 activó la revalidación Sonnet/Engineer; falló por 7
    incidencias Ruff pese a 3/3 hidden y queda fail-fast, sin completar la
    matriz hasta otro cambio material. Dos roles Flash High tampoco repiten la
    segunda familia fallida sin cambio material.
  - Gemini 3.6 High/Low fueron catalogados pero no ejecutables; Medium completó
    review sin superar al baseline. No repetir runs idénticas sin cambio de
    modelo, CLI, catálogo o contrato.
  - Cierre por candidato: tres semillas mínimas, contrato de rol exacto,
    evidencia independiente, liveness, mediana+rango y gate de promoción.

- [ ] **Calibrar promociones gratuitas provisionales**.
  - Gemini Free 3.5 Flash y GPT-OSS 120B: review/QA/test design.
  - Flash-Lite, Qwen 3.6 y GPT-OSS 20B: scouts/context curator.
  - Mantener bloqueados Lead/quorum, review crítico Tier 3, MCP externo y datos
    no compatibles hasta superar canarios exactos de contrato, criticidad y
    recovery.
  - OpenCode no se reabre salvo cambio de catálogo/modelo, CLI o contrato: sus
    canarios actuales, incluido Ling 3.0 Flash, no autorizaron promoción.

- [ ] **Extender BYOK gratuito solo con catálogo ejecutable demostrado**.
  - GitHub Models y OpenRouter requieren credencial real, discovery por ID,
    salida estructurada, probe exacto y límites observados.
  - Calibrar Gemini/Groq por rol antes de ampliar `supported_roles` o defaults.
  - Persistir rate-limit headers sin secretos cuando el helper pueda conservarlos.
  - Bloqueo actual: no hay keys de esos cuatro perfiles y `gh` carece de
    `models:read`; no crear perfiles hasta resolverlo.

- [x] **Cerrar el drift abierto por Codex 0.145.0**. `✅✅`
  Doble comprobación completada el 2026-07-22. El registro apunta al par
  exacto Luna/`context_curator`, CLI 0.145.0 y seis recibos v3 más el agregado.
  El auditor confirma catálogo, flujo, tiers y frescura: 6/6 gates.
  La reauditoría conectó el snapshot Codex actual directamente a inventario y
  cobertura —sin permitir que health histórico oculte modelos retirados— y
  valida el contenido de los recibos, no solo su existencia. Casos negativos
  de Luna ausente y agregado manipulado fallan cerrados; 44 tests dirigidos y
  recibo vivo regenerado sin inferencias.

### Criterio de cierre P0

Cada opción habilitada debe tener identidad exacta, catálogo vigente, probe o
run ejecutable, compatibilidad de rol y calibración suficiente para cualquier
promoción. Discovery, tier o health de un hermano nunca conceden capacidad.
Además, cada contratación automática debe resolver desde la proyección común el
mejor candidato elegible del rol, persistir score/breakdown/confianza/provenance
y coincidir con lo que muestran Modelos y Equipo.

## P1 — Endurecimiento condicionado por evidencia

- [ ] **Mantener telemetría comparable para Antigravity** antes de usarlo en
  comparaciones de coste. `agy 1.1.5` no expone tokens headless por run; no
  parsear la TUI ni inventar estimaciones.
- [ ] **Robustecer clasificación de cuotas de suscripción** cuando exista señal
  estructurada o variantes reales. Añadir solo fixtures observados; conservar
  fallback seguro y no reintentar cuota agotada.
- [ ] **Extraer políticas de quorum solo si vuelven a crecer** y aparece una
  frontera funcional verificable. No dividir mecánicamente `RunExecutor`.

### Criterio de cierre P1

Cada extracción reduce una política mutable sin cambiar semántica; cada señal
nueva conduce a una acción operativa demostrable.

## P2 — Estudios que requieren datos reales

### Coste por entrega/proyecto

- [ ] Repetir `scripts/audit_cost_report_readiness.py` cuando una misma SQLite
  acumule cinco entregas terminales comparables por perfil, ≥80 % de runs
  temporizadas, ≥80 % con provenance de coste y ≥80 % con señal de calidad.
- [ ] Solo si el gate pasa, implementar API/UI por entrega y proyecto,
  separando coste real de ahorro estimado.

Estado: el recibo
`benchmarks/results/cost_reporting/cost-report-readiness-v1.json` auditó 70 de
71 DB y no encontró ningún proyecto listo.

### Paralelismo por canal

- [ ] Obtener un trigger vivo con múltiples raíces y pools y espera
  paralelizable exacta mayor que cero. No fabricar evidencia con fixtures.
- [ ] Tras el trigger, ejecutar A/B secuencial/paralelo con misma cola y
  workspace, varias semillas y canales distintos. Medir makespan, espera,
  calidad, runs, usage disponible, cuota, checkout y liveness.
- [ ] Activar o ampliar límites solo con mejora consistente sin regresiones; de
  lo contrario mantener el opt-in y cerrar con evidencia negativa.

Estado: el inventario vivo no encontró candidatos y
`parallel-live-trigger-inventory-v1.json` conserva `live_ab_allowed=false`.

### Orientación del usuario

- [ ] Reclutar y ejecutar ocho sesiones humanas consentidas según
  `docs/FRONTEND_ORIENTATION_STUDY.md`, con órdenes contrabalanceados y sin
  excluir bajo rendimiento.
- [ ] Agregar únicamente conteos y medianas en un recibo separado y evaluar los
  gates prerregistrados. No concluir adopción, retención, productividad,
  satisfacción, causalidad o claridad universal.

Estado: persistencia, consentimiento, revocación/borrado, UI, E2E y preregistro
están cerrados. El recibo sintético `orientation-flow-v1.json` no sustituye
sesiones humanas.

## Mantenimiento no bloqueante

- [ ] Eliminar, solo tras liberar handles o corregir ACL, los temporales exactos
  `.tmp_pytest/tmpi0cx_njg`, `.tmp_pytest/tmpmzgfjkhr` y
  `.tmp_dispatch_growth_d_46h9iy`. No tocar caches o runtime ajenos.
- [x] Compactar `task.md`: cierres trasladados a `docs/HISTORY.md`; el backlog
  conserva orden, bloqueadores, criterios, decisiones y recibos canónicos.
- [x] Publicar el bloque anterior: `65eb862`, `c9dd733` y `f1227e4` están en
  `origin/master` después de 1229 tests y revisión de secretos/diff.

## Decisiones vigentes

- `lead_quorum` solo se activa por perfil explícito; una tarea simple no exige
  quorum ni review pesado.
- Lead es autoridad, no proveedor. Plan A, síntesis y Plan B pertenecen al Lead
  asignado.
- El default es secuencial; `AITEAM_PARALLEL_CHANNELS` sigue opt-in.
- API, suscripción y local son canales distintos; diversidad de pool no implica
  diversidad de perspectiva.
- Una opción visible no es seleccionable hasta demostrar ejecutabilidad exacta.
- OpenCode es read-only; sus permisos de tools no constituyen sandbox.
- APIs pueden materializar ops bajo RBAC, pero no reciben MCP externo gobernado.
- `actual_cost_cents=0` no significa cuota o tokens ilimitados.
- Context curator usa Luna con esfuerzo `medium` tras superar auth+queue 6/6;
  GPT-5.5 queda solo como control histórico.
- Sonnet 4.6 conserva evidencia de Engineer en Antigravity; Flash High conserva
  review/QA. Ninguno queda auto-elegible mientras no supere todos los gates.
- Gemini 3.6 y modelos gratuitos provisionales no se promocionan por discovery.
- Una calibración stale bloquea promociones nuevas, no cambia defaults por sí sola.
- No implementar poda de `dispatch_candidate_decisions` mientras su benchmark
  permanezca bajo thresholds; no aplicar TTL global a telemetría durable.

## Evidencia canónica

- Plan rector: `docs/MIGRATION_PAPERCLIP.md`.
- Orquestación: `docs/ORCHESTRATION.md`.
- Estado operativo: `HANDOFF.md`.
- Historial cerrado: `docs/HISTORY.md`.
- Drift/calibraciones:
  `benchmarks/results/model_catalog_drift/model-catalog-drift-2026-07-23.json`.
- Crecimiento de decisiones:
  `benchmarks/results/dispatch_decision_growth/dispatch-decision-growth-v1.json`.
- Canarios de perfiles y calibración de modelos:
  `benchmarks/results/model_calibration/`.
- Context curator: `benchmarks/results/context_curator*.json` y variantes
  `context-curator-*`.
- Paralelismo: `benchmarks/results/parallel_channels/`.
- Coste: `benchmarks/results/cost_reporting/`.
- Orientación: `benchmarks/results/frontend_orientation/`.

## Verificación mínima por bloque

Backend/documentación:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\audit_model_flow_matrix.py
.\scripts\python_local.bat scripts\audit_model_catalog_drift.py
```

Canarios de perfiles, solo cuando cambie runtime/orquestación:

```powershell
.\scripts\python_local.bat scripts\e2e_full_team_canary.py
.\scripts\python_local.bat scripts\e2e_quorum_canary.py
.\scripts\python_local.bat scripts\e2e_solo_lead_canary.py
```

Frontend, solo si el diff toca `ide-frontend/`:

```powershell
Set-Location ide-frontend
npm run check
```

Durante iteración usar gates proporcionales; reservar suite completa y canarios
relevantes para cerrar un bloque material.
