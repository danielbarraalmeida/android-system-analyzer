"""Navigation primitives used by the agent tools.

Trimmed from the historical ``v2_navigator.py`` BFS engine to the
minimum needed by the RAG system analyzer: pressing HOME and waiting
for the UI to settle before a single optional ``capture_home_screen``.

The settle implementation issues two ``dumpsys window | grep
mCurrentFocus`` probes with a Python sleep in between so the device
has time to finish animated transitions before downstream callers
take a screenshot.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import time
from typing import Any


def _run_adb(
    args: list[str],
    serial: str,
    command_log: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    cmd = ["adb", "-s", serial, *args]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        result = subprocess.CompletedProcess(
            args=cmd, returncode=127, stdout="", stderr="adb not found",
        )
    finished = dt.datetime.now(dt.timezone.utc)
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    result.returncode,
        "stdout":       (result.stdout or "").strip(),
        "stderr":       (result.stderr or "").strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    return result


def navigate_to_home(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
    settle_ms: int = 2000,
) -> None:
    """Press HOME (keyevent 3) and wait for the UI to settle."""
    _run_adb(
        ["shell", "input", "keyevent", "KEYCODE_HOME"], serial, command_log,
    )
    wait_for_ui_settle(serial, command_log, settle_ms)


def wait_for_ui_settle(
    serial: str,
    command_log: list[dict[str, Any]],
    settle_ms: int = 2000,
) -> None:
    """Poll focused window twice ``settle_ms / 2`` apart."""
    half = max(0.0, settle_ms) / 2 / 1000.0
    _run_adb(
        ["shell", "dumpsys window windows | grep mCurrentFocus"],
        serial, command_log,
    )
    time.sleep(half)
    _run_adb(
        ["shell", "dumpsys window windows | grep mCurrentFocus"],
        serial, command_log,
    )
    time.sleep(half)
