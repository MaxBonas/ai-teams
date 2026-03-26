import tempfile
import unittest
from pathlib import Path

from aiteam.agent_session import ThreadStore
from aiteam.communication import MeetingParticipant, TeamCommunicator
from aiteam.mailbox import Mailbox
from aiteam.memory import AgentMemoryStore
from aiteam.observability import EventLogger


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
            self.assertIn("Meeting Kind", minutes)
            messages = mailbox.list_messages(recipient="lead-1")
            self.assertTrue(messages)
            self.assertIn("Sync meeting", messages[-1].subject)
            self.assertEqual(messages[-1].kind, "informational")

    def test_informational_meeting_skips_when_signal_is_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mailbox = Mailbox(base / "mailbox.jsonl")
            memory = AgentMemoryStore(base / "memory")
            events = EventLogger(base / "runtime")

            comms = TeamCommunicator(
                mailbox=mailbox, memory=memory, event_logger=events
            )
            minutes = comms.run_sync_meeting(
                topic="Round 1",
                participants=[MeetingParticipant(agent_id="lead-1", role="team_lead")],
                meeting_kind="informational",
            )

            self.assertEqual(minutes, "")
            self.assertEqual(mailbox.list_messages(), [])
            records = events.events_windowed(hours=1)
            self.assertTrue(
                any(
                    item.get("event_type") == "sync_meeting_skipped" for item in records
                )
            )

    def test_actionable_meeting_emits_kind_and_persists_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            mailbox = Mailbox(base / "mailbox.jsonl")
            memory = AgentMemoryStore(base / "memory")
            events = EventLogger(base / "runtime")

            comms = TeamCommunicator(
                mailbox=mailbox, memory=memory, event_logger=events
            )
            minutes = comms.run_sync_meeting(
                topic="Event task_failed @ T-1 reason=boom",
                participants=[MeetingParticipant(agent_id="lead-1", role="team_lead")],
                task_id="T-1",
                meeting_kind="actionable",
            )

            self.assertIn("Meeting Kind: actionable", minutes)
            messages = mailbox.list_messages(recipient="lead-1")
            self.assertTrue(messages)
            recent = memory.recent("lead-1", limit=1)
            self.assertTrue(recent)
            self.assertEqual(recent[-1].kind, "meeting_minutes")
            self.assertIn("actionable", recent[-1].tags or [])
            records = events.events_windowed(hours=1)
            meeting_events = [
                item for item in records if item.get("event_type") == "sync_meeting"
            ]
            self.assertTrue(meeting_events)
            payload = meeting_events[-1].get("payload", {}) or {}
            self.assertEqual(payload.get("meeting_kind"), "actionable")

    def test_mailbox_can_mark_message_consumed_and_filter_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mailbox = Mailbox(Path(tmp) / "mailbox.jsonl")
            mailbox.send(
                "team_lead", "eng-1", "Do this", "Implement 2FA", kind="actionable"
            )
            mailbox.send("system", "eng-1", "FYI", "Status note", kind="informational")

            actionable = mailbox.inbox_query("eng-1", actionable_only=True)
            self.assertEqual(len(actionable), 1)
            self.assertEqual(actionable[0].subject, "Do this")

            mailbox.mark_consumed(actionable[0].message_id, consumed_by="eng-1")
            reloaded = mailbox.list_messages(recipient="eng-1")
            consumed = next(msg for msg in reloaded if msg.subject == "Do this")
            self.assertTrue(consumed.consumed)
            self.assertEqual(consumed.consumed_by, "eng-1")

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
            memory.remember(
                "lead-1", "team_lead", "meeting_minutes", "Meeting Topic: Round 0"
            )
            memory.remember("lead-1", "team_lead", "note", "Lead prepared release plan")

            comms = TeamCommunicator(mailbox=mailbox, memory=memory)
            minutes = comms.run_sync_meeting(
                topic="Daily",
                participants=[MeetingParticipant(agent_id="lead-1", role="team_lead")],
            )

            self.assertIn("[note]", minutes)
            self.assertNotIn("[meeting_minutes]", minutes)

    def test_memory_recent_and_relevant_are_isolated_by_project_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemoryStore(Path(tmp) / "memory")
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="task_success",
                content="Implemented auth with JWT and 2FA",
                task_id="A-1",
                project_key="project-a",
            )
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="task_success",
                content="Implemented shopping cart sync",
                task_id="B-1",
                project_key="project-b",
            )

            recent_a = memory.recent("eng-1", limit=5, project_key="project-a")
            relevant_b = memory.relevant(
                "eng-1",
                "shopping cart",
                limit=5,
                project_key="project-b",
            )

            self.assertEqual(len(recent_a), 1)
            self.assertEqual(recent_a[0].project_key, "project-a")
            self.assertEqual(len(relevant_b), 1)
            self.assertEqual(relevant_b[0].project_key, "project-b")

    def test_relevant_across_agents_isolated_by_project_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = AgentMemoryStore(Path(tmp) / "memory")
            memory.remember(
                agent_id="eng-1",
                role="engineer",
                kind="task_success",
                content="Redis rate limiting implemented",
                task_id="A-1",
                project_key="project-a",
            )
            memory.remember(
                agent_id="eng-2",
                role="engineer",
                kind="task_success",
                content="Redis cache invalidation implemented",
                task_id="B-1",
                project_key="project-b",
            )

            results = memory.relevant_across_agents(
                query="Redis implemented",
                project_key="project-a",
                limit=5,
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].project_key, "project-a")

    def test_conversation_thread_compacts_old_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ThreadStore(Path(tmp) / "runtime")
            thread = store.get_thread("eng-1", "project-a")
            for idx in range(15):
                thread.append_turn(role="user", content=f"turn-{idx}", source="task")
            store.save_thread(thread)

            loaded = store.get_thread("eng-1", "project-a")
            self.assertLessEqual(len(loaded.turns), 9)
            self.assertEqual(loaded.turns[0].source, "summary")
            self.assertIn("Resumen de", loaded.turns[0].content)

    def test_conversation_thread_skips_duplicate_consecutive_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ThreadStore(Path(tmp) / "runtime")
            thread = store.get_thread("eng-1", "project-a")
            thread.append_turn("user", "same content", source="task", task_id="T-1")
            thread.append_turn("user", "same content", source="task", task_id="T-1")
            store.save_thread(thread)

            loaded = store.get_thread("eng-1", "project-a")
            self.assertEqual(len(loaded.turns), 1)

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
