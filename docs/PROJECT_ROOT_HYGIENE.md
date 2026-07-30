# Higiene de la raíz de proyectos

Actualizado: `2026-07-30`

Este contrato cubre P0.K.8.5. AI Teams comprueba la raíz de proyectos durante
el primer uso, antes de crear un proyecto y al editar Configuración. La
comprobación es local y de solo lectura: no crea, mueve, renombra ni borra
carpetas y no instala limpieza de arranque, periódica o por TTL.

## Flujo para una persona

1. Escribir o confirmar una ruta absoluta.
2. Pulsar **Comprobar sin guardar**.
3. Revisar el estado y los contadores agregados.
4. Guardar solo cuando la observación corresponda a la ruta visible.
5. Si se detecta legado, ejecutar primero la auditoría completa K.8.1. El aviso
   no autoriza K.8.3/K.8.4 ni convierte una carpeta en desechable.

Cambiar el texto de la ruta invalida el preview anterior. Guardar mezcla solo
`projects_root` en `settings.json`; no reemplaza otras preferencias ni toca
credenciales, perfiles de adapter o proyectos existentes. Una raíz declarada
mediante `AITEAM_PROJECTS_ROOT` cuenta como configuración efectiva y conserva
su precedencia.

## Estados

| Estado | Significado | Siguiente acción |
|---|---|---|
| `clean` | No se observan restos conocidos en el barrido ligero. | Continuar. |
| `legacy_artifacts_detected` | Hay familias numeradas conocidas, tombstones o staging legacy. | Ejecutar K.8.1 y revisar. |
| `review_required` | Hay reparse points, enlaces o errores de lectura. | Revisar manualmente y usar K.8.1. |
| `root_missing` | La ruta todavía no existe. | Crear/elegir la carpeta fuera del doctor y repetir. |
| `not_configured` | No existe raíz efectiva. | Configurar una ruta absoluta. |

Una carpeta sin `.aiteam/aiteam.db` no se atribuye a AI Teams aunque su nombre
sea numerado. Se trata como contenido personal protegido. La presencia de
`.aiteam` tampoco concede autoridad de limpieza.

## Contrato técnico

`aiteam/project_hygiene.py` produce `project_hygiene_v1`. El documento:

- no contiene paths; la raíz se representa con un fingerprint SHA-256;
- no sigue symlinks/reparse points;
- no abre SQLite ni invoca Git;
- solo cuenta directorios hijos directos y staging interno conocido;
- declara explícitamente que doctor y lifecycle no pueden mutar.

Superficies:

- `GET /api/settings`: configuración efectiva y observación actual;
- `POST /api/settings/project-hygiene/preview`: observa la ruta enviada en el
  body sin persistirla ni crearla;
- `POST /api/settings`: guarda la raíz y devuelve una observación nueva;
- machine doctor: incluye la proyección redacted y emite el warning
  `project_root_hygiene_requires_attention` con una acción no mutante.

El esquema `config/machine_doctor.v1.schema.json` falla cerrado para informes
nuevos, pero `validate_machine_inventory` acepta recibos históricos que aún no
incluían `project_hygiene`.

## Protocolo para una IA integradora

1. Consultar `GET /api/settings`; no inferir configuración solo por el JSON
   local, porque el entorno puede tener precedencia.
2. Enviar la ruta exacta al endpoint de preview mediante POST. No ponerla en
   query params o logs.
3. Confirmar `scope.read_only=true`, `lifecycle.doctor_can_mutate=false` y que
   el fingerprint/preview sigue asociado a la ruta que muestra la UI.
4. No presentar `legacy_artifacts_detected` como error fatal ni ejecutar
   remediación automáticamente.
5. Para diagnóstico profundo, seguir `PROJECT_ARTIFACT_AUDIT.md`.
6. Para cualquier cuarentena histórica, detenerse y exigir la autorización
   exacta descrita en `PROJECT_ARTIFACT_REMEDIATION.md`.

## Límites deliberados

El barrido ligero no decide si un repositorio es desechable, no inspecciona
dirty/untracked/remotos, no valida la DB y no busca handles. Es una señal de
configuración, no una prueba de limpieza ni de seguridad para borrar.

K.8.6.1 ya repite el contrato en una raíz hermética adversa mediante
`scripts/audit_project_portability_acceptance.py`. La matriz incluye proyecto
personal numerado, Git limpio/dirty/remoto con credenciales redacted, DB
corrupta, staging interrumpido y symlink/reparse no seguido; exige bytes
protegidos intactos, cero writers y cero lifecycle de limpieza. También compone
los aceptadores vigentes de adapters, commit guiado, preflight no programativo,
actualización de CLIs y React+TypeScript. El receipt versionado vive en
`benchmarks/results/guided_setup/project-portability-acceptance-2026-07-30.json`.

Esto no sustituye K.8.6.2–K.8.6.4: aún hay que reparar el runner Windows que
invoca el comando legacy retirado, y después obtener evidencia SHA-bound en un
clone limpio y una instalación existente independiente. La raíz real continúa
siendo read-only y la aceptación hermética no concede autoridad de cuarentena.
