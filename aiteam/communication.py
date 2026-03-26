from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiteam.mailbox import Mailbox
from aiteam.memory import AgentMemoryStore
from aiteam.observability import EventLogger


@dataclass
class MeetingParticipant:
    agent_id: str
    role: str


@dataclass
class TeamDecision:
    """Decision registrada por el equipo."""

    decision_id: str
    task_id: str
    proposer: str  # agent_id que propuso
    decision_text: str
    context: str = ""
    status: str = "proposed"  # proposed, accepted, rejected
    votes: dict[str, str] = field(
        default_factory=dict
    )  # agent_id → approve/reject/abstain
    created_at: str = ""
    resolved_at: str = ""


class TeamCommunicator:
    def __init__(
        self,
        mailbox: Mailbox,
        memory: AgentMemoryStore,
        event_logger: EventLogger | None = None,
        runtime_dir: Path | None = None,
    ) -> None:
        self.mailbox = mailbox
        self.memory = memory
        self.event_logger = event_logger
        self._decisions: list[TeamDecision] = []
        self._decisions_path = (
            (runtime_dir / "team_decisions.jsonl") if runtime_dir else None
        )
        if self._decisions_path:
            self._load_decisions()

    # ── Direct Messaging ──────────────────────────────────────

    def send_dm(
        self,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        task_id: str | None = None,
    ) -> None:
        self.mailbox.send(
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            task_id=task_id,
        )
        self._event(
            "mail_dm",
            {
                "sender": sender,
                "recipient": recipient,
                "subject": subject,
                "task_id": task_id,
            },
        )

    def broadcast(
        self,
        sender: str,
        subject: str,
        body: str,
        task_id: str | None = None,
    ) -> None:
        self.mailbox.send(
            sender=sender,
            recipient="broadcast",
            subject=subject,
            body=body,
            task_id=task_id,
        )
        self._event(
            "mail_broadcast",
            {"sender": sender, "subject": subject, "task_id": task_id},
        )

    # ── Sync Meetings ─────────────────────────────────────────

    def run_sync_meeting(
        self,
        topic: str,
        participants: list[MeetingParticipant],
        task_id: str | None = None,
        meeting_kind: str | None = None,
    ) -> str:
        resolved_kind = meeting_kind or self._classify_meeting(
            topic=topic, task_id=task_id
        )
        lines = [f"Meeting Topic: {topic}", f"Meeting Kind: {resolved_kind}"]
        useful_participants = 0
        for participant in participants:
            standup, is_useful = self._standup_line(participant)
            if is_useful:
                useful_participants += 1
            lines.append(standup)

        # Include recent decisions in meeting minutes
        decision_count = 0
        if task_id:
            task_decisions = self.get_decisions(task_id=task_id, limit=5)
            if task_decisions:
                decision_count = len(task_decisions)
                lines.append("\nDecisiones del equipo:")
                for d in task_decisions:
                    status_icon = {
                        "accepted": "+",
                        "rejected": "-",
                        "proposed": "?",
                    }.get(d.status, "?")
                    lines.append(
                        f"  [{status_icon}] {d.decision_text[:150]} (por {d.proposer})"
                    )

        if (
            resolved_kind == "informational"
            and useful_participants == 0
            and decision_count == 0
        ):
            self._event(
                "sync_meeting_skipped",
                {
                    "topic": topic,
                    "meeting_kind": resolved_kind,
                    "participants": [f"{p.role}:{p.agent_id}" for p in participants],
                    "task_id": task_id,
                    "reason": "insufficient_signal",
                },
            )
            return ""

        minutes = "\n".join(lines)
        self.broadcast(
            sender="meeting-bot",
            subject=f"Sync meeting: {topic}",
            body=minutes,
            task_id=task_id,
        )

        for participant in participants:
            self.memory.remember(
                agent_id=participant.agent_id,
                role=participant.role,
                kind="meeting_minutes",
                content=minutes,
                task_id=task_id,
                tags=["meeting", "sync", resolved_kind],
            )

        self._event(
            "sync_meeting",
            {
                "topic": topic,
                "meeting_kind": resolved_kind,
                "useful_participants": useful_participants,
                "decision_count": decision_count,
                "participants": [f"{p.role}:{p.agent_id}" for p in participants],
                "task_id": task_id,
            },
        )
        return minutes

    # ── Decision Recording ────────────────────────────────────

    def record_decision(
        self,
        task_id: str,
        proposer: str,
        decision_text: str,
        context: str = "",
        status: str = "accepted",
    ) -> TeamDecision:
        """Registra una decision del equipo."""
        import uuid

        now = datetime.now(timezone.utc).isoformat()
        decision = TeamDecision(
            decision_id=str(uuid.uuid4())[:8],
            task_id=task_id,
            proposer=proposer,
            decision_text=decision_text,
            context=context,
            status=status,
            created_at=now,
            resolved_at=now if status != "proposed" else "",
        )
        self._decisions.append(decision)
        self._persist_decision(decision)

        # Broadcast to team
        self.broadcast(
            sender=proposer,
            subject=f"Decision: {decision_text[:60]}",
            body=f"[{status.upper()}] {decision_text}\nContexto: {context[:200]}"
            if context
            else f"[{status.upper()}] {decision_text}",
            task_id=task_id,
        )
        self._event(
            "team_decision",
            {
                "decision_id": decision.decision_id,
                "task_id": task_id,
                "proposer": proposer,
                "status": status,
                "text": decision_text[:200],
            },
        )
        return decision

    def vote_on_decision(self, decision_id: str, voter: str, vote: str) -> bool:
        """Registra un voto en una decision (approve/reject/abstain)."""
        for d in reversed(self._decisions):
            if d.decision_id == decision_id:
                d.votes[voter] = vote
                # Auto-resolve if enough votes
                approvals = sum(1 for v in d.votes.values() if v == "approve")
                rejections = sum(1 for v in d.votes.values() if v == "reject")
                if approvals >= 2 and d.status == "proposed":
                    d.status = "accepted"
                    d.resolved_at = datetime.now(timezone.utc).isoformat()
                elif rejections >= 2 and d.status == "proposed":
                    d.status = "rejected"
                    d.resolved_at = datetime.now(timezone.utc).isoformat()
                self._persist_decision(d)
                return True
        return False

    def get_decisions(
        self,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[TeamDecision]:
        """Lista decisiones con filtros."""
        filtered = self._decisions
        if task_id:
            filtered = [d for d in filtered if d.task_id == task_id]
        if status:
            filtered = [d for d in filtered if d.status == status]
        return filtered[-limit:]

    # ── Structured Team Context ───────────────────────────────

    def build_team_context(
        self,
        task_id: str,
        agent_id: str,
        role: str,
        include_decisions: bool = True,
        include_recent_comms: bool = True,
        max_chars: int = 2000,
    ) -> str:
        """Construye contexto estructurado del equipo para inyectar en prompts.

        Incluye: decisiones recientes, mensajes relevantes, estado del standup.
        """
        sections: list[str] = []

        # Recent decisions for this task
        if include_decisions:
            decisions = self.get_decisions(task_id=task_id, limit=5)
            if decisions:
                lines = ["Decisiones del equipo:"]
                for d in decisions:
                    icon = {"accepted": "OK", "rejected": "NO", "proposed": "??"}.get(
                        d.status, "??"
                    )
                    lines.append(
                        f"- [{icon}] {d.decision_text[:120]} (por {d.proposer})"
                    )
                sections.append("\n".join(lines))

        # Recent relevant messages from mailbox
        if include_recent_comms:
            recent_msgs = self.mailbox.list_messages(recipient=agent_id)[-5:]
            if not recent_msgs:
                recent_msgs = self.mailbox.list_messages(recipient=role)[-5:]
            if recent_msgs:
                lines = ["Mensajes recientes:"]
                for msg in recent_msgs[-3:]:
                    sender = getattr(msg, "sender", "?")
                    subject = str(getattr(msg, "subject", ""))[:80]
                    body_preview = str(getattr(msg, "body", ""))[:100]
                    lines.append(f"- De {sender}: {subject} — {body_preview}")
                sections.append("\n".join(lines))

        # Agent's own recent memory
        recent_memory = self.memory.recent(
            agent_id,
            limit=3,
            exclude_kinds={"meeting_minutes"},
        )
        if recent_memory:
            lines = ["Tu contexto reciente:"]
            for item in recent_memory:
                text = item.content.strip().replace("\n", " ")[:100]
                lines.append(f"- [{item.kind}] {text}")
            sections.append("\n".join(lines))

        result = "\n\n".join(sections)
        return result[:max_chars] if len(result) > max_chars else result

    # ── Handoff Context ───────────────────────────────────────

    def build_handoff_context(
        self,
        from_agent: str,
        from_role: str,
        to_role: str,
        task_id: str,
        output_summary: str = "",
    ) -> str:
        """Construye contexto de handoff entre agentes.

        Usado cuando una fase completa y entrega a la siguiente.
        """
        lines = [f"Handoff de {from_role} a {to_role}:"]
        if output_summary:
            lines.append(f"Resumen del trabajo completado: {output_summary[:500]}")

        # Include relevant decisions
        decisions = self.get_decisions(task_id=task_id, status="accepted", limit=3)
        if decisions:
            lines.append("Decisiones vigentes:")
            for d in decisions:
                lines.append(f"- {d.decision_text[:120]}")

        # From-agent's recent findings
        recent = self.memory.recent(
            from_agent, limit=3, exclude_kinds={"meeting_minutes"}
        )
        if recent:
            lines.append(f"Contexto de {from_role}:")
            for item in recent:
                text = item.content.strip().replace("\n", " ")[:120]
                lines.append(f"- {text}")

        return "\n".join(lines)

    # ── Internal ──────────────────────────────────────────────

    def _standup_line(self, participant: MeetingParticipant) -> tuple[str, bool]:
        recent = self.memory.recent(
            participant.agent_id,
            limit=4,
            exclude_kinds={"meeting_minutes"},
        )
        if not recent:
            return (
                f"- {participant.role}/{participant.agent_id}: sin novedades registradas.",
                False,
            )

        chunks = []
        useful_chunks = 0
        for item in recent:
            text = item.content.strip().replace("\n", " ")
            text = text[:120]
            chunks.append(f"[{item.kind}] {text}")
            if self._is_useful_meeting_item(item.kind, text):
                useful_chunks += 1

        return f"- {participant.role}/{participant.agent_id}: " + " | ".join(
            chunks
        ), useful_chunks > 0

    @staticmethod
    def _classify_meeting(topic: str, task_id: str | None = None) -> str:
        topic_lower = topic.lower()
        if (
            task_id
            or "event " in topic_lower
            or "blocked" in topic_lower
            or "failed" in topic_lower
        ):
            return "actionable"
        return "informational"

    @staticmethod
    def _is_useful_meeting_item(kind: str, text: str) -> bool:
        text_lower = text.strip().lower()
        if not text_lower:
            return False
        if kind == "meeting_minutes":
            return False
        weak_markers = (
            "processed prompt",
            "sin novedades",
            "ok",
            "done",
            "completed",
        )
        if any(text_lower == marker for marker in weak_markers):
            return False
        if "processed prompt" in text_lower:
            return False
        return len(text_lower) >= 12

    def _persist_decision(self, decision: TeamDecision) -> None:
        if self._decisions_path is None:
            return
        record = {
            "decision_id": decision.decision_id,
            "task_id": decision.task_id,
            "proposer": decision.proposer,
            "decision_text": decision.decision_text,
            "context": decision.context[:500],
            "status": decision.status,
            "votes": decision.votes,
            "created_at": decision.created_at,
            "resolved_at": decision.resolved_at,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._decisions_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except OSError:
            pass

    def _load_decisions(self) -> None:
        if self._decisions_path is None or not self._decisions_path.exists():
            return
        try:
            raw = self._decisions_path.read_text(encoding="utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    self._decisions.append(
                        TeamDecision(
                            decision_id=record.get("decision_id", ""),
                            task_id=record.get("task_id", ""),
                            proposer=record.get("proposer", ""),
                            decision_text=record.get("decision_text", ""),
                            context=record.get("context", ""),
                            status=record.get("status", "proposed"),
                            votes=record.get("votes", {}),
                            created_at=record.get("created_at", ""),
                            resolved_at=record.get("resolved_at", ""),
                        )
                    )
                except (json.JSONDecodeError, TypeError):
                    continue
        except OSError:
            pass

    def _event(self, event_type: str, payload: dict) -> None:
        if self.event_logger is not None:
            self.event_logger.emit(event_type, payload)
