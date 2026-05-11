# Android System Analyzer

BFS-based Android UI explorer using ADB: captures every screen, element, and transition into rich JSON + HTML reports.

Starting from the device Home screen, the tool performs a bounded BFS traversal — tapping every eligible element, capturing the resulting UI state, and repeating for each new state discovered — producing structured JSON, Markdown, and HTML artifacts for every screen encountered.

## Features

- **v2 interaction-driven exploration** — BFS from Home, bounded by max states, transitions, depth, and timeout.
- **Full element extraction** — every UIAutomator node captured with no filtering or truncation.
- **State deduplication** — screens are fingerprinted; revisited states are linked, not re-captured.
- **Rich output formats** — `screen-snapshot.json`, `report.md`, `report.html` per state; session-level manifest and HTML report.
- **Single-screen capture** — point-in-time capture of the current active screen (v1 mode).
- **Diff tool** — optional comparison between two captures.
- **Test suite** — unit and component tests with HTML report generation.

## Repository Layout

```
scripts/
  current_screen_report.py   # v1: capture the current active screen
  v2_explore.py              # v2: BFS interaction-driven exploration
  v2_navigator.py            # tap, back, home, settle, state-sig helpers
  v2_registry.py             # persist states/transitions/attempts registries
  v2_report.py               # generate session-level HTML/MD report
  run_capture_pipeline.py    # pipeline wrapper (capture + optional diff)
  diff_captures.py           # compare two screen-snapshot.json files
  collect_system_context.py  # gather device/OS/app context via ADB
  run_tests_report.py        # run pytest and generate HTML test report

templates/
  screen-snapshot.schema.json      # element capture schema
  session-manifest.schema.json     # session BFS manifest schema
  system-context.schema.json       # device context schema
  report-template.html / .md       # per-screen report templates
  session-report-template.html     # session-level report template
  test-report-template.html        # test results HTML template

.github/
  agents/         # specialized Copilot agents (see Agents section)
  instructions/   # always-on scoped behavior rules
  prompts/        # reusable task entry points
  skills/         # domain skills (android-device-manager-adb)

output/
  captures/       # v1 single-screen captures
  sessions/       # v2 BFS sessions (states, registries, session report)

tests/
  unit/           # unit tests for helpers, transport, renderers
  component/      # component tests for pipeline and orchestration
```

## Agents

| Agent | Purpose |
|-------|---------|
| `android-scrape-planner` | Plan exhaustive UI element extraction and interaction workflows |
| `environment-mentor` | Create and maintain Copilot customization files |
| `pipeline-runner` | Execute capture pipeline, diagnose ADB/device issues |
| `python-implementer` | Implement v2 interaction-driven capture in Python |
| `report-designer` | Improve HTML report templates and visual quality |
| `test-engineer` | Design, implement, and run tests; produce HTML test reports |
| `git-keeper` | Keep README, CHANGELOG, and git artifacts in sync with the code |

## Python Environment Setup

Requires Python 3.10+ and a connected Android device with ADB enabled.

**Windows (Git Bash / PowerShell):**

```bash
python -m venv .venv
source .venv/Scripts/activate       # Git Bash
# .\.venv\Scripts\Activate.ps1     # PowerShell
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Single-Screen Capture (v1)

Captures the current active screen and writes three artifacts.

```bash
python scripts/current_screen_report.py
python scripts/current_screen_report.py --serial <device_serial>
```

Output written to `output/captures/<capture-id>/`:
- `screen-snapshot.json`
- `report.md`
- `report.html`

## BFS Exploration (v2)

Explores the device UI from Home, tapping elements and capturing each resulting state.

```bash
python scripts/v2_explore.py --serial <device_serial>
```

Key options:

| Flag | Default | Description |
|------|---------|-------------|
| `--max-states` | 50 | Stop after N unique states |
| `--max-transitions` | 200 | Stop after N transitions |
| `--max-depth` | 1 | Only explore states ≤ N taps from Home |
| `--timeout-seconds` | 3600 | Abort after N seconds |
| `--settle-ms` | 2000 | Wait after each interaction (ms) |
| `--output-dir` | `output/sessions/` | Override session output directory |

Output written to `output/sessions/<session-id>/`:
- `session-manifest.json` — full BFS graph (states, transitions, attempts)
- `system-context.json` — device/OS context
- `session-report.html` / `session-report.md`
- `states/<capture-id>/` — per-state artifacts

## Diff Tool

Compare two captures (auxiliary, opt-in):

```bash
python scripts/diff_captures.py \
  output/captures/<old-id>/screen-snapshot.json \
  output/captures/<new-id>/screen-snapshot.json \
  --format md --output output/captures/diff-report.md
```

## Run Tests

```bash
python scripts/run_tests_report.py
```

Produces `output/test-results/test-report.html` and `output/test-results/junit.xml`.
