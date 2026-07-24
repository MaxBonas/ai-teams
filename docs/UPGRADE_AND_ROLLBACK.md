# Actualización y rollback de AI Teams

Este procedimiento sirve tanto para una instalación clonada como para un ZIP de
GitHub Releases. La actualización se hace **side-by-side**: la versión nueva va
en otro directorio y la versión anterior no se borra hasta aceptar el resultado.
Nunca se copian `venv/`, `node_modules/`, `.env`, sesiones de proveedor ni
`runtime/` entre máquinas.

## 1. Inventario y parada

1. Anota la versión/commit actual y la ruta de cada SQLite que uses.
2. Detén AI Teams con `.\stop_ide.bat` o el mecanismo equivalente.
3. Confirma que no quedan procesos escribiendo en esas bases.
4. Conserva una copia externa de cada base antes de empezar. El backup automático
   del migrador es una segunda protección, no su sustituto.

En PowerShell, usa rutas explícitas y valida el hash de la copia:

```powershell
$AiTeamsDb = (Resolve-Path "C:\ruta\explicita\aiteam.db").Path
$BackupDir = (Resolve-Path "C:\ruta\explicita\backups").Path
$ManualBackup = Join-Path $BackupDir "aiteam.before-upgrade.sqlite"
Copy-Item -LiteralPath $AiTeamsDb -Destination $ManualBackup
Get-FileHash -LiteralPath $AiTeamsDb -Algorithm SHA256
Get-FileHash -LiteralPath $ManualBackup -Algorithm SHA256
```

## 2. Adquirir y verificar

Descarga del mismo GitHub Release el ZIP y su fichero `.zip.sha256`. Ejecuta el
verificador desde una copia confiable del repositorio o desde la versión anterior:

```powershell
.\scripts\python_local.bat scripts\verify_release_artifact.py `
  C:\Descargas\ai-teams-0.1.0.zip `
  --checksum C:\Descargas\ai-teams-0.1.0.zip.sha256 `
  --require-promotable
```

El resultado debe contener `"ok": true`. El comando valida el checksum externo,
la ausencia de rutas inseguras/duplicadas, `SHA256SUMS` para todo el payload y la
coherencia de versión, revisión y autorización de promoción. No extraigas ni
ejecutes un paquete que falle este gate.

Extrae el ZIP en un directorio nuevo. Para una actualización por Git, clona o
crea otro worktree en el tag exacto; no uses un reset destructivo sobre la copia
activa.

## 3. Preparar la versión nueva

Desde el directorio nuevo:

```powershell
.\scripts\prepare_dev_env.bat
.\scripts\python_local.bat scripts\machine_doctor.py --json
```

Resuelve solo los requisitos básicos que indique el diagnóstico. Codex CLI,
OpenCode Zen Free y Antigravity son canales recomendados; Ollama y LM Studio son
opcionales. Las credenciales permanecen locales y se configuran siguiendo
`INSTALLATION_AND_INTEGRATION.md`.

## 4. Migrar cada base

Primero ejecuta el dry-run:

```powershell
.\scripts\python_local.bat scripts\migrate_to_v2.py --db $AiTeamsDb --json
```

Si el recibo es correcto, aplica la migración manteniendo el backup automático:

```powershell
.\scripts\python_local.bat scripts\migrate_to_v2.py --db $AiTeamsDb --apply --json
```

No uses `--no-backup` en una actualización. Registra la ruta
`.pre_v2_<timestamp>.sqlite.bak` devuelta por el migrador.

## 5. Smoke y aceptación

Arranca la copia nueva, comprueba health/start/stop y ejecuta el proyecto fixture
del checklist I.8.3. Acepta la actualización solo si el recibo demuestra que:

- el doctor no presenta blockers básicos;
- la base abre y el esquema esperado está presente;
- una tarea temporal completa su ciclo sin loops de tests;
- start/stop no deja procesos huérfanos.

Hasta entonces conserva intacta la instalación anterior y todos los backups.

Para una aceptación automatizada y desechable del ZIP usa
`scripts/accept_release_archive.py` desde un checkout confiable. El wrapper
realiza este recorrido, verifica los 17 pasos canónicos y elimina instalación y
fixture desde un proceso externo. `--allow-preview` nunca autoriza promoción.

## 6. Rollback

Detén primero la versión nueva. Si la base no fue migrada ni recibió escrituras,
puedes arrancar directamente la versión anterior.

Si la base fue migrada o modificada, volver solo al código anterior **no es
seguro**. Conserva la base fallida para diagnóstico y restaura el backup exacto
con todos los procesos detenidos:

```powershell
$FailedDb = "$AiTeamsDb.failed-upgrade"
Copy-Item -LiteralPath $AiTeamsDb -Destination $FailedDb
Copy-Item -LiteralPath $ManualBackup -Destination $AiTeamsDb -Force
Get-FileHash -LiteralPath $AiTeamsDb -Algorithm SHA256
Get-FileHash -LiteralPath $ManualBackup -Algorithm SHA256
```

Los dos hashes finales deben coincidir. Arranca entonces la instalación anterior
y repite health y un smoke de lectura. El rollback de código/DB no revierte
logins, instalaciones de CLI ni cambios externos realizados por proveedores.

## 7. Cierre

Solo después de la aceptación conserva o elimina manualmente la copia anterior.
Mantén los recibos de versión, hashes, migración, smoke y rollback disponible.
Si cualquiera falta, la actualización queda incompleta, no “probablemente bien”.
