#!/usr/bin/env python3
"""v2 interaction-driven Android UI exploration.

Starting from the device Home screen, performs a bounded BFS traversal:
tap every eligible element, capture the resulting state, repeat for each
new state discovered — until a stop condition is met.

Usage
-----
python scripts/v2_explore.py [options]

Options
-------
  --serial         ADB device serial (required if multiple devices connected)
  --adb-root       auto | required | never (default: auto)
  --max-states     Stop after discovering N unique states (default: 50)
  --max-transitions Stop after recording N transitions (default: 200)
  --max-depth      Only explore states reachable in ≤N taps from Home (default: 1)
  --timeout-seconds Abort after N seconds (default: 3600)
  --settle-ms      Milliseconds to wait after each interaction (default: 2000)
  --output-dir     Override the default sessions directory

Output
------
  output/sessions/<session_id>/
    session-manifest.json
    system-context.json
    session-report.html
    session-report.md
    states/<capture_id>/   (screen-snapshot.json, report.md, report.html, ...)
    registries/
      states.json
      transitions.json
      attempts.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

# Project modules
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_system_context import collect_system_context  # noqa: E402
from current_screen_report import (  # noqa: E402
    CaptureFatalError, generate_report,
)
from v2_navigator import (  # noqa: E402
    compute_state_signature,
    execute_back,
    execute_tap,
    is_same_state,
    navigate_to_home,
    select_tap_candidates,
    wait_for_ui_settle,
)
from v2_registry import (  # noqa: E402
    save_registry,
    save_session_manifest,
    save_system_context,
)
from v2_report import generate_session_report  # noqa: E402

ROOT         = Path(__file__).resolve().parent.parent
SESSIONS_DIR = ROOT / "output" / "sessions"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _make_session_id(serial: str) -> str:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
    safe_serial = serial.replace(":", "_").replace(".", "_")
    return f"session_{ts}_{safe_serial}"


def _load_snapshot(capture_dir: Path) -> dict[str, Any]:
    snap_path = capture_dir / "screen-snapshot.json"
    return json.loads(snap_path.read_text(encoding="utf-8"))


def _center_of(element: dict[str, Any]) -> tuple[int, int]:
    return element.get("center_x", 0), element.get("center_y", 0)


def _element_label(elem: dict[str, Any]) -> str:
    """Return the most human-readable label for an element (text > desc > resource_id tail)."""
    text = (elem.get("text") or "").strip()
    if text:
        return text
    desc = (elem.get("content_desc") or "").strip()
    if desc:
        return desc
    rid = (elem.get("resource_id") or "").strip()
    if rid:
        # "com.example:id/my_button" → "my button"
        tail = rid.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        return tail.replace("_", " ").strip()
    return ""


def _activity_short(activity_name: str | None) -> str:
    """Return a readable short form of an activity name, e.g. 'ClimateMain'."""
    if not activity_name:
        return ""
    tail = activity_name.rsplit(".", 1)[-1]
    for suffix in ("Activity", "Fragment", "Screen"):
        tail = tail.replace(suffix, "")
    return tail.strip() or activity_name


def _make_state_record(
    capture_dir: Path,
    snapshot: dict[str, Any],
    sig: str,
    is_home_root: bool,
    depth: int,
    via_element: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cap   = snapshot["capture"]
    ctx   = snapshot.get("context", {})
    elems = snapshot.get("elements", [])

    if is_home_root or via_element is None:
        display_name = "Home"
    else:
        act_short = _activity_short(ctx.get("activity_name"))
        lbl = _element_label(via_element)
        if act_short and lbl:
            display_name = f"{act_short} · {lbl}"
        elif act_short:
            display_name = act_short
        else:
            display_name = lbl or "Unknown"

    return {
        "state_id":        cap["capture_id"],
        "display_name":    display_name,
        "state_signature": sig,
        "package_name":    ctx.get("package_name"),
        "activity_name":   ctx.get("activity_name"),
        "element_count":   len(elems),
        "candidate_count": len(select_tap_candidates(elems)),
        "is_home_root":    is_home_root,
        "depth":           depth,
        "visited_at_utc":  cap["timestamp_utc"],
    }


# ---------------------------------------------------------------------------
# Interrupt handler
# ---------------------------------------------------------------------------

_interrupted = False


def _on_interrupt(signum, frame):
    global _interrupted
    _interrupted = True
    print("\n[v2_explore] Interrupt received — will stop after current action.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main BFS loop
# ---------------------------------------------------------------------------

def explore(  # noqa: PLR0912, PLR0915  — intentionally a single-function BFS
    serial: str,
    adb_root_mode: str = "auto",
    max_states: int = 50,
    max_transitions: int = 200,
    max_depth: int = 1,
    timeout_seconds: float = 3600.0,
    settle_ms: int = 2000,
    output_dir: Path | None = None,
) -> Path:
    """Run a full BFS session. Returns the session directory path."""
    global _interrupted

    session_id  = _make_session_id(serial)
    session_dir = (output_dir or SESSIONS_DIR) / session_id
    states_dir  = session_dir / "states"
    session_dir.mkdir(parents=True, exist_ok=True)
    states_dir.mkdir(parents=True, exist_ok=True)

    started_utc   = _now()
    deadline      = time.monotonic() + timeout_seconds
    command_log: list[dict[str, Any]] = []
    warnings:    list[str] = []

    stop_conditions = {
        "max_states":      max_states,
        "max_transitions": max_transitions,
        "max_depth":       max_depth,
        "timeout_seconds": timeout_seconds,
        "settle_ms":       settle_ms,
    }

    # BFS data structures
    states:      list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    attempts:    list[dict[str, Any]] = []

    sig_to_state_id: dict[str, str] = {}   # signature → state_id (dedup)
    state_queue:     deque[tuple[str, int]] = deque()  # (state_id, depth)
    visited_state_ids: set[str] = set()    # state_ids whose candidates we've tried

    stop_reason = "queue_exhausted"

    # -------------------------------------------------------------------------
    # Phase A: Navigate to Home and capture root state
    # -------------------------------------------------------------------------
    print(f"[v2_explore] Session: {session_id}")
    print(f"[v2_explore] Navigating to Home …")
    navigate_to_home(serial, command_log, warnings, settle_ms)

    print("[v2_explore] Collecting system context …")
    sys_ctx = collect_system_context(serial, command_log, warnings)
    save_system_context(session_dir, sys_ctx)

    print("[v2_explore] Capturing Home state …")
    try:
        home_cap_dir = generate_report(
            serial=serial,
            output_dir=states_dir,
            adb_root_mode=adb_root_mode,
            parent_capture_id=None,
            interacted_element_id=None,
            action_type=None,
        )
    except CaptureFatalError as exc:
        print(f"[v2_explore] FATAL: Home capture failed: {exc}", file=sys.stderr)
        raise

    home_snap = _load_snapshot(home_cap_dir)
    home_elems = home_snap.get("elements", [])
    home_sig  = compute_state_signature(home_elems)
    home_record = _make_state_record(home_cap_dir, home_snap, home_sig, True, 0)
    home_state_id = home_record["state_id"]

    states.append(home_record)
    sig_to_state_id[home_sig] = home_state_id
    state_queue.append((home_state_id, 0))

    save_registry(session_dir, states, transitions, attempts)
    save_session_manifest(session_dir, _build_manifest(
        session_id, serial, started_utc, None, home_state_id,
        stop_conditions, "interrupted", states, transitions, attempts,
    ))

    # -------------------------------------------------------------------------
    # Phase B: BFS
    # -------------------------------------------------------------------------
    while state_queue:
        if _interrupted:
            if stop_reason == "queue_exhausted":
                stop_reason = "interrupted"
            break
        if time.monotonic() > deadline:
            stop_reason = "timeout"
            break
        if len(transitions) >= max_transitions:
            stop_reason = "max_transitions"
            break

        current_state_id, current_depth = state_queue.popleft()

        if current_state_id in visited_state_ids:
            continue
        visited_state_ids.add(current_state_id)

        # Retrieve the current state snapshot
        current_snap_dir = states_dir / current_state_id
        current_snap     = _load_snapshot(current_snap_dir)
        current_elems    = current_snap.get("elements", [])
        candidates       = select_tap_candidates(current_elems)

        # Deduplicate by tap coordinates: multiple elements sharing the same
        # (center_x, center_y) produce identical outcomes, so keep only the
        # first candidate (deterministic sort order) for each unique point.
        seen_coords: set[tuple[int, int]] = set()
        deduped: list[dict[str, Any]] = []
        for _c in candidates:
            coord = _center_of(_c)
            if coord not in seen_coords:
                seen_coords.add(coord)
                deduped.append(_c)
        skipped_dupes = len(candidates) - len(deduped)
        candidates = deduped

        print(
            f"[v2_explore] State {current_state_id} (depth={current_depth})"
            f" — {len(candidates)} candidates"
            + (f" ({skipped_dupes} duplicate tap points removed)" if skipped_dupes else "")
        )

        # Check depth limit — if at max_depth, record state but don't explore further
        if current_depth >= max_depth:
            continue

        for elem in candidates:
            if _interrupted:
                if stop_reason == "queue_exhausted":
                    stop_reason = "interrupted"
                break
            if time.monotonic() > deadline:
                stop_reason = "timeout"
                break
            if len(transitions) >= max_transitions:
                stop_reason = "max_transitions"
                break

            element_id   = elem["element_id"]
            element_path = elem.get("resource_id") or elem.get("normalized_path") or element_id
            x, y         = _center_of(elem)
            attempt_id   = str(uuid.uuid4())
            attempt_time = _now()

            # Record attempt
            attempt: dict[str, Any] = {
                "attempt_id":    attempt_id,
                "state_id":      current_state_id,
                "element_id":    element_id,
                "element_path":  element_path,
                "action_type":   "tap",
                "action_payload": {"x": x, "y": y},
                "outcome":       "executed",
                "skip_reason":   None,
                "transition_id": None,
                "attempted_utc": attempt_time,
            }

            # For depth > 0 taps, deep replay is not yet implemented
            if current_depth > 0:
                msg = (
                    f"[v2_explore] WARNING: deep replay not implemented "
                    f"(state {current_state_id} depth={current_depth})"
                    f" — skipping tap on {element_id}"
                )
                print(msg, file=sys.stderr)
                warnings.append(msg)
                attempt["outcome"]     = "blocked"
                attempt["skip_reason"] = "deep_replay_not_implemented"
                attempts.append(attempt)
                save_registry(session_dir, states, transitions, attempts)
                continue

            # Always navigate to Home before every tap (Home-per-tap invariant)
            navigate_to_home(serial, command_log, warnings, settle_ms)
            wait_for_ui_settle(serial, command_log, settle_ms)

            # For Home-level taps: Home is restored before every tap, so use home_sig
            pre_tap_sig = home_sig

            tap_started = _now()
            tap_ok      = execute_tap(serial, x, y, command_log)
            wait_for_ui_settle(serial, command_log, settle_ms)

            transition_id = str(uuid.uuid4())
            transition: dict[str, Any] = {
                "transition_id":        transition_id,
                "source_state_id":      current_state_id,
                "source_element_id":    element_id,
                "action_type":          "tap",
                "action_payload":       {"x": x, "y": y, "element_path": element_path},
                "destination_state_id": None,
                "outcome":              "failed",
                "error":                None,
                "started_utc":          tap_started,
                "finished_utc":         _now(),
            }

            if not tap_ok:
                transition["outcome"] = "failed"
                transition["error"]   = "adb tap returned non-zero exit code"
                attempt["outcome"]    = "blocked"
                attempt["skip_reason"] = "tap_failed"
            else:
                # Capture post-tap state
                try:
                    new_cap_dir = generate_report(
                        serial=serial,
                        output_dir=states_dir,
                        adb_root_mode=adb_root_mode,
                        parent_capture_id=current_state_id,
                        interacted_element_id=element_id,
                        action_type="tap",
                    )
                    new_snap  = _load_snapshot(new_cap_dir)
                    new_elems = new_snap.get("elements", [])
                    new_sig   = compute_state_signature(new_elems)

                    if is_same_state(pre_tap_sig, new_sig):
                        transition["outcome"] = "no_change"
                        transition["destination_state_id"] = current_state_id
                    elif new_sig in sig_to_state_id:
                        # Revisit of an already-known state
                        known_state_id = sig_to_state_id[new_sig]
                        transition["outcome"]              = "success"
                        transition["destination_state_id"] = known_state_id
                    else:
                        # New state discovered
                        new_record   = _make_state_record(
                            new_cap_dir, new_snap, new_sig, False, current_depth + 1,
                            via_element=elem,
                        )
                        new_state_id = new_record["state_id"]
                        states.append(new_record)
                        sig_to_state_id[new_sig] = new_state_id
                        transition["outcome"]              = "success"
                        transition["destination_state_id"] = new_state_id

                        if len(states) >= max_states:
                            print(f"[v2_explore] max_states={max_states} reached.")
                            state_queue.appendleft((new_state_id, current_depth + 1))
                            transitions.append(transition)
                            attempt["transition_id"] = transition_id
                            attempts.append(attempt)
                            save_registry(session_dir, states, transitions, attempts)
                            stop_reason = "max_states"
                            _interrupted = True
                            break

                        state_queue.append((new_state_id, current_depth + 1))
                        print(f"[v2_explore]   → New state: {new_state_id}")

                except CaptureFatalError as exc:
                    transition["outcome"] = "failed"
                    transition["error"]   = str(exc)
                    warnings.append(f"Capture failed after tap on {element_id}: {exc}")

            attempt["transition_id"] = transition_id
            transitions.append(transition)
            attempts.append(attempt)

            # Incremental save after every attempt
            save_registry(session_dir, states, transitions, attempts)
            save_session_manifest(session_dir, _build_manifest(
                session_id, serial, started_utc, None, home_state_id,
                stop_conditions, "interrupted", states, transitions, attempts,
            ))

    # -------------------------------------------------------------------------
    # Phase C: Finalise
    # -------------------------------------------------------------------------
    finished_utc = _now()
    manifest = _build_manifest(
        session_id, serial, started_utc, finished_utc, home_state_id,
        stop_conditions, stop_reason, states, transitions, attempts,
    )
    save_session_manifest(session_dir, manifest)
    save_registry(session_dir, states, transitions, attempts)
    generate_session_report(session_dir, manifest)

    total_failures = sum(1 for t in transitions if t["outcome"] == "failed")
    print(
        f"\n[v2_explore] Done — {len(states)} states, {len(transitions)} transitions, "
        f"{len(attempts)} attempts, {total_failures} failures. Stop: {stop_reason}"
    )
    print(f"[v2_explore] Session dir: {session_dir}")
    return session_dir


def _build_manifest(
    session_id: str,
    serial: str,
    started_utc: str,
    finished_utc: str | None,
    home_capture_id: str,
    stop_conditions: dict,
    stop_reason: str,
    states: list,
    transitions: list,
    attempts: list,
) -> dict[str, Any]:
    return {
        "session_id":      session_id,
        "started_utc":     started_utc,
        "finished_utc":    finished_utc,
        "device_serial":   serial,
        "home_capture_id": home_capture_id,
        "stop_conditions": stop_conditions,
        "stop_reason":     stop_reason,
        "summary": {
            "total_states":      len(states),
            "total_transitions": len(transitions),
            "total_attempts":    len(attempts),
            "total_failures":    sum(1 for t in transitions if t["outcome"] == "failed"),
        },
        "states":      states,
        "transitions": transitions,
        "attempts":    attempts,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="v2 interaction-driven Android UI exploration (BFS from Home).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--serial",           default=None,  help="ADB device serial.")
    p.add_argument("--adb-root",         default="auto", choices=["auto", "required", "never"])
    p.add_argument("--max-states",       type=int,   default=50)
    p.add_argument("--max-transitions",  type=int,   default=200)
    p.add_argument("--max-depth",        type=int,   default=1)
    p.add_argument("--timeout-seconds",  type=float, default=3600.0)
    p.add_argument("--settle-ms",        type=int,   default=2000)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override sessions root directory.",
    )
    return p.parse_args()


def main() -> int:
    signal.signal(signal.SIGINT, _on_interrupt)
    args = _parse_args()

    try:
        session_dir = explore(
            serial=args.serial,
            adb_root_mode=args.adb_root,
            max_states=args.max_states,
            max_transitions=args.max_transitions,
            max_depth=args.max_depth,
            timeout_seconds=args.timeout_seconds,
            settle_ms=args.settle_ms,
            output_dir=args.output_dir,
        )
        print(f"Session report: {session_dir / 'session-report.html'}")
        return 0
    except CaptureFatalError as exc:
        print(f"Fatal capture error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
