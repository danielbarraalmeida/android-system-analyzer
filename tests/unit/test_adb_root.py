"""Unit tests for ``_ensure_adb_root`` resilience.

We monkey-patch ``_run_adb`` so no real ``adb`` is invoked. The helper's
contract is: ``adb shell id`` is the source of truth for root state;
``adb root``'s exit code is advisory; TCP/IP serials must be reconnected
after the daemon restart.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from agent import _adb


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout     = stdout
        self.stderr     = stderr


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, list[_FakeCompleted]],
    calls:     list[list[str]],
) -> None:
    """Install a fake ``_run_adb`` driven by a per-command response queue."""

    def _fake(args: list[str], *, serial: str | None,
              command_log: list[dict[str, Any]], check: bool = True) -> _FakeCompleted:
        calls.append(list(args))
        key   = args[0]
        queue = responses.get(key, [])
        if not queue:
            return _FakeCompleted(returncode=0, stdout="")
        item = queue.pop(0)
        command_log.append({"command": " ".join(args), "exit_code": item.returncode})
        return item

    monkeypatch.setattr(_adb, "_run_adb", _fake)
    # Skip real time.sleep in the polling loop.
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)


# ---------------------------------------------------------------------------
# Happy path: adb root exit 0, shell id returns uid=0 immediately.
# ---------------------------------------------------------------------------

def test_ensure_adb_root_usb_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _install_fake(monkeypatch, {
        "root":  [_FakeCompleted(0, "restarting adbd as root\n", "")],
        "shell": [_FakeCompleted(0, "uid=0(root) gid=0(root)\n", "")],
    }, calls)

    warnings: list[str] = []
    _adb._ensure_adb_root("emulator-5554", [], warnings, "required")

    assert warnings == []
    # No adb connect for USB serial.
    assert all(c[0] != "connect" for c in calls)


# ---------------------------------------------------------------------------
# TCP/IP recovery: adb root exits 1 (connection dropped), reconnect,
# second shell id returns uid=0.
# ---------------------------------------------------------------------------

def test_ensure_adb_root_tcp_recovers_after_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    _install_fake(monkeypatch, {
        "root":    [_FakeCompleted(1, "", "error: closed\n")],
        "connect": [_FakeCompleted(0, "connected to 10.56.19.39:5555\n", ""),
                    _FakeCompleted(0, "already connected\n", "")],
        # First id attempt fails (daemon still restarting), second succeeds.
        "shell":   [_FakeCompleted(1, "", "error: device offline\n"),
                    _FakeCompleted(0, "uid=0(root)\n", "")],
        "wait-for-device": [_FakeCompleted(0), _FakeCompleted(0)],
    }, calls)

    warnings: list[str] = []
    _adb._ensure_adb_root("10.56.19.39:5555", [], warnings, "required")

    assert warnings == []
    assert any(c[0] == "connect" for c in calls), "TCP serial must trigger adb connect"


# ---------------------------------------------------------------------------
# Production-locked build: adb root prints the well-known message, shell
# id returns a non-root uid. Required → raise; preferred → warn.
# ---------------------------------------------------------------------------

def test_ensure_adb_root_production_locked_required_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    _install_fake(monkeypatch, {
        "root":  [_FakeCompleted(0,
                  "adbd cannot run as root in production builds\n", "")],
        "shell": [_FakeCompleted(0, "uid=2000(shell) gid=2000(shell)\n", "")],
    }, calls)

    with pytest.raises(RuntimeError, match="production-locked"):
        _adb._ensure_adb_root("emulator-5554", [], [], "required")


def test_ensure_adb_root_production_locked_preferred_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    _install_fake(monkeypatch, {
        "root":  [_FakeCompleted(0,
                  "adbd cannot run as root in production builds\n", "")],
        "shell": [_FakeCompleted(0, "uid=2000(shell)\n", "")],
    }, calls)

    warnings: list[str] = []
    _adb._ensure_adb_root("emulator-5554", [], warnings, "preferred")

    assert len(warnings) == 1
    assert "production-locked" in warnings[0]


# ---------------------------------------------------------------------------
# Skipped policy short-circuits before any adb invocation.
# ---------------------------------------------------------------------------

def test_ensure_adb_root_skipped_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _install_fake(monkeypatch, {}, calls)

    _adb._ensure_adb_root("10.56.19.39:5555", [], [], "skipped")

    assert calls == []
