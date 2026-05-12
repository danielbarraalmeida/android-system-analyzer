# Architecture Flowchart

Top-down view of the RAG-powered Android System Analyzer. This is the
canonical reference diagram for the repo — keep it in sync when the
agent loop, tool surface, web app, or knowledge store change shape.

```mermaid
flowchart TB
    %% ─────────── ENTRY POINTS ───────────
    subgraph Entry["Entry points (scripts/rag_run.py)"]
        CLI_inspect[/"CLI: rag_run.py inspect<br/>--serial --goal --no-rag"/]
        CLI_serve[/"CLI: rag_run.py serve<br/>FastAPI + uvicorn"/]
        CLI_ask[/"CLI: rag_run.py ask<br/>one-shot RAG Q&amp;A"/]
    end

    %% ─────────── WEB ───────────
    subgraph Web["Local web UI (scripts/agent/web/)"]
        UI["index.html<br/>(inline JS, SSE)"]
        APP["FastAPI app.py<br/>/api/devices /api/adb-devices<br/>/api/ask /api/inspect<br/>/api/sessions/{id}/stream"]
        REG["SessionRegistry<br/>thread + queue + SSE"]
        ASKMOD["web/ask.py<br/>answer_question(...)"]
    end

    %% ─────────── DEVICE LAYER ───────────
    subgraph Device["ADB layer (scripts/agent/_adb.py)"]
        ADBRUN["_run_adb (adb -s ... )"]
        ROOT["_ensure_adb_root<br/>TCP reconnect + shell id poll"]
        ADBPRIM["_capture_ui_dump<br/>_capture_screenshot<br/>_get_screen_size/_density"]
        NAV["_navigation.navigate_to_home"]
    end
    DEV[(Android device<br/>adb shell)]

    %% ─────────── SESSION + TOOLS ───────────
    subgraph Session["Session (scripts/agent/tools.py)"]
        OPEN["open_session<br/>resolve serial + root + dirs"]
        AS["AgentSession<br/>caches: properties / packages /<br/>services / settings / dumpsys / logcat / facts"]
        subgraph Tools["TOOL_REGISTRY"]
            T_enum["Enumeration<br/>get_device_properties<br/>list_packages / inspect_package<br/>list_services / read_settings<br/>list_processes / dumpsys"]
            T_search["Abstract search<br/>find_property / find_package<br/>find_service / find_setting<br/>grep_dumpsys / grep_logcat<br/>grep_file / search_facts"]
            T_io["I/O escape<br/>read_file / list_dir / run_shell<br/>(allowlist gated)"]
            T_meta["Meta<br/>capture_home_screen<br/>note / finish"]
        end
        RAW[("session_dir/raw/*<br/>full command outputs")]
    end

    %% ─────────── AGENT LOOP ───────────
    subgraph Runner["Agent loop (scripts/agent/runner.py)"]
        SP["_build_system_prompt<br/>prompts/system.md + dumpsys + RAG ctx"]
        BOOT["_bootstrap: get_device_properties"]
        LOOP{{"for turn in 1..max_turns<br/>llm.chat(tools=TOOL_SCHEMAS)"}}
        PARSE["_parse_args + _validate<br/>against schemas.py"]
        DISP["_dispatch -> TOOL_REGISTRY[name]"]
        FIN["finish() -> StopCondition"]
    end

    %% ─────────── LLM ───────────
    LLM["LLMClient<br/>OpenAI-compatible<br/>chat + embed"]:::ext

    %% ─────────── KNOWLEDGE ───────────
    subgraph Know["Knowledge (scripts/agent/knowledge/)"]
        IDX["indexer.index_session<br/>(facts, packages, services, props)"]
        STORE["KnowledgeStore (SQLite)<br/>devices / facts / packages /<br/>services / properties (+ embeddings JSON)"]
        RET["retriever.get_context<br/>cosine_search"]
    end
    DB[("output/knowledge.db")]
    SESSDIR[("output/sessions/&lt;id&gt;/<br/>manifest.json + summary.md<br/>+ report.html + raw/")]

    %% ─────────── FLOWS ───────────
    CLI_inspect --> OPEN
    CLI_ask --> ASKMOD
    CLI_serve --> APP
    UI -- "fetch / SSE" --> APP
    APP -- "/api/inspect" --> REG --> OPEN
    APP -- "/api/ask" --> ASKMOD

    OPEN --> ROOT --> ADBRUN --> DEV
    OPEN --> AS

    AS --> Runner
    SP --> LOOP
    BOOT --> LOOP
    LOOP -- "tool_call" --> PARSE --> DISP
    DISP --> Tools
    Tools -- "_shell / _run" --> ADBRUN
    Tools --> RAW
    DISP -- "observation" --> LOOP
    LOOP -- "finish" --> FIN

    ADBPRIM --> ADBRUN
    NAV --> ADBRUN

    LOOP <-- "chat + embed" --> LLM
    ASKMOD <-- "embed + chat" --> LLM
    RET <-- "embed" --> LLM

    SP -. "RAG ctx" .- RET
    RET --> STORE
    IDX --> STORE
    STORE --> DB
    ASKMOD --> STORE
    APP -- "list devices/facts" --> STORE

    FIN --> IDX
    FIN --> SESSDIR
    REG -- "log SSE" --> UI
    REG --> SESSDIR

    classDef ext fill:#1f2937,stroke:#60a5fa,color:#e5e7eb;
    classDef store fill:#0b3d2e,stroke:#3fb950,color:#e6edf3;
    class DB,SESSDIR,RAW store
    class DEV ext
```

## How to read it

1. **Entry points** — three CLI subcommands (`inspect`, `serve`, `ask`)
   plus the FastAPI web UI served by `serve`.
2. **Web** — `index.html` ↔ `app.py`. `/api/inspect` spawns a
   `SessionRegistry` background thread that streams logs to the browser
   via Server-Sent Events.
3. **`open_session`** resolves the device serial, runs
   `_ensure_adb_root` (TCP-aware: reconnects and polls `adb shell id`
   after `adb root` restarts adbd), and builds an `AgentSession`
   carrying the in-memory caches that the `find_*` / `grep_*` tools
   rely on.
4. **Agent loop** — `_build_system_prompt` composes
   [scripts/agent/prompts/system.md](scripts/agent/prompts/system.md)
   + the dumpsys cheatsheet + any RAG context retrieved from the
   knowledge store. Each turn the runner calls the LLM with
   `TOOL_SCHEMAS`, validates the tool call, and dispatches into
   `TOOL_REGISTRY`.
5. **Tools** split into four groups: broad enumeration, abstract
   search (regex over cached data), I/O escape hatches, and meta
   (`note`, `finish`, `capture_home_screen`). Every call's full output
   lands under `session_dir/raw/`.
6. **Knowledge** — when `finish` fires, the indexer writes facts /
   packages / services / properties to
   [output/knowledge.db](output/knowledge.db). The next inspection's
   runner pulls relevant rows back via the retriever and embeds them
   into the system prompt — this is the "RAG" loop.
7. **Ask flow** is the simpler subset: `web/ask.py` (or the `ask`
   CLI subcommand) embeds the question, cosine-searches `facts`, and
   asks the LLM to answer **grounded in those citations only**.

## When to update this diagram

Edit this file whenever any of the following change shape:

- A new top-level CLI subcommand or web route.
- A new tool family in `scripts/agent/tools.py` (and its schema).
- A new table or store backend under `scripts/agent/knowledge/`.
- A change to how `open_session` resolves serial / root / dirs.
- A new artifact written under `output/sessions/<id>/`.

The pointer in
[.github/instructions/core-workspace.instructions.md](.github/instructions/core-workspace.instructions.md)
makes this diagram part of the standing context for every workspace task.
