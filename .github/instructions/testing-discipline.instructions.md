---
description: "Use when authoring or modifying tests under tests/, the run_tests_report.py harness, or the HTML test report template for the RAG-powered Android System Analyzer."
applyTo: "tests/**/*.py,scripts/run_tests_report.py,templates/test-report-template.html"
---

# Testing Discipline Instruction

## Purpose

Tests verify the RAG system analyzer pipeline without a connected
device and without a live LLM. They must be deterministic, fast, and
produce machine-readable plus human-readable reports.

## Layout

- `tests/unit/` — pure helpers, no I/O beyond `tmp_path`.
- `tests/component/` — multi-helper flows that exercise
  `runner.run_agent(...)` end-to-end against a fake LLM and a
  monkeypatched ADB.
- `tests/conftest.py` — shared pytest fixtures
  (`repo_root`, scripts path injection).

## Rules

- **No real ADB.** Patch `subprocess.run` for any code path that
  would shell out.
- **No real LLM.** Inject a scripted fake `LLMClient` that returns
  pre-baked tool calls and assistant messages.
- **No network calls.** Tests must run with no internet.
- **No writes outside `tmp_path`.** Never touch `output/` from
  tests.
- Freeze time with monkeypatch when tests assert on session ids or
  timestamps.
- Use `pytest.mark.parametrize` for matrix coverage instead of
  duplicated tests.
- Component tests must validate `manifest.json` produced by a
  session against the JSON schema, when one applies.
- Never write tests that pass-by-default because an exception is
  silently swallowed.

## Reporting

- `scripts/run_tests_report.py` is the only entry point that
  aggregates results.
- Emits:
  - `output/test-results/junit.xml`
  - `output/test-results/test-results.json`
  - `output/test-results/test-report.html`
- HTML uses `templates/test-report-template.html` and must show
  totals, per-test status, duration, and failure details.

## Out of scope

- Real-device integration tests.
- Live-LLM tests against the OpenAI-compatible endpoint.
- Performance benchmarks.
- UI-extraction tests (the pipeline no longer does UI traversal).
