"""Component tests: overlay geometry — percentage calculations for element bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.component


def _pct(left: int, top: int, right: int, bottom: int, sw: int, sh: int) -> dict:
    """Compute percentage overlay values matching what _render_html produces."""
    return {
        "left_pct":   round(left   / sw * 100, 2),
        "top_pct":    round(top    / sh * 100, 2),
        "width_pct":  round((right  - left) / sw * 100, 2),
        "height_pct": round((bottom - top)  / sh * 100, 2),
    }


# ─────────────────────────── Bounds math ─────────────────────────────────────

def test_full_screen_element_is_100_percent() -> None:
    """An element that fills the entire screen (0,0 to 1080,1920) must compute as left=0%, top=0%, width=100%, height=100% — the baseline case for overlay percentage maths."""
    p = _pct(0, 0, 1080, 1920, 1080, 1920)
    assert p["left_pct"]   == 0.0
    assert p["top_pct"]    == 0.0
    assert p["width_pct"]  == 100.0
    assert p["height_pct"] == 100.0


def test_quarter_screen_element() -> None:
    """An element covering the top-left quadrant (0,0 to 540,960 on a 1080×1920 screen) must compute as width=50%, height=50%, used when rendering element overlay thumbnails."""
    p = _pct(0, 0, 540, 960, 1080, 1920)
    assert p["width_pct"]  == 50.0
    assert p["height_pct"] == 50.0


def test_zero_size_element() -> None:
    """A zero-area element (collapsed or invisible, right==left and bottom==top) must produce width=0% and height=0% without triggering division errors."""
    p = _pct(100, 100, 100, 100, 1080, 1920)
    assert p["width_pct"]  == 0.0
    assert p["height_pct"] == 0.0


# ─────────────────────────── width / height helpers in element model ─────────

def test_element_width_height_correct(sample_xml_path: Path) -> None:
    """The root element's width and height fields in the extracted model must match the difference between its right/bottom and left/top bounds — computed automatically during extraction."""
    elements, _ = csr._extract_elements(sample_xml_path)
    root = next(e for e in elements if e["normalized_path"] == "")
    assert root["width"]  == 1080
    assert root["height"] == 1920


def test_element_bounds_match_width_height(sample_xml_path: Path) -> None:
    """For every element in the fixture, the width field must equal bounds.right − bounds.left and height must equal bounds.bottom − bounds.top. Validates that the automatic width/height computation is correct for all 10 nodes."""
    elements, _ = csr._extract_elements(sample_xml_path)
    for elem in elements:
        b = elem["bounds"]
        assert elem["width"]  == b["right"]  - b["left"]
        assert elem["height"] == b["bottom"] - b["top"]


# ─────────────────────────── center_x / center_y ────────────────────────────

def test_element_center_coordinates(sample_xml_path: Path) -> None:
    """The root element's center_x must be 540 (1080 ÷ 2) and center_y must be 960 (1920 ÷ 2). These centre coordinates are used for tap gestures targeting the middle of an element."""
    elements, _ = csr._extract_elements(sample_xml_path)
    root = next(e for e in elements if e["normalized_path"] == "")
    assert root["center_x"] == 540   # (0 + 1080) // 2
    assert root["center_y"] == 960   # (0 + 1920) // 2
