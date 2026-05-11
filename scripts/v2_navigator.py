#!/usr/bin/env python3
"""v2 navigation and state-engine helpers.

All functions that issue ADB commands accept a `command_log` list and append
a structured entry for every invocation (consistent with Layer 1 in
current_screen_report.py). All timing waits are implemented via ADB polling
rather than Python sleep so the log reflects real device behaviour.

Public API
----------
navigate_to_home(serial, command_log, warnings, settle_ms)
wait_for_ui_settle(serial, command_log, settle_ms)
compute_state_signature(elements) -> str
execute_tap(serial, x, y, command_log) -> bool
execute_back(serial, command_log, settle_ms)
select_tap_candidates(elements) -> list[dict]
is_same_state(sig_a, sig_b) -> bool
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import time
from typing import Any


# ---------------------------------------------------------------------------
# Internal ADB runner (mirrors Layer 1 in current_screen_report.py)
# ---------------------------------------------------------------------------

def _run_adb(
    args: list[str],
    serial: str,
    command_log: list[dict[str, Any]],
    *,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["adb", "-s", serial, *args]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        result = subprocess.CompletedProcess(
            args=cmd, returncode=127,
            stdout="", stderr="adb not found",
        )
    finished = dt.datetime.now(dt.timezone.utc)
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    result.returncode,
        "stdout":       result.stdout.strip(),
        "stderr":       result.stderr.strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    if check and result.returncode != 0:
        raise RuntimeError(
            f"ADB command failed (exit {result.returncode}): {' '.join(cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def _shell(
    cmd: str,
    serial: str,
    command_log: list[dict[str, Any]],
) -> str:
    return _run_adb(["shell", cmd], serial, command_log).stdout.strip()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def navigate_to_home(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
    settle_ms: int = 2000,
) -> None:
    """Press the Home key and wait for the UI to settle.

    Uses KEYCODE_HOME (3). On automotive/launcher devices this navigates to
    the primary launcher screen regardless of current state.
    """
    _run_adb(["shell", "input", "keyevent", "KEYCODE_HOME"], serial, command_log)
    wait_for_ui_settle(serial, command_log, settle_ms)


def execute_back(
    serial: str,
    command_log: list[dict[str, Any]],
    settle_ms: int = 1000,
) -> None:
    """Press the Back key and wait for the UI to settle."""
    _run_adb(["shell", "input", "keyevent", "KEYCODE_BACK"], serial, command_log)
    wait_for_ui_settle(serial, command_log, settle_ms)


def wait_for_ui_settle(
    serial: str,
    command_log: list[dict[str, Any]],
    settle_ms: int = 2000,
) -> None:
    """Poll window focus twice (500 ms apart) to detect stabilisation.

    The full settle_ms budget is always spent regardless of poll results so
    that animated transitions on slower devices (e.g. BMW IDC23) have time
    to complete before a screenshot is taken.
    """
    half = settle_ms / 2 / 1000.0  # seconds
    _shell("dumpsys window windows | grep mCurrentFocus", serial, command_log)
    time.sleep(half)
    _shell("dumpsys window windows | grep mCurrentFocus", serial, command_log)
    time.sleep(half)


# ---------------------------------------------------------------------------
# State identity
# ---------------------------------------------------------------------------

def compute_state_signature(elements: list[dict[str, Any]]) -> str:
    """Deterministic SHA-1 fingerprint of a screen state.

    Based on the sorted set of element_id values — stable across label
    changes because element_id is text-independent (see v1 contract §4).
    Returns a 40-character hex string.
    """
    ids = sorted(e["element_id"] for e in elements)
    payload = "\n".join(ids).encode()
    return hashlib.sha1(payload).hexdigest()


def is_same_state(sig_a: str, sig_b: str) -> bool:
    return sig_a == sig_b


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def execute_tap(
    serial: str,
    x: int,
    y: int,
    command_log: list[dict[str, Any]],
) -> bool:
    """Issue `adb shell input tap x y`. Returns True on success."""
    result = _run_adb(["shell", "input", "tap", str(x), str(y)], serial, command_log)
    return result.returncode == 0


def select_tap_candidates(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter and sort elements that are eligible for tap interaction.

    Eligibility:
      - is_interaction_candidate is True
      - "tap" appears in action_types
      - enabled is True
      - bounds are non-zero (width > 0 and height > 0)

    Ordering: (depth ASC, sibling_index ASC, xml_index_preorder ASC)
    This is a deterministic total order independent of text content.
    """
    candidates = [
        e for e in elements
        if e.get("is_interaction_candidate") is True
        and "tap" in e.get("action_types", [])
        and e.get("enabled") is True
        and e.get("width", 0) > 0
        and e.get("height", 0) > 0
    ]
    return sorted(
        candidates,
        key=lambda e: (
            e.get("depth", 0),
            e.get("sibling_index", 0),
            e.get("xml_index_preorder", 0),
        ),
    )
