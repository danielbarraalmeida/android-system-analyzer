---
name: inspect-current-screen
description: "Plan a current-screen Android extraction workflow and produce JSON, Markdown, and HTML report artifacts based on the repository contracts."
---

# Inspect Current Screen

Plan a current-screen Android scrape workflow.

## Expected Outputs

- Canonical JSON matching `templates/screen-snapshot.schema.json`.
- Markdown report matching `templates/report-template.md`.
- HTML report matching `templates/report-template.html`.

## Required Coverage

1. Capture provenance metadata.
2. Element normalization fields.
3. Summary statistics.
4. Diagnostics and limitations.
