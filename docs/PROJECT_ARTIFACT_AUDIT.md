# Auditoría segura de artefactos de proyecto

Actualizado: `2026-07-30`

Este documento define el inventario local de P0.K.8.1. Su objetivo es separar
proyectos personales, proyectos AI Teams que deben preservarse y artefactos
legacy que podrían revisarse después. No limpia, mueve, renombra ni borra.

## Invariante de seguridad

El auditor solo admite una raíz absoluta elegida explícitamente, inspecciona
sus hijos directos y no sigue symlinks ni reparse points. El receipt debe
escribirse fuera de la raíz auditada. Siempre publica:

- `cleanup_authorized=false`;
- cero movimientos, borrados, renames y escrituras en proyectos;
- rutas relativas de primer nivel, nunca la ruta absoluta de la máquina;
- hosts remotos sin URL, usuario, path ni credenciales;
- referencia de branch e identidad de objetivo como SHA-256, no texto libre;
- errores tipados, no stdout/stderr potencialmente sensible.

Una DB SQLite se abre con `mode=ro&immutable=1`. Esto impide escrituras, pero
puede omitir un WAL activo; el receipt declara esa limitación. El probe de
handles es opt-in y solo registra conteos, nunca PID, proceso o path.

## Clasificación

| Clase | Regla conservadora |
|---|---|
| `active_current_project` | Coincide exactamente con el workspace activo observado. |
| `aiteam_preserve_or_migrate` | Tiene identidad AI Teams válida, pero está referenciado, contiene trabajo Git/remoto o no reúne evidencia fuerte de fixture legacy. |
| `aiteam_disposable_candidate` | DB válida, familia numerada legacy conocida, inventario completo y ningún trabajo Git/remoto observado. Sigue sin autorizar ninguna acción. |
| `ambiguous_owner_review_required` | Enlace/reparse, DB ausente/corrupta bajo `.aiteam`, Git no observable, inventario incompleto o evidencia contradictoria. |
| `personal_protected` | No tiene identidad `.aiteam`; se protege aunque el nombre parezca generado o numerado. |

`.aiteam` identifica procedencia, no propiedad exclusiva ni permiso de
eliminación. Cualquier duda cae en conservación o revisión humana.

## Ejecución

```powershell
$root = "C:\ruta\absoluta\de\proyectos"
$receipt = Join-Path $env:LOCALAPPDATA "AI Teams\receipts\project-artifact-audit.json"
.\scripts\python_local.bat scripts\audit_project_artifacts.py `
  --root $root `
  --output $receipt `
  --active-workspace "C:\ruta\exacta\si\existe" `
  --registry-workspace "C:\otra\ruta\referenciada" `
  --probe-process-handles
```

Omitir una referencia que no exista; no inventarla. El comando termina con
éxito aunque existan carpetas ambiguas, porque encontrarlas es un resultado de
auditoría. Falla si la raíz es relativa/inválida, es un reparse point o el
receipt intenta escribirse dentro del árbol inspeccionado.

## Evidencia local del owner

La ejecución read-only del `2026-07-30` sobre la raíz elegida produjo un
receipt local redacted de 2.716 carpetas:

- 2.359 `aiteam_disposable_candidate`, exactamente en las familias conocidas
  Demo (998), OrgChart (333), Reconcile (333), Quorum (244), Solo (236) y
  AnthropicLead (215);
- 342 `aiteam_preserve_or_migrate`;
- 15 `personal_protected`;
- cero ambiguas y cero `active_current_project`, porque el workspace persistido
  válido estaba fuera de la raíz seleccionada.

Se observaron 2.701 DB válidas, 2.035 repositorios Git y cero handles de archivo
abiertos visibles con los permisos de la ejecución. El receipt final quedó
fuera de la raíz auditada con SHA-256 de contenido
`b0479d34eeec4be91c5f61ff5583678a80a1b520f774eab3bcc911cedb12965b`.
Estas cifras no son un manifiesto de limpieza. P0.K.8.3 deberá crear
otro artefacto, inmutable y aprobable, y volverá a denegar cualquier caso
protegido o contradictorio.
