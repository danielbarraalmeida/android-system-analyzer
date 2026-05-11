"""Component tests: assemble the canonical model from fixture XML and verify
parity, schema conformance, and renderer outputs without touching ADB.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.component


# ─────────────────────────── Helpers ─────────────────────────────────────────

def _build_model_from_fixture(xml_path: Path) -> dict:
    elements, xml_node_count = csr._extract_elements(xml_path)
    summary = csr._build_summary(elements, xml_node_count)
    timestamp = dt.datetime(2026, 5, 8, 14, 35, 43, 275_000, tzinfo=dt.timezone.utc)
    return {
        "capture": {
            "capture_id":    csr._make_capture_id(timestamp, "TEST_SERIAL"),
            "timestamp_utc": timestamp.isoformat(),
            "device_serial": "TEST_SERIAL",
            "source": {
                "ui_dump_path":    "tests/fixtures/sample_window_dump.xml",
                "screenshot_path": "tests/fixtures/screen.png",
            },
            "origin": {
                "parent_capture_id":     None,
                "interacted_element_id": None,
                "action_type":           None,
            },
        },
        "context": {
            "package_name":   "com.example.app",
            "activity_name":  ".MainActivity",
            "screen_width":   1080,
            "screen_height":  1920,
            "screen_density": 480,
        },
        "summary":  summary,
        "elements": elements,
        "diagnostics": {
            "adb_command_log": [],
            "warnings":        [],
            "limitations":     ["test fixture"],
            "errors":          [],
            "validation": {
                "schema_validation_performed": False,
                "schema_validation_passed":    False,
                "schema_validation_error":     None,
            },
        },
    }


# ─────────────────────────── Tests ───────────────────────────────────────────

def test_extract_elements_counts(sample_xml_path: Path) -> None:
    """Parsing the fixture XML must produce exactly 10 element objects and an XML node count of 10. This guards against silent drops or duplications in the XML parser."""
    elements, node_count = csr._extract_elements(sample_xml_path)
    # 1 root frame + 1 header + 1 list + 2 items + 1 badge + 1 checkbox + 1 password + 1 input + 1 submit = 10
    assert node_count == 10
    assert len(elements) == node_count


def test_extract_elements_preserves_order_and_depth(sample_xml_path: Path) -> None:
    """Elements must be returned in document (pre-order) traversal order. Path strings encode the tree position ('/n[0]/n[1]/n[0]') and depth integers increase as you go deeper into the UI hierarchy."""
    elements, _ = csr._extract_elements(sample_xml_path)
    paths = [e["normalized_path"] for e in elements]
    # The single-window fixture uses <hierarchy><node>… structure.
    # The root <node> under <hierarchy> gets the empty-string path ("") which
    # represents the tree root.  Its children use /n[N] segments.
    assert paths[0]                                  == ""
    assert any(p.startswith("/n[1]/n[")   for p in paths)
    # Depths increase monotonically along any single descent.
    depth_at = {p: e["depth"] for p, e in zip(paths, elements)}
    assert depth_at[""]                             == 0
    assert depth_at["/n[1]"]                        == 1
    assert depth_at["/n[1]/n[0]"]                   == 2


def test_interaction_candidates_present(sample_xml_path: Path) -> None:
    """Three key elements in the fixture must be correctly classified: 'Item one' is a tap candidate; the RecyclerView list is a scroll+swipe candidate (large area); 'Submit' is clickable but disabled so must NOT be a candidate."""
    elements, _ = csr._extract_elements(sample_xml_path)

    def find(predicate) -> dict:
        for e in elements:
            if predicate(e):
                return e
        raise AssertionError("element not found")

    button = find(lambda e: e["text"] == "Item one")
    assert button["clickable"] is True
    assert button["is_interaction_candidate"] is True
    assert "tap" in button["action_types"]

    list_view = find(lambda e: e["resource_id"] == "com.example.app:id/list")
    assert list_view["scrollable"] is True
    assert "scroll" in list_view["action_types"]
    # Bounds are 1080x1500 → area >> threshold → swipe candidate.
    assert "swipe" in list_view["action_types"]

    submit = find(lambda e: e["text"] == "Submit")
    # enabled=false ⇒ clickable but NOT a candidate.
    assert submit["clickable"] is True
    assert submit["enabled"]   is False
    assert submit["is_interaction_candidate"] is False


def test_model_validates_against_schema(sample_xml_path: Path, schema_path: Path) -> None:
    """The complete in-memory capture model assembled from the fixture must pass JSON Schema validation (Draft 2020-12, strict additionalProperties:false on all objects). This is the end-to-end schema conformance check."""
    jsonschema = pytest.importorskip("jsonschema")
    model = _build_model_from_fixture(sample_xml_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=model, schema=schema)  # raises on failure


def test_markdown_render_has_full_element_parity(sample_xml_path: Path) -> None:
    """Every element's unique ID must appear in the rendered Markdown report, and both the '## Element Catalog' section (element table) and '## Diagnostics' section must be present in the output."""
    model = _build_model_from_fixture(sample_xml_path)
    md = csr._render_markdown(model)
    # Element catalog rows: every element id appears exactly once.
    for elem in model["elements"]:
        assert elem["element_id"] in md
    assert md.count("el_v1_") >= len(model["elements"])
    assert "## Element Catalog" in md
    assert "## Diagnostics"     in md


def test_html_render_has_full_element_parity(sample_xml_path: Path, repo_root: Path) -> None:
    """The HTML report must have exactly one table row per element, contain no unreplaced '{{template}}' tokens, show the root element's bounds '[0,0][1080,1920]' verbatim, and style depth-0 elements with 'padding-left:0px'."""
    model = _build_model_from_fixture(sample_xml_path)
    template = (repo_root / "templates" / "report-template.html").read_text(encoding="utf-8")
    html = csr._render_html(model, template)
    # Each element produces a single <tr> in the catalog body.
    tr_count = len(re.findall(r"<tr>\s*<td>\d+</td>", html))
    assert tr_count == len(model["elements"])
    assert "{{element_rows}}" not in html  # template fully substituted
    assert "{{capture_id}}"   not in html
    # Root element bounds string must appear verbatim in output.
    assert "[0,0][1080,1920]" in html
    # Depth-0 element has padding-left:0px style.
    assert "padding-left:0px" in html


def test_end_to_end_writes_three_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_xml_path: Path,
) -> None:
    """Full generate_report() run with all ADB functions replaced by stubs. Verifies that screen-snapshot.json, report.md, and report.html are all written to the capture directory and that the JSON payload reports element_count=10."""

    # Stub _capture_ui_dump to return our fixture (copied into tmp_path).
    def fake_ui_dump(serial, capture_dir, command_log):
        dest = capture_dir / "window_dump.xml"
        dest.write_bytes(sample_xml_path.read_bytes())
        return dest

    def fake_screenshot(serial, capture_dir, command_log):
        dest = capture_dir / "screen.png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header
        return dest

    monkeypatch.setattr(csr, "_resolve_serial",       lambda log, s: "TEST_SERIAL")
    monkeypatch.setattr(csr, "_ensure_adb_root",      lambda *a, **k: None)
    monkeypatch.setattr(csr, "_capture_ui_dump",      fake_ui_dump)
    monkeypatch.setattr(csr, "_capture_screenshot",   fake_screenshot)
    monkeypatch.setattr(csr, "_get_package_activity", lambda *a, **k: ("com.example.app", ".MainActivity"))
    monkeypatch.setattr(csr, "_get_screen_size",      lambda *a, **k: (1080, 1920))
    monkeypatch.setattr(csr, "_get_screen_density",   lambda *a, **k: 480)

    # Point ROOT at tmp_path so generate_report's relative_to(ROOT) resolves cleanly.
    monkeypatch.setattr(csr, "ROOT", tmp_path)

    capture_dir = csr.generate_report(serial="TEST_SERIAL", output_dir=tmp_path)

    assert (capture_dir / "screen-snapshot.json").exists()
    assert (capture_dir / "report.md").exists()
    assert (capture_dir / "report.html").exists()

    payload = json.loads((capture_dir / "screen-snapshot.json").read_text(encoding="utf-8"))
    assert payload["summary"]["element_count"] == 10
    assert payload["diagnostics"]["validation"]["schema_validation_passed"] is True


def test_end_to_end_allows_temp_output_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_xml_path: Path,
) -> None:
    """generate_report must not fail when capture artifacts live outside repository ROOT."""

    def fake_ui_dump(serial, capture_dir, command_log):
        dest = capture_dir / "window_dump.xml"
        dest.write_bytes(sample_xml_path.read_bytes())
        return dest

    def fake_screenshot(serial, capture_dir, command_log):
        dest = capture_dir / "screen.png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return dest

    monkeypatch.setattr(csr, "_resolve_serial",       lambda log, s: "TEST_SERIAL")
    monkeypatch.setattr(csr, "_ensure_adb_root",      lambda *a, **k: None)
    monkeypatch.setattr(csr, "_capture_ui_dump",      fake_ui_dump)
    monkeypatch.setattr(csr, "_capture_screenshot",   fake_screenshot)
    monkeypatch.setattr(csr, "_get_package_activity", lambda *a, **k: ("com.example.app", ".MainActivity"))
    monkeypatch.setattr(csr, "_get_screen_size",      lambda *a, **k: (1080, 1920))
    monkeypatch.setattr(csr, "_get_screen_density",   lambda *a, **k: 480)

    capture_dir = csr.generate_report(serial="TEST_SERIAL", output_dir=tmp_path)

    payload = json.loads((capture_dir / "screen-snapshot.json").read_text(encoding="utf-8"))
    source = payload["capture"]["source"]
    assert source["ui_dump_path"].endswith("window_dump.xml")
    assert source["screenshot_path"].endswith("screen.png")
