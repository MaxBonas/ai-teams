# AI Teams Deep Implementation Plan

Updated: 2026-02-20
Status: In execution (Wave A done, Wave B done, Wave C started)

## 1) Objective

Move this project from a strong single-process orchestration baseline to a production-grade AI team system with:
- consistent role specialization,
- robust model/provider routing,
- deterministic operations,
- and Claude-style teammate coordination patterns (task decomposition, handoffs, and measurable quality loops).

Primary mission statement:
- The default objective is software engineering delivery (design, coding, review, QA) with the AI Team.
- Non-coding requests are supported as secondary work when explicitly requested.

## 2) Current Baseline (Validated)

- Tests: 193 passing.
- Strong modules: taskboard, mailbox, finops, compliance, observability, tool catalog.
- Gaps found:
  - Learning Registry CLI/docs/runtime mismatch.
  - Provider health not fully green in stage.
  - Role assignment too rigid (first enabled agent wins).
  - Sequential orchestration limits teammate parallelism.

## 3) Execution Strategy (4 Waves)

### Wave A - Consistency and correctness (Now)

Scope:
- Fix Learning Registry path/file handling.
- Align learning CLI UX with docs (support subcommand style).
- Align ingestion script with runtime + summary schema.
- Add tests for new CLI/ingestion behavior.

Acceptance:
- `aiteam learning list` works.
- `aiteam learning summary` works.
- `scripts/ingest_learnings.py` works with runtime dir and malformed legacy path states.
- Full test suite green.

### Wave B - Role-model governance

Scope:
- Define explicit role->capability->model matrix in config.
- Add weighted role scheduling (load/health/latency aware) instead of first-enabled assignment.
- Add provider diversity floor (avoid silent mono-provider behavior).
- Add role decision charters (decision rank, personality, mandatory peer listening, justification trace).

Acceptance:
- Routing decisions explainable per task (`why model X for role Y`).
- Reduced concentration on single provider under normal conditions.
- New tests for routing fairness and policy constraints.

### Wave C - Parallel teammate execution

Scope:
- Add bounded worker pool for independent ready tasks.
- Preserve quality gates and lock safety under concurrency.
- Add deterministic event ordering metadata for auditability.

Current progress:
- `AITEAM_MAX_PARALLEL_TASKS` introduced.
- Environment thresholds added (`AITEAM_MAX_PARALLEL_TASKS_STAGE`, `AITEAM_MAX_PARALLEL_TASKS_PROD`).
- Ready tasks now support bounded parallel execution.
- Deterministic metadata added per task execution (`execution_round`, `execution_order`).
- Dynamic parallel autotuning available (`AITEAM_PARALLEL_AUTOTUNE`) based on latency and failure rate.

Acceptance:
- Parallel rounds execute without lock collisions.
- Throughput improves over sequential baseline in synthetic workload.
- No regression in compliance gates.

### Wave D - Production operations and SLOs

Scope:
- Provider SLO dashboard (health, latency, fallback, spend by role/model).
- Incident runbooks linked to learning registry records.
- Progressive rollout by environment and team.

Acceptance:
- Stage `system-check --strict` consistently green.
- Actionable weekly reliability/cost report.
- Go-live checklist completed.

## 4) Delivery Cadence

- Wave A: 1-2 days
- Wave B: 3-5 days
- Wave C: 5-8 days
- Wave D: 3-4 days

Total: 2-3 weeks for full transition.

## 5) Risks and Mitigations

- Concurrency race risks -> keep file locks + add stress tests before rollout.
- Provider drift -> enforce policy assertions in tests + system-check gates.
- Documentation drift -> one command style, one schema source, doc CI checks.

## 6) Immediate Next Actions

1. Complete Wave A fixes (in progress).
2. Merge CLI/doc/schema alignment.
3. Run targeted + full tests.
4. Open Wave B implementation branch with routing matrix and weighted scheduler.

## 7) Progress Snapshot

- Wave A completed:
  - Learning Registry CLI/runtime/docs alignment done.
  - Legacy path handling and ingestion fixes done.
- Wave B implemented (partial):
  - Round-robin role assignment shipped.
  - Role-model/provider preferences shipped in router policy.
  - Groq API integrated as fallback provider.
  - Decision governance shipped:
    - role decision ranks,
    - role personalities,
    - peer consultation expansion,
    - decision justification records in task metadata/memory/events.
- Wave C implemented (phase 1):
  - bounded parallel processing with worker pool,
  - deterministic execution order telemetry,
  - dynamic parallel adjustment controls,
  - throughput benchmark script.
- Remaining for Wave B:
  - strict role-policy toggles by environment,
  - fairness and latency-aware weighting.
