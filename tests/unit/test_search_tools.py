"""Unit tests for the abstract-query tool family (find_*, grep_*, search_facts).

We monkey-patch ``tools._shell`` so no real ADB invocation occurs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent import tools
from agent.tools import AgentSession


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session(tmp_path: Path) -> AgentSession:
    sess = AgentSession(serial="emulator-5554", session_dir=tmp_path)
    sess.ensure_dirs()
    return sess


def _patch_shell(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, tuple[int, str, str]],
) -> list[str]:
    """Stub ``_shell`` with a substring→(code, stdout, stderr) map."""
    calls: list[str] = []

    def _fake(_session: AgentSession, cmd: str, *, timeout: float = 60.0):
        calls.append(cmd)
        for key, response in responses.items():
            if cmd.startswith(key):
                return response
        return (0, "", "")

    monkeypatch.setattr(tools, "_shell", _fake)
    return calls


# ---------------------------------------------------------------------------
# _grep_text
# ---------------------------------------------------------------------------

def test_grep_text_returns_line_no_and_context() -> None:
    text = "alpha\nbeta\nfoo bar\ngamma\ndelta\nfoo baz\nepsilon"
    result = tools._grep_text(text, r"foo", context=1, max_matches=10)
    assert result["total_matches"] == 2
    assert [m["line_no"] for m in result["matches"]] == [3, 6]
    assert result["matches"][0]["context"] == ["beta", "foo bar", "gamma"]


def test_grep_text_invalid_regex_returns_error() -> None:
    result = tools._grep_text("anything", "(unclosed", context=0)
    assert "error" in result


def test_grep_text_case_insensitive_by_default() -> None:
    result = tools._grep_text("HELLO\nworld", "hello", context=0)
    assert result["total_matches"] == 1


def test_grep_text_caps_at_max_matches() -> None:
    text = "\n".join(["hit"] * 100)
    result = tools._grep_text(text, "hit", context=0, max_matches=5)
    assert result["total_matches"] == 100
    assert result["returned"] == 5


# ---------------------------------------------------------------------------
# find_property
# ---------------------------------------------------------------------------

def test_find_property_bootstraps_and_matches(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "[ro.product.model]: [IDC23]\n[ro.audio.silent]: [0]\n[ro.build.type]: [user]\n"
    _patch_shell(monkeypatch, {"getprop": (0, raw, "")})
    result = tools.tool_find_property(session, pattern="audio")
    assert result["total_matches"] == 1
    assert result["matches"][0]["key"] == "ro.audio.silent"


def test_find_property_value_pattern_filters(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = "[ro.a]: [foo]\n[ro.b]: [bar]\n[ro.c]: [foobar]\n"
    _patch_shell(monkeypatch, {"getprop": (0, raw, "")})
    result = tools.tool_find_property(session, pattern="ro\\.", value_pattern="^bar$")
    keys = {m["key"] for m in result["matches"]}
    assert keys == {"ro.b"}


def test_find_property_invalid_regex(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, {"getprop": (0, "[a]: [b]\n", "")})
    result = tools.tool_find_property(session, pattern="(broken")
    assert "error" in result


# ---------------------------------------------------------------------------
# find_package
# ---------------------------------------------------------------------------

def test_find_package_bootstraps_and_filters_by_pattern(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pm_out = (
        "package:/system/app/A.apk=com.example.alpha\n"
        "package:/data/app/B.apk=com.bmw.idc\n"
        "package:/system/app/C.apk=com.android.systemui\n"
    )
    _patch_shell(monkeypatch, {"pm list": (0, pm_out, "")})
    result = tools.tool_find_package(session, pattern="bmw|idc")
    pkgs = [m["key"] for m in result["matches"]]
    assert "com.bmw.idc" in pkgs
    assert "com.example.alpha" not in pkgs


# ---------------------------------------------------------------------------
# find_service
# ---------------------------------------------------------------------------

def test_find_service_bootstraps(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc_out = (
        "Found 3 services:\n"
        "0\taudio: [android.media.IAudioService]\n"
        "1\tcar_audio: [android.car.media.ICarAudio]\n"
        "2\tactivity: [android.app.IActivityManager]\n"
    )
    _patch_shell(monkeypatch, {"service list": (0, svc_out, "")})
    result = tools.tool_find_service(session, pattern="audio")
    keys = {m["key"] for m in result["matches"]}
    assert keys == {"audio", "car_audio"}


# ---------------------------------------------------------------------------
# find_setting
# ---------------------------------------------------------------------------

def test_find_setting_searches_all_namespaces(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_shell(monkeypatch, {
        "settings list system": (0, "screen_brightness=128\nfont_scale=1.0\n", ""),
        "settings list secure": (0, "location_mode=3\n", ""),
        "settings list global": (0, "wifi_on=1\nbluetooth_on=0\n", ""),
    })
    result = tools.tool_find_setting(session, pattern="brightness|wifi")
    ns = {m["namespace"] for m in result["matches"]}
    assert ns == {"system", "global"}


def test_find_setting_rejects_unknown_namespace(session: AgentSession) -> None:
    result = tools.tool_find_setting(session, pattern=".", namespaces=["bogus"])
    assert "error" in result


# ---------------------------------------------------------------------------
# grep_dumpsys
# ---------------------------------------------------------------------------

def test_grep_dumpsys_fetches_then_caches(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_dump = (
        "AudioFlinger dump:\n"
        "  HAL version: 7.1\n"
        "  Streams:\n"
        "    Stream 0: PCM 48000Hz\n"
    )
    calls = _patch_shell(monkeypatch, {"dumpsys audio": (0, audio_dump, "")})
    r1 = tools.tool_grep_dumpsys(session, section="audio", pattern="HAL")
    assert r1["total_matches"] == 1
    assert r1["matches"][0]["line"].strip() == "HAL version: 7.1"
    # Second call must NOT re-shell — cache hit.
    r2 = tools.tool_grep_dumpsys(session, section="audio", pattern="Stream")
    assert r2["total_matches"] == 2
    assert len([c for c in calls if c.startswith("dumpsys audio")]) == 1


def test_grep_dumpsys_rejects_unknown_section(session: AgentSession) -> None:
    result = tools.tool_grep_dumpsys(session, section="not_a_section", pattern=".")
    assert "error" in result


# ---------------------------------------------------------------------------
# grep_logcat
# ---------------------------------------------------------------------------

def test_grep_logcat_runs_with_since_and_filters(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = (
        "01-01 00:00:00.000  100  100 I tag: info hello\n"
        "01-01 00:00:01.000  100  100 E AndroidRuntime: FATAL EXCEPTION\n"
        "01-01 00:00:02.000  100  100 W tag: warning here\n"
    )
    calls = _patch_shell(monkeypatch, {"logcat -d -t 5m": (0, log, "")})
    result = tools.tool_grep_logcat(session, pattern="FATAL", since="5m")
    assert result["total_matches"] == 1
    assert any(c.startswith("logcat -d -t 5m") for c in calls)


def test_grep_logcat_rejects_bad_since(session: AgentSession) -> None:
    result = tools.tool_grep_logcat(session, pattern=".", since="; rm -rf /")
    assert "error" in result


# ---------------------------------------------------------------------------
# grep_file
# ---------------------------------------------------------------------------

def test_grep_file_returns_matches_with_context(
    session: AgentSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = "androidboot.slot=a\nandroidboot.verifiedbootstate=green\nfoo=bar\n"
    _patch_shell(monkeypatch, {"cat ": (0, content, "")})
    result = tools.tool_grep_file(session, path="/proc/cmdline",
                                  pattern="slot|verified", context=0)
    assert result["total_matches"] == 2
    assert result["path"] == "/proc/cmdline"


def test_grep_file_rejects_bad_path(session: AgentSession) -> None:
    result = tools.tool_grep_file(session, path="../etc/passwd", pattern=".")
    assert "error" in result


# ---------------------------------------------------------------------------
# search_facts
# ---------------------------------------------------------------------------

def test_search_facts_matches_across_fields(session: AgentSession) -> None:
    tools.tool_note(session, category="audio", key="hal_version", value="7.1")
    tools.tool_note(session, category="hardware", key="soc", value="SA8155")
    result = tools.tool_search_facts(session, pattern="hal")
    assert result["total_matches"] == 1
    assert result["matches"][0]["category"] == "audio"


# ---------------------------------------------------------------------------
# Schema ↔ registry contract
# ---------------------------------------------------------------------------

def test_new_tools_present_in_schemas_and_registry() -> None:
    from agent.schemas import SCHEMAS_BY_NAME
    new = {
        "find_property", "find_package", "find_service", "find_setting",
        "grep_dumpsys", "grep_logcat", "grep_file", "search_facts",
    }
    assert new.issubset(set(SCHEMAS_BY_NAME))
    assert new.issubset(set(tools.TOOL_REGISTRY))
