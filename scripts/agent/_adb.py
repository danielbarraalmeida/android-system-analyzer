"""Internal ADB primitive layer used by the agent tools.

Trimmed from the historical ``current_screen_report.py`` UI-scraper to
the minimum surface needed by the RAG system analyzer:

- Run an ``adb`` (sub)command with structured logging.
- Resolve / pick a connected device serial.
- Enable adb root with one of three policies.
- Capture a UI dump + screenshot (only used by ``capture_home_screen``).
- Read focused package/activity, screen size, density.
- Compute a stable signature + element count from a UI dump (no full
  element-model construction — system inspection does not need it).

Every function that issues an ADB invocation accepts ``command_log`` and
appends a structured entry for provenance.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------

def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _run_adb(
    args: list[str],
    *,
    serial: str | None,
    command_log: list[dict[str, Any]],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``adb [-s serial] <args>``. Always logs; raises only when ``check``."""
    cmd: list[str] = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    started = _now_utc()
    try:
        completed = subprocess.run(
            cmd, text=True, capture_output=True, check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "adb executable not found. Install Android SDK Platform Tools "
            "and ensure 'adb' is on your PATH."
        ) from exc
    finished = _now_utc()
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    completed.returncode,
        "stdout":       (completed.stdout or "").strip(),
        "stderr":       (completed.stderr or "").strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"ADB command failed (exit {completed.returncode}): {' '.join(cmd)}\n"
            f"stderr: {(completed.stderr or '').strip()}"
        )
    return completed


# ---------------------------------------------------------------------------
# Serial resolution
# ---------------------------------------------------------------------------

def _resolve_serial(
    command_log: list[dict[str, Any]],
    explicit_serial: str | None,
) -> str:
    if explicit_serial:
        return explicit_serial
    completed = _run_adb(["devices"], serial=None, command_log=command_log)
    devices: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        raise RuntimeError(
            "No connected Android devices found. Connect a device or start "
            "an emulator and confirm with 'adb devices'."
        )
    if len(devices) > 1:
        raise RuntimeError(
            f"Multiple devices connected ({', '.join(devices)}). "
            "Provide --serial <device_serial> to select one."
        )
    return devices[0]


# ---------------------------------------------------------------------------
# Root elevation
# ---------------------------------------------------------------------------

def _ensure_adb_root(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
    adb_root_mode: str,
) -> None:
    """Attempt ``adb root`` according to policy.

    Modes:
    - ``required``  — abort if root cannot be obtained.
    - ``preferred`` — try, warn if unavailable, continue.
    - ``skipped``   — do not attempt.

    The legacy mode names ``auto`` / ``never`` are accepted as aliases.

    Robustness notes:
    - ``adb root`` restarts the device's adbd. For TCP/IP serials
      (``host:port``) this drops the connection, so ``adb root`` itself
      often exits non-zero even when the daemon successfully restarts
      as root. We therefore treat the exit code as advisory and use
      ``adb shell id`` as the source of truth, reconnecting TCP devices
      and retrying briefly while the daemon comes back up.
    """
    import time

    if adb_root_mode in ("skipped", "never"):
        return

    root_cmd = _run_adb(["root"], serial=serial, command_log=command_log, check=False)
    root_out = "\n".join([root_cmd.stdout or "", root_cmd.stderr or ""]).lower()

    already_root      = "already running as root" in root_out
    production_locked = "cannot run as root in production builds" in root_out

    # TCP/IP serial → adbd restart drops the connection. Reconnect.
    is_tcp = ":" in serial and serial.rsplit(":", 1)[-1].isdigit()
    if is_tcp and not already_root:
        _run_adb(["connect", serial], serial=None,
                 command_log=command_log, check=False)

    # Poll until adbd is back and we can run a shell command. Up to ~5s.
    uid0 = False
    last_id_out = ""
    for attempt in range(10):
        _run_adb(["wait-for-device"], serial=serial,
                 command_log=command_log, check=False)
        shell_id = _run_adb(
            ["shell", "id"], serial=serial,
            command_log=command_log, check=False,
        )
        last_id_out = (shell_id.stdout or "") + (shell_id.stderr or "")
        if shell_id.returncode == 0 and "uid=0" in (shell_id.stdout or ""):
            uid0 = True
            break
        if shell_id.returncode == 0 and (shell_id.stdout or "").strip():
            # Got a real, non-root shell response — daemon is up but
            # not root. No point waiting longer.
            break
        time.sleep(0.5)
        if is_tcp:
            _run_adb(["connect", serial], serial=None,
                     command_log=command_log, check=False)

    if uid0:
        return

    if production_locked:
        msg = "adb root unavailable on production-locked build; continuing without root."
    elif root_cmd.returncode != 0 and not last_id_out.strip():
        msg = (
            "adb root failed and device became unreachable; continuing without root. "
            f"exit={root_cmd.returncode} stderr={(root_cmd.stderr or '').strip()!r}"
        )
    else:
        msg = "adb root was requested but shell is still non-root; continuing without root."

    if adb_root_mode == "required":
        raise RuntimeError(msg)
    warnings.append(msg)


# ---------------------------------------------------------------------------
# UI dump + screenshot (used only by capture_home_screen)
# ---------------------------------------------------------------------------

def _capture_ui_dump(
    serial: str,
    capture_dir: Path,
    command_log: list[dict[str, Any]],
) -> Path:
    remote_path = "/sdcard/window_dump.xml"
    local_path = capture_dir / "window_dump.xml"
    _run_adb(
        ["shell", "uiautomator", "dump", "--windows", remote_path],
        serial=serial, command_log=command_log,
    )
    _run_adb(
        ["pull", remote_path, str(local_path)],
        serial=serial, command_log=command_log,
    )
    return local_path


def _capture_screenshot(
    serial: str,
    capture_dir: Path,
    command_log: list[dict[str, Any]],
) -> Path:
    local_path = capture_dir / "screen.png"
    cmd = ["adb", "-s", serial, "exec-out", "screencap", "-p"]
    started = _now_utc()
    with local_path.open("wb") as fh:
        completed = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.PIPE, check=False,
        )
    finished = _now_utc()
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    completed.returncode,
        "stdout":       "",
        "stderr":       (completed.stderr or b"").decode("utf-8", errors="replace").strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    if completed.returncode != 0:
        raise RuntimeError(
            f"Screenshot capture failed (exit {completed.returncode})"
        )
    return local_path


# ---------------------------------------------------------------------------
# Focus / size / density probes
# ---------------------------------------------------------------------------

_FOCUS_RE = re.compile(r"([A-Za-z0-9_$.]+)/([A-Za-z0-9_$.]+)")


def _get_package_activity(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, str]:
    completed = _run_adb(
        ["shell", "dumpsys", "window", "windows"],
        serial=serial, command_log=command_log, check=False,
    )
    for line in (completed.stdout or "").splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            m = _FOCUS_RE.search(line)
            if m:
                return m.group(1), m.group(2)

    # Fallback for OEM builds where ``dumpsys window`` omits the focus line.
    fallback = _run_adb(
        ["shell", "dumpsys", "activity", "top"],
        serial=serial, command_log=command_log, check=False,
    )
    for line in (fallback.stdout or "").splitlines():
        if " ACTIVITY " in line:
            m = _FOCUS_RE.search(line)
            if m:
                return m.group(1), m.group(2)

    warnings.append("Unable to determine focused package/activity from dumpsys.")
    return "unknown.package", "unknown.activity"


def _get_screen_size(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[int, int]:
    completed = _run_adb(
        ["shell", "wm", "size"], serial=serial, command_log=command_log, check=False,
    )
    m = re.search(r"(\d+)x(\d+)", completed.stdout or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    warnings.append("Could not determine screen size from 'wm size'.")
    return 0, 0


def _get_screen_density(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> int | None:
    completed = _run_adb(
        ["shell", "wm", "density"], serial=serial, command_log=command_log, check=False,
    )
    m = re.search(r"\d+", completed.stdout or "")
    if m:
        return int(m.group())
    warnings.append("Could not determine screen density from 'wm density'.")
    return None


# ---------------------------------------------------------------------------
# UI-dump signature (cheap dedupe key — system analyzer does not need
# the full element model)
# ---------------------------------------------------------------------------

def extract_ui_signature(xml_path: Path) -> tuple[str, int]:
    """Return ``(sha1_signature, node_count)`` for a uiautomator XML dump.

    The signature is the SHA-1 of the sorted set of
    ``(resource-id, class, bounds)`` tuples — stable across label/text
    changes but sensitive to structural differences.
    """
    try:
        tree = ET.parse(str(xml_path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse UI dump XML: {exc}") from exc

    keys: list[str] = []
    count = 0
    for node in tree.iter("node"):
        count += 1
        keys.append(
            f"{node.get('resource-id', '')}|"
            f"{node.get('class', '')}|"
            f"{node.get('bounds', '')}"
        )
    keys.sort()
    sig = hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()
    return sig, count
