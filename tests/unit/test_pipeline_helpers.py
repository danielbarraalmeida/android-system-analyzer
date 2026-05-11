"""Unit tests for run_capture_pipeline helpers (no ADB, no subprocess)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import run_capture_pipeline as rcp


pytestmark = pytest.mark.unit


# ─────────────────────────── _list_capture_dirs ──────────────────────────────

def test_list_capture_dirs_empty_if_not_exists(tmp_path: Path) -> None:
    """Returns an empty list when the captures root directory does not yet exist — safe first-run behaviour so the pipeline doesn't crash on a fresh installation."""
    missing = tmp_path / "does_not_exist"
    assert rcp._list_capture_dirs(missing) == []


def test_list_capture_dirs_sorted_by_mtime(tmp_path: Path) -> None:
    """Capture directories must be returned sorted by modification time (oldest first) so the most recent capture is always at the end of the list — used to find the diff baseline."""
    import time
    d1 = tmp_path / "cap_a"
    d1.mkdir()
    time.sleep(0.01)
    d2 = tmp_path / "cap_b"
    d2.mkdir()
    result = rcp._list_capture_dirs(tmp_path)
    assert result == [d1, d2]


def test_list_capture_dirs_ignores_files(tmp_path: Path) -> None:
    """Regular files in the captures root (e.g. README.md) must be ignored — only subdirectories represent capture snapshots."""
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    d = tmp_path / "cap_a"
    d.mkdir()
    result = rcp._list_capture_dirs(tmp_path)
    assert result == [d]


# ─────────────────────────── _find_previous_capture ─────────────────────────

def test_find_previous_capture_returns_last_excluding_current(tmp_path: Path) -> None:
    """With three captures A, B, C where C is the current one, the previous capture must be B — the most recent baseline available for generating a diff report."""
    import time
    d1 = tmp_path / "cap_a"; d1.mkdir()
    time.sleep(0.01)
    d2 = tmp_path / "cap_b"; d2.mkdir()
    time.sleep(0.01)
    d3 = tmp_path / "cap_c"; d3.mkdir()
    assert rcp._find_previous_capture(tmp_path, d3) == d2


def test_find_previous_capture_no_previous(tmp_path: Path) -> None:
    """When only one capture exists and it is the current one, the function must return None — the diff step is then skipped gracefully without an error."""
    d = tmp_path / "cap_a"; d.mkdir()
    assert rcp._find_previous_capture(tmp_path, d) is None


# ─────────────────────────── _load_snapshot ──────────────────────────────────

def test_load_snapshot_reads_json(tmp_path: Path) -> None:
    """Reads the screen-snapshot.json file from inside a capture directory and returns its parsed contents as a Python dictionary."""
    capture_dir = tmp_path / "cap_x"
    capture_dir.mkdir()
    data = {"elements": [], "summary": {"element_count": 0}}
    (capture_dir / "screen-snapshot.json").write_text(
        json.dumps(data), encoding="utf-8"
    )
    loaded = rcp._load_snapshot(capture_dir)
    assert loaded == data


# ─────────────────────────── _write_text ─────────────────────────────────────

def test_write_text_adds_trailing_newline(tmp_path: Path) -> None:
    """Text content without a trailing newline gets one added before writing — follows the standard POSIX convention that text files end with a newline."""
    f = tmp_path / "out.txt"
    rcp._write_text(f, "hello")
    assert f.read_text(encoding="utf-8") == "hello\n"


def test_write_text_does_not_double_newline(tmp_path: Path) -> None:
    """Text content that already ends with a newline must not receive a second one — prevents an accumulating blank line at the end of report files."""
    f = tmp_path / "out.txt"
    rcp._write_text(f, "hello\n")
    assert f.read_text(encoding="utf-8") == "hello\n"
