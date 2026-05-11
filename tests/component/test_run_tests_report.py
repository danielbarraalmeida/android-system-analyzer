"""Component tests: run_tests_report main() happy path and edge cases."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import run_tests_report as rtr


pytestmark = pytest.mark.component

JUNIT_MINIMAL = """\
<?xml version="1.0" ?>
<testsuite name="pytest" tests="1" time="0.1">
  <testcase classname="tests.unit.test_x" name="test_y" time="0.1"/>
</testsuite>
"""


def test_main_happy_path_writes_three_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a faked pytest run that produces a passing JUnit XML, main() must write junit.xml, test-results.json, and test-report.html, return exit code 0, and the JSON summary must report total=1 and exit_code=0."""
    junit_path = tmp_path / "junit.xml"
    json_path  = tmp_path / "test-results.json"
    html_path  = tmp_path / "test-report.html"

    monkeypatch.setattr(rtr, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(rtr, "JUNIT_PATH",  junit_path)
    monkeypatch.setattr(rtr, "JSON_PATH",   json_path)
    monkeypatch.setattr(rtr, "HTML_PATH",   html_path)
    monkeypatch.setattr(rtr, "ROOT",        tmp_path)

    def fake_run_pytest(extra_args):
        junit_path.write_text(JUNIT_MINIMAL, encoding="utf-8")
        return 0

    monkeypatch.setattr(rtr, "_run_pytest", fake_run_pytest)
    monkeypatch.setattr(sys, "argv", ["run_tests_report.py"])

    exit_code = rtr.main()

    assert exit_code == 0
    assert junit_path.exists()
    assert json_path.exists()
    assert html_path.exists()

    summary = json.loads(json_path.read_text(encoding="utf-8"))
    assert summary["totals"]["total"] == 1
    assert summary["exit_code"]       == 0


def test_main_propagates_nonzero_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When pytest reports a test failure (exit code 1), main() must propagate that exit code and the JSON summary must record exit_code=1 with failed=1 — CI pipelines rely on this to mark builds as broken."""
    junit_path = tmp_path / "junit.xml"
    json_path  = tmp_path / "test-results.json"
    html_path  = tmp_path / "test-report.html"

    monkeypatch.setattr(rtr, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(rtr, "JUNIT_PATH",  junit_path)
    monkeypatch.setattr(rtr, "JSON_PATH",   json_path)
    monkeypatch.setattr(rtr, "HTML_PATH",   html_path)
    monkeypatch.setattr(rtr, "ROOT",        tmp_path)

    def fake_run_pytest_fail(extra_args):
        junit_path.write_text("""\
<?xml version="1.0" ?>
<testsuite name="pytest" tests="1" time="0.1">
  <testcase classname="tests.unit.test_x" name="test_bad" time="0.1">
    <failure message="assert False">details</failure>
  </testcase>
</testsuite>
""", encoding="utf-8")
        return 1

    monkeypatch.setattr(rtr, "_run_pytest", fake_run_pytest_fail)
    monkeypatch.setattr(sys, "argv", ["run_tests_report.py"])

    exit_code = rtr.main()

    assert exit_code == 1
    summary = json.loads(json_path.read_text(encoding="utf-8"))
    assert summary["exit_code"] == 1
    assert summary["totals"]["failed"] == 1
