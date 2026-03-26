# NotebookLM Operational Prompts
## Ready-to-Use Queries for Every Scenario

**Format**: Copy → Paste into NotebookLM → Get instant answer  
**Updated**: 2026-02-20  
**Notebooks Required**: Listed for each prompt

---

## 📋 Daily Operations (Notebook: Operations & Incidents)

### ✅ 1. System Health Check (Morning Brief)
```
Notebooks needed: Operations & Incidents, Metrics & Insights

Prompt:
"Based on the latest system-check report and test results:
1. Is the system currently healthy? (Green/Yellow/Red)
2. What failed in the last 24 hours?
3. Are there any lingering issues from yesterday?
4. Do any test trends suggest degradation?
5. Recommended actions for today?"

Expected output: 2-3 min read, actionable list
Frequency: Daily 9am
Owner: On-call engineer
```

### ✅ 2. Test Trend Analysis
```
Notebooks needed: Operations & Incidents, Metrics & Insights

Prompt:
"Analyze the test suite:
1. Are tests passing more or less than last week?
2. Which test files are most flaky?
3. Are there any tests that suddenly started failing?
4. Average test execution time trend (faster/slower)?
5. Should we split tests into fast/slow suites?"

Expected output: 1 page analysis + recommendations
Frequency: Bi-weekly (Mondays)
Owner: QA Lead
```

### ✅ 3. Error Pattern Recognition
```
Notebooks needed: Operations & Incidents

Prompt:
"Looking at the recent errors and failures:
1. What are the top 5 error categories?
2. Which ones are user-facing vs internal?
3. Is there a temporal pattern (time of day, day of week)?
4. What errors repeat most?
5. Root cause likely to be in which module?"

Expected output: 1-2 page deep-dive
Frequency: Weekly
Owner: SRE / Infrastructure Lead
```

### ✅ 4. "Quick Debug" (Incident Mode)
```
Notebooks needed: Operations & Incidents

Prompt:
"Error happening now: [PASTE ERROR LOG / STACK TRACE / SYMPTOMS]

Context:
1. When did this start?
2. Has this happened before?
3. What changed recently that could cause this?
4. What should I check first?
5. Similar incidents in history and how they were resolved?"

Expected output: Action plan in 3 minutes
Frequency: On-demand during incidents
Owner: On-call engineer
```

---

## 🏗️ Architecture & Design (Notebook: Architecture & Design)

### ✅ 5. System Architecture Overview
```
Notebooks needed: Architecture & Design

Prompt:
"New team member question: Explain the AI Team orchestrator:
1. What is it? (1 sentence)
2. Main components and their roles (with examples)
3. How does a request flow through the system? (step-by-step)
4. What are the key design decisions and trade-offs?
5. Where would they start if they need to add a feature?"

Expected output: Onboarding doc (15-20 min read)
Frequency: On-demand for new hires
Owner: Tech Lead
```

### ✅ 6. Routing System Deep-Dive
```
Notebooks needed: Architecture & Design, Decisions & Learning

Prompt:
"Explain the hybrid routing (Pro-first + API fallback):
1. How does it decide between Pro and API?
2. What happens when Pro times out?
3. Budget enforcement workflow
4. Anomaly detection trigger
5. Why was this design chosen over alternatives?"

Expected output: Architecture doc + decision context
Frequency: On-demand or during feature planning
Owner: Routing Owner
```

### ✅ 7. Data Persistence Strategy
```
Notebooks needed: Architecture & Design, Decisions & Learning

Prompt:
"Explain persistence and data durability:
1. How do we ensure data isn't lost on failure?
2. Atomic write pattern explained (why?)
3. Where does each type of data go? (finops, audit, observability)
4. What happens if corruption is detected?
5. How does deduplication work?"

Expected output: Design doc + failure scenarios
Frequency: On-demand or during data-related PRs
Owner: Infrastructure / Data Lead
```

---

## 📊 Metrics & Insights (Notebook: Metrics & Insights)

### ✅ 8. Weekly Health Dashboard
```
Notebooks needed: Metrics & Insights, Operations & Incidents

Prompt:
"Generate this week's health dashboard for exec update:
- Test health: # passing, # failing, trend
- Latency: p50, p95, p99 (compare to last week)
- Cost: daily spend, anomalies, forecast
- Deployment health: # incidents, MTTR, success rate
- Velocity: # features shipped, # bugs fixed, blockers
- Risk surface: top tech debt items, compliance issues
- One sentence recommendation for leadership"

Expected output: 1-page exec summary
Frequency: Weekly (Friday afternoon)
Owner: Product Manager / Engineering Manager
```

### ✅ 9. Technical Debt Prioritization
```
Notebooks needed: Metrics & Insights

Prompt:
"Analyze technical debt:
1. Top 5 highest-ROI items to tackle (impact vs effort)
2. Which items are blocking features?
3. Which items are highest risk?
4. Time estimate for each
5. Recommended prioritization for next sprint

Format as: [Item Name] - [Impact] - [Effort] - [Days] - [Blocker?]"

Expected output: Prioritized backlog
Frequency: Bi-weekly before sprint planning
Owner: Engineering Manager
```

### ✅ 10. Velocity Forecast
```
Notebooks needed: Metrics & Insights

Prompt:
"Based on last 3 sprints:
1. Average velocity (points/tasks per sprint)
2. Trend: improving, stable, or degrading?
3. Blockers that slowed velocity
4. Forecast for next sprint (realistic capacity)
5. If we want to ship Feature X (Y points), when?"

Expected output: Capacity planning chart
Frequency: Weekly (before planning)
Owner: Scrum Master
```

---

## 🔍 Compliance & Audits (Notebook: Compliance & Audits)

### ✅ 11. Audit Readiness Report
```
Notebooks needed: Compliance & Audits

Prompt:
"Generate audit readiness report:
1. Is system audit-ready? (Y/N, and why)
2. All approval workflows documented
3. Evidence trails for sensitive operations
4. Policy changes logged and justified
5. Compliance gaps and remediation plans

Format as: [Requirement] - [Status] - [Evidence Location]"

Expected output: Audit-ready document
Frequency: Monthly or on audit trigger
Owner: Compliance Officer
```

### ✅ 12. Decision Approval Chain
```
Notebooks needed: Compliance & Audits, Decisions & Learning

Prompt:
"For decision '[DECISION_NAME]':
1. Who made the decision? When?
2. Who approved it? (all approvers)
3. What policy or rule required this?
4. What was the impact?
5. Is there any follow-up or review scheduled?"

Expected output: Compliance evidence file
Frequency: On-demand or monthly review
Owner: Compliance Officer
```

### ✅ 13. Rule Application Tracking
```
Notebooks needed: Compliance & Audits

Prompt:
"Summarize all rules applied in the last 30 days:
1. Which compliance/security rules were triggered?
2. How many times each?
3. Who triggered them and why?
4. Any patterns or concerning trends?
5. Recommended policy updates?"

Expected output: 1-page compliance trend report
Frequency: Monthly
Owner: Security / Compliance Lead
```

---

## 📚 Decisions & Learning (Notebook: Decisions & Learning)

### ✅ 14. Architecture Decision Context
```
Notebooks needed: Decisions & Learning, Architecture & Design

Prompt:
"For decision 'Atomic Writes':
1. Why was this decision made?
2. What problem did it solve?
3. Alternatives considered and rejected
4. Who approved it?
5. Has it worked well? Any regrets?"

Expected output: Decision rationale + learning
Frequency: On-demand or during tech reviews
Owner: Architect / Tech Lead
```

### ✅ 15. Sprint Retrospective Insights
```
Notebooks needed: Decisions & Learning, Metrics & Insights

Prompt:
"Lessons from last sprint:
1. Top 3 things that went well
2. Top 3 things that could improve
3. Unexpected learnings
4. Patterns across sprints?
5. Decision: should we change anything for next sprint?"

Expected output: Retrospective notes + action items
Frequency: Weekly (end of sprint)
Owner: Scrum Master
```

---

## 🚨 Incident Management (Notebook: Operations & Incidents)

### ✅ 16. Incident Timeline & Analysis
```
Notebooks needed: Operations & Incidents, Decisions & Learning

Prompt:
"[PASTE INCIDENT LOG / TIMELINE]

Analyze:
1. Timeline of events (when did things break)
2. Root cause (most likely)
3. What early warning signs existed?
4. How long did we take to detect/respond/fix?
5. What was the impact (users, money, systems)?"

Expected output: 1-page incident summary
Frequency: On-demand during incidents
Owner: On-call Lead
```

### ✅ 17. Postmortem Draft
```
Notebooks needed: Operations & Incidents, Decisions & Learning

Prompt:
"Draft postmortem for incident '[INCIDENT_NAME]':
1. Summary (what happened, impact, duration)
2. Timeline (with timestamps)
3. Root cause analysis
4. Contributing factors
5. Action items (what we'll change)
6. Lessons learned
7. Owner for follow-ups

Format for: [Team Postmortem Review]"

Expected output: Postmortem template filled
Frequency: After every incident
Owner: On-call Lead
```

### ✅ 18. Historical Incident Pattern
```
Notebooks needed: Operations & Incidents

Prompt:
"Have we seen similar incidents before?
- If error is '[ERROR_TYPE]', what similar incidents occurred?
- What was the root cause then?
- How was it fixed?
- Has the fix held?"

Expected output: Incident history + pattern analysis
Frequency: On-demand
Owner: SRE
```

---

## 👥 Onboarding & Knowledge (Notebook: Architecture & Design + all)

### ✅ 19. New Engineer Crash Course
```
Notebooks needed: All 5 notebooks

Prompt:
"New engineer is starting Monday. Create 2-hour self-guided tour:
1. What is this project? (context in 5 min)
2. How do I set it up locally? (walkthrough)
3. Architecture 101 (key concepts, 20 min)
4. Where's my first PR? (beginner-friendly issue + guide)
5. Who do I ask questions to? (points of contact)
6. Most common gotchas?
7. Reading list (order them by importance)"

Expected output: Structured onboarding doc
Frequency: Per new hire
Owner: Tech Lead
```

### ✅ 20. Q&A Session
```
Notebooks needed: Relevant to question

Prompt:
"[NEW ENGINEER QUESTION]

Answer:
1. Direct answer
2. Why is it that way?
3. Common mistakes to avoid
4. Further reading/related topics
5. Who can help if you get stuck?"

Expected output: Teaching-style response
Frequency: On-demand
Owner: Team (asynchronous)
```

---

## 📢 Stakeholder Communication (Notebook: Metrics & Insights)

### ✅ 21. Executive Brief
```
Notebooks needed: Metrics & Insights, Operations & Incidents

Prompt:
"Create 5-minute executive brief:
1. Status: Green/Yellow/Red (with context)
2. Progress: % toward roadmap goals
3. Key wins this week/month
4. Top 1-2 risks and mitigation
5. What we need from leadership
6. Next milestone and ETA

Audience: C-suite (non-technical)"

Expected output: 1-page exec summary
Frequency: Weekly or on-demand
Owner: Product Manager
```

### ✅ 22. Stakeholder Risk Communication
```
Notebooks needed: Metrics & Insights

Prompt:
"Translate technical risks for investors/executives:
- Technical debt: [X items] → Business risk: ?
- Test flakiness: [Y% pass rate] → Customer impact: ?
- Latency p95: [Zms] → User experience: ?
- Incident rate: [N incidents/month] → Cost/reputation: ?

Format as: Technical Metric → Business Impact → Action"

Expected output: 1-2 page risk narrative
Frequency: Monthly
Owner: Product / Engineering Manager
```

### ✅ 23. Feature Readiness Checklist
```
Notebooks needed: Architecture & Design, Metrics & Insights

Prompt:
"Is feature '[FEATURE_NAME]' ready to ship?
- Architecture documented? (Y/N + evidence)
- Tests written? (# tests, coverage %)
- Performance acceptable? (latency, memory)
- Security reviewed? (audit trail checks)
- Documentation complete? (user + internal)
- Compliance/audit implications? (rules affected)
- Rollback plan in place? (how to undo)
- Monitoring/alerts configured? (what to watch)

Output: Ship / Hold (with why)"

Expected output: Go/No-go decision
Frequency: Before every release
Owner: Tech Lead + PM
```

---

## 📝 Sprint Ceremonies (Notebooks: Metrics & Insights + Decisions & Learning)

### ✅ 24. Sprint Planning Prep
```
Prompt:
"Prepare for sprint planning meeting:
1. Last sprint: velocity and blockers
2. Roadmap status: % complete
3. Technical debt to tackle
4. Risks we should mitigate
5. Recommended story priorities
6. Team capacity (vacation, support, etc.)
7. One-page agenda for planning meeting"

Frequency: Day before sprint planning
Owner: Scrum Master
```

### ✅ 25. Sprint Goal Recommendation
```
Prompt:
"Given roadmap, velocity, and risks, recommend sprint goal:
- Option A: [Goal] - Pro: [X], Con: [Y], Risk: [Z]
- Option B: [Goal] - Pro: [X], Con: [Y], Risk: [Z]
- Recommendation: [Which + why]"

Frequency: Before sprint planning
Owner: Engineering Manager
```

---

## 🔄 Automatable Prompts (Run Daily/Weekly)

### ✅ 26. Daily Digest
```
Notebooks needed: Operations & Incidents, Metrics & Insights

Prompt (template):
"Daily digest (run every morning at 9am):
1. Overnight incidents? (if any)
2. Tests status (# pass/fail, trend)
3. Cost snapshot (spend vs budget)
4. Any audit trail changes? (approvals, rules)
5. Decision Log updates? (if any new)
6. One recommendation for the team today"

Output: 3-min read for team Slack
Frequency: Daily
Owner: Automation / Bot
```

### ✅ 27. Weekly Summary
```
Prompt (template):
"Weekly summary (run every Friday at 4pm):
1. Week overview (what shipped, what broke)
2. Team pulse (morale indicators from retro)
3. Blockers for next week
4. Top learning/decision from week
5. Metrics trend (velocity, quality, cost)
6. Risk hotspot this week"

Output: Email to stakeholders
Frequency: Weekly
Owner: Automation / PM
```

---

## 🎯 How to Use This Document

1. **Daily**: Use prompts #1-4 for operations
2. **Weekly**: Use prompts #8-10, #24-27 for planning
3. **Monthly**: Use prompts #11-13 for compliance
4. **On-demand**: Use #5-7 for architecture questions, #16-18 for incidents, #19-20 for onboarding
5. **Before shipping**: Use #23 for readiness check

**Pro Tip**: Copy entire prompt (context + question) into NotebookLM. Results are better with full context.

