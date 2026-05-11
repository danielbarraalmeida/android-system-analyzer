# Customization Playbook

This document explains how to evolve this repository's Copilot environment.

## Concepts

- Instructions: scoped behavior and constraints for file/task contexts.
- Agents: role-specialized assistants for targeted workflows.
- Skills: reusable domain capabilities with documentation and assets.
- Prompts: reusable entry points for repeatable tasks.

## Current Agent Roles

- `environment-mentor`: customization architecture and discoverability guidance.
- `android-scrape-planner`: current-screen extraction and reporting contract planning.

## How To Add A New Agent

1. Create `.github/agents/<name>.agent.md`.
2. Add YAML frontmatter with `name` and `description`.
3. Include clear responsibilities and non-goals.
4. Add routing notes in `.github/AGENTS.md`.

## How To Add A New Instruction

1. Create `.github/instructions/<topic>.instructions.md`.
2. Add frontmatter with `description` and constrained `applyTo` pattern.
3. Keep content short and testable.
4. Avoid conflicting guidance across instruction files.

## How To Add A New Skill

1. Copy `.github/skills/_template/SKILL.md` into a new skill folder.
2. Ensure folder name matches `name` in frontmatter.
3. Make description discoverable with concrete "Use when..." language.
4. Provide assets/docs referenced by the skill.

## Validation Checklist

- Frontmatter parses correctly.
- File is in canonical location.
- Description contains trigger keywords.
- Scope does not overload global context.
- Linked references exist.
