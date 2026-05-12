"""In-process inspection-session registry with SSE-friendly streams.

A ``SessionRegistry`` holds one entry per launched inspection. Each
entry owns:

- the background ``threading.Thread`` running ``run_agent``,
- a ``queue.Queue[str]`` carrying log lines emitted by the runner's
  ``log`` callback,
- a status enum (``starting`` / ``running`` / ``finished`` / ``error``),
- the final manifest dict once the thread completes.

The web app exposes a SSE endpoint that consumes the queue. The
runner stays unaware of the web layer — we just pass it a closure
that does ``queue.put(line)``.

Threads are NOT joined automatically; the user-facing UI is expected
to poll status until ``finished`` / ``error``. A ``cleanup_finished``
helper trims old entries to avoid unbounded growth.
"""

from __future__ import annotations

import datetime as dt
import json
import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

_SENTINEL = object()  # marker pushed when the session thread exits


@dataclass
class SessionEntry:
    session_id:   str
    serial:       str
    goal:         str
    status:       str = "starting"           # starting|running|finished|error
    started_utc:  str = ""
    finished_utc: str = ""
    session_dir:  str | None = None
    manifest:     dict[str, Any] | None = None
    error:        str | None = None
    logs:         queue.Queue = field(default_factory=queue.Queue)
    _thread:      threading.Thread | None = None


class SessionRegistry:
    """Thread-safe in-memory map of ``session_id -> SessionEntry``."""

    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(
        self,
        *,
        serial: str,
        goal:   str,
        runner: Callable[[Callable[[str], None]], dict[str, Any]],
    ) -> SessionEntry:
        """Spawn a thread that calls ``runner(log_fn)`` and tracks status.

        ``runner`` is a zero-arg-after-log_fn closure built by the
        caller — keeps this module independent of the agent runner
        signature.
        """
        session_id = uuid.uuid4().hex[:12]
        entry = SessionEntry(
            session_id=session_id,
            serial=serial,
            goal=goal,
            started_utc=_now_utc(),
        )

        def _log(msg: str) -> None:
            entry.logs.put(str(msg))

        def _target() -> None:
            entry.status = "running"
            _log(f"--- session {session_id} starting on {serial} ---")
            try:
                result = runner(_log)
                entry.manifest = result
                entry.session_dir = result.get("session_dir")
                entry.status = "finished"
                _log("--- session finished ---")
            except Exception as exc:                              # noqa: BLE001
                entry.error = f"{type(exc).__name__}: {exc}"
                entry.status = "error"
                _log(f"--- session error: {entry.error} ---")
                _log(traceback.format_exc())
            finally:
                entry.finished_utc = _now_utc()
                entry.logs.put(_SENTINEL)  # type: ignore[arg-type]

        thread = threading.Thread(
            target=_target,
            name=f"inspect-{session_id}",
            daemon=True,
        )
        entry._thread = thread

        with self._lock:
            self._entries[session_id] = entry

        thread.start()
        return entry

    def get(self, session_id: str) -> SessionEntry | None:
        with self._lock:
            return self._entries.get(session_id)

    def list(self) -> list[SessionEntry]:
        with self._lock:
            return list(self._entries.values())

    def cleanup_finished(self, keep_last: int = 50) -> int:
        """Drop oldest finished/error entries beyond ``keep_last``."""
        with self._lock:
            finished = [
                e for e in self._entries.values()
                if e.status in ("finished", "error")
            ]
            finished.sort(key=lambda e: e.finished_utc)
            drop = finished[:-keep_last] if len(finished) > keep_last else []
            for e in drop:
                self._entries.pop(e.session_id, None)
            return len(drop)

    # ------------------------------------------------------------------
    # SSE stream — generator the FastAPI route forwards to the client.
    # ------------------------------------------------------------------
    def stream(self, session_id: str) -> Iterator[str]:
        entry = self.get(session_id)
        if entry is None:
            yield _sse_event("error", "unknown session_id")
            return

        # Replay anything queued before the client connected, then
        # block on the queue. The sentinel breaks the loop cleanly.
        while True:
            try:
                item = entry.logs.get(timeout=30.0)
            except queue.Empty:
                # Periodic keep-alive so proxies do not close the
                # connection during a long dumpsys call.
                yield _sse_event("ping", "")
                continue
            if item is _SENTINEL:
                payload = {
                    "status":       entry.status,
                    "session_dir":  entry.session_dir,
                    "error":        entry.error,
                    "manifest":     entry.manifest,
                }
                yield _sse_event("done", json.dumps(payload))
                return
            yield _sse_event("log", str(item))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _sse_event(event: str, data: str) -> str:
    # Multi-line data must be split into multiple ``data:`` lines per
    # the SSE spec; single line is fine for our case.
    safe = data.replace("\r\n", "\n").replace("\r", "\n")
    lines = safe.split("\n")
    body = "\n".join(f"data: {ln}" for ln in lines)
    return f"event: {event}\n{body}\n\n"
