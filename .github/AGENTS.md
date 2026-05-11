# Agent Routing Guide

The project mission is to exhaustively document every element visible on the current Android screen and to prepare for future interaction-driven captures where elements are acted on and the resulting screens are re-captured.
Current execution scope is Android device investigation through ADB; SSH-based flows are intentionally deferred.

## environment-mentor

Use when you want to:

- Create, update, or debug `.instructions.md`, `.agent.md`, `.prompt.md`, or `SKILL.md` files.
- Keep customization scope aligned with the extraction-first mission.
- Validate frontmatter quality, `applyTo` scopes, and file placement.
- Understand why a customization is or is not being invoked.

## android-scrape-planner

Use when you want to:

- Define or extend the exhaustive element extraction contract.
- Specify element completeness rules, stable identity, or capture provenance.
- Plan interaction candidacy: which elements to act on, what action types apply.
- Design the future interaction loop: capture → act → re-capture → link.
- Plan the JSON/Markdown/HTML report structure for full element documentation.

## pipeline-runner

Use when you want to:

- Execute the Android capture pipeline scripts and collect their output.
- Diagnose ADB/device connectivity issues blocking a capture run.
- Triage Python tracebacks from capture scripts and pinpoint the failing module.
- Validate generated JSON/Markdown/HTML artifacts against the screen-snapshot schema and templates.
- Receive a structured diagnostic report with a proposed fix (no code edits applied).

## python-implementer

Use when you want to:

- Implement an approved `android-scrape-planner` plan in Python with surgical precision.
- Implement v2 interaction traversal starting from Home, tapping actionable candidates, and re-capturing resulting states.
- Register complete state/transition provenance for every interaction attempt and outcome.
- Produce JSON/Markdown/HTML artifacts from a single in-memory model with full element parity.
- Keep exploration deterministic (stable ordering, visited-state control, explicit stop conditions).

## test-engineer

Use when you want to:

- Design, implement, run, or report unit and component tests for `scripts/`.
- Add coverage for parsing, identity, interaction candidacy, summary, renderers, or diff helpers.
- Run the offline test harness and produce JSON + JUnit XML + HTML test reports.
- Maintain the `tests/` tree, fixtures, and `scripts/run_tests_report.py`.

## Routing Rule

- Environment and customization tasks → `environment-mentor`.
- Android extraction, schema, element identity, interaction planning → `android-scrape-planner`.
- Running scripts, ADB/runtime debugging, traceback triage, artifact validation → `pipeline-runner`.
- Implementing an approved plan in Python → `python-implementer`.
- Writing/running/reporting unit and component tests offline → `test-engineer`.
