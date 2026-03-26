# Quick Start: Execute Sprint 1-3 Hardening Plan

**Status**: Plan complete, ready for execution  
**Target Completion**: 24 days (3 sprints × 7-10 days each)  
**Success Metric**: 142+ tests passing, `system-check --strict` passes, zero regressions

---

## 📋 Quick Navigation

### Documents to Read First
1. **This file** (you are here) — overview + commands
2. **`docs/SPRINT_ROADMAP_Q1_2026.md`** — detailed sprint breakdown (tasks, effort, criteria)
3. **`docs/TEST_MATRIX_SPRINTS_1_2_3.md`** — exact test specs (50+ tests defined)

### Key Resources
- **Validation Script**: `scripts/validate_sprint_plan.py` (check plan is executable)
- **Updated Audit Doc**: `docs/DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` (Tier 1 summary + Tier 2/3 roadmap)
- **Implementation files**: `aiteam/*.py` (modules to extend)
- **Test files**: `tests/test_*.py` (new test files to create)

---

## 🚀 Quick Commands

### Before Starting: Validate Plan

```bash
# Check that plan is executable (reads/writes, imports work, baseline correct)
python scripts/validate_sprint_plan.py --verbose

# Expected output:
# ✓ Sprint plan validation PASSED. Ready to execute sprints.
```

### Sprint 1 Execution (Days 1-7)

**Goal**: Sync docs, add 12 tests for finops/execution/system-check.

```bash
# Run only Sprint 1 tests
python -m unittest discover -s tests -p "test_finops_anomaly test_execution_limits test_system_check_finops" -v

# Expected: 12/12 tests pass
```

**Files to Create**:
- `tests/test_finops_anomaly.py` (5 tests)
- `tests/test_execution_limits.py` (4 tests)
- `tests/test_system_check_finops.py` (3 tests)

**Files to Update**:
- `docs/DEEP_AUDIT_AND_IMPROVEMENTS_PHASE_2.md` (update metrics)
- `README.md` (update test count)

**Effort**: ~1 developer × 5-6 days

---

### Sprint 2 Execution (Days 8-17)

**Goal**: Implement time-windowed observability, configurable alerts, compliance audit, config validation.

```bash
# Run Sprint 2 tests
python -m unittest discover -s tests -p "test_observability_* test_compliance_audit test_config_validation" -v

# Expected: 19/19 tests pass
```

**Files to Create**:
- `aiteam/metrics.py` (MetricsAggregator class)
- `aiteam/audit_trail.py` (AuditTrail class)
- `aiteam/config_schema.py` (schema definitions)
- `tests/test_observability_metrics.py` (6 tests)
- `tests/test_observability_alerts.py` (4 tests)
- `tests/test_compliance_audit.py` (5 tests)
- `tests/test_config_validation.py` (4 tests)

**Files to Update**:
- `aiteam/observability.py` (add windowing + percentiles)
- `aiteam/compliance.py` (extend with audit logging)
- `aiteam/config.py` (add AlertPolicy + validation)
- `aiteam/cli.py` (validate config on startup)

**Effort**: ~2 developers × 7-9 days

---

### Sprint 3 Execution (Days 18-24)

**Goal**: Tool version pinning, retry/backoff, skills enrichment, integration/chaos tests.

```bash
# Run Sprint 3 tests
python -m unittest discover -s tests -p "test_tool_* test_integration_cli test_chaos" -v

# Expected: 19/19 tests pass
```

**Files to Create**:
- `aiteam/tool_lock.py` (ToolLockManager class)
- `tests/test_tool_pinning.py` (5 tests)
- `tests/test_tool_acquisition_retry.py` (4 tests)
- `tests/test_integration_cli.py` (6 tests)
- `tests/test_chaos.py` (4 tests)

**Files to Update**:
- `aiteam/autotools.py` (retry logic + lockfile support)
- `.cloud/skills/*.md` (all 8 skills, expand to 50+ lines each)

**Effort**: ~2 developers × 6-7 days

---

### Final Validation

```bash
# Full test suite
python -m unittest discover -s tests -p "test_*.py" -v 2>&1 | tail -5

# Expected:
# Ran 142+ tests in X.XXXs
# OK

# System check (should pass)
python -m aiteam.cli system-check --environment stage --strict

# Expected: "checks_passed" + "cost_anomaly=normal"
```

---

## 📊 Metrics Dashboard

### Current State (After Tier 1)
| Metric | Value | Target |
|--------|-------|--------|
| Tests | 91 | 91 ✓ |
| Coverage (core) | ~75% | 85% |
| Observability Dims | 4 | 7 |
| Compliance Audit | None | Full |
| Config Validation | None | Full |
| Tool Determinism | ~60% | 100% |
| Integration Tests | 0 | 6+ |

### After Sprint 1
| Metric | Value | Target |
|--------|-------|--------|
| Tests | 103 | 103+ ✓ |
| New Tests | 12 | 12 ✓ |
| Docs Synced | Yes | Yes ✓ |

### After Sprint 2
| Metric | Value | Target |
|--------|-------|--------|
| Tests | 122 | 122+ ✓ |
| New Tests | 31 | 31 ✓ |
| Observability Dims | 7 | 7 ✓ |
| Compliance Audit | Full | Full ✓ |
| Config Validation | Full | Full ✓ |

### After Sprint 3
| Metric | Value | Target |
|--------|-------|--------|
| Tests | 142+ | 142+ ✓ |
| New Tests | 50+ | 50+ ✓ |
| Tool Determinism | 100% | 100% ✓ |
| Integration Tests | 10 | 6+ ✓ |
| Skills Playbooks | Production | Production ✓ |
| **READY FOR PROD** | **YES** | **YES ✓** |

---

## ⚠️ Key Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Tests run slow (>30s) | Separate integration tests, fast suite default |
| Config breaks workflows | Backward compat mode, env override |
| Tool lockfile conflicts | Atomic writes, user-specific paths |
| Memory pressure | Sliding window, event archival |

---

## 🛠️ Development Workflow

### For Each Sprint:

1. **Start of Sprint** (Day N):
   - Read sprint section in `docs/SPRINT_ROADMAP_Q1_2026.md`
   - Read test specs in `docs/TEST_MATRIX_SPRINTS_1_2_3.md`
   - Create test files (copy signatures)

2. **During Sprint** (Days N-N+6):
   - Write implementation code to pass tests
   - Run tests frequently: `python -m unittest tests.test_X -v`
   - Commit working code (atomic commits, message: "feature: X for Tier Y")

3. **End of Sprint** (Day N+7):
   - Full test run: `python -m unittest discover -s tests -p "test_*.py" -v`
   - Document in sprint roadmap: mark tasks complete
   - Update metrics dashboard above

### Tools & Commands

```bash
# Individual test file
python -m unittest tests.test_finops_anomaly -v

# Test class
python -m unittest tests.test_finops_anomaly.PersistenceTests -v

# Single test
python -m unittest tests.test_finops_anomaly.PersistenceTests.test_detect_cost_anomaly_with_zscore_spike -v

# Full suite
python -m unittest discover -s tests -p "test_*.py" -v

# Coverage (install: pip install coverage)
coverage run -m unittest discover -s tests -p "test_*.py"
coverage report -m --include=aiteam --skip-empty

# Git workflow
git add aiteam/module.py tests/test_module.py
git commit -m "feature: implement X for Tier Y (Sprint Z.task_N)"
```

---

## ✅ Definition of Done (per task)

Each task is complete when:

1. ✅ All associated tests pass (`python -m unittest tests.test_X -v`)
2. ✅ No regressions (full suite still passes: `python -m unittest discover -s tests -p "test_*.py"`)
3. ✅ Code reviewed (follow style: type hints, docstrings, 80-char lines)
4. ✅ Committed to git with clear message
5. ✅ Updated `docs/SPRINT_ROADMAP_Q1_2026.md` with task status
6. ✅ Edge cases tested (per test specs in TEST_MATRIX)
7. ✅ Performance OK (tests run <30s for fast suite, <120s for full)

---

## 📞 Support & Questions

- **Plan unclear?** → Read `docs/SPRINT_ROADMAP_Q1_2026.md` (detailed breakdown)
- **Test spec unclear?** → Read `docs/TEST_MATRIX_SPRINTS_1_2_3.md` (exact signatures + assertions)
- **Validation issue?** → Run `python scripts/validate_sprint_plan.py --verbose`
- **Test fails?** → Check acceptance criteria in TEST_MATRIX for that test

---

## 🎯 Success Criteria at End of Day 24

```
✓ 142+ tests passing
✓ Zero regressions from baseline
✓ system-check --environment prod --strict PASSES
✓ All docs updated + consistent
✓ All 8 skills playbooks expanded (50+ lines each)
✓ Tier 1 + Tier 2 + Tier 3 hardening complete
✓ Ready for production deployment
```

---

**Ready? Start with Sprint 1: `docs/SPRINT_ROADMAP_Q1_2026.md` (Days 1-7)**
