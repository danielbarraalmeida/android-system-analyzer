# scripts/

Runtime entry points and the agent package.

| Path                     | Purpose |
|--------------------------|---------|
| `rag_run.py`             | CLI entry point — runs a RAG-powered system inspection session. |
| `run_tests_report.py`    | pytest harness that emits `output/test-results/test-report.html`. |
| `agent/`                 | Agent loop, tool surface, prompts, knowledge store, and internal ADB / navigation primitives (`_adb.py`, `_navigation.py`). |

See the repository [README](../README.md) for the full workflow.
