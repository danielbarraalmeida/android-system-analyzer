---
name: android-device-manager-adb
version: 2.0.0
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

| Tool | Purpose |
|---|---|
| `get_device_properties` | Run `getprop` and return key build / hardware properties |
| `list_packages` | Enumerate installed packages (all / system / third-party) |
| `inspect_package` | Pull manifest, dump, version, and intents for a single package |
| `dumpsys` | Run `dumpsys <section>` with output capture and provenance |
| `read_settings` | Read `settings list <system\|secure\|global>` |
| `list_processes` | Run `ps -A` and return parsed rows |
| `read_file` | `adb shell cat <path>` for an allowlisted path |
| `list_dir` | `adb shell ls -la <path>` for an allowlisted path |
| `run_shell` | Allowlisted arbitrary shell (gated by `--allow-arbitrary-shell`) |
| `capture_home_screen` | Optional: press HOME, settle, capture UI dump signature + screenshot |
| `note` | Persist a structured fact into the knowledge store |
| `finish` | Signal that the goal is satisfied; ends the session |

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
