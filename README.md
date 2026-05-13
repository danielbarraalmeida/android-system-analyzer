# Android System Analyzer

> **Branch `agentic-scrapper`** — the project has been refactored away
> from UI scraping. It is now a **RAG-powered Android system inspector**:
> a local LLM drives a small set of root-privileged ADB tools
> (`getprop`, `pm`, `dumpsys`, `service list`, `settings`, …) to map
> the device's system state, and every session is indexed into a
> persistent SQLite knowledge store. Subsequent runs are seeded with
> prior findings via embedding-based retrieval.

UI exploration (taps, swipes, BFS) is **out of scope** on this branch.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the canonical top-down
flowchart (entry points → web → session → agent loop → tools →
knowledge store).

## What it does

- Connects to a single Android device over ADB and enables root.
- Hands a curated tool surface to a local LLM (OpenAI-compatible
  endpoint — default LM Studio).
- The model issues breadth-first system inspection calls and records
  structured facts via a `note(category, key, value)` tool.
- Every property, package, service, setting, dumpsys excerpt and free-
  text fact is written to `output/knowledge.db`.
- Per-session artifacts (transcript, summary, raw command outputs)
  land in `output/rag-sessions/<session_id>/`.
- On the next session for the same device, a "Prior knowledge" block
  is built from SQLite (recent facts + cosine-similar findings) and
  injected into the system prompt — so the model refines rather than
  repeats.

## Repository layout

```
scripts/
  rag_run.py                # CLI entry point
  run_tests_report.py       # pytest → HTML report harness
  agent/
    runner.py               # agent loop (RAG read → tools → RAG write)
    tools.py                # 13 root-privileged system tools
    schemas.py              # OpenAI tool schemas
    llm_client.py           # chat + embeddings wrapper
    _adb.py                 # internal ADB primitives (kept as a library)
    _navigation.py          # internal navigation helpers (HOME / settle)
    prompts/
      system.md             # system analyst persona
      default_goal.md       # default mission
      dumpsys_sections.md   # curated allowlist reference
    knowledge/
      store.py              # SQLite schema + cosine search
      indexer.py            # session → store (with batched embeddings)
      retriever.py          # store → system-prompt context block

.github/
  agents/                   # specialised Copilot agents
  instructions/             # always-on scoped behaviour rules
  skills/                   # domain skills (android-device-manager-adb)

output/
  rag-sessions/<id>/        # per-run artifacts (gitignored)
  knowledge.db              # cumulative SQLite store (gitignored)
  test-results/             # pytest output (gitignored)

tests/
  unit/                     # store, indexer, retriever, tools, runner
  component/                # run_tests_report harness
```

## Setup

Python 3.10+ and a connected, root-capable Android device.

```bash
python -m venv .venv
source .venv/Scripts/activate         # Git Bash on Windows
# .\.venv\Scripts\Activate.ps1       # PowerShell
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## LLM endpoint

Configured via `scripts/agent/llm_client.py`. Defaults:

| Field             | Default                                           |
|-------------------|---------------------------------------------------|
| `base_url`        | `http://127.0.0.1:1234/v1`                        |
| `model`           | `google/gemma-4-e4b`                              |
| `embedding_model` | `text-embedding-nomic-embed-text-v1.5`            |
| `api_key`         | `local` (any non-empty string)                    |

All four are overridable via CLI flags. Any OpenAI-compatible server
exposing both `/v1/chat/completions` and `/v1/embeddings` works
(LM Studio, llama.cpp `--api-server`, vLLM, Ollama with the OpenAI
shim, etc.).

If the embeddings endpoint is unreachable, the indexer stores rows with
`embedding = NULL`. The session still completes; semantic retrieval is
just disabled for those rows.

## Run a session

```bash
python scripts/rag_run.py --serial 10.56.19.39:5555
```

Useful flags:

| Flag                       | Default                      | Purpose |
|----------------------------|------------------------------|---------|
| `--serial`                 | auto-resolve                 | ADB serial. |
| `--output-root`            | `output/rag-sessions`        | Where session_dir lands. |
| `--db-path`                | `output/knowledge.db`        | SQLite store path. |
| `--no-rag`                 | off                          | Disable knowledge store entirely. |
| `--require-root`           | `required`                   | `required` \| `preferred` \| `skipped`. |
| `--allow-arbitrary-shell`  | off                          | Let `run_shell` bypass the allowlist (be careful). |
| `--goal` / `--goal-file`   | `default_goal.md`            | Override the mission. |
| `--max-turns`              | 25                           | LLM turn budget. |
| `--timeout-seconds`        | 600                          | Hard wall-clock cap. |
| `--base-url` / `--model` / `--api-key` / `--embedding-model` | — | Endpoint overrides. |
| `--quiet`                  | off                          | Suppress live progress. |

## Tool surface

The model sees the following tools (full schemas in
`scripts/agent/schemas.py`):

| Tool                     | Purpose |
|--------------------------|---------|
| `get_device_properties`  | `getprop` → identity / build / hardware. Pre-run at session start. |
| `list_packages`          | `pm list packages` filtered by `third_party \| system \| all \| …`. |
| `inspect_package`        | `dumpsys package <pkg>` → version, permissions, activities. |
| `list_services`          | `service list` → binder service registry. |
| `dumpsys`                | Allowlisted sections (audio, display, connectivity, …). |
| `read_settings`          | `settings list <namespace>` (system / secure / global). |
| `list_processes`         | `ps -A`. |
| `read_file`              | `cat` (root) of an absolute device path. |
| `list_dir`               | `ls -la` of an absolute device path. |
| `run_shell`              | Free-form shell, **allowlisted** by default. |
| `capture_home_screen`    | One optional UI snapshot of the launcher. |
| `note`                   | Record a structured `(category, key, value)` fact. |
| `finish`                 | End the session with a markdown summary. |

## Per-session artifacts

`output/rag-sessions/<session_id>/`:

```
summary.md          # final markdown summary (model's or fallback)
transcript.json     # full message + tool-call log
command_log.json    # every adb invocation with timing + exit codes
manifest.json       # session metadata + indexed counts + warnings
warnings.txt        # (only if non-empty)
raw/                # one file per tool call's raw output
screens/            # optional UI snapshot JSON (if capture_home_screen used)
screenshots/        # optional PNG
```

## Knowledge store

SQLite, nine tables (`device`, `properties`, `packages`, `services`,
`settings`, `dumpsys_excerpts`, `facts`, `findings`, `screen_snapshots`).
Embeddings are stored as JSON arrays in `TEXT` columns; cosine
similarity is computed in pure Python. No external vector DB.

Inspect manually:

```bash
sqlite3 output/knowledge.db ".tables"
sqlite3 output/knowledge.db "SELECT category, key, value FROM facts;"
```

## Tests

```bash
python -m pytest tests -q                  # full suite (~73 tests)
python -m pytest tests/unit -q             # just unit tests
python scripts/run_tests_report.py         # → output/test-results/test-report.html
```

## Copilot agents

| Agent                | Purpose |
|----------------------|---------|
| `system-analyzer`    | Drive `scripts/rag_run.py` sessions, interpret results. |
| `python-implementer` | Add or modify Python code in this repo. |
| `test-engineer`      | Author and run tests. |
| `report-designer`    | Improve HTML report templates. |
| `environment-mentor` | Maintain Copilot customization files. |
| `git-keeper`         | Keep README / CHANGELOG / commit hygiene. |

## Status

Branch `agentic-scrapper` — RAG system analyzer is the only supported
workflow. The legacy v1 / v2 UI scrapers and the LLM UI-driver have
been removed.
