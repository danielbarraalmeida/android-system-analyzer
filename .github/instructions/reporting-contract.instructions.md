---
description: "Use when generating or changing report outputs (HTML, JSON, Markdown) for the RAG-powered Android System Analyzer sessions or its test suite."
applyTo: "templates/**/*.{json,md,html},**/*report*.{md,html,json},output/sessions/**/*"
---

# Reporting Contract Instruction

## Purpose

Reports must give a reader the full picture of a finished
inspection session: the goal, what tools were called in what order,
what knowledge facts were captured, and links to raw artifacts —
without requiring the reader to open the device or the SQLite store.

## Session output set

Every `scripts/rag_run.py` invocation produces, under
`output/sessions/<session_id>/`:

- `manifest.json` — canonical machine-readable record of the run
  (goal, device, tools called, knowledge facts, warnings, exit
  reason).
- `summary.md` — human narrative.
- `transcript.json` — full LLM message + tool-call log.
- `report.html` — visual session report rendered from
  `templates/session-report-template.html`.
- `raw/*.txt` — verbatim shell / dumpsys outputs referenced by the
  manifest.

`manifest.json` is the **single source of truth**. Markdown and
HTML must not show facts that are absent from the manifest, and
counts must match exactly.

## Test output set

`scripts/run_tests_report.py` produces, under
`output/test-results/`:

- `junit.xml` — JUnit-format pytest results.
- `test-results.json` — aggregated machine-readable summary.
- `test-report.html` — rendered from
  `templates/test-report-template.html`.

## HTML guidance

- All CSS / JS inline. No CDN dependencies. The file must open
  offline.
- Hero section with the most important facts first.
- Tables for transcript / fact list with sticky headers, alternating
  row shading, truncation with tooltip.
- Use `<details>`/`<summary>` for verbose blocks (raw transcripts,
  command logs, failing-test stack traces).
- Semantic color: success/green, warning/amber, failure/red,
  neutral/muted.

## Out of scope

- UI element catalogs, BFS state graphs, tap-candidacy reports.
- Diff / delta reports between sessions (auxiliary only, never
  primary).
