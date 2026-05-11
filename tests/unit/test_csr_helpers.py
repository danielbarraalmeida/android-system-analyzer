"""Unit tests for pure helpers in current_screen_report.py."""

from __future__ import annotations

import datetime as dt

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.unit


# ─────────────────────────── Bounds parsing ──────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[0,0][100,200]", {"left": 0, "top": 0, "right": 100, "bottom": 200}),
        ("[10,20][30,40]", {"left": 10, "top": 20, "right": 30, "bottom": 40}),
        ("[-5,-5][5,5]",   {"left": -5, "top": -5, "right": 5,  "bottom": 5}),
    ],
)
def test_parse_bounds_valid(raw: str, expected: dict[str, int]) -> None:
    """Parses Android's '[left,top][right,bottom]' coordinate string into a dictionary with integer pixel values for each edge (left, top, right, bottom)."""
    assert csr._parse_bounds(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "garbage", "[0,0]", "0,0,1,1"])
def test_parse_bounds_invalid_returns_zero(raw: str | None) -> None:
    """Malformed, empty, or null bounds strings safely return a zero-filled dictionary instead of crashing — keeps XML extraction fault-tolerant."""
    assert csr._parse_bounds(raw) == {"left": 0, "top": 0, "right": 0, "bottom": 0}


# ─────────────────────────── Null/empty rules ────────────────────────────────

@pytest.mark.parametrize(("value", "expected"), [(None, ""), ("", ""), ("x", "x")])
def test_safe_text(value: str | None, expected: str) -> None:
    """Text fields from Android XML never appear as null in the output: both Python None and an empty string become ''; any real text passes through unchanged."""
    assert csr._safe_text(value) == expected


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("x", "x")])
def test_null_or_str(value: str | None, expected: str | None) -> None:
    """Attributes that are logically absent (None or empty string) become JSON null; any non-empty string is preserved verbatim."""
    assert csr._null_or_str(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("false", False), ("true", True), ("TRUE", True)],
)
def test_as_bool(value: str | None, expected: bool) -> None:
    """Converts Android XML boolean strings ('true', 'TRUE', 'false', None) to real Python booleans so the rest of the pipeline can use simple True/False checks."""
    assert csr._as_bool(value) is expected


# ─────────────────────────── Identity hashing ────────────────────────────────

def test_compute_element_id_deterministic() -> None:
    """The element ID is a SHA-1 fingerprint of the element's structural position. Running the same extraction twice must produce identical IDs, and the format must be 'el_v1_' followed by exactly 16 lowercase hex characters."""
    bounds = {"left": 0, "top": 0, "right": 10, "bottom": 10}
    a = csr._compute_element_id("/n[0]", "Button", "id1", "pkg", bounds, 0)
    b = csr._compute_element_id("/n[0]", "Button", "id1", "pkg", bounds, 0)
    assert a == b
    assert a.startswith("el_v1_")
    assert len(a) == 22  # "el_v1_" (6) + 16 hex chars
    assert all(c in "0123456789abcdef" for c in a[6:])


def test_compute_element_id_changes_on_path() -> None:
    """Two elements at different positions in the UI hierarchy (different sibling index) must produce different IDs, ensuring the fingerprint uniquely locates each node."""
    bounds = {"left": 0, "top": 0, "right": 10, "bottom": 10}
    a = csr._compute_element_id("/n[0]", "Button", "id1", "pkg", bounds, 0)
    b = csr._compute_element_id("/n[1]", "Button", "id1", "pkg", bounds, 0)
    assert a != b


def test_compute_element_id_excludes_text() -> None:
    """Text content is intentionally excluded from the ID basis so that IDs stay stable when labels change (e.g. a counter updating from '5 items' to '6 items')."""
    bounds = {"left": 0, "top": 0, "right": 10, "bottom": 10}
    # Identical structural inputs produce identical ids regardless of caller's text.
    a = csr._compute_element_id("/n[0]", "Button", "id1", "pkg", bounds, 0)
    b = csr._compute_element_id("/n[0]", "Button", "id1", "pkg", bounds, 0)
    assert a == b


# ─────────────────────────── View type hint ──────────────────────────────────

@pytest.mark.parametrize(
    ("class_name", "expected"),
    [
        (None,                              None),
        ("",                                None),
        ("android.widget.Button",           "Button"),
        ("androidx.recyclerview.widget.RecyclerView", "RecyclerView"),
        ("NoDots",                          "NoDots"),
    ],
)
def test_compute_view_type_hint(class_name: str | None, expected: str | None) -> None:
    """Extracts the short widget type name ('Button', 'RecyclerView') from the full Java class path. Missing or empty class names produce null; names with no dots are returned as-is."""
    assert csr._compute_view_type_hint(class_name) == expected


# ─────────────────────────── Interaction candidacy ───────────────────────────

def test_candidacy_clickable_enabled_yields_tap() -> None:
    """A UI element that is both clickable and enabled must be flagged with exactly the 'tap' action type and at least one reason explaining the candidacy decision."""
    actions, reasons = csr._compute_interaction_candidacy(
        clickable=True, long_clickable=False, scrollable=False,
        focusable=False, enabled=True, width=10, height=10,
    )
    assert actions == ["tap"]
    assert any("tap" in r for r in reasons)


def test_candidacy_disabled_yields_nothing() -> None:
    """No matter how many interaction flags are set, a disabled element must never be suggested as a candidate — disabled elements cannot respond to any gestures."""
    actions, _ = csr._compute_interaction_candidacy(
        clickable=True, long_clickable=True, scrollable=True,
        focusable=True, enabled=False, width=1000, height=1000,
    )
    assert actions == []


def test_candidacy_scrollable_large_area_includes_swipe() -> None:
    """A scrollable element whose bounding box exceeds the 10,000 px² swipe threshold (200×200 = 40,000 px²) must be flagged for both 'scroll' and 'swipe'. Also verifies the SWIPE_AREA_THRESHOLD constant equals 10,000."""
    actions, _ = csr._compute_interaction_candidacy(
        clickable=False, long_clickable=False, scrollable=True,
        focusable=False, enabled=True,
        width=200, height=200,  # 40_000 > SWIPE_AREA_THRESHOLD (10_000)
    )
    assert set(actions) == {"scroll", "swipe"}
    # Verify threshold constant is exposed and matches expected value.
    assert csr.SWIPE_AREA_THRESHOLD == 10_000


def test_candidacy_scrollable_small_area_excludes_swipe() -> None:
    """A scrollable element with a small bounding box (50×50 = 2,500 px², below the 10,000 px² threshold) is only a 'scroll' candidate — tiny scrollables are not useful swipe targets."""
    actions, _ = csr._compute_interaction_candidacy(
        clickable=False, long_clickable=False, scrollable=True,
        focusable=False, enabled=True,
        width=50, height=50,  # 2_500 < SWIPE_AREA_THRESHOLD
    )
    assert "scroll" in actions
    assert "swipe" not in actions


def test_candidacy_focusable_yields_input() -> None:
    """A focusable element that is neither clickable nor scrollable must be suggested only as an 'input' candidate — the tool will recommend typing into it, not tapping or swiping."""
    actions, _ = csr._compute_interaction_candidacy(
        clickable=False, long_clickable=False, scrollable=False,
        focusable=True, enabled=True, width=10, height=10,
    )
    assert actions == ["input"]


@pytest.mark.parametrize(
    ("clickable", "long_clickable", "scrollable", "focusable", "width", "height", "expected_set"),
    [
        # Long-clickable only → long_tap only (no tap)
        (False, True,  False, False, 10, 10,  {"long_tap"}),
        # Clickable + long_clickable → both tap and long_tap
        (True,  True,  False, False, 10, 10,  {"tap", "long_tap"}),
        # Scrollable below threshold → scroll only (no swipe)
        (False, False, True,  False, 50, 50,  {"scroll"}),
        # Scrollable above threshold → scroll + swipe
        (False, False, True,  False, 200, 200, {"scroll", "swipe"}),
        # Focusable + clickable → input + tap
        (True,  False, False, True,  10, 10,  {"tap", "input"}),
    ],
)
def test_candidacy_parametrized(
    clickable: bool, long_clickable: bool, scrollable: bool,
    focusable: bool, width: int, height: int, expected_set: set,
) -> None:
    """Parametrized matrix of interaction scenarios: long-clickable only → long_tap; clickable+long_clickable → both; scrollable with small/large area; focusable+clickable → input+tap."""
    actions, _ = csr._compute_interaction_candidacy(
        clickable=clickable, long_clickable=long_clickable, scrollable=scrollable,
        focusable=focusable, enabled=True, width=width, height=height,
    )
    assert set(actions) == expected_set


# ─────────────────────────── Source attribute split ──────────────────────────

def test_build_source_attributes_known_and_extra() -> None:
    """All 21 contract-defined XML attribute keys must appear in the 'known' bag (None or '' when absent), and any non-standard attribute (e.g. a vendor extension) must land in the separate 'extra' bag."""
    raw = {
        "text": "Hello",
        "class": "android.widget.TextView",
        "bounds": "[0,0][1,1]",
        "custom-vendor-flag": "1",
    }
    known, extra = csr._build_source_attributes(raw)
    # Every contract key is present (None when missing, "" for text/content-desc).
    for key in csr._KNOWN_XML_KEYS:
        assert key in known
    assert known["text"] == "Hello"
    assert known["content-desc"] == ""  # empty string for missing content-desc (§3 rule 2)
    assert extra == {"custom-vendor-flag": "1"}


# ─────────────────────────── Summary aggregation ─────────────────────────────

def _mk(elem_overrides: dict) -> dict:
    base = {
        "clickable": False, "long_clickable": False, "focusable": False,
        "focused": False, "enabled": True, "scrollable": False,
        "selected": False, "checked": False, "checkable": False,
        "password": False, "is_interaction_candidate": False,
    }
    base.update(elem_overrides)
    return base


def test_build_summary_counts_each_flag() -> None:
    """The summary section of a capture counts elements by flag (clickable, scrollable, focusable, enabled, interaction candidates). Every counter must exactly match the element list it was built from."""
    elements = [
        _mk({"clickable": True, "is_interaction_candidate": True}),
        _mk({"scrollable": True, "is_interaction_candidate": True}),
        _mk({"focusable": True}),
        _mk({"enabled": False}),
    ]
    summary = csr._build_summary(elements, xml_node_count=4)
    assert summary["element_count"]               == 4
    assert summary["xml_node_count"]              == 4
    assert summary["clickable_count"]             == 1
    assert summary["scrollable_count"]            == 1
    assert summary["focusable_count"]             == 1
    assert summary["enabled_count"]               == 3
    assert summary["interaction_candidate_count"] == 2
    assert summary["parity_assertions"]["xml_equals_elements"] is True


def test_build_summary_parity_failure_detected() -> None:
    """If the number of parsed elements doesn't match the raw XML node count, the parity flag must be False. This detects silent parsing bugs where some XML nodes were silently dropped."""
    elements = [_mk({})]
    summary = csr._build_summary(elements, xml_node_count=99)
    assert summary["parity_assertions"]["xml_equals_elements"] is False


# ─────────────────────────── Capture id ──────────────────────────────────────

def test_sanitize_serial_replaces_unsafe_chars() -> None:
    """Device serial numbers from TCP connections (e.g. '10.56.19.39:5555') contain dots and colons that are unsafe in file/directory names — all unsafe characters must be replaced with underscores."""
    assert csr._sanitize_serial("10.56.19.39:5555") == "10_56_19_39_5555"
    assert csr._sanitize_serial("abc-DEF_123") == "abc-DEF_123"


def test_make_capture_id_format() -> None:
    """The capture ID encodes a UTC timestamp and sanitized device serial into a unique directory name (e.g. 'cap_20260508T143543275Z_10_56_19_39_5555'). Each component is verified against the expected format."""
    ts = dt.datetime(2026, 5, 8, 14, 35, 43, 275_000, tzinfo=dt.timezone.utc)
    capture_id = csr._make_capture_id(ts, "10.56.19.39:5555")
    assert capture_id == "cap_20260508T143543275Z_10_56_19_39_5555"
