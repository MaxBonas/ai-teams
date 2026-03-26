# NotebookLM Ingestion & Automation Playbook
## How to Feed Data Into NotebookLM (Daily, Weekly, On-Demand)

**Purpose**: Ensure NotebookLM notebooks stay fresh and useful  
**Owners**: DevOps + Documentation Lead  
**Frequency**: Daily (auto) + Weekly (manual) + On-demand (event-triggered)

---

## Daily Automated Ingestion (Morning Ritual)

### ✅ Task 1: System Health Report (9:00 AM UTC)

**What to do**:
1. Run system check:
   ```bash
   aiteam system-check --strict --output json > /tmp/daily_check.json
   ```

2. Extract key metrics:
   ```json
   {
     "timestamp": "2026-02-20T09:00:00Z",
     "status": "healthy" | "degraded" | "unhealthy",
     "metrics": {
       "tests_passing": 176,
       "tests_failing": 0,
       "cost_anomaly_detected": false,
       "latency_p99_ms": 850,
       "uptime_percent": 99.97
     },
     "alerts": []
   }
   ```

3. **Upload to NotebookLM**: Operations & Incidents notebook
   - Copy JSON into notebook as "Daily Report: 2026-02-20"
   - NotebookLM will parse and store

**Script** (save as `scripts/ingest_daily_health.sh`):
```bash
#!/bin/bash
# Daily health ingestion

TIMESTAMP=$(date -u +"%Y-%m-%d")

# Run system check
python -m aiteam system-check --strict --output json > /tmp/daily_check_$TIMESTAMP.json

# Extract summary
python << 'EOF'
import json
with open('/tmp/daily_check_$TIMESTAMP.json') as f:
    data = json.load(f)
    print(f"✅ {TIMESTAMP}: {data['status'].upper()}")
    print(f"  Tests: {data['metrics']['tests_passing']} pass, {data['metrics']['tests_failing']} fail")
    print(f"  Latency p99: {data['metrics']['latency_p99_ms']}ms")
    print(f"  Uptime: {data['metrics']['uptime_percent']}%")
EOF

# Manual: Copy JSON to NotebookLM Operations & Incidents
echo "📤 Ready to upload. Copy /tmp/daily_check_$TIMESTAMP.json to NotebookLM"
```

**Automation**: 
- Schedule with cron (Linux/Mac):
  ```bash
  0 9 * * * /path/to/scripts/ingest_daily_health.sh
  ```
- Schedule with Windows Task Scheduler (Windows):
  ```
  Trigger: Daily at 9:00 AM
  Action: python scripts/ingest_daily_health.py
  ```

---

### ✅ Task 2: Test Report Snapshot (9:15 AM UTC)

**What to do**:
1. Run test suite with JSON output:
   ```bash
   pytest tests/ --json-report --json-report-file=/tmp/test_report_$(date +%Y-%m-%d).json
   ```

2. Generate summary:
   ```
   Date: 2026-02-20
   Total: 176 tests
   Passed: 176
   Failed: 0
   Skipped: 0
   Duration: 6.27s
   
   Slowest tests:
   1. test_integration_cli.py - 0.45s
   2. test_chaos.py - 0.38s
   3. test_config_validation.py - 0.25s
   ```

3. **Upload to NotebookLM**: Metrics & Insights notebook
   - Add as "Daily Test Report: 2026-02-20"

**Script** (save as `scripts/ingest_daily_tests.sh`):
```bash
#!/bin/bash
DATE=$(date -u +"%Y-%m-%d")
pytest tests/ --json-report --json-report-file=/tmp/test_report_$DATE.json

python << EOF
import json
with open('/tmp/test_report_$DATE.json') as f:
    data = json.load(f)
    passed = data['summary']['passed']
    failed = data['summary']['failed']
    duration = data['duration']
    print(f"📊 {DATE} Test Report")
    print(f"   {passed} passed, {failed} failed")
    print(f"   Duration: {duration:.2f}s")
EOF

echo "📤 Test report ready: /tmp/test_report_$DATE.json"
```

---

### ✅ Task 3: Cost Snapshot (10:00 AM UTC)

**What to do**:
1. Extract finops data:
   ```python
   from aiteam.finops import BudgetManager
   manager = BudgetManager(...)
   snapshot = manager.snapshot()
   anomaly, reason = manager.detect_cost_anomaly()
   ```

2. Format snapshot:
   ```
   Daily Cost Report: 2026-02-20
   - Daily spend: $0.45
   - Monthly spend: $8.30
   - Budget remaining: $191.70 / $200.00
   - Anomaly detected: No
   - Trend: Stable (±2% vs last week)
   ```

3. **Upload to NotebookLM**: Metrics & Insights notebook

**Script** (save as `scripts/ingest_daily_costs.py`):
```python
from aiteam.finops import BudgetManager, BudgetPolicy
from pathlib import Path
from datetime import datetime

runtime_dir = Path("/path/to/runtime")
policy = BudgetPolicy()
manager = BudgetManager(runtime_dir, policy=policy)

snapshot = manager.snapshot()
anomaly, reason = manager.detect_cost_anomaly()

report = {
    "date": datetime.utcnow().isoformat(),
    "daily_spend": snapshot["daily_api_spend_usd"],
    "monthly_spend": snapshot["monthly_api_spend_usd"],
    "budget_remaining": policy.monthly_api_budget_usd - snapshot["monthly_api_spend_usd"],
    "anomaly_detected": anomaly,
    "anomaly_reason": reason,
}

print(f"💰 Cost Report: {report['daily_spend']}/day, {report['monthly_spend']}/month")
print(f"   Anomaly: {reason}")

# Save for upload
import json
with open(f"/tmp/cost_report_{datetime.utcnow().strftime('%Y-%m-%d')}.json", "w") as f:
    json.dump(report, f, indent=2)
```

---

### ✅ Task 4: Audit Trail Digest (11:00 AM UTC)

**What to do**:
1. Export audit trail:
   ```python
   from aiteam.audit_trail import AuditTrail
   audit = AuditTrail(runtime_dir)
   records = audit.read_all()
   summary = audit.summary()
   ```

2. Format digest:
   ```
   Audit Trail Digest: 2026-02-20
   - Total records: 147
   - New today: 8
   - Decision types: approval_granted (5), approval_denied (0), command_blocked (3)
   - Approvers active: lead-1, security-1, compliance-1
   - Rules triggered: [routing_policy, budget_check, compliance_gate]
   ```

3. **Upload to NotebookLM**: Compliance & Audits notebook

---

### ✅ Task 5: Commit Log (Rolling 24h)

**What to do**:
1. Extract recent commits:
   ```bash
   git log --oneline -20
   ```

2. **Upload to NotebookLM**: Architecture & Design notebook (as "Recent Changes")

**Script** (one-liner):
```bash
git log --oneline -20 > /tmp/recent_commits_$(date +%Y-%m-%d).txt
```

---

## Weekly Manual Ingestion (Monday 9:00 AM)

### ✅ Task 1: Sprint Retrospective

**What to do**:
1. Export retrospective notes (from team):
   ```
   Sprint 2 Retrospective: 2026-02-20
   
   What went well:
   - NotebookLM integration planned
   - 176 tests passing (no regressions)
   - Audit trail implementation complete
   
   What could improve:
   - Sprint planning took 2 hours (should be 1h)
   - Two incidents with slow response
   
   Unexpected learnings:
   - Team very engaged with NotebookLM pilot
   - Compliance checks catch real issues
   
   Decision: Continue current pace, no changes recommended
   ```

2. **Upload to NotebookLM**: Decisions & Learning notebook

---

### ✅ Task 2: Roadmap Status Update

**What to do**:
1. Export current roadmap progress:
   ```
   Roadmap Status: 2026-02-20
   
   Tier 1: 100% (91 tests) ✅
   Tier 2: 100% (143 tests) ✅
   Tier 3: 100% (176 tests) ✅
   
   Next milestone: NotebookLM integration (Week 1)
   Blockers: None
   Risk: None
   ```

2. **Upload to NotebookLM**: Metrics & Insights notebook

---

### ✅ Task 3: Tech Debt Review

**What to do**:
1. Scan for TODO comments:
   ```bash
   grep -r "TODO" aiteam/ tests/ --include="*.py" > /tmp/todos_$(date +%Y-%m-%d).txt
   ```

2. Categorize by priority:
   ```
   Tech Debt Summary: 2026-02-20
   
   HIGH priority (blocks features):
   - [3] Performance optimization in routing (estimated 5 days)
   
   MEDIUM priority (improves quality):
   - [5] Add error recovery tests (estimated 3 days)
   
   LOW priority (nice-to-have):
   - [2] Code cleanup (estimated 1 day)
   
   Recommended next sprint: Tackle HIGH items
   ```

3. **Upload to NotebookLM**: Metrics & Insights notebook

---

## Event-Triggered Ingestion (On-Demand)

### 🚨 Incident Ingestion

**When**: Incident occurs (detected by monitoring or report)

**What to do**:
1. Gather incident info:
   ```
   Incident Report: [INCIDENT_ID]
   
   Time detected: 2026-02-20 14:32 UTC
   Duration: 15 minutes
   Impact: API calls slow, 3 users affected
   
   Error logs: [PASTE HERE]
   Timeline: [PASTE HERE]
   Recovery: [HOW IT WAS FIXED]
   ```

2. **Upload to NotebookLM**: Operations & Incidents notebook
   - Title: "Incident: [NAME] 2026-02-20"

3. Use prompt #16-18 from NOTEBOOKLM_PROMPTS.md to draft analysis/postmortem

---

### 📈 Deployment Ingestion

**When**: Deploy to production

**What to do**:
1. Capture deployment info:
   ```
   Deployment Report: 2026-02-20
   
   Version: v0.2.0 (Tier 2 completion)
   Changes: 35 new tests, compliance audit, metrics
   Rollback plan: Revert to v0.1.0 via CLI
   
   Pre-deployment checks:
   - ✅ All 176 tests pass
   - ✅ No regressions
   - ✅ Performance acceptable
   - ✅ Security review passed
   ```

2. **Upload to NotebookLM**: Operations & Incidents notebook

---

### 🔐 Compliance Audit Ingestion

**When**: Audit requested or scheduled

**What to do**:
1. Export audit bundle:
   ```bash
   # Extract all compliance data
   python << 'EOF'
   from aiteam.audit_trail import AuditTrail
   from pathlib import Path
   
   audit = AuditTrail(Path("runtime"))
   records = audit.read_all()
   summary = audit.summary()
   
   # Output audit evidence bundle
   print(f"Total audit records: {len(records)}")
   print(f"Date range: [earliest] to [latest]")
   print(f"Rules enforced: [list]")
   print(f"Approvals: {summary['approvers']}")
   
   # Save for auditor
   import json
   with open("audit_evidence_bundle.json", "w") as f:
       json.dump(records, f, indent=2)
   EOF
   ```

2. **Upload to NotebookLM**: Compliance & Audits notebook
   - Use prompt #11-13 to generate audit readiness report

---

## Decision Log Ingestion (Real-Time)

**When**: Major architectural decision made

**What to do**:
1. **Immediately** fill out decision template (see DECISION_LOG.md)
2. **Commit** to git:
   ```bash
   git add docs/DECISION_LOG.md
   git commit -m "docs: Log decision - [Decision Name]"
   ```
3. **Share** with team in #decisions Slack channel
4. **Add to NotebookLM**: Decisions & Learning notebook
   - Copy full decision entry

---

## Integration Checklist

- [ ] Daily ingestion script created (health + tests + costs + audit)
- [ ] Weekly ingestion calendar set (Monday 9am retro + roadmap)
- [ ] Event-triggered process documented (incidents, deployments, audits)
- [ ] Decision log template in use (at least 3 entries)
- [ ] All 5 NotebookLM notebooks accessible by team
- [ ] Prompts document shared (NOTEBOOKLM_PROMPTS.md)
- [ ] Slack bot or reminder for daily uploads
- [ ] First incident handled via NotebookLM (postmortem drafted)
- [ ] First audit handled via NotebookLM (report generated)
- [ ] Team trained on NotebookLM workflow (30-min session)

---

## Automation Timeline

### Week 1 (Feb 24-28): Manual Phase
- Manual uploads to NotebookLM (proof of concept)
- Verify content quality
- Team feedback

### Week 2 (Mar 3-7): Partial Automation
- Scripts for daily ingestion
- Scheduled cron jobs
- Manual weekly/event uploads still

### Week 3+ (Mar 10+): Full Automation
- All daily uploads automated
- Slack bot notifies team
- Decision log auto-synced
- Real-time incident ingestion

---

## Troubleshooting

**Q: NotebookLM says "document too large"**  
A: Split into smaller chunks (e.g., last 30 days of audit trail, not all history)

**Q: Data is stale**  
A: Check if script failed. Manually trigger ingestion.

**Q: Conflicts in notebook**  
A: Use separate dated entries (e.g., "Daily Report: 2026-02-20") to avoid overwrites

**Q: Team not using it**  
A: Show ROI (time saved in first incident), then adoption spreads

