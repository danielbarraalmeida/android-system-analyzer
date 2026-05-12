---
description: "Use when working on Android/ADB automation, root-privileged system inspection, dumpsys/getprop/pm/service workflows, or anything inside scripts/agent/."
applyTo: "scripts/**/*.py,tests/**/*.py,**/*.md"
---

# Android ADB Safety Instruction

## Mission

Drive root-privileged Android system inspection through ADB.
Read-only enumeration of properties, packages, services, settings,
and dumpsys sections is the **primary** workflow. UI traversal is
**not** part of the mission.

## Safety defaults

- Verify ADB availability and connected devices before issuing any
  command (`adb version`, `adb devices`).
- If multiple devices are connected, require an explicit serial
  target. Never auto-pick.
- Default to read-only interrogation. Mutating shell commands are
  gated behind `--allow-arbitrary-shell` and the runner's allowlist.
- Root elevation policy is explicit. The three modes are:
  - `required` — abort if `adb root` fails or shell is non-root.
  - `preferred` — try; warn and continue on failure (default).
  - `skipped` — never call `adb root`.

## Reliability

- Record stderr, exit code, and full command string for every ADB
  invocation in the session command log.
- Surface production-locked devices clearly: when `adb root` returns
  "cannot run as root in production builds", warn and continue.
- Never silently drop a failed ADB call. Failures belong in the
  manifest / transcript.

## Knowledge persistence

- Every fact the LLM extracts must be stored through the
  `KnowledgeStore` API. Do not write to SQLite directly.
- Embeddings are batched and stored as JSON TEXT — keep that
  invariant; do not introduce a binary `BLOB` column without a
  matching migration.
- Retrieval into the next session prompt is the whole point of the
  system. Treat the retriever output as authoritative context, not
  decoration.

## Out of scope

- UI extraction (per-element JSON, BFS exploration, tap candidacy).
- SSH-based device transports.
- Cross-device sessions inside a single run.
