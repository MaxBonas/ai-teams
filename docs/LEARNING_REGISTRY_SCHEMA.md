# Learning Registry Data Schema
## Structure & Storage Format

**Purpose**: Define data structure for learning records  
**Format**: JSONL (one JSON object per line, stored atomically)  
**Location**: `runtime/learning_registry.jsonl`  
**Updated**: 2026-02-20

---

## Record Structure

Each learning record is a JSON object with the following schema:

```json
{
  "ts": "2026-02-20T14:32:00+00:00",
  "category": "project_failure",
  "title": "Atomic write race condition caused data corruption",
  "description": "...",
  "impact": "Lost cost data for 1 hour, audit trail inconsistent",
  "recommendation": "Implement atomic write pattern (write-to-temp + rename)",
  "tags": ["persistence", "critical", "sprint-1"],
  "project_id": "sprint-1",
  "owner": "system",
  "status": "open",
  "metadata": {
    "severity": "critical",
    "frequency": "first_occurrence",
    "effort_to_fix": "2 days"
  }
}
```

---

## Field Definitions

### Core Fields (Required)

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ts` | ISO8601 | Timestamp when learning was recorded | `"2026-02-20T14:32:00+00:00"` |
| `category` | enum | Type of learning | `"project_failure"`, `"system_insight"`, `"team_learning"`, `"user_feedback"` |
| `title` | string | Short, searchable title | `"Atomic write race condition"` |
| `description` | string | Detailed description | Multi-line markdown |
| `impact` | string | What was affected | `"1 hour data loss, user-facing"` |
| `recommendation` | string | What to do about it | Action steps |

### Context Fields (Recommended)

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `tags` | string[] | Tags for categorization | `["persistence", "critical"]` |
| `project_id` | string | Related project/sprint | `"sprint-1"`, `"tier-1"`, `"notebooklm"` |
| `owner` | string | Who discovered/owns this | `"@infra-lead"`, `"system"` |
| `status` | enum | Current status | `"open"`, `"actionable"`, `"addressed"`, `"archived"` |

### Metadata (Optional)

| Field | Type | Description | Example |
|------|------|-------------|---------|
| `severity` | enum | #critical, #high, #medium, #low | `"critical"` |
| `frequency` | string | Is this recurrent? | `"first_occurrence"`, `"recurring"` |
| `effort_to_fix` | string | Estimated time to address | `"2 days"`, `"1 week"` |
| `addressed_at` | ISO8601 | When was it fixed? | `"2026-02-21T09:00:00+00:00"` |
| `related_learnings` | string[] | Links to related learnings | `["title-1", "title-2"]` |
| `custom_field` | any | Any domain-specific data | Flexible |

---

## Category Types

### 1. `project_failure`
**When**: Test failure, bug, incident, regression, security issue, performance degradation, data loss  
**Required fields**: title, error_message, root_cause, impact, prevention  
**Example**:
```json
{
  "ts": "2026-02-20T14:32:00+00:00",
  "category": "project_failure",
  "title": "Concurrent writes corrupted finops ledger",
  "description": "Process 1 and 2 wrote to ledger simultaneously without locking",
  "impact": "Lost 1 hour of cost data; audit trail inconsistent; 30 min recovery time",
  "recommendation": "Implement atomic write pattern (write-to-temp + rename)",
  "tags": ["persistence", "critical"],
  "project_id": "sprint-1",
  "owner": "system",
  "status": "addressed",
  "metadata": {
    "severity": "critical",
    "effort_to_fix": "2 days",
    "addressed_at": "2026-02-20T16:00:00+00:00"
  }
}
```

### 2. `system_insight`
**When**: Performance observation, architectural pattern, data insight, design validation  
**Required fields**: title, observation, implication, suggested_action  
**Example**:
```json
{
  "ts": "2026-02-20T09:15:00+00:00",
  "category": "system_insight",
  "title": "P95 latency reveals tail-latency problems before p50 degrades",
  "description": "P95 jumped 40% (150→600ms) while p50 stayed stable (30ms). This caught a problem that would be invisible with just p50.",
  "impact": "System understanding improved; better alerting strategy",
  "recommendation": "Add p95/p99 percentiles to all dashboards; alert if p95 > 1000ms",
  "tags": ["performance", "metrics", "observability"],
  "owner": "@ops-lead",
  "status": "addressed"
}
```

### 3. `team_learning`
**When**: Best practice discovered, skill improvement, process optimization, cultural insight  
**Required fields**: title, what_we_learned, how_we_discovered, how_to_apply  
**Example**:
```json
{
  "ts": "2026-02-15T11:00:00+00:00",
  "category": "team_learning",
  "title": "Exponential backoff prevents retry storms",
  "description": "Exponential backoff (1s, 2s, 4s) reduces server load 60% vs linear (1s, 1s, 1s). Tested on 5 timeout incidents over 2 weeks.",
  "impact": "Team capability improved; 60% reduction in retry-related incidents",
  "recommendation": "Use exponential backoff for all network/API retries; max 3 attempts. Document in retry policy.",
  "tags": ["resilience", "performance", "retry-logic"],
  "owner": "@infra-lead",
  "status": "addressed",
  "metadata": {
    "effort_to_fix": "3 days",
    "addressed_at": "2026-02-15T16:00:00+00:00"
  }
}
```

### 4. `user_feedback`
**When**: User complaint, feature request, compliance concern, onboarding feedback, performance feedback  
**Required fields**: title, feedback, context, opportunity  
**Example**:
```json
{
  "ts": "2026-02-18T10:30:00+00:00",
  "category": "user_feedback",
  "title": "Onboarding experience is 5 days, should be 2 hours",
  "description": "New engineers spend 3-5 days reading documentation and asking questions. Observed in 3 recent hires independently. All said architecture is hard to understand without guidance.",
  "impact": "3 days/hire × 4 hires/year × $75/hr = $9K annual cost",
  "recommendation": "Create NotebookLM integration + architecture bootcamp + onboarding bot. Target: 2-hour ramp-up.",
  "tags": ["ux", "onboarding", "developer-experience"],
  "owner": "@product-lead",
  "status": "open",
  "metadata": {
    "frequency": "recurring",
    "effort_to_fix": "1 week"
  }
}
```

---

## Tag Conventions

### Standard Tags (Use These)

**Area Tags**:
- `#architecture` - System design decisions
- `#performance` - Speed, latency, throughput
- `#reliability` - Uptime, fault tolerance, recovery
- `#security` - Auth, encryption, data protection
- `#compliance` - Audit, governance, regulations
- `#observability` - Metrics, logging, tracing
- `#ux` - User experience, onboarding, developer experience
- `#database` - Data persistence, schema
- `#api` - REST, gRPC, contract, versioning

**Severity Tags**:
- `#critical` - Blocks production, data loss, security issue
- `#high` - Major impact, many users affected
- `#medium` - Noticeable but workaround exists
- `#low` - Minor, nice-to-have fix

**Project Tags**:
- `#sprint-1`, `#sprint-2`, `#sprint-3` - Sprint ID
- `#tier-1`, `#tier-2`, `#tier-3` - Tier ID
- `#notebooklm-integration`, `#tool-pinning`, etc. - Feature tags

### Tag Best Practices

- Use 2-5 tags per learning
- Combine 1 area + 1 severity + 0-2 project tags
- Example: `#performance #high #sprint-2`
- Consistent spelling (all lowercase)
- No spaces in tags

---

## Status Lifecycle

```
OPEN (new learning)
  ↓
  ACTIONABLE (decision made to address)
  ↓
  ADDRESSED (action completed/mitigated)
  ↓
  ARCHIVED (old, not relevant anymore)
```

### Status Rules

| Status | Meaning | Action |
|--------|---------|--------|
| `open` | Learning logged, no action yet | Needs assignment |
| `actionable` | Owner assigned, ready to work | In sprint backlog |
| `addressed` | Action completed/mitigated | Add `addressed_at` timestamp |
| `archived` | Superseded or no longer relevant | Set `archived_at` timestamp |

---

## Storage & Persistence

### Location
```
runtime/learning_registry.jsonl
```

### Format
JSONL (one JSON object per line):
```
{"ts":"2026-02-20T14:32:00+00:00","category":"project_failure",...}
{"ts":"2026-02-20T15:45:00+00:00","category":"system_insight",...}
{"ts":"2026-02-20T16:20:00+00:00","category":"team_learning",...}
```

### Durability
- **Atomic writes**: Write-to-temp + rename (prevents corruption)
- **Dedup on load**: MD5 checksums prevent duplicate entries
- **Backup**: Sync daily to docs/learning_registry_export.md

### Retention
- **Keep forever**: All learning records are valuable
- **Archive old**: After 6 months without activity, mark `archived`
- **Export**: Monthly markdown export for documentation

---

## Querying Examples

### Find all critical failures
```sql
WHERE category = "project_failure" AND tags CONTAINS "#critical"
```

### Find learnings by owner
```sql
WHERE owner = "@infra-lead"
```

### Find open items by project
```sql
WHERE status = "open" AND project_id = "sprint-1"
```

### Find repeated failures
```sql
WHERE category = "project_failure" AND metadata.frequency = "recurring"
```

### Find failures from last week
```sql
WHERE ts >= "2026-02-13T00:00:00+00:00" AND category = "project_failure"
```

---

## Integration with NotebookLM

### Daily Sync (Automated)

```python
registry = LearningRegistry(runtime_dir)
all_learnings = registry.read_all()
open_items = registry.read_open_items()
summary = registry.summary()

# Upload to NotebookLM
notebooklm.notebooks["Learnings & Insights"].add_document(
    title=f"Daily Learning Digest: {date}",
    content=registry.export_markdown()
)
```

### Weekly Report

```
Learning Registry Weekly Summary

Total: 42 learnings
This week: 8 new learnings
Addressed: 3
Open: 18

Top Issues:
1. #performance (11) - most common area
2. #sprint-1 (7) - most active sprint
3. #critical (5) - highest severity

Open Items Needing Action:
- Onboarding experience (user_feedback, @product-lead) - 1 week effort
- Tool acquisition retry (project_failure, @infra-lead) - 3 days effort
- Compliance audit (system_insight, @security-lead) - 5 days effort
```

---

## Example Query Results

### List Open Failures (Sorted by Severity)

```
PROJECT FAILURES (OPEN)

[CRITICAL] Atomic write race - persistence (Feb 20, @system)
  Impact: 1h data loss
  Action: Add atomic writes
  Effort: 2 days

[HIGH] Budget cap not enforced - compliance (Feb 18, @ops-lead)
  Impact: Overspend possible
  Action: Add cap enforcement in router
  Effort: 1 day

[HIGH] Slow onboarding - ux (Feb 15, @product-lead)
  Impact: 3 days/hire × $75/hr
  Action: NotebookLM + bootcamp
  Effort: 1 week
```

---

## Metadata Examples

### Minimal Record

```json
{
  "ts": "2026-02-20T14:32:00+00:00",
  "category": "system_insight",
  "title": "P95 reveals latency problems",
  "description": "P95 jumped before p50",
  "impact": "Better visibility",
  "recommendation": "Add p95 to dashboards",
  "tags": ["performance"],
  "owner": "system",
  "status": "open"
}
```

### Rich Record (With Context)

```json
{
  "ts": "2026-02-20T14:32:00+00:00",
  "category": "project_failure",
  "title": "Atomic write race condition",
  "description": "...",
  "impact": "1h data loss, audit inconsistency, 30m recovery",
  "recommendation": "Implement atomic write (write-to-temp + rename)",
  "tags": ["persistence", "critical", "sprint-1"],
  "project_id": "sprint-1",
  "owner": "system",
  "status": "addressed",
  "metadata": {
    "severity": "critical",
    "frequency": "first_occurrence",
    "effort_to_fix": "2 days",
    "addressed_at": "2026-02-20T16:00:00+00:00",
    "files_modified": ["aiteam/persistence.py"],
    "tests_added": 6,
    "related_learnings": ["Concurrent writes need protection", "Atomic patterns prevent corruption"]
  }
}
```

---

## Backward Compatibility

Current schema is v1.0. If fields change:
- New fields: Always optional, default to null
- Renamed fields: Add new field, deprecate old (keep both for 1 version)
- Type changes: Create new field with `_v2` suffix

---

## Validation Rules

- `ts`: Must be valid ISO8601
- `category`: Must be one of 4 types
- `title`: Required, 10-100 chars
- `status`: Must be open|actionable|addressed|archived
- `tags`: Array of lowercase strings starting with #
- `owner`: String (email or @username)
- `project_id`: Optional string (should match sprint/feature IDs)

