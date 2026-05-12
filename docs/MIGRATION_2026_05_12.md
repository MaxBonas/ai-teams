# Migration Guide — 2026-05-12

## QA Tier 2 Role → test_runner Tier 3

### What changed

The `qa` Tier 2 role has been deprecated. The Reviewer already performs static QA
(logic verification, edge cases, error handling, dependency checks). A separate QA
agent running on the same static artifacts adds cost without value.

For **runtime test execution** (actually running pytest, npm test, etc.), use the new
`test_runner` Tier 3 specialist. It executes commands and reports stdout/exitcode without
making decisions — the Lead reads the output and decides whether to open a fix cycle.

### Role mapping

| Old role | New role | Notes |
|---|---|---|
| `qa` (static analysis) | `reviewer` | Already done by Reviewer — no action needed |
| `qa` (runtime tests) | `test_runner` | Tier 3 scout; execute commands, report output |

### Impact on existing databases

Existing agents with `role = 'qa'` in the database are **not automatically migrated**.
They will continue to run if assigned to issues. The system treats `qa` as an unknown
role with no Tier 3 restrictions, which may cause unexpected behavior.

To identify existing QA agents:

```sql
SELECT id, name, supervisor_agent_id FROM agents WHERE lower(role) = 'qa';
```

**Recommended action:**
- If the QA agent was doing static analysis → reassign its open issues to the Reviewer.
- If the QA agent was running actual test commands → change its role to `test_runner`
  and update its skill assignment.

### Skill changes

- `skills/qa.md` — **deleted**.
- `skills/test_runner.md` — **new**. See the skill file for the expected input format
  (`Commands:`, `Working directory:`) and output format (stdout/exitcode table).
- `skills/reviewer.md` — updated to explicitly own static QA.

### Code changes

| File | Change |
|---|---|
| `aiteam/run_profiles.py` | `requires_qa_gate` deprecated (always `False`); `qa` blueprint warns on use |
| `aiteam/heartbeat/executor.py` | Removed QA gate from `_all_children_done`; added `test_runner` to `_TIER3_ROLES` and `_WORKSPACE_READER_ROLES` |
| `aiteam/adapters/work_contract.py` | Role enum updated: `qa` → `test_runner` in op schema |
| `aiteam/project_adapters.py` | `JUNIOR_ROLES` updated: `qa` → `test_runner` |

### Feature flags

None. The change is effective immediately on upgrade.

### Rollback

If rollback is needed, restore the deleted files from git:

```sh
git checkout HEAD~1 -- skills/qa.md tests/test_qa_quality_gate.py
```

Note that `_all_children_done` would also need to be restored to re-enable the QA gate.
