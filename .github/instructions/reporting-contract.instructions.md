---
description: "Use when generating or changing report outputs that exhaustively document every Android screen element in JSON, Markdown, or HTML."
applyTo: "templates/**/*.{json,md,html},docs/**/*.md,**/*report*.{md,html,json}"
---

# Reporting Contract Instruction

## Purpose

Reports must exhaustively document every element on the current Android screen so a reader can fully understand the UI surface without inspecting the device. Reports also carry interaction candidacy data so future workflows can act on them without re-parsing.

## Output Set (Required)

Every scrape capture produces three synchronized artifacts:

- JSON canonical dataset (source of truth).
- Markdown narrative report.
- HTML visual report.

## Consistency Rules

- JSON is the single source of truth.
- Markdown and HTML must reflect the exact same element set, counts, and metadata as the JSON.
- All field names must match `templates/screen-snapshot.schema.json`.
- No truncation: all elements appear in all artifacts.

## Required Report Content

- **Capture metadata**: id, UTC timestamp, device serial, package, activity, screen width/height.
- **Source artifacts**: UI dump path, screenshot path.
- **Element catalog**: every element with full identity, classification, content, geometry, state flags, and interaction candidacy.
- **Hierarchy view**: depth and parent-child relationships visible in the rendered output.
- **Interaction candidates**: summary of which elements are tap/scroll/input/swipe candidates and their action types.
- **Statistics**: total element count, per-flag counts (clickable, focusable, enabled, scrollable, checked).
- **Diagnostics**: ADB command log with exit codes, warnings, known limitations.

## HTML Guidance

- Provide a searchable element table covering every element.
- Provide visual hierarchy cues using depth or indentation levels.
- Provide a screenshot overlay showing element bounding boxes.
- Keep styling lightweight and self-contained (no external CDN dependencies).

## Out of Scope

- Diff or change-comparison reports are not part of the standard output set; they are an optional auxiliary artifact only.
