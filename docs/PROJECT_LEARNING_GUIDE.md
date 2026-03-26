# Project Learning Registry Guide
## How to Capture Failures, Insights, and Growth

**Purpose**: Institutionalize learning so the project and team continuously improve  
**Audience**: All team members, product leadership  
**Updated**: 2026-02-20

---

## Why This Matters

Every failure, insight, and piece of feedback is **data**. Without capturing it, we:
- Repeat the same mistakes (bug resurfaces)
- Lose institutional knowledge (person leaves, knowledge gone)
- Miss patterns (3 incidents are related, but we didn't notice)
- Disappoint users (feedback ignored, same problem next sprint)

This registry ensures **nothing is lost**.

---

## 4 Types of Learning to Register

### 1. 🔴 **Project Failures**
*"What broke and why? What did we learn?"*

**When to log**:
- Test failure caused by code bug
- Security issue discovered
- Performance degradation
- Data corruption or loss
- Incident postmortem

**Example**:
```
Title: "Atomic write race condition caused data corruption"
What happened: Two concurrent processes wrote to finops ledger simultaneously
Why it happened: No file locking mechanism
Impact: Lost cost data for 1 hour, audit trail inconsistent
Prevention: Implement atomic write pattern (write-to-temp + rename)
Tags: #persistence #critical
```

**Command** (coming soon):
```bash
aiteam learning record-failure \
  --title "Atomic write race" \
  --error "File corruption" \
  --cause "No locking" \
  --impact "1 hour data loss" \
  --prevention "Add atomic writes" \
  --tags "persistence,critical"
```

---

### 2. 🧠 **System Insights**
*"What did we learn about how our system works?"*

**When to log**:
- Performance observation (p95 jumped 40%)
- Architectural pattern discovery (atomic writes reduce corruption by 99%)
- Data pattern (certain error types cluster on Mondays)
- Tradeoff validation (metrics confirm our decision was right)

**Example**:
```
Title: "Percentile metrics reveal latency problems before p50 degrades"
Observation: p95 jumped 40% while p50 stayed stable
Implication: We have tail-latency issues affecting 5% of requests; p50 is not sufficient metric
Action: Add p95/p99 to dashboards; alert if p95 > 1000ms
Tags: #performance #metrics #observability
```

**Command**:
```bash
aiteam learning record-insight \
  --title "P95 reveals tail latency" \
  --observation "p95 at 1200ms, p50 at 50ms" \
  --implication "Tail latency hidden by averages" \
  --action "Add p95/p99 to dashboards" \
  --tags "performance,metrics"
```

---

### 3. 👥 **Team Learnings**
*"What did the team learn together? How should we change behavior?"*

**When to log**:
- New best practice discovered (exponential backoff works better)
- Team skill improvement (everyone now knows atomic writes)
- Process optimization (decision log saves meetings)
- Cultural insight (async decision-making faster)

**Example**:
```
Title: "Exponential backoff reduces retry storm by 60%"
What we learned: 1s, 2s, 4s backoff beats 1s, 1s, 1s linear
How we discovered: Built retry logic, measured incident recovery
How to apply: Use exponential backoff for all network retries
Owner: @infra-lead
Tags: #resilience #performance
```

**Command**:
```bash
aiteam learning record-team \
  --title "Exponential backoff wins" \
  --what "Exp backoff 60% better than linear" \
  --discovery "Tested on 5 incidents" \
  --application "Use for all retries" \
  --owner "@infra-lead" \
  --tags "resilience"
```

---

### 4. 💬 **User/Product Feedback**
*"What did we learn from stakeholders? What opportunities exist?"*

**When to log**:
- User complaint or praise
- Onboarding feedback
- Feature request with reasoning
- Performance feedback from customers
- Compliance/security concern

**Example**:
```
Title: "Onboarding takes 5 days, should be 2 hours"
Feedback: New engineers struggle with architecture docs and need constant help
Context: Observed in 3 recent hires; consistent pattern
Opportunity: Create onboarding NotebookLM + auto-generated architecture guide
From: @product-lead, @tech-lead (multiple sources)
Impact: 5 days → 2 hours = 3 days saved per hire × 4 hires/year = 12 days ROI
```

**Command**:
```bash
aiteam learning record-feedback \
  --title "Onboarding too long" \
  --feedback "New hires lost for 5 days" \
  --context "3 hires reported same issue" \
  --opportunity "NotebookLM + onboarding bot" \
  --from "@product-lead" \
  --impact "3 days/hire × 4/year = 12 days/year ROI"
```

---

## How to Log a Learning (4 Ways)

### Option 1: Direct CLI (Fastest) 🚀
```bash
aiteam learning record-failure \
  --title "X" \
  --error "Y" \
  --cause "Z" \
  --prevention "A" \
  --tags "critical,performance"
```

### Option 2: API in Code (For Automation)
```python
from aiteam.learning_registry import LearningRegistry
from pathlib import Path

registry = LearningRegistry(Path("runtime"))

registry.record_project_failure(
    title="Atomic write race",
    error_message="File corruption",
    what_happened="Two processes wrote simultaneously",
    why_it_happened="No locking",
    impact="1 hour data loss",
    how_to_prevent="Add atomic writes",
    project_id="sprint-1",
    tags=["persistence", "critical"],
)
```

### Option 3: Web Form (Coming Soon) 🔄
Link: https://ai-team-learning.internal/new

### Option 4: Slack Bot (Coming Soon) 💬
```
/learning failure title="X" error="Y" cause="Z" prevention="A"
/learning insight title="X" observation="Y" action="Z"
/learning team-learning title="X" what="Y" how-to-apply="Z"
/learning feedback title="X" feedback="Y" opportunity="Z"
```

---

## Best Practices

### ✅ **Do This**

1. **Log ASAP**
   - Don't wait for end of sprint
   - Capture while memory is fresh
   - Learning decays with time

2. **Be Specific**
   - Vague: "System was slow"
   - Better: "p95 latency jumped from 150ms to 800ms on Feb 20, 3:30 PM UTC"

3. **Include "Why"**
   - Vague: "We chose atomic writes"
   - Better: "We chose atomic writes to prevent race conditions (vs DB=complex, vs simple JSON=risky)"

4. **Tie to Impact**
   - Vague: "Onboarding could be faster"
   - Better: "Onboarding 5 days → 2 hours = 3 days saved × 4 hires/year = 12 days ROI"

5. **Use Tags Consistently**
   - Standard tags: #performance, #reliability, #security, #compliance, #ux, #architecture
   - Project tags: #sprint-1, #tier-1, #notebooklm-integration
   - Severity: #critical, #high, #medium, #low

### ❌ **Don't Do This**

- Blame individuals ("John broke this")
- Vague descriptions ("bad design")
- No action ("we know this is broken")
- Log months later (context is lost)

---

## Viewing & Using Learnings

### Query Learning Registry

```bash
# View all open learnings
aiteam learning list --status open

# View failures only
aiteam learning list --category failure

# View learnings by tag
aiteam learning list --tag performance

# View learnings by project
aiteam learning list --project sprint-1

# Export as markdown for docs
aiteam learning export --format markdown > learnings.md

# Generate summary
aiteam learning summary
```

### Output: Summary Report

```
Learning Registry Summary

Total Learnings: 42
├── Project Failures: 8
├── System Insights: 15
├── Team Learnings: 12
└── User Feedback: 7

Status:
├── Open: 18 (actions pending)
├── Addressed: 20 (fixed)
└── Archived: 4

Top Tags:
1. #performance (11 learnings)
2. #reliability (8)
3. #sprint-1 (7)
4. #critical (5)
5. #architecture (4)

Recent Failures:
- Atomic write race (Feb 20, open) → prevention: add locking
- Budget limit exceeded (Feb 18, addressed) → prevention: add cap enforcement
```

---

## Integration with NotebookLM

Every learning is **automatically synced** to NotebookLM's "Learnings & Insights" notebook:

1. **Daily 10am**: All new learnings from past 24h uploaded
2. **Weekly Friday 4pm**: Summary + pattern analysis
3. **On-demand**: "What have we learned about [topic]?" → NotebookLM synthesizes

**Example NotebookLM Query**:
```
"Based on all our learnings:
1. What are the top 5 system insights?
2. What patterns do you see in failures?
3. What should we prioritize fixing based on impact?
4. What user feedback is most common?"
```

---

## Workflow: From Failure to Prevention

### Timeline

```
1. FAILURE OCCURS
   ↓
2. INCIDENT RESPONSE (0-30 min)
   - Fix immediate issue
   ↓
3. POSTMORTEM (30 min - 2 hours)
   - Root cause analysis
   - Impact assessment
   ↓
4. LOG TO REGISTRY (5 min)
   - Record failure with context
   - Record prevention action
   - Tag and assign owner
   ↓
5. PREVENT RECURRENCE (3-7 days)
   - Implement prevention (add atomic writes, add cap, etc.)
   - Add test to catch regression
   - Close registry item
   ↓
6. REFLECT (Weekly)
   - Review open items
   - Identify patterns
   - Share learnings in team retro
   ↓
7. ACT (Ongoing)
   - Prioritize high-impact learnings
   - Build into sprint planning
```

---

## Sprint Workflow

### Sprint Planning

**Before planning**:
```bash
aiteam learning list --status open --project sprint-1
```

**In planning meeting**:
- Review top 3 open learnings
- Discuss: "Should we prioritize fixing these?"
- Decide: Add to backlog or defer

### Sprint Execution

**Weekly in retro**:
```bash
aiteam learning export --format markdown --week current > this_week_learnings.md
```

**Discussion**:
- What failures happened this week?
- What insights did we gain?
- What should we learn for next sprint?

### Sprint Retrospective

**After sprint**:
- Mark addressed learnings as "addressed"
- Log any new insights from sprint
- Measure: "How many learnings did we act on?"

---

## Metrics & Impact

### Track These

| Metric | Meaning |
|--------|---------|
| Total learnings | Institutional knowledge captured |
| Learnings/sprint | Team engagement |
| % addressed | Action orientation |
| Time to address | Responsiveness |
| Repeat failures | Learning effectiveness |

### Goals

- ✅ **50+** learnings by end of Q1
- ✅ **80%+** of failures logged within 1 hour
- ✅ **90%+** of open learnings have action owners
- ✅ **Zero** repeat failures (same root cause)
- ✅ **50%+** of failures get prevented action implemented

---

## Examples (Real from This Project)

### Example 1: Project Failure
```
Title: Concurrent writes corrupted finops ledger
Error: JSON parse error on ledger read
What happened: Process 1 and 2 wrote to finops_ledger.json simultaneously
Why: No file locking mechanism
Impact: Lost 1 hour of cost data; audit inconsistent; took 30 min to recover
Prevention: Implement atomic write (write-to-temp + rename)
Tags: #persistence #critical #sprint-1
Status: Addressed (Feb 20, added AtomicFileWriter)
```

### Example 2: System Insight
```
Title: Z-score anomaly detection needs more history
Observation: Anomaly detection required 7+ records; too strict for new systems
Implication: Can't detect anomalies in early-stage services
Action: Make history requirement configurable; document when it's reliable (>14 days data)
Tags: #observability #anomaly-detection #configuration
Status: Open
```

### Example 3: Team Learning
```
Title: Exponential backoff prevents retry storms
What we learned: Exponential backoff (1s, 2s, 4s) reduces server load 60% vs linear
How discovered: Tested on 5 timeout incidents over 2 weeks
How to apply: Use exponential backoff for all network/API retries; max 3 attempts
Owner: @infra-lead
Tags: #resilience #performance #retry-logic
Status: Addressed (Feb 15, implemented in tool_lock.py)
```

### Example 4: User Feedback
```
Title: Onboarding experience is 5 days, should be 2 hours
Feedback: New engineers spend 3-5 days reading docs, asking questions
Context: 3 recent hires reported same issue; independent corroboration
Opportunity: Create NotebookLM integration + architecture bootcamp
From: @product-lead, corroborated by tech lead
Impact: 3 days × 4 hires/year × $75/hr = $9K ROI annually
Status: Open → Action: Implement NotebookLM (starting Feb 24)
```

---

## Coming Soon (Automation)

- 🔄 **Slack integration** - Log learnings without leaving Slack
- 📊 **Dashboards** - Visualize learnings over time
- 🤖 **AI analysis** - Pattern detection ("you've had 5 timeout failures, all around 3pm")
- 📧 **Weekly digest** - Email summary to team
- 🔗 **GitHub integration** - Auto-log from issue comments
- 📈 **Forecasting** - Predict next failure patterns

---

## Quick Reference

| Command | Purpose |
|---------|---------|
| `aiteam learning record-failure` | Log project failure |
| `aiteam learning record-insight` | Log system insight |
| `aiteam learning record-team` | Log team learning |
| `aiteam learning record-feedback` | Log user feedback |
| `aiteam learning list` | View learnings |
| `aiteam learning summary` | Stats |
| `aiteam learning export` | Markdown export |
| `aiteam learning mark-addressed` | Close item |

---

## Questions?

- **How do I log?** → Section "How to Log (4 Ways)"
- **What should I log?** → Section "4 Types of Learning"
- **How to use learnings?** → Section "Viewing & Using"
- **Examples?** → Section "Examples (Real)"

**Start logging today!** Every learning helps the project and team grow. 🚀

