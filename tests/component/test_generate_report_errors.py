"""Component tests: generate_report error paths — bad XML, CaptureFatalError,
_validate_schema failure path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.component


def _stub_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch all ADB functions so no real device is needed."""
    monkeypatch.setattr(csr, "_resolve_serial",       lambda log, s: "TEST_SERIAL")
    monkeypatch.setattr(csr, "_ensure_adb_root",      lambda *a, **k: None)
    monkeypatch.setattr(csr, "_get_package_activity", lambda *a, **k: ("com.example", ".Main"))
    monkeypatch.setattr(csr, "_get_screen_size",      lambda *a, **k: (1080, 1920))
    monkeypatch.setattr(csr, "_get_screen_density",   lambda *a, **k: 480)


# ─────────────────────────── bad XML → _extract_elements raises ──────────────

def test_generate_report_raises_on_malformed_xml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the UIAutomator XML dump contains garbage (double angle brackets), the extraction step must raise a parse error. Captures must fail loudly rather than silently producing empty or partial output."""
    _stub_adb(monkeypatch)

    def bad_ui_dump(serial, capture_dir, command_log):
        dest = capture_dir / "window_dump.xml"
        dest.write_text("<<< not valid XML >>>", encoding="utf-8")
        return dest

    def fake_screenshot(serial, capture_dir, command_log):
        dest = capture_dir / "screen.png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return dest

    monkeypatch.setattr(csr, "_capture_ui_dump",    bad_ui_dump)
    monkeypatch.setattr(csr, "_capture_screenshot", fake_screenshot)
    monkeypatch.setattr(csr, "ROOT", tmp_path)

    with pytest.raises(ET_ParseError_or_RuntimeError):
        csr.generate_report(serial="TEST_SERIAL", output_dir=tmp_path)


# The XML parse failure may be ET.ParseError (subclass of SyntaxError) or
# wrapped in RuntimeError depending on the implementation.
import xml.etree.ElementTree as ET
ET_ParseError_or_RuntimeError = (ET.ParseError, RuntimeError, SyntaxError)


def test_generate_report_raises_on_malformed_xml_concrete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Confirms error-raising with a different malformed XML variant (an unclosed tag). Belt-and-suspenders coverage that the parser always rejects structurally invalid XML regardless of the specific corruption."""
    _stub_adb(monkeypatch)

    def bad_ui_dump(serial, capture_dir, command_log):
        dest = capture_dir / "window_dump.xml"
        dest.write_text("<unclosed>", encoding="utf-8")
        return dest

    def fake_screenshot(serial, capture_dir, command_log):
        dest = capture_dir / "screen.png"
        dest.write_bytes(b"\x89PNG\r\n\x1a\n")
        return dest

    monkeypatch.setattr(csr, "_capture_ui_dump",    bad_ui_dump)
    monkeypatch.setattr(csr, "_capture_screenshot", fake_screenshot)
    monkeypatch.setattr(csr, "ROOT", tmp_path)

    with pytest.raises((ET.ParseError, RuntimeError, SyntaxError)):
        csr.generate_report(serial="TEST_SERIAL", output_dir=tmp_path)


# ─────────────────────────── _validate_schema on invalid model ───────────────

def test_validate_schema_returns_error_on_invalid() -> None:
    """An arbitrary dictionary that does not match the screen-snapshot schema must return (False, non-null error string). Verifies the validator is actually checking structure."""
    pytest.importorskip("jsonschema")
    ok, err = csr._validate_schema({"not_a_valid": "model"})
    assert ok is False
    assert err is not None


def test_validate_schema_no_false_positive_on_empty_dict() -> None:
    """An empty dictionary must fail schema validation — ensures the validator is genuinely checking required fields and not accepting anything as valid."""
    pytest.importorskip("jsonschema")
    ok, err = csr._validate_schema({})
    assert ok is False


# ─────────────────────────── CaptureFatalError carries capture_dir ───────────

def test_capture_fatal_error_stores_capture_dir(tmp_path: Path) -> None:
    """CaptureFatalError accepts an optional capture_dir so callers can still point to partial output after a failure. Verifies the directory path is accessible via exc.capture_dir and the error message is preserved."""
    exc = csr.CaptureFatalError("boom", capture_dir=tmp_path)
    assert exc.capture_dir == tmp_path
    assert "boom" in str(exc)


def test_capture_fatal_error_none_capture_dir() -> None:
    """CaptureFatalError can be raised without a capture_dir (e.g. for very early failures before any directory is created). The capture_dir attribute must default to None."""
    exc = csr.CaptureFatalError("msg")
    assert exc.capture_dir is None
