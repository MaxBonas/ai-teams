# AI Team Documentation Index

**Last Updated**: 2026-02-20  
**Current Phase**: Tier 1 Complete, Tier 2-3 Planning Done  
**Status**: Ready for Sprint Execution

---

## 🎯 Quick Links by Purpose

### 👤 "I want to understand the project"
1. **README.md** — Start here! Project overview, quick commands, setup.
2. **docs/ARCHITECTURE.md** — Components, flow, tech stack.
3. **docs/INTERNAL_QUALITIES_ROADMAP.md** — Product vision + qualities.

### 🔍 "I want to know what's been done"
1. **docs/PROJECT_AUDIT_AND_HARDENING_PLAN.md** — Round 1 quick wins (done).
2. **docs/DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md** — Full 8-D audit + Tier 1 summary.

### 🚀 "I want to execute the next 3 sprints"
1. **docs/EXECUTION_QUICK_START.md** ⭐ **START HERE** — Commands + timeline overview.
2. **docs/SPRINT_ROADMAP_Q1_2026.md** — Detailed 3-sprint plan (tasks, effort, criteria).
3. **docs/TEST_MATRIX_SPRINTS_1_2_3.md** — 50+ test specifications (exact code to write).
4. **scripts/validate_sprint_plan.py** — Validate plan is executable before starting.

### 🛠️ "I want to integrate my tools or run in production"
1. **docs/INTEGRATION_GUIDE.md** — Connect external adapters, compliance controls.
2. **docs/LLM_CONNECTION_SYSTEM.md** — Configure LLM providers (Pro + API).
3. **docs/PRODUCTION_ROLLOUT_RUNBOOK.md** — Phased deployment, incident response.
4. **docs/SECURITY_COMPLIANCE.md** — Approval gates, redaction, audit rules.

### 🧠 "I want to understand the roadmap"
1. **docs/TASKS_AI_TEAM.md** — 19-point implementation checklist (mostly done).
2. **docs/MCP_CLI_SKILLS_ROADMAP.md** — Tool integration strategy (Phase 1-5).
3. **docs/EXTERNAL_TOOLS_INVENTORY.md** — Secondary tools available for integration.

### 🛡️ "I want to see what was fixed recently"
1. **docs/PROJECT_AUDIT_AND_HARDENING_PLAN.md** (Applied section) — Round 1 fixes.
2. **docs/DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md** (Applied section) — Tier 1 fixes.

---

## 📚 Document Map (by Topic)

### Architecture & Design
| Document | Purpose | Audience |
|----------|---------|----------|
| `ARCHITECTURE.md` | System components, flow | Architects, new team members |
| `INTERNAL_QUALITIES_ROADMAP.md` | Product vision, qualities | Product leads, architects |
| `TASKS_AI_TEAM.md` | 19-point build plan | Project managers, engineers |

### Security & Operations
| Document | Purpose | Audience |
|----------|---------|----------|
| `SECURITY_COMPLIANCE.md` | Approval gates, redaction | Security, compliance teams |
| `PRODUCTION_ROLLOUT_RUNBOOK.md` | Deployment, incidents | DevOps, SREs |
| `LLM_CONNECTION_SYSTEM.md` | Provider setup | Engineers, DevOps |

### Integration & Tools
| Document | Purpose | Audience |
|----------|---------|----------|
| `INTEGRATION_GUIDE.md` | External adapters, tools | Engineers, integrators |
| `MCP_CLI_SKILLS_ROADMAP.md` | Tool strategy + phases | Architects, tool engineers |
| `EXTERNAL_TOOLS_INVENTORY.md` | Available secondary tools | Engineers |

### Audit & Hardening
| Document | Purpose | Audience |
|----------|---------|----------|
| `PROJECT_AUDIT_AND_HARDENING_PLAN.md` | Round 1 audit + fixes | Architects, QA |
| `DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` | 8-D audit, Tier 1-3 plan | Architects, engineers |

### Execution & Planning
| Document | Purpose | Audience |
|----------|---------|----------|
| `EXECUTION_QUICK_START.md` ⭐ | Sprint execution entry point | **All engineers** |
| `SPRINT_ROADMAP_Q1_2026.md` ⭐ | Detailed 3-sprint plan | **Sprint leads, engineers** |
| `TEST_MATRIX_SPRINTS_1_2_3.md` ⭐ | Test specifications | **QA, engineers writing tests** |

⭐ = Essential for next phase (Sprint 1-3)

---

## 🎓 Learning Paths by Role

### For a New Team Member
1. **Day 1**: `README.md` → project overview
2. **Day 2**: `ARCHITECTURE.md` → system design
3. **Day 3**: `INTERNAL_QUALITIES_ROADMAP.md` → product goals
4. **Day 4**: `INTEGRATION_GUIDE.md` → how to extend the system
5. **Day 5**: Pick a Sprint 1 task from `EXECUTION_QUICK_START.md` and start coding

### For an Architect
1. `ARCHITECTURE.md` — current state
2. `INTERNAL_QUALITIES_ROADMAP.md` — vision vs. reality
3. `DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` — 8-D analysis
4. `SPRINT_ROADMAP_Q1_2026.md` — next 24 days

### For a DevOps/SRE
1. `LLM_CONNECTION_SYSTEM.md` — provider setup
2. `PRODUCTION_ROLLOUT_RUNBOOK.md` — deployment
3. `SECURITY_COMPLIANCE.md` — compliance controls
4. `EXECUTION_QUICK_START.md` — validation commands

### For QA/Testing
1. `EXECUTION_QUICK_START.md` — sprint overview
2. `TEST_MATRIX_SPRINTS_1_2_3.md` — test specifications
3. `scripts/validate_sprint_plan.py` — validation script
4. Run full test suite (Sprint 1, 2, 3)

### For an Integrator (External Tools)
1. `INTEGRATION_GUIDE.md` — how to connect tools
2. `EXTERNAL_TOOLS_INVENTORY.md` — available tools
3. `MCP_CLI_SKILLS_ROADMAP.md` — tool phases
4. `LLM_CONNECTION_SYSTEM.md` — provider integration

---

## 📊 Current State Summary

### Tier 1 — COMPLETED ✅ (2026-02-20)
- **Atomic persistence** layer: `aiteam/persistence.py` (atomic writes, dedup, checksums).
- **Finops anomaly detection**: `detect_cost_anomaly()` with z-score, per-model caps.
- **Execution output limits**: 10MB cap on plan output, prevents OOM.
- **System-check finops**: Cost anomalies reported + blocked checks.
- **Test count**: 91 passing (0 regressions).

**Files**: `aiteam/persistence.py` (new), `aiteam/finops.py`, `aiteam/router.py`, `aiteam/execution.py`, `aiteam/observability.py`, `tests/test_persistence.py` (new).

### Tier 2 — PLANNED 🔄 (Sprint 2, Days 8-17)
- **Time-windowed metrics**: p50/p95/p99, error categorization, 5m/1h/24h buckets.
- **Configurable alerts**: AlertPolicy class, thresholds from config.
- **Compliance audit trail**: AuditTrail with timestamps, approver ID, rule applied.
- **Config validation**: JSON schemas, unified loader, CLI enforcement.
- **Test count target**: 122+ (31 new tests).

**Files**: `aiteam/metrics.py` (new), `aiteam/audit_trail.py` (new), `aiteam/config_schema.py` (new), updated modules + test files.

### Tier 3 — PLANNED 🔄 (Sprint 3, Days 18-24)
- **Tool version pinning**: ToolLockManager, `runtime/tool_lock.json`, deterministic acquisition.
- **Tool retry/backoff**: Exponential backoff (1s, 2s, 4s), auth fail-fast.
- **Skills playbooks**: 8 skills expanded to 50+ lines each (procedures, guardrails, examples).
- **Integration tests**: 6 end-to-end CLI workflows.
- **Chaos tests**: 4 failure scenarios (corrupted ledger, timeouts, tool failures, budget blocks).
- **Test count target**: 142+ (50+ new tests, all passing).

**Files**: `aiteam/tool_lock.py` (new), updated `aiteam/autotools.py`, 8 `skill.md` refactored, 11 test files (new).

---

## 🚦 Status Dashboard

| Item | Status | Last Updated |
|------|--------|--------------|
| Tier 1 Hardening | ✅ COMPLETE | 2026-02-20 |
| Tier 2 Plan | ✅ READY | 2026-02-20 |
| Tier 3 Plan | ✅ READY | 2026-02-20 |
| Sprint 1 Tasks | 📋 DEFINED | docs/SPRINT_ROADMAP_Q1_2026.md |
| Sprint 2 Tasks | 📋 DEFINED | docs/SPRINT_ROADMAP_Q1_2026.md |
| Sprint 3 Tasks | 📋 DEFINED | docs/SPRINT_ROADMAP_Q1_2026.md |
| Test Specs | ✅ 50+ WRITTEN | docs/TEST_MATRIX_SPRINTS_1_2_3.md |
| Validation Script | ✅ READY | scripts/validate_sprint_plan.py |
| Execution Guide | ✅ READY | docs/EXECUTION_QUICK_START.md |

---

## 📖 How to Use This Index

### To Find Something Specific
1. Use **"Quick Links by Purpose"** section above (find your goal).
2. Click the recommended document.
3. If unclear, check **"Document Map"** to see alternatives.

### To Understand Progression
1. **Current** → `DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` (Tier 1 summary).
2. **Next** → `EXECUTION_QUICK_START.md` (Sprint 1-3 overview).
3. **Details** → `SPRINT_ROADMAP_Q1_2026.md` + `TEST_MATRIX_SPRINTS_1_2_3.md`.

### To Jump Into Work
1. **START HERE** → `docs/EXECUTION_QUICK_START.md`
2. Pick a sprint (1, 2, or 3)
3. Read tasks in `docs/SPRINT_ROADMAP_Q1_2026.md`
4. Read test specs in `docs/TEST_MATRIX_SPRINTS_1_2_3.md`
5. Write code to pass tests

---

## 🔗 Related Files (Not Markdown)

| File | Purpose |
|------|---------|
| `scripts/validate_sprint_plan.py` | Validate plan before execution |
| `aiteam/persistence.py` | Atomic write layer (Tier 1) |
| `aiteam/finops.py` | Budget + anomaly detection (Tier 1) |
| `tests/test_*.py` | All test files (91 current + 50+ planned) |
| `.cloud/skills/*.md` | Skills playbooks (8 files, to expand) |
| `config/*.json` | Tool catalogs, routing policies |

---

## ✨ Next Action

**👉 To execute Sprint 1-3: Go to `docs/EXECUTION_QUICK_START.md`**

---

*This index is maintained as the central hub for all AI Team documentation. Update this file when adding new docs or changing structure.*
