"""Unit tests for v2 serial resolution helper.

Real ADB is never invoked from these tests.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import v2_explore as v2


pytestmark = pytest.mark.unit


class _FakeRun:
    """Pluggable subprocess.run replacement keyed by full adb command."""

    def __init__(self, mapping: dict[str, dict[str, Any]]) -> None:
        self._mapping = mapping
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        key = " ".join(cmd)
        spec = self._mapping.get(key, {"returncode": 0, "stdout": "", "stderr": ""})
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=spec.get("returncode", 0),
            stdout=spec.get("stdout", ""),
            stderr=spec.get("stderr", ""),
        )


def test_resolve_serial_explicit_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit serial must be returned immediately with no ADB command."""
    fake = _FakeRun({})
    monkeypatch.setattr(v2.subprocess, "run", fake)
    assert v2._resolve_serial("ABC123") == "ABC123"
    assert fake.calls == []


def test_resolve_serial_single_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single connected device must be auto-selected."""
    fake = _FakeRun({
        "adb devices": {
            "returncode": 0,
            "stdout": "List of devices attached\nABC123\tdevice\n",
        }
    })
    monkeypatch.setattr(v2.subprocess, "run", fake)
    assert v2._resolve_serial(None) == "ABC123"


def test_resolve_serial_no_devices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """No connected devices must raise a clear RuntimeError."""
    fake = _FakeRun({
        "adb devices": {
            "returncode": 0,
            "stdout": "List of devices attached\n",
        }
    })
    monkeypatch.setattr(v2.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="No connected"):
        v2._resolve_serial(None)


def test_resolve_serial_multiple_devices_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple devices without explicit serial must raise a clear RuntimeError."""
    fake = _FakeRun({
        "adb devices": {
            "returncode": 0,
            "stdout": "List of devices attached\nA\tdevice\nB\tdevice\n",
        }
    })
    monkeypatch.setattr(v2.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="Multiple devices"):
        v2._resolve_serial(None)


def test_resolve_serial_adb_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-zero adb devices exit code must raise RuntimeError."""
    fake = _FakeRun({
        "adb devices": {
            "returncode": 1,
            "stderr": "adb server is out of date",
        }
    })
    monkeypatch.setattr(v2.subprocess, "run", fake)
    with pytest.raises(RuntimeError, match="ADB command failed"):
        v2._resolve_serial(None)
