# Plan y trabajo vigente

Actualizado: `2026-07-22`

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
- La matriz hermética cubre 46 modelos, 321 decisiones positivas y 415
  negativas.
- Sonnet 4.6 está promovido solo para Engineer de Antigravity; Luna conserva
  `context_curator` con esfuerzo `medium`; Flash High conserva review/QA.
- OpenCode Zen sigue read-only y sin promociones automáticas. Gemini 3.6 sigue
  manual/probe-gated.
- Codex CLI 0.145.0 enumera Sol/Terra/Luna. El A/B causal auth+queue deja
  GPT-5.5 como control histórico 6/6 y promueve Luna `medium` 6/6 como Tier 3;
  el auditor pasa sus 6/6 gates, incluida la matriz capacidad+economía+velocidad.
- Paralelismo continúa opt-in; no existe trigger vivo representativo.
- El informe de coste y las conclusiones de orientación esperan volumen real.

## Orden de ejecución

1. Cerrar el contrato y la proyección de catálogo/ranking `model_role_score_v1`
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

## P0.I — Distribución portable e integración poliglota

- [ ] **I.1 Definir la matriz de soporte y el contrato de instalación**.
  - [ ] Separar `verified`, `preview`, `planned` y `unsupported` por combinación
    de OS, arquitectura, versión de Python/Node y modo de distribución. Windows
    nativo es el único bootstrap verificado hoy; Linux y macOS son objetivo hasta
    tener recibos en máquinas limpias.
  - [ ] Publicar requisitos mínimos y recomendados desde una única fuente
    comprobable; los scripts, CI, packaging y documentación deben consumirla o
    validarla para impedir drift.
  - [ ] Definir Git y artefacto de release versionado como vías de descarga,
    con checksum, versión, notas de migración y política de actualización/
    rollback. Un contenedor puede ser opción adicional, no sustituto de los CLI
    y credenciales que viven en el host.
  - Cierre: instalación desde cero y actualización verificadas por plataforma,
    sin pasos implícitos ni afirmaciones de soporte sin evidencia fechada.

- [ ] **I.2 Hacer portable la configuración y el estado por máquina**.
  - [ ] Formalizar capas: defaults versionados, `config/*.example.json`, ajustes
    de usuario por OS, variables de entorno, secrets locales y overrides por
    proyecto bajo `.aiteam/`; documentar precedencia y ownership.
  - [ ] Añadir export/import redacted de configuración operativa. Nunca incluir
    keys, tokens, sesiones CLI, `runtime/`, `venv/`, `node_modules/`, DB activas
    ni rutas absolutas de otra máquina.
  - [ ] Auditar rutas, separadores, encoding, permisos, case sensitivity, señales
    y lanzamiento de procesos; aislar las diferencias de OS detrás de helpers.
  - Cierre: un checkout limpio reconstruye el entorno y una mudanza conserva
    intención/configuración no secreta sin copiar estado local.

- [ ] **I.3 Crear un `doctor` de máquina seguro y legible por humanos/IA**.
  - [ ] Inventariar OS/arquitectura, Python, Node/npm, Git, SQLite, puertos,
    permisos, toolchains, CLIs/adapters y health; discovery siempre read-only y
    sin imprimir secretos.
  - [ ] Ofrecer salida humana y `--json` con estado, versión observada, fuente,
    bloqueo y siguiente acción. Distinguir ausente, no autenticado, incompatible,
    no verificado y degradado.
  - [ ] No instalar runtimes, paquetes globales ni CLIs automáticamente. Cualquier
    mutación requiere comando explícito y conserva un recibo reproducible.
  - Cierre: una IA puede decidir si la máquina está lista usando solo el JSON y
    puede explicar cada bloqueo sin inferirlo de logs libres.

- [ ] **I.4 Unificar bootstrap y ciclo de vida cross-platform**.
  - [ ] Extraer la lógica de `prepare_dev_env.bat`/PowerShell a un contrato
    idempotente con frontends Windows y POSIX equivalentes; mantener comandos de
    start, stop, test y migrate por plataforma.
  - [ ] Usar entorno local del repo, locks/versiones reproducibles y procesos
    hijos explícitos; no depender de asociaciones de `.ps1`, shell interactiva,
    PATH mutable ni instalaciones globales accidentales.
  - [ ] Probar espacios y Unicode en rutas, puertos ocupados, dependencia ausente,
    ejecución repetida, interrupción y limpieza/recovery.
  - Cierre: segunda ejecución no rompe ni reinstala innecesariamente; todo fallo
    deja diagnóstico accionable y no una instalación parcial silenciosa.

- [ ] **I.5 Construir un registro extensible de ecosistemas/toolchains**.
  - [ ] Definir descriptor versionado por ecosistema: detectores, manifests,
    extensiones, binarios/versiones, comandos permitidos de build/test/lint/
    typecheck, cwd/env, artefactos y capacidades requeridas.
  - [ ] Priorizar fixtures para Python; JS/TS; Java/Kotlin; Go; Rust; C/C++;
    .NET; PHP; Ruby; Swift; web/mobile y repos con Docker/devcontainers. Añadir
    otros lenguajes mediante plugins/descriptores, no condicionales dispersos.
  - [ ] Separar detectar de ejecutar: la detección es read-only; instalar
    runtimes/dependencias o ejecutar scripts del proyecto requiere política,
    sandbox, timeout y autorización acordes al riesgo.
  - [ ] Proyectar el stack detectado al Lead, hiring, prompts, tools y gates para
    que cada rol reciba únicamente comandos y capacidades compatibles.
  - Cierre: ningún lenguaje obtiene etiqueta `supported` solo por reconocer una
    extensión; debe completar fixture de ciclo build/test y recibo por OS.

- [ ] **I.6 Validar proyectos poliglotas y entornos heterogéneos**.
  - [ ] Crear fixtures mínimos, monorepo y multi-language con tests de detección,
    selección de comandos, quoting, timeouts, artefactos y errores esperados.
  - [ ] Ejecutar matriz CI por OS/toolchain sin credenciales; reservar canarios
    vivos de adapters para entornos controlados y registrar provenance separada.
  - [ ] Cuando falte soporte, devolver `capability_gap` con descriptor, owner y
    acción; nunca improvisar comandos destructivos ni declarar éxito parcial.
  - Cierre: matriz pública de cobertura, recibos fechados y regresión automática
    para cada celda anunciada como soportada.

- [x] **I.7 Crear onboarding canónico para personas y agentes de IA**.
  - [x] Corregir el README raíz: URL real, bootstrap vigente, modelos no
    hardcodeados y límites de plataforma explícitos.
  - [x] Añadir `docs/INSTALLATION_AND_INTEGRATION.md` con configuración,
    traslado entre máquinas, arranque, validación y protocolo de integración IA.
  - [x] Enlazar la guía desde el índice vivo y registrar el contrato en plan y
    handoff. La documentación describe el estado actual; no da por cerrados
    `doctor`, POSIX, releases ni soporte poliglota todavía no implementados.

- [ ] **I.8 Preparar release y aceptación en máquina limpia**.
  - [ ] Automatizar artefactos, checksums, SBOM/licencias, smoke tests y notas de
    upgrade; excluir secretos y estado local mediante test del contenido final.
  - [ ] Definir checklist de aceptación humana/IA: clone/download, doctor,
    prepare, test mínimo, start/stop, proyecto temporal y desinstalación/rollback.
  - [ ] Probar al menos Windows, Linux y macOS en runners limpios y después una
    máquina real por plataforma antes de promover de `preview` a `verified`.
  - Cierre: una persona o IA sin contexto previo instala siguiendo solo la guía,
    obtiene los mismos checks y deja un recibo auditable de éxito o bloqueo.

## P0 — Modelos, catálogos y promociones

- [ ] **Mantener actualizado y evaluar todo el catálogo modelo+rol**.
  - [x] Baseline `2026-07-22`: defaults, opciones, prompts y scripts activos
    usan las familias vigentes; GPT-5.5 queda solo como control histórico y las
    tarifas antiguas solo como compatibilidad FinOps de runs ya persistidas.
  - [x] Las 46 opciones activas exponen banda de capacidad, economía específica
    del canal y clase/fuente de velocidad bajo
    `capability_economy_speed_v1`; un dato desconocido queda explícito y no se
    sustituye por una estimación.
  - [x] La matriz hermética perfil+modelo+rol verifica capacidades, privacidad,
    workspace, salida estructurada, MCP gobernado y roles deterministas. Tier y
    `best_for` orientan ranking, pero nunca conceden herramientas o autoridad.
  - [x] Generar un inventario durable de cobertura conductual por par exacto
    perfil+modelo+rol: `calibrated`, `partial`, `requires_canary`,
    `requires_tool_fixture`, `manual_candidate` o `blocked`. Baseline actual:
    46 modelos/131 destinos semánticos; 8 calibrados, 5 parciales, 32 canarios
    ejecutables pendientes, 4 fixtures de tools pendientes, 3 candidatos
    manuales y 79 bloqueados por canal/health. Recibo:
    `benchmarks/results/model_evaluation_coverage/model-evaluation-coverage-2026-07-22.json`.
  - [ ] **Lote A — Codex subscription (13 destinos)**: Luna para scouts/worker;
    Terra para Engineer/MCP/QA/review/test design; Sol para Lead/arquitectura/
    quorum. Reutilizar harnesses por familia de contrato y registrar por rol
    semántico, sin contar aliases dos veces.
    - [x] A.1 Alinear `worker` como Tier 3 de solo lectura en políticas, tools,
      sandbox, contrato y scheduler; no ocupa work slots de implementación.
    - [x] A.2 Impedir cierre `done` de worker/scouts/test runner sin
      `AGENT-REPORT` válido: un reintento correctivo y bloqueo+escalado durable
      al segundo fallo; 121 tests dirigidos pasan.
    - [x] A.3 Screening Luna `file_scout` y `worker`, seed 1, esfuerzos low y
      medium. File scout conserva 3/6 anclas en ambos esfuerzos; worker conserva
      7/7, pero low usa un `result` inválido y medium omite el informe. No ampliar a tres
      semillas ni promover; los cuatro recibos negativos quedan registrados sin
      ocultar la deuda `requires_canary`.
    - [x] A.4 Construir fixture MCP gobernado para Luna `web_scout`; discovery
      o acceso web nativo no sustituyen el grant `external_mcp`. Tres semillas
      conservan 8/8 anclas y respetan allow/deny de tools; solo 2/3 cierran en
      una run, mediana 31,094 s (21,641–48,187), por lo que queda `partial` y no
      se promueve.
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
    - [ ] A.6 Calibrar Sol por contratos Tier 1: Lead, arquitectura y quorum,
      sin extrapolar el canario de un rol a sus aliases semánticamente distintos.
  - [ ] **Lote B — Antigravity (12 pendientes + 3 parciales)**: conservar Flash
    High Reviewer como calibrado durable; completar contratos exactos que el
    screening genérico de Lead/scout no demuestra. No repetir review 3/3 ni
    coding Sonnet sin cambio de CLI/modelo/contrato.
  - [ ] **Lote C — locales instalados (8 destinos)**: Gemma 4 E4B/26B y Qwen
    2.5 Coder 14B; medir calidad, latencia/throughput y RAM/VRAM. No descargar
    Qwen3 Coder ni modelos LM Studio ausentes para completar una matriz.
  - [ ] **Lote D — OpenCode (12 destinos)**: reutilizar los canarios durables;
    DeepSeek Reviewer queda parcial 1/3. Mantener read-only y no reabrir el
    transporte server/SDK sin cambio relevante.
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
  - [x] **M.1 Contrato de identidad y estados**.
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
  - [x] **M.2 Métricas y puntuación versionada por rol**.
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
  - [x] **M.3 Read model, persistencia y auditoría**.
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
  - [x] **M.4 API canónica del catálogo**.
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
  - [x] **M.5 Nueva pestaña `Modelos`**.
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
  - [ ] **M.6 Crear y editar equipos con ranking global por rol**.
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
    - [x] **M.6.1 Completar contexto y explicación del selector**.
      - [x] Mostrar breakdown resumido del ganador y la razón legible por la que
        supera al siguiente, incluidos empates por evidencia/identidad.
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
      - Cierre: contexto de issue y economía se resuelven en backend; 19 tests
        dirigidos y 2 E2E protegen gates, orden, explicación y selección owner.
    - [ ] **M.6.2 Unificar todos los consumidores**.
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
      - [ ] Retirar los defaults residuales que aún eligen el primer modelo del
        perfil y delegar gradualmente el endpoint legacy por perfil.
        - [x] Inventariar call sites y separar falsos positivos: los `[0]` de
          “Probar conexión” solo inicializan un probe manual y el de onboarding
          solo explica un rechazo; no asignan agentes.
        - [ ] Sustituir `_choose_model`/`choose_adapter_for_role` en creación
          automática (`_ensure_role_agent`, liveness, Tier 3 y quorum sin pin)
          por el ganador contextual M.7; mientras no haya auto-elegible debe
          conservarse el shim o exigir owner, nunca tomar el primer candidato.
        - [ ] Llevar enforcement cross-provider y recovery cross-adapter a una
          propuesta contextual explícita; no mutar canal/modelo silenciosamente.
        - [ ] Retirar `GET /api/user-adapters/models` de los consumidores una vez
          que probes/config legacy tengan contrato propio y el POST global cubra
          todas las asignaciones. No confundir inventario local con ranking global.
        - Dependencia explícita: el smoke real del 2026-07-22 tiene 0 candidatos
          auto-elegibles; eliminar el shim antes de M.7 rompería bootstrap en vez
          de mejorar la selección.
    - [ ] **M.6.3 Persistencia de la intención del owner**.
      - [x] Etiquetar la elección del selector como `owner_explicit` mediante
        `model_selection_intent_v1` dentro del contrato durable de adapter; el
        modo `default` se añadirá cuando M.7 pueda resolverlo de forma segura.
      - [ ] Distinguir selección `default` frente a `owner_explicit` en el contrato
        durable de asignación, sin inferirlo solo por presencia de `model`.
        - [x] Normalizar todos los flujos owner mediante
          `model_selection_intent_v1/owner_explicit`, vincular `candidate_id` al
          par canónico exacto y rechazar intentos `default` desde APIs owner.
        - [ ] Persistir `mode=default` únicamente desde M.7 con ganador
          auto-elegible y snapshot reproducible; ningún cliente puede fabricarlo.
      - [ ] Probar create/update, aceptación de hiring, reconcile y reload: una
        selección explícita nunca se reemplaza y una default solo se resuelve
        cuando existe candidato auto-elegible.
        - [x] Reconcile preserva byte a byte el par y
          `model_selection_intent_v1` de un agente no-placeholder.
        - [x] Cubrir create/update, materialización del hiring y reload de UI;
          PATCH del mismo par hereda la marca byte a byte y candidate IDs
          falsificados fallan antes de persistir. Evidencia: 186 tests dirigidos,
          3 E2E del selector, TypeScript, ESLint y Ruff verdes.
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
  - [ ] **M.7 Default automático y rollout seguro**.
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
    - [ ] Activar después solo para nuevas plazas sin modelo fijado; nunca mutar
      agentes existentes ni cambiar de adapter silenciosamente. Sin candidato
      elegible, conservar default explícito o pedir owner, no inventar fallback.
      - [x] Construir `selection_intent/mode=default` solo desde snapshot
        `auto_applied`, ganador elegible y hash recalculado; un snapshot shadow o
        manipulado falla cerrado.
      - [ ] Añadir flag/rollback, promoción shadow → recommend → auto y conectar
        únicamente creación de plazas nuevas sin pin.
    - [ ] Validar con canarios de todos los roles y al menos dos canales,
      incluyendo adapter rojo, score alto incompatible, precio desconocido,
      quota pressure, evidencia stale, tie y override manual.
    - Cierre: una única función de selección compartida por bootstrap, hiring,
      Equipo y dispatch; snapshot durable y rollback/flag de desactivación.
  - [ ] **M.8 Cobertura completa y mantenimiento continuo**.
    - [ ] Hacer que los lotes A–E alimenten las métricas normalizadas para todos
      los proveedores y roles; una celda sin test permanece visible como deuda,
      no recibe una puntuación de calidad inventada.
    - [ ] Para cada modelo enumerar todos los roles canónicos: las celdas
      incompatibles quedan explicadas y sin score; cada celda compatible que
      pueda entrar en selección automática recibe fixture/canario exacto y
      valoración propia. No extrapolar una prueba de Engineer a Reviewer/Lead.
    - [ ] Separar benchmarks de capacidad general de los canarios exactos por
      rol/tools y usar varias familias de casos para reducir overfitting.
    - [ ] Recalcular por evento de modelo/CLI/precio/cuota/prompt/tool/contrato y
      mensualmente; conservar histórico y tendencias sin retirar por edad sola.
    - Cierre: 100 % del inventario visible, 100 % de rutas automáticas con
      evidencia fresca y cada hueco restante con owner/bloqueador/próxima acción.

- [x] **Desbloquear y probar Luna como `context_curator`**.
  - [x] Codex CLI actualizado de 0.128.0 a 0.145.0; cache autenticado con
    `client_version=0.145.0` y probe efímero read-only `LUNA_OK` completado.
  - [x] Comparar Luna con GPT-5.5 en auth y queue, tres semillas por caso,
    mismas anclas, ratio total, runs y duración.
  - [x] GPT-5.5 control `low`: 6/6; Luna `low`: 3/6; prompt v2: 4/6;
    Luna `medium` v3: 6/6, 36,55 s medianos y menos tokens medianos que control.
  - [x] Recibo agregado:
    `benchmarks/results/model_calibration/context-curator-gpt-tier3-cli-0.145.0-aggregate-v3.json`.
  - Cierre: matriz completa, juez causal/determinista, recibo agregado y decisión
    explícita. Un fallo de versión o catálogo es diagnóstico, no calidad.
  - Evidencia previa:
    `benchmarks/results/context-curator-auth-codex-luna-seed-1.json`.

- [ ] **Completar calibraciones nuevas por perfil+modelo+rol**.
  - Sol/Terra/Luna, Opus/Sonnet/Haiku y Pro/Flash/Flash-Lite se comparan contra
    baselines locales antes de cambiar gates o cascadas.
  - Antigravity ya completó 27 runs: Pro High, Flash High y Flash Low conservan
    defaults; Sonnet 4.6 superó el canario conductual de coding; Flash Medium no
    se promovió por falta de telemetría económica comparable.
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
    canarios actuales no autorizaron promoción.

- [ ] **Extender BYOK gratuito solo con catálogo ejecutable demostrado**.
  - GitHub Models y OpenRouter requieren credencial real, discovery por ID,
    salida estructurada, probe exacto y límites observados.
  - Calibrar Gemini/Groq por rol antes de ampliar `supported_roles` o defaults.
  - Persistir rate-limit headers sin secretos cuando el helper pueda conservarlos.
  - Bloqueo actual: no hay keys de esos cuatro perfiles y `gh` carece de
    `models:read`; no crear perfiles hasta resolverlo.

- [x] **Cerrar el drift abierto por Codex 0.145.0**. El registro apunta al par
  exacto Luna/`context_curator`, CLI 0.145.0 y seis recibos v3 más el agregado.
  El auditor confirma catálogo, flujo, tiers y frescura: 6/6 gates.

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
- Sonnet 4.6 es Engineer de Antigravity; Flash High conserva review/QA.
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
  `benchmarks/results/model_catalog_drift/model-catalog-drift-2026-07-22.json`.
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
npm run build
npm run lint
npm run test:e2e:orientation
```

Durante iteración usar gates proporcionales; reservar suite completa y canarios
relevantes para cerrar un bloque material.
