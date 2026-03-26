"""Learning Registry - Capture failures, insights, and growth across projects and team."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from aiteam.persistence import AtomicFileWriter


type LearningCategory = Literal[
    "project_failure",
    "system_insight",
    "team_learning",
    "user_feedback",
    "architectural_decision",
    "incident_postmortem",
    "process_improvement",
]


class LearningRegistry:
    """Record failures, insights, and learnings across all dimensions."""

    def __init__(self, runtime_dir: Path) -> None:
        """Initialize learning registry.

        Args:
            runtime_dir: Directory for learning ledger storage.
        """
        incoming_path = Path(runtime_dir)
        if incoming_path.suffix.lower() == ".jsonl":
            self.runtime_dir = incoming_path.parent
            self.ledger_path = incoming_path
        else:
            self.runtime_dir = incoming_path
            self.ledger_path = self.runtime_dir / "learning_registry.jsonl"

        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        # Handle accidental directory creation at ledger file path.
        if self.ledger_path.exists() and self.ledger_path.is_dir():
            self.ledger_path = self.ledger_path / "learning_registry.jsonl"
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.ledger_path.exists():
            self.ledger_path.write_text("", encoding="utf-8")

    def record_learning(
        self,
        category: LearningCategory,
        title: str,
        description: str,
        impact: str,
        recommendation: str,
        tags: list[str] | None = None,
        project_id: str | None = None,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a learning, failure, or insight.

        Args:
            category: Type of learning (failure, insight, team, user_feedback, etc.)
            title: Short title of the learning
            description: Detailed description of what happened
            impact: What was the impact (cost, time, quality, team morale)
            recommendation: What should we do differently next time?
            tags: Optional tags for categorization (e.g., "routing", "performance")
            project_id: Optional project this relates to
            owner: Who discovered/logged this learning
            metadata: Additional context
        """
        now = datetime.now(timezone.utc)
        record = {
            "ts": now.isoformat(),
            "category": category,
            "title": title,
            "description": description,
            "impact": impact,
            "recommendation": recommendation,
            "tags": tags or [],
            "project_id": project_id,
            "owner": owner or "system",
            "status": "open",  # open, actionable, addressed, archived
            "metadata": metadata or {},
        }

        AtomicFileWriter.append_jsonl_with_checksum(self.ledger_path, record)

    def record_project_failure(
        self,
        title: str,
        error_message: str,
        what_happened: str,
        why_it_happened: str,
        impact: str,
        how_to_prevent: str,
        project_id: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Record a project failure for learning.

        Args:
            title: Failure title (e.g., "Atomic write race condition")
            error_message: The actual error from logs
            what_happened: Timeline of what occurred
            why_it_happened: Root cause analysis
            impact: Impact assessment (tests, users, data, time)
            how_to_prevent: Specific action to prevent recurrence
            project_id: Project this occurred in
            tags: Tags (e.g., "persistence", "critical")
        """
        description = f"""
**Error**: {error_message}

**Timeline**:
{what_happened}

**Root Cause**:
{why_it_happened}

**Impact**:
{impact}
"""
        self.record_learning(
            category="project_failure",
            title=title,
            description=description,
            impact=impact,
            recommendation=how_to_prevent,
            tags=tags or [],
            project_id=project_id,
            owner="system",
        )

    def record_system_insight(
        self,
        title: str,
        observation: str,
        implication: str,
        suggested_action: str,
        tags: list[str] | None = None,
    ) -> None:
        """Record an insight about how the system works or should work.

        Args:
            title: Insight title (e.g., "Atomic writes reduce corruption by 99%")
            observation: What we observed
            implication: What this means for the system
            suggested_action: What to do about it
            tags: Tags (e.g., "performance", "reliability")
        """
        description = f"""
**Observation**: {observation}

**Implication**: {implication}
"""
        self.record_learning(
            category="system_insight",
            title=title,
            description=description,
            impact="System understanding improved",
            recommendation=suggested_action,
            tags=tags or [],
        )

    def record_team_learning(
        self,
        title: str,
        what_we_learned: str,
        how_we_discovered_it: str,
        how_to_apply: str,
        team_member: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Record something the team learned (skill, process, pattern).

        Args:
            title: Learning title (e.g., "Exponential backoff is better than linear")
            what_we_learned: Description of the learning
            how_we_discovered_it: How did we learn this?
            how_to_apply: How should we apply this going forward?
            team_member: Who led this learning?
            tags: Tags (e.g., "performance", "resilience")
        """
        description = f"""
**What We Learned**: {what_we_learned}

**Discovery Process**: {how_we_discovered_it}
"""
        self.record_learning(
            category="team_learning",
            title=title,
            description=description,
            impact="Team capability improved",
            recommendation=how_to_apply,
            tags=tags or [],
            owner=team_member,
        )

    def record_user_feedback(
        self,
        title: str,
        feedback: str,
        context: str,
        opportunity: str,
        from_user: str | None = None,
    ) -> None:
        """Record feedback from you (the user/stakeholder).

        Args:
            title: Feedback title (e.g., "Onboarding experience too long")
            feedback: The actual feedback
            context: Context where feedback came from
            opportunity: What opportunity does this represent?
            from_user: Who provided this (name/role)
        """
        description = f"""
**Feedback**: {feedback}

**Context**: {context}
"""
        self.record_learning(
            category="user_feedback",
            title=title,
            description=description,
            impact="User satisfaction / product direction",
            recommendation=opportunity,
            owner=from_user or "product-leadership",
        )

    def read_all(self) -> list[dict[str, Any]]:
        """Read all learning records.

        Returns:
            List of learning records.
        """
        return AtomicFileWriter.read_jsonl_with_dedup(self.ledger_path)

    def read_by_category(self, category: LearningCategory) -> list[dict[str, Any]]:
        """Get learnings by category.

        Args:
            category: Category to filter by.

        Returns:
            Records matching category.
        """
        records = self.read_all()
        return [r for r in records if r.get("category") == category]

    def read_by_project(self, project_id: str) -> list[dict[str, Any]]:
        """Get learnings for a specific project.

        Args:
            project_id: Project ID to filter by.

        Returns:
            Records for this project.
        """
        records = self.read_all()
        return [r for r in records if r.get("project_id") == project_id]

    def read_by_tag(self, tag: str) -> list[dict[str, Any]]:
        """Get learnings by tag.

        Args:
            tag: Tag to filter by.

        Returns:
            Records with this tag.
        """
        records = self.read_all()
        return [r for r in records if tag in r.get("tags", [])]

    def read_open_items(self) -> list[dict[str, Any]]:
        """Get all open/actionable learnings.

        Returns:
            Records with status=open or actionable.
        """
        records = self.read_all()
        return [r for r in records if r.get("status") in ("open", "actionable")]

    def mark_addressed(self, learning_title: str) -> None:
        """Mark a learning as addressed/resolved.

        Args:
            learning_title: Title of the learning to mark.
        """
        records = self.read_all()
        for record in records:
            if record.get("title") == learning_title:
                record["status"] = "addressed"
                record["addressed_at"] = datetime.now(timezone.utc).isoformat()
        self._rewrite_ledger(records)

    def summary(self) -> dict[str, Any]:
        """Get summary statistics of learnings.

        Returns:
            Summary with counts by category, top tags, open items.
        """
        records = self.read_all()

        categories: dict[str, int] = {}
        tags: dict[str, int] = {}
        statuses: dict[str, int] = {}

        for record in records:
            cat = record.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

            for tag in record.get("tags", []):
                tags[tag] = tags.get(tag, 0) + 1

            status = record.get("status", "unknown")
            statuses[status] = statuses.get(status, 0) + 1

        total_records = len(records)
        open_count = statuses.get("open", 0)
        addressed_count = statuses.get("addressed", 0)
        archived_count = statuses.get("archived", 0)

        return {
            "total_records": total_records,
            "by_category": categories,
            "by_tag": tags,
            "by_status": statuses,
            "top_tags": sorted(tags.items(), key=lambda x: x[1], reverse=True)[:5],
            "open_count": open_count,
            "addressed_count": addressed_count,
            # Compatibility aliases used by CLI/docs.
            "total": total_records,
            "open": open_count,
            "addressed": addressed_count,
            "archived": archived_count,
        }

    def _rewrite_ledger(self, records: list[dict[str, Any]]) -> None:
        """Rewrite entire ledger atomically (for status updates).

        Args:
            records: Updated records list.
        """
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=self.ledger_path.parent,
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp_path = Path(tmp.name)
            try:
                for record in records:
                    checksum = hashlib.sha256(
                        json.dumps(record, sort_keys=True).encode()
                    ).hexdigest()[:16]
                    record_copy = {**record, "_checksum": checksum}
                    tmp.write(json.dumps(record_copy, ensure_ascii=True) + "\n")
                tmp.flush()
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        try:
            tmp_path.replace(self.ledger_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def export_markdown(self) -> str:
        """Export learnings as markdown for documentation.

        Returns:
            Markdown formatted learnings.
        """
        records = self.read_all()
        summary = self.summary()

        md = "# Learning Registry Export\n\n"
        md += f"**Total Learnings**: {summary['total_records']}\n"
        md += f"**Open Items**: {summary['open_count']}\n"
        md += f"**Addressed**: {summary['addressed_count']}\n\n"

        # Group by category
        for category in [
            "project_failure",
            "system_insight",
            "team_learning",
            "user_feedback",
        ]:
            items = [r for r in records if r.get("category") == category]
            if items:
                md += f"## {category.upper()}\n\n"
                for item in items:
                    md += f"### {item.get('title')}\n"
                    md += f"- **Status**: {item.get('status')}\n"
                    md += f"- **Tags**: {', '.join(item.get('tags', []))}\n"
                    md += f"- **Description**: {item.get('description')}\n"
                    md += f"- **Recommendation**: {item.get('recommendation')}\n\n"

        return md
