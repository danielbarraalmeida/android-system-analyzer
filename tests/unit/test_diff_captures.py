"""Unit tests for diff_captures helpers."""

from __future__ import annotations

import pytest

import diff_captures as dc


pytestmark = pytest.mark.unit


def _capture(elements: list[dict]) -> dict:
    return {
        "capture": {"capture_id": "cap_x", "timestamp_utc": "2026-05-08T00:00:00+00:00"},
        "context": {"package_name": "pkg", "activity_name": "act"},
        "elements": elements,
    }


def test_build_diff_added_removed_modified() -> None:
    """Compares two captures: element /b is unchanged, /a has its text modified ('old' → 'new'), and /c is brand-new. Verifies added_count=1, removed_count=0, modified_count=1, and that the change record holds both the before and after values."""
    before = _capture([
        {"path": "/a", "id": "1", "text": "old", "class_name": "A", "bounds": [0, 0, 1, 1]},
        {"path": "/b", "id": "2", "text": "stable", "class_name": "B", "bounds": [0, 0, 1, 1]},
    ])
    after = _capture([
        {"path": "/a", "id": "1a", "text": "new", "class_name": "A", "bounds": [0, 0, 1, 1]},
        {"path": "/b", "id": "2",  "text": "stable", "class_name": "B", "bounds": [0, 0, 1, 1]},
        {"path": "/c", "id": "3",  "text": "added", "class_name": "C", "bounds": [0, 0, 1, 1]},
    ])
    diff = dc.build_diff(before, after)

    assert diff["summary"]["added_count"]    == 1
    assert diff["summary"]["removed_count"]  == 0
    assert diff["summary"]["modified_count"] == 1
    assert diff["added_paths"]   == ["/c"]
    assert diff["removed_paths"] == []
    assert diff["modified"][0]["path"] == "/a"
    assert "text" in diff["modified"][0]["changes"]
    assert diff["modified"][0]["changes"]["text"]["before"] == "old"
    assert diff["modified"][0]["changes"]["text"]["after"]  == "new"


def test_build_diff_no_changes() -> None:
    """When the same capture is diffed against itself, no elements should be added, removed, or modified — the diff must be entirely empty."""
    cap = _capture([{"path": "/a", "id": "1", "text": "x", "class_name": "A", "bounds": [0, 0, 1, 1]}])
    diff = dc.build_diff(cap, cap)
    assert diff["summary"]["added_count"]    == 0
    assert diff["summary"]["removed_count"]  == 0
    assert diff["summary"]["modified_count"] == 0
    assert diff["modified"] == []


def test_to_markdown_contains_meta_and_summary_sections() -> None:
    """The Markdown diff report must have a top-level '# Capture Diff Report' heading plus '## Meta' and '## Summary' sub-sections so the document renders with proper structure."""
    diff = dc.build_diff(_capture([]), _capture([]))
    md = dc.to_markdown(diff)
    assert "# Capture Diff Report" in md
    assert "## Meta"    in md
    assert "## Summary" in md
