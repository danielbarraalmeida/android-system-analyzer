"""Unit tests for renderer helpers in current_screen_report.py."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.unit


# ─────────────────────────── _cell ───────────────────────────────────────────

def test_cell_none_returns_empty() -> None:
    """A null value in a Markdown table cell must render as an empty string — never as the literal text 'None'."""
    assert csr._cell(None) == ""


def test_cell_pipe_is_escaped() -> None:
    """A pipe character '|' inside a cell value must be escaped as '\\|' to prevent it from being interpreted as a Markdown table column separator."""
    assert csr._cell("a|b") == r"a\|b"


def test_cell_newline_becomes_space() -> None:
    """Newlines in a cell value are replaced with spaces so each element row stays on a single line in the Markdown table."""
    assert csr._cell("a\nb") == "a b"


def test_cell_integer_stringified() -> None:
    """Non-string values such as integers or booleans must be converted to their string representation before being placed in a Markdown table cell."""
    assert csr._cell(42) == "42"


def test_cell_empty_string() -> None:
    """An empty string value renders as an empty cell without any trailing spaces or unexpected characters."""
    assert csr._cell("") == ""


# ─────────────────────────── _validate_schema ────────────────────────────────

def _minimal_model() -> dict:
    """Build a minimal schema-valid model using real extraction helpers."""
    from pathlib import Path
    ts = dt.datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt.timezone.utc)
    fixture = Path(__file__).parent.parent / "fixtures" / "sample_window_dump.xml"
    elements, xml_node_count = csr._extract_elements(fixture)
    summary = csr._build_summary(elements, xml_node_count)
    return {
        "capture": {
            "capture_id":    "cap_20260101T000000000Z_TEST",
            "timestamp_utc": ts.isoformat(),
            "device_serial": "TEST",
            "source": {
                "ui_dump_path":    "output/captures/x/window_dump.xml",
                "screenshot_path": "output/captures/x/screen.png",
            },
            "origin": {
                "parent_capture_id":     None,
                "interacted_element_id": None,
                "action_type":           None,
            },
        },
        "context": {
            "package_name":   "com.example",
            "activity_name":  ".Main",
            "screen_width":   1080,
            "screen_height":  1920,
            "screen_density": 480,
        },
        "summary":  summary,
        "elements": elements,
        "diagnostics": {
            "adb_command_log": [],
            "warnings":        [],
            "limitations":     [],
            "errors":          [],
            "validation": {
                "schema_validation_performed": False,
                "schema_validation_passed":    False,
                "schema_validation_error":     None,
            },
        },
    }


def test_validate_schema_valid_model_passes(schema_path: Path) -> None:
    """A correctly structured capture model built from real extraction helpers must pass JSON Schema validation (Draft 2020-12, additionalProperties:false) without any errors."""
    pytest.importorskip("jsonschema")
    model = _minimal_model()
    ok, err = csr._validate_schema(model)
    assert ok is True
    assert err is None


def test_validate_schema_missing_elements_fails(schema_path: Path) -> None:
    """A model with the required 'elements' key deleted must fail schema validation and return a non-null error string describing what is missing."""
    pytest.importorskip("jsonschema")
    model = _minimal_model()
    del model["elements"]
    ok, err = csr._validate_schema(model)
    assert ok is False
    assert err is not None
    assert isinstance(err, str)


def test_validate_schema_extra_top_level_key_fails(schema_path: Path) -> None:
    """The schema uses additionalProperties:false, so any unexpected top-level key must cause validation to fail — prevents undocumented fields from silently entering the output format."""
    pytest.importorskip("jsonschema")
    model = _minimal_model()
    model["unexpected_key"] = "oops"
    ok, err = csr._validate_schema(model)
    assert ok is False
    assert err is not None
