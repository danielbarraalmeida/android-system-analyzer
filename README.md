# Android System Analyzer

Android System Analyzer is a workspace focused on Android device inspection and rich UI documentation.

This repository is currently configured as a Copilot-first environment where you can learn and evolve:

- Instructions: always-on behavior and scoped coding/reporting rules.
- Agents: specialized assistants for environment customization and scrape planning.
- Skills: reusable domain workflows (ADB automation and future additions).
- Prompts: reusable entry points for common tasks.

## Current Scope (v1)

- Scrape depth: current active screen only; no recursive multi-screen traversal.
- Every UIAutomator node is captured with no filtering or truncation.
- Canonical in-memory model (`ScreenSnapshotModel`) drives all three output formats.
- Capture provenance fields present; `origin.*` is null for root v1 captures.
- Interaction candidacy fields populated (tap, long_tap, scroll, swipe, input); no interaction execution in v1.
- Diff is auxiliary only and opt-in (`--diff`); not part of the v1 standard output set.

## Repository Layout

- `.github/instructions/`: scoped behavior for core, Android safety, and reporting.
- `.github/agents/`: two specialized custom agents.
- `.github/prompts/`: reusable prompts for bootstrap, inspect, report, troubleshoot.
- `.github/skills/`: domain skills including `android-device-manager-adb`.
- `templates/`: output templates and schema for reports.
- `references/`: practical command references.
- `docs/`: learning-oriented guides.

## Quick Start

1. Open this workspace in VS Code.
2. Use the environment mentor agent for setup and customization tasks.
3. Use the Android scrape planner agent to define/iterate current-screen extraction.
4. Generate artifacts following `templates/screen-snapshot.schema.json`, `templates/report-template.md`, and `templates/report-template.html`.

## Python Environment Setup

Create and activate a local `.venv`, then install dependencies from `requirements.txt`.

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Windows (Git Bash):

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Run v1 Current-Screen Capture

From the repository root:

```bash
python scripts/current_screen_report.py
```

If multiple devices are connected, provide a serial:

```bash
python scripts/current_screen_report.py --serial <device_serial>
```

Outputs are written to:

- `output/captures/<capture-id>/screen-snapshot.json`
- `output/captures/<capture-id>/report.md`
- `output/captures/<capture-id>/report.html`

Auxiliary optional comparison (not primary objective):

```bash
python scripts/diff_captures.py output/captures/<old-id>/screen-snapshot.json output/captures/<new-id>/screen-snapshot.json --format md --output output/captures/diff-report.md
```

Capture pipeline (diff is auxiliary and opt-in):

```bash
python scripts/run_capture_pipeline.py --serial <device_serial>
python scripts/run_capture_pipeline.py --serial <device_serial> --diff
```

## Roadmap

- v1: current-screen extraction and rich reporting contract.
- v2: multi-screen flow traversal with loop avoidance and deduplication.
- v3: recursive crawl mode with state graph and regression fixtures.
- Future scope: SSH-based remote workflows (deferred; Android ADB focus first).
