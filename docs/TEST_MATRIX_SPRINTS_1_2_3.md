# Test Matrix: Sprints 1-3 Detailed Test Specs

This document provides exact test signatures, assertions, and implementation guidance for all tests to be created across the 3 sprints.

**Format**: `Test File :: Test Class :: test_method_name` → Expected Behavior

---

## Sprint 1: Documentation Sync & Test Coverage (91 → 103+ tests)

### File: `tests/test_finops_anomaly.py` (NEW, 5 tests)

#### Test 1.1: `test_detect_cost_anomaly_with_zscore_spike`
**Purpose**: Verify 3-sigma spike detection with real historical data.

**Setup**:
- Create cost ledger with 7 days of data: days 1-6 = $0.10/day, day 7 = $1.00.
- Mean = $0.267, StdDev ≈ $0.30, Today's cost = $1.00, Z-score ≈ 2.44 (below 3σ threshold).
- Add one more entry to push today to $2.00, Z-score ≈ 5.78 (above 3σ).

**Assertion**:
```python
anomaly_detected, reason = budget_manager.detect_cost_anomaly()
assert anomaly_detected == True
assert "cost_spike_zscore_" in reason
assert float(reason.split("_")[-1]) > 3.0
```

**Expected Output**:
```
(True, "cost_spike_zscore_5.78")
```

---

#### Test 1.2: `test_detect_cost_anomaly_insufficient_history`
**Purpose**: Return "insufficient_history" when < 7 records exist.

**Setup**:
- Create cost ledger with 6 records (7-day minimum not met).

**Assertion**:
```python
anomaly_detected, reason = budget_manager.detect_cost_anomaly()
assert anomaly_detected == False
assert reason == "insufficient_history"
```

---

#### Test 1.3: `test_detect_cost_anomaly_insufficient_daily_data`
**Purpose**: Return "insufficient_daily_data" when < 2 unique dates in current month.

**Setup**:
- 10 records, all from today (only 1 unique date).

**Assertion**:
```python
anomaly_detected, reason = budget_manager.detect_cost_anomaly()
assert anomaly_detected == False
assert reason == "insufficient_daily_data"
```

---

#### Test 1.4: `test_detect_cost_anomaly_no_variance`
**Purpose**: Return "no_variance" when all daily costs are identical.

**Setup**:
- 7 records: each day $0.10 cost (StdDev = 0).

**Assertion**:
```python
anomaly_detected, reason = budget_manager.detect_cost_anomaly()
assert anomaly_detected == False
assert reason == "no_variance"
```

---

#### Test 1.5: `test_router_blocks_model_daily_cap_exceed`
**Purpose**: Router blocks API call when model daily cap exceeded.

**Setup**:
```python
budget_policy = BudgetPolicy(
    per_model_daily_cap_usd={"gpt-api": 0.05}
)
# Pre-fill ledger with today's cost for gpt-api = $0.06
```

**Assertion**:
```python
decision = router.route_and_invoke(request, prompt)
# Should not use gpt-api because cap exceeded
assert "model_cap_block:gpt-api" in " ".join(decision.attempts)
```

---

### File: `tests/test_execution_limits.py` (NEW, 4 tests)

#### Test 2.1: `test_output_limit_blocks_plan_on_exceed`
**Purpose**: ExecutionEngine stops plan when cumulative output > 10MB.

**Setup**:
- Create 2-step plan: step 1 outputs 6MB, step 2 outputs 6MB.

**Assertion**:
```python
results = execution_engine.execute_plan(task_id="T1", plan=plan)
# results[2] should be CommandResult with reason="output_limit_exceeded"
assert len(results) == 3
assert results[2].reason == "output_limit_exceeded"
assert results[2].exit_code == 1
```

---

#### Test 2.2: `test_command_result_tracks_bytes`
**Purpose**: CommandResult properly counts stdout + stderr bytes.

**Setup**:
```python
result = CommandResult(
    stdout="hello world",  # 11 bytes
    stderr="error",        # 5 bytes
)
```

**Assertion**:
```python
total_bytes = len(result.stdout.encode()) + len(result.stderr.encode())
assert total_bytes == 16
```

---

#### Test 2.3: `test_limit_customizable_via_policy`
**Purpose**: CommandPolicy max_output_bytes parameter is respected.

**Setup**:
```python
policy = CommandPolicy(max_output_bytes=1024)
executor = LocalCommandExecutor(policy=policy)
```

**Assertion**:
```python
assert executor.policy.max_output_bytes == 1024
```

---

#### Test 2.4: `test_output_truncation_message_includes_size`
**Purpose**: Truncation result message includes max bytes constant.

**Setup**:
- Plan that exceeds 10MB.

**Assertion**:
```python
results = execution_engine.execute_plan(task_id="T1", plan=plan)
truncation_result = results[-1]
assert "10485760" in truncation_result.stdout  # 10MB in bytes
```

---

### File: `tests/test_system_check_finops.py` (NEW, 3 tests)

#### Test 3.1: `test_system_check_includes_finops_section`
**Purpose**: system-check JSON report has `"finops"` key with cost_anomaly_detected and reason.

**Setup**:
- Run `cmd_system_check()` and capture report JSON.

**Assertion**:
```python
report = json.loads(report_path.read_text())
assert "finops" in report
assert "cost_anomaly_detected" in report["finops"]
assert "cost_anomaly_reason" in report["finops"]
```

---

#### Test 3.2: `test_system_check_cost_anomaly_check_fails_report`
**Purpose**: system-check includes cost_anomaly check in fails list if anomaly detected.

**Setup**:
- Manually inject anomaly condition (spike in ledger).

**Assertion**:
```python
report = json.loads(report_path.read_text())
failed_checks = report["failed_checks"]
# If anomaly, should have cost_anomaly check fail
if report["finops"]["cost_anomaly_detected"]:
    assert any("cost_anomaly=" in check for check in failed_checks)
```

---

#### Test 3.3: `test_system_check_finops_section_schema`
**Purpose**: finops section has correct schema (types, keys).

**Setup**:
- Run system-check normally.

**Assertion**:
```python
report = json.loads(report_path.read_text())
finops = report["finops"]
assert isinstance(finops["cost_anomaly_detected"], bool)
assert isinstance(finops["cost_anomaly_reason"], str)
assert len(finops.keys()) == 2  # exactly 2 keys
```

---

## Sprint 2: Observability, Compliance, Config (103 → 122+ tests)

### File: `tests/test_observability_metrics.py` (NEW, 6 tests)

#### Test 4.1: `test_recent_events_filters_by_hours`
**Purpose**: `recent_events(hours=2)` excludes events older than 2 hours.

**Setup**:
- Create 4 events: at -3h, -1h, -0.5h, now.

**Assertion**:
```python
recent = event_logger.recent_events(hours=2)
assert len(recent) == 3  # events at -1h, -0.5h, now
assert recent[0]["ts"] >= (now - timedelta(hours=2)).isoformat()
```

---

#### Test 4.2: `test_percentile_latency_p50`
**Purpose**: MetricsAggregator.percentile_latency(events, p=50) computes median.

**Setup**:
- 5 execution events with latencies: 100, 200, 300, 400, 500 ms.

**Assertion**:
```python
p50 = MetricsAggregator.percentile_latency(events, p=50)
assert p50 == 300  # median of 5 values
```

---

#### Test 4.3: `test_percentile_latency_p95`
**Purpose**: P95 latency correctly computed.

**Setup**:
- 20 events with latencies 1-1000 ms (uniform distribution).

**Assertion**:
```python
p95 = MetricsAggregator.percentile_latency(events, p=95)
assert 950 <= p95 <= 1000  # approximately 95th percentile
```

---

#### Test 4.4: `test_error_categorization_by_type`
**Purpose**: Errors bucketed by root cause (api_error, timeout, budget_block).

**Setup**:
- 10 events: 4 api_error, 3 timeout, 2 budget_block, 1 unknown.

**Assertion**:
```python
categorized = MetricsAggregator.error_categorization(events)
assert categorized["api_error"] == 4
assert categorized["timeout"] == 3
assert categorized["budget_block"] == 2
```

---

#### Test 4.5: `test_event_type_breakdown`
**Purpose**: Count events by type over window.

**Setup**:
- 20 events: 8 task_execution, 7 routing_decision, 5 execution_step.

**Assertion**:
```python
breakdown = MetricsAggregator.event_type_breakdown(events)
assert breakdown["task_execution"] == 8
assert breakdown["routing_decision"] == 7
assert breakdown["execution_step"] == 5
```

---

#### Test 4.6: `test_summary_respects_window_hours_parameter`
**Purpose**: `summary(window_hours=24)` uses windowed data, backward compat when None.

**Setup**:
- Old events + new events.

**Assertion**:
```python
summary_24h = event_logger.summary(window_hours=24)
summary_all = event_logger.summary(window_hours=None)
# All-time should have more or equal events than 24h window
assert summary_all["total_events"] >= summary_24h["total_events"]
```

---

### File: `tests/test_observability_alerts.py` (NEW, 4 tests)

#### Test 5.1: `test_alert_policy_applied_in_summary`
**Purpose**: AlertPolicy thresholds override hardcoded values.

**Setup**:
```python
policy = AlertPolicy(min_success_rate_percent=90.0)
event_logger = EventLogger(policy=policy)
# Add events with 85% success rate
```

**Assertion**:
```python
summary = event_logger.summary()
# Should have alert because 85% < 90% threshold
assert any("low_task_execution_success_rate:85" in alert for alert in summary["alerts"])
```

---

#### Test 5.2: `test_default_alert_policy_fallback`
**Purpose**: When AlertPolicy=None, use defaults (85%, 40%, etc.).

**Setup**:
```python
event_logger = EventLogger(alert_policy=None)
```

**Assertion**:
```python
# Fallback to 85% success threshold
# If success_rate < 85%, alert triggered
```

---

#### Test 5.3: `test_custom_alert_thresholds_override`
**Purpose**: Custom thresholds in AlertPolicy override defaults.

**Setup**:
```python
policy = AlertPolicy(
    min_success_rate_percent=95.0,
    max_api_dependency_percent=30.0
)
```

**Assertion**:
```python
# Thresholds applied in summary() calculations
assert event_logger.policy.min_success_rate_percent == 95.0
```

---

#### Test 5.4: `test_alert_policy_validation_rejects_invalid`
**Purpose**: AlertPolicy validation rejects invalid thresholds (negative, >100).

**Setup**:
```python
try:
    policy = AlertPolicy(min_success_rate_percent=-5.0)
    assert False, "Should raise ValidationError"
except ValueError:
    pass  # expected
```

---

### File: `tests/test_compliance_audit.py` (NEW, 5 tests)

#### Test 6.1: `test_audit_trail_records_approval_granted`
**Purpose**: AuditTrail logs approval decisions with timestamp + approver.

**Setup**:
```python
audit_trail = AuditTrail(runtime_dir=Path(tmp))
audit_trail.record_decision(
    decision_type="approval_granted",
    task_id="T123",
    approver_id="lead-1",
    reason="Sensitive operation approved",
    metadata={"commands": ["publish"], "count": 1}
)
```

**Assertion**:
```python
records = audit_trail.read_all()
assert len(records) == 1
assert records[0]["decision_type"] == "approval_granted"
assert records[0]["approver_id"] == "lead-1"
assert "ts" in records[0]  # ISO timestamp
```

---

#### Test 6.2: `test_audit_trail_records_approval_denied`
**Purpose**: Denied approvals logged with reason.

**Setup**:
```python
audit_trail.record_decision(
    decision_type="approval_denied",
    task_id="T124",
    approver_id="security-1",
    reason="Insufficient approvers (required 2, got 1)"
)
```

**Assertion**:
```python
records = audit_trail.read_all()
assert records[-1]["decision_type"] == "approval_denied"
assert "approvers" in records[-1]["reason"]
```

---

#### Test 6.3: `test_audit_trail_includes_rule_applied`
**Purpose**: Audit record includes which compliance rule triggered.

**Setup**:
```python
audit_trail.record_decision(
    decision_type="command_blocked",
    task_id="T125",
    approver_id="system",
    reason="Blocked by pattern matching",
    metadata={"rule": "playstore_publish", "pattern": r"playstore|google\s+play"}
)
```

**Assertion**:
```python
records = audit_trail.read_all()
assert records[-1]["metadata"]["rule"] == "playstore_publish"
```

---

#### Test 6.4: `test_audit_trail_read_window_by_date`
**Purpose**: Query audit records by date range.

**Setup**:
- Create records on day 1, day 2, day 3.

**Assertion**:
```python
day2_records = audit_trail.read_by_date_range(start_date="2026-02-20", end_date="2026-02-20")
# Should only include day 2 records
```

---

#### Test 6.5: `test_audit_trail_dedup_on_load`
**Purpose**: Checksums prevent duplicate audit entries.

**Setup**:
- Manually append same audit record twice to ledger.

**Assertion**:
```python
records = audit_trail.read_all()
# Dedup should keep only 1 copy
assert len(records) == 1
```

---

### File: `tests/test_config_validation.py` (NEW, 4 tests)

#### Test 7.1: `test_routing_policy_valid_schema`
**Purpose**: Valid RouterPolicy config passes validation.

**Setup**:
```python
config_file = Path(tmp) / "routing_policy.json"
config_file.write_text(json.dumps({
    "pro_first": True,
    "max_subscription_attempts": 3,
    "max_api_attempts": 2,
    "daily_api_budget_usd": 10.0,
    "monthly_api_budget_usd": 200.0
}))
```

**Assertion**:
```python
valid, error = validate_config(config_file, schema=ROUTING_POLICY_SCHEMA)
assert valid == True
assert error == ""
```

---

#### Test 7.2: `test_routing_policy_invalid_schema_rejected`
**Purpose**: Missing required field → validation error.

**Setup**:
```python
# Missing "pro_first" required field
config_file.write_text(json.dumps({
    "max_api_attempts": 2
}))
```

**Assertion**:
```python
valid, error = validate_config(config_file, schema=ROUTING_POLICY_SCHEMA)
assert valid == False
assert "pro_first" in error  # error message mentions missing field
```

---

#### Test 7.3: `test_tool_catalog_validation`
**Purpose**: Tool entry schema enforced.

**Setup**:
```python
catalog = {"tools": [{
    "name": "github_mcp",
    "category": "mcp",
    "source_type": "npm",
    "source": "@modelcontextprotocol/server-github",
    "capabilities": ["github", "pr_management"]
    # required fields
}]}
```

**Assertion**:
```python
valid, error = validate_config_dict(catalog, schema=TOOL_CATALOG_SCHEMA)
assert valid == True
```

---

#### Test 7.4: `test_schema_error_message_helpful`
**Purpose**: Validation errors include field name + expected type.

**Setup**:
```python
# Wrong type: "pro_first" is string instead of bool
config_file.write_text(json.dumps({
    "pro_first": "yes",  # should be boolean
    "max_api_attempts": 2
}))
```

**Assertion**:
```python
valid, error = validate_config(config_file, schema=ROUTING_POLICY_SCHEMA)
assert valid == False
assert "pro_first" in error
assert "boolean" in error or "bool" in error
```

---

## Sprint 3: Tool Integration, Skills, Integration Tests (122 → 142+ tests)

### File: `tests/test_tool_pinning.py` (NEW, 5 tests)

#### Test 8.1: `test_tool_lock_created_after_acquire`
**Purpose**: After tool acquisition, `runtime/tool_lock.json` exists with pinned versions.

**Setup**:
```python
integrator = AutoToolIntegrator(runtime_dir=Path(tmp))
integrator.integrate_from_metadata(
    task_id="T1",
    metadata={"tool_requirements": [{"name": "github_mcp"}]},
    internet_allowed=True
)
```

**Assertion**:
```python
lock_file = Path(tmp) / "tool_lock.json"
assert lock_file.exists()
lock = json.loads(lock_file.read_text())
assert "github_mcp" in lock
assert "version" in lock["github_mcp"]
assert "checksum" in lock["github_mcp"]
```

---

#### Test 8.2: `test_tool_lock_respects_pinned_version`
**Purpose**: If lock specifies v2.0, don't upgrade to v2.5.

**Setup**:
- Pre-create lock with `github_mcp: {version: "2.0.0"}`.
- Mock npm latest version = "2.5.0".

**Assertion**:
```python
integrator = AutoToolIntegrator(runtime_dir=Path(tmp))
integrator.integrate_from_metadata(...)
# Should acquire v2.0.0, not v2.5.0
acquired_version = integrator._tool_registry.get("github_mcp", {}).get("version")
assert acquired_version == "2.0.0"
```

---

#### Test 8.3: `test_tool_lock_integrity_check_detects_tampering`
**Purpose**: Modifying lock file (changing version without updating checksum) detected.

**Setup**:
- Create valid lock with checksum.
- Manually modify version in lock file (corrupt checksum).

**Assertion**:
```python
is_valid, error = tool_lock_manager.verify_lock_integrity()
assert is_valid == False
assert "checksum" in error
```

---

#### Test 8.4: `test_tool_lock_missing_falls_back_to_latest`
**Purpose**: No lock file → acquire latest version, create lock.

**Setup**:
- No `tool_lock.json` exists.

**Assertion**:
```python
integrator = AutoToolIntegrator(runtime_dir=Path(tmp))
integrator.integrate_from_metadata(...)
# Should acquire latest (mocked as "2.5.0")
lock_file = Path(tmp) / "tool_lock.json"
assert lock_file.exists()
lock = json.loads(lock_file.read_text())
assert lock["github_mcp"]["version"] == "2.5.0"
```

---

#### Test 8.5: `test_tool_lock_restored_on_reload`
**Purpose**: Lock persists across CLI restarts; same tool = same version.

**Setup**:
- First run: acquire tool, create lock.
- Second run: reload integrator.

**Assertion**:
```python
first_run = integrator1.integrate_from_metadata(...)
first_version = integrator1._tool_registry["github_mcp"]["version"]

integrator2 = AutoToolIntegrator(runtime_dir=Path(tmp))
second_run = integrator2.integrate_from_metadata(...)
second_version = integrator2._tool_registry["github_mcp"]["version"]

assert first_version == second_version
```

---

### File: `tests/test_tool_acquisition_retry.py` (NEW, 4 tests)

#### Test 9.1: `test_acquire_retries_on_timeout`
**Purpose**: Tool acquisition retries on timeout (1st → 2nd succeeds).

**Setup**:
- Mock npm package: fails on attempt 1 (timeout), succeeds on attempt 2.

**Assertion**:
```python
result = integrator._acquire_tool({"name": "github_mcp", "required": True})
assert result == (True, "acquired_github_mcp")
# Should have logged "attempt 2/3" in registry
```

---

#### Test 9.2: `test_acquire_exponential_backoff_timing`
**Purpose**: Retry backoff follows 1s, 2s, 4s pattern (logged).

**Setup**:
- Mock npm: 3 timeouts.

**Assertion**:
```python
with patch("time.sleep") as mock_sleep:
    result = integrator._acquire_tool(...)
    # 3 retries → 3 sleep calls: 1s, 2s, 4s
    assert mock_sleep.call_count == 3
    calls = mock_sleep.call_args_list
    assert calls[0][0][0] == 1
    assert calls[1][0][0] == 2
    assert calls[2][0][0] == 4
```

---

#### Test 9.3: `test_acquire_fails_auth_without_retry`
**Purpose**: Auth errors (non-transient) → fail immediately, no retry.

**Setup**:
- Mock npm: returns 403 Forbidden (auth error).

**Assertion**:
```python
result = integrator._acquire_tool({"name": "github_mcp", "required": True})
assert result == (False, "auth_required:github_mcp")
# Should NOT have retried
```

---

#### Test 9.4: `test_acquire_max_retries_exceeded_auto_disables`
**Purpose**: After 3 failed retries, tool auto-disabled and marked in registry.

**Setup**:
- Mock npm: all 3 attempts timeout.

**Assertion**:
```python
result = integrator._acquire_tool({"name": "github_mcp", "required": False})
assert result == (False, "max_retries_exceeded:github_mcp")
# Tool should be marked disabled
registry_entry = integrator._tool_registry.get("github_mcp", {})
assert registry_entry.get("enabled") == False
```

---

### File: `tests/test_integration_cli.py` (NEW, 6 integration tests)

#### Test 10.1: `test_integration_init_to_demo_to_status`
**Purpose**: Full CLI flow: init → demo → status.

**Setup**:
```bash
runtime_dir = Path(tmp) / "integration1"
# Simulate: aiteam init && aiteam demo && aiteam status
```

**Assertion**:
```python
# init: creates runtime/ structure
assert (runtime_dir / "adapters.json").exists()

# demo: runs tasks
output = run_cli_command("demo", runtime_dir=runtime_dir)
assert "task_execution_success_rate" in output

# status: reads state
output = run_cli_command("status", runtime_dir=runtime_dir)
assert "Task Summary" in output
```

---

#### Test 10.2: `test_integration_provider_connect_to_system_check`
**Purpose**: Provider connect → doctor → system-check flow.

**Setup**:
```python
# provider-connect: discover/connect providers
# provider-doctor: health check
# system-check: full validation
```

**Assertion**:
```python
output1 = run_cli_command("provider-connect", runtime_dir=runtime_dir)
assert "provider_accounts.json" in output1 or Path(.../"provider_accounts.json").exists()

output2 = run_cli_command("provider-doctor", runtime_dir=runtime_dir)
# Expect at least 1 provider healthy or degraded with reason

output3 = run_cli_command("system-check --strict", runtime_dir=runtime_dir)
# Should pass or list specific failures
```

---

#### Test 10.3: `test_integration_tool_sync_to_mcp_doctor`
**Purpose**: Tool sync with pro profile → MCP doctor validation.

**Setup**:
```python
# tool-sync: acquire tools from config/tool_requests.pro.json
# mcp-doctor: validate MCP health
# skills-coverage: measure skill usage
```

**Assertion**:
```python
output1 = run_cli_command(
    "tool-sync --tool-request-file config/tool_requests.pro.json",
    runtime_dir=runtime_dir
)
assert "tool_lock.json" in output1 or Path(.../"tool_lock.json").exists()

output2 = run_cli_command("mcp-doctor", runtime_dir=runtime_dir)
assert ("healthy" in output2 or "auto_disabled" in output2)

output3 = run_cli_command("skills-coverage", runtime_dir=runtime_dir)
assert "coverage_percent" in output3
```

---

#### Test 10.4: `test_integration_plan_to_pilot_check`
**Purpose**: Plan task → run → pilot-check (success flow).

**Setup**:
```python
# plan: create task plan
# run: execute N rounds
# pilot-check: validate success metrics
```

**Assertion**:
```python
output1 = run_cli_command("plan --epic-id EPIC-001", runtime_dir=runtime_dir)
assert "tasks" in output1

output2 = run_cli_command("run --rounds 3", runtime_dir=runtime_dir)
assert "Round 1" in output2

output3 = run_cli_command("pilot-check", runtime_dir=runtime_dir)
assert "pass" in output3.lower() or "success_rate" in output3
```

---

#### Test 10.5: `test_integration_compliance_sensitive_approval_flow`
**Purpose**: Create sensitive task → compliance blocks → approve → runs.

**Setup**:
```python
# Create task with sensitive command (publish, etc.)
# Without approval: blocked by compliance
# With approval: runs successfully
```

**Assertion**:
```python
# Task without approval → compliance_violation event
output1 = run_cli_command("run --rounds 1", runtime_dir=runtime_dir)
# Should see compliance violation or blocked message

# Approve task
approve_task(task_id="T_sensitive", approved_by=["lead-1", "security-1"])

# Task with approval → runs
output2 = run_cli_command("run --rounds 1", runtime_dir=runtime_dir)
assert "compliance_violation" not in output2 or "approved" in output2
```

---

#### Test 10.6: `test_integration_snapshot_restore_workflow`
**Purpose**: Snapshot create → modify → restore flow.

**Setup**:
```python
# snapshot-create: save current state
# (modify some files)
# snapshot-restore: revert to saved state
```

**Assertion**:
```python
output1 = run_cli_command("snapshot-create --snapshot-label test1", runtime_dir=runtime_dir)
snapshot_id = extract_snapshot_id(output1)

# Modify a file
(runtime_dir / "tasks.json").write_text("modified")

output2 = run_cli_command(f"snapshot-restore --snapshot-id {snapshot_id}", runtime_dir=runtime_dir)
# File should be restored to original content
```

---

### File: `tests/test_chaos.py` (NEW, 4 chaos tests)

#### Test 11.1: `test_chaos_corrupted_ledger_auto_recovers`
**Purpose**: Corrupted cost_ledger.jsonl → CLI still starts, dedup recovers.

**Setup**:
```python
# Create valid ledger with 5 entries, then corrupt entry 3
ledger_path.write_text(
    '{"ts":"...", "cost": 0.1}\n'
    '{"ts":"...", "cost": 0.1}\n'
    '{broken json line here\n'
    '{"ts":"...", "cost": 0.1}\n'
)
```

**Assertion**:
```python
budget_manager = BudgetManager(runtime_dir=Path(tmp), policy=BudgetPolicy())
records = budget_manager._records()
# Should have 3 records (skipped broken line)
assert len(records) == 3
```

---

#### Test 11.2: `test_chaos_provider_timeout_fallback_works`
**Purpose**: Provider timeout → router tries next adapter successfully.

**Setup**:
```python
# Mock subscription adapter to timeout
# Mock API adapter to succeed
```

**Assertion**:
```python
decision = router.route_and_invoke(request, prompt)
assert decision.success == True
assert decision.channel.value == "api"  # Fell back to API
assert "timeout" in " ".join(decision.attempts)
```

---

#### Test 11.3: `test_chaos_tool_acquisition_failure_auto_disables`
**Purpose**: Tool npm package missing → auto-disable, task continues.

**Setup**:
```python
# Mock npm to return 404 for tool
# Tool marked as optional (required=False)
```

**Assertion**:
```python
result = integrator.integrate_from_metadata(
    task_id="T1",
    metadata={"tool_requirements": [{"name": "missing_tool", "required": False}]},
    internet_allowed=True
)
assert result.success == True  # Task continues
# Tool should be in registry with enabled=False
```

---

#### Test 11.4: `test_chaos_budget_exceeded_blocks_api`
**Purpose**: Budget exhausted → API blocked, Pro used as fallback.

**Setup**:
```python
budget_policy = BudgetPolicy(daily_api_budget_usd=0.0)
# Set budget to 0
```

**Assertion**:
```python
budget_manager = BudgetManager(..., policy=budget_policy)
signal = budget_manager.api_signal()
assert signal.can_use_api == False
assert signal.max_api_cost_tier == 0

# Router should block API
router = HybridRouter(..., budget_manager=budget_manager)
# All attempts should be subscription, no API tried
```

---

## Test Coverage Summary by File

| Test File | # Tests | Focus | Sprint |
|-----------|---------|-------|--------|
| test_finops_anomaly.py | 5 | Anomaly detection, model caps | 1 |
| test_execution_limits.py | 4 | Output size limits | 1 |
| test_system_check_finops.py | 3 | System-check finops reporting | 1 |
| test_observability_metrics.py | 6 | Time windows, percentiles | 2 |
| test_observability_alerts.py | 4 | Configurable thresholds | 2 |
| test_compliance_audit.py | 5 | Audit trail, timestamps | 2 |
| test_config_validation.py | 4 | Schema validation | 2 |
| test_tool_pinning.py | 5 | Version pinning, lockfile | 3 |
| test_tool_acquisition_retry.py | 4 | Retry, backoff, auth errors | 3 |
| test_integration_cli.py | 6 | End-to-end workflows | 3 |
| test_chaos.py | 4 | Failure scenarios | 3 |
| **TOTAL** | **50** | **All new tests** | **1-3** |

**Final Test Count**: 91 (baseline) + 50 (new) = **141 tests** (target: 142+).

---

## Next Steps for Sprint Execution

1. Create each test file per Sprint timeline.
2. Run tests independently to validate each sprint:
   ```bash
   # Sprint 1
   python -m unittest discover -s tests -p "test_finops_anomaly.py test_execution_limits.py test_system_check_finops.py" -v
   
   # Sprint 2
   python -m unittest discover -s tests -p "test_observability_*.py test_compliance_audit.py test_config_validation.py" -v
   
   # Sprint 3
   python -m unittest discover -s tests -p "test_tool_*.py test_integration_cli.py test_chaos.py" -v
   ```
3. Full suite check:
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v 2>&1 | tail -5
   ```

