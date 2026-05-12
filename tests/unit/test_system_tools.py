"""Unit tests for the system-inspection tool layer.

These tests exercise the parsing, allowlisting, and session-buffer logic
without touching ADB. We monkey-patch ``agent.tools._shell`` (and ``_run``
where needed) to return canned outputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent import tools
from agent.tools import (
    AgentSession, _parse_getprop, _parse_pm_list, _parse_service_list,
    _parse_settings, _shell_command_allowed,
)


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------

def test_parse_getprop_basic() -> None:
    raw = "[ro.product.model]: [IDC23]\n[ro.build.version.sdk]: [33]\n"
    props = _parse_getprop(raw)
    assert props == {"ro.product.model": "IDC23", "ro.build.version.sdk": "33"}


def test_parse_getprop_ignores_garbage_lines() -> None:
    props = _parse_getprop("not a property line\n[a]: [b]\n")
    assert props == {"a": "b"}


def test_parse_pm_list_with_and_without_apk_path() -> None:
    raw = (
        "package:/system/app/A.apk=com.a\n"
        "package:com.b\n"
        "garbage\n"
    )
    rows = _parse_pm_list(raw)
    assert rows == [
        {"package": "com.a", "apk_path": "/system/app/A.apk"},
        {"package": "com.b", "apk_path": None},
    ]


def test_parse_service_list() -> None:
    raw = "Found 2 services:\n  0\taudio: [android.media.IAudioService]\n  1\twifi: []\n"
    rows = _parse_service_list(raw)
    assert rows == [
        {"service": "audio", "interface": "android.media.IAudioService"},
        {"service": "wifi",  "interface": ""},
    ]


def test_parse_settings_keyvalue() -> None:
    raw = "airplane_mode_on=0\nwifi_on=1\nbroken_line\n"
    out = _parse_settings(raw)
    assert out == {"airplane_mode_on": "0", "wifi_on": "1"}


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "getprop",
    "getprop ro.product.model",
    "pm list packages -3 -f",
    "dumpsys audio",
    "service list",
    "settings get global airplane_mode_on",
    "settings list system",
    "ps -A",
    "cat /proc/cpuinfo",
    "ls -la /system",
    "cmd activity start-activity foo",
])
def test_allowlist_accepts_known_commands(cmd: str) -> None:
    assert _shell_command_allowed(cmd)


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "echo hi",
    "reboot",
    "input tap 100 200",
    "settings put global x y",
    "; cat /etc/passwd",
])
def test_allowlist_rejects_dangerous_or_unknown(cmd: str) -> None:
    assert not _shell_command_allowed(cmd)


# ---------------------------------------------------------------------------
# Tool functions (with mocked _shell)
# ---------------------------------------------------------------------------

@pytest.fixture()
def session(tmp_path: Path) -> AgentSession:
    return AgentSession(serial="dev", session_dir=tmp_path / "sess")


def _patch_shell(monkeypatch: pytest.MonkeyPatch, code: int, out: str, err: str = "") -> list[str]:
    calls: list[str] = []
    def fake(_sess, cmd, *, timeout=60.0):  # noqa: ANN001
        calls.append(cmd)
        return code, out, err
    monkeypatch.setattr(tools, "_shell", fake)
    return calls


def test_get_device_properties_populates_session(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "[ro.product.manufacturer]: [BMW]\n[ro.product.model]: [IDC23]\n"
    _patch_shell(monkeypatch, 0, raw)
    result = tools.tool_get_device_properties(session)
    assert result["property_count"] == 2
    assert result["headline"]["ro.product.manufacturer"] == "BMW"
    assert session.properties["ro.product.model"] == "IDC23"
    # Raw file persisted under session/raw/
    assert (session.session_dir / result["raw_file"]).exists()


def test_get_device_properties_returns_error_on_nonzero(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, 1, "", "boom")
    result = tools.tool_get_device_properties(session)
    assert "error" in result and "boom" in result["error"]


def test_list_packages_records_buffer(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "package:/x/A.apk=com.a\npackage:/y/B.apk=com.b\n"
    calls = _patch_shell(monkeypatch, 0, raw)
    result = tools.tool_list_packages(session, filter="system")
    assert result["package_count"] == 2
    assert "-s" in calls[0]
    assert session.packages["com.a"]["is_system"] is True


def test_list_packages_rejects_unknown_filter_via_default(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown filter falls back to ``-3`` rather than crashing."""
    calls = _patch_shell(monkeypatch, 0, "")
    tools.tool_list_packages(session, filter="bogus")
    assert "-3" in calls[0]


def test_inspect_package_validates_name(session: AgentSession) -> None:
    result = tools.tool_inspect_package(session, package="not a package!!")
    assert "error" in result


def test_inspect_package_parses_version_and_perms(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dump = (
        "Package [com.a]:\n"
        "  versionName=1.2.3\n"
        "  versionCode=10203\n"
        "  requested permissions:\n"
        "    android.permission.INTERNET\n"
        "    android.permission.CAMERA\n"
        "  install permissions:\n"
        "      abcdef com.a/.MainActivity filter 0x1\n"
    )
    _patch_shell(monkeypatch, 0, dump)
    result = tools.tool_inspect_package(session, package="com.a")
    assert result["version_name"] == "1.2.3"
    assert result["version_code"] == 10203
    assert result["permission_count"] == 2
    assert result["activity_count"] == 1


def test_inspect_package_handles_not_installed(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, 0, "Unable to find package: com.missing\n")
    result = tools.tool_inspect_package(session, package="com.missing")
    assert "not installed" in result["error"]


def test_dumpsys_rejects_unknown_section(session: AgentSession) -> None:
    result = tools.tool_dumpsys(session, section="not_a_section")
    assert "error" in result
    assert "allowed_sections" in result


def test_dumpsys_audio_records_excerpt(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, 0, "audio policy v2\nstream count 5\n")
    result = tools.tool_dumpsys(session, section="audio")
    assert result["section"] == "audio"
    assert len(session.dumpsys_excerpts) == 1
    assert session.dumpsys_excerpts[0]["section"] == "audio"


def test_read_settings_buckets(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, 0, "a=1\nb=2\n")
    result = tools.tool_read_settings(session, namespace="global")
    assert result["key_count"] == 2
    assert session.settings_buckets["global"] == {"a": "1", "b": "2"}


def test_read_settings_rejects_invalid_namespace(session: AgentSession) -> None:
    assert "error" in tools.tool_read_settings(session, namespace="bogus")


def test_run_shell_blocks_non_allowlisted(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, 0, "")  # never called
    result = tools.tool_run_shell(session, command="rm -rf /")
    assert "not on allowlist" in result["error"]


def test_run_shell_permits_when_arbitrary_enabled(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.allow_arbitrary_shell = True
    _patch_shell(monkeypatch, 0, "ok\n")
    result = tools.tool_run_shell(session, command="echo hello world")
    assert result["exit_code"] == 0
    assert "ok" in result["stdout_preview"]


def test_note_rejects_unknown_category(session: AgentSession) -> None:
    result = tools.tool_note(session, category="bogus", key="k", value="v")
    assert "error" in result


def test_note_records_into_session(session: AgentSession) -> None:
    result = tools.tool_note(session, category="automotive",
                             key="platform", value="IDC23")
    assert result["accepted"] is True
    assert session.facts and session.facts[0]["key"] == "platform"


def test_finish_returns_action(session: AgentSession) -> None:
    result = tools.tool_finish(session, summary="long summary text")
    assert result["action"] == "finish"
    assert result["summary_length"] == len("long summary text")


def test_list_dir_rejects_relative_paths(session: AgentSession) -> None:
    assert "error" in tools.tool_list_dir(session, path="../etc/passwd")


def test_read_file_rejects_relative_paths(session: AgentSession) -> None:
    assert "error" in tools.tool_read_file(session, path="passwd")


def test_tool_registry_covers_all_schemas() -> None:
    """Every schema must have a matching implementation."""
    from agent.schemas import SCHEMAS_BY_NAME
    assert set(SCHEMAS_BY_NAME) == set(tools.TOOL_REGISTRY)
