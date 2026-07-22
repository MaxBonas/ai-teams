# Instalación e integración de AI Teams

Actualizado: `2026-07-22`

Esta es la guía canónica para instalar AI Teams en una máquina nueva, trasladar
una instalación y entregar la integración a una persona o agente de IA. Describe
el estado real: Windows tiene bootstrap probado; Linux, macOS, releases
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

Estado actual: Windows nativo es `verified` para el bootstrap de desarrollo.
Linux y macOS son `planned`; no deben presentarse como instalaciones cerradas.

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

Requisitos actuales:

- Git;
- Python `>=3.10` (3.12 recomendado para desarrollo);
- Node.js y npm compatibles con el lock del frontend;
- PowerShell;
- al menos un adapter LLM instalado y autenticado si se harán runs vivos.

```powershell
git clone https://github.com/MaxBonas/ai-teams.git
Set-Location ai-teams
.\scripts\prepare_dev_env.bat
```

El script coordina validación de runtime, entorno Python y dependencias del
frontend. Debe ejecutarse en primer plano y puede repetirse. No instala ni
autentica Codex, Gemini, Claude u otros CLIs.

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

La precedencia prevista y que debe preservarse al implementar el export/import
es:

1. defaults y plantillas versionadas;
2. configuración de usuario de la máquina;
3. variables de entorno explícitas;
4. configuración del proyecto bajo `.aiteam/`, dentro de sus límites;
5. overrides explícitos de una run.

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

## Validación mínima

Después de preparar el checkout:

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\migrate_to_v2.py --json
.\scripts\python_local.bat -m aiteam.cli system-check
Set-Location ide-frontend
node_modules\.bin\tsc.cmd -b
```

`system-check` es un smoke test de adapters. El futuro `doctor --json` añadirá
inventario completo, clasificación de bloqueos y salida estable para máquinas;
hasta entonces, no interpretar un CLI encontrado como autenticado o compatible.
Los canarios vivos consumen cuota y se ejecutan solo de forma intencional.

## Traslado y actualización

1. Commit/push de código y configuración versionable; verificar que no contiene
   secretos ni estado local.
2. En destino, clonar o hacer `git pull` sobre una copia limpia.
3. Ejecutar el bootstrap local; no copiar `venv/`, `node_modules/` o `runtime/`.
4. Configurar la nueva raíz de proyectos y las credenciales/sesiones locales.
5. Ejecutar la validación mínima y guardar versiones/resultados.
6. Migrar una DB solo con backup y el migrador canónico. Una DB de proyecto es
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

El backlog ejecutable y los criterios de cierre viven en `../task.md` P0.I; esta
guía no es un segundo plan.
