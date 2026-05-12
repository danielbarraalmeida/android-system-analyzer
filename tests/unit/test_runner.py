"""Unit tests for the agent runner loop.

We don't hit a real LLM or device. We fake both:
- ``_FakeLLM`` returns a scripted sequence of messages.
- ``agent.tools._shell`` is monkey-patched per test so bootstrap and
  tool calls return deterministic output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent import runner, tools
from agent.knowledge.store import KnowledgeStore
from agent.runner import AgentResult, Budget, run_agent
from agent.tools import AgentSession


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeToolCall:
    id: str
    function: Any


@dataclass
class _FakeFn:
    name: str
    arguments: str


@dataclass
class _FakeMsg:
    content: str | None = None
    tool_calls: list[_FakeToolCall] | None = None


class _FakeLLM:
    """LLM that replays a queue of pre-baked messages."""

    def __init__(self, scripted: list[_FakeMsg]) -> None:
        self.scripted = list(scripted)
        self.embed_calls = 0

    def ping(self) -> tuple[bool, str]:
        return True, "ok"

    def chat(self, messages, tools):  # noqa: ANN001
        if not self.scripted:
            return _FakeMsg(content="(no script left)")
        return self.scripted.pop(0)

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.embed_calls += 1
        return [[1.0, 0.0, 0.0] for _ in texts]


def _tc(idx: int, name: str, args: dict[str, Any]) -> _FakeToolCall:
    return _FakeToolCall(id=f"call_{idx}",
                         function=_FakeFn(name=name, arguments=json.dumps(args)))


def _stub_shell(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, tuple[int, str]]) -> None:
    """Map a command substring → (exit_code, stdout). Default = (0, '')."""
    def fake(_sess, cmd, *, timeout=60.0):  # noqa: ANN001
        for key, (code, out) in outputs.items():
            if key in cmd:
                return code, out, ""
        return 0, "", ""
    monkeypatch.setattr(tools, "_shell", fake)


def _make_session(tmp_path: Path) -> AgentSession:
    return AgentSession(serial="dev", session_dir=tmp_path / "sess")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_agent_completes_on_finish_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_shell(monkeypatch, {
        "getprop": (0, "[ro.product.model]: [IDC23]\n"),
        "pm list packages": (0, "package:/x/A.apk=com.a\n"),
    })
    sess = _make_session(tmp_path)
    llm = _FakeLLM([
        _FakeMsg(tool_calls=[_tc(1, "list_packages", {"filter": "third_party"})]),
        _FakeMsg(tool_calls=[_tc(2, "finish", {"summary": "# Done\n\nA summary."})]),
    ])
    result = run_agent(session=sess, goal="map the device", llm=llm,
                      budget=Budget(max_turns=5), log=lambda _m: None)
    assert isinstance(result, AgentResult)
    assert result.stop.reason == "finish"
    assert result.final_summary and result.final_summary.startswith("# Done")
    # Artifacts written
    assert (sess.session_dir / "summary.md").exists()
    assert (sess.session_dir / "transcript.json").exists()
    assert (sess.session_dir / "command_log.json").exists()


def test_run_agent_handles_invalid_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown tool name must produce a tool-message error and not crash."""
    _stub_shell(monkeypatch, {"getprop": (0, "")})
    sess = _make_session(tmp_path)
    llm = _FakeLLM([
        _FakeMsg(tool_calls=[_tc(1, "nonexistent_tool", {})]),
        _FakeMsg(tool_calls=[_tc(2, "finish", {"summary": "x" * 100})]),
    ])
    result = run_agent(session=sess, goal="g", llm=llm,
                      budget=Budget(max_turns=5), log=lambda _m: None)
    assert result.stop.reason == "finish"
    transcript_text = (sess.session_dir / "transcript.json").read_text()
    assert "unknown tool" in transcript_text


def test_run_agent_validates_required_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_shell(monkeypatch, {"getprop": (0, "")})
    sess = _make_session(tmp_path)
    llm = _FakeLLM([
        _FakeMsg(tool_calls=[_tc(1, "dumpsys", {})]),               # missing section
        _FakeMsg(tool_calls=[_tc(2, "finish", {"summary": "x" * 100})]),
    ])
    result = run_agent(session=sess, goal="g", llm=llm,
                      budget=Budget(max_turns=5), log=lambda _m: None)
    assert result.stop.reason == "finish"
    transcript_text = (sess.session_dir / "transcript.json").read_text()
    assert "missing required argument" in transcript_text


def test_run_agent_treats_long_freeform_text_as_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_shell(monkeypatch, {"getprop": (0, "")})
    sess = _make_session(tmp_path)
    long_text = "# Summary\n\n" + ("This is detailed prose. " * 20)
    llm = _FakeLLM([_FakeMsg(content=long_text)])
    result = run_agent(session=sess, goal="g", llm=llm,
                      budget=Budget(max_turns=5), log=lambda _m: None)
    assert result.stop.reason == "model_summary"
    assert result.final_summary == long_text


def test_run_agent_budget_exhaustion_produces_fallback_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_shell(monkeypatch, {"getprop": (0, "[ro.product.model]: [X]\n")})
    sess = _make_session(tmp_path)
    # Model never calls finish; just keeps issuing short non-summary text.
    llm = _FakeLLM([_FakeMsg(content="hmm") for _ in range(10)])
    result = run_agent(session=sess, goal="g", llm=llm,
                      budget=Budget(max_turns=3), log=lambda _m: None)
    assert result.stop.reason == "budget_exhausted"
    # Fallback summary was written even though the LLM never called finish.
    summary = (sess.session_dir / "summary.md").read_text()
    assert "fallback summary" in summary.lower()


def test_run_agent_returns_error_when_llm_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DownLLM(_FakeLLM):
        def ping(self): return False, "boom"
    sess = _make_session(tmp_path)
    result = run_agent(session=sess, goal="g",
                       llm=_DownLLM([]), budget=Budget(),
                       log=lambda _m: None)
    assert result.stop.reason == "llm_unreachable"


def test_run_agent_writes_to_knowledge_store_when_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Indexing must populate the store and counts must be reported."""
    _stub_shell(monkeypatch, {
        "getprop": (0, "[ro.product.manufacturer]: [BMW]\n"
                       "[ro.product.model]: [IDC23]\n"
                       "[ro.build.version.sdk]: [33]\n"),
    })
    sess = _make_session(tmp_path)
    store = KnowledgeStore(":memory:")
    llm = _FakeLLM([
        _FakeMsg(tool_calls=[_tc(1, "note", {
            "category": "automotive", "key": "platform", "value": "IDC23",
        })]),
        _FakeMsg(tool_calls=[_tc(2, "finish", {"summary": "x" * 200})]),
    ])
    result = run_agent(session=sess, goal="map", llm=llm,
                      budget=Budget(max_turns=5),
                      knowledge_store=store, log=lambda _m: None)
    assert result.stop.reason == "finish"
    assert result.indexed_counts.get("properties", 0) >= 3
    assert result.indexed_counts.get("facts") == 1
    # Verify the store actually has the device row + the fact.
    dev = store.get_device("dev")
    assert dev and dev["manufacturer"] == "BMW"
    assert store.count_table("facts") == 1
    store.close()
