"""Unit tests for diff_captures low-level helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import diff_captures as dc


pytestmark = pytest.mark.unit


# ─────────────────────────── _load_capture ───────────────────────────────────

def test_load_capture_parses_json(tmp_path: Path) -> None:
    """Reads a screen-snapshot.json file from disk and returns its contents as a Python dictionary, exactly as written."""
    payload = {"capture": {"capture_id": "x"}, "elements": []}
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    assert dc._load_capture(f) == payload


# ─────────────────────────── _index_by_path ──────────────────────────────────

def test_index_by_path_keyed_on_path() -> None:
    """Builds a lookup table keyed by each element's normalised UI path, enabling O(1) lookups when comparing two captures side by side."""
    elements = [
        {"path": "/a", "text": "A"},
        {"path": "/b", "text": "B"},
    ]
    idx = dc._index_by_path(elements)
    assert set(idx.keys()) == {"/a", "/b"}
    assert idx["/a"]["text"] == "A"


def test_index_by_path_missing_path_uses_empty_str() -> None:
    """Elements without a path attribute are indexed under an empty string key rather than raising a KeyError — guards against malformed element data."""
    elements = [{"text": "no-path"}]
    idx = dc._index_by_path(elements)
    assert "" in idx


def test_index_by_path_empty_list() -> None:
    """An empty element list must produce an empty index dictionary without raising any errors."""
    assert dc._index_by_path([]) == {}


# ─────────────────────────── _value_changed ──────────────────────────────────

@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("x",  "x",  False),
        ("x",  "y",  True),
        (None, None, False),
        (None, "x",  True),
        (1,    1,    False),
        (1,    2,    True),
    ],
)
def test_value_changed(a, b, expected: bool) -> None:
    """Parametrised comparison helper: identical values return False; any difference returns True; two None values count as equal (unchanged)."""
    assert dc._value_changed(a, b) is expected


# ─────────────────────────── _element_change ─────────────────────────────────

def test_element_change_detects_text_change() -> None:
    """When two versions of the same element differ in text, the change record must contain a 'text' entry with {'before': 'old', 'after': 'new'} so the diff report shows exactly what changed."""
    a = {"path": "/a", "id": "1", "text": "old"}
    b = {"path": "/a", "id": "1", "text": "new"}
    change = dc._element_change(a, b)
    assert change is not None
    assert change["path"] == "/a"
    assert change["changes"]["text"] == {"before": "old", "after": "new"}


def test_element_change_no_change_returns_none() -> None:
    """When two versions of an element are identical across all tracked fields, _element_change must return None so no spurious change record is written to the diff output."""
    a = {"path": "/a", "text": "same", "class_name": "A"}
    b = {"path": "/a", "text": "same", "class_name": "A"}
    assert dc._element_change(a, b) is None


def test_element_change_multiple_fields() -> None:
    """When both text and class_name differ between two versions of an element, both field changes must appear in the same change record with their respective before/after values."""
    a = {"path": "/a", "text": "old", "class_name": "OldClass"}
    b = {"path": "/a", "text": "new", "class_name": "NewClass"}
    change = dc._element_change(a, b)
    assert change is not None
    assert "text"       in change["changes"]
    assert "class_name" in change["changes"]
    assert change["changes"]["class_name"] == {"before": "OldClass", "after": "NewClass"}
