#!/usr/bin/env python3
"""Run the offline pytest suite, aggregate results, and render an HTML report.

Outputs (all under ``output/test-results/``):
- ``junit.xml``         — pytest JUnit XML (raw).
- ``test-results.json`` — aggregated machine-readable summary.
- ``test-report.html``  — human-readable HTML report.

The harness intentionally invokes pytest as a subprocess so the same exit
code propagates and the run is reproducible from a clean environment.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import html as html_module
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
RESULTS_DIR = ROOT / "output" / "test-results"
JUNIT_PATH = RESULTS_DIR / "junit.xml"
JSON_PATH = RESULTS_DIR / "test-results.json"
HTML_PATH = RESULTS_DIR / "test-report.html"
TEMPLATE_PATH = TEMPLATES_DIR / "test-report-template.html"


def _now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _extract_docstrings(tests_dir: Path) -> dict[str, str]:
    """Return {module_classname::func_name: first_docstring_line} for all test_*.py files.

    Uses ast.parse so no test module is imported and no side effects occur.
    Parametrized names like 'test_foo[a-b]' are matched via the base name 'test_foo'.
    """
    docs: dict[str, str] = {}
    if not tests_dir.exists():
        return docs
    for py_file in sorted(tests_dir.rglob("test_*.py")):
        rel = py_file.relative_to(tests_dir.parent)
        module_name = rel.with_suffix("").as_posix().replace("/", ".")
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                raw = ast.get_docstring(node) or ""
                # Keep only the first paragraph (up to first blank line).
                first_para = raw.split("\n\n")[0].replace("\n", " ").strip()
                docs[f"{module_name}::{node.name}"] = first_para
    return docs


def _run_pytest(extra_args: list[str]) -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pytest",
        f"--junitxml={JUNIT_PATH}",
        "-q",
        *extra_args,
    ]
    completed = subprocess.run(cmd, cwd=ROOT, check=False)
    return completed.returncode


def _parse_junit(junit_path: Path) -> dict[str, Any]:
    tree = ET.parse(str(junit_path))
    root = tree.getroot()

    # JUnit XML produced by pytest may wrap suites in <testsuites>.
    suites = (
        list(root.findall("testsuite"))
        if root.tag == "testsuites"
        else [root]
    )

    cases: list[dict[str, Any]] = []
    totals = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    duration = 0.0

    for suite in suites:
        try:
            duration += float(suite.get("time", "0") or 0.0)
        except ValueError:
            pass
        for case in suite.findall("testcase"):
            name      = case.get("name", "")
            classname = case.get("classname", "")
            file_attr = case.get("file") or ""
            line_attr = case.get("line") or ""
            try:
                case_time = float(case.get("time", "0") or 0.0)
            except ValueError:
                case_time = 0.0

            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")

            if failure is not None:
                status = "failed"
                detail = (failure.get("message") or "") + "\n" + (failure.text or "")
            elif error is not None:
                status = "error"
                detail = (error.get("message") or "") + "\n" + (error.text or "")
            elif skipped is not None:
                status = "skipped"
                detail = (skipped.get("message") or "") + "\n" + (skipped.text or "")
            else:
                status = "passed"
                detail = ""

            stdout_node = case.find("system-out")
            stderr_node = case.find("system-err")
            stdout = (stdout_node.text or "") if stdout_node is not None else ""
            stderr = (stderr_node.text or "") if stderr_node is not None else ""

            location_hint = (file_attr or classname).replace("\\", "/").replace(".", "/")
            suite_kind = "component" if "/component/" in location_hint else "unit"

            cases.append({
                "name":       name,
                "classname":  classname,
                "file":       file_attr,
                "line":       line_attr,
                "duration":   round(case_time, 4),
                "status":     status,
                "suite_kind": suite_kind,
                "detail":     detail.strip(),
                "stdout":     stdout.strip(),
                "stderr":     stderr.strip(),
            })

            totals["total"] += 1
            totals[status if status in totals else "errors"] += 1

    return {
        "totals":   totals,
        "duration": round(duration, 4),
        "cases":    cases,
    }


def _aggregate_summary(parsed: dict[str, Any], exit_code: int) -> dict[str, Any]:
    suites = {"unit": 0, "component": 0}
    for case in parsed["cases"]:
        suites[case["suite_kind"]] = suites.get(case["suite_kind"], 0) + 1
    return {
        "generated_utc":    _now_utc_iso(),
        "exit_code":        exit_code,
        "duration_seconds": parsed["duration"],
        "totals":           parsed["totals"],
        "suite_counts":     suites,
        "cases":            parsed["cases"],
    }


def _render_html(summary: dict[str, Any]) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    def esc(value: Any) -> str:
        return html_module.escape("" if value is None else str(value))

    rows: list[str] = []
    for case in summary["cases"]:
        detail_block = ""
        if case["detail"] or case["stdout"] or case["stderr"]:
            chunks: list[str] = []
            if case["detail"]:
                chunks.append(f"<strong>Failure:</strong>\n{esc(case['detail'])}")
            if case["stdout"]:
                chunks.append(f"<strong>stdout:</strong>\n{esc(case['stdout'])}")
            if case["stderr"]:
                chunks.append(f"<strong>stderr:</strong>\n{esc(case['stderr'])}")
            detail_block = (
                "<details><summary>details</summary>"
                f"<pre>{'\n\n'.join(chunks)}</pre></details>"
            )
        location = case["file"]
        if case["line"]:
            location += f":{case['line']}"
        desc_block = (
            f'<p class="desc">{esc(case["description"])}</p>'
            if case.get("description") else ""
        )
        rows.append(
            f'<tr data-status="{esc(case["status"])}">'
            f'<td><span class="status {esc(case["status"])}">{esc(case["status"])}</span></td>'
            f'<td><code class="test-id">{esc(case["classname"])}::{esc(case["name"])}</code>'
            f'{desc_block}{detail_block}</td>'
            f'<td><code>{esc(location)}</code></td>'
            f'<td>{esc(case["suite_kind"])}</td>'
            f'<td class="duration">{esc(case["duration"])}s</td>'
            "</tr>"
        )

    totals = summary["totals"]
    suites = summary["suite_counts"]

    replacements = {
        "{{generated_utc}}":    esc(summary["generated_utc"]),
        "{{exit_code}}":        esc(summary["exit_code"]),
        "{{duration_seconds}}": esc(summary["duration_seconds"]),
        "{{total}}":            esc(totals.get("total", 0)),
        "{{passed}}":           esc(totals.get("passed", 0)),
        "{{failed}}":           esc(totals.get("failed", 0)),
        "{{errors}}":           esc(totals.get("errors", 0)),
        "{{skipped}}":          esc(totals.get("skipped", 0)),
        "{{unit_count}}":       esc(suites.get("unit", 0)),
        "{{component_count}}":  esc(suites.get("component", 0)),
        "{{junit_path}}":       esc(JUNIT_PATH.relative_to(ROOT).as_posix()),
        "{{json_path}}":        esc(JSON_PATH.relative_to(ROOT).as_posix()),
        "{{rows}}":             "\n".join(rows),
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run pytest, aggregate results, and render an HTML report.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments forwarded to pytest (after a literal '--').",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    extra = list(args.pytest_args)
    if extra and extra[0] == "--":
        extra = extra[1:]

    exit_code = _run_pytest(extra)

    if not JUNIT_PATH.exists():
        print(f"Error: pytest did not produce {JUNIT_PATH}", file=sys.stderr)
        return exit_code or 1

    parsed = _parse_junit(JUNIT_PATH)

    # Enrich each case with its docstring extracted from the source file.
    docstrings = _extract_docstrings(ROOT / "tests")
    for case in parsed["cases"]:
        base_name = re.sub(r"\[.*\]$", "", case["name"])
        case["description"] = docstrings.get(f"{case['classname']}::{base_name}", "")

    summary = _aggregate_summary(parsed, exit_code)

    JSON_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    HTML_PATH.write_text(_render_html(summary), encoding="utf-8")

    totals = summary["totals"]
    print(
        "Tests: total={total} passed={passed} failed={failed} "
        "errors={errors} skipped={skipped} duration={dur}s".format(
            total=totals["total"], passed=totals["passed"],
            failed=totals["failed"], errors=totals["errors"],
            skipped=totals["skipped"], dur=summary["duration_seconds"],
        )
    )
    print(f"JUnit:   {JUNIT_PATH}")
    print(f"JSON:    {JSON_PATH}")
    print(f"HTML:    {HTML_PATH}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
