---
description: "Use when working on Android/ADB automation, exhaustive element extraction, full-screen documentation, or interaction-driven device workflows in this repository."
applyTo: "**/*.{py,md,json,html}"
---

# Android ADB Extraction Instruction

## Mission

Capture exhaustive information about every element on the current Android screen. Prepare data structures that support future workflows where those elements are interacted with and the resulting screen state is re-captured and linked back to the originating interaction.

## Safety Defaults

- Verify ADB availability and connected devices before issuing any command.
- If multiple devices are connected, require an explicit serial target.
- Default to read-only interrogation for repeatability, but allow interaction commands when explicitly requested.
- Developer/analyst environments may use elevated Android permissions (including root) when available and appropriate for investigation depth.

## Extraction Requirements (v1)

- Scope is the current active screen only.
- Extraction is exhaustive: every node in the UIAutomator hierarchy is captured with its full attribute set.
- No elements are filtered or omitted, including leaf nodes with empty text.
- Both structural data (UI hierarchy XML) and visual data (screenshot PNG) are captured for the same point in time.

## Element Completeness

For every element, capture and preserve:

- **Identity**: stable id, normalized hierarchy path, depth, parent path, sibling index.
- **Classification**: class name, resource-id, package owner.
- **Content**: text, content-desc, hint, value, input-type (null when absent — never omit the field).
- **Geometry**: bounds (left, top, right, bottom), width, height, center x/y.
- **State flags**: clickable, long-clickable, focusable, focused, scrollable, selected, enabled, checked, checkable, password.
- **Interaction candidacy**: whether the element is an interaction target and which action types apply (tap, long-tap, scroll, input, swipe).

## Capture Provenance

Every capture must carry:

- Capture id (deterministic, timestamp-based), UTC timestamp, device serial.
- Package name, activity name, screen width, height.
- Source artifact paths: UI dump XML path, screenshot PNG path.
- Origin link: parent capture id and interacted element id (null for initial captures).

## Interaction Readiness

- Plan stable element identity so the same logical element can be addressed across captures.
- Preserve interaction candidacy fields so future code can select and act without re-parsing.
- Do not perform interaction in v1; only ensure data structures support it.
- Keep connection scope Android-only for now; SSH-based system flows are future scope.

## Reliability

- Record stderr, exit code, and full command string for every ADB invocation in diagnostics.
- Normalize all missing attributes to null/empty — never skip a field because it is absent.
- Document expected failure modes: no device, multiple devices, secure overlay, and root unavailable on production-locked devices.

## Out of Scope

- Diff and change-detection workflows are not the primary deliverable.
- Recursive multi-screen crawling is not in v1.
- SSH connection and remote shell orchestration are deferred to future phases.
