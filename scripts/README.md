# scripts/

Runtime entry points and the agent package.

| Path                     | Purpose |
|--------------------------|---------|
| `rag_run.py`             | CLI entry point — runs a RAG-powered system inspection session. |
| `current_screen_report.py` | Internal ADB primitive library (screenshot, UI dump, root, serial resolution). Not invoked directly. |
| `v2_navigator.py`        | Internal navigation helpers (HOME, settle, state signature). Used only by `capture_home_screen`. |
| `run_tests_report.py`    | pytest harness that emits `output/test-results/test-report.html`. |
| `agent/`                 | Agent loop, tool surface, prompts, knowledge store. |

See the repository [README](../README.md) for the full workflow.
