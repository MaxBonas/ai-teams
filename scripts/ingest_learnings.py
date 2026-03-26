#!/usr/bin/env python
"""
Learning Registry Ingestion Script
====================================

Exports learning records from learning_registry.jsonl and prepares them for
NotebookLM ingestion to the "Learnings & Insights" notebook.

This script:
1. Reads the learning registry file
2. Generates a formatted summary with statistics
3. Exports records by category
4. Provides output ready for manual copy-paste to NotebookLM

Run this daily as part of the morning ingestion ritual (10:15 AM UTC).

Usage:
    python scripts/ingest_learnings.py
    python scripts/ingest_learnings.py --format markdown --output /tmp/learnings_export.md
    python scripts/ingest_learnings.py --format json > /tmp/learnings_export.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from aiteam.learning_registry import LearningRegistry


def get_registry() -> LearningRegistry:
    """Initialize and return the learning registry."""
    # Use default runtime directory
    runtime_dir = Path(__file__).parent.parent / "runtime"
    registry = LearningRegistry(runtime_dir)
    return registry


def export_for_notebook(registry: LearningRegistry, days: int = 7) -> str:
    """Export learning records in format optimized for NotebookLM."""
    output = []
    
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    
    output.append(f"# Learning Registry Export")
    output.append(f"**Exported**: {date_str} at {now.strftime('%H:%M:%S')} UTC")
    output.append("")
    
    # Get all learnings
    all_learnings = registry.read_all()
    if not all_learnings:
        output.append("*No learnings recorded yet.*")
        return "\n".join(output)
    
    # Generate summary statistics
    summary = registry.summary()
    output.append(f"## Summary")
    output.append(f"- **Total Learnings**: {summary['total']}")
    output.append(f"- **Open Items**: {summary['open']}")
    output.append(f"- **Addressed**: {summary['addressed']}")
    output.append(f"- **Archived**: {summary['archived']}")
    output.append("")
    
    # Group by category
    output.append(f"## By Category")
    categories = {}
    for learning in all_learnings:
        category = learning.get("category", "UNKNOWN")
        if category not in categories:
            categories[category] = []
        categories[category].append(learning)
    
    for category in sorted(categories.keys()):
        items = categories[category]
        output.append(f"### {category} ({len(items)} items)")
        for item in items:
            status = item.get("status", "unknown")
            title = item.get("title", "Untitled")
            output.append(f"- [{status.upper()}] {title}")
        output.append("")
    
    # Recent learnings (last 7 days)
    output.append(f"## Recent Learnings (Last {days} Days)")
    cutoff = (now - timedelta(days=days)).isoformat()
    recent = [l for l in all_learnings if l.get("created_at", "") >= cutoff]
    
    if recent:
        for item in recent[-10:]:  # Last 10
            category = item.get("category", "UNKNOWN")
            title = item.get("title", "Untitled")
            status = item.get("status", "unknown")
            output.append(f"- **{category}**: {title} [*{status}*]")
    else:
        output.append("*No recent learnings.*")
    output.append("")
    
    # Open action items
    output.append(f"## Open Action Items")
    open_items = registry.read_open_items()
    if open_items:
        for item in open_items:
            title = item.get("title", "Untitled")
            category = item.get("category", "UNKNOWN")
            priority = item.get("priority", "medium")
            output.append(f"- [{priority.upper()}] {category}: {title}")
    else:
        output.append("*No open items.*")
    output.append("")
    
    return "\n".join(output)


def export_json(registry: LearningRegistry) -> str:
    """Export all learning records as JSON."""
    all_learnings = registry.read_all()
    export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_learnings),
        "learnings": all_learnings,
    }
    return json.dumps(export, indent=2, default=str)


def export_markdown(registry: LearningRegistry) -> str:
    """Export full learning records as markdown."""
    return registry.export_markdown()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Export learning records for NotebookLM ingestion"
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "json"],
        default="text",
        help="Export format (default: text)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Include learnings from last N days (default: 7)",
    )
    
    args = parser.parse_args()
    
    try:
        registry = get_registry()
    except Exception as e:
        print(f"❌ Error loading learning registry: {e}", file=sys.stderr)
        return 1
    
    # Generate export
    if args.format == "json":
        content = export_json(registry)
    elif args.format == "markdown":
        content = export_markdown(registry)
    else:  # text (default)
        content = export_for_notebook(registry, days=args.days)
    
    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content)
        print(f"✅ Learning export written to {output_path}")
    else:
        print(content)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
