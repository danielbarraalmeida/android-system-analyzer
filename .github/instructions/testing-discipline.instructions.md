---
description: "Use when authoring or modifying tests under tests/, the run_tests_report.py harness, or the HTML test report template for the Android System Analyzer pipeline."
applyTo: "tests/**/*.py,scripts/run_tests_report.py,templates/test-report-template.html"
---

# Testing Discipline Instruction

## Purpose

Tests verify the v1 extraction contract without a connected device. They must be deterministic, fast, and produce machine-readable plus human-readable reports.

## Layout

- `tests/unit/` — pure helpers, no I/O beyond `tmp_path`.
- `tests/component/` — multi-helper flows assembling the canonical model from fixture XML.
- `tests/fixtures/` — shared XML, JSON, and snapshot fixtures.
- `tests/conftest.py` — shared pytest fixtures.

## Rules

- No real ADB invocation. Patch `subprocess.run` when transport behavior is exercised.
- No network calls and no reliance on `output/` or any path outside `tmp_path`.
- Freeze time with monkeypatch when tests assert on capture ids or timestamps.
- Use `pytest.mark.parametrize` for boolean state matrices (clickable, scrollable, focusable, etc.) instead of duplicating tests.
- Component tests must validate the produced model against `templates/screen-snapshot.schema.json`.
- Component tests must assert element-count parity across JSON model, Markdown rows, and HTML rows.
- Never write tests that pass-by-default when an exception is silently swallowed.

## Reporting

- The harness `scripts/run_tests_report.py` is the only entry point that aggregates results.
- It must emit:
  - `output/test-results/junit.xml`
  - `output/test-results/test-results.json`
  - `output/test-results/test-report.html`
- HTML output uses `templates/test-report-template.html` and must show totals, per-test status, duration, and failure details.

## Out of Scope

- Performance benchmarks.
- Real-device integration tests (covered by `pipeline-runner`).
- Recursive crawl tests (deferred beyond v1).
