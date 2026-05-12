# Agent Routing Guide

The project mission is to drive **root-privileged, LLM-orchestrated
Android system inspection sessions** that persist findings into a
SQLite knowledge store and retrieve prior knowledge into the next
session via RAG. UI exploration is **not** the mission — the agent
works at the `dumpsys` / `getprop` / `pm` / `service` / `settings`
layer.

Execution scope is local: a workstation with `adb` and a single
target device. SSH-based flows are intentionally deferred.

## system-analyzer

Use when you want to:

- Drive a root-privileged Android inspection session via
  `scripts/rag_run.py`.
- Plan which `dumpsys` sections / properties / packages to enumerate
  next on this device.
- Reason about what is already known (RAG retrieval) before launching
  the next run.
- Triage a finished session: read `summary.md`, `manifest.json`, raw
  artifacts.

## environment-mentor

Use when you want to:

- Create, update, or debug `.instructions.md`, `.agent.md`,
  `.prompt.md`, or `SKILL.md` files.
- Keep customization scope aligned with the RAG-system-analyzer
  mission.
- Validate frontmatter, `applyTo` scopes, and file placement.
- Understand why a customization is or is not being invoked.

## python-implementer

Use when you want to:

- Implement changes inside `scripts/agent/` (runner, tools,
  knowledge store, llm_client) or `scripts/rag_run.py`.
- Add a new agent tool, extend a tool schema, or refine the system
  prompt.
- Touch the SQLite indexer/retriever or embedding pipeline.

## test-engineer

Use when you want to:

- Add, run, or report unit + component tests under `tests/`.
- Maintain `scripts/run_tests_report.py` and the HTML test report.
- Cover knowledge store, retriever, tool dispatch, and runner
  control flow — without invoking real ADB or the real LLM.

## report-designer

Use when you want to:

- Improve the visual quality of HTML report templates
  (`session-report-template.html`, `test-report-template.html`,
  `report-template.html`).
- Polish hero sections, tables, color semantics, dark-mode contrast.

## git-keeper

Use when you want to:

- Keep `README.md`, `CHANGELOG`, commit messages, or `.gitignore`
  aligned with the actual code.

## Routing Rule

- Inspection-session planning, execution, triage → `system-analyzer`.
- Customization-file edits, frontmatter, scopes → `environment-mentor`.
- Python edits inside `scripts/` → `python-implementer`.
- Offline tests + test report → `test-engineer`.
- HTML report visual polish → `report-designer`.
- Repository hygiene (README, changelog, commits) → `git-keeper`.

