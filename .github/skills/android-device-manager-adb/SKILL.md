---
name: android-device-manager-adb
version: 2.1.0
description: >
  Reference data and workflow for performing programmatic, root-privileged Android system
  inspection through ADB (Android Debug Bridge) inside this repository's RAG-powered analyzer.
  Use this skill whenever the user wants to interact with an Android device or emulator at the
  system layer — dumpsys, getprop, pm, service, settings, logcat, file pulls, package
  inspection, or arbitrary allowlisted shell. Trigger on any mention of ADB, adb shell,
  Android automation, Android testing, dumpsys deep dive, system inspection, root, package
  enumeration, or emulator interaction — even if the user does not say "ADB" explicitly.
  Keywords: ADB, Android, device, emulator, dumpsys, getprop, pm, service, settings, logcat, shell, root, system inspection, RAG session, knowledge store
---

# Android Device Manager — ADB (System Inspection)

## Overview

This skill is the entry point for driving root-privileged Android
system inspection inside this repository. The mission is **not** UI
scraping — it is enumerating system state (properties, packages,
services, settings, dumpsys sections, processes, files) through an
LLM-orchestrated agent that persists every finding into a SQLite
knowledge store and retrieves prior knowledge into the next run via
RAG.

## Prerequisites

- `adb` on `PATH`. Verify with `adb version`.
- At least one device or emulator visible to `adb devices`.
- If multiple devices are connected, every entry point requires an
  explicit serial (`--serial <id>` for the CLI, `-s <id>` for raw
  `adb` invocations).
- Python 3.10+ with `requirements.txt` installed.
- Optional but recommended: an OpenAI-compatible chat + embedding
  endpoint reachable from the host. Configure via environment
  variables `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
  `OPENAI_EMBEDDING_MODEL`.

## Repository surface

- `scripts/rag_run.py` — CLI entry point.
- `scripts/agent/runner.py` — agent loop (system prompt + RAG inject
  + tool dispatch).
- `scripts/agent/tools.py` — system inspection tools.
- `scripts/agent/_adb.py` — internal ADB primitives (`_run_adb`,
  `_resolve_serial`, `_ensure_adb_root`, `_capture_ui_dump`,
  `_capture_screenshot`, focus / size / density probes,
  `extract_ui_signature`).
- `scripts/agent/_navigation.py` — `navigate_to_home`,
  `wait_for_ui_settle`.
- `scripts/agent/knowledge/` — SQLite store, indexer, retriever.
- `output/sessions/<session_id>/` — per-run artifacts.
- `output/knowledge.db` — cumulative knowledge store.

There are **no** standalone per-task scripts (no `screenshot.py`,
`packages.py`, etc.). Everything goes through the agent loop and its
tool calls.

## Available agent tools

These are the tools the LLM can call inside a session (see
`scripts/agent/schemas.py` for full schemas):

### Enumeration (broad, cached after first call)

| Tool | Purpose |
|---|---|
| `get_device_properties` | Run `getprop`; populate the property cache |
| `list_packages(filter)` | `third_party` / `system` / `all` / `disabled` / `enabled` |
| `inspect_package(package, compact?)` | Manifest, version, permissions, activities |
| `list_services` | Registered binder services |
| `read_settings(namespace)` | Full bucket: `system` / `secure` / `global` |
| `list_processes` | `ps -A` parsed rows |
| `dumpsys(section)` | Full dumpsys section text (heavy — prefer `grep_dumpsys`) |

### Abstract search (preferred for targeted questions)

| Tool | Purpose |
|---|---|
| `find_property(pattern, value_pattern?)` | Regex over `getprop` (key OR value) |
| `find_package(pattern, filter?)` | Regex over installed packages + APK paths |
| `find_service(pattern)` | Regex over binder services |
| `find_setting(pattern, namespaces?)` | Regex across settings buckets |
| `grep_dumpsys(section, pattern, context?)` | Regex over a dumpsys section's full output |
| `grep_logcat(pattern, since?, max_lines?)` | Regex over recent logcat (`-d`) |
| `grep_file(path, pattern, context?)` | Regex over a single device file |
| `search_facts(pattern)` | Regex over notes already recorded this session |

### File and shell escape hatches

| Tool | Purpose |
|---|---|
| `read_file(path, max_bytes?)` | Full `cat` for a path |
| `list_dir(path)` | `ls -la` |
| `run_shell(command)` | Allowlisted arbitrary shell (gated by `--allow-arbitrary-shell`) |

### Visual + knowledge

| Tool | Purpose |
|---|---|
| `capture_home_screen` | One optional UI dump + screenshot of the launcher |
| `note(category, key, value)` | Persist a structured fact into the knowledge store |
| `finish(summary)` | Markdown summary; ends the session |

## Querying with abstract inputs

All `find_*` and `grep_*` tools accept a Python regex (case-insensitive
by default). They return only matching lines plus surrounding context,
along with `total_matches` so the agent knows whether to refine.

Worked examples:

- Confirm root posture → `find_property("ro\\.debuggable|ro\\.secure")`.
- Locate OEM / vendor packages → `find_package("\\bcar\\b|automotive|vendor")`.
- Audio HAL details → `grep_dumpsys("audio", "HAL|Patch|version")`.
- Focused window / IME → `grep_dumpsys("window", "mCurrentFocus|imeWindow")`.
- Validated networks → `grep_dumpsys("connectivity", "VALIDATED|Network \\{")`.
- Recent fatal errors → `grep_logcat("FATAL|AndroidRuntime|ANR", since="15m")`.
- Build slot info → `grep_file("/proc/cmdline", "androidboot\\.slot")`.
- Recall a fact → `search_facts("audio")` before another `note`.

### Decision tree — which tool?

1. **Question is about identity / build / hardware** → `find_property`.
2. **Question is about installed apps** → `find_package` first;
   `inspect_package` only for unusual hits.
3. **Question is about a registered service** → `find_service`.
4. **Question is about a setting** → `find_setting`.
5. **Question is about a subsystem state** (audio, display, power,
   connectivity, …) → `grep_dumpsys(<section>, <pattern>)`.
6. **Question is about a recent runtime event** → `grep_logcat`.
7. **Question is about a specific file** → `grep_file` for a substring,
   `read_file` for the whole thing.
8. **Already covered this?** → `search_facts` before re-noting.

Reach for `dumpsys`, `list_packages`, or `run_shell` only when no
abstract-search tool fits the question.

## Workflow

### One-shot run (no prior knowledge)

```bash
python scripts/rag_run.py --serial <id> --no-rag --goal "Map this device."
```

### Iterative run (default — RAG enabled)

```bash
python scripts/rag_run.py --serial <id>
```

Prior findings stored in `output/knowledge.db` are retrieved into the
next session's system prompt. The LLM should refine, fill gaps, and
correct mistakes — not repeat itself.

### Root elevation modes

- `--require-root required` — abort if `adb root` fails.
- `--require-root preferred` (default) — try; warn and continue.
- `--require-root skipped` — never call `adb root`.

### Inspecting a finished session

Open `output/sessions/<session_id>/report.html` or read
`summary.md` + `manifest.json`. Raw shell / dumpsys outputs live
under `raw/`.

## Error handling

Common failure modes surfaced into the session manifest:

- **No device connected** — start an emulator or connect a device.
- **Multiple devices without `--serial`** — pass `--serial <id>`.
- **adb root unavailable on production builds** — the warning
  `"cannot run as root in production builds"` is recorded; some
  `dumpsys` sections will be partial. Re-run with
  `--require-root preferred` if you accidentally used `required`.
- **LLM endpoint unreachable** — check `OPENAI_BASE_URL` and network
  connectivity; the runner raises before any ADB call.

## Reference material

For raw ADB commands outside the agent loop, see
`references/adb_cheatsheet.md`.

For the canonical top-down architecture flowchart (entry points → web →
session → agent loop → tools → knowledge store), see
[ARCHITECTURE.md](../../../ARCHITECTURE.md) at the repo root.
