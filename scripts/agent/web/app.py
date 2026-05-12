"""FastAPI application factory.

``create_app(...)`` builds a self-contained app instance:

- mounts a single inline HTML page at ``/``,
- mounts ``output_root`` at ``/sessions/`` so finished session
  reports open in-place,
- exposes JSON REST endpoints for devices, facts, ask, and inspect,
- exposes an SSE endpoint for live inspection logs.

The factory accepts callables (``store_factory``, ``llm_factory``,
``runner``) so tests can inject fakes without monkeypatching imports.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable

try:
    from fastapi              import FastAPI, HTTPException
    from fastapi.responses    import HTMLResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles  import StaticFiles
    from pydantic             import BaseModel, Field
except ImportError as exc:                                     # pragma: no cover
    raise ImportError(
        "FastAPI is required for the web UI. Install with "
        "'pip install fastapi[standard]'."
    ) from exc

from ..knowledge  import KnowledgeStore
from ..llm_client import LLMClient
from .ask         import answer_question
from .sessions    import SessionRegistry


_STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    serial:   str
    question: str
    top_k:    int = Field(default=5, ge=1, le=20)


class InspectRequest(BaseModel):
    serial:                str
    goal:                  str = ""
    max_turns:             int   = 25
    timeout_seconds:       float = 600.0
    require_root:          str   = "preferred"
    allow_arbitrary_shell: bool  = False
    no_rag:                bool  = False
    settle_ms:             int   = 1500


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_app(
    *,
    db_path:      str | Path,
    output_root:  str | Path,
    llm_factory:  Callable[[], LLMClient],
    store_factory: Callable[[str | Path], KnowledgeStore] | None = None,
    runner:       Callable[..., dict[str, Any]] | None = None,
) -> FastAPI:
    """Build a FastAPI app wired to the given knowledge store + LLM.

    Parameters
    ----------
    db_path
        SQLite path passed to ``KnowledgeStore``.
    output_root
        Directory where new sessions write artifacts (mounted at
        ``/sessions/``).
    llm_factory
        Zero-arg callable returning a fresh ``LLMClient``. Called
        per-request so tests can swap implementations.
    store_factory
        Optional override that returns the ``KnowledgeStore`` for a
        given db path. Defaults to direct construction.
    runner
        Optional override for ``run_agent``. Receives keyword args
        matching the registry's runner contract — used by tests to
        avoid touching ADB or the real LLM.
    """
    db_path     = Path(db_path)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store_factory = store_factory or (lambda p: KnowledgeStore(p, check_same_thread=False))
    runner        = runner or _default_runner

    store    = store_factory(db_path)
    registry = SessionRegistry()

    app = FastAPI(title="Android System Analyzer", version="1.0")

    # --- Static UI + session artifacts ------------------------------------
    if _STATIC_DIR.exists():
        index_html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    else:
        index_html = "<h1>UI assets missing</h1>"

    if output_root.exists():
        app.mount(
            "/sessions",
            StaticFiles(directory=str(output_root), check_dir=False),
            name="sessions",
        )

    # --- Routes -----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def _index() -> str:
        return index_html

    @app.get("/api/health")
    def _health() -> dict[str, Any]:
        llm = llm_factory()
        ok, detail = llm.ping()
        return {
            "ok":          True,
            "llm_ok":      ok,
            "llm_detail":  detail,
            "db_path":     str(db_path),
            "output_root": str(output_root),
            "time_utc":    dt.datetime.now(dt.timezone.utc).isoformat(),
        }

    @app.get("/api/devices")
    def _devices() -> list[dict[str, Any]]:
        rows = store.conn.execute(
            "SELECT * FROM device ORDER BY last_seen_utc DESC",
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["fact_count"]    = _count(store, "facts",    d["serial"])
            d["package_count"] = _count(store, "packages", d["serial"])
            out.append(d)
        return out

    @app.get("/api/devices/{serial}")
    def _device(serial: str) -> dict[str, Any]:
        dev = store.get_device(serial)
        if dev is None:
            raise HTTPException(status_code=404, detail="unknown serial")
        dev["fact_count"]    = _count(store, "facts",    serial)
        dev["package_count"] = _count(store, "packages", serial)
        return dev

    @app.get("/api/devices/{serial}/facts")
    def _device_facts(
        serial:   str,
        limit:    int = 50,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.get_known_facts(serial, limit=limit)
        if category:
            rows = [r for r in rows if r.get("category") == category]
        return rows

    @app.get("/api/adb-devices")
    def _adb_devices() -> list[dict[str, str]]:
        """Run ``adb devices -l`` and return live device list.

        Used by the UI to populate the serial picker before any
        inspection has been recorded.
        """
        import subprocess
        try:
            out = subprocess.run(
                ["adb", "devices", "-l"],
                text=True, capture_output=True, check=False, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail=f"adb error: {exc}")
        devices: list[dict[str, str]] = []
        for line in (out.stdout or "").splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                meta = {"serial": parts[0], "state": "device"}
                for kv in parts[2:]:
                    if ":" in kv:
                        k, _, v = kv.partition(":")
                        meta[k] = v
                devices.append(meta)
        return devices

    @app.post("/api/ask")
    def _ask(req: AskRequest) -> dict[str, Any]:
        result = answer_question(
            store=store,
            llm=llm_factory(),
            serial=req.serial,
            question=req.question,
            top_k=req.top_k,
        )
        return result.to_dict()

    @app.post("/api/inspect")
    def _inspect(req: InspectRequest) -> dict[str, Any]:
        def _build(log_fn: Callable[[str], None]) -> dict[str, Any]:
            return runner(
                serial=req.serial,
                goal=req.goal,
                max_turns=req.max_turns,
                timeout_seconds=req.timeout_seconds,
                require_root=req.require_root,
                allow_arbitrary_shell=req.allow_arbitrary_shell,
                no_rag=req.no_rag,
                settle_ms=req.settle_ms,
                output_root=output_root,
                db_path=db_path,
                llm_factory=llm_factory,
                store=store,
                log_fn=log_fn,
            )

        entry = registry.start(serial=req.serial, goal=req.goal, runner=_build)
        return {
            "session_id":  entry.session_id,
            "status":      entry.status,
            "started_utc": entry.started_utc,
        }

    @app.get("/api/sessions")
    def _sessions() -> list[dict[str, Any]]:
        return [_entry_summary(e) for e in registry.list()]

    @app.get("/api/sessions/{session_id}")
    def _session(session_id: str) -> dict[str, Any]:
        entry = registry.get(session_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown session_id")
        return _entry_summary(entry)

    @app.get("/api/sessions/{session_id}/stream")
    def _stream(session_id: str) -> StreamingResponse:
        if registry.get(session_id) is None:
            raise HTTPException(status_code=404, detail="unknown session_id")
        return StreamingResponse(
            registry.stream(session_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count(store: KnowledgeStore, table: str, serial: str) -> int:
    row = store.conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE serial = ?",
        (serial,),
    ).fetchone()
    return int(row["n"]) if row else 0


def _entry_summary(entry: Any) -> dict[str, Any]:
    return {
        "session_id":   entry.session_id,
        "serial":       entry.serial,
        "goal":         entry.goal,
        "status":       entry.status,
        "started_utc":  entry.started_utc,
        "finished_utc": entry.finished_utc,
        "session_dir":  entry.session_dir,
        "error":        entry.error,
        "manifest":     entry.manifest,
    }


def _default_runner(
    *,
    serial:                str,
    goal:                  str,
    max_turns:             int,
    timeout_seconds:       float,
    require_root:          str,
    allow_arbitrary_shell: bool,
    no_rag:                bool,
    settle_ms:             int,
    output_root:           Path,
    db_path:               Path,
    llm_factory:           Callable[[], LLMClient],
    store:                 KnowledgeStore,
    log_fn:                Callable[[str], None],
) -> dict[str, Any]:
    """Real runner: opens a session, runs the agent loop, returns a manifest."""
    from ..runner import Budget, run_agent
    from ..tools  import open_session

    log_fn(f"opening session (serial={serial}, root={require_root})")
    session = open_session(
        serial=serial,
        output_root=output_root,
        adb_root_mode=require_root,
        allow_arbitrary_shell=allow_arbitrary_shell,
        settle_ms=settle_ms,
    )
    log_fn(f"session_dir = {session.session_dir}")

    llm = llm_factory()
    budget = Budget(max_turns=max_turns, timeout_seconds=timeout_seconds)
    result = run_agent(
        session=session,
        goal=goal or "",
        llm=llm,
        budget=budget,
        knowledge_store=None if no_rag else store,
        log=log_fn,
    )

    manifest = {
        "session_dir":     str(result.session_dir),
        "serial":          session.serial,
        "stop_reason":     result.stop.reason,
        "stop_detail":     result.stop.detail,
        "turns":           result.turns,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "indexed_counts":  result.indexed_counts,
        "warnings":        session.warnings,
    }
    (session.session_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )
    return manifest
