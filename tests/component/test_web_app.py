"""Component tests for the FastAPI web app."""

from __future__ import annotations

import time
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from agent.knowledge import KnowledgeStore
from agent.web       import create_app


# ---------------------------------------------------------------------------
# Fake LLM (mirrors the one in test_web_ask.py — kept inline so each test
# module is self-contained)
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self) -> None:
        self.embeddings = [[1.0, 0.0, 0.0]]
        self.answer     = "fake answer"

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        return self.embeddings * len(texts)

    def chat(self, *, messages: Any, tools: Any) -> Any:
        return _Msg(self.answer)

    def ping(self) -> tuple[bool, str]:
        return True, "ok"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path):
    """A KnowledgeStore prepopulated with one device + one fact."""
    db = tmp_path / "kb.db"
    store = KnowledgeStore(db)
    store.upsert_device(
        serial="abc:5555",
        manufacturer="Acme", model="Devboard",
        android_version="13", sdk_int=33,
    )
    store.insert_fact(
        serial="abc:5555", session_id="seed",
        category="system", key="hostname", value="acme-dev",
        recorded_utc="2026-05-12T10:00:00+00:00",
        text_repr="hostname=acme-dev",
        embedding=[1.0, 0.0, 0.0],
    )
    store.commit()
    store.close()
    return db


@pytest.fixture()
def fake_runner_calls():
    return []


@pytest.fixture()
def client(tmp_path, seeded_db, fake_runner_calls):
    output_root = tmp_path / "sessions"
    output_root.mkdir()

    def _runner(**kwargs: Any) -> dict[str, Any]:
        fake_runner_calls.append(kwargs)
        log_fn = kwargs["log_fn"]
        log_fn("starting fake run")
        log_fn("fake run done")
        return {
            "session_dir":     str(output_root / "fake"),
            "serial":          kwargs["serial"],
            "stop_reason":     "finish",
            "turns":           1,
            "elapsed_seconds": 0.01,
            "warnings":        [],
        }

    app = create_app(
        db_path=seeded_db,
        output_root=output_root,
        llm_factory=lambda: FakeLLM(),
        runner=_runner,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["llm_ok"] is True


def test_devices_lists_seeded_device(client: TestClient) -> None:
    r = client.get("/api/devices")
    assert r.status_code == 200
    devices = r.json()
    assert len(devices) == 1
    assert devices[0]["serial"]     == "abc:5555"
    assert devices[0]["fact_count"] == 1


def test_device_facts(client: TestClient) -> None:
    r = client.get("/api/devices/abc:5555/facts")
    assert r.status_code == 200
    facts = r.json()
    assert len(facts) == 1
    assert facts[0]["category"] == "system"


def test_device_404(client: TestClient) -> None:
    r = client.get("/api/devices/unknown")
    assert r.status_code == 404


def test_ask_returns_answer_with_citation(client: TestClient) -> None:
    r = client.post("/api/ask", json={
        "serial":   "abc:5555",
        "question": "what is the hostname?",
        "top_k":    3,
    })
    assert r.status_code == 200
    j = r.json()
    assert j["answer"] == "fake answer"
    assert len(j["citations"]) == 1
    assert j["citations"][0]["key"] == "hostname"


def test_inspect_launches_background_session(
    client: TestClient,
    fake_runner_calls: list[Any],
) -> None:
    r = client.post("/api/inspect", json={
        "serial": "abc:5555",
        "goal":   "fake goal",
    })
    assert r.status_code == 200
    sid = r.json()["session_id"]

    # Wait for the background thread to finish.
    for _ in range(50):
        s = client.get(f"/api/sessions/{sid}").json()
        if s["status"] in ("finished", "error"):
            break
        time.sleep(0.05)
    else:
        pytest.fail("session never finished")

    assert s["status"] == "finished"
    assert s["manifest"]["serial"] == "abc:5555"
    # The fake runner saw the right keyword args.
    assert fake_runner_calls
    assert fake_runner_calls[0]["serial"] == "abc:5555"
    assert fake_runner_calls[0]["goal"]   == "fake goal"


def test_unknown_session_404(client: TestClient) -> None:
    r = client.get("/api/sessions/does-not-exist")
    assert r.status_code == 404
