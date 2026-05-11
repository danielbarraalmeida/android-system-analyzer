---
description: "Use when working in this repository to keep outputs structured, actionable, and aligned with Android System Analyzer conventions."
applyTo: "**/*"
---

# Core Workspace Instruction

## Primary Objective

Deliver practical, implementation-oriented outputs for Android inspection workflows and Copilot customization.

## Rules

- Prefer small, composable changes.
- Keep naming consistent across instructions, prompts, agents, skills, docs, and templates.
- When creating or editing customization files, include complete YAML frontmatter with meaningful descriptions.
- Avoid broad assumptions about connected Android devices. Explicitly state prerequisites.
- Keep examples reproducible and deterministic.

## Documentation Quality

- Use concise sections with clear purpose.
- Explain tradeoffs when defining schemas or workflows.
- Make future extension points explicit.

## Validation Checklist

- Correct file placement under `.github`.
- Frontmatter syntax is valid YAML.
- Description text contains trigger language ("Use when...").
- No contradictory scope between files.
