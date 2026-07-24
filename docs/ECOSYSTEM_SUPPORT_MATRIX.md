# Matriz de validación de ecosistemas

Actualizado: `2026-07-23`

Esta matriz publica evidencia de fixtures, no promesas globales de soporte.
`config/ecosystems.v1.json` sigue siendo el catálogo canónico. Una detección,
un runtime instalado o un comando planificable no equivalen a una celda
soportada.

## Evidencia actual

| Caso | Windows x86_64 local | Windows CI | Linux CI | macOS CI | Alcance |
|---|---:|---:|---:|---:|---|
| `python_pytest` | pass | pass | pass | pass | pytest mínimo |
| `javascript_npm` | pass | pass | pass | pass | build, test, lint y typecheck npm |
| `monorepo_python` | pass | pass | pass | pass | detección y pytest en monorepo |
| `monorepo_javascript` | pass | pass | pass | pass | detección, build y test npm en monorepo |
| `java_maven_junit` | pass | pass | pass | pass | package Maven, JUnit y surefire report |
| `dotnet_xunit` | bloqueado: falta SDK | pass | pass | pass | build y xUnit; tener solo runtime no basta |
| `go_builtin` | bloqueado: falta Go | pass | pass | pass | build y test estándar, sin dependencias |
| `rust_cargo` | bloqueado: falta Cargo | pass | pass | pass | build `--locked`, test y artefacto rlib |
| `c_cpp_cmake` | bloqueado: falta CMake | pass | pass | pass | configure, build y CTest en orden obligatorio |

Los recibos locales se generaron en un worktree sucio y por ello solo son
evidencia de desarrollo, no promoción.
La run
[`30085247826`](https://github.com/MaxBonas/ai-teams/actions/runs/30085247826)
produjo los receipts ligados al SHA
`775e72e09fde87a1b5251f44076b4f6c4690a91e`. Su job
`evidence-gate` no confía en el estado de los jobs: descarga los 18 receipts y
exige las 27 combinaciones OS/caso, SHA único, worktree limpio, todos los casos
`passed` y `support_claim=false`. El agregado
`ecosystem_ci_evidence_v1` está conservado como
`benchmarks/results/ecosystem_validation/polyglot-ci-775e72e.json`, SHA-256
`9ce3c81b41817a9a7b3fde78a99ea5753722385f8cb309cfe5b204f802d2fc64`;
sigue sin promover soporte automáticamente.

## Estado del resto del catálogo

Java/Kotlin, .NET, Go y Rust conservan estado `planned` aunque ya tengan
fixture: Java solo pasó en Windows local con worktree sucio; .NET demostró un
gap de SDK; Go, Rust y C/C++ demostraron runtimes ausentes. C/C++ ya modela
`configure → build → test`: las fases posteriores no se ejecutan sin el recibo
anterior. PHP, Ruby, Swift, Web/Mobile y Containers/Dev Containers aún
necesitan fixture. Que un runtime exista en una máquina no cambia ese estado.
Cada familia necesita acción real, artefactos cuando correspondan y recibos
por OS.

Cuando una celda no puede ejecutarse, el validador devuelve
`capability_gap_v1` con ecosistema, acción, descriptor, owner y siguiente paso.
No instala runtimes, no improvisa comandos y no convierte bloqueos en éxitos.

## Reproducción

```powershell
.\scripts\python_local.bat scripts\validate_ecosystem_fixtures.py `
  --require python_pytest `
  --require javascript_npm `
  --require monorepo_python `
  --require monorepo_javascript `
  --receipt runtime\receipts\ecosystem-local.json
```

Los recibos locales viven bajo `runtime/` y no se versionan. CI publica los
suyos como artifacts efímeros con fecha, OS, arquitectura, SHA, estado del
worktree y versiones de runtime sin rutas absolutas.
