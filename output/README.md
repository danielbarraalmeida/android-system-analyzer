# output/

Runtime artifacts. Everything except this README is gitignored.

| Path              | Producer                          | Notes |
|-------------------|-----------------------------------|-------|
| `rag-sessions/`   | `scripts/rag_run.py`              | One subdirectory per session. |
| `knowledge.db`    | `scripts/rag_run.py` (RAG indexer) | Cumulative SQLite store. |
| `test-results/`   | `scripts/run_tests_report.py`     | JUnit XML + HTML report. |

Safe to delete the whole directory between runs.
