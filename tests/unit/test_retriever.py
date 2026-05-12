"""Unit tests for the retriever (prior-knowledge context block builder)."""

from __future__ import annotations

from agent.knowledge.retriever import get_context
from agent.knowledge.store import KnowledgeStore


class _FakeLLM:
    def __init__(self, vec: list[float] | None) -> None:
        self.vec = vec

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if self.vec is None:
            return None
        return [list(self.vec) for _ in texts]


def test_get_context_empty_when_store_is_none() -> None:
    assert get_context(store=None, serial="S1", goal="x", llm=None) == ""


def test_get_context_first_session_message() -> None:
    """Unseen device → block announces "FIRST session"."""
    s = KnowledgeStore(":memory:")
    ctx = get_context(store=s, serial="UNKNOWN", goal="map device", llm=None)
    assert "FIRST session" in ctx
    s.close()


def test_get_context_includes_device_identity() -> None:
    s = KnowledgeStore(":memory:")
    s.upsert_device(serial="S1", manufacturer="BMW", model="IDC23",
                    android_version="13", sdk_int=33,
                    build_fingerprint="bmw/idc23/x:13/y/z:user/release-keys")
    ctx = get_context(store=s, serial="S1", goal="audio", llm=None)
    assert "BMW" in ctx and "IDC23" in ctx
    assert "Android 13" in ctx
    s.close()


def test_get_context_lists_recent_facts_grouped() -> None:
    s = KnowledgeStore(":memory:")
    s.upsert_device(serial="S1")
    s.insert_fact(serial="S1", session_id="sess1", category="audio",
                  key="dsp", value="Harman",
                  recorded_utc="2026-02-01T00:00:00Z", text_repr="t")
    s.insert_fact(serial="S1", session_id="sess1", category="hardware",
                  key="soc", value="SA8155",
                  recorded_utc="2026-02-01T00:00:00Z", text_repr="t")
    s.commit()
    ctx = get_context(store=s, serial="S1", goal="x", llm=None)
    assert "**audio**" in ctx
    assert "**hardware**" in ctx
    assert "Harman"  in ctx
    assert "SA8155"  in ctx
    s.close()


def test_get_context_semantic_section_skipped_when_llm_returns_none() -> None:
    """If embeddings are unavailable, the semantic section is omitted."""
    s = KnowledgeStore(":memory:")
    s.upsert_device(serial="S1")
    s.insert_fact(serial="S1", session_id="sess1", category="audio",
                  key="dsp", value="x", recorded_utc="t",
                  text_repr="dsp text", embedding=[1.0, 0.0])
    s.commit()
    ctx = get_context(store=s, serial="S1", goal="audio",
                      llm=_FakeLLM(vec=None))
    assert "Most relevant prior findings" not in ctx
    s.close()


def test_get_context_includes_semantic_hits() -> None:
    s = KnowledgeStore(":memory:")
    s.upsert_device(serial="S1")
    s.insert_finding(serial="S1", session_id="sess1", chunk=0,
                     text_repr="audio DSP is Harman",
                     embedding=[1.0, 0.0, 0.0])
    s.insert_finding(serial="S1", session_id="sess1", chunk=1,
                     text_repr="something unrelated",
                     embedding=[0.0, 1.0, 0.0])
    s.commit()
    ctx = get_context(store=s, serial="S1", goal="audio",
                      llm=_FakeLLM(vec=[1.0, 0.0, 0.0]), top_k=2)
    assert "Most relevant prior findings" in ctx
    assert "audio DSP is Harman" in ctx
    s.close()
