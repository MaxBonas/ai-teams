"""Tests for _safe_truncate_output.

Four cases:
1. text fits within max_len  → returned unchanged
2. text too long, has AGENT-REPORT block → prose truncated, block preserved at end
3. text too long, no AGENT-REPORT block → plain truncation
4. AGENT-REPORT block alone exceeds max_len → block returned, possibly truncated
"""
from __future__ import annotations

import pytest

# Import the private helper directly — it's a module-level function, not
# gated behind a class, so the import is stable.
from aiteam.heartbeat.executor import _safe_truncate_output

_MARKER = "---AGENT-REPORT---"


class TestFits:
    def test_empty_string(self) -> None:
        assert _safe_truncate_output("", max_len=10) == ""

    def test_exactly_max_len(self) -> None:
        text = "x" * 100
        assert _safe_truncate_output(text, max_len=100) == text

    def test_short_text_with_report(self) -> None:
        text = f"intro\n\n{_MARKER}\nrole: engineer\nresult: done\n"
        assert _safe_truncate_output(text, max_len=4096) == text

    def test_does_not_mutate_short_text(self) -> None:
        text = "hello world"
        result = _safe_truncate_output(text, max_len=1000)
        assert result is text or result == text  # returned as-is (may be same object)


class TestTruncatedWithReport:
    def _make_text(self, prose_size: int, report: str = "") -> str:
        if not report:
            report = (
                f"{_MARKER}\n"
                "role: engineer\n"
                "result: done\n"
                "issue_status: done\n"
                "next_owner: reviewer\n"
                "tech_match: yes\n"
                "blocker: none\n"
                "evidence: src/main.py:1-50\n"
            )
        prose = "A" * prose_size + "\n\n"
        return prose + report

    def test_report_block_present_in_output(self) -> None:
        text = self._make_text(prose_size=8000)
        result = _safe_truncate_output(text, max_len=4096)
        assert _MARKER in result, "AGENT-REPORT marker must be preserved"

    def test_result_respects_max_len(self) -> None:
        text = self._make_text(prose_size=8000)
        result = _safe_truncate_output(text, max_len=4096)
        assert len(result) <= 4096

    def test_report_block_content_intact(self) -> None:
        text = self._make_text(prose_size=8000)
        result = _safe_truncate_output(text, max_len=4096)
        # The full block should appear verbatim after the marker
        block_start = text.rfind(_MARKER)
        block = text[block_start:]
        assert result.endswith(block), (
            "Full AGENT-REPORT block should appear verbatim at end of output"
        )

    def test_truncation_indicator_present(self) -> None:
        text = self._make_text(prose_size=8000)
        result = _safe_truncate_output(text, max_len=4096)
        assert "truncated" in result.lower(), "Should indicate truncation happened"

    def test_prose_is_truncated_not_dropped(self) -> None:
        """Some prose content should remain before the separator."""
        text = self._make_text(prose_size=8000)
        result = _safe_truncate_output(text, max_len=4096)
        # The very first character 'A' of the prose must still be present
        assert result.startswith("A"), "Some prose should appear before the truncation marker"

    def test_multiple_report_markers_uses_last(self) -> None:
        """If there are multiple markers (e.g., quoted old report + new report),
        the LAST one is used as the anchor for preservation."""
        old_block = f"{_MARKER}\nrole: engineer\nresult: old\n"
        new_block = f"{_MARKER}\nrole: engineer\nresult: new\nfinal: yes\n"
        prose = "B" * 8000
        text = prose + "\n\n" + old_block + "\n\n" + new_block
        result = _safe_truncate_output(text, max_len=4096)
        # The new block (last marker) should be preserved
        assert "result: new" in result
        assert "final: yes" in result


class TestTruncatedWithoutReport:
    def test_plain_truncation_at_max_len(self) -> None:
        text = "Z" * 8000
        result = _safe_truncate_output(text, max_len=4096)
        assert len(result) == 4096

    def test_plain_truncation_uses_prefix(self) -> None:
        text = "Z" * 8000
        result = _safe_truncate_output(text, max_len=4096)
        assert result == text[:4096]

    def test_no_truncation_marker_added(self) -> None:
        """Without a report block, no truncation indicator is injected."""
        text = "Z" * 8000
        result = _safe_truncate_output(text, max_len=4096)
        assert _MARKER not in result


class TestBlockAloneExceedsLimit:
    def test_returns_block_prefix_when_block_too_big(self) -> None:
        """When the AGENT-REPORT block itself exceeds max_len, we return the
        block truncated to max_len rather than the prose prefix."""
        separator = "\n\n[…output truncated…]\n\n"
        block_content = "role: engineer\nresult: done\n" + "X" * 8000
        block = f"{_MARKER}\n{block_content}"
        prose = "Short prose. "
        text = prose + block
        max_len = 200  # smaller than the block
        result = _safe_truncate_output(text, max_len=max_len)
        assert len(result) <= max_len
        assert result.startswith(_MARKER), (
            "When block alone exceeds limit, result should start with the marker"
        )

    def test_block_alone_truncated_exact_length(self) -> None:
        block = f"{_MARKER}\n" + "Y" * 5000
        prose = "P" * 100
        text = prose + "\n" + block
        max_len = 500
        result = _safe_truncate_output(text, max_len=max_len)
        assert len(result) == max_len

    def test_block_alone_no_separator_injected(self) -> None:
        """When falling back to block-only, we must NOT inject the separator
        (it would push us over the limit)."""
        block = f"{_MARKER}\n" + "Y" * 5000
        text = "intro " + block
        max_len = 200
        result = _safe_truncate_output(text, max_len=max_len)
        # Separator would start with '\n\n[…' — should not appear in result
        assert "[…" not in result
