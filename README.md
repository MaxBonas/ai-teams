# AI Teams

Control plane multi-agente Lead-first para equipos de programación. Mantiene en
SQLite issues, agentes del producto, runs, wakeups, interactions, costes y
evidencia; el Lead entiende la tarea, forma el equipo y mantiene el trabajo vivo
hasta cerrarlo o pedir un desbloqueo.

## Estado de instalación

| Entorno | Estado | Entrada |
|---|---|---|
| Windows x86_64 nativo | Verificado para control plane | `scripts\prepare_dev_env.bat` |
| Linux | Objetivo, aún no verificado | `sh scripts/prepare_dev_env.sh` · preview |
| macOS | Objetivo, aún no verificado | `sh scripts/prepare_dev_env.sh` · preview |

“Objetivo” no significa soporte probado. La matriz y los criterios de promoción
viven en [task.md](task.md), bloque P0.I.

## Inicio rápido en Windows

Requisitos: Git, Python 3.10 o posterior, Node.js 22 o posterior con npm y
PowerShell. La fuente comprobable es
[`config/installation_support.v1.json`](config/installation_support.v1.json).
El bootstrap no instala programas globales ni autentica cuentas.

```powershell
git clone https://github.com/MaxBonas/ai-teams.git
Set-Location ai-teams
.\scripts\prepare_dev_env.bat
.\start_ide.bat
```

Abre `http://localhost:9490`. Para detener los procesos:

```powershell
.\stop_ide.bat
```

La aceptación reproducible de Windows usa
[windows-clean-room.yml](.github/workflows/windows-clean-room.yml): repite el
bootstrap, prueba start/stop y crea un proyecto fixture en un runner efímero.
Windows x86_64 está `verified` para ese alcance mediante el
[recibo independiente](benchmarks/results/installation_acceptance/windows-clean-room-f2a20ed.json)
y su [run de GitHub Actions](https://github.com/MaxBonas/ai-teams/actions/runs/30023876549).
Esto no declara autenticados los adapters ni sustituye sus probes.

El bootstrap crea o actualiza dependencias locales del checkout y materializa
configuración runtime desde `config/*.example.json`. En una actualización,
añade defaults ausentes sin sustituir valores locales y conserva un backup de
la primera fusión. Es seguro repetirlo. No
copies desde otra máquina `venv/`, `runtime/`, `node_modules/`, bases activas,
sesiones CLI ni archivos con secretos.

### Actualizar una instalación Windows existente

Con el checkout limpio, la entrada habitual es:

```powershell
.\scripts\update_windows.bat
.\start_ide.bat
```

El actualizador detiene los servicios, exige que no haya cambios Git pendientes,
usa exclusivamente `git pull --ff-only`, reconstruye dependencias y deja un
recibo local en `runtime/last_update.json`. No hace `stash`, `reset`, migraciones
de DB, login ni instalación de CLIs; tampoco borra proyectos, `.aiteam/`,
settings, secretos o sesiones de proveedor.

El bootstrap instala únicamente en `venv/` y `ide-frontend/node_modules` desde
los locks versionados. Arranque y parada usan `runtime/ide_processes.json`:
un puerto ocupado produce diagnóstico y nunca autoriza matar el proceso ajeno.

Para una instalación anterior a la existencia del actualizador:

```powershell
.\stop_ide.bat
git status --short
git pull --ff-only
.\scripts\update_windows.bat -SkipPull -SkipStop
.\start_ide.bat
```

Si `git status --short` muestra archivos, deben revisarse o commitearse antes;
no se deben descartar automáticamente.

Al terminar imprime un diagnóstico de instalación:

- para runs vivos hace falta **un** adapter Lead-capable instalado,
  autenticado y verde; no hacen falta todos los proveedores;
- Codex o Antigravity son opciones primarias guiadas;
- OpenCode Zen es un carril económico opcional y actualmente requiere una API
  key personal incluso para modelos de precio temporalmente cero;
- Ollama y LM Studio son opcionales y nunca deben instalarse como prerrequisito.

El mismo diagnóstico puede repetirse sin mutar la máquina:

```powershell
.\scripts\python_local.bat scripts\audit_installation_support.py --json
```

## Configuración

- La precedencia es defaults versionados → usuario de la máquina → variables
  de entorno allowlisted → `.aiteam/` del proyecto → override de run. El
  contrato legible por máquina está en
  [`config/configuration_layers.v1.json`](config/configuration_layers.v1.json).
- La carpeta de proyectos se elige en la UI o mediante
  `AITEAM_PROJECTS_ROOT`; la variable de entorno gana y la API expone la fuente
  efectiva para evitar que la UI parezca ignorada.
- Los perfiles de adapters, modelos y su health se administran en Config. El
  catálogo vivo es la fuente; este README no congela nombres de modelos.
- `AITEAM_MODEL_DEFAULT_ROLLOUT` gobierna defaults nuevos: `shadow` es el
  fallback seguro y el rollback inmediato; la plantilla usa `recommend`, que
  registra sin asignar. `auto` sigue denegado hasta que cada rol tenga ganador
  vivo elegible y snapshot sellado. Nunca migra agentes existentes.
- API y suscripción son canales independientes. Una key API no autentica un CLI
  y una sesión CLI no habilita una API.
- `.env.example` documenta variables opcionales. Si se crea `.env`, es local y
  nunca se commitea.
- Los artefactos que AI Teams crea en proyectos externos viven bajo `.aiteam/`.
  El producto nunca crea `AGENTS.md`, `CLAUDE.md` o `GEMINI.md` allí.

### Exportar configuración a otra máquina

La exportación conserva intención no secreta y puede incluir la política
estructurada de un proyecto:

```powershell
.\scripts\python_local.bat scripts\config_portability.py export `
  --output "$env:TEMP\aiteam-portable.json" `
  --project "C:\ruta\al\proyecto"
.\scripts\python_local.bat scripts\config_portability.py inspect `
  --input "$env:TEMP\aiteam-portable.json"
```

En destino, importar sin `--apply` solo hace preflight. La mutación requiere
confirmación explícita:

```powershell
.\scripts\python_local.bat scripts\config_portability.py import `
  --input "$env:TEMP\aiteam-portable.json" `
  --project "D:\ruta\al\proyecto"
.\scripts\python_local.bat scripts\config_portability.py import `
  --input "$env:TEMP\aiteam-portable.json" `
  --project "D:\ruta\al\proyecto" `
  --apply
```

El paquete lleva hash, no contiene `projects_root`, secretos, health, sesiones,
DB, runtime ni dependencias. Todo perfil importado queda `untested`: debe
configurarse y probarse en la máquina destino antes de ser seleccionable.

## Verificación de desarrollo

```powershell
.\scripts\python_local.bat scripts\machine_doctor.py --json --strict
.\scripts\python_local.bat scripts\machine_doctor_receipt.py --output runtime\machine-doctor-receipt.json
.\scripts\python_local.bat scripts\audit_platform_portability.py --json --strict
.\scripts\pytest_local.bat tests -q --tb=short
.\scripts\python_local.bat scripts\migrate_to_v2.py --json
Set-Location ide-frontend
node_modules\.bin\tsc.cmd -b
```

La superficie versionada `config/dev_lifecycle.v1.json` mantiene los comandos
`prepare`, `start`, `stop`, `test` y `migrate` para Windows/POSIX. Puede
inspeccionarse sin ejecutar nada:

```powershell
.\scripts\python_local.bat scripts\dev_lifecycle.py --platform windows
.\scripts\python_local.bat scripts\dev_lifecycle.py --platform linux
```

Los frontends POSIX existen en preview, pero no promueven Linux/macOS a soporte
verificado. Requieren aceptación independiente; start es foreground y termina
con Ctrl+C.

El auditor de portabilidad prueba filesystem UTF-8, espacios/Unicode, permisos
y teardown de procesos, y revisa rutas personales/`shell=True`; es local y no
promociona soporte de nuevas plataformas.

`machine_doctor_v1` añade inventario read-only de host, runtimes, SQLite,
puertos, permisos, señales de toolchain y perfiles adapter sin leer credenciales
ni emitir paths personales. Manifest, binario, auth y health son estados
independientes; una detección no declara soporte. La salida añade blockers,
warnings y siguientes acciones; `--strict` devuelve 2 únicamente si existe un
bloqueo real de preparación. El doctor no ejecuta esas acciones. Para conservar
evidencia reproducible, el comando de recibo compara superficies antes/después
y escribe únicamente el path indicado. La remediation guiada se solicita aparte:

```powershell
.\scripts\python_local.bat scripts\machine_doctor_remediate.py `
  --receipt runtime\machine-doctor-receipt.json `
  --action verify_primary_adapter
```

Ese comando no instala ni aplica cambios: devuelve un plan sellado con
`applied=false`.

El actual `aiteam system-check` comprueba que el registro de adapters puede
cargarse y enumera sus tipos; no prueba conectividad, autenticación ni health y
no sustituye la prueba/health explícita de cada perfil ni la remediation.

## Instalar o trasladar a otra máquina

La guía canónica es
[Instalación e integración](docs/INSTALLATION_AND_INTEGRATION.md). Incluye el
procedimiento para personas y un protocolo determinista para agentes de IA, los
límites actuales y la estrategia de soporte poliglota.

Documentación activa adicional:

- [Plan y tareas](task.md)
- [Contrato del artefacto de release](docs/RELEASE_ARTIFACT.md)
- [Migración Paperclip-like](docs/MIGRATION_PAPERCLIP.md)
- [Orquestación](docs/ORCHESTRATION.md)
- [Índice documental](docs/INDEX.md)
- [Handoff vigente](HANDOFF.md)
- [Instrucciones para agentes de desarrollo](AGENTS.md)

## Licencia

AI Teams se distribuye bajo
[Apache License 2.0](LICENSE). Copyright 2026 Max Bonas Fuertes.
