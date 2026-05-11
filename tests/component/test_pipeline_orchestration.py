"""Component tests: run_capture_pipeline main() orchestration scenarios."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import run_capture_pipeline as rcp
import current_screen_report as csr


pytestmark = pytest.mark.component


def _stub_generate_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample_xml_path: Path) -> Path:
    """Return a factory that, when called, writes the 3 real artifacts."""
    import datetime as dt

    capture_dir = tmp_path / "cap_test"
    capture_dir.mkdir(parents=True, exist_ok=True)

    def fake_generate(serial=None, output_dir=None, adb_root_mode="auto"):
        # Write minimal screen-snapshot.json
        payload = {
            "capture": {"capture_id": "cap_test"},
            "context": {"package_name": "com.example", "activity_name": ".Main"},
            "elements": [{"path": "/n[0]", "id": "1", "text": "", "class_name": "A", "bounds": [0, 0, 1, 1]}],
        }
        (capture_dir / "screen-snapshot.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        (capture_dir / "report.md").write_text("# Report\n", encoding="utf-8")
        (capture_dir / "report.html").write_text("<html></html>", encoding="utf-8")
        return capture_dir

    monkeypatch.setattr(rcp, "generate_report", fake_generate)
    return capture_dir


def test_pipeline_main_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_xml_path: Path,
) -> None:
    """With generate_report stubbed to write three artifact files, the pipeline's main() must return exit code 0 and all three artifacts must exist on disk at the expected paths."""
    capture_dir = _stub_generate_report(monkeypatch, tmp_path, sample_xml_path)
    monkeypatch.setattr(sys, "argv", ["run_capture_pipeline.py", "--output-dir", str(tmp_path)])

    exit_code = rcp.main()

    assert exit_code == 0
    assert (capture_dir / "screen-snapshot.json").exists()
    assert (capture_dir / "report.md").exists()
    assert (capture_dir / "report.html").exists()


def test_pipeline_main_with_diff_no_previous(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_xml_path: Path,
) -> None:
    """When --diff is passed but only one capture exists (no previous baseline), main() must exit 0 with an informational message rather than crashing — diff is opt-in and gracefully skipped."""
    _stub_generate_report(monkeypatch, tmp_path, sample_xml_path)
    monkeypatch.setattr(sys, "argv", [
        "run_capture_pipeline.py", "--output-dir", str(tmp_path), "--diff"
    ])
    # _find_previous_capture returns None since there's only 1 dir
    exit_code = rcp.main()
    assert exit_code == 0


def test_pipeline_main_capture_fatal_error_returns_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When generate_report raises CaptureFatalError (e.g. XML parse failure or file write error), main() must catch it and return exit code 1 without letting the exception propagate to the caller."""
    def fail_generate(serial=None, output_dir=None, adb_root_mode="auto"):
        raise csr.CaptureFatalError("simulated failure")

    monkeypatch.setattr(rcp, "generate_report", fail_generate)
    monkeypatch.setattr(sys, "argv", ["run_capture_pipeline.py", "--output-dir", str(tmp_path)])

    exit_code = rcp.main()
    assert exit_code == 1
