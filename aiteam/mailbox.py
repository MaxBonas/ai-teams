from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class MailMessage:
    timestamp: str
    sender: str
    recipient: str
    subject: str
    body: str
    task_id: str | None = None
    message_id: str = ""


class Mailbox:
    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._lock = threading.RLock()
        self._read_set: set[str] = set()  # message_ids marked as read
        self._msg_counter = 0
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.storage_path.exists():
            self.storage_path.write_text("", encoding="utf-8")

    def send(
        self,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
        task_id: str | None = None,
    ) -> None:
        with self._lock:
            self._msg_counter += 1
            msg_id = f"msg-{self._msg_counter}-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        msg = MailMessage(
            timestamp=datetime.now(timezone.utc).isoformat(),
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            task_id=task_id,
            message_id=msg_id,
        )
        with self._lock:
            with self.storage_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(msg), ensure_ascii=True) + "\n")
                f.flush()

    def list_messages(self, recipient: str | None = None) -> list[MailMessage]:
        with self._lock:
            raw = self.storage_path.read_text(encoding="utf-8")
        items: list[MailMessage] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            if recipient and data.get("recipient") not in (recipient, "broadcast"):
                continue
            try:
                items.append(MailMessage(**data))
            except TypeError:
                continue
        return items

    def mark_read(self, message_id: str) -> None:
        """Mark a message as read."""
        with self._lock:
            self._read_set.add(message_id)

    def mark_read_bulk(self, message_ids: list[str]) -> None:
        """Mark multiple messages as read."""
        with self._lock:
            self._read_set.update(message_ids)

    def is_read(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._read_set

    def unread_messages(self, recipient: str) -> list[MailMessage]:
        """Get unread messages for a recipient."""
        messages = self.list_messages(recipient=recipient)
        with self._lock:
            return [m for m in messages if m.message_id and m.message_id not in self._read_set]

    def unread_count(self, recipient: str) -> int:
        return len(self.unread_messages(recipient))

    def inbox_query(
        self,
        recipient: str,
        *,
        unread_only: bool = False,
        sender: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[MailMessage]:
        """Query inbox with filters."""
        if unread_only:
            messages = self.unread_messages(recipient)
        else:
            messages = self.list_messages(recipient=recipient)
        if sender:
            messages = [m for m in messages if m.sender == sender]
        if task_id:
            messages = [m for m in messages if m.task_id == task_id]
        return messages[-limit:]
