"""Unit tests for ADB transport helpers using subprocess monkeypatching.

Real ADB is never invoked from these tests.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import current_screen_report as csr


pytestmark = pytest.mark.unit


class _FakeRun:
    """Pluggable subprocess.run replacement keyed on the trailing args joined by space."""

    def __init__(self, mapping: dict[str, dict[str, Any]]) -> None:
        self._mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        # Match by the suffix after "adb [-s SERIAL]".
        idx = 1
        if idx < len(cmd) and cmd[idx] == "-s":
            idx = 3
        key = " ".join(cmd[idx:])
        spec = self._mapping.get(key, {"returncode": 0, "stdout": "", "stderr": ""})
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=spec.get("returncode", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
        )


# ─────────────────────────── _resolve_serial ─────────────────────────────────

def test_resolve_serial_explicit_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller provides an explicit device serial, the function must return it immediately without invoking any ADB command — fast-path for scripted pipelines."""
    fake = _FakeRun({})
    monkeypatch.setattr(csr.subprocess, "run", fake)
    serial = csr._resolve_serial(command_log=[], explicit_serial="ABC123")
    assert serial == "ABC123"
    assert fake.calls == []  # no adb invocation when serial is provided


def test_resolve_serial_single_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """When exactly one device is connected, its serial is returned automatically and the 'adb devices' command is recorded in the command log."""
    fake = _FakeRun({
        "devices": {"returncode": 0, "stdout": "List of devices attached\nABC123\tdevice\n"},
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    log: list[dict[str, Any]] = []
    assert csr._resolve_serial(log, None) == "ABC123"
    assert len(log) == 1


def test_resolve_serial_no_devices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no devices are connected, the function must raise RuntimeError with a message containing 'No connected' so the user knows immediately that no device is available."""
    fake = _FakeRun({"devices": {"returncode": 0, "stdout": "List of devices attached\n"}})
    monkeypatch.setattr(csr.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="No connected"):
        csr._resolve_serial([], None)


def test_resolve_serial_multiple_devices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If multiple devices are connected but no serial was specified, the function must raise RuntimeError with 'Multiple devices' — the user must be explicit to avoid acting on the wrong device."""
    fake = _FakeRun({
        "devices": {
            "returncode": 0,
            "stdout": "List of devices attached\nA\tdevice\nB\tdevice\n",
        },
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="Multiple devices"):
        csr._resolve_serial([], None)


# ─────────────────────────── _ensure_adb_root ────────────────────────────────

def test_ensure_adb_root_never_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """With adb_root_mode='never', the function must not run any ADB command and must not add any warnings — designed for use on production devices where root escalation is unwanted."""
    fake = _FakeRun({})
    monkeypatch.setattr(csr.subprocess, "run", fake)
    warnings: list[str] = []
    csr._ensure_adb_root("S", command_log=[], warnings=warnings, adb_root_mode="never")
    assert fake.calls == []
    assert warnings == []


def test_ensure_adb_root_auto_already_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the ADB daemon is already running as root ('adbd is already running as root'), the auto mode must complete silently without adding any warnings."""
    fake = _FakeRun({
        "root":           {"returncode": 0, "stdout": "adbd is already running as root", "stderr": ""},
        "wait-for-device": {"returncode": 0},
        "shell id":       {"returncode": 0, "stdout": "uid=0(root) gid=0(root)"},
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    warnings: list[str] = []
    csr._ensure_adb_root("S", command_log=[], warnings=warnings, adb_root_mode="auto")
    assert warnings == []


def test_ensure_adb_root_auto_production_locked_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the device refuses root escalation ('production builds'), auto mode must add a warning containing 'production-locked' and continue rather than aborting — metadata may be limited but the capture proceeds."""
    fake = _FakeRun({
        "root": {
            "returncode": 0,
            "stdout":     "adbd cannot run as root in production builds",
            "stderr":     "",
        },
        "wait-for-device": {"returncode": 0},
        "shell id":       {"returncode": 0, "stdout": "uid=2000(shell)"},
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    warnings: list[str] = []
    csr._ensure_adb_root("S", command_log=[], warnings=warnings, adb_root_mode="auto")
    assert any("production-locked" in w for w in warnings)


def test_ensure_adb_root_required_raises_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """With adb_root_mode='required', a production-locked device must raise RuntimeError immediately instead of continuing with limited permissions — used when root is mandatory for the capture to be meaningful."""
    fake = _FakeRun({
        "root": {
            "returncode": 0,
            "stdout":     "adbd cannot run as root in production builds",
            "stderr":     "",
        },
        "wait-for-device": {"returncode": 0},
        "shell id":       {"returncode": 0, "stdout": "uid=2000(shell)"},
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="production-locked"):
        csr._ensure_adb_root("S", command_log=[], warnings=[], adb_root_mode="required")


# ─────────────────────────── _get_package_activity ───────────────────────────

def test_get_package_activity_from_window_focus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parses the currently focused window from 'dumpsys window windows' output to extract the foreground package name (e.g. 'com.example.app') and activity name (e.g. '.MainActivity')."""
    fake = _FakeRun({
        "shell dumpsys window windows": {
            "returncode": 0,
            "stdout":     "  mCurrentFocus=Window{... com.example.app/.MainActivity}\n",
        },
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    pkg, act = csr._get_package_activity("S", command_log=[], warnings=[])
    assert pkg == "com.example.app"
    assert act == ".MainActivity"


def test_get_package_activity_fallback_to_activity_top(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the window focus output has no recognisable package/activity markers (common on MIUI devices), the function falls back to parsing 'dumpsys activity top' for the most recent foreground activity."""
    fake = _FakeRun({
        "shell dumpsys window windows": {"returncode": 0, "stdout": "no focus markers here\n"},
        "shell dumpsys activity top": {
            "returncode": 0,
            "stdout":     "  ACTIVITY com.example.app/.MainActivity 1234 pid=1\n",
        },
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    warnings: list[str] = []
    pkg, act = csr._get_package_activity("S", command_log=[], warnings=warnings)
    assert pkg == "com.example.app"
    assert act == ".MainActivity"
    assert warnings == []


def test_get_package_activity_unknown_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """When both parsers fail to identify a package, the function returns 'unknown.package' and adds a warning — the capture can still complete, but the context section will be incomplete."""
    fake = _FakeRun({
        "shell dumpsys window windows": {"returncode": 0, "stdout": ""},
        "shell dumpsys activity top":   {"returncode": 0, "stdout": ""},
    })
    monkeypatch.setattr(csr.subprocess, "run", fake)
    warnings: list[str] = []
    pkg, _ = csr._get_package_activity("S", command_log=[], warnings=warnings)
    assert pkg == "unknown.package"
    assert any("Unable to determine" in w for w in warnings)


# ─────────────────────────── _get_screen_size / _get_screen_density ──────────

def test_get_screen_size_parses_wm_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parses the 'Physical size: WxH' line from 'adb shell wm size' into a (width, height) integer tuple used to compute element coverage percentages."""
    fake = _FakeRun({"shell wm size": {"returncode": 0, "stdout": "Physical size: 1080x1920"}})
    monkeypatch.setattr(csr.subprocess, "run", fake)
    assert csr._get_screen_size("S", command_log=[], warnings=[]) == (1080, 1920)


def test_get_screen_density_parses_wm_density(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parses the 'Physical density: N' line from 'adb shell wm density' into an integer DPI value stored in the capture context."""
    fake = _FakeRun({"shell wm density": {"returncode": 0, "stdout": "Physical density: 480"}})
    monkeypatch.setattr(csr.subprocess, "run", fake)
    assert csr._get_screen_density("S", command_log=[], warnings=[]) == 480
