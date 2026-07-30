# Plan y trabajo vigente

Actualizado: `2026-07-30`

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
- Tier 1 reservado a máxima calidad, con habilitaciones independientes
  `lead_ready` y `quorum_ready` configuradas de extremo a extremo; score alto,
  tier o discovery nunca sustituyen la calibración exacta de autoridad;
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
- Codex CLI usa la prerelease oficial `0.146.0-alpha.6`, compatible con la
  caché autenticada `0.146.0`, y enumera Sol/Terra/Luna. Sol vuelve a estar
  ejecutable y completa 30/30 muestras Tier 1. Terra/Reviewer queda
  recalibrado en `0.146.0-alpha.6`; Terra/Engineer tiene una revalidación
  negativa actual y queda diferido hasta cambio material. Los demás pares
  Terra y todos los de Luna conservan evidencia `0.145.0` visible pero stale.
  El A/B causal auth+queue
  deja GPT-5.5 como control histórico y Luna `medium` como Tier 3 histórico,
  no fresco.
- La separación Tier 1 está cerrada de extremo a extremo y visible en
  API/Catálogo/UI.
  Tras el drift Antigravity 1.1.8, Gemini Pro High se revalidó por separado
  como Lead y quorum: ambos carriles vuelven a 2/2 con Sol/Codex, dos
  perspectivas y dos pools. La UI
  consume la autoridad exacta del backend, sin deducirla del score. Selección,
  onboarding, Equipo, hiring, defaults, quorum, fallback, reconcile, dispatch,
  recovery y liveness aplican el mismo hard gate fail-closed y el auditor
  transversal P0.h.4e comprueba su paridad.
- Paralelismo continúa opt-in; no existe trigger vivo representativo.
- El informe de coste y las conclusiones de orientación esperan volumen real.

## Orden de ejecución

1. Ampliar cobertura útil sin rebajar calidad: P0.h.2d restaura primero Tier 2
   stale y añade segundas perspectivas a sus roles críticos.
2. Calibrar únicamente pares modelo+rol cuyo canal y credencial estén realmente
   disponibles e ingerir sus recibos en la proyección canónica; no crear perfiles
   decorativos ni confundir selección manual con automatización.
3. Mantener verificados instalación portable, doctor, registro poliglota,
   Catálogo y selección de Equipo mientras se añaden proveedores/modelos.
4. Usar el gate adapter→calibración P0.g ya cerrado para ejecutar las
   remediaciones de canales prioritarios solo cuando exista un cambio material
   que reabra su diagnóstico.
5. Implementar P0.K, asistente guiado de primer uso y nuevo proyecto, para
   configurar y probar correctamente máquina, adapters y equipo antes de crear.
6. Implementar P0.N para descubrir cambios de CLI/MCP/adapters y catálogos de
   modelos, clasificarlos y avisar al developer con remediación durable.
7. Ejecutar los estudios condicionados solo cuando aparezca su trigger real:
   entregas, paralelismo, señales de cuota o participantes humanos.
8. Repetir drift/calibraciones por evento y en la fecha programada.

Próximo bloque ejecutable local: **P0.N.3, persistencia y scheduling durable de
cambios de proveedor**. P0.N.1 ya fija el vocabulario y las fuentes admisibles;
P0.N.2 normaliza probes read-only y emite diffs SHA-bound sobre cinco
superficies sin actualizar ni conceder routing.
K.8.6.1 ya cierra la matriz hermética, K.8.6.2 deja el
runner Windows alineado con el commit guiado actual y K.8.6.3a–b prueban en CI
independiente tanto clon limpio como actualización de checkout, con auditor
agregado 9/9. K.8.6.3c queda pendiente de una instalación física de otro
usuario y exige su consentimiento; no bloquea el trabajo local de P0.N.
K.8.1 ya atribuye el histórico, K.8.2
impide contaminación nueva, K.8.3 produce una lista exacta sellada y K.8.5
guía/avisa sin mutar. K.8.4.2 continúa bloqueado hasta una aprobación owner
exacta; no se autoriza todavía mover ni borrar el histórico. P0.K.1–K.7,
P0.f, P0.g e I.10 ya están cerrados.
P0.h.2d.5b MCP Operator solo se reabre cuando aparezca una segunda perspectiva
con transporte MCP gobernado real. Terra ya queda
renovado en Codex `0.146.0-alpha.6`: 6/6 muestras, 72/72 gates y una sola
run por muestra; la cobertura sube de 0/2 a `single_point` 1/2. Sol no aporta
diversidad porque comparte perspectiva y pool con Terra. Hoy no existe un
segundo brazo ejecutable: Ollama no está instalado, LM Studio está archivado,
OpenCode 1.18.4 no habilita el rol/structured output y los adapters API o
Antigravity no ofrecen loop MCP gobernado. No repetir hasta un cambio material
en uno de esos canales. Test Designer ya queda
`covered` 2/2 con Terra y Flash High sobre dos familias y mutantes ocultos. La
segunda autoridad Tier 1 ya quedó restaurada en Antigravity 1.1.8 sin heredar
evidencia entre Lead y quorum. QA Tier 2 queda `covered` 2/2 con Terra y Flash
High, dos perspectivas y dos pools. Engineer no se
repite hasta un cambio material: Terra pasó 9/9 tests ocultos pero dejó dos
incidencias Ruff, y Sonnet pasó 3/3 pero dejó siete. El owner ha
despriorizado por ahora I.8.4c/d Linux/macOS,
Containers, Mobile nativo y PHP/Ruby/Swift. Web moderno queda cerrado con
30/30 celdas CI; Docker continúa opcional y nunca requisito.
M.8 permanece cerrado con mantenimiento mensual/event-driven durable; sus
cuatro diagnósticos mono-familia no se repiten hasta cambio material.

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
      o POSIX. Linux/macOS ejecutan los 17 gates originales y, desde I.10.4,
      el gate redacted de versiones CLI como paso 18, incluida salud,
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

- [x] **I.10 Gate final de versiones CLI e instalación**.
  - Antes de cerrar distribución/instalación, construir una comparación
    determinista entre: versiones o rangos aceptados en
    `docs/INSTALLATION_AND_INTEGRATION.md`; ejecutables finales resueltos por la
    misma frontera que usan los adapters; e inventario/versiones publicados por
    `machine_doctor_v1`.
  - Cubrir al menos Codex, Antigravity y OpenCode, además de cualquier CLI que
    pase a requisito o recomendación activa. Identidad del ejecutable,
    canal/tag (`stable`, prerelease explícita), versión semántica y
    compatibilidad con catálogo/cache deben coincidir; `installed` sin versión
    verificable no pasa.
  - El check debe fallar cerrado ante documentación stale, dos binarios
    distintos resueltos por doctor/runtime, prerelease no declarada, CLI más
    antiguo que su catálogo o doctor que omita una herramienta documentada.
    Componentes opcionales ausentes siguen siendo no bloqueantes si la guía y
    el doctor coinciden en su opcionalidad.
  - Integrarlo en la aceptación final de release y conservar un receipt
    redacted ligado a SHA/OS/arquitectura. Cierre: una instalación limpia y una
    actualización existente producen la misma matriz documentada y doctor no
    reporta drift de versión.
  - [x] **I.10.1 Autoridad canónica**: añadir una única matriz versionada para
    cada CLI activo/recomendado con identidad, comandos candidatos,
    opcionalidad, rango o versión aceptada, canal `stable|prerelease`, comando
    de versión y requisitos de catálogo/cache. Codex, Antigravity y OpenCode
    son el mínimo; la guía y `installation_support_v1` deben referenciar esta
    autoridad, no copiar rangos divergentes.
    Cerrado el 2026-07-30: `provider_cli_version_contract_v1` vive dentro de
    `config/installation_support.v1.json`, referencia las filas de adapter para
    no duplicar comandos y fija suelo validado, versión exacta, canales,
    prereleases explícitas y guard de catálogo. Codex declara
    `0.146.0-alpha.6`, Antigravity `1.1.8` y OpenCode `1.18.4`; son baselines
    locales fechados, no afirmaciones de latest ni sustitutos de health. El
    loader rechaza referencias, requisitos, canales, prereleases, duplicados u
    omisiones incoherentes. Verificación: 45 tests de instalación/doctor/
    release y 9 tests directos pasan; Ruff F/I y diff check quedan verdes,
    salvo el aviso CRLF ya conocido del CSS no tocado por este bloque.
  - [x] **I.10.2 Resolución compartida**: hacer que doctor y adapters consuman
    la misma frontera de resolución. El recibo publicará nombre y fingerprint
    redacted del ejecutable —nunca una ruta personal— para detectar dos
    binarios distintos, shims inesperados o versión no observable.
    - [x] **I.10.2a Frontera única**:
      `platform_runtime.resolve_provider_cli` gobierna doctor y runtime,
      incluidos shims Windows y la ubicación conocida de Antigravity bajo
      `LOCALAPPDATA`. El doctor vivo resuelve `codex.cmd`
      `0.146.0-alpha.6`, `agy.exe` 1.1.8 y `opencode.cmd` 1.18.4 sin paths
      personales. Verificación: 119 tests integrados y 106 al repetir el núcleo,
      Ruff F/I y diff check verdes.
    - [x] **I.10.2b Identidad redacted**: añadir un fingerprint estable que
      combine identidad de resolución y contenido sin emitir la ruta; doctor,
      runtime y auditor deben comparar el mismo valor y fallar ante dos
      binarios distintos aunque compartan nombre/versión.
      Cerrado el 2026-07-30: `provider_cli_fingerprint` usa un dominio
      versionado y liga path normalizado resuelto + SHA-256 del contenido en un
      único digest; solo publica basename y 64 caracteres hex. Es estable para
      el mismo binario, cambia por ruta o contenido y devuelve `null` si no
      puede verificar el archivo. `machine_doctor_v1` y su schema lo incorporan.
      El doctor vivo observa fingerprints no nulos para Codex
      `0.146.0-alpha.6`, Antigravity 1.1.8 y OpenCode 1.18.4. Verificación:
      122 tests integrados, Ruff F/I y diff check verdes; I.10.3 será quien
      convierta un `null` o mismatch en bloqueo de aceptación.
  - [x] **I.10.3 Auditor fail-closed**: comparar matriz, versión observada,
    canal/tag y compatibilidad catálogo/cache. Rechazar documentación stale,
    prerelease no declarada, versión fuera de rango, identidad distinta,
    catálogo más nuevo que el CLI y CLI documentado omitido por doctor. Un
    opcional ausente pasa únicamente si todas las superficies lo declaran
    opcional.
    Cerrado el 2026-07-30 con `provider_cli_version_audit_v1` y
    `scripts/audit_provider_cli_versions.py`. El auditor compara doctor y
    resolución runtime por instalación, versión SemVer/prerelease, basename y
    fingerprint; exige suelo, canal y prerelease declarada. La guía debe
    referenciar schema, contrato y los tres CLIs. Codex valida cache vivo,
    versión instalada/catalog client y modelos; Antigravity/OpenCode consumen
    evidencia de catálogo ≤31 días ligada a la versión exacta. Codex y
    Antigravity son alternativas: puede faltar uno, pero no ambos; OpenCode
    ausente sigue siendo opcional. El recibo
    `provider-cli-version-audit-2026-07-30.json` queda
    `identity_version_ok=true`, `catalog_ready=true`,
    `documentation_ready=true`, `promotion_ready=true`, cero fallos, cero paths
    personales y cero patrones de secretos. Verificación: 70 tests integrados,
    Ruff F/I y diff check verdes.
  - [x] **I.10.4 Integración durable**: proyectar el resultado en
    `machine_doctor_v1`, añadir receipt redacted ligado a SHA/OS/arquitectura e
    incorporarlo a la aceptación de release como gate 18 antes de promoción.
    Cerrado el 2026-07-30: doctor proyecta estado, cuatro gates y SHA-256 del
    auditor; un resultado no listo añade bloqueo estricto
    `provider_cli_version_gate_failed`. El receipt
    `machine-doctor-i10-4-2026-07-30.json` conserva proyección `ready`, hash,
    OS/arquitectura, mutation guard verde, cero paths personales y cero
    secretos. Windows/POSIX crean shims efímeros versionados y cache Codex bajo
    el fixture, ejecutan el auditor sin instalar CLIs globales ni login y
    persisten resumen/identidades redacted. El contrato de release pasa de 17 a
    18 pasos con `provider_cli_version_gate`; workflow hereda el bloqueo a
    través del harness. La integración detectó y corrigió que doctor
    sobrescribía `config.command=["agy"]` con `["agy.exe","agy"]`, pudiendo
    observar otro binario que runtime. Verificación dirigida: 61 tests,
    compilación de ambos harnesses, Ruff E402/F/I y diff check verdes; el
    fixture Windows completa identidad+catálogo+docs con tres shims. Regresión
    backend completa: 1735 passed, 2 skipped. Ambos receipts son JSON válido,
    no contienen rutas personales ni patrones de secreto y el SHA-256 canónico
    del reporte de doctor coincide. El doctor global conserva bloqueos locales
    ajenos; solo la proyección de este gate queda `ready`.
  - [x] **I.10.5 Aceptación de actualización**: demostrar con fixtures y
    recibos que clone limpio y `git pull` existente resuelven la misma matriz;
    cubrir binario duplicado, upgrade requerido, prerelease explícita,
    opcional ausente y documentación/matriz desincronizadas.
    Cerrado el 2026-07-30: el contrato
    `provider_cli_update_acceptance_v1` compara `clean_clone` con
    `existing_checkout_after_fast_forward` y exige el mismo SHA-256 de matriz.
    El recibo `provider-cli-update-acceptance-2026-07-30.json` conserva además
    el preflight bloqueado del checkout antiguo por versión inferior y
    fingerprint divergente. Cinco canarios negativos cubren binario duplicado,
    upgrade requerido, prerelease no declarada, documentación obsoleta y
    catálogo/matriz desincronizados; todos fallan cerrado. OpenCode ausente pasa
    únicamente como opcional. El fixture no muta instalaciones globales, no
    lee secretos, no inicia login ni ejecuta inferencias; el receipt no contiene
    paths personales ni patrones de secreto. Verificación: 60 tests integrados,
    Ruff E402/F/I y diff check verdes; regresión backend completa posterior:
    1738 passed, 2 skipped.
  - Cierre I.10 (`2026-07-30`): autoridad, resolución, identidad redacted,
    auditor fail-closed, doctor, gate 18 de release y equivalencia clean/update
    quedan implementados y verificados. Este cierre certifica el contrato de
    instalación de los CLIs; no concede auth, health ni calibración de modelos.

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

## P0.K — Primer uso y creación de proyecto guiados

- [ ] **P0.K Rediseñar onboarding y Nuevo proyecto como wizard durable**:
  sustituir las pantallas monolíticas por un flujo claro, reanudable y
  explicable que enseñe cómo funciona AI Teams, prepare la máquina, ayude a
  configurar el máximo de adapters realmente utilizables y no permita empezar
  con una configuración aparentemente válida pero rota.
  - [x] **P0.K.1 Contrato y máquina de estados**: inventariar el flujo actual y
    definir `guided_setup_v1` con pasos versionados, dependencias, estado
    `not_started/in_progress/blocked/skipped/passed`, back/next, guardado
    incremental, resume tras reinicio y reset explícito. Separar onboarding de
    máquina, creación/importación de proyecto y edición posterior; nunca
    encerrar el estado solo en React.
    Cerrado el `2026-07-30` con `guided_setup_v1`: tres scopes versionados
    (`machine_onboarding` 6 pasos, `project_setup` 7,
    `installation_repair` 4), dependencias ordenadas y estados fail-closed.
    `guided_setup_sessions/steps` persisten en una SQLite de configuración de
    máquina disponible antes de crear proyecto; `schema.sql` conserva el mismo
    contrato para portabilidad. Create/resume es idempotente, cada transición
    usa revisión optimista, los borradores `in_progress` se guardan, blocked
    reanuda y reset exige confirmación. Payloads admiten `secret_ref` pero
    rechazan valores de API key/password/token. API autenticada:
    contract/create/get/transition/reset. Recibo 10/10:
    `benchmarks/results/guided_setup/guided-setup-contract-2026-07-30.json`;
    15 tests focalizados, Ruff y schema/migración verdes.
  - [x] **P0.K.2 Entrevista de necesidades**: formulario breve y adaptativo
    sobre objetivo, programativo/no programativo, lenguajes/toolchains,
    sensibilidad de datos, presupuesto, suscripciones/APIs disponibles,
    preferencia local/cloud, autonomía, criticidad y colaboración. Explicar por
    qué se pregunta, ofrecer recomendaciones y permitir “no lo sé”, omitir y
    volver atrás sin perder datos.
    Cerrado el `2026-07-30` con `guided_setup_needs_v1`: 12 preguntas
    explicadas, recomendaciones deterministas y visibilidad adaptativa; los
    borradores incompletos siguen reanudables y `unknown` evita forzar
    decisiones falsas. El objetivo explícito prevalece y la clasificación
    inferida exige confirmación. El resultado sella respuestas, resumen,
    perfil `solo_lead/lead_quorum/full_team` y estrategia de canales con
    SHA-256; backend/SQLite recalculan y rechazan respuestas incompletas,
    scopes erróneos o resúmenes manipulados al completar el paso. Los modelos
    locales solo aparecen tras opt-in. API autenticada de contrato/evaluación.
    El fixture de estudio de empresa de limpieza recomienda `research`, sin
    ejecutar inferencia ni crear tests de software. Recibo 10/10:
    `benchmarks/results/guided_setup/guided-setup-needs-2026-07-30.json`;
    25 tests focalizados y Ruff verdes.
  - [x] **P0.K.3 Preparación de máquina y adapters**: mostrar requisitos como
    `required`, `recommended` u `optional`; detectar instalación, versión,
    autenticación, catálogo, health y contrato exacto. Guiar Codex,
    Antigravity, OpenCode y APIs elegidas, incluidas claves personales, sin
    leerlas ni mostrarlas. Suscripción y API permanecen canales distintos.
    Nunca presentar Ollama, LM Studio, Docker o todos los CLIs como
    imprescindibles ni instalar globales/aceptar términos automáticamente.
    - [x] **P0.K.3.1 Proyección read-only de preparación**: convertir la
      entrevista sellada y `machine_doctor_v1` en requisitos
      `required/recommended/optional` y seis fases separadas: instalación,
      versión, autenticación, catálogo, health y contrato. Cerrado el
      `2026-07-30` con `guided_setup_preparation_v1`: inventario inseguro falla
      cerrado; presencia o versión nunca implican adapter verde; catálogo y
      contrato requieren evidencia exacta; Lead sigue bloqueado hasta completar
      todas las fases. OpenCode gratuito es recomendado, nunca requerido; API
      personal queda sin verificar hasta elegir proveedor; Ollama/LM Studio solo
      aparecen como opcionales tras opt-in. 36 tests de preparación,
      entrevista, doctor e instalación y Ruff verdes.
    - [x] **P0.K.3.2 API durable y persistencia de evidencia**: exponer el plan
      por API autenticada, guardar solo referencias/recibos redacted y enlazar
      su revisión con `adapter_setup` sin duplicar el estado canónico.
      Cerrado el `2026-07-30`: `POST
      /api/guided-setup/sessions/{id}/preparation` carga la entrevista desde
      SQLite, ejecuta `machine_doctor_v1` en servidor y no acepta inventario ni
      `provider_evidence` del cliente. Persiste un recibo compacto con hashes de
      needs/plan/doctor, readiness y bloqueadores, ligado por FK al paso; no
      copia inventario, rutas ni credenciales. Revisión optimista evita carreras.
      `adapter_setup` no puede pasar sin el último recibo server-side listo y
      sustituye cualquier evidencia forjada por la durable. Recibo 10/10:
      `benchmarks/results/guided_setup/guided-setup-preparation-persistence-2026-07-30.json`;
      23 tests focalizados y Ruff verdes.
    - [x] **P0.K.3.3 Guías por proveedor y acción humana**: Codex,
      Antigravity, OpenCode y APIs elegidas con instrucciones de
      instalar/actualizar/login/secret-ref, consentimiento y errores
      recuperables; ninguna ejecución automática.
      Cerrado el `2026-07-30` con `guided_setup_provider_guidance_v1`: la API
      devuelve solo los canales solicitados, versiones mínima/validada y
      acciones ordenadas por instalación, versión, auth, catálogo, health y
      contrato. Todas son `manual_only`, requieren confirmación y declaran
      riesgo/evidencia; completar una guía nunca concede readiness. Los
      instaladores remotos se señalan como riesgo, OpenCode explica API key
      personal, oferta temporal y `non_confidential_only`, y las APIs guardan la
      clave solo por `/api/user-adapters/secrets` conservando `secret_ref`.
      Local permanece opt-in. No se ejecutaron comandos, logins, términos,
      inferencias ni probes. Recibo 10/10:
      `benchmarks/results/guided_setup/guided-setup-provider-guidance-2026-07-30.json`;
      17 tests focalizados y Ruff verdes.
    - [x] **P0.K.3.4 Catálogo, health y probe exactos**: consumir discovery,
      health y contrato estructurado canónicos manteniendo `not_checked` cuando
      falte evidencia y avisando de cuota antes de probes remotos.
      Cerrado el `2026-07-30` con `guided_setup_provider_evidence_v1`: la API
      carga una única proyección redacted de perfiles, la comparte con doctor y
      deriva auth, catálogo, health y contrato por separado. Catálogo
      autenticado no implica calidad; `verified_models` o una run completada no
      implican salida estructurada. Contract exige JSON declarado, recibo
      relativo seguro, fecha ≤30 días y versión CLI exacta; health/auth exigen
      `checked_at` ≤24 h y catálogo API persistido también. Evidencia antigua,
      sin recibo, con versión distinta o ruta absoluta queda `not_checked`.
      Los probes remotos siguen siendo una acción manual con confirmación y
      aviso de posible cuota; este cierre no ejecutó ninguno. Recibo 10/10:
      `benchmarks/results/guided_setup/guided-setup-provider-evidence-2026-07-30.json`;
      23 tests focalizados y Ruff verdes.
    - [x] **P0.K.3.5 Auditoría y aceptación de reparación**: fixtures de
      máquina limpia/parcial, CLI obsoleto, auth ausente, catálogo incompatible,
      API válida, offline/rate-limit y opt-in local; recibo redacted y
      reanudación sin instalaciones silenciosas.
      Cerrado el `2026-07-30` con
      `guided_setup_adapter_repair_acceptance_v1`: 10/10 escenarios verdes.
      Se añadió selección explícita de IDs de perfiles API ya configurados; el
      servidor valida que sean canal API y calcula toda evidencia, de modo que
      el cliente elige proveedor pero no estados. Una API Lead-capable con
      catálogo, health y JSON exactos puede quedar lista usando versión de
      transporte declarada, mientras suscripciones siguen ligadas a CLI.
      Máquina limpia/parcial, CLI antiguo, auth ausente, catálogo incompatible
      y offline/rate-limit degradan con una sola acción recuperable; local solo
      aparece por opt-in. Create/resume conserva revisión y recibo sin
      reinstalar. El harness no ejecutó comandos, probes, logins, términos,
      inferencias ni consumió cuota. Recibo:
      `benchmarks/results/guided_setup/guided-setup-adapter-repair-acceptance-2026-07-30.json`;
      24 tests focalizados y Ruff verdes. Con K.3.1–K.3.5, P0.K.3 queda cerrado.
  - [x] **P0.K.4 Cobertura y recomendaciones progresivas**: visualizar qué
    adapters/modelos están verdes, qué roles cubren y qué falta para
    `solo_lead`, `lead_quorum` o `full_team`. Recomendar primero una ruta mínima
    Lead-capable y después opciones gratuitas, diversidad/quorum y workers
    económicos. Maximizar adapters configurados significa ampliar cobertura
    útil con consentimiento, no acumular instalaciones. Cada sugerencia expone
    coste/cuota, privacidad, capacidades, estado y motivo.
    - [x] **P0.K.4.1 Contrato de cobertura por perfil y rol**: proyectar
      `solo_lead`, `lead_quorum` y `full_team` desde rankings
      `model_selection_v1` sin reconstruir scores. Cerrado el `2026-07-30` con
      `guided_setup_coverage_v1`: solo
      `candidate_is_automation_eligible` concede cobertura; selección manual,
      discovery o calibración incompleta permanecen visibles pero no cuentan.
      `solo_lead` exige Team Lead; `lead_quorum`, Lead y dos auditores con
      perspectivas y pools distintos; `full_team`, Lead+Engineer+Reviewer.
      Adapters fuera del conjunto preparado se excluyen. Cada candidato
      proyecta perfil/modelo, proveedor, canal, tier, rank, score, capacidades,
      privacidad, gates y economía. Local y suscripción tienen coste marginal
      cero; API sigue metered. Recibo 10/10:
      `benchmarks/results/guided_setup/guided-setup-coverage-contract-2026-07-30.json`;
      33 tests de contrato/selector/blueprints y Ruff verdes.
    - [x] **P0.K.4.2 API de cobertura canónica**: cerrada el `2026-07-30`.
      `POST /api/guided-setup/sessions/{id}/coverage` reutiliza la misma
      reconstrucción server-side que preparación, valida revisión y perfiles
      API, carga catálogo/perfiles una vez y obtiene
      `contextual_model_selection` para Team Lead, quorum, Engineer, Reviewer y
      el Worker informativo que alimenta la recomendación económica.
      Después filtra por los IDs cuyo adapter terminó realmente `ready`; un
      perfil conocido pero no preparado no cuenta. La consulta autenticada
      expone hash de catálogo y contexto de selección, no persiste preparación,
      no cambia defaults y no crea proyectos. Evidencia aportada por el cliente
      queda prohibida. Prueba de API con revisión stale, filtro de adapter,
      cinco roles y snapshot SQLite antes/después; 10 tests focalizados y Ruff
      verdes.
    - [x] **P0.K.4.3 Recomendaciones progresivas**: cerrada el `2026-07-30`
      con `guided_setup_recommendations_v1`. Ordena ruta mínima Lead-capable,
      quorum/diversidad, equipo completo y Worker económico. Si hay varios
      adapters Lead incompletos recomienda exactamente uno y conserva los demás
      como alternativas; stages ya pasados y adapters verdes nunca generan
      reinstalación. Un adapter verde sin Lead auto-elegible dirige a reparar
      elegibilidad/calibración, no a instalar. Quorum/full team solo son
      obligatorios cuando corresponden al perfil recomendado; workers de coste
      marginal cero quedan opcionales y al final. No instala ni cambia defaults.
      Recibo 10/10:
      `benchmarks/results/guided_setup/guided-setup-recommendations-2026-07-30.json`;
      17 tests focalizados y Ruff verdes.
    - [x] **P0.K.4.4 Visualización de cobertura**: cerrada el `2026-07-30`
      con el componente reutilizable `GuidedSetupCoverage`. El panel industrial
      muestra perfiles recomendado/alternativos, siguiente acción server-side y
      matriz por rol con modelo, adapter, score —cero no se confunde con dato
      ausente—, gates, coste marginal/cuota, privacidad, capacidades, estado y
      motivo. Los candidatos bloqueados siguen visibles y desplegables, pero
      separados de los elegibles; esta corrección al backend alinea el código
      con el contrato de K.4.1. Semántica nativa, `aria-live`, responsive y
      reduced motion; React no recalcula cobertura ni recomendación. Queda
      preparado para que K.7 lo inserte en el shell completo del wizard.
      Verificación: 2 tests de componente, 19 backend del delta, TypeScript,
      ESLint, Stylelint, límite de módulo, build y presupuesto de bundle verdes.
    - [x] **P0.K.4.5 Auditoría y aceptación**: cerrada el `2026-07-30` con
      `guided_setup_coverage_acceptance_v1`. Pasa 10/10: sin Lead, Lead único,
      quorum sin diversidad, full team parcial/completo, Worker local gratuito,
      API limitada visible pero excluida, owner override manual sin cobertura
      automática, paridad exacta con `candidate_is_automation_eligible`+adapter
      preparado e inputs inmutables. La prueba API conserva revisión/paso
      SQLite y rechaza evidencia cliente; el componente muestra bloqueados sin
      promoverlos. Recibo:
      `benchmarks/results/guided_setup/guided-setup-coverage-acceptance-2026-07-30.json`.
      Sin instalaciones, secretos, proyectos, inferencia, cuota ni cambios de
      defaults. Con K.4.1–K.4.5, P0.K.4 queda cerrado.
  - [x] **P0.K.5 Configuración del proyecto y equipo**: crear/importar carpeta,
    detectar ecosistemas, confirmar objetivo e instrucciones `.aiteam/`,
    proponer perfil y formar Lead-first mediante el selector canónico. Mostrar
    equipo, modelo/canal por rol, score/gates, presupuesto y degradaciones antes
    de guardar; permitir override explícito sin saltar compatibilidad.
    - [x] **P0.K.5.1 Contrato read-only de propuesta**: cerrado el
      `2026-07-30` con `guided_setup_project_proposal_v1`. Valida intent
      create/import, identidad observada por servidor, needs selladas,
      detección segura, instrucciones y perfil; construye el blueprint canónico
      y asigna Team Lead primero mediante candidatos ya ordenados. Quorum exige
      dos candidatos, perspectivas y pools distintos. Expone presupuesto,
      accountability, degradaciones y save gate, con hash estable. Override
      manual requiere `owner_selectable`, privacidad compatible y adapter
      preparado; no concede cobertura, no puede reutilizar candidato y exige
      confirmación. Inputs inmutables y cero filesystem/DB/agentes/wakeups.
      Verificación conjunta K.5.1: 31 tests y Ruff verdes.
    - [x] **P0.K.5.2 API canónica de propuesta**: cerrada el `2026-07-30`.
      `POST /api/guided-setup/sessions/{id}/project-proposal` reconstruye
      identidad y objetivo desde la sesión, confina la ruta resuelta bajo
      `projects_root`, detecta ecosistemas de forma acotada y read-only,
      recompone adapters/evidencia y obtiene cinco selecciones contextuales
      desde un único catálogo. Devuelve propuesta, cobertura, preparación y
      hash/contexto; no crea siquiera `projects_root`, `.aiteam/`, DB, agentes
      o wakeups. Revisión stale, path externo, evidencia/inventario cliente y
      overrides forjados fallan cerrados. Verificación transversal: 61 tests,
      Ruff y diff check verdes.
    - [x] **P0.K.5.3 Commit revalidado y recuperable**: cerrado el
      `2026-07-30` con
      `POST /api/guided-setup/sessions/{id}/project-commit` y
      `guided_setup_project_commit_v1`. El cliente solo aporta revisión
      esperada, hash del preview, las mismas elecciones y confirmación; el
      servidor recompone identidad/ruta, inventario, evidencia, catálogo,
      cobertura, compatibilidad y propuesta. Un hash distinto devuelve `409`
      antes de escribir. Create construye proyecto+`.aiteam/` en un sibling
      temporal e import construye solo `.aiteam-staging-*` dentro del proyecto;
      ambos publican por rename y eliminan exclusivamente sus árboles propios
      ante fallo. El commit no vuelve a ejecutar ningún selector: verifica y
      persiste exactamente `profile_id`, `model_id`, candidato e intent del
      preview. Una transacción SQLite crea objetivo/intake, Team Lead primero,
      agentes, blueprint activo, accountability/asignaciones y un único wakeup;
      por estar aún en staging, el heartbeat no puede verlo antes del publish.
      `.aiteam/instructions.md` y `project_config.json` quedan ligados al hash.
      Un recibo global durable y único por sesión hace el replay idempotente y
      rechaza otro hash; un recibo cuyo DB ya no existe falla cerrado. Create
      conserva además el git gestionado opcional; import nunca toca archivos ni
      historia ajenos. Verificación: 68 tests guided-setup verdes, Ruff y diff
      check verdes; rollback inyectado cubierto en create/import.
    - [x] **P0.K.5.4 Interfaz de proyecto/equipo**: cerrada el `2026-07-30`
      con `ProjectSetupWizard` y `ProjectProposalReview`. El primer uso queda
      dividido en identidad create/import, objetivo proporcional, recursos y
      perfil, y revisión sellada. La UI solo selecciona adapters preparados y
      envía intent; sesión, needs, propuesta y commit pasan por los endpoints
      K.5.1–K.5.3. La revisión muestra destino, ecosistemas, perfil, equipo
      Lead-first, modelo/proveedor/canal, score, hard gates, tier,
      coste marginal/cuota, presupuesto sellado, degradaciones y hash. El
      override por rol invalida el preview y obliga a regenerarlo; Guardar solo
      existe para `save_gate.allowed`. K.8.2 retiró después físicamente el
      endpoint `/api/projects/new`, su control React y `aiteam project create`;
      ya no sobrevive una segunda ruta de materialización.
      Se añadió bootstrap explícito para no mostrar un falso onboarding mientras
      carga un workspace y se corrigió la carrera que vaciaba `projects_root`.
      Diseño cockpit industrial responsive, navegación atrás, teclado, estados
      semánticos y `prefers-reduced-motion`. Verificación: 12/12 tests React,
      TypeScript+Vite build, ESLint, Stylelint, límites de módulos y bundle
      verdes; Playwright real sin errores de consola y revisión visual desktop
      y 390×844.
    - [x] **P0.K.5.5 Auditoría y aceptación**: cerrada el `2026-07-30` con
      `guided_setup_project_acceptance_v1`. La matriz 13/13 materializa create e
      import en árboles temporales y SQLite real; conserva archivos ajenos y
      demuestra que un estudio `research` crea solo Lead, un wakeup y cero
      roles de programación/tests. Rechaza ruta externa, colisión, revisión
      stale, perfil sin cobertura, quorum sin diversidad y override inválido;
      mantiene el override válido como `owner_explicit` sin convertirlo en
      cobertura. Detección truncada exige confirmación, un fallo inyectado
      limpia create/import sin tocar el workspace ajeno y sesión+recibo
      sobreviven reanudación, replay idempotente y hash conflictivo. El recibo
      sella evidencia y detecta manipulación:
      `benchmarks/results/guided_setup/guided-setup-project-acceptance-2026-07-30.json`.
      Verificación: 89 tests guided-setup, 3 tests propios del auditor, Ruff y
      test unitario del wizard verdes; cero secretos, inferencias, cuota,
      defaults, configuración o proyectos del usuario mutados. Con
      K.5.1–K.5.5, P0.K.5 queda cerrado.
  - [x] **P0.K.6 Preflight y prueba antes de empezar**: ejecutar doctor,
    permisos/rutas, toolchains, conexión y catálogo de adapters seleccionados,
    probe estructurado mínimo y fixture proporcional al tipo de proyecto. No
    confundir discovery con calidad ni convertir un proyecto teórico en una
    fábrica de tests. Terminar con resumen go/no-go, warnings, acciones guiadas
    y “Entrar al proyecto” solo tras persistencia consistente; los opcionales
    pueden quedar pendientes explícitos.
    - [x] **P0.K.6.1 Contrato puro de preflight proporcional**: componer
      propuesta sellada, needs, path observado, doctor, preparación de adapters
      y recibos de fixture sin ejecutar comandos. Separar gates obligatorios,
      warnings y opcionales; `research`/`operations` usan contratos
      deterministas sin tests, `software` exige smoke ejecutable y `mixed` solo
      lo exige cuando hay superficie software detectada. Exponer go/no-go,
      siguiente acción, consentimiento/cuota y hash estable.
      Cerrado el 2026-07-30 con
      `guided_setup_project_preflight_v1`: seis gates deterministas recomponen
      propuesta, ruta, runtimes, adapters, toolchains y fixture sin confiar en
      el navegador ni ejecutar trabajo. Discovery no concede readiness;
      adapters exigen contrato pasado y los toolchains detectados bloquean si
      faltan. Research y operations terminan sin comandos/tests; software exige
      el receipt smoke exacto y mixed solo cuando detectó superficie software.
      Ruta no confinada, evidencia con cuota/remote calls y receipt inseguro
      fallan cerrados. El resumen separa blockers/warnings/opcionales, conserva
      `enter_project_allowed=false` hasta persistencia y queda sellado con hash
      validado contra manipulación. Auditor durable:
      `guided-setup-project-preflight-contract-2026-07-30.json`, 10/10 checks.
      Verificación: 17 tests focales, 106 tests guided-setup y Ruff verdes; cero
      proyectos/configuración/DB mutados, secretos, inferencias o cuota.
    - [x] **P0.K.6.2 API server-side y observación confinada**: recomponer la
      propuesta por revisión/hash, observar ruta/permisos y doctor en servidor,
      aceptar solo referencias de evidence y devolver el plan read-only. El
      navegador no aporta inventario, estados de provider ni resultados.
      Cerrado el 2026-07-30 con
      `POST /api/guided-setup/sessions/{id}/project-preflight`: recompone sesión,
      needs, identidad, ecosistemas, preparación, cobertura y propuesta y exige
      revisión y `proposal_hash` exactos antes de componer K.6.1. El servidor
      ejecuta doctor read-only sobre la raíz objetivo y observa existencia,
      tipo, lectura/escritura, parent y confinamiento sin crear archivos. La
      propia raíz de proyectos ya no es un proyecto válido. El request
      fail-closed prohíbe inventario, path observation o evidence inline; solo
      admite referencias `sha256:` deduplicadas. Hasta que K.6.3/K.6.4 creen su
      store durable, una referencia bien formada pero ausente falla
      explícitamente, nunca se trata como pasada. La respuesta diferencia la
      composición pura de los comandos de versión/puertos read-only del doctor
      y declara cero tests, inferencia, remote probes, cuota o mutación.
      Verificación: software queda no-go por fixture, research queda go sin
      tests, spoofing devuelve 422, hash stale 409 y create/import/root se
      observan fail-closed; 23 tests focales, 106 guided-setup y Ruff verdes.
    - [x] **P0.K.6.3 Ejecutor acotado y consentido**: ejecutar como máximo el
      probe estructurado exacto y el fixture proporcional autorizado, con
      timeout, redacción, cuota advertida, fail-fast y cero auto-install.
      Research/operations nunca invocan runners de software.
      - [x] **K.6.3a Plan y consentimiento**: derivar del preflight sellado un
        plan máximo de un fixture local y un probe modelo+adapter exacto, con
        orden económico, razones, timeout, impacto y consentimientos separados.
        Sin confirmación o con preflight/proposal divergentes no se ejecuta.
        Implementado con
        `guided_setup_project_preflight_execution_plan_v1`: hashes de
        needs/proposal/preflight, máximo 1+1 acciones, local antes que remote,
        un intento, scopes/consentimientos exactos y bloqueo temprano de ruta,
        runtime, toolchain o stack desconocido.
      - [x] **K.6.3b Fixture local proporcional**: reutilizar únicamente los
        descriptores allowlisted de `ecosystem_validation` sobre copia temporal;
        seleccionar un caso exacto desde ecosistemas/lenguajes, fail-fast,
        redacción y cleanup. Nunca instalar dependencias ni tocar el proyecto.
        El executor efímero valida todos los consentimientos/runners antes de
        empezar, ejecuta un único case allowlisted con timeouts de descriptor,
        normaliza `guided_setup_fixture_evidence_v1`, conserva el receipt por
        hash y redacciona fallos a clase. Una prueba real `python_pytest` pasa
        sobre copia temporal; el workspace del usuario permanece intacto.
      - [x] **K.6.3c Probe estructurado exacto**: extraer el probe de adapter a
        un servicio no persistente, fijar perfil+modelo+contrato, máximo un
        intento, timeout y telemetría de cuota. Exigir consentimiento remoto y
        aceptación explícita de posible consumo antes de leer el secreto.
        `guided_setup_adapter_contract_probe_receipt_v1` exige opción exacta y
        structured output declarado, runtime con timeout configurable, sandbox
        read-only vacío y submit_work `ops=[]` con marker exacto. Solo después
        de ambos consentimientos inyecta credencial. El receipt omite
        prompt/output/error/secreto y conserva únicamente código, tokens
        numéricos, coste y cuota observada; no persiste health ni catálogo.
      - [x] **K.6.3d Endpoint y receipt efímero**: API revalida revisión,
        proposal/preflight hash y consentimiento, ejecuta local antes que
        remoto, devuelve evidence redacted/hash y no altera health, catálogo,
        defaults, DB o workspace. K.6.4 será el único store durable.
        `project-preflight` publica el plan sellado y
        `project-preflight-execute` vuelve a recomponer todo, compara los tres
        hashes, valida consentimientos antes del runner y devuelve receipt más
        preflight posterior. No acepta receipts/resultados inline ni persiste
        nada; declara explícitamente que K.6.4 es obligatorio antes de commit.
        Verificación K.6.3: 30 tests focales, 129 guided-setup y Ruff verdes.
        La única ejecución material fue el fixture Python temporal; cero
        inferencias o cuota de proveedor durante el desarrollo.
    - [x] **P0.K.6.4 Persistencia y gate de commit**: guardar recibos sellados,
      reanudar/idempotencia, invalidar evidencia stale y exigir último preflight
      `go` antes de `/project-commit`. Ninguna wakeup o entrada al cockpit puede
      adelantarse al gate.
      Cerrado el `2026-07-30` con
      `guided_setup_project_preflight_receipt_v1`. SQLite conserva attempts
      append-only y artifacts content-addressed por sesión; una referencia no
      puede cruzar sesiones y su contenido/normalización se rehashea al leer.
      `project-preflight-execute` persiste el preflight posterior y hace replay
      por hash de plan sin repetir fixture o probe, incluso si después hubo
      otros intentos. `/project-commit` exige el último receipt durable `go`,
      recompone propuesta, doctor, preparación, inventario, path y fixture
      desde estado server-side y compara el objeto/hash exacto antes de crear
      filesystem, agentes o wakeup. Receipt ausente/no-go, proposal distinta,
      evidencia corrupta o cualquier input stale fallan 409; el commit existente
      conserva replay idempotente. La matriz demuestra además que antes del
      gate no existe el target ni DB de proyecto. Auditor hermético 6/6:
      `benchmarks/results/guided_setup/guided-setup-project-preflight-persistence-2026-07-30.json`.
      Verificación: 54 pruebas focales de commit/preflight/persistencia, 132
      pruebas guided-setup, Ruff y auditor strict verdes; solo se mutaron SQLite y workspaces
      temporales, sin secretos, inferencia, red, cuota ni proyecto del usuario.
      La siguiente unidad es K.6.5, UI y aceptación end-to-end.
    - [x] **P0.K.6.5 UI y aceptación**: resumen visual de blockers/warnings/
      opcionales, acciones guiadas, consentimiento explícito y botón “Entrar al
      proyecto” solo tras persistencia consistente. Matriz create/import,
      software/research/operations/mixed, offline/429, toolchain ausente,
      adapter rojo, receipt stale, retry/reanudación y anti-tampering.
      Cerrado el `2026-07-30`: `ProjectPreflightPanel` proyecta exclusivamente
      gates, plan y receipt server-side. El flujo separa fixture local, probe
      remoto y posible cuota en tres autorizaciones; research sin acciones
      declara cero tests/llamadas remotas, no-go durable conduce a recursos y
      solo un receipt `go` persistido cuyo hash coincide con el preflight
      posterior muestra “Entrar al proyecto”. Los 409 stale invalidan preview
      y regresan a recursos; 429 y offline conservan detalle/estado sin retry
      ciego. La matriz UI cubre bloqueo, research, consentimientos, no-go,
      hash vigente, import path y errores de transporte; las 135 pruebas
      guided-setup conservan create/import, los cuatro tipos de objetivo,
      toolchain/adapter, replay, stale y anti-tampering. El E2E Chromium recorre
      la aplicación real, comprueba ausencia de errores y overflow, y mantiene
      entrada deshabilitada antes del receipt. Auditor 10/10:
      `benchmarks/results/guided_setup/guided-setup-project-preflight-ui-acceptance-2026-07-30.json`.
      Verificación: 20 tests unitarios frontend, build, ESLint, Stylelint,
      typecheck, límites de módulo/bundle, 1 E2E, 135 tests guided-setup,
      3 tests de auditor y Ruff verdes; cero proveedor, inferencia o cuota.
      Con K.6.1–K.6.5, P0.K.6 queda cerrado.
  - [x] **P0.K.7 Diseño visual y accesibilidad**: interfaz de “checklist de
    puesta en marcha” coherente con el cockpit industrial de AI Teams, con
    progreso real, mapa de pasos, ayuda contextual, lenguaje humano,
    microinteracciones sobrias y jerarquía clara. Responsive, teclado, foco,
    lectores de pantalla, contraste AA, reduced motion y errores junto al
    campo. Evitar wizard genérico, modales encadenados y formularios eternos.
    - [x] **P0.K.7.1 Semántica y progreso accesibles**: convertir el mapa de
      pasos en navegación comprensible sin depender de color, publicar paso
      actual/completados y mover el foco al contenido correcto tras una
      transición sin robarlo en la carga inicial. Relacionar cada acción
      principal con su condición de avance y anunciar errores/estado durable.
      Cerrado el `2026-07-30`: `ProjectSetupProgress` publica posición y estado
      textual visible (`Actual`, `Completado`, `Pendiente`) además de icono/color,
      `aria-current` y nombre “Paso n de 4”. Solo los pasos completados son
      navegables. `useWizardStageFocus` conserva el autofocus inicial y, tras
      avanzar, retroceder, generar propuesta o invalidar un receipt stale,
      enfoca la región etiquetada por el heading del nuevo paso. La condición
      humana de avance vive en `project-setup-readiness`, se anuncia y queda
      relacionada con la acción principal; los errores alertan y describen la
      región activa. Verificación: 11 tests focales, typecheck, ESLint, límite
      de módulo, build y bundle verdes; el E2E Chromium comprueba foco y
      `aria-current` en Objetivo, Equipo y Revisión, sin errores ni overflow.
    - [x] **P0.K.7.2 Teclado, foco y errores de campo**: recorrido completo sin
      ratón, foco visible y estable al avanzar/retroceder, errores próximos al
      control con `aria-invalid`/`aria-describedby`, y recuperación de foco
      después de 409/no-go o una reparación. No usar controles deshabilitados
      sin explicar la condición pendiente.
      Cerrado el `2026-07-30`: la acción primaria permanece alcanzable y solo
      `busy` la deshabilita. Enter valida nombre/ruta, objetivo/stack y
      adapters; publica un alert junto al control, `aria-invalid`, descripción
      y foco en el primer error. Editar limpia el diagnóstico obsoleto. Modo,
      perfil y adapters preparados usan `aria-pressed`; los adapters no
      preparados conservan causa en su nombre accesible. El foco visible cubre
      inputs, botones, selects y la región programáticamente enfocada. Un 409
      stale invalida revisión y devuelve foco a Recursos; el no-go conserva su
      ruta probada de revisión de recursos. `ProjectIdentityStep` evita
      rebasar el límite modular y `invalidProjectStepControls` mantiene orden
      determinista de corrección. Verificación: 16 tests focales, 26 unitarios
      frontend, E2E Chromium con Enter/error/recuperación, typecheck, ESLint,
      Stylelint, módulos, build y bundle verdes.
    - [x] **P0.K.7.3 Responsive, contraste y movimiento**: validar desktop,
      tablet y móvil sin overflow ni acciones ocultas; contraste AA de texto,
      estados y foco; zoom/reflow y reduced motion reales. Mantener el cockpit
      industrial con densidad controlada, no convertirlo en un formulario
      genérico. Primer gate: recuperar margen significativo del bundle actual
      —321 B JS y 136 B CSS— sin subir límites antes de añadir estilos o
      instrumentación. Cerrado el `2026-07-30`: el configurador retirado queda
      fuera del render y del bundle mediante gate constante y se elimina su CSS
      exclusivo; la extirpación física de su fuente/estado queda acotada a
      K.8. El wizard usa mapa 2×2 en móvil, protocolo vertical a 520 px y
      preflight de una columna en tablet; mantiene mensaje de readiness y
      acciones visibles. E2E Chromium cubre 768, 390 y reflow WCAG a 320 CSS
      px, cero overflow, contraste AA sin violaciones, foco de teclado ≥3:1,
      capturas reproducibles y `prefers-reduced-motion` ≤0,011 ms. Se corrigió
      el ordinal del manifiesto de 2,74:1 al token AA. Verificación: 26
      unitarios, typecheck, ESLint,
      Stylelint, límites de módulo, build, bundle y E2E verdes. Bundle final:
      JS 400.925 B raw/116.129 B gzip; CSS 122.316 B raw/22.057 B gzip, sin
      elevar límites.
    - [x] **P0.K.7.4 Lectores de pantalla y auditoría automática**: nombres,
      landmarks, orden de headings, estados/live regions y controles de
      consentimiento verificables con pruebas DOM y una auditoría de
      accesibilidad en navegador. Cero violaciones críticas/serias en los
      estados representativos. Cerrado el `2026-07-30`: un E2E WCAG
      2 A/AA ejecuta Axe en seis estados —Proyecto y Objetivo limpios/con error,
      Recursos y Revisión+preflight pendiente— y obtiene cero violaciones de
      cualquier impacto. Comprueba un único `main`, navegación y región activa
      nombradas, jerarquía `h1`→`h2`→`h3` sin saltos, consentimiento con nombre
      accesible y protocolo como lista ordenada. Se corrige el hover principal
      blanco/verde de 2,62:1 y se evita que un cambio de checkbox anuncie todo
      el preflight: solo el sello durable es `aria-live=polite`,
      `aria-atomic=true`. Tests DOM sellan tres estados del protocolo y la
      frontera de la live region. Verificación: 26 unitarios, typecheck, ESLint,
      Stylelint, límites de módulo, build, bundle y E2E verdes. Bundle:
      JS 400.942 B raw/116.144 B gzip; CSS 122.514 B raw/22.108 B gzip.
    - [x] **P0.K.7.5 Aceptación visual y recibo durable**: matriz de los cuatro
      pasos, propuesta, pending/go/no-go, errores, viewport y teclado; capturas
      reproducibles, hashes de evidencia y auditor anti-tampering. Solo cerrar
      K.7 cuando unit, E2E, build, límites y auditor estén verdes. Cerrado el
      `2026-07-30` con
      `guided-setup-project-visual-acceptance-2026-07-30.json`: 10/10 checks y
      seis estados sellados —pending desktop/tablet/móvil/reflow 320, durable
      NO-GO y durable GO—. El E2E recorre los cuatro pasos, errores y teclado;
      ejecuta NO-GO, vuelve a Recursos, recompone proposal+preflight y solo
      entonces obtiene GO. Cada PNG se liga por SHA-256 a viewport, proposal,
      preflight, plan, execution receipt y durable receipt aplicables. El
      auditor reabre los binarios, rechaza path traversal, matrices incompletas,
      hashes/autoridad manipulados y sella evidencia+informe. Dos repeticiones
      producen el mismo hash visual `810fc439…45cdba5` y report hash
      `3cce823b…0072a53`; el auditor y sus tests también quedan entre las fuentes
      selladas. Se corrigieron tres defectos: el panel mostraba el
      preflight previo después de ejecutar; GO no comprobaba plan y execution
      receipt completos; y un paso completado bajo hover quedaba en 4,41:1.
      `preflightExecutionAuthorizesCommit` es ahora el guard único de panel y
      commit; un GO inconsistente falla cerrado y vuelve a Recursos.
      Verificación: 27 unitarios frontend, 38 pruebas backend/auditor focales,
      Axe tras NO-GO/GO, typecheck, ESLint, Stylelint, Ruff, límites, build,
      bundle y E2E verdes. Bundle final: JS 401.517 B raw/116.261 B gzip; CSS
      122.564 B raw/22.115 B gzip. P0.K.7 queda cerrado.
  - [ ] **P0.K.8 Integración, actualización, higiene y aceptación portable**:
    reutilizar el mismo asistente desde primer uso, Nuevo proyecto y
    Configuración para reparar o ampliar una instalación existente. Al
    actualizar se conservan proyectos, credenciales y preferencias y solo
    aparecen pasos nuevos/incompletos. Este bloque absorbe RUN-024 y el
    incidente local de carpetas numeradas: en
    `%USERPROFILE%\Documents\Antigravity Projects` se observaron el
    `2026-07-30`, mediante inventario read-only, 2.716 directorios de primer
    nivel, 2.366 con sufijo numérico y marcador `.aiteam/aiteam.db`; 2.029
    contienen además `.git`. Esto demuestra atribución a AI Teams, no que sea
    seguro borrarlos.
    - **Invariante de producto**: una instalación limpia no necesita un
      “limpiador” posterior. AI Teams solo puede crear el destino exacto
      confirmado por el owner y su `.aiteam/`; todo artefacto transaccional es
      temporal, propiedad de una operación y desaparece antes de cerrarla. No
      existirán cleanup periódico, barrido al arrancar, TTL destructivo,
      renombrado silencioso, carpeta numerada automática ni borrado inferido.
      La remediación de este incidente será manual, separada, opt-in y
      exclusiva para instalaciones legacy ya contaminadas.
    - **Orden de cierre**: K.8.2 prevención → K.8.1 inventario → K.8.3 dry-run
      legacy → K.8.4.1 motor hermético → K.8.5 UX/actualización → K.8.6
      aceptación portable. K.8.4.2 es una decisión owner separada y no bloquea
      las capas read-only; solo puede ejecutarse tras aprobar paths y dos hashes
      exactos. No se limpia el histórico mientras aún pueda generarse
      contaminación nueva.
    - [x] **P0.K.8.1 Inventario y atribución read-only**: cerrado el
      `2026-07-30` con `project_artifact_audit_v1`. El auditor
      local y portable recorre una raíz elegida explícitamente sin seguir
      symlinks/reparse points y produzca un receipt redacted. Por carpeta debe
      registrar identidad `.aiteam`, versión/schema y proyecto de la DB,
      referencias del registro/workspace, familia de nombre, timestamps,
      tamaño, Git remoto/branch/dirty/untracked y procesos/handles observables.
      Clasificar, con razones y confianza, como `active_current_project`,
      `aiteam_preserve_or_migrate`, `aiteam_disposable_candidate`,
      `ambiguous_owner_review_required` o `personal_protected`. Una carpeta
      atribuible nunca se convierte solo por ello en desechable; ausencia,
      corrupción o contradicción de evidencia cae en revisión humana.
      Implementación: `aiteam/project_artifact_audit.py` y
      `scripts/audit_project_artifacts.py`; exige root absoluto, escribe el
      receipt fuera del árbol, abre SQLite immutable/read-only, reduce remotos
      a host y branch/objetivo a hash, no sigue enlaces y hace opt-in el conteo
      anónimo de handles. `.aiteam` nunca autoriza cleanup y hasta
      `aiteam_disposable_candidate` conserva `cleanup_authorized=false`.
      Ocho tests herméticos cubren personales numerados, workspace activo,
      familias legacy incluidas las secuencias que empiezan por `1`, DB
      corrupta, Git dirty/untracked, remoto con secreto, timeout, symlink y
      rechazo de receipt dentro de la raíz. La pasada real corregida clasificó
      2.716 carpetas en 2.359 candidatas de las seis familias conocidas, 342 a
      preservar/migrar y 15 personales protegidas; cero movimientos, borrados,
      renames, escrituras o enlaces seguidos. El workspace persistido válido
      estaba fuera de la raíz seleccionada, por eso la categoría activa quedó
      a cero. Receipt local fuera del árbol, no versionado; guía y límites en
      `docs/PROJECT_ARTIFACT_AUDIT.md`; hash final
      `b0479d34eeec4be91c5f61ff5583678a80a1b520f774eab3bcc911cedb12965b`.
    - [x] **P0.K.8.2 Garantía preventiva de cero basura**: cerrada el
      `2026-07-30`. Se retiraron de código `_allocate_project_path`,
      `/api/projects/new`, el panel/estado React legacy y
      `aiteam project create`; seleccionar workspace por API o CLI exige ahora
      un proyecto existente con `.aiteam/aiteam.db` y nunca inicializa una
      carpeta personal. El borrado bloqueado devuelve 423 sobre la ruta
      original, sin rename, tombstone ni cleanup pendiente. El commit guiado
      exige parent existente, colisión exacta, cleanup estricto y un guard
      pre/post de footprint; retry no crea siblings y una entrada concurrente
      no propiedad se conserva aunque la operación falle.
      Verificación: 159 tests backend dirigidos, 27 unitarios frontend y un
      E2E Chromium, Ruff F/E9, TypeScript, ESLint, Stylelint, límites y build
      Vite verdes; el test de higiene inspecciona además la ausencia
      física de allocator, endpoint, tombstone y UI legacy. K.8.6 conserva la
      obligación más amplia de repetir clone/bootstrap/restart/upgrade en cada
      plataforma soportada.
      Contrato cerrado:
      retirar de toda ruta
      activa la asignación silenciosa `Nombre 2…Nombre 999`; una colisión debe
      bloquear y pedir nombre/ruta explícitos. Cada retry reutiliza la misma
      sesión/receipt o falla, nunca crea otro sibling. Tests, benchmarks y
      aceptación usan una raíz temporal hermética y un guard rechaza una raíz
      personal/real en modo test. Staging vive en una raíz temporal de la
      operación, con owner y receipt, y debe publicarse atómicamente o
      eliminarse antes de retornar; el producto no conserva tombstones junto a
      proyectos. Extirpar físicamente allocator, fallback de tombstone,
      configurador/ruta legacy y su estado ya retirados del render. Añadir un
      guard de filesystem que compare el footprint pre/post y haga fallar la
      operación si aparece cualquier path no declarado.
    - [x] **P0.K.8.3 Remediación legacy manual, no lifecycle normal**: cerrada
      el `2026-07-30` con `project_artifact_remediation_manifest_v1`. Se ofrece
      únicamente para una raíz histórica elegida explícitamente un dry-run que
      genere un manifiesto inmutable con path resuelto, categoría, evidencia,
      acción propuesta, tamaño, riesgos y recoverability. No se instala como
      daemon, tarea programada, startup hook, doctor writer ni mantenimiento
      periódico, y no se ejecuta en clones limpios. Denegar automáticamente
      personal, ambiguo, proyecto actual, Git dirty/untracked, remoto no
      preservado, symlink/reparse point, DB referenciada o evidencia
      contradictoria. No aceptar globs, prefijos ni raíces como targets; el
      owner aprueba hash y lista exacta del batch.
      `build_remediation_manifest` reejecuta K.8.1 en vivo y admite solo nombres
      de hijos directos exactos o `--include-all-candidates`, que materializa
      después una lista exacta, nunca un patrón. Personal, ambiguo, activo,
      registrado, DB inválida, Git no observado/dirty/untracked/remoto,
      symlink/reparse, tamaño incompleto y handles abiertos quedan denegados.
      El output vive fuera de la raíz, se crea en modo exclusivo, contiene
      paths resueltos, evidencia hasheada, tamaño, riesgos y recoverability, y
      mantiene execution/quarantine/cleanup en falso. Dieciséis tests cubren
      drift vivo, remoto con secreto, DB corrupta, raíz limpia, selección
      exacta, globs/prefijos, symlink, integridad, overwrite y CLI.
      El dry-run real propone 2.359 paths hijos directos (766.901.650 bytes),
      cero denegados dentro del batch y cero operaciones. Hash de manifiesto
      `3aadd5a9828c1f8bf8544c578d9ff4463136fb35fcf5e8c59010ed782fc6fcfc`;
      hash de batch
      `8a1be67c6e1057b82b95931b3f3d6d65e6b90a8125f6f380d173d5a18da2debb`.
      Aprobarlo no ejecuta nada: K.8.4 exige una autorización posterior y
      revalidación viva.
    - [ ] **P0.K.8.4 Cuarentena legacy y rollback explícitos**: solo desde la
      herramienta manual anterior y tras aprobación humana, mover candidatos
      exactos a una cuarentena elegida fuera de la raíz de proyectos, con
      manifiesto, checksums, timestamps y batch ID. Probar restauración byte a
      byte y colisión de destino. El purgado permanente es otra operación
      manual y nuevamente confirmada. El runtime normal nunca crea, vacía,
      recorre ni aplica TTL a esa cuarentena.
      - [x] **P0.K.8.4.1 Motor hermético y fail-closed**: implementado el
        `2026-07-30` sin ejecutar sobre la raíz real. `apply` exige coincidencia
        explícita de hash de manifiesto y batch, reaudita con handles
        obligatorios, compara evidencia, sella cada árbol y rechaza drift,
        destino existente, symlink, path no exacto y filesystem distinto antes
        de crear el batch. Usa rename atómico, journal después de cada move y
        rollback inverso ante interrupción. `restore` preflighta todas las
        colisiones y checksums antes de mover y revierte también una restauración
        parcial. Copia aprobada y journal tienen integridad propia y permanecen
        tras restore. No existe operación de purge/delete/TTL.
        El desarrollo detectó y corrigió un lock Windows real: el context
        manager SQLite cerraba la transacción pero no garantizaba cerrar la
        conexión; K.8.1 usa ahora `contextlib.closing` antes de cualquier move.
        Trece tests propios cubren doble sello, drift, colisiones,
        cross-filesystem, interrupción, rollback byte a byte, manipulación y
        CLI apply→restore real sobre fixtures.
      - [ ] **P0.K.8.4.2 Revisión owner y batch real**: revisar la lista exacta
        del manifiesto K.8.3 y aprobar explícitamente ambos hashes. Solo después
        elegir una `quarantine_root` existente, externa y en el mismo volumen,
        ejecutar el batch real, auditar journal/checksums y probar una
        restauración controlada antes de considerar cualquier purga separada.
        El mensaje genérico `continúa` no constituye esa aprobación.
    - [x] **P0.K.8.5 UX, doctor y actualización**: cerrado el `2026-07-30`.
      Primer uso, Nuevo proyecto y Configuración
      muestran la raíz elegida, preview del destino exacto, ownership/lifecycle
      y estado de higiene. Doctor detecta nuevas carpetas inesperadas y ofrece
      auditoría, pero solo avisa. Una instalación existente puede migrar,
      conservar o revisar artefactos legacy sin perder proyectos, credenciales
      ni preferencias. `project_hygiene_v1` hace un barrido ligero redacted:
      no sigue enlaces, no abre DB, no invoca Git y no crea/mueve/borra. La API
      de preview usa POST y no persiste; cambiar la ruta invalida el preview y
      Guardar queda bloqueado hasta recomprobar. El doctor añade solo un
      warning con acción `mutates_state=false`; el esquema JSON falla cerrado
      sin romper receipts históricos. Se corrigió además el primer uso con
      `AITEAM_PROJECTS_ROOT`: una raíz efectiva por entorno ya cuenta como
      configurada. Tests prueban que actualizar la raíz conserva preferencias
      y almacenamiento de adapters. Guía humana/IA:
      `docs/PROJECT_ROOT_HYGIENE.md`.
      Evidencia: 69 tests backend del bloque K.8, 32 unitarios frontend,
      Ruff F/I/E9, TypeScript, ESLint, Stylelint, límites, build y bundle
      verdes. Bundle: JS 404.859 B raw/117.124 B gzip; CSS 124.903 B
      raw/22.565 B gzip. El presupuesto CSS raw sube de 120 a 124 KiB por la
      tarjeta responsive; el límite gzip de 25 KiB no cambia.
    - [ ] **P0.K.8.6 Aceptación hermética y máquina real**: cierre dividido para
      no confundir evidencia de fixtures con evidencia obtenida en una máquina
      independiente. La raíz real permanece read-only hasta que el owner
      apruebe un manifiesto exacto.
      - [x] **P0.K.8.6.1 Matriz hermética compuesta**: cubrir máquina limpia,
        instalación parcial, API key/auth, offline/rate limit, skip/reanudación,
        adapter roto, React/TS y proyecto no programativo. Añadir una raíz
        fixture con proyectos personales mezclados, nombres numerados, Git
        limpio y dirty, remoto redacted, DB corrupta, symlink/reparse point,
        staging legacy y operación interrumpida. Demostrar cero mutaciones
        sobre protegidos, cero siblings no declarados, idempotencia, rollback
        íntegro, ausencia de loops de tests y receipt sin secretos, paths
        personales, login, inferencia ni consumo de cuota.
        Cerrado el `2026-07-30` con
        `project_portability_acceptance_v1`. El auditor compone y revalida los
        contratos canónicos de reparación de adapters, commit guiado,
        preflight proporcional y equivalencia clean/update; no mantiene una
        segunda implementación de sus reglas. La raíz temporal hostil cubre
        proyecto personal numerado, familias legacy, Git limpio/dirty/remoto
        redacted, DB corrupta, staging interrumpido y symlink/reparse sin
        seguirlo. El fixture React+TypeScript ejecuta build, test, lint y
        typecheck en copia aislada. Resultado: 9/9 checks; 88 tests dirigidos
        pasados y 2 omitidos por capacidades opcionales ajenas al gate; Ruff
        E/F/I y diff check verdes. Receipt:
        `benchmarks/results/guided_setup/project-portability-acceptance-2026-07-30.json`;
        evidence hash
        `865bf7bd544861b5a6a090c3ce68cf9e34657f190030de8683f2b56ae0d2aef2`
        y SHA-256 de archivo
        `c6cae54eb332effc20c8d9771186ada95c39372af0e00a517359ee4e28caa483`.
        No se leyó ni mutó la raíz real.
      - [x] **P0.K.8.6.2 Reparar y sellar el aceptador Windows vigente**:
        retirar del runner `accept_windows_clean_room.py` la llamada al comando
        legacy `project create`, ya extirpado por K.8.2, y conducir el primer
        proyecto exclusivamente por el commit guiado actual. Clone + bootstrap
        + primer proyecto + retry + restart + actualización deben dejar
        exactamente el footprint declarado y cero tareas, daemons, TTL o
        utilidades de limpieza instaladas.
        Cerrado el `2026-07-30`: el runner usa exclusivamente
        `guided_setup_project_commit_v1` con propuesta sellada Lead-first de
        investigación y adapter fixture sin inferencia. El retry sobre el mismo
        destino falla cerrado sin siblings; bootstrap sobre checkout existente
        y equivalencia clean/update conservan el árbol byte a byte; el segundo
        start/health/stop libera ambos puertos. La migración crea backup, prueba
        rollback byte a byte, retira el backup y recupera el footprint. Los
        cinco entrypoints de instalación quedan hasheados y sin registro de
        scheduled tasks, servicios o startup; higiene confirma cero limpieza
        automática/startup/TTL. La provenance añade hash del harness y estado
        dirty; solo un checkout Git limpio puede ser independiente. Verificación:
        32 tests dirigidos, Ruff E/F/I y diff check verdes. El runner local
        completa 24/24 pasos en 42 s, pero declara correctamente
        `local_existing_host`, `working_tree_dirty=true` y
        `promotion_allowed=false`. Receipt:
        `benchmarks/results/installation_acceptance/windows-clean-room-k8-6-2-local-2026-07-30.json`;
        SHA-256
        `739daa80be69fb418f48b17274401b55bc83c47c0df0c3366db524c782d91913`.
        Las tres raíces temporales de los intentos quedaron eliminadas y los
        puertos 8010/9490 libres.
      - [ ] **P0.K.8.6.3 Evidencia Windows independiente**: ejecutar el runner
        sellado tanto en clone limpio como sobre una instalación existente
        actualizable —incluido el caso de otro usuario que ya tenga AI Teams—,
        conservar receipt SHA-bound y verificar start/stop/restart, actualización
        y recuperación tras interrupción sin depender de configuración, paths,
        secretos ni CLIs particulares de esta máquina.
        - [x] **K.8.6.3a Matriz CI y auditor de receipts**: el workflow Windows
          ejecuta `clean-clone` y `existing-checkout-updated`. El segundo
          bootstrappea `HEAD^`, exige cero cambios tracked, avanza al SHA exacto
          y pasa ambas revisiones al runner. Cada receipt debe ser independiente,
          promocionable, clean y compartir revisión+harness. Un job posterior
          descarga ambos y `audit_windows_clean_room_matrix.py` exige 9/9 gates
          antes de emitir el receipt agregado. El local dirty no puede
          satisfacerlo. Verificación local: 25 tests, parse YAML, Ruff E/F/I y
          diff check verdes. Esto prepara la evidencia; no afirma que los jobs
          ya hayan corrido.
        - [x] **K.8.6.3b Publicar un SHA limpio y recoger evidencia CI**:
          commit/push del estado coherente completo, esperar las dos celdas y el
          auditor agregado, descargar los tres receipts, verificar SHA/harness
          y versionar el agregado. Cerrado con el
          [run 30563841249](https://github.com/MaxBonas/ai-teams/actions/runs/30563841249)
          sobre `6145567c8fb7393dce7479d6fdbf3180a2826533`: `clean-clone` y
          `existing-checkout-updated` pasan 24/24 pasos, quedan clean,
          independientes y promocionables; la actualización parte de
          `6fd5e421d9ad9da6ec31314604cf6358422004c8`. Ambos receipts comparten
          harness
          `5c2c183c48f9b3341b59db542f09b233796d9a10eedd3bba61d8238265b669cc`;
          el auditor agregado pasa 9/9. Receipt durable:
          `benchmarks/results/installation_acceptance/windows-clean-room-matrix-6145567.json`
          (SHA-256
          `444d55db1a55176b6b9a1ee451cd140c486893cdd4a68a3f7900c2984813b156`).
          Los artefactos originales de clone/update tienen SHA-256 de archivo
          `5ccdc1188ca0416ec0e569bbf913285899a991e5a30ee02ed7a25864dd74454d`
          y
          `1f75d49e8680d156fde67c6cb76c8e112f8cdfd7ca36c3f27794e198b36078f5`.
        - [ ] **K.8.6.3c Actualización en otra instalación real**: ejecutar la
          guía de update y el runner no promocionable en la máquina de un
          usuario que ya tenga AI Teams, con su consentimiento; conservar solo
          evidencia redacted y confirmar que configuración/proyectos personales
          sobreviven. No copiar secretos, runtime ni sesiones entre máquinas.
      - [ ] **P0.K.8.6.4 Plataformas soportadas restantes**: repetir el mismo
        contrato en cada plataforma declarada como soportada antes de anunciar
        paridad. Linux/macOS pueden aplazarse mientras no se presenten como
        cerrados ni bloqueen la prioridad Windows actual.

## P0 — Modelos, catálogos y promociones

- [ ] **Mantener actualizado y evaluar todo el catálogo modelo+rol**.
  - [x] **Registrar prioridad del owner del 2026-07-24**. Sol nunca estuvo
    incluido entre los archivados y queda explícitamente como Tier 1 de máximo
    interés.
    - **Archivar cuando M.9 aplique enforcement**:
      `local_gem4_lmstudio/gemma-3-4b-it`,
      `local_gem4_lmstudio/gemma-3-12b-it` y
      `local_gem4_lmstudio/google/gemma-4-26b-a4b`;
      `antigravity_subscription/gpt-oss-120b-medium` y
      `groq_api_free/openai/gpt-oss-120b`,
      `groq_api_free/openai/gpt-oss-20b`.
    - **Prioridad alta**: `codex_subscription/gpt-5.6-sol`;
      Antigravity `gemini-3.6-flash-high`, `medium` y `low`;
      Gemini API Free `gemini-3.6-flash` y `gemini-3.5-flash-lite`;
      Groq Free `qwen/qwen3.6-27b`; OpenCode Free Nemotron 3 Ultra, DeepSeek V4
      Flash, MiMo V2.5, North Mini Code, Laguna S 2.1 y Ling 3.0 Flash.
    - **Prioridad baja**: todos los demás modelos, sin retirarlos ni borrar
      evidencia. No gastar cuota proactivamente en ellos salvo necesidad o
      cambio material.
    - Esta marca expresa preferencia del owner, no disponibilidad, calidad ni
      autorización. M.9.2 aplica el gate general del runtime y M.9.6 ya
      persiste estas identidades exactas en la configuración local.
  - [ ] **Ejecutar la prioridad del owner en lotes pequeños y verificables**.
    - [x] **P0.a Preferencias**: cerrar M.9 y confirmar que los seis modelos
      indicados —tres LM Studio y tres identidades GPT-OSS— quedan archivados,
      fuera de selección y mantenimiento periódico, sin afectar Gemma vía
      Ollama ni Qwen 3.6 de Groq. Cerrado el 2026-07-24: configuración local
      con 47 identidades exactas —6 `archived`, 13 `high`, 28 `low`—; matriz
      de 17 roles con cero archivados seleccionables/default y cobertura con
      seis filas archivadas sin mantenimiento ni backlog.
    - [x] **P0.b Sol Tier 1**: reconciliar versión de Codex CLI y cache; cuando
      el slug exacto vuelva a ser ejecutable, repetir health y las calibraciones
      Tier 1 necesarias antes de habilitarlo.
      Cerrado el 2026-07-24 con la prerelease oficial
      `@openai/codex@0.146.0-alpha.6`, ya que npm estable permanece en
      `0.145.0`: login ChatGPT preservado, caché `0.146.0` en estado `current`
      y `gpt-5.6-sol` verificado/selectable. La revalidación exacta cubre
      `architect`, `lead`, `lead_executor`, `quorum_auditor` y `team_lead`,
      dos familias × tres semillas: 30/30. Una muestra se reevaluó sin rerun
      tras corregir el juez para reconocer `indivisible` como semántica de
      checkout atómico. Los cinco agregados `*-aggregate-cli-0.146.0.json`
      quedan registrados con provider version exacta `0.146.0-alpha.6`.
      Sol es elegible para selección explícita, pero continúa sin
      auto-promoción porque los demás hard gates siguen gobernando. Al
      publicarse `0.146.0` estable, sustituir la prerelease y revalidar
      transporte/versionado; no repetir calidad sin otro cambio material.
      Para lotes paralelos, preparar el venv una vez y ejecutar su Python
      directamente: varias instancias simultáneas de `python_local.bat`
      compiten por el bootstrap y producen fallo de infraestructura previo a
      inferencia.
    - [x] **P0.c Antigravity 3.6**: verificar ejecutabilidad exacta de High,
      Medium y Low en la versión vigente. Partir de Medium, que ya completó
      submit; High/Low no avanzan a canarios de rol mientras el submit los
      rechace.
      Cerrado el 2026-07-24 sobre Antigravity CLI 1.1.6 y catálogo autenticado
      de 11 modelos. Medium/review completa 1/1 con 100 % estructural y
      9,125 s; High completa Lead 93,3 %/11,985 s y coding 72,7 %/7,640 s;
      Low/scout completa 100 %/5,282 s. Los tres slugs quedan `verified` y
      seleccionables manualmente con receipts exactos, corrigiendo el rechazo
      de High/Low observado en 1.1.5. Conservan `automatic=false`,
      `requires_probe=true` y `manual_only=true`: una semilla prueba
      transporte/contrato, no calidad suficiente, ranking definitivo ni
      default. Uso de tokens continúa `unknown`; coste marginal de suscripción
      es cero. Próximo gate de estos pares: matriz multi-semilla comparable
      contra el baseline vigente y, cuando aplique, validación behavioral
      independiente.
    - [ ] **P0.d APIs gratuitas**: poner verdes por separado Gemini API Free y
      Groq Free, validar auth/catálogo/structured output sin exponer secretos y
      después calibrar únicamente los pares de rol compatibles.
      - [x] **P0.d.1 Preflight seguro**: comprobar solo referencias y health,
        sin resolver ni imprimir valores. En el preflight inicial del
        2026-07-24 ambos perfiles quedaron bloqueados correctamente: faltaban
        `secret:google-free:default` y
        `secret:groq:default`; catálogo `not_checked`, cero llamadas API y
        diagnóstico local `api_key_ref_missing`.
      - [x] **P0.d.2 Configuración owner**: el owner guardó el 2026-07-24 las
        keys personales desde Config bajo `secret:google-free:default` y
        `secret:groq:default`. El preflight posterior solo leyó referencias y
        `has_secret`; ningún valor apareció en prompts, Git, SQLite o recibos.
      - [x] **P0.d.3 Gemini Free readiness**: discovery autenticado devuelve 41
        modelos e incluye exactamente `gemini-3.6-flash` y
        `gemini-3.5-flash-lite`. El primer probe descubrió que Gemini rechaza
        enums numéricos en `responseSchema`; el sanitizador retira únicamente
        ese constraint del schema remoto y conserva la validación local. Tras
        el fix, `gemini-3.6-flash` completa dos probes `submit_work`, health
        queda `ok/verified` y el uso se persiste sin secretos. El proveedor no
        expuso headers de rate limit en la respuesta.
      - [x] **P0.d.4 Groq Free readiness diagnóstica**: discovery autenticado
        devuelve 15 modelos e incluye `qwen/qwen3.6-27b`. El 403 Cloudflare
        `1010` era nuestro cliente sin `User-Agent`; el cliente compartido y
        discovery ya se identifican. El perfil deja de usar como default el
        GPT-OSS archivado y apunta a Qwen con `reasoning_format=hidden`. Groq
        observó/persistió 1000 RPD y 8000 TPM, pero Qwen terminó en
        `tool_parse_error` incluso tras el único repair acotado: clave y
        catálogo válidos, health estructural rojo. No repetir hasta cambio
        material de modelo, transporte o contrato.
      - [x] **P0.d.5 Calibración posterior**: Gemini Free
        `gemini-3.6-flash/reviewer` completa 3/3 ciclos durables
        rechazo→fix→aprobación, con fuentes hasheadas, mediana 19,766 s y
        52.773 tokens totales observados; queda calibrado solo para ese par y
        no cambia defaults. QA falla 0/1 porque afirma crear tests pero no
        materializa artefacto ejecutable. Test Designer falla 0/1: crea la ruta
        esperada vacía, pytest no ejecuta tests y falta `AGENT-REPORT`. Ambos
        aplican fail-fast y quedan `deferred_until_material_change`; no se
        rebaja la rúbrica ni se completan las otras cinco celdas. Groq/Qwen
        sigue diferido por structured output. El auditor liga hashes,
        degrada reviewer ante tampering y prohíbe promocionar los dos fallos.
    - [ ] **P0.e OpenCode Free**: conservar los seis modelos como prioridad
      alta de seguimiento, pero no repetir inferencias sobre OpenCode 1.18.4;
      reabrir solo al cambiar CLI, catálogo, modelo, transporte o structured
      output.
    - [x] **P0.f Resto**: mantener visible y funcional si ya cumple gates, pero
      no gastar cuota proactiva ni ampliar calibraciones mientras existan
      acciones anteriores ejecutables.
      - [x] **P0.f.1 Inventario residual durable**: emitir un recibo redacted
        que clasifique cada identidad exacta del read model como `high`, `low`,
        `archived` o pendiente de clasificación, y demuestre que todas siguen
        visibles. Observación read-only del `2026-07-30`: 98 candidatos —13
        `high`, 28 `low`, 6 `archived` y 51 `normal`—; las 18 acciones actuales
        pertenecen solo a `high`, pero eso no cierra la política.
        Cerrado el `2026-07-30` con
        `model_residual_policy_inventory_v1`: 98/98 identidades únicas y
        visibles, 47 preferencias explícitas, 51 pendientes
        (`gemini_api_free=39`, `groq_api_free=12`), cero fuentes inválidas,
        cero preferencias huérfanas y 10 slugs compartidos entre perfiles
        conservados como identidades distintas. Las 867 filas pendientes tienen
        cero acciones, permisos proactivos o promociones actuales. El recibo
        mantiene correctamente `policy_complete=false` y apunta a P0.f.2:
        `benchmarks/results/model_catalog_read_model/`
        `model-residual-policy-inventory-2026-07-30.json`. Verificación:
        47 tests relevantes y Ruff verdes; sin mutaciones, secretos, rutas,
        modelos en claro ni inferencias.
      - [x] **P0.f.2 Reconciliar la directiva local del owner**: distinguir
        identidades nuevas/aliases de las ya clasificadas y convertir a `low`
        únicamente el residual exacto que no sea prioridad alta ni archivado.
        La migración debe ser atómica, reversible, local a la máquina y no
        viajar en Git.
        Cerrado el `2026-07-30`: preview 51/51 sin colisiones; una única
        escritura atómica añadió esas identidades como `low`, preservó
        literalmente las 47 entradas existentes y dejó 98 preferencias
        explícitas —13 `high`, 79 `low`, 6 `archived`—. La segunda ejecución
        fue idempotente con cero adiciones. El archivo local no viaja en Git y
        cada entrada puede volver explícitamente a `normal`. Recibo redacted:
        `benchmarks/results/model_catalog_read_model/`
        `model-residual-preference-reconcile-2026-07-30.json`.
      - [x] **P0.f.3 Gate de gasto residual**: probar que `low` nunca entra en
        backlog, probe, canario o calibración proactiva; una identidad aún
        `normal` no puede aprovechar accidentalmente la ausencia temporal de
        acciones `high`. La selección manual continúa permitida si todos los
        gates técnicos pasan.
        Cerrado el `2026-07-30`: el tablero y el maintenance backlog exigen
        `source=user_machine` para trabajo proactivo. Una identidad nueva con
        `source=default`, aunque sea `normal`, nominada y técnicamente apta,
        queda `owner_unclassified` y propone
        `owner_classify_before_maintenance`; no abre inferencia. Las
        preferencias explícitas conservan su semántica y selección manual no
        cambia. Verificación: 76 tests de política/gates y 49 de
        selección/API/defaults verdes; único warning conocido Starlette/httpx.
      - [x] **P0.f.4 Paridad y cierre**: auditar read model, tablero, API/UI,
        maintenance backlog, hiring/defaults y reactivación explícita. Conservar
        visibilidad/evidencia y demostrar cero cambios silenciosos en
        asignaciones existentes y cero inferencias durante la reconciliación.
        Cerrado el `2026-07-30` con
        `model_residual_policy_parity_audit_v1`: 98 identidades explícitas,
        1.666 celdas read model/tablero/API, backlog residual cero y 1.568
        decisiones de automatización sin fallos. Comprueba wiring de
        preferencias/gates en API y UI, hiring/defaults/fallback, reactivación
        explícita y pausa de asignaciones archivadas sin sustitución. Pasa
        10/10 invariantes, 76 tests transversales más 3 del auditor y Ruff.
        No muta preferencias/asignaciones, no ejecuta inferencias y el receipt
        omite rutas, secretos e IDs de modelos:
        `benchmarks/results/model_catalog_read_model/`
        `model-residual-policy-parity-2026-07-30.json`.
    - [x] **P0.g Gate único adapter → calibración**: mantener un tablero por
      `(profile_id, model_id, role)` con bloqueador, owner y próxima acción, y
      ejecutar siempre en este orden: configuración/auth local sin exponer
      secretos → catálogo y versión exactos → health del adapter en verde →
      probe del contrato real de structured output/tools → canario del rol →
      calibración multi-familia → promoción. Un adapter rojo abre remediación,
      no una inferencia de calidad; no se consume cuota de calibración hasta
      que esté verde. Un adapter verde tampoco concede compatibilidad,
      calibración ni elegibilidad automática por sí solo.
      Cerrado el `2026-07-30` con
      `model_calibration_gate_board_v1`: read model, endpoint
      `GET /api/model-catalog/calibration-gates`, respuestas por rol y pestaña
      Modelos comparten la misma secuencia determinista de siete gates. La
      política del owner se evalúa antes de gastar: `archived`, `low` y
      manual/no nominado conservan evidencia, pero no abren trabajo proactivo.
      Evidencia histórica queda visible como `historical` y nunca atraviesa un
      health/version gate actual. El auditor vivo cubre 1.666 identidades
      perfil+modelo+rol, 10/10 invariantes, cero bypass de adapter rojo y cero
      promociones completas; no leyó secretos ni ejecutó inferencias. Recibo:
      `benchmarks/results/model_catalog_read_model/`
      `model-calibration-gate-board-2026-07-30.json`. Verificación focalizada:
      22 tests del bloque, 104 tests integrados, suite global 1.764 passed /
      2 skipped, TypeScript, ESLint, Stylelint y 3 E2E verdes. Único aviso:
      deprecación conocida Starlette/httpx.
    - [ ] **P0.h Cobertura suficiente de Tier 1 y Tier 2 sin rebajar calidad**:
      medir por rol canónico cuántos pares exactos están saludables,
      seleccionables, frescos y calibrados con evidencia conductual
      independiente. Objetivo operativo: al menos dos candidatos por rol
      crítico, y cuando sea posible dos perspectivas de proveedor y dos pools
      de capacidad; el suelo mínimo es uno por rol para no fingir redundancia.
      `partial`, stale, rojo, archivado o manual/probe-gated no cuenta. Si no
      se alcanza, Catálogo debe mostrar el hueco y hiring degradar o pedir
      desbloqueo: nunca se baja la rúbrica, se promueve por necesidad numérica
      ni se confunde un health verde con calidad.
      Tier 1 mantiene una sola banda máxima, con habilitaciones independientes:
      `lead_ready` exige el contrato integral de Lead; `quorum_ready` exige
      auditoría crítica estructurada y puede ser read-only; `tier1_support`
      proyecta `architect`, `lead_executor` y `team_lead`. Ninguna habilitación
      se hereda de otra y `quorum_ready` nunca concede autoridad de Lead.
      - [x] **P0.h.1 Inventario de cobertura**: `aiteam.model_tier_coverage` y
        `scripts/audit_model_tier_coverage.py` proyectan por carril Tier 1 y rol
        Tier 2 solo pares automáticos, ejecutables, no archivados y calibrados,
        incluyendo perspectiva y pool. Recibo vivo:
        `benchmarks/results/model_evaluation_coverage/model-tier-coverage-2026-07-24.json`.
        Resultado vivo tras P0.h.2c.1: `lead_ready` y `quorum_ready` están
        `covered` 2/2 con Sol/Codex y Gemini Pro/Antigravity, dos perspectivas
        y pools. Los tres roles Tier 1 de soporte siguen `single_point`.
        Tier 2 tiene Reviewer y QA 2/2. QA aporta
        perspectivas OpenAI/Google y pools Codex/Antigravity; Engineer, MCP
        Operator y Test Designer quedan 0/2 con la versión/health exactos del
        recibo
        `model-tier-coverage-2026-07-29-tier2-qa.json`.
      - [ ] **P0.h.2 Backlog dirigido**: priorizar adapters/modelos capaces de
        cerrar los huecos de diversidad y continuidad, no acumular variantes
        redundantes del mismo proveedor.
        - [x] **P0.h.2a Quorum Tier 1**: recalibrar
          `antigravity_subscription/gemini-3.1-pro-high` como
          `quorum_auditor` sobre la versión exacta 1.1.6. Las dos familias por
          tres semillas completan 6/6 al primer intento y el agregado liga las
          respuestas por hash:
          `critical-defaults-v2-gemini-3.1-pro-high-quorum-auditor-aggregate-cli-1.1.6.json`.
          El carril queda 2/2 con perspectivas OpenAI/Google y pools
          Codex/Antigravity; no concede `lead_ready`.
        - [x] **P0.h.2b Lead Tier 1**: Gemini 3.1 Pro High completa por
          separado el contrato `lead` en Antigravity 1.1.6: dos familias por
          tres semillas, 6/6 al primer intento, schema exacto y anclas causales
          completas. El agregado `critical_role_aggregate_v2` exige y sella
          una única versión CLI además de perfil, modelo, rol, prompt, fuentes
          y hashes:
          `critical-defaults-v2-gemini-3.1-pro-high-lead-aggregate-cli-1.1.6.json`.
          `lead_ready` pasa a 2/2; el resultado no autoriza cambiar defaults.
          Verificación: 64 tests dirigidos, 1698 backend, Ruff y diff check
          verdes; read model, paridad Tier 1 y enforcement sin divergencias.
        - [x] **P0.h.2c Diversidad de capacidad Tier 1**: el objetivo mínimo
          quedó demostrado en Antigravity 1.1.6 con Codex/Sol y
          Antigravity/Gemini, dos perspectivas, dos transportes y dos pools de
          capacidad independientes. Anthropic sigue siendo expansión deseable
          cuando su adapter esté verde, pero no se finge como requisito
          pendiente ni se cuenta Claude vía Antigravity como otro pool.
          - [x] **P0.h.2c.1 Revalidación por drift Antigravity 1.1.8**:
            restaurar 2/2 sin rebajar rúbrica repitiendo las matrices exactas
            y separadas de `gemini-3.1-pro-high` para `lead` y
            `quorum_auditor`, dos familias × tres semillas, versión única,
            hashes profundos y `default_change_allowed=false`. El inventario
            o un éxito QA no conceden autoridad Tier 1. El drift auditado
            dejó ambos carriles 1/2 hasta cerrar esta unidad. Lead y quorum
            completan por separado 6/6 en prompt v2, con doce recibos y ambos
            agregados ligados a CLI 1.1.8. El harness Antigravity impone ahora
            `--sandbox --mode plan` además de cwd temporal; su línea de comando
            queda cubierta por test. El recibo
            `model-tier-coverage-2026-07-30-tier1-restored.json` devuelve
            ambos carriles `covered` 2/2 con perspectivas Google/OpenAI y
            pools Antigravity/Codex. Read model, enforcement y paridad quedan
            verdes, con cero defaults/fallbacks y
            `default_change_allowed=false`. Verificación final: 320 tests
            transversales y 1.706 backend pasan, con dos skips de toolchain;
            14 recibos nuevos son JSON válidos sin patrones de secretos, Ruff
            F/I del bloque y `git diff --check` quedan verdes.
        - [ ] **P0.h.2d Tier 2**: restaurar primero las calibraciones stale de
          Terra y Flash High tras validar versión exacta, y después añadir una
          segunda perspectiva por cada rol aún `single_point` o `no_eligible`.
          - [x] **P0.h.2d.1 Reviewer**: Terra
            `codex_subscription/gpt-5.6-terra` completa 3/3 ciclos
            rechazo→fix→aprobación en Codex `0.146.0-alpha.6`: seis llamadas,
            12 runs, mediana 62,563 s y telemetría de tokens completa. El
            agregado `durable_review_aggregate_v2` liga perfil, modelo,
            versión única, tres fuentes y hashes, y mantiene
            `default_change_allowed=false`. Reviewer queda `covered` 2/2 al
            combinar Terra con Gemini API Free, dos perspectivas y dos pools.
            El harness observa la versión real y falla ante versión ausente o
            mezclada; el validador profundo comprueba agregado y muestras.
            Read model mantiene cero candidatos automáticos y el auditor de
            enforcement cero defaults/fallbacks; verificación final: 39 tests
            focalizados, 1.700 backend, un skip de toolchain, Ruff funcional y
            `git diff --check` verdes.
          - [x] **P0.h.2d.2 Engineer**: Terra alcanzó el proveedor en Codex
            `0.146.0-alpha.6` y pasó 9/9 tests ocultos de `cli_conversor`, pero
            dejó dos incidencias Ruff en los entregables; el fail-fast evitó
            las otras cinco ejecuciones. Sonnet 4.6 ya tenía un screening
            equivalente vigente en Antigravity 1.1.6: 3/3 ocultos y siete
            incidencias Ruff, por lo que no se repitió sin cambio material.
            Ambos diagnósticos quedan profundamente validados por identidad,
            versión, familia, score y recibo; Engineer sigue 0/2 y no cambia
            defaults ni selección automática. Verificación: 103 tests
            transversales y 1.702 backend pasan, con un skip de toolchain;
            read model y enforcement conservan cero candidatos automáticos,
            defaults y fallbacks.
          - [x] **P0.h.2d.3 QA**: Terra en Codex `0.146.0-alpha.6` y Flash High
            en Antigravity `1.1.8` completan cada uno dos familias
            adversariales × tres semillas: 6/6 muestras, 66/66 gates por
            modelo y una única versión por agregado. El contrato familiar
            `adversarial_qa_fix_cycle_v4` exige actor/recurso con
            `tenant_id`, ataque que falle antes del fix, test runner
            determinista tras el fix, cierre durable y hashes profundos; el
            agregado `adversarial_qa_two_family_v5` enlaza las seis muestras.
            El runner determinista evita confundir la inaccesibilidad del venv
            desde un sandbox de proveedor con calidad del modelo. Los intentos
            v3 ambiguos o sin runner quedan preservados solo como diagnóstico
            y no cuentan. QA queda `covered` 2/2 con dos perspectivas y pools;
            no cambia defaults. Recibos:
            `p0h2d3-terra-qa-diversity-v5-cli-0.146.0.json` y
            `p0h2d3-flash-high-qa-diversity-v5-cli-1.1.8.json`.
            Verificación final: 245 tests transversales y 1.704 backend pasan,
            con dos skips de toolchain; 18 recibos finales son JSON válidos,
            sin patrones de secretos, y Ruff F/I del bloque más
            `git diff --check` quedan verdes. La suite detectó y corrigió una
            expectativa histórica: el snapshot del 23/07 no puede consumir
            recalibraciones QA fechadas el 29/07.
          - [x] **P0.h.2d.4 Test Designer**: Terra en Codex
            `0.146.0-alpha.6` y Gemini 3.5 Flash High en Antigravity `1.1.8`
            completan cada uno `pricing_boundary_mutation` y
            `job_state_machine_mutation`, tres semillas por familia: 6/6
            muestras, 48/48 gates y 30/30 mutantes por modelo. El harness
            observa la versión antes de inferir, fija un Lead Sol para aislar
            el rol, rechaza versiones ausentes/mezcladas y liga muestras,
            familias y agregados por hash. Un primer Terra seed 2 mató 5/5
            mutantes pero tradujo las claves del `AGENT-REPORT`; queda como
            diagnóstico. Se corrigió el contrato exacto y el runtime impide
            ahora que `test_designer` cierre `done` sin reporte válido; el
            reintento nuevo pasa sin relajar mutantes. El auditor profundo
            verifica baseline, cinco mutantes, único artefacto, reporte,
            identidad, versión, familia y hash de las doce muestras. El recibo
            `model-tier-coverage-2026-07-30-tier2-test-designer.json` deja
            Test Designer `covered` 2/2 con perspectivas OpenAI/Google y pools
            Codex/Antigravity; read model y enforcement mantienen cero
            candidatos automáticos, defaults y fallbacks. Verificación final:
            183 tests transversales y 1.712 backend pasan, con dos skips de
            toolchain; 23 recibos nuevos son JSON válidos sin patrones de
            secretos, Ruff F/I del bloque y `git diff --check` quedan verdes.
            El único warning es la deprecación Starlette/httpx ya existente.
          - [ ] **P0.h.2d.5 MCP Operator**: alcanzar 2/2 perspectivas y pools
            con transporte MCP gobernado real; un API sin loop de tools no
            puede rellenar este cupo.
            - [x] **P0.h.2d.5a Renovar Terra**: Codex
              `0.146.0-alpha.6` completa `advisory_recovery_governance` y
              `dependency_policy_governance`, tres semillas por familia:
              6/6 muestras, 72/72 gates y single-attempt. Cada muestra observa
              fallo de versión, recovery activo, grant read, deny write,
              llamada MCP permitida real, ausencia de write y reporte durable.
              Harness y auditor exigen versión exacta, contrato familiar,
              hashes, artefacto y los seis gates MCP específicos. El recibo
              `p0h2d5-terra-mcp-diversity-v3-cli-0.146.0.json` queda
              calibrado sin autorizar defaults.
            - [ ] **P0.h.2d.5b Segunda perspectiva**: el inventario vivo deja
              MCP Operator `single_point` 1/2, perspectiva OpenAI y pool Codex,
              en
              `model-tier-coverage-2026-07-30-tier2-mcp-operator.json`.
              Sol no cuenta como diversidad por compartir ambos. Reabrir solo
              si OpenCode cambia versión/structured output y habilita el rol,
              si un modelo local no archivado queda instalado y supera el
              contrato, o si otro adapter incorpora un loop MCP gobernado
              equivalente. Ollama ausente, LM Studio archivado, OpenCode
              1.18.4 incompatible y API/Antigravity sin ese transporte no
              justifican inferencias hoy. Verificación del bloque: 205 tests
              focales, Ruff F/I, 13 receipts JSON válidos sin patrones de
              secretos, `git diff --check` y 1.715 tests backend pasan, con
              dos skips de toolchain y el warning Starlette/httpx ya conocido.
      - [x] **P0.h.3 Enforcement**: impedir que defaults, hiring o fallback
        cuenten candidatos no calibrados para satisfacer el objetivo.
        Implementado: `candidate_is_automation_eligible` separa selección
        manual del owner de autoridad automática. Defaults, plazas nuevas,
        hiring/reconcile, fallback dentro del perfil, escalado de modelo,
        cambio de perspectiva y recovery de adapter exigen ahora
        `selection_score.auto_eligible=true`; esto incorpora calibración,
        frescura, health, compatibilidad, privacidad, tools y política
        automática. `shadow` y `recommend` solo conservan el selector legacy si
        el par exacto supera ese gate; de lo contrario dejan la plaza
        `default_unresolved`. Las propuestas se revalidan al aceptarse, mientras
        un override manual explícito continúa permitido si es compatible y
        seleccionable. `AITEAM_PROVIDER_FALLBACK_ADAPTER` ya no cambia de
        runtime sólo con un `adapter_type`: registra deny y deriva al recovery
        gobernado porque carece de perfil+modelo+rol verificables.
        `model_automation_enforcement_v1` audita 1.568 celdas en 16 roles:
        1.560 gates de calibración/frescura rojos no producen ninguna
        automatización; el estado vivo tiene cero defaults/fallbacks elegibles,
        hecho que se conserva en vez de inventar cobertura. La matriz hermética
        4/4 prueba el caso positivo y los rechazos, y el wiring 5/5 cubre
        default, hiring, fallback y recovery. Recibo:
        `benchmarks/results/model_catalog_read_model/model-automation-enforcement-2026-07-24.json`.
        Verificación final: 1697 tests backend, Ruff dirigido y
        `git diff --check` verdes.
      - [x] **P0.h.4 Integración transversal de habilitaciones Tier 1 — P0**:
        la separación ya definida no se considera terminada hasta estar
        configurada, visible y verificada en todas las superficies. El tier
        conserva capacidad máxima; `lead_ready`, `quorum_ready` y cada rol
        `tier1_support` son gates exactos de autoridad, no puntos extra del
        score ni aliases de `best_for`.
        - [x] **P0.h.4a Contrato canónico y migración**:
          `model_catalog_read_model_v2` añade `tier1_authority` por
          `(profile_id, model_id, role)`, con carril, estado, razón, versión,
          frescura y recibos, derivado de evaluación+compatibilidad exactas e
          incluido en el hash de la celda. El contrato superior declara
          `legacy_missing_policy=fail_closed` y
          `score_relationship=independent_hard_gate`. No hace falta migración
          física: `candidates_json` es autosuficiente y los snapshots v1 siguen
          legibles/verificables; al intentar autoaplicar un rol Tier 1, uno
          legacy sin habilitación exacta se rechaza. Selección contextual y
          snapshots nuevos transportan el campo, pero todavía no lo aplican a
          todos los consumidores —eso pertenece a P0.h.4d—. Se corrigió además
          la versión observable de adapters API para no declarar stale una
          calibración por omitir `api:{provider}:{version}`. Recibo compacto:
          `model-catalog-read-model-2026-07-24-tier1-authority-v2.json`, con
          98 identidades locales/históricas, 1.666 celdas, 6 habilitaciones
          Tier 1, 0 candidatos automáticos y 0 fallos.
        - [x] **P0.h.4b Calibración y valoración**: mantener rúbricas distintas
          para Lead y quorum. Lead mide planificación durable, hiring,
          delegación, accountability, recovery y gobierno de tools/workspace;
          quorum mide crítica independiente, retención causal, go/no-go y
          salida estructurada verificable, pudiendo ser read-only. Cada
          valoración sigue `model_role_score_v2` por rol exacto —calidad,
          capacidad, fiabilidad, economía y velocidad, con confianza aparte—;
          la habilitación es un hard gate y nunca se compra con una nota alta.
          Recalibración por cambio de modelo, versión, prompt, contrato o
          tooling invalida solo el par afectado.
          Implementado: cada habilitación publica un contrato versionado con
          constructos distintos (`tier1_lead_authority_v1`,
          `tier1_quorum_authority_v1` o soporte exacto). El auditor recalcula la
          habilitación desde la evaluación y rechaza cualquier fuga de
          `tier1_authority` dentro de componentes, evidencia o hard gates de
          `model_role_score_v2`. Una prueba demuestra que un score numérico alto
          con calibración Lead parcial sigue bloqueado; versión/prompt/contrato/
          tooling continúan invalidando únicamente el par exacto mediante la
          cobertura existente.
        - [x] **P0.h.4c Catálogo, API y UI**: exponer badges/filtros
          `Lead-ready`, `Quorum-ready` y soporte Tier 1, además de score,
          confianza, breakdown, frescura, recibos y causa de bloqueo. Mostrar
          huecos 0/2 o 1/2 y diversidad de perspectiva/pool. React consume el
          backend y no vuelve a inferir habilitaciones.
          Implementado: los endpoints global y por rol aceptan filtro
          `authority`, conservan `tier1_authority` por celda y publican
          `tier1_coverage` con objetivo, habilitados, perspectivas, pools y
          estado por rol. La pestaña Modelos muestra cobertura Lead/quorum,
          marcas independientes del score, filtro, badges y ficha de contrato,
          versión, constructos y causa de bloqueo. El frontend solo filtra el
          campo recibido; no recalcula autoridad. Validación: 34 tests backend,
          Ruff, tipos, ESLint, Stylelint, límites de módulo/bundle, 7 unitarios,
          build y 3 E2E de escritorio/móvil verdes.
        - [x] **P0.h.4d Selección y lifecycle**: onboarding, creación/edición
          de Equipo, propuesta/aceptación de hiring, defaults, quorum, fallback,
          reconcile, dispatch, recovery y liveness deben consultar la misma
          proyección contextual. Un `quorum_ready` jamás puede ocupar Lead; un
          `lead_ready` no entra en quorum si carece de calibración exacta como
          auditor. Overrides del owner respetan compatibilidad y autoridad y
          nunca saltan el gate.
          Implementado: `tier1_authority_gate` es la única decisión fail-closed
          por rol/carril/versión; selección contextual la aplica antes de
          `owner_selectable`, default o fallback y el override del owner
          conserva el rechazo explícito. Bootstrap, propuestas de Equipo/hiring,
          reconcile y quorum validan el par exacto contra esa proyección. Un
          preflight del executor vuelve a comprobar asignaciones persistidas
          antes de invocar el LLM y, ante estado ausente, stale, bloqueado o de
          carril incorrecto, bloquea la issue, crea interacción durable y
          registra el deny; por tanto dispatch, retry, recovery y liveness no
          pueden revivir una autoridad inválida. No se mutan silenciosamente
          asignaciones legacy ni se permite sustitución entre carriles.
          Verificación: 118 tests completos del executor, 176 tests dirigidos
          de consumidores y matriz global de 1688 tests backend; lint estricto
          del código nuevo y `git diff --check` verdes.
        - [x] **P0.h.4e Auditoría y cierre**: matriz de tests positiva/negativa
          en backend y frontend, incluyendo modelo Tier 1 sin habilitación,
          score alto sin gate, quorum-only intentando ser Lead, evidencia stale,
          adapter rojo, modelo archivado y divergencia entre consumidores.
          Auditor durable debe comparar read model, endpoints, UI, snapshots y
          decisiones reales. Criterio final: ningún consumidor deduce autoridad
          desde `tier`, `best_for`, nombre de modelo o score, y todos explican
          la misma decisión.
          Implementado: `tier1_authority_parity_audit_v1` compara las 490
          celdas Tier 1 de 98 candidatos entre read model y proyección API,
          tres filtros de carril y cinco resúmenes de cobertura; contrasta 235
          decisiones activas del selector, 20 aceptaciones/rechazos reales de
          snapshots y cinco invariantes del frontend. La matriz negativa
          demuestra bloqueo de score 100 con carril incorrecto, evidencia
          stale y modelo archivado; un adapter rojo queda fuera de default y
          exige configuración. El endpoint serializa el campo exacto y Equipo
          deshabilita el quorum-only como Lead sin inferir desde score.
          Recibo:
          `benchmarks/results/model_catalog_read_model/tier1-authority-parity-2026-07-24.json`,
          con `ok=true` y cero divergencias. Verificación: 1692 tests backend,
          8 unitarios frontend, Ruff, ESLint, typecheck, build y
          `git diff --check` verdes.
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

- [x] **P0.M — Catálogo universal, scoring por rol y selección de equipos**.
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
  - [x] **M.8 Cobertura completa y mantenimiento continuo**.
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
    - [x] Recalcular por evento de modelo/CLI/precio/cuota/prompt/tool/contrato y
      mensualmente; conservar histórico y tendencias sin retirar por edad sola.
      Cerrado el 2026-07-30 con `model_catalog_maintenance_v1`: SQLite conserva
      snapshots append-only solo ante cambios materiales o el primer cálculo de
      cada mes. Siete hashes separan modelo, CLI, precio, cuota, prompt, tools y
      contrato; el mismo input/mes es idempotente. Cada fila conserva métricas,
      delta, hash y causas, sin candidatos, paths ni secretos. El servicio
      reconcilia al reconstruir el read model y nunca altera scores,
      preferencias o assignments. `GET /api/model-catalog/maintenance` expone
      hasta 120 snapshots redacted con retención
      `append_only_no_age_deletion`. El receipt
      `model-catalog-maintenance-2026-07-30.json` deja 7/7 triggers y cadencia
      mensual verdes. Verificación: 106 tests integrados, Ruff E402/F/I y diff
      check verdes; regresión backend completa posterior: 1752 passed, 2
      skipped. El receipt es JSON válido sin rutas personales ni patrones de
      secreto.
    - Cierre: 100 % del inventario visible, 100 % de rutas automáticas con
      evidencia fresca y cada hueco restante con owner/bloqueador/próxima acción.

  - [x] **M.9 Preferencias y archivo reversible del owner**.
    - [x] **M.9.1 Contrato y persistencia local**:
      `model_owner_preferences_v1` por `(profile_id, model_id)`, con estado
      `high|normal|low|archived`, razón y fecha. Guardarlo en configuración de
      usuario/máquina, no en defaults compartidos ni SQLite de otro proyecto.
      `aiteam.model_owner_preferences` valida schema e identidades exactas,
      separa slugs iguales por canal, persiste en orden mediante reemplazo
      atómico, reactiva con un `normal` explícito y falla cerrado ante corrupción
      sin sobrescribirla. Ausencia equivale a `normal` sin crear archivo.
      Verificación: 9 tests propios, 69 focales conjuntos, Ruff y diff verdes.
    - [x] **M.9.2 Enforcement único**: superponer la preferencia en el read
      model y selección contextual. `archived` bloquea nuevas selecciones,
      hiring, fallback, defaults, canarios y recalibración periódica, pero
      conserva catálogo/receipts. `high` solo ordena el backlog y `low` evita
      trabajo proactivo; ninguna marca modifica score o elude hard gates.
      El read model carga una sola instantánea local validada y la incluye en
      su hash; el selector añade `owner_preference` únicamente al score
      contextual, deja intacto el score técnico base y excluye archivados de
      `owner_selectable`, default y fallback. Cobertura conserva estado y
      recibos, publica permisos de mantenimiento y genera un backlog donde
      `high` precede a normal mientras `low` y `archived` no abren canarios
      proactivos. La caché cambia con el archivo local y corrupción nueva nunca
      reutiliza un catálogo anterior. Verificación: 59 tests focales directos,
      201 integrados con API/executor, Ruff F/I/E402 y diff-check.
    - [x] **M.9.3 Asignaciones existentes**: no reemplazar silenciosamente un
      modelo archivado que ya esté asignado. Mostrar warning durable y exigir
      elección owner-confirmed antes de reencolar trabajo.
      El executor consulta la identidad exacta antes de cualquier inferencia:
      bloquea la issue, conserva intacto el agente y crea una interacción
      idempotente con la mejor alternativa seleccionable —preferentemente en
      el mismo perfil— o instrucciones de reactivación/configuración. Solo un
      `accept` revalidado contra catálogo, preferencia, adapter y
      compatibilidad puede actualizar Equipo y devolver la issue a `todo`.
      Cambios manuales concurrentes se preservan si siguen siendo válidos;
      rechazo, propuesta stale, configuración ilegible o ausencia de
      alternativa mantienen el bloqueo. La escalada senior tampoco puede
      saltarse `owner_selectable`, y las APIs de owner rechazan nuevas
      asignaciones archivadas. El doble check posterior queda cubierto dentro
      de la regresión M.9.4: 152 tests backend relevantes en verde.
    - [x] **M.9.4 API y UI**: permitir Prioridad alta, Normal, Baja,
      Archivar y Reactivar desde Modelos; mostrar badges/filtros y causa. Equipo
      mantiene archivados visibles pero deshabilitados.
      `GET/PUT /api/model-catalog/preferences` usa el contrato local validado,
      exige razón, persiste por identidad exacta e invalida la caché. La
      pestaña Modelos permite editar/reactivar, filtrar y distinguir visualmente
      archivados; el selector compartido de Equipo los conserva visibles con
      causa, pero deshabilitados. React no recalcula score ni disponibilidad.
      Verificación: 152 tests backend relevantes, TypeScript, ESLint, Stylelint
      y límites de tamaño frontend en verde.
    - [x] **M.9.5 Auditoría y portabilidad**: probar separación por canal,
      restart, reactivación, caché, onboarding, Equipo, hiring, quorum,
      fallback y defaults. Una instalación nueva comienza en `normal`; no
      hereda las preferencias personales versionadas en este repositorio.
      La matriz hermética prueba proceso nuevo, ausencia de archivo en máquina
      limpia, persistencia tras restart, reactivación explícita, identidades con
      el mismo slug en canales distintos e invalidación/fallo cerrado de caché.
      Onboarding, API/selector de Equipo, propuesta de hiring, quorum explícito,
      fallback/executor y snapshots de default rechazan archivados antes de
      mutar o consumir inferencia. La auditoría encontró y corrigió tres
      defectos: hiring legacy en rollout `shadow` no consultaba preferencias;
      onboarding bloqueaba pero atribuía falsamente el rechazo al tier; y PATCH
      de Equipo normalizaba antes de compatibilidad, degradando un diagnóstico
      estructurado 422 a un 400 genérico. Defaults y quorum añaden además
      defensa frente a proyecciones/llamadas internas stale.
      Verificación: 308 tests backend integrados, 7 tests unitarios frontend,
      TypeScript, ESLint, Stylelint, límites de módulo, Ruff F del delta y
      diff-check en verde.
    - [x] **M.9.6 Aplicar la directiva actual**: archivar únicamente los tres
      modelos LM Studio y las tres identidades GPT-OSS indicadas; priorizar
      Sol Tier 1 y las cohortes
      Antigravity 3.6, Gemini Free, Groq Free y OpenCode; dejar el resto en
      baja. Qwen 3.6 27B es la única prioridad alta de Groq. Regenerar
      cobertura/read-model y verificar cero selección archivada.
      Aplicado localmente el 2026-07-24 mediante reemplazo total validado y
      atómico: 47/47 candidatos clasificados, exactamente 6 archivados,
      13 altos y 28 bajos. El auditor del read model pasa con 47 candidatos,
      799 filas de rol y cero fallos; los 17 roles canónicos arrojan cero
      archivados `owner_selectable` o elegidos como default. El recibo
      `benchmarks/results/model_evaluation_coverage/model-evaluation-coverage-2026-07-24-owner-preferences.json`
      conserva 124 pares, seis filas archivadas con ambos permisos de
      mantenimiento a `false` y backlog vacío. Se corrigió además el auditor
      para no confundir una deuda deliberadamente suprimida por `low` o
      `archived` con una acción de calibración ausente.
    - Cierre M.9 (`2026-07-30`): se corrige el checkbox padre tras comprobar que
      M.9.1–M.9.6 estaban completos; no se modifica ninguna preferencia local.
  - Cierre P0.M (`2026-07-30`): M.1–M.9 quedan completos. Identidad, scoring,
    read model, API/UI, selector de equipos, defaults gobernados, cobertura,
    mantenimiento durable y preferencias reversibles consumen contratos
    comunes. Los huecos de modelos siguen visibles y fail-closed; cerrar P0.M
    no significa que todos los adapters o pares modelo+rol estén calibrados.

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

### P0.N — Detección y gestión de cambios en proveedores

- [ ] **P0.N Sistema provider-change-aware de extremo a extremo**: AI Teams
  debe descubrir cambios relevantes en CLIs, servidores MCP, adapters/APIs y
  catálogos de modelos, distinguir novedad de incompatibilidad y avisar al
  developer con evidencia y remediación. Nunca actualizar globales, aceptar
  términos, rotar credenciales, promocionar modelos ni consumir canarios sin
  autorización.
  - [x] **P0.N.1 Contrato canónico y fuentes**: definir
    `provider_change_intelligence_v1` por perfil/canal con versión instalada,
    versión soportada/pin, última versión conocida, fuente y timestamp. Separar
    CLI/package, servidor MCP, SDK/API/endpoint/auth/schema, adapter interno y
    catálogo/model metadata. Fuentes admisibles: contrato del repo, resolución
    local compartida, registry/release oficial y discovery autenticado; una
    web o nombre comercial sin provenance no cambia estado.
    Cerrado con contrato JSON y proyección neutral fail-closed: cubre los 12
    perfiles built-in, 42 componentes, cinco superficies y tres MCP curados.
    Cada hecho usa `known/unknown/not_applicable`; unknown no puede contener
    valor ni timestamp. Solo `repo_contract` establece el pin,
    `local_resolution` la instalación y registry/release oficial o discovery
    autenticado la última versión. API, suscripción y local permanecen scopes
    distintos; discovery no concede calidad, rol, routing ni actualización.
    Receipt 7/7:
    `benchmarks/results/provider_change_contract/provider-change-contract-2026-07-30.json`
    con inventario SHA-256
    `8efbe929662c159dce37cb6bb7ccd6d8385af7b788a26b15daf75c3c8dc7acd2`.
    Verificación: 10 tests focalizados y Ruff verdes.
  - [x] **P0.N.2 Detectores y diffs semánticos**: implementar probes
    provider-neutral, read-only e idempotentes para release nueva, instalación
    atrasada, prerelease, retirada/deprecación, incompatibilidad con el rango
    soportado, cambio de protocolo/auth/schema, MCP capabilities/tools y modelos
    añadidos, retirados, renombrados/aliased o modificados en contexto, tools,
    structured output, precio/cuota y lifecycle. `newer_available` es
    informativo; solo evidencia compatible determina `update_recommended`,
    `update_required` o `blocked`.
    Cerrado con snapshots y diffs canónicos SHA-bound. Los readers se inyectan
    y ejecutan una vez; solo campos allowlisted entran al snapshot. Offline,
    timeout, 429, auth o fallo permanecen `unknown`. SemVer distingue
    upgrade/downgrade/prerelease y las versiones opacas no se ordenan.
    Compatibilidad explícita gobierna recommend/required/blocked; toda salida
    conserva routing y actualización automática en falso. Catálogo compara IDs
    exactos, aliases y siete dimensiones de metadata; MCP/API/adapter comparan
    contratos separados. Receipt:
    `benchmarks/results/provider_change_detection/provider-change-detection-2026-07-30.json`
    con 19/19 escenarios, 8/8 gates y SHA-256 de archivo
    `2789df9e2cfa9bd8f8aa8bf70e37d830db64ee502016b79249f23ca08bac3f56`.
    Verificación focal: 21 tests y Ruff verdes; fixtures sin red, secretos,
    login, inferencias ni mutaciones.
  - [ ] **P0.N.3 Persistencia y scheduling durable**: almacenar snapshots,
    diffs y eventos deduplicados en SQLite con fingerprint, primera/última
    observación, severidad, owner, acknowledge/snooze/resolved y próxima
    comprobación. Ejecutar en doctor/startup y por scheduler con cadencia,
    jitter, backoff y caché; offline, rate limit o auth ausente quedan
    `unknown`, nunca “sin cambios”. Un cambio confirmado dispara M.8/P0.g y
    vuelve stale solo la evidencia afectada.
  - [ ] **P0.N.4 Workflow de gestión y rollback**: convertir cada cambio
    accionable en issue/interacción durable con diff, impacto,
    perfiles/modelos/roles afectados, recomendación, comandos guiados, riesgo y
    rollback. Flujo: observar → confirmar → clasificar → aprobar → actualizar
    pin/adapter o catálogo → doctor/probe → canario/calibración proporcional →
    aceptar o revertir. Modelos nuevos quedan visibles como
    `owner_unclassified`; modelos retirados bloquean nuevas selecciones y las
    asignaciones existentes piden sustitución sin mutación silenciosa.
  - [ ] **P0.N.5 Avisos al developer y superficie UI**: añadir inbox/banner en
    Config y Modelos con contador, severidad, proveedor/canal, resumen del diff,
    edad, evidencia, acción recomendada y botones acknowledge/snooze/gestionar.
    Crear actividad e interacción local siempre; notificaciones externas son
    opt-in y configurables, sin secretos ni spam repetido. Alertar de inmediato
    ante retirada, incompatibilidad o seguridad; agrupar novedades informativas.
  - [ ] **P0.N.6 Aceptación portable y auditoría**: fixtures de release,
    downgrade, prerelease, MCP capability drift, auth/schema, adición/retirada/
    alias/cambio de modelo, offline, rate limit, duplicados y rollback. Probar
    clean machine y actualización existente, paridad doctor/runtime/API/UI,
    cero instalación automática, cero inferencia de calidad por discovery y
    receipts redacted. Documentar cómo añadir detectores para proveedores
    nuevos y cómo una IA integradora diagnostica y resuelve el aviso.

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
