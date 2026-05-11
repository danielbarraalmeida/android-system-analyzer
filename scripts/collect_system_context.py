#!/usr/bin/env python3
"""Collect exhaustive system-level information from a connected Android device.

Every sub-collector runs a single ADB command (or small set), stores the full
raw stdout, and stores a best-effort parsed dict. Parsing is deliberately
lenient: if a field cannot be extracted the raw string is preserved and
parsing simply omits that field with no exception raised.

Public API
----------
collect_system_context(serial, command_log, warnings) -> dict
    Run all sub-collectors and return a single dict ready to serialise.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from typing import Any


# ---------------------------------------------------------------------------
# Internal ADB runner
# ---------------------------------------------------------------------------

def _run(
    args: list[str],
    serial: str,
    command_log: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    """Run an ADB shell command and append to command_log. Never raises on non-zero exit."""
    cmd = ["adb", "-s", serial, *args]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        result = subprocess.CompletedProcess(
            args=cmd, returncode=127,
            stdout="", stderr="adb executable not found",
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
    return result


def _shell(
    cmd: str,
    serial: str,
    command_log: list[dict[str, Any]],
) -> str:
    """Run `adb shell <cmd>` and return stdout. Empty string on failure."""
    return _run(["shell", cmd], serial, command_log).stdout.strip()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_getprop(raw: str) -> dict[str, str]:
    """Parse `getprop` output: [key]: [value] lines."""
    props: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            key_end = line.index("]:")
            key = line[1:key_end]
            val_start = line.index("[", key_end) + 1
            val = line[val_start:-1] if line.endswith("]") else ""
            props[key] = val
        except (ValueError, IndexError):
            continue
    return props


def _parse_pm_packages(raw: str) -> list[dict[str, str]]:
    """Parse `pm list packages -f -i --show-versioncode` output."""
    pkgs: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        # Format: package:<apk_path>=<package_name>  installer=<installer>  versionCode:<code>
        parts: dict[str, str] = {}
        seg = line[len("package:"):]
        eq = seg.find("=")
        if eq != -1:
            parts["apk_path"] = seg[:eq]
            rest = seg[eq + 1:]
        else:
            rest = seg
        # package name ends at first space
        sp = rest.find(" ")
        parts["package_name"] = rest[:sp] if sp != -1 else rest
        for token in rest[sp:].split() if sp != -1 else []:
            if "=" in token:
                k, _, v = token.partition("=")
                parts[k.strip()] = v.strip()
        pkgs.append(parts)
    return pkgs


def _parse_pm_features(raw: str) -> list[str]:
    features: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("feature:"):
            features.append(line[len("feature:"):])
    return features


def _parse_key_value_lines(raw: str, sep: str = "=") -> dict[str, str]:
    """Generic parser for lines of the form key=value or key: value."""
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if sep in line:
            k, _, v = line.partition(sep)
            result[k.strip()] = v.strip()
    return result


def _parse_settings(raw: str) -> dict[str, str]:
    """Parse `settings list *` output (key=value per line)."""
    return _parse_key_value_lines(raw, "=")


def _parse_battery(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" in line:
            k, _, v = line.partition(":")
            parsed[k.strip()] = v.strip()
    return parsed


def _parse_meminfo(raw: str) -> dict[str, str]:
    """Parse /proc/meminfo into key: value dict (stripping 'kB')."""
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" in line:
            k, _, v = line.partition(":")
            parsed[k.strip()] = v.strip()
    return parsed


def _parse_df(raw: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = raw.splitlines()
    if not lines:
        return rows
    headers = lines[0].split()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def _parse_sensors(raw: str) -> list[dict[str, str]]:
    """Extract sensor list from dumpsys sensorservice output."""
    sensors: list[dict[str, str]] = []
    in_list = False
    for line in raw.splitlines():
        stripped = line.strip()
        if "Sensor List" in stripped:
            in_list = True
            continue
        if in_list:
            if stripped.startswith("0x") or (stripped and stripped[0].isdigit()):
                sensors.append({"raw_line": stripped})
            elif stripped == "" and sensors:
                break
    return sensors


def _parse_input_devices(raw: str) -> list[dict[str, str]]:
    """Extract device name blocks from dumpsys input."""
    devices: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("Input Device"):
            if current:
                devices.append(current)
            current = {"header": stripped}
        elif "Name:" in stripped and current is not None:
            current["name"] = stripped.split("Name:", 1)[1].strip().strip('"')
        elif "Classes:" in stripped and current is not None:
            current["classes"] = stripped.split("Classes:", 1)[1].strip()
    if current:
        devices.append(current)
    return devices


def _parse_display(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        for key in ("mDisplayId", "mBaseDisplayInfo", "mOverrideDisplayInfo",
                    "width", "height", "rotation", "density", "refreshRate",
                    "colorMode", "isHdr", "mDisplayState"):
            if stripped.startswith(key):
                parsed[key] = stripped
                break
    return parsed


def _parse_wifi(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        for field in ("mWifiInfo", "SSID", "BSSID", "rssi", "linkSpeed",
                      "ipAddress", "networkId", "Wi-Fi is", "mNetworkState"):
            if field in stripped:
                parsed[field] = stripped
                break
    return parsed


def _parse_bluetooth(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        for field in ("state", "address", "name", "bonded", "Bonded devices",
                      "Connected devices", "enabled"):
            if field.lower() in stripped.lower():
                parsed.setdefault(field, stripped)
    return parsed


def _parse_connectivity(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        for field in ("Active network", "NetworkInfo", "LinkProperties",
                      "defaultNetwork", "mActiveDefaultNetwork", "type:",
                      "DnsServers"):
            if field in stripped:
                parsed.setdefault(field, stripped)
                break
    return parsed


def _parse_location(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        for field in ("gps", "network", "passive", "LocationProvider",
                      "isEnabled", "latitude", "longitude", "accuracy"):
            if field.lower() in stripped.lower():
                parsed.setdefault(field, stripped)
    return parsed


def _parse_audio(raw: str) -> dict[str, Any]:
    """Parse the most useful fields from dumpsys audio."""
    parsed: dict[str, Any] = {"streams": {}, "devices": [], "focus": []}
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- STREAM_"):
            parsed["streams"][stripped.split()[1]] = stripped
        elif "AudioAttributes" in stripped:
            parsed["focus"].append(stripped)
        elif stripped.startswith("Output devices:") or stripped.startswith("Input devices:"):
            parsed["devices"].append(stripped)
    return parsed


def _parse_processes(raw: str) -> list[dict[str, str]]:
    """Parse dumpsys meminfo --short: process name + PSS."""
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        parts = stripped.split()
        if len(parts) >= 2 and parts[0].isdigit():
            rows.append({"pss_kb": parts[0], "process": " ".join(parts[1:])})
    return rows


# ---------------------------------------------------------------------------
# Sub-collectors — each returns a standardised dict
# ---------------------------------------------------------------------------

def _collect(
    label: str,
    cmd: str,
    serial: str,
    command_log: list[dict[str, Any]],
    parser,
) -> dict[str, Any]:
    t = _now()
    result = _run(["shell", cmd], serial, command_log)
    raw = result.stdout.strip()
    try:
        parsed = parser(raw)
    except Exception as exc:  # noqa: BLE001 — parser bug, keep going
        parsed = {"parse_error": str(exc)}
    return {
        "command":       f"adb shell {cmd}",
        "exit_code":     result.returncode,
        "collected_utc": t,
        "raw":           raw,
        "parsed":        parsed,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_system_context(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    """Run all sub-collectors and return a single system-context dict.

    This is designed to be called once per session from Home.
    No sub-collector failure aborts the rest; failures are recorded inline.
    """

    def run(label: str, cmd: str, parser) -> dict[str, Any]:
        return _collect(label, cmd, serial, command_log, parser)

    ctx: dict[str, Any] = {
        "collected_at_utc": _now(),
        "device_serial":    serial,
    }

    # Build properties — all ~200 Android system properties
    ctx["build_properties"] = run(
        "build_properties",
        "getprop",
        _parse_getprop,
    )

    # Installed packages (third-party, with APK path, installer, version code)
    ctx["installed_packages"] = run(
        "installed_packages",
        "pm list packages -f -i --show-versioncode",
        _parse_pm_packages,
    )

    # Device hardware / software features
    ctx["device_features"] = run(
        "device_features",
        "pm list features",
        _parse_pm_features,
    )

    # All declared permissions
    ctx["declared_permissions"] = run(
        "declared_permissions",
        "pm list permissions -f",
        lambda r: [l.strip() for l in r.splitlines() if l.strip()],
    )

    # Audio state — streams, volumes, routing, focus holders
    ctx["audio_state"] = run(
        "audio_state",
        "dumpsys audio",
        _parse_audio,
    )

    # Android settings (three namespaces)
    ctx["settings_system"] = run(
        "settings_system",
        "settings list system",
        _parse_settings,
    )
    ctx["settings_secure"] = run(
        "settings_secure",
        "settings list secure",
        _parse_settings,
    )
    ctx["settings_global"] = run(
        "settings_global",
        "settings list global",
        _parse_settings,
    )

    # Battery and power
    ctx["battery_state"] = run(
        "battery_state",
        "dumpsys battery",
        _parse_battery,
    )

    # Display — size, density, rotation, refresh rate, HDR, colour mode
    ctx["display_info"] = run(
        "display_info",
        "dumpsys display",
        _parse_display,
    )

    # Memory (/proc/meminfo)
    ctx["memory_info"] = run(
        "memory_info",
        "cat /proc/meminfo",
        _parse_meminfo,
    )

    # CPU (/proc/cpuinfo)
    ctx["cpu_info"] = run(
        "cpu_info",
        "cat /proc/cpuinfo",
        lambda r: {"raw_lines": r.splitlines()},
    )

    # Storage (filesystem usage)
    ctx["storage_info"] = run(
        "storage_info",
        "df -h",
        _parse_df,
    )

    # Network / connectivity
    ctx["network_state"] = run(
        "network_state",
        "dumpsys connectivity",
        _parse_connectivity,
    )

    # WiFi
    ctx["wifi_state"] = run(
        "wifi_state",
        "dumpsys wifi",
        _parse_wifi,
    )

    # Bluetooth
    ctx["bluetooth_state"] = run(
        "bluetooth_state",
        "dumpsys bluetooth_manager",
        _parse_bluetooth,
    )

    # Sensors
    ctx["sensors"] = run(
        "sensors",
        "dumpsys sensorservice",
        _parse_sensors,
    )

    # Input devices (touchscreen, keyboards, etc.)
    ctx["input_devices"] = run(
        "input_devices",
        "dumpsys input",
        _parse_input_devices,
    )

    # Running services
    ctx["running_services"] = run(
        "running_services",
        "dumpsys activity services",
        lambda r: {"raw_lines": r.splitlines()},
    )

    # Running processes (PSS per process)
    ctx["running_processes"] = run(
        "running_processes",
        "dumpsys meminfo --short",
        _parse_processes,
    )

    # Window manager (display orientation, cutouts)
    ctx["window_manager"] = run(
        "window_manager",
        "dumpsys window displays",
        lambda r: {"raw_lines": r.splitlines()},
    )

    # Location (providers, last known location)
    ctx["location_state"] = run(
        "location_state",
        "dumpsys location",
        _parse_location,
    )

    return ctx
