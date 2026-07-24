<!-- layer: system-development | audiencia: maintainers, CI y agentes de integración -->

# Artefacto de release

AI Teams dispone de un contrato de empaquetado reproducible y una ruta de
publicación fail-closed. El contrato del ZIP es
`config/release_artifact.v1.json`; cada versión publicable tiene además un
descriptor bajo `config/releases/`, enlazado a sus notas y a
`UPGRADE_AND_ROLLBACK.md`.

## Garantías

El generador:

- toma únicamente archivos controlados por Git;
- rechaza por defecto un worktree sucio, conflictos y symlinks;
- valida rutas, tamaños y archivos obligatorios;
- rechaza runtime local, dependencias reconstruibles, bases SQLite, claves y
  patrones de secretos de alta confianza;
- ordena miembros, normaliza timestamps/modos y usa ZIP sin compresión para que
  el mismo commit produzca los mismos bytes entre plataformas;
- genera manifiesto por archivo, `SHA256SUMS`, checksum externo del ZIP,
  CycloneDX 1.6 y un informe de licencias;
- exige que los tres sidecars externos de manifiesto, SBOM y licencias coincidan
  byte a byte con sus copias internas cuando se descargan juntos;
- consume `package-lock.json` y el lock universal `uv.lock`; los exports
  `requirements.lock` y `requirements-dev.lock` conservan hashes para que el
  bootstrap pueda usar `pip` sin exigir `uv` al usuario.

Las únicas cadenas que pueden neutralizar el detector de secretos están
enumeradas como fixtures literales en el contrato. No se excluyen directorios
completos del escaneo para ocultar falsos positivos.

## Construcción local

Desde la raíz:

```powershell
.\scripts\python_local.bat scripts\build_release_artifact.py `
  --version 0.1.0-preview.1 `
  --output-dir dist\release `
  --allow-dirty
```

`--allow-dirty` sirve solo para diagnóstico: el manifiesto conserva
`dirty=true` y `promotion_allowed=false`. Un candidato real se construye desde
un checkout limpio y exige el tag exacto:

```powershell
.\scripts\python_local.bat scripts\build_release_artifact.py `
  --version 0.1.0 `
  --output-dir dist\release `
  --require-release-tag
```

La salida incluye:

- `ai-teams-VERSION.zip`;
- `ai-teams-VERSION.zip.sha256`;
- `ai-teams-VERSION.manifest.json`;
- `ai-teams-VERSION.sbom.cdx.json`;
- `ai-teams-VERSION.licenses.json`.

El ZIP contiene las mismas piezas bajo `RELEASE-METADATA/`, además de
`SHA256SUMS` para cada miembro de payload.

Un consumidor debe verificarlo antes de extraer:

```powershell
.\scripts\python_local.bat scripts\verify_release_artifact.py `
  dist\release\ai-teams-0.1.0.zip `
  --checksum dist\release\ai-teams-0.1.0.zip.sha256 `
  --require-promotable
```

## CI y publicación

`.github/workflows/release-artifact.yml` construye un preview auditable en
pull requests y ejecuciones manuales. Fija `uv` 0.11.31, exige que `uv.lock`
esté vigente y compara byte a byte ambos exports regenerados. En un tag `v*`
exige tag anotado exacto, descriptor alineado con `pyproject.toml`, notas
versionadas, rollback documentado, `publish.enabled=true`,
`promotion_allowed=true`, verificación completa del ZIP y smoke tras extraerlo.

Solo el job posterior `publish`, asociado al environment `github-release`,
obtiene `contents: write`. Descarga el mismo artifact ya validado, repite los
gates, crea una Release como draft, exige sus cinco assets y solo entonces la
publica. Si ya existe una Release para el tag, falla sin sobrescribirla.

Para preparar un tag, primero deben estar verdes los gates de aceptación de la
versión y su descriptor debe cambiar explícitamente a `publish.enabled=true`:

```powershell
git tag -a v0.1.0 -m "AI Teams v0.1.0"
git push origin v0.1.0
```

El repositorio debería proteger el environment `github-release` con required
reviewers. Esa aprobación es defensa adicional; el descriptor deshabilitado
sigue bloqueando técnicamente la candidata v0.1.0 mientras I.8.4 esté pendiente.

Antes del job `publish`, la matriz `release-acceptance` descarga exactamente el
mismo artifact de CI en Windows, Linux y macOS. El wrapper elige el harness
nativo, conserva un recibo redacted por sistema y exige en las tres celdas los
17 gates canónicos: verificación/extracción, bootstrap idempotente, auditoría,
tests mínimos, start/health/stop, fixture, backup y restauración SQLite,
liberación de puertos y retirada externa de instalación y fixture. En PR y
ejecución manual acepta previews sin convertirlos en promocionables; en tag
exige metadatos promocionables.

Esta matriz demuestra portabilidad en runners efímeros, no en hardware de un
usuario. La promoción de `preview` a `verified` sigue requiriendo conservar
además un recibo de máquina real por familia de sistema operativo.

## Protocolo para agentes de IA

1. No añadir `--allow-dirty` a una ruta de publicación.
2. Conservar Apache-2.0 y `NOTICE`; cambiar licencia requiere decisión explícita
   del owner.
3. Regenerar locks solo con la versión de `uv` fijada en CI.
4. No eliminar blockers del manifiesto: resolver su causa y conservar tests.
5. Usar `verify_release_artifact.py` antes de extraer el ZIP.
6. No habilitar el descriptor hasta adjuntar la evidencia de aceptación.
7. Tratar `promotion_allowed=false` como bloqueo, no como warning.
