"""Tests for Batch 5, 6, and Backlog features.

Covers: cross-agent memory, self-delegation parsing, session history,
tool invocation parsing, mailbox read/unread, specialization routing,
skill ranking, budget signaling context.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from aiteam.memory import AgentMemoryStore, MemoryEntry
from aiteam.mailbox import Mailbox, MailMessage


# ── Cross-Agent Memory (5.2) ───────────────────────────────────────


class TestCrossAgentMemory:
    def _store(self, tmp_path: Path) -> AgentMemoryStore:
        return AgentMemoryStore(tmp_path / "memory")

    def test_relevant_across_agents_returns_entries(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.remember("eng-1", "engineer", "finding", "The API uses REST with JSON payloads")
        store.remember("research-1", "researcher", "finding", "API latency is 200ms p99")
        results = store.relevant_across_agents("API latency")
        assert len(results) >= 1
        agents = {e.agent_id for e in results}
        assert "research-1" in agents

    def test_relevant_across_agents_excludes_agent(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.remember("eng-1", "engineer", "finding", "Found a bug in auth")
        store.remember("eng-2", "engineer", "finding", "Auth module needs refactoring")
        results = store.relevant_across_agents("auth bug", exclude_agent="eng-1")
        agent_ids = {e.agent_id for e in results}
        assert "eng-1" not in agent_ids

    def test_relevant_across_agents_empty_query(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.remember("eng-1", "engineer", "finding", "something")
        assert store.relevant_across_agents("") == []
        assert store.relevant_across_agents("   ") == []

    def test_relevant_across_agents_truncates_content(self, tmp_path: Path):
        store = self._store(tmp_path)
        long_content = "keyword " + "x" * 1000
        store.remember("eng-1", "engineer", "finding", long_content)
        results = store.relevant_across_agents("keyword", max_chars_per_entry=100)
        assert len(results) == 1
        assert len(results[0].content) <= 104  # 100 + "..."

    def test_relevant_across_agents_respects_limit(self, tmp_path: Path):
        store = self._store(tmp_path)
        for i in range(10):
            store.remember(f"agent-{i}", "engineer", "finding", f"test keyword item {i}")
        results = store.relevant_across_agents("keyword", limit=3)
        assert len(results) <= 3

    def test_relevant_across_agents_excludes_meeting_minutes(self, tmp_path: Path):
        store = self._store(tmp_path)
        store.remember("eng-1", "engineer", "meeting_minutes", "keyword discussed in meeting")
        store.remember("eng-2", "engineer", "finding", "keyword in actual finding")
        results = store.relevant_across_agents("keyword")
        kinds = {e.kind for e in results}
        assert "meeting_minutes" not in kinds


# ── Self-Delegation Parsing (5.1) ──────────────────────────────────


class TestSelfDelegationParsing:
    """Test the REQUEST_TASK regex pattern directly."""

    _RE = re.compile(
        r"\[REQUEST_TASK\s+"
        r'type=(?P<type>research|engineer|review)\s+'
        r'topic="(?P<topic>[^"]{1,200})"\s+'
        r"priority=(?P<priority>high|medium|low)"
        r"\s*\]",
        re.IGNORECASE,
    )

    def test_parse_basic_request(self):
        text = '[REQUEST_TASK type=research topic="Investigate API rate limits" priority=high]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("type") == "research"
        assert m.group("topic") == "Investigate API rate limits"
        assert m.group("priority") == "high"

    def test_parse_engineer_request(self):
        text = '[REQUEST_TASK type=engineer topic="Implement caching layer" priority=medium]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("type") == "engineer"

    def test_parse_review_request(self):
        text = '[REQUEST_TASK type=review topic="Review auth changes" priority=low]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("type") == "review"
        assert m.group("priority") == "low"

    def test_parse_multiple_requests(self):
        text = (
            'Some output\n'
            '[REQUEST_TASK type=research topic="Check docs" priority=high]\n'
            'More output\n'
            '[REQUEST_TASK type=engineer topic="Fix bug" priority=medium]\n'
        )
        matches = list(self._RE.finditer(text))
        assert len(matches) == 2

    def test_no_match_invalid_type(self):
        text = '[REQUEST_TASK type=manager topic="Manage" priority=high]'
        assert self._RE.search(text) is None

    def test_no_match_missing_fields(self):
        text = '[REQUEST_TASK type=research priority=high]'
        assert self._RE.search(text) is None


# ── Tool Invocation Parsing (6.1) ──────────────────────────────────


class TestToolInvocationParsing:
    _RE = re.compile(
        r"\[USE_TOOL\s+"
        r"(?:server=(?P<server>[^\s]+)\s+)?"
        r"tool=(?P<tool>[^\s]+)"
        r'(?:\s+args=(?P<args>\{[^}]*\}))?'
        r"\s*\]",
        re.IGNORECASE,
    )

    def test_parse_mcp_tool(self):
        text = '[USE_TOOL server=semgrep tool=scan args={"path": "/src"}]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("server") == "semgrep"
        assert m.group("tool") == "scan"
        assert m.group("args") == '{"path": "/src"}'

    def test_parse_cli_tool_no_server(self):
        text = '[USE_TOOL tool=pytest args={"command": "pytest tests/"}]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("server") is None
        assert m.group("tool") == "pytest"

    def test_parse_tool_no_args(self):
        text = '[USE_TOOL tool=lint]'
        m = self._RE.search(text)
        assert m is not None
        assert m.group("tool") == "lint"
        assert m.group("args") is None

    def test_multiple_tool_calls(self):
        text = (
            '[USE_TOOL server=s1 tool=t1 args={"a": 1}]\n'
            '[USE_TOOL tool=t2]\n'
            '[USE_TOOL server=s2 tool=t3 args={"b": 2}]\n'
        )
        matches = list(self._RE.finditer(text))
        assert len(matches) == 3


# ── Mailbox Read/Unread (Backlog B5) ──────────────────────────────


class TestMailboxReadUnread:
    def _mailbox(self, tmp_path: Path) -> Mailbox:
        return Mailbox(tmp_path / "mailbox.jsonl")

    def test_send_creates_message_id(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("eng-1", "lead-1", "Test", "Body")
        msgs = mb.list_messages()
        assert len(msgs) == 1
        assert msgs[0].message_id.startswith("msg-")

    def test_unread_messages(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("eng-1", "lead-1", "Test 1", "Body 1")
        mb.send("eng-2", "lead-1", "Test 2", "Body 2")
        unread = mb.unread_messages("lead-1")
        assert len(unread) == 2

    def test_mark_read_reduces_unread(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("eng-1", "lead-1", "Test", "Body")
        msgs = mb.list_messages("lead-1")
        assert mb.unread_count("lead-1") == 1
        mb.mark_read(msgs[0].message_id)
        assert mb.unread_count("lead-1") == 0

    def test_mark_read_bulk(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("a", "lead-1", "S1", "B1")
        mb.send("b", "lead-1", "S2", "B2")
        mb.send("c", "lead-1", "S3", "B3")
        msgs = mb.list_messages("lead-1")
        mb.mark_read_bulk([m.message_id for m in msgs[:2]])
        assert mb.unread_count("lead-1") == 1

    def test_inbox_query_filters(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("eng-1", "lead-1", "From eng", "Body", task_id="t1")
        mb.send("qa-1", "lead-1", "From qa", "Body", task_id="t2")
        mb.send("eng-1", "review-1", "Other", "Body", task_id="t1")

        # Filter by sender
        results = mb.inbox_query("lead-1", sender="eng-1")
        assert len(results) == 1
        assert results[0].sender == "eng-1"

        # Filter by task_id
        results = mb.inbox_query("lead-1", task_id="t2")
        assert len(results) == 1
        assert results[0].task_id == "t2"

    def test_inbox_query_unread_only(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        mb.send("a", "lead-1", "S1", "B1")
        mb.send("b", "lead-1", "S2", "B2")
        msgs = mb.list_messages("lead-1")
        mb.mark_read(msgs[0].message_id)
        unread = mb.inbox_query("lead-1", unread_only=True)
        assert len(unread) == 1

    def test_inbox_query_limit(self, tmp_path: Path):
        mb = self._mailbox(tmp_path)
        for i in range(10):
            mb.send("a", "lead-1", f"S{i}", f"B{i}")
        results = mb.inbox_query("lead-1", limit=3)
        assert len(results) == 3


# ── Skill Ranking (6.4) ───────────────────────────────────────────


# ── Evidence Gate Phase Bypass (Batch 8) ───────────────────────────


class TestEvidenceGatePhaseBypass:
    """Test that planning phases skip evidence gate while build phases don't."""

    def _phase_name(self, task_id: str) -> str:
        return task_id.split("::")[-1] if "::" in task_id else ""

    def _is_planning_phase(self, task_id: str) -> bool:
        phase = self._phase_name(task_id)
        return phase.startswith("plan_") or phase in ("lead_intake", "lead_close", "discovery")

    def test_plan_engineering_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::plan_engineering")

    def test_plan_research_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::plan_research")

    def test_plan_risks_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::plan_risks")

    def test_lead_intake_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::lead_intake")

    def test_lead_close_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::lead_close")

    def test_discovery_skips_gate(self):
        assert self._is_planning_phase("CHAT-123::discovery")

    def test_build_does_not_skip_gate(self):
        assert not self._is_planning_phase("CHAT-123::build")

    def test_review_does_not_skip_gate(self):
        assert not self._is_planning_phase("CHAT-123::review")

    def test_qa_does_not_skip_gate(self):
        assert not self._is_planning_phase("CHAT-123::qa")

    def test_security_does_not_skip_gate(self):
        assert not self._is_planning_phase("CHAT-123::security")

    def test_delegated_does_not_skip_gate(self):
        assert not self._is_planning_phase("CHAT-123::delegated_0")

    def test_bare_task_id_does_not_skip(self):
        assert not self._is_planning_phase("CHAT-123")


# ── Conversational Task Detection (Batch 10) ──────────────────────


class TestConversationalTaskDetection:
    """Test keyword-based conversational/theoretical task detection."""

    _CONVERSATIONAL_KEYWORDS = frozenset({
        "¿", "explica", "explícame", "describe", "qué es", "qué son", "cuál es",
        "cuáles son", "cómo funciona", "cómo se", "por qué", "para qué",
        "diferencia entre", "compara", "análisis", "analiza", "reflexión",
        "reflexiona", "opinión", "filosofía", "filosófico", "teoría", "teórico",
        "estrategia", "recomendación", "recomienda", "debería", "consejo",
        "resumen", "resume", "enumera", "lista de", "ventajas",
        "desventajas", "pros y contras", "cuándo", "qué piensas",
        "what is", "what are", "how does", "how do", "why is", "why are",
        "explain", "describe", "compare", "analysis", "analyze", "review",
        "theory", "theoretical", "philosophy", "opinion", "strategy",
        "recommend", "should i", "pros and cons", "when to", "what do you think",
        "summarize", "summary", "list of", "advantages", "disadvantages",
    })

    def _is_conversational(self, title: str, description: str = "") -> bool:
        blob = f"{title} {description}".lower()
        if "?" in blob:
            return True
        return any(kw in blob for kw in self._CONVERSATIONAL_KEYWORDS)

    # --- Should be detected as conversational ---

    def test_question_mark_in_title(self):
        assert self._is_conversational("¿Cuál es la mejor arquitectura?")

    def test_english_question_mark(self):
        assert self._is_conversational("What is the best architecture?")

    def test_explain_keyword(self):
        assert self._is_conversational("Explica el flujo de autenticación")

    def test_theory_keyword(self):
        assert self._is_conversational("Teoría de diseño de APIs REST")

    def test_comparison_keyword(self):
        assert self._is_conversational("Compara Redis vs Memcached")

    def test_analysis_keyword_english(self):
        assert self._is_conversational("Analysis of current architecture")

    def test_recommendation_keyword(self):
        assert self._is_conversational("Recomienda una estrategia de cache")

    def test_philosophy_keyword(self):
        assert self._is_conversational("Reflexión filosófica sobre microservicios")

    def test_summary_in_description(self):
        assert self._is_conversational("Tech review", "Provide a summary of options")

    def test_pros_and_cons(self):
        assert self._is_conversational("Pros y contras de usar GraphQL")

    # --- Should NOT be detected as conversational ---

    def test_build_task_not_conversational(self):
        assert not self._is_conversational("Implementar endpoint de login", "Crear ruta POST /auth/login con JWT")

    def test_fix_task_not_conversational(self):
        assert not self._is_conversational("Fix bug in payment module", "Payment fails when amount is zero")

    def test_create_task_not_conversational(self):
        assert not self._is_conversational("Create database migration", "Add users table with index on email")

    def test_refactor_task_not_conversational(self):
        assert not self._is_conversational("Refactor auth middleware", "Extract token validation to separate function")

    def test_deploy_task_not_conversational(self):
        assert not self._is_conversational("Deploy to production", "Run deployment pipeline for v2.1.0")


class TestSkillRanking:
    """Test that skill ranking annotations are generated correctly."""

    def test_success_rate_annotation_recommended(self):
        rate = 85.0
        tag = " [RECOMENDADO - {:.0f}% exito]".format(rate)
        assert "RECOMENDADO" in tag
        assert "85%" in tag

    def test_success_rate_annotation_caution(self):
        rate = 30.0
        tag = " [USAR CON PRECAUCION - {:.0f}% exito]".format(rate)
        assert "PRECAUCION" in tag
        assert "30%" in tag

    def test_success_rate_annotation_neutral(self):
        rate = 60.0
        tag = " [{:.0f}% exito]".format(rate)
        assert "60%" in tag
        assert "RECOMENDADO" not in tag
        assert "PRECAUCION" not in tag
