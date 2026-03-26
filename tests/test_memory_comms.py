import tempfile
import unittest
from pathlib import Path

from aiteam.communication import MeetingParticipant, TeamCommunicator
from aiteam.mailbox import Mailbox
from aiteam.memory import AgentMemoryStore


class MemoryAndCommsTests(unittest.TestCase):
    def test_memory_relevance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemoryStore(Path(tmp) / "memory")
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="task_success",
                content="Implemented router fallback with budget checks",
                task_id="T-1",
                tags=["router", "budget"],
            )
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="note",
                content="Updated UI palette",
                task_id="T-2",
                tags=["frontend"],
            )

            relevant = memory.relevant("eng-1", "router budget", limit=2)
            self.assertTrue(relevant)
            self.assertIn("router", relevant[0].content.lower())

    def test_sync_meeting_writes_mail_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mailbox = Mailbox(base / "mailbox.jsonl")
            memory = AgentMemoryStore(base / "memory")
            memory.remember("lead-1", "team_lead", "note", "Lead prepared plan")
            memory.remember("eng-1", "engineer", "note", "Engineer implemented patch")

            comms = TeamCommunicator(mailbox=mailbox, memory=memory)
            minutes = comms.run_sync_meeting(
                topic="Daily",
                participants=[
                    MeetingParticipant(agent_id="lead-1", role="team_lead"),
                    MeetingParticipant(agent_id="eng-1", role="engineer"),
                ],
            )

            self.assertIn("Meeting Topic", minutes)
            messages = mailbox.list_messages(recipient="lead-1")
            self.assertTrue(messages)
            self.assertIn("Sync meeting", messages[-1].subject)

    def test_memory_filters_exclude_meeting_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemoryStore(Path(tmp) / "memory")
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="meeting_minutes",
                content="router budget fallback notes",
                task_id="M-1",
            )
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="task_success",
                content="router fallback implemented with budget checks",
                task_id="T-1",
            )

            recent = memory.recent("eng-1", limit=5, exclude_kinds={"meeting_minutes"})
            self.assertTrue(recent)
            self.assertTrue(all(item.kind != "meeting_minutes" for item in recent))

            relevant = memory.relevant(
                "eng-1",
                "router budget",
                limit=5,
                exclude_kinds={"meeting_minutes"},
            )
            self.assertTrue(relevant)
            self.assertTrue(all(item.kind != "meeting_minutes" for item in relevant))

    def test_standup_line_skips_recursive_meeting_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mailbox = Mailbox(base / "mailbox.jsonl")
            memory = AgentMemoryStore(base / "memory")
            memory.remember("lead-1", "team_lead", "meeting_minutes", "Meeting Topic: Round 0")
            memory.remember("lead-1", "team_lead", "note", "Lead prepared release plan")

            comms = TeamCommunicator(mailbox=mailbox, memory=memory)
            minutes = comms.run_sync_meeting(
                topic="Daily",
                participants=[MeetingParticipant(agent_id="lead-1", role="team_lead")],
            )

            self.assertIn("[note]", minutes)
            self.assertNotIn("[meeting_minutes]", minutes)

    def test_mailbox_skips_invalid_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mailbox.jsonl"
            mailbox = Mailbox(path)
            mailbox.send(
                sender="lead-1",
                recipient="eng-1",
                subject="ok",
                body="valid",
                task_id="T-1",
            )
            with path.open("a", encoding="utf-8") as f:
                f.write("{broken-json\n")

            messages = mailbox.list_messages(recipient="eng-1")
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].subject, "ok")


if __name__ == "__main__":
    unittest.main()
