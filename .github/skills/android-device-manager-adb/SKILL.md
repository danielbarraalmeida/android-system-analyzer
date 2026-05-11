---
name: android-device-manager-adb
version: 1.1.0
description: >
  Reference data and workflow for performing programmatic control over Android devices through ADB
  (Android Debug Bridge). Use this skill whenever the user wants to interact with an Android device,
  emulator, or anything involving ADB — including taking screenshots, inspecting UI layouts, installing
  or managing apps, tapping or swiping on screen, typing text, pulling or pushing files, reading logs,
  managing device settings, dumping activity info, or running arbitrary shell commands on a connected
  Android device. Trigger this skill for any mention of ADB, Android automation, Android testing,
  Android device scripting, UI inspection, accessibility tree, logcat, or Android emulator interaction,
  even if the user doesn't explicitly say "ADB."
  Keywords: ADB, Android, device, emulator, screenshot, logcat, UI inspection, accessibility tree, shell, automation, testing, adb shell, install, push, pull
---

# Android Device Manager — ADB

## Overview

This skill provides a comprehensive Python toolkit for programmatic control of Android devices via
ADB (Android Debug Bridge). It's designed for a software developer agent that needs to automate
interactions with real or emulated Android devices — taking screenshots, reading UI hierarchies,
managing packages, simulating input, handling files, and reading system state.

All functionality is exposed through standalone Python scripts in the `scripts/` directory. Each
script can be run directly from the command line or imported as a module.

## Prerequisites

ADB must be installed and available on `PATH`. Verify with:

```bash
adb version
```

At least one device or emulator must be connected. Verify with:

```bash
adb devices
```

If multiple devices are connected, most scripts accept a `--serial` / `-s` flag to target a
specific device. When omitted, they default to the single connected device (and fail if there are
multiple).

Python 3.8+ is required. The current reporting scripts in this repository use the Python standard
library only.

If deeper system inspection is needed and the target permits it, root mode may be enabled with:

```bash
adb root
```

## Script Catalog

Below is a quick-reference for every script. Each script supports `--help` for full usage.

### Device & Connectivity

| Script | Purpose |
|--------|---------|
| `device_info.py` | List connected devices and their properties (model, Android version, SDK, serial) |
| `adb_command.py` | Run an arbitrary `adb shell` command and return stdout/stderr |

### Screen Capture & UI Inspection

| Script | Purpose |
|--------|---------|
| `current_screen_report.py` | Capture current screen and generate JSON/Markdown/HTML report set |
| `diff_captures.py` | Optional auxiliary comparison utility (not the primary extraction objective) |
| `run_capture_pipeline.py` | Capture orchestration script; optional auto-diff behavior is secondary |
| `screenshot.py` | Capture a PNG screenshot and pull it to the host |
| `screenrecord.py` | Record the screen to an MP4 for a given duration |
| `ui_layout.py` | Dump the current UI hierarchy (XML) and optionally parse it into structured JSON |
| `ui_element_finder.py` | Search the UI hierarchy for elements by text, resource-id, class, or content-desc |

### Package Management

| Script | Purpose |
|--------|---------|
| `packages.py` | List installed packages (all, system, third-party) with optional filtering |
| `package_intents.py` | Dump all activity, service, receiver, and provider intents for a given package |
| `app_install.py` | Install an APK onto the device (supports `-r` for reinstall, `-d` for downgrade) |
| `app_uninstall.py` | Uninstall a package by name, with optional `--keep-data` |
| `app_lifecycle.py` | Force-stop, clear data, or clear cache for a package |

### Input Simulation

| Script | Purpose |
|--------|---------|
| `input_tap.py` | Tap at (x, y) coordinates |
| `input_swipe.py` | Swipe from (x1, y1) to (x2, y2) with an optional duration |
| `input_text.py` | Type a string into the currently focused field |
| `input_keyevent.py` | Send a key event by code or name (e.g., KEYCODE_HOME, KEYCODE_BACK) |

### File Transfer

| Script | Purpose |
|--------|---------|
| `file_transfer.py` | Push or pull files/directories between host and device |

### System & Diagnostics

| Script | Purpose |
|--------|---------|
| `logcat.py` | Stream or dump logcat with optional tag/priority filtering and line limits |
| `device_settings.py` | Read or write system/secure/global settings |
| `activity_manager.py` | Start activities, broadcast intents, or query the current foreground activity |
| `property_reader.py` | Read system properties via `getprop` (build info, network, hardware) |

## Workflow Patterns

### Pattern 1 — "See then Act" Loop

The most common automation pattern: capture the screen state, decide what to do, act, repeat.

```
1. screenshot.py          →  get visual state
2. ui_layout.py --json    →  get structured element tree
3. ui_element_finder.py   →  locate target element by text or id
4. input_tap.py           →  tap the element's center coordinates
5. screenshot.py          →  verify result
```

### Pattern 2 — App Lifecycle Management

Install, launch, interact, clean up.

```
1. app_install.py myapp.apk
2. activity_manager.py start -n com.example.myapp/.MainActivity
3. (interact via input scripts)
4. app_lifecycle.py force-stop com.example.myapp
5. app_uninstall.py com.example.myapp
```

### Pattern 3 — Log-Driven Debugging

Watch logs while interacting to diagnose issues.

```
1. logcat.py --clear                              →  clear old logs
2. activity_manager.py start -n <component>       →  launch the target
3. (reproduce the issue via input scripts)
4. logcat.py --tag MyAppTag --priority E --lines 200  →  grab relevant errors
```

### Pattern 4 — Bulk Device Interrogation

Gather comprehensive device and app info for reporting.

```
1. device_info.py --json
2. packages.py --third-party --json
3. property_reader.py --all --json
4. device_settings.py list system --json
```

### Pattern 5 — Current-Screen Rich Documentation (v1)

Generate a single-screen dataset and synchronized report artifacts.

```
1. current_screen_report.py                    -> run end-to-end current-screen extraction
2. emit output/captures/<capture-id>/...       -> JSON, Markdown, and HTML artifacts
```

## Coordinate System

Android screen coordinates are in pixels with origin (0, 0) at the top-left corner. When tapping
elements found via `ui_layout.py` or `ui_element_finder.py`, use the center of the element's
bounds rectangle. The scripts that parse UI XML compute center coordinates automatically.

## Error Handling

Every script exits with code 0 on success and non-zero on failure. Stderr carries human-readable
error messages. Common failure modes:

- **No device connected**: "error: no devices/emulators found" — start an emulator or connect a device.
- **Multiple devices without --serial**: "error: more than one device/emulator" — pass `-s <serial>`.
- **Package not found**: the package name may differ from the app name — use `packages.py` to search.
- **Permission denied**: some operations need root or a debuggable build — check with `adb root`.

## Current-Screen Contract (v1)

For this repository, current-screen scraping output is standardized as:

- Canonical JSON: `templates/screen-snapshot.schema.json`
- Markdown report: `templates/report-template.md`
- HTML report: `templates/report-template.html`

The minimum element fields should include bounds, center coordinates, class, resource-id,
text/content-desc, interaction flags, and depth/path metadata.

## Reference Material

For extended ADB command reference and tips beyond what the scripts cover, see
`references/adb_cheatsheet.md`.

For repository customization and extension guidance, see:

- `docs/customization-playbook.md`
- `.github/AGENTS.md`
