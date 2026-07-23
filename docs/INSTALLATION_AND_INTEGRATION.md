# Instalación e integración de AI Teams

Actualizado: `2026-07-23`

Esta es la guía canónica para instalar AI Teams en una máquina nueva, trasladar
una instalación y entregar la integración a una persona o agente de IA. Describe
el estado real: Windows x86_64 tiene control plane verificado; Linux, macOS, releases
empaquetadas, `doctor --json` y la matriz poliglota siguen en P0.I de `task.md`.

## Contrato de portabilidad

Una plataforma o toolchain solo se anuncia como soportada cuando una máquina
limpia completa instalación, preparación, pruebas mínimas, start/stop y un
proyecto fixture, dejando versión y resultado fechados. Los estados son:

| Estado | Significado |
|---|---|
| `verified` | La combinación exacta tiene recibo vigente y regresión automática. |
| `preview` | Funciona parcialmente, con límites documentados y sin garantía completa. |
| `planned` | Existe tarea y contrato, pero no evidencia suficiente. |
| `unsupported` | Hay incompatibilidad conocida o no existe integración segura. |

Estado actual: Windows x86_64 está `verified` para clone, bootstrap idempotente,
audit, start/stop y proyecto SQLite fixture. La evidencia es el
[recibo redacted](../benchmarks/results/installation_acceptance/windows-clean-room-f2a20ed.json)
y el [run independiente](https://github.com/MaxBonas/ai-teams/actions/runs/30023876549).
Los adapters vivos conservan sus propios gates de instalación, autenticación,
health y modelo exacto. Linux y macOS son `planned`; no deben presentarse como
instalaciones cerradas.

La fuente única legible por máquina es
`config/installation_support.v1.json`. Contiene plataformas, arquitecturas,
runtimes, distribuciones y clases de adapter. El bootstrap y los tests validan
ese contrato; esta guía lo explica, pero no mantiene una matriz paralela.

## Qué viaja y qué se reconstruye

Git es la fuente de verdad. En una máquina nueva se clona o descarga una versión
y se reconstruyen las dependencias.

| Sí se transporta | Se reconstruye o configura localmente |
|---|---|
| Código, tests y documentación | `venv/` y `node_modules/` |
| `config/*.example.json` | `runtime/` y bases SQLite activas |
| Migraciones y defaults versionados | `.env`, keys y secretos |
| Configuración exportada y redacted, cuando exista | Sesiones/autenticación de CLIs |
| Artefactos `.aiteam/` que pertenezcan al proyecto | Rutas absolutas y health de adapters |

No copiar entornos virtuales, dependencias compiladas o sesiones entre sistemas
operativos. No commitear `.env`, secretos, `runtime/`, `venv/` ni
`node_modules/`.

## Instalación verificada: Windows

Requisitos actuales del control plane:

- Git;
- Python `>=3.10` (3.12 recomendado para desarrollo);
- Node.js `>=22` (24 LTS recomendado) y npm `>=10`;
- PowerShell `>=5.1` (7 recomendado).

Para runs vivos se necesita además al menos un adapter Lead-capable instalado,
autenticado, compatible y verde. No es necesario instalar todos los canales:

| Clase | Componentes | Contrato |
|---|---|---|
| Opción primaria | Codex CLI, Antigravity CLI o una API Lead-capable | Hace falta al menos una, no todas |
| Economía opcional | OpenCode Zen | No sustituye por sí solo al Lead vigente |
| Local opcional | Ollama o LM Studio | Solo por decisión del owner y hardware adecuado |

AI Teams no instala automáticamente CLIs globales ni acepta condiciones de
terceros. Después del bootstrap muestra presencia y siguiente acción mediante:

```powershell
.\scripts\python_local.bat scripts\audit_installation_support.py --json
```

Este auditor I.1 es read-only: no prueba credenciales ni health y no sustituye
al `doctor --json` de I.3.

```powershell
git clone https://github.com/MaxBonas/ai-teams.git
Set-Location ai-teams
.\scripts\prepare_dev_env.bat
```

El script coordina validación de runtime, entorno Python y dependencias del
frontend, y termina mostrando adapters primarios y opcionales. Debe ejecutarse
en primer plano y puede repetirse. No instala ni autentica CLIs.

### Adapters recomendados y autenticación

Codex y Antigravity son alternativas primarias, no requisitos acumulativos.
Sus instaladores oficiales se muestran en el manifiesto y deben ejecutarse de
forma explícita por la persona o IA integradora. La autenticación requiere al
owner: `codex login` usa ChatGPT o una key gestionada por Codex; `agy` reutiliza
el keyring del sistema y abre Google Sign-In cuando falta sesión.

OpenCode es opcional. La documentación vigente de Zen exige iniciar sesión y
crear una API key personal, incluso para modelos cuyo precio sea temporalmente
cero. AI Teams puede abrir
`opencode auth login --provider opencode`, detectar después la sesión y usarla
sin copiar su credencial, pero no puede crear la cuenta, aceptar condiciones,
añadir facturación ni compartir una key. “Gratis” describe el precio observado
del modelo, no autenticación anónima ni disponibilidad permanente.

Flujo guiado:

1. Abrir `https://opencode.ai/auth`, iniciar sesión y crear una API key personal.
2. En Config, pulsar **Conectar OpenCode Zen**. Alternativamente, ejecutar
   `opencode auth login --provider opencode`.
3. Pegar la key únicamente en la terminal que controla OpenCode. No introducirla
   en un formulario, prompt o archivo de AI Teams.
4. Comprobar la sesión con `opencode auth list`.
5. Volver a Config y usar **Probar** sobre
   `OpenCode Zen · modelos gratuitos`.

AI Teams no guarda esa key en SQLite ni en la configuración del proyecto. Si el
CLI existe pero el perfil no pasa la prueba, la interfaz debe conservar la
diferencia entre “instalado”, “autenticado” y “modelo ejecutable” y mostrar el
diagnóstico correspondiente.

Ollama y LM Studio nunca forman parte de la instalación mínima. No descargarlos,
instalarlos ni bajar modelos salvo petición explícita del owner; sus perfiles
permanecen visibles como opciones locales.

Arranque y parada:

```powershell
.\start_ide.bat
.\stop_ide.bat
```

Backend y frontend por separado:

```powershell
.\scripts\python_local.bat -m uvicorn api.main:app --reload --port 8010
Set-Location ide-frontend
npm run dev -- --port 9490
```

### Aceptación Windows limpia

El contrato ejecutable de I.1.4 vive en
`.github/workflows/windows-clean-room.yml`. En un runner Windows x86_64 efímero:

- checkout de la revisión exacta;
- bootstrap dos veces;
- auditoría estricta del control plane;
- arranque y health de backend/frontend;
- creación de un proyecto fixture con issue inicial y SQLite válida;
- parada y comprobación de puertos liberados;
- verificación de que el bootstrap no instaló CLIs globales.

El workflow conserva `windows-clean-room-receipt.json` como artefacto redacted.
No autentica proveedores ni ejecuta inferencias: esos gates pertenecen al
`doctor` y a los canarios por adapter. Una ejecución manual del harness sirve
para depurarlo, pero se etiqueta `local_existing_host` y nunca autoriza pasar
Windows de `preview` a `verified` por sí sola:

```powershell
python scripts\accept_windows_clean_room.py `
  --receipt "$env:TEMP\windows-clean-room-receipt.json" `
  --fixture-root "$env:TEMP\aiteam-i1-fixtures"
```

La revisión `f2a20ed` completó el workflow en el
[run 30023876549](https://github.com/MaxBonas/ai-teams/actions/runs/30023876549).
El recibo versionado conserva SHA exacto, provenance del runner, cinco runtimes
listos, 10/10 pasos, una issue/26 tablas, inventario CLI antes/después y ausencia
de rutas o secretos. Por ello Windows x86_64 y `git_checkout` pasan a
`verified` únicamente para el alcance descrito.

## Linux y macOS

El código Python declara `>=3.10` y la pila web es portable en principio, pero
el repositorio aún no tiene bootstrap POSIX ni recibos de aceptación. Instalar
manualmente con `python -m venv`, `pip install -e ".[dev]"` y `npm ci` puede
servir para contribuir a esa validación, pero no convierte la plataforma en
`verified`.

Toda prueba POSIX debe registrar como mínimo OS/arquitectura, versiones de
Python/Node/npm/Git, comandos exactos, checks ejecutados, limitaciones y diff
necesario. No trasladar scripts PowerShell por traducción literal: quoting,
señales, procesos, permisos, encoding y rutas deben probarse.

## Configuración por máquina

La precedencia implementada es:

1. defaults y plantillas versionadas;
2. configuración de usuario de la máquina;
3. variables de entorno explícitas;
4. configuración del proyecto bajo `.aiteam/`, dentro de sus límites;
5. overrides explícitos de una run.

La fuente ejecutable es `config/configuration_layers.v1.json`. Cada capa declara
owner, ubicación, portabilidad y frontera de secretos. El resolver
`aiteam.configuration_layers` hace merge profundo y conserva provenance por
campo. Solo recibe variables de entorno mapeadas expresamente: nunca incorpora
todo el entorno del proceso.

Los secretos no son una sexta capa. La configuración conserva referencias
`secret:proveedor:nombre`; el valor se obtiene del store local y se inyecta
después de resolver, sin persistirlo en el resultado. Health, sesiones CLI,
bases SQLite y workspace actual son estado local, no configuración exportable.

### Export/import redacted

El formato `aiteam_portable_config_v1` conserva solo:

- settings de usuario explícitamente allowlisted y no ligados a paths;
- perfiles custom con identidad, canal, modelos y gobernanza;
- opcionalmente `project_config.json` estructurado bajo sus límites permitidos.

Excluye `projects_root`, rutas absolutas, credenciales inline, contenedores
`env`/headers, store de secretos, health, sesiones CLI, `runtime/`, entornos,
dependencias, bases, assignments, runs, costes y telemetría. Las referencias
`secret:...` pueden viajar porque no contienen el valor y permiten explicar qué
credencial local falta.

Exportar e inspeccionar:

```powershell
.\scripts\python_local.bat scripts\config_portability.py export `
  --output "$env:TEMP\aiteam-portable.json" `
  --project "C:\proyectos\mi-proyecto"
.\scripts\python_local.bat scripts\config_portability.py inspect `
  --input "$env:TEMP\aiteam-portable.json"
```

El proyecto es opcional. Si se incluye, el paquete nunca contiene su nombre o
path de origen. `instructions.md` y otros textos libres permanecen en el Git
del proyecto; no se duplican dentro del paquete.

En la máquina destino:

```powershell
# Preflight: valida schema, SHA-256, paths, secretos y destino; no escribe.
.\scripts\python_local.bat scripts\config_portability.py import `
  --input "$env:TEMP\aiteam-portable.json" `
  --project "D:\proyectos\mi-proyecto"

# Aplicación explícita y atómica por archivo.
.\scripts\python_local.bat scripts\config_portability.py import `
  --input "$env:TEMP\aiteam-portable.json" `
  --project "D:\proyectos\mi-proyecto" `
  --apply
```

La importación hace merge; no reemplaza el store de secretos ni borra perfiles
ajenos. Invalida el health de cada perfil importado con
`portable_configuration_imported_requires_retest`. Después se instala/localiza
el canal exacto, se autentica localmente y se ejecutan health y probe del modelo.
Un perfil no se vuelve verde por aparecer en el paquete.

Las asignaciones vivas del equipo están en SQLite y se excluyen. En destino se
reconcilia o forma de nuevo el equipo usando los perfiles que pasen los gates;
no se copia una selección que podría ser incompatible o no estar disponible.

Ubicación actual de configuración de usuario:

- Windows: `%LOCALAPPDATA%\AI Teams`;
- Linux/macOS: `$XDG_CONFIG_HOME/aiteams` o `~/.config/aiteams`;
- tests/automatización: override `AITEAM_USER_CONFIG_DIR`.

`AITEAM_PROJECTS_ROOT` tiene prioridad para la raíz de proyectos. La UI también
puede persistirla en `settings.json`. Los perfiles de adapter, health y secretos
son locales: un perfil solo está disponible si su canal exacto está configurado
y verde en esa máquina.

Para APIs, configurar únicamente las variables necesarias usando
`.env.example` como referencia. Para suscripciones, instalar y autenticar el CLI
fuera de AI Teams y después comprobar el adapter. Nunca usar `npx -y` o un
instalador implícito como comando de producción: cambiaría binarios durante una
run y ocultaría la versión real.

### Frontera de filesystem y procesos

`aiteam.platform_runtime` concentra las diferencias que no deben dispersarse
por adapters y routers:

- comparación de paths sensible a mayúsculas en POSIX e insensible en Windows;
- resolución de ejecutables y shims `.cmd`/`.exe`, rechazando aliases
  `WindowsApps` y paths extensionless que `CreateProcess` no puede ejecutar;
- layout del Python de `venv` por plataforma;
- stdin/stdout/stderr UTF-8 y entorno Python hijo UTF-8;
- grupos de proceso separados y teardown del árbol completo al vencer timeout;
- fixtures temporales con espacios, caracteres Unicode y permisos ejecutables.

Auditoría local, read-only salvo sus fixtures temporales:

```powershell
.\scripts\python_local.bat scripts\audit_platform_portability.py --json --strict
```

La salida `platform_portability_audit_v1` no incluye secretos ni paths
absolutos de la máquina. Comprueba además que los consumidores críticos usan la
frontera, que el código activo no contiene rutas personales ni `shell=True` y
declara siempre `support_promotion=false`: pasarla localmente no convierte
Linux, macOS o ARM64 en plataformas soportadas.

Los scripts de NordVPN son utilidades opcionales, no parte del bootstrap.
Detectan paths de esta instalación, conservan exclusiones existentes y hacen
dry-run por defecto; modificar el settings del proveedor exige PowerShell admin
y `-Apply`, crea backup y restaura ante fallo.

## Validación mínima

Después de preparar el checkout:

```powershell
.\scripts\python_local.bat scripts\audit_platform_portability.py --json --strict
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\migrate_to_v2.py --json
.\scripts\python_local.bat -m aiteam.cli system-check
Set-Location ide-frontend
node_modules\.bin\tsc.cmd -b
```

`system-check` valida únicamente que el registro de adapters puede cargarse y
enumera sus tipos; no prueba conectividad, autenticación ni health. El futuro
`doctor --json` añadirá inventario completo, clasificación de bloqueos y salida
estable para máquinas; hasta entonces, no interpretar un CLI encontrado como
autenticado o compatible. Los canarios vivos consumen cuota y se ejecutan solo
de forma intencional.

## Traslado y actualización

### Actualización en la misma máquina Windows

Para instalaciones que ya incluyen el actualizador:

```powershell
.\scripts\update_windows.bat
.\start_ide.bat
```

El script:

1. aborta si encuentra cambios tracked o untracked sin resolver;
2. detiene backend/frontend;
3. ejecuta `git pull --ff-only`, nunca `reset`, `stash` o checkout destructivo;
4. repite el bootstrap;
5. fusiona JSON a tres vías: plantilla anterior, plantilla nueva y cambios
   locales, manteniendo solo los overrides reales del owner;
6. deja `runtime/last_update.json` con revisión anterior, nueva y resultado;
7. no arranca automáticamente, para que un fallo sea visible antes de reanudar.

La primera fusión de un JSON heredado conserva
`*.pre_template_sync.bak`. Un JSON inválido no se reemplaza: el bootstrap falla
con diagnóstico y deja el archivo intacto. Los perfiles de adapter que
personalizan un perfil incorporado heredan campos nuevos del default y solo
sobrescriben los campos declarados localmente.

Una instalación anterior a `scripts/update_windows.bat` debe hacer una única
transición manual:

```powershell
.\stop_ide.bat
git status --short
git pull --ff-only
.\scripts\update_windows.bat -SkipPull -SkipStop
.\start_ide.bat
```

Si `git status --short` no está vacío, detenerse y revisar. No usar
`git reset --hard` ni copiar encima un checkout nuevo. Las sesiones Codex,
Antigravity/OpenCode y el store de secretos son locales y se conservan; su
health se debe volver a comprobar después de actualizar.

### Traslado a otra máquina

1. Commit/push de código y configuración versionable; verificar que no contiene
   secretos ni estado local.
2. En destino, clonar o hacer `git pull` sobre una copia limpia.
3. Ejecutar el bootstrap local; no copiar `venv/`, `node_modules/` o `runtime/`.
4. Configurar la nueva raíz de proyectos y las credenciales/sesiones locales.
5. Opcionalmente importar el paquete redacted, primero sin `--apply`.
6. Ejecutar la validación mínima y guardar versiones/resultados.
7. Migrar una DB solo con backup y el migrador canónico. Una DB de proyecto es
   dato del usuario, no parte del paquete de aplicación.

Las releases con checksum, notas de migración y rollback son trabajo pendiente
I.8. Mientras no existan, Git es la vía canónica de actualización.

## Integración poliglota

“Máximo de lenguajes” se consigue con un registro extensible, no con una lista
de extensiones ni prompts genéricos. Cada descriptor de ecosistema debe declarar:

- detectores y manifests con prioridad;
- binarios y rangos de versión observables;
- comandos permitidos de build, test, lint y typecheck;
- directorio de trabajo, variables, timeout y artefactos esperados;
- capacidades/tools necesarias y riesgos de ejecutar scripts del proyecto;
- fixtures por OS, incluido monorepo cuando aplique.

La detección es read-only. Instalar runtimes o dependencias y ejecutar scripts
del repositorio son acciones separadas y gobernadas. El Lead, hiring, prompts,
roles y gates deben consumir el mismo perfil detectado. Si falta una capacidad,
el resultado correcto es `capability_gap` con siguiente acción, no improvisar un
comando ni marcar éxito.

La prioridad inicial de fixtures está detallada en P0.I.5: Python, JS/TS,
Java/Kotlin, Go, Rust, C/C++, .NET, PHP, Ruby, Swift, web/mobile y
Docker/devcontainers. Ninguna celda se llama `supported` antes de completar su
ciclo build/test en la plataforma correspondiente.

## Protocolo para un agente de IA integrador

Un agente sin contexto previo debe seguir este orden:

1. Leer `AGENTS.md`, `README.md`, este documento, `task.md` y `HANDOFF.md`.
2. Inspeccionar `git status` y preservar cambios ajenos; Git es la fuente de
   verdad, pero un worktree sucio pertenece al usuario.
3. Inventariar OS, arquitectura y versiones sin exponer variables, tokens o
   contenido de stores de credenciales.
4. Clasificar la plataforma con la tabla anterior. No promocionar `planned` por
   intuición ni por una única ejecución parcial.
5. Preparar usando el entrypoint de la plataforma. No instalar runtimes/CLIs
   globales, autenticar cuentas o alterar PATH sin autorización explícita.
   Ollama y LM Studio son siempre opcionales.
6. Configurar paths y adapters locales. Tratar API y suscripción como canales
   independientes y exigir health del par exacto.
7. Ejecutar checks herméticos primero; pedir permiso antes de canarios vivos o
   acciones que consuman cuota.
8. Informar comandos, versiones, resultados, archivos cambiados, bloqueos y
   siguiente acción. Nunca esconder un fallo de auth, catálogo o plataforma como
   fallo de calidad del modelo.

En proyectos externos, todos los artefactos del producto van bajo `.aiteam/` y
las instrucciones persistentes del usuario en `.aiteam/instructions.md`. AI
Teams nunca debe crear `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` o equivalentes en
esos proyectos.

## Criterio de aceptación de una máquina nueva

La integración se considera completa únicamente si:

- checkout y bootstrap terminan sin depender de archivos de la máquina origen;
- una segunda preparación es idempotente;
- tests mínimos, migración dry-run/typecheck y start/stop producen resultados
  reproducibles;
- configuración y secretos quedan en ubicaciones locales correctas;
- adapters seleccionables tienen versión, autenticación y health verificadas;
- cualquier plataforma/toolchain no cubierta queda etiquetada y accionable;
- se conserva un recibo sin secretos con fecha, versiones y resultados.

## Fuentes de instalación y autenticación

- Codex CLI: https://github.com/openai/codex
- OpenCode CLI: https://opencode.ai/docs/cli
- OpenCode Zen: https://opencode.ai/docs/zen
- Antigravity CLI: https://antigravity.google/docs/cli-install
- Ciclo de soporte Node.js: https://nodejs.org/en/about/previous-releases

El backlog ejecutable y los criterios de cierre viven en `../task.md` P0.I; esta
guía no es un segundo plan.
