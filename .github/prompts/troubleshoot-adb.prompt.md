---
name: troubleshoot-adb
description: "Troubleshoot Android ADB connection, root elevation, and command issues for RAG system-inspection sessions."
---

# Troubleshoot ADB

Diagnose ADB issues blocking a `scripts/rag_run.py` session.

## Checklist

1. Verify `adb version` and `adb devices` output.
2. Check serial targeting when multiple devices exist
   (`--serial <id>` to `rag_run.py`).
3. Validate root elevation mode (`--require-root required|preferred|skipped`)
   and inspect warnings in the session manifest for
   "production-locked" or non-root shell messages.
4. Capture stderr / exit codes from the failing tool call (visible
   in `transcript.json` under the matching tool-call entry).
5. Propose corrective steps and, if the failure is reproducible,
   open a minimal reproduction under `tmp/`.
