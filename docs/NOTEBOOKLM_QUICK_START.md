# NotebookLM Integration - Quick Start Guide
## Get Started in 1 Hour

**Goal**: By end of this session, you'll have NotebookLM set up and answering your first question.

**Time commitment**: 60 minutes

**Prerequisites**: Access to NotebookLM (app.notebooklm.google.com), read these 3 docs

---

## Step 1: Create 5 Notebooks (15 min)

Go to [NotebookLM](https://notebooklm.google.com) and create 5 notebooks:

### Notebook 1: **Architecture & Design**
- Click "Create notebook"
- Name: `Architecture & Design - AI Team`
- Upload or paste:
  - `README.md` (from repo)
  - `aiteam/` folder docstrings (copy key modules)
  - This: `NOTEBOOKLM_STRATEGY.md` (section: "5-Notebook Architecture" → Notebook 1)

### Notebook 2: **Operations & Incidents**
- Name: `Operations & Incidents - AI Team`
- Upload or paste:
  - Latest `system-check --strict` output
  - Test report (from `pytest --json`)
  - This: `NOTEBOOKLM_STRATEGY.md` (section: "5-Notebook Architecture" → Notebook 2)

### Notebook 3: **Compliance & Audits**
- Name: `Compliance & Audits - AI Team`
- Upload or paste:
  - `audit_trail.jsonl` (sample entries)
  - `DECISION_LOG.md` (first 3 decisions)
  - This: `NOTEBOOKLM_STRATEGY.md` (section: "5-Notebook Architecture" → Notebook 3)

### Notebook 4: **Decisions & Learning**
- Name: `Decisions & Learning - AI Team`
- Upload or paste:
  - Full `DECISION_LOG.md`
  - Sprint retrospectives (if you have any)
  - This: `NOTEBOOKLM_STRATEGY.md` (section: "5-Notebook Architecture" → Notebook 4)

### Notebook 5: **Metrics & Insights**
- Name: `Metrics & Insights - AI Team`
- Upload or paste:
  - `SPRINT_ROADMAP_Q1_2026.md` (roadmap status)
  - Latest metrics report (test counts, costs)
  - This: `NOTEBOOKLM_STRATEGY.md` (section: "5-Notebook Architecture" → Notebook 5)

**✅ Done**: 5 notebooks created and seeded

---

## Step 2: Ask Your First Question (10 min)

Go to **Notebook 1 (Architecture & Design)** and ask:

```
Explain the AI Team orchestrator to a new engineer:
1. What is it? (1 sentence)
2. Main components and their roles
3. How does a request flow through the system?
4. Key design decisions and trade-offs
```

**Expected output**: 2-3 minute read explaining the system

**💡 Pro Tip**: Copy entire context (question + setup) for better answers

---

## Step 3: Try 3 More Scenarios (20 min)

### Scenario A: Operations Question
Go to **Notebook 2 (Operations & Incidents)** and ask:

```
Based on the latest system-check and test reports:
1. Is the system healthy?
2. What tests are slowest?
3. Any alerts or warnings?
```

### Scenario B: Compliance Question
Go to **Notebook 3 (Compliance & Audits)** and ask:

```
Generate a quick audit readiness checklist:
1. Are decisions logged?
2. Who approved recent changes?
3. Any compliance gaps?
```

### Scenario C: Decision Question
Go to **Notebook 4 (Decisions & Learning)** and ask:

```
Why did we choose atomic writes over simpler alternatives?
What were the trade-offs?
```

**✅ Done**: You've used NotebookLM for 3 different use cases

---

## Step 4: Share with Team (10 min)

1. **Send links** to all 5 notebooks
2. **Slack announcement**:
   ```
   📚 NotebookLM is live! 
   
   We now have 5 AI-powered notebooks for:
   • Architecture questions
   • Operational issues
   • Compliance/audits
   • Decision context
   • Metrics & insights
   
   Try it → Ask a question you've been wondering about
   Docs: #notebooklm-integration
   ```

3. **Schedule 30-min team training** (optional but recommended)
   - Show 3 prompt examples
   - Demonstrate speed vs. manual search
   - Let team try one question live

---

## Step 5: Daily/Weekly Routine (5 min)

Every day (morning) or week (as needed):

1. **Run system check**:
   ```bash
   aiteam system-check --strict --output json
   ```

2. **Paste output** into Notebook 2 (Operations & Incidents)
   - Give it a date label: "Daily Report: 2026-02-20"

3. **Run tests**:
   ```bash
   pytest tests/ --tb=short
   ```

4. **Paste summary** into Notebook 5 (Metrics & Insights)
   - Example: "176 tests passing, p99 latency 850ms, cost $0.45"

5. **When making decisions**, add to DECISION_LOG.md and paste to Notebook 4

---

## 🎯 Success Metrics (After 1 Week)

Check these to know it's working:

- [ ] Team asked at least 5 questions in NotebookLM
- [ ] At least 1 question saved someone time vs. manual search
- [ ] Daily ingestion is happening (uploads daily)
- [ ] No one has said "I need to dig through docs manually" (sign of adoption)
- [ ] Positive feedback in team retro

---

## 📖 Next: Read Full Docs (After Quick Start)

Once you're comfortable, read these in order:

1. **NOTEBOOKLM_STRATEGY.md** — Strategic overview, all 8 value dimensions
2. **NOTEBOOKLM_PROMPTS.md** — 25+ specific prompts for every scenario
3. **NOTEBOOKLM_INGESTION.md** — Automation scripts, daily workflows
4. **DECISION_LOG.md** — How to log decisions for context

---

## 💬 Common Questions

**Q: Will NotebookLM hallucinate?**  
A: Possible, but rare. Use it for synthesis (summaries, explanations), not ground truth. Verify critical info before acting.

**Q: Can I use it for secrets?**  
A: **No.** Never paste API keys, passwords, or sensitive data. Treat it like a public document.

**Q: How often should I update?**  
A: Daily if possible (morning ritual). Weekly minimum. More = fresher answers.

**Q: What if my question is too specific?**  
A: Add context. Example:
```
❌ Bad: "Why did we fail?"
✅ Good: "Why did [INCIDENT_NAME] happen? [PASTE ERROR LOG]"
```

**Q: Can multiple people use the same notebook?**  
A: Yes! Share the link. Everyone can add/ask.

---

## 🚀 Quick Links

- [NotebookLM App](https://notebooklm.google.com)
- [Full Strategy Doc](./NOTEBOOKLM_STRATEGY.md)
- [Prompt Examples](./NOTEBOOKLM_PROMPTS.md)
- [Ingestion Playbook](./NOTEBOOKLM_INGESTION.md)
- [Decision Log](./DECISION_LOG.md)

---

## ⏱️ Timeline (Recommended)

| Phase | Duration | Activities |
|-------|----------|------------|
| **Phase 0** | 1 hour | Quick Start (this doc) |
| **Phase 1** | 1 week | Daily ingestion + team questions |
| **Phase 2** | 2 weeks | Automation scripts, incident handling |
| **Phase 3** | Ongoing | Measurement, refinement, expansion |

---

## 📞 Questions?

- **Tech question**: Ask in #engineering Slack
- **How-to question**: Check NOTEBOOKLM_PROMPTS.md (25+ examples)
- **Not working?**: Troubleshooting in NOTEBOOKLM_INGESTION.md

---

**Ready? → Go to [NotebookLM](https://notebooklm.google.com) and create your first notebook now!** 🚀

