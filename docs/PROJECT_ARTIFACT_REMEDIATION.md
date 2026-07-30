# Dry-run de remediación de artefactos legacy

Actualizado: `2026-07-30`

P0.K.8.3 convierte la auditoría read-only en una propuesta local, exacta e
inmutable. No implementa cuarentena, restauración ni borrado. Es una herramienta
manual para una raíz histórica elegida explícitamente; no forma parte de
bootstrap, startup, doctor, actualización ni mantenimiento periódico.

## Fronteras de autoridad

El manifiesto siempre declara:

- `execution_authorized=false`;
- `quarantine_authorized=false`;
- `cleanup_authorized=false`;
- cero movimientos, renames, borrados y escrituras en proyectos;
- `approval.status=not_approved`;
- `next_step.available=false`, porque la cuarentena pertenece a P0.K.8.4.

Una futura aprobación del hash prueba que el owner revisó ese documento exacto.
No es permiso de ejecución. K.8.4 deberá pedir otra acción explícita, revalidar
cada target contra el filesystem vivo y demostrar rollback antes de mover nada.

## Selección exacta

Hay dos modos mutuamente excluyentes:

```powershell
# Todos los candidatos que sobrevivan a una auditoría viva completa
.\scripts\python_local.bat scripts\plan_project_artifact_remediation.py `
  --root "C:\raíz\histórica" `
  --output "C:\fuera\de\la\raíz\plan.json" `
  --include-all-candidates `
  --probe-process-handles

# Uno o más nombres de hijos directos exactos
.\scripts\python_local.bat scripts\plan_project_artifact_remediation.py `
  --root "C:\raíz\histórica" `
  --output "C:\fuera\de\la\raíz\plan-selectivo.json" `
  --target-name "Demo 42" `
  --target-name "Solo 8"
```

No se aceptan paths absolutos como targets, separadores, `.`, `..`, globs,
corchetes, prefijos ni nombres inexistentes. `--include-all-candidates` no
significa wildcard: primero reaudita la raíz y después materializa una lista
ordenada de paths exactos. Una raíz limpia no genera manifiesto.

## Revalidación y denegaciones

Cada ejecución repite la auditoría K.8.1. Un target queda en `denied`, sin acción
propuesta, si es personal, ambiguo, el proyecto activo, está registrado, es
symlink/reparse point, su DB no es válida, Git no pudo observarse, hay cambios
dirty/untracked, existe cualquier remoto, el inventario de tamaño es incompleto
o se observan handles abiertos.

Cada propuesta conserva path resuelto, categoría, confianza, evidencia
estructurada, SHA-256 de evidencia, tamaño, riesgos y contrato de recuperación.
El manifiesto contiene:

- `target_batch_sha256`: sello de la lista exacta, evidencias y acción propuesta;
- `manifest_sha256`: sello de todo el documento salvo su propio campo;
- creación exclusiva con modo `x`: nunca sobrescribe un plan anterior;
- paths locales deliberadamente no redactados, necesarios para revisar targets.

Por contener paths de máquina, el manifiesto vive en almacenamiento local y no
se versiona ni se adjunta sin una redacción adicional.

## Dry-run del owner

La pasada real del `2026-07-30` produjo:

- 2.359 propuestas exactas;
- 0 denegadas dentro del batch, porque solo se seleccionaron candidatos
  revalidados;
- 766.901.650 bytes observados;
- 0 paths fuera de la relación hijo-directo;
- 0 operaciones de filesystem.

Sellos:

- manifiesto:
  `3aadd5a9828c1f8bf8544c578d9ff4463136fb35fcf5e8c59010ed782fc6fcfc`;
- batch:
  `8a1be67c6e1057b82b95931b3f3d6d65e6b90a8125f6f380d173d5a18da2debb`.

El archivo local está en
`%LOCALAPPDATA%\AI Teams\receipts\project-artifact-remediation-dry-run-2026-07-30.json`.
Estos sellos dejan de representar el estado vivo en cuanto cambie cualquier
carpeta; por eso K.8.4 deberá volver a inspeccionar antes de actuar.

## Implementación hermética de K.8.4

`scripts/quarantine_project_artifacts.py` implementa dos comandos manuales y
ninguno de purga:

```powershell
# Solo después de aprobar explícitamente ambos sellos
.\scripts\python_local.bat scripts\quarantine_project_artifacts.py apply `
  --manifest "C:\fuera\de\la\raíz\plan.json" `
  --quarantine-root "C:\cuarentena-existente" `
  --approve-manifest-sha256 "<sha256-manifiesto>" `
  --approve-batch-sha256 "<sha256-batch>"

# Restauración completa, también explícita
.\scripts\python_local.bat scripts\quarantine_project_artifacts.py restore `
  --batch-dir "C:\cuarentena-existente\batch-<id>" `
  --approve-batch-sha256 "<sha256-batch>"
```

La `quarantine_root` debe existir, estar fuera de la raíz histórica, no ser un
symlink/reparse point y compartir filesystem con el origen. No se usa
copy-delete: cada directorio se mueve con rename atómico. Antes de crear el
batch se reauditan todos los proyectos con handles obligatorios, se compara el
hash de evidencia y se calculan checksums de árbol. Cualquier drift, handle,
colisión o cruce de filesystem falla antes del primer move.

El batch conserva una copia exacta del manifiesto y un journal atómico con
batch ID, timestamps, paths, bytes, número de archivos y checksum de cada árbol.
Una interrupción revierte en orden inverso todo lo ya movido; una restauración
comprueba primero que todos los destinos originales estén libres. El journal y
la copia del manifiesto se sellan y permanecen después del restore. No existe
purga, TTL, daemon, startup hook ni borrado automático.

La implementación se validó solo sobre fixtures temporales: aprobación
incorrecta, drift vivo, filesystem distinto, colisión de batch, interrupción en
el segundo move, rollback completo, colisión de restore, manipulación del
journal/manifiesto y apply→restore CLI real en Windows. La raíz histórica del
owner no se ha pasado a `apply`.
