"""Unit tests for run_tests_report helpers (no subprocess, no file I/O)."""

from __future__ import annotations

import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import run_tests_report as rtr


pytestmark = pytest.mark.unit


# ─────────────────────────── _parse_junit ────────────────────────────────────

def _write_junit(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "junit.xml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_parse_junit_all_passed(tmp_path: Path) -> None:
    """Parses a JUnit XML file containing two passing tests and verifies total=2, passed=2, failed=0, with every case status set to 'passed'."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuite name="pytest" tests="2" time="0.5">
          <testcase classname="tests.unit.test_foo" name="test_a" time="0.1"/>
          <testcase classname="tests.unit.test_foo" name="test_b" time="0.2"/>
        </testsuite>
    """)
    result = rtr._parse_junit(p)
    assert result["totals"]["total"]  == 2
    assert result["totals"]["passed"] == 2
    assert result["totals"]["failed"] == 0
    assert all(c["status"] == "passed" for c in result["cases"])


def test_parse_junit_has_failure(tmp_path: Path) -> None:
    """When a testcase element contains a <failure> child, its status must be 'failed' and the failure message must appear verbatim in the 'detail' field shown in the HTML report."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuite name="pytest" tests="2" time="0.1">
          <testcase classname="tests.unit.test_foo" name="test_ok" time="0.05"/>
          <testcase classname="tests.unit.test_foo" name="test_bad" time="0.05">
            <failure message="assert False">Long traceback here</failure>
          </testcase>
        </testsuite>
    """)
    result = rtr._parse_junit(p)
    assert result["totals"]["failed"] == 1
    failed = next(c for c in result["cases"] if c["status"] == "failed")
    assert "assert False" in failed["detail"]


def test_parse_junit_testsuites_wrapper(tmp_path: Path) -> None:
    """Some pytest versions emit <testsuites> as the root XML element instead of <testsuite>. Both forms must parse correctly. Also verifies that a classname path containing 'component' maps to suite_kind='component'."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuites>
          <testsuite name="s1" tests="1" time="0.1">
            <testcase classname="tests.component.test_bar" name="test_x" time="0.1"/>
          </testsuite>
        </testsuites>
    """)
    result = rtr._parse_junit(p)
    assert result["totals"]["total"] == 1
    assert result["cases"][0]["suite_kind"] == "component"


def test_parse_junit_suite_kind_unit_by_default(tmp_path: Path) -> None:
    """A test whose classname path contains 'unit' is classified as a unit test (suite_kind='unit'), which drives the unit/component counter displayed in the report header."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuite name="pytest" tests="1" time="0.0">
          <testcase classname="tests.unit.test_helpers" name="test_foo" time="0.0"/>
        </testsuite>
    """)
    result = rtr._parse_junit(p)
    assert result["cases"][0]["suite_kind"] == "unit"


# ─────────────────────────── _aggregate_summary ─────────────────────────────

def test_aggregate_summary_structure(tmp_path: Path) -> None:
    """The aggregated summary dictionary must contain exit_code, totals (with correct counts), generated_utc timestamp, and suite_counts with the correct per-suite breakdown."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuite name="pytest" tests="1" time="1.0">
          <testcase classname="tests.unit.test_x" name="test_y" time="1.0"/>
        </testsuite>
    """)
    parsed  = rtr._parse_junit(p)
    summary = rtr._aggregate_summary(parsed, exit_code=0)
    assert summary["exit_code"]       == 0
    assert summary["totals"]["total"] == 1
    assert "generated_utc"   in summary
    assert "suite_counts"    in summary
    assert summary["suite_counts"]["unit"] == 1


# ─────────────────────────── _render_html (harness) ─────────────────────────

def test_render_html_substitutes_all_placeholders(tmp_path: Path) -> None:
    """Every '{{placeholder}}' token in the HTML template must be replaced with a real value — an unreplaced token would show raw template syntax to the user. The rendered HTML must also contain the test function name."""
    p = _write_junit(tmp_path, """\
        <?xml version="1.0" ?>
        <testsuite name="pytest" tests="1" time="0.5">
          <testcase classname="tests.unit.test_x" name="test_y" time="0.5"/>
        </testsuite>
    """)
    parsed  = rtr._parse_junit(p)
    summary = rtr._aggregate_summary(parsed, exit_code=0)
    html = rtr._render_html(summary)
    assert "{{" not in html
    assert "}}" not in html
    assert "test_y" in html
