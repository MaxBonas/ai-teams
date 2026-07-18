# Prompt de continuidad: quorum profundo Lead-owned

Usa este prompt para continuar el trabajo de orquestación en AI Teams:

---

Trabajas en `AI Teams`, un control plane multi-LLM Lead-first sobre SQLite. Lee
primero `AGENTS.md`, `docs/MIGRATION_PAPERCLIP.md`, `task.md`, `docs/INDEX.md`,
`HANDOFF.md` y `docs/ORCHESTRATION.md`. No recuperes flujos legacy ni conviertas
`lead_quorum` en un modo de programación.

## Intención de producto

`lead_quorum` sirve exclusivamente para crear un plan robusto del objetivo
actual. El agente que el usuario haya configurado como `role:lead` conserva
ownership, relación con el usuario y decisión final, con independencia de su
proveedor, canal o modelo. Los demás modelos son seniors independientes que auditan Plan A,
argumentan sus desacuerdos y entregan informes al Lead. No son co-leads, no
dialogan entre ellos y no ejecutan código. Codex puede ser Lead o senior auditor,
igual que cualquier otro proveedor compatible; su marca nunca determina autoridad.

El flujo esperado es:

1. Congelar el objetivo vigente de la issue creada por el prompt del usuario.
2. El Lead configurado por el usuario produce Plan A profundo y accionable.
3. Congelar la revisión A.
4. Uno o dos seniors reciben exactamente el mismo objetivo y Plan A, sin ver los
   informes de otros auditores.
5. Cada senior entrega un informe profundo al Lead.
6. Cuando todos los informes válidos están disponibles, despertar al Lead.
7. El Lead lee todos los argumentos, acepta, matiza o descarta cada finding con
   justificación y publica Plan B.
8. La issue termina como `accepted_plan`; no comienza implementación.

## Cambios ya implementados

- Nuevo módulo `aiteam/quorum_quality.py` con contratos deterministas.
- Plan A y Plan B requieren al menos 300 palabras y cobertura explícita de:
  objetivo/alcance, estado actual, supuestos/restricciones, arquitectura y
  alternativas, fases/dependencias/owners, riesgos/rollback,
  verificación/evidencia, preguntas/escalado y continuación.
- Un Plan A superficial no abre auditorías: se registra
  `quorum.plan_depth_rejected` y se encola
  `quorum_plan_revision_required` para el Lead.
- Al iniciar la sesión se persiste en metadata un
  `quorum_objective_snapshot` con objetivo y revisión base.
- Los auditores reciben `quorum_review.objective` y el Plan A inmutable; no
  reciben contribuciones de otros seniors.
- Cada auditor debe emitir un bloque `---QUORUM-AUDIT---` JSON con:
  `executive_assessment`, `strengths`, `assumptions_challenged` y `findings`.
- Cada finding exige ID estable, severidad, summary, reasoning, justification,
  recommendation y tradeoffs. Argumentos superficiales no cuentan para el gate.
- El informe completo se conserva en la contribución; el contexto de síntesis
  entrega al Lead fortalezas, supuestos y findings, no una frase resumida.
- RBAC trata `quorum_auditor`/`quorum_senior` como roles no editores. Solo pueden
  comentar y cerrar su issue; no pueden escribir archivos, delegar, crear
  interacciones ni usar `accept_quorum_synthesis`.
- Dos seniors siguen siendo el objetivo canónico. Si el equipo aceptado contiene
  solo uno, `requested_contributions=1` y `min_valid_contributions=1`; el gate no
  exige diversidad imposible.
- La síntesis sigue siendo Lead-only. Plan B debe superar el mismo contrato de
  profundidad y cada finding necesita `accept|qualify|discard` con rationale de
  al menos 20 caracteres.
- Cada miembro usa el adapter/modelo que el usuario haya configurado en Equipo.
  Antigravity puede usar `Gemini 3.1 Pro (High)` y Codex subscription el modelo
  premium configurado, tanto como Lead como senior. `agy --print` aún no entrega
  usage comparable.

## Objetivos actuales

1. Validar el flujo nuevo con un canario capa 2 completo y cross-provider:
   Lead configurable + uno o dos seniors (incluido Codex si procede), Plan A,
   informes profundos, wakeup, Plan B y sesión accepted.
2. Añadir a la API/UI una señal clara de `reduced_quorum` cuando solo participe
   un senior; no presentarlo como equivalente a dos proveedores.
3. Repetir benchmarks en `sqlite_online_migration`,
   `multitenant_authorization` y `provider_failover`. Comparar Plan A/Plan B,
   hard gates, latencia, tokens, coste y regresiones.
4. Diseñar semántica explícita para un prompt que cambie materialmente el
   objetivo después del freeze: preferiblemente nueva issue/nueva sesión, nunca
   mutación silenciosa de una sesión terminal.
5. Mejorar telemetría de Antigravity cuando el CLI exponga usage. Hasta entonces,
   registrar `usage=None` y no inventar costes.

## Ideas para abordarlos

- Mantén invariantes en código y juicio abierto en LLM. No añadas más prosa al
  prompt si puede validarse estructuralmente.
- Para `reduced_quorum`, añade un campo derivado al endpoint existente sin romper
  su shape: `reduced_quorum = min_valid_contributions == 1` y muéstralo como
  advertencia, no error.
- Para cambios de objetivo, compara un hash del snapshot con la issue vigente.
  Si cambia antes de `accepted`, crea una nueva revisión Plan A y sesión; si la
  anterior es terminal, crea una nueva issue de planificación enlazada.
- Evalúa profundidad semántica con rúbricas ocultas; el gate de keywords solo
  protege estructura y no debe convertirse en juez de verdad.
- Conserva independencia: nunca pases informe A al auditor B antes de la
  síntesis del Lead.
- Un fallo de formato permite un único retry correctivo; después debe degradar y
  escalar durablemente, nunca quedar en reviewing sin wakeup.

## Verificación obligatoria

```powershell
.\scripts\pytest_local.bat tests -q --tb=short
Set-Location ide-frontend
npm run build
Set-Location ..
.\scripts\python_local.bat scripts\e2e_quorum_canary.py
.\scripts\python_local.bat scripts\audit_project_db.py <proyecto-capa-2>
```

Antes de declarar mejora, exige varias semillas. Una sesión accepted demuestra
operatividad; no demuestra que quorum mejore sistemáticamente el plan.

---
