# AI Team Project Audit and Hardening Plan

## Executive Assessment

This project has a strong orchestrator core and broad operational surface (providers, MCP, skills, snapshots), but it had reliability and governance blind spots in key runtime paths.

Primary risk themes found:

1. Runtime health checks could over-report readiness in non-dev environments.
2. Router policy thresholds were declared but not fully enforced.
3. Persistence readers were vulnerable to malformed file content.
4. Snapshot backups could include sensitive files by default.
5. Gemini provider health checks could use runtime invocation instead of auth-status semantics.

## Critical Weak Points (High Priority)

1. `aiteam/router.py`
   - API fallback could be skipped when no subscription adapters were eligible for low/medium tasks.
   - Configured API thresholds (`complexity_threshold_for_api`, `criticality_threshold_for_api`) were not used.

2. `aiteam/cli.py`
   - `system-check` did not enforce required provider health by environment (dev/stage/prod), allowing false positives.

3. `aiteam/taskboard.py`
   - Corrupted `tasks.json` could break taskboard boot due to strict JSON loading.

4. `aiteam/mailbox.py`
   - Corrupted JSONL lines could break message listing and downstream collaboration context.

5. `aiteam/snapshots.py`
   - Snapshot creation included sensitive files by default (for example `.env`) unless manually avoided.

6. `aiteam/cli.py` (`_gemini_health`)
   - Health check behavior depended on prompt invocation path, not explicit auth status.

## Medium Priority Improvements Needed

1. Introduce atomic write helpers for all JSON/JSONL critical paths.
2. Add bounded log retention and archival strategy for large runtime files.
3. Add richer provider SLO telemetry (latency/error budget) to `system-check`.
4. Add approval/audit chain metadata standards for sensitive operations.
5. Add structured schema validation for runtime config files.
6. Add integration tests for full provider-connect + system-check workflow.
7. Add staged rollout gates and environment-specific policy presets.

## Quick Wins (High Impact, Low Cost)

1. Harden parsers for malformed JSON/JSONL.
2. Enforce provider health threshold in stage/prod.
3. Prevent sensitive files from entering snapshots by default.
4. Ensure Gemini health checks use auth-state semantics.

## Deep Systemic Work (Next Iterations)

1. Transactional runtime persistence layer with atomic writes and checksums.
2. Policy engine extraction for reusable, testable governance controls.
3. Observability model upgrade (time-window metrics and trend analysis).
4. End-to-end chaos/failure simulation for handoff and provider failover.

## Applied in This Iteration

1. Router hardening (`aiteam/router.py`)
   - Implemented policy-aware threshold checks for API inclusion.
   - Fixed fallback behavior when only API adapters are eligible.

2. Provider governance hardening (`aiteam/cli.py`)
   - Added provider health aggregation helper.
   - `system-check` now enforces environment-specific required provider minimums:
     - dev: at least one required provider healthy,
     - stage/prod: all required providers healthy.

3. Gemini health hardening (`aiteam/cli.py`)
   - Added auth-status-based command generation for Gemini CLI.
   - Added environment key fast-path (`GEMINI_API_KEY` / `GOOGLE_API_KEY`).

4. Persistence resilience (`aiteam/taskboard.py`, `aiteam/mailbox.py`)
   - Added safe parsing and malformed record skipping.

5. Snapshot security hardening (`aiteam/snapshots.py`, `aiteam/cli.py`)
   - Excluded sensitive files by default.
   - Added explicit opt-in flag for snapshot creation:
     - `--snapshot-include-sensitive`.

6. Test suite expansion
   - Added coverage for routing thresholds/fallback edge cases.
   - Added parser resilience tests for mailbox/taskboard.
   - Added snapshot sensitivity behavior tests.
   - Added Gemini health/auth command and provider minimum tests.

## Validation Status

- Command run: `python -m unittest discover -s tests -p "test_*.py"`
- Result: `86 tests` passing.
