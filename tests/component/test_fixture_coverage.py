"""Component tests: verify that the enriched fixture XML exercises all coverage
goals — checkable/checked nodes, password field, long-clickable, pipe in
content-desc, vendor attributes, and depth-3 nesting.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.component


def _elements(sample_xml_path: Path) -> list[dict]:
    elems, _ = csr._extract_elements(sample_xml_path)
    return elems


def _find(elems: list[dict], **kw) -> dict:
    for e in elems:
        if all(e.get(k) == v for k, v in kw.items()):
            return e
    raise AssertionError(f"no element matching {kw}")


# ─────────────────────────── CheckBox (checkable + checked) ──────────────────

def test_fixture_has_checkbox_node(sample_xml_path: Path) -> None:
    """The fixture XML contains a CheckBox element with checkable=true and checked=true, verifying that these boolean flags are parsed correctly and the element is flagged as an interaction candidate."""
    elems = _elements(sample_xml_path)
    checkbox = _find(elems, resource_id="com.example.app:id/checkbox")
    assert checkbox["checkable"] is True
    assert checkbox["checked"]   is True
    assert checkbox["clickable"] is True
    assert checkbox["is_interaction_candidate"] is True


# ─────────────────────────── Password field ──────────────────────────────────

def test_fixture_has_password_field(sample_xml_path: Path) -> None:
    """The fixture contains a password EditText (password=true). The flag must be preserved in the element model and the field must still be treated as focusable, making it an input candidate."""
    elems = _elements(sample_xml_path)
    pwd = _find(elems, resource_id="com.example.app:id/password")
    assert pwd["password"] is True
    assert pwd["focusable"] is True
    assert pwd["is_interaction_candidate"] is True


# ─────────────────────────── Vendor attribute in extra ───────────────────────

def test_fixture_vendor_attribute_in_extra(sample_xml_path: Path) -> None:
    """The password field has a non-standard 'extra-vendor' XML attribute. Because it is not in the 21-key contract list, it must land in source_attributes_extra rather than the known-keys bag."""
    elems = _elements(sample_xml_path)
    pwd = _find(elems, resource_id="com.example.app:id/password")
    # extra-vendor is not a known XML key → must land in source_attributes_extra
    assert pwd.get("source_attributes_extra", {}).get("extra-vendor") == "sensitive"


# ─────────────────────────── Pipe in content-desc ────────────────────────────

def test_fixture_pipe_in_content_desc(sample_xml_path: Path) -> None:
    """The password field's content-desc attribute contains a pipe character. The raw string must be preserved verbatim in the element model; escaping only happens later at Markdown render time."""
    elems = _elements(sample_xml_path)
    pwd = _find(elems, resource_id="com.example.app:id/password")
    # Pipe is preserved verbatim in content_desc (markdown escaping only in renderer)
    assert "|" in pwd["content_desc"]


# ─────────────────────────── Long-clickable ──────────────────────────────────

def test_fixture_long_clickable_node(sample_xml_path: Path) -> None:
    """'Item one' has long-clickable=true. The flag must be set in the element model and 'tap' must still appear in action_types because tap and long_tap are independent candidacy decisions."""
    elems = _elements(sample_xml_path)
    item = _find(elems, resource_id="com.example.app:id/item", text="Item one")
    assert item["long_clickable"] is True
    assert "tap" in item["action_types"]


# ─────────────────────────── Depth-3 nesting ─────────────────────────────────

def test_fixture_depth3_node_exists(sample_xml_path: Path) -> None:
    """The 'Badge' TextView is nested three levels deep (root → list → item → badge). Its depth must be 3 and its normalised path must reflect all ancestor indices."""
    elems = _elements(sample_xml_path)
    deep = _find(elems, resource_id="com.example.app:id/badge")
    assert deep["depth"] == 3
    assert deep["normalized_path"] == "/n[1]/n[1]/n[0]"


# ─────────────────────────── Total node count ────────────────────────────────

def test_fixture_total_node_count(sample_xml_path: Path) -> None:
    """The enriched fixture XML has exactly 10 nodes. This guards against accidental edits to the fixture that add or remove elements without updating test expectations elsewhere."""
    elems, node_count = csr._extract_elements(sample_xml_path)
    assert node_count == 10
    assert len(elems) == 10
