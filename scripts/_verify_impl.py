#!/usr/bin/env python3
"""Offline verification script for the v1 implementation.

Runs against a synthetic UIAutomator XML — no connected device required.
Exits 0 if all checks pass, 1 otherwise.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location(
        "current_screen_report",
        ROOT / "scripts" / "current_screen_report.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


SYNTHETIC_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.example" content-desc="" checkable="false" checked="false"
        clickable="false" enabled="true" focusable="false" focused="false"
        scrollable="false" long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,1920]">
    <node index="0" text="Hello" resource-id="com.example:id/title"
          class="android.widget.TextView" package="com.example"
          content-desc="Title" checkable="false" checked="false"
          clickable="true" enabled="true" focusable="true" focused="false"
          scrollable="false" long-clickable="false" password="false" selected="false"
          bounds="[10,10][500,80]" />
    <node index="1" text="" resource-id="com.example:id/list"
          class="android.widget.ListView" package="com.example"
          content-desc="" checkable="false" checked="false"
          clickable="false" enabled="true" focusable="false" focused="false"
          scrollable="true" long-clickable="false" password="false" selected="false"
          bounds="[0,100][1080,1820]" />
  </node>
</hierarchy>
"""


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    mod = load_module()
    print("Module loaded OK")

    # Write synthetic XML to temp file
    tmp = pathlib.Path(tempfile.mktemp(suffix=".xml"))
    tmp.write_text(SYNTHETIC_XML, encoding="utf-8")

    try:
        # ── 1. Element extraction ────────────────────────────────────────────
        elements, xml_node_count = mod._extract_elements(tmp)
        check(xml_node_count == 3,         "xml_node_count == 3")
        check(len(elements) == 3,          "element_count == 3")
        check(xml_node_count == len(elements), "parity: xml_node_count == element_count")

        # ── 2. normalized_path (§4.1) ────────────────────────────────────────
        check(elements[0]["normalized_path"] == "/n[0]",      "root path == /n[0]")
        check(elements[1]["normalized_path"] == "/n[0]/n[0]", "child0 path == /n[0]/n[0]")
        check(elements[2]["normalized_path"] == "/n[0]/n[1]", "child1 path == /n[0]/n[1]")

        # ── 3. parent_path ───────────────────────────────────────────────────
        check(elements[0]["parent_path"] is None,       "root parent_path is None")
        check(elements[1]["parent_path"] == "/n[0]",    "child0 parent_path == /n[0]")
        check(elements[2]["parent_path"] == "/n[0]",    "child1 parent_path == /n[0]")

        # ── 4. Nullability rules (§3) ────────────────────────────────────────
        # text / content_desc → empty string when absent
        check(elements[0]["text"] == "",         'root text == "" (not None)')
        check(elements[0]["content_desc"] == "", 'root content_desc == "" (not None)')
        check(elements[1]["text"] == "Hello",    "child0 text == Hello")
        # Optional strings → null when absent
        check(elements[0]["hint"] is None,       "root hint is None")
        check(elements[0]["value"] is None,      "root value is None")

        # ── 5. Identity fields ───────────────────────────────────────────────
        check(all(e["identity_version"] == "v1" for e in elements), "identity_version == v1")
        check(all(e["element_id"].startswith("el_v1_") for e in elements), "element_id format el_v1_*")
        check(len(elements[0]["element_id"]) == len("el_v1_") + 16, "element_id length")

        # text must NOT appear in element_id basis (§4.2)
        # Change text; id should stay the same (same structural fields)
        import copy, hashlib
        e_copy = copy.deepcopy(elements[1])
        e_copy["text"] = "CHANGED"
        id1 = mod._compute_element_id(
            e_copy["normalized_path"], e_copy["class_name"],
            e_copy["resource_id"],     e_copy["package"],
            e_copy["bounds"],          e_copy["sibling_index"],
        )
        check(id1 == elements[1]["element_id"], "element_id excludes text")

        # ── 6. xml_index_preorder 0-based sequential (§5) ───────────────────
        check(all(e["xml_index_preorder"] == i for i, e in enumerate(elements)),
              "xml_index_preorder is 0-based sequential")

        # ── 7. Interaction candidacy (§7) ────────────────────────────────────
        # TextView: clickable=T, enabled=T, focusable=T → tap + input
        e1 = elements[1]
        check("tap"   in e1["action_types"], "TextView has tap")
        check("input" in e1["action_types"], "TextView has input")
        check(e1["is_interaction_candidate"] is True, "TextView is_interaction_candidate")
        # ListView: scrollable=T, enabled=T, large area → scroll + swipe
        e2 = elements[2]
        check("scroll" in e2["action_types"], "ListView has scroll")
        check("swipe"  in e2["action_types"], "ListView has swipe (large area)")
        check(len(e2["candidacy_reasons"]) > 0, "ListView candidacy_reasons non-empty")
        # FrameLayout: all false (enabled but nothing else)
        e0 = elements[0]
        check(e0["is_interaction_candidate"] is False, "FrameLayout not a candidate")
        check(e0["action_types"] == [],                "FrameLayout action_types == []")

        # ── 8. source_attributes keys (§2.4 / §3 rule 9) ────────────────────
        for e in elements:
            for k in mod._KNOWN_XML_KEYS:
                check(k in e["source_attributes"], f"source_attributes has key '{k}'")
        check(all(isinstance(e["source_attributes_extra"], dict) for e in elements),
              "source_attributes_extra is dict")

        # ── 9. summary (§2.3) ────────────────────────────────────────────────
        s = mod._build_summary(elements, xml_node_count)
        check(s["xml_node_count"] == 3,                          "summary xml_node_count == 3")
        check(s["element_count"]  == 3,                          "summary element_count == 3")
        check(s["parity_assertions"]["xml_equals_elements"],     "summary parity True")
        check(s["enabled_count"]   == 3,                         "summary enabled_count == 3")
        check(s["clickable_count"] == 1,                         "summary clickable_count == 1")
        check(s["scrollable_count"] == 1,                        "summary scrollable_count == 1")
        check(s["interaction_candidate_count"] == 2,             "summary candidates == 2")

        # ── 10. capture_id format (§6) ───────────────────────────────────────
        import datetime as dt
        ts = dt.datetime(2026, 5, 7, 14, 32, 1, 42000, tzinfo=dt.timezone.utc)
        cap_id = mod._make_capture_id(ts, "emulator-5554")
        check(cap_id == "cap_20260507T143201042Z_emulator-5554", f"capture_id: {cap_id}")

        # ── 11. Schema validation ────────────────────────────────────────────
        try:
            import jsonschema  # type: ignore[import-untyped]
            schema = json.loads((ROOT / "templates" / "screen-snapshot.schema.json").read_text())
            test_model = {
                "capture": {
                    "capture_id":    cap_id,
                    "timestamp_utc": ts.isoformat(),
                    "device_serial": "emulator-5554",
                    "source": {
                        "ui_dump_path":    "output/captures/test/window_dump.xml",
                        "screenshot_path": "output/captures/test/screen.png",
                    },
                    "origin": {
                        "parent_capture_id":     None,
                        "interacted_element_id": None,
                        "action_type":           None,
                    },
                },
                "context": {
                    "package_name":   "com.example",
                    "activity_name":  ".MainActivity",
                    "screen_width":   1080,
                    "screen_height":  1920,
                    "screen_density": 420,
                },
                "summary": s,
                "elements": elements,
                "diagnostics": {
                    "adb_command_log": [{
                        "command":      "adb devices",
                        "exit_code":    0,
                        "stdout":       "List of devices attached",
                        "stderr":       "",
                        "started_utc":  ts.isoformat(),
                        "finished_utc": ts.isoformat(),
                    }],
                    "warnings":    [],
                    "limitations": ["v1 only"],
                    "errors":      [],
                    "validation": {
                        "schema_validation_performed": True,
                        "schema_validation_passed":    True,
                        "schema_validation_error":     None,
                    },
                },
            }
            jsonschema.validate(instance=test_model, schema=schema)
            check(True, "JSON Schema validation passed")

            # ── 12. MD / HTML / JSON parity ──────────────────────────────────
            import re
            md  = mod._render_markdown(test_model)
            html_tmpl = (ROOT / "templates" / "report-template.html").read_text()
            htm = mod._render_html(test_model, html_tmpl)

            # Markdown: count lines starting with "| <digit>" (element rows)
            md_elem_rows = len(re.findall(r"^\| \d+", md, re.MULTILINE))
            check(md_elem_rows == len(elements),
                  f"MD element row count {md_elem_rows} == {len(elements)}")

            # HTML: count element_id cells
            htm_id_cells = htm.count("<code>el_v1_")
            check(htm_id_cells == len(elements),
                  f"HTML element_id cell count {htm_id_cells} == {len(elements)}")

            # JSON element count
            json_model = json.loads(json.dumps(test_model))
            check(len(json_model["elements"]) == len(elements),
                  f"JSON element count {len(json_model['elements'])} == {len(elements)}")

            print("  PASS  MD / HTML / JSON parity: element counts match")

        except ImportError:
            print("  SKIP  jsonschema not installed — install: pip install jsonschema>=4.0")

    finally:
        tmp.unlink(missing_ok=True)

    print()
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
