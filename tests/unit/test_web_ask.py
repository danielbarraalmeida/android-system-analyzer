"""Unit tests for ``scripts/agent/web/ask.py``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agent.knowledge import KnowledgeStore
from agent.web.ask   import answer_question


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------

@dataclass
class _FakeMessage:
    content: str = "stub answer"


class FakeLLM:
    """Scripted embed + chat for deterministic tests."""

    def __init__(
        self,
        *,
        embeddings: list[list[float]] | None,
        answer: str = "the device runs Android 13",
    ) -> None:
        self._embeddings = embeddings
        self._answer     = answer
        self.chat_calls: list[list[dict[str, Any]]] = []

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return self._embeddings

    def chat(self, *, messages: list[dict[str, Any]], tools: list[Any]) -> Any:
        self.chat_calls.append(list(messages))
        return _FakeMessage(content=self._answer)

    def ping(self) -> tuple[bool, str]:
        return True, "ok"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_store(store: KnowledgeStore, serial: str) -> None:
    store.upsert_device(serial=serial)
    store.insert_fact(
        serial=serial, session_id="s1",
        category="system", key="android_version", value="13",
        recorded_utc="2026-05-12T10:00:00+00:00",
        text_repr="android_version=13",
        embedding=[1.0, 0.0, 0.0],
    )
    store.insert_fact(
        serial=serial, session_id="s1",
        category="package", key="com.example", value="installed",
        recorded_utc="2026-05-12T10:01:00+00:00",
        text_repr="package com.example installed",
        embedding=[0.0, 1.0, 0.0],
    )
    store.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_question_returns_placeholder() -> None:
    store = KnowledgeStore(":memory:")
    llm   = FakeLLM(embeddings=[[1.0, 0.0, 0.0]])
    result = answer_question(
        store=store, llm=llm,
        serial="abc", question="   ",
    )
    assert result.question == ""
    assert "empty question" in result.answer
    assert result.citations == []
    assert llm.chat_calls == []


def test_embed_failure_short_circuits() -> None:
    store = KnowledgeStore(":memory:")
    llm   = FakeLLM(embeddings=None)
    result = answer_question(
        store=store, llm=llm,
        serial="abc", question="anything",
    )
    assert "Embeddings unavailable" in result.answer
    assert "embed_failed" in result.warnings
    assert llm.chat_calls == []


def test_no_facts_recorded_yet() -> None:
    store = KnowledgeStore(":memory:")
    store.upsert_device(serial="abc")
    store.commit()
    llm = FakeLLM(embeddings=[[1.0, 0.0, 0.0]])
    result = answer_question(
        store=store, llm=llm,
        serial="abc", question="what version?",
    )
    assert "No facts recorded yet" in result.answer
    assert result.citations == []
    assert "no_citations" in result.warnings


def test_top_k_returns_grounded_answer() -> None:
    store = KnowledgeStore(":memory:")
    _seed_store(store, "abc")
    # Query vector aligned with the android_version fact.
    llm = FakeLLM(embeddings=[[1.0, 0.0, 0.0]], answer="Android 13 per [1].")

    result = answer_question(
        store=store, llm=llm,
        serial="abc", question="what android version?",
        top_k=2,
    )

    assert result.answer == "Android 13 per [1]."
    assert len(result.citations) == 2
    # First citation should be the android_version fact (cosine = 1.0).
    assert result.citations[0].category == "system"
    assert result.citations[0].key      == "android_version"
    assert result.citations[0].score    == pytest.approx(1.0)
    # And the grounded prompt should embed the citation text.
    user_msg = llm.chat_calls[0][-1]["content"]
    assert "android_version: 13" in user_msg
    assert "serial: `abc`" in user_msg.lower()
