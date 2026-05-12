"""Unit tests for the indexer (AgentSession → KnowledgeStore)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.knowledge.indexer import _chunk_summary, _extract_identity, index_session
from agent.knowledge.store import KnowledgeStore
from agent.tools import AgentSession


class _FakeLLM:
    """Deterministic embed() that returns a unit vector encoding the text length."""

    def __init__(self, return_none: bool = False) -> None:
        self.return_none = return_none
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        self.calls.append(list(texts))
        if self.return_none:
            return None
        # Map each text to a stable 3-vec so cosine ordering is predictable.
        return [[float(len(t) % 7), 1.0, 0.5] for t in texts]


def _make_session(tmp_path: Path) -> AgentSession:
    sess = AgentSession(serial="10.0.0.1:5555", session_dir=tmp_path / "s1")
    sess.ensure_dirs()
    sess.properties = {
        "ro.product.manufacturer": "BMW",
        "ro.product.model":        "IDC23",
        "ro.build.version.release": "13",
        "ro.build.version.sdk":    "33",
        "ro.build.fingerprint":    "bmw/idc23/test:13/abc/123:user/release-keys",
    }
    sess.packages = {
        "com.bmw.navi": {
            "apk_path": "/system_ext/priv-app/Navi.apk",
            "is_system": True,
            "version_name": "2.4.1",
            "version_code": 2401,
            "permissions": ["android.permission.INTERNET"],
            "activity_count": 5,
        },
    }
    sess.services = {"car_audio": "android.car.IAudio"}
    sess.settings_buckets = {"global": {"airplane_mode_on": "0"}}
    sess.dumpsys_excerpts = [{
        "section": "audio", "raw_file": "raw/dumpsys__audio.txt",
        "captured_utc": "2026-01-01T00:00:00+00:00",
        "first_lines": "audio policy line 1\nline 2",
    }]
    sess.facts = [{
        "category": "automotive", "key": "platform", "value": "IDC23",
        "recorded_utc": "2026-01-01T00:00:00+00:00",
    }]
    sess.screen_snapshots = [{
        "signature": "abc123", "package": "com.bmw.launcher",
        "activity": ".MainActivity", "screenshot": "screenshots/home.png",
        "captured_utc": "2026-01-01T00:00:01+00:00",
    }]
    return sess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_extract_identity_parses_sdk_int() -> None:
    ident = _extract_identity({
        "ro.product.manufacturer": "X", "ro.build.version.sdk": "33",
    })
    assert ident["manufacturer"] == "X"
    assert ident["sdk_int"]      == 33


def test_extract_identity_handles_missing_sdk() -> None:
    ident = _extract_identity({"ro.product.manufacturer": "X"})
    assert ident["sdk_int"] is None


def test_chunk_summary_splits_long_text() -> None:
    text = "\n\n".join(["paragraph " + str(i) * 80 for i in range(5)])
    chunks = _chunk_summary(text, max_chars=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 250 for c in chunks)


def test_chunk_summary_empty() -> None:
    assert _chunk_summary("") == []


# ---------------------------------------------------------------------------
# index_session end-to-end
# ---------------------------------------------------------------------------

def test_index_session_writes_all_tables(tmp_path: Path) -> None:
    store = KnowledgeStore(":memory:")
    sess  = _make_session(tmp_path)
    llm   = _FakeLLM()

    counts = index_session(
        store=store, session=sess, session_id="sess_001",
        summary="# Test\n\nFirst paragraph.\n\nSecond paragraph.", llm=llm,
    )

    assert counts == {
        "properties": 5, "packages": 1, "services": 1, "settings": 1,
        "dumpsys_excerpts": 1, "facts": 1, "findings": 1, "screens": 1,
    }
    assert store.count_table("device")            == 1
    assert store.count_table("properties")        == 5
    assert store.count_table("packages")          == 1
    assert store.count_table("services")          == 1
    assert store.count_table("settings")          == 1
    assert store.count_table("dumpsys_excerpts")  == 1
    assert store.count_table("facts")             == 1
    assert store.count_table("findings")          == 1
    assert store.count_table("screen_snapshots")  == 1

    dev = store.get_device(sess.serial)
    assert dev["manufacturer"] == "BMW"
    assert dev["sdk_int"]      == 33
    store.close()


def test_index_session_tolerates_null_embeddings(tmp_path: Path) -> None:
    """If the embedding endpoint is down, rows are stored with embedding=NULL."""
    store = KnowledgeStore(":memory:")
    sess  = _make_session(tmp_path)
    llm   = _FakeLLM(return_none=True)

    index_session(store=store, session=sess, session_id="sess_001",
                  summary="A summary.", llm=llm)

    row = store.conn.execute(
        "SELECT embedding FROM facts LIMIT 1"
    ).fetchone()
    assert row["embedding"] is None
    store.close()


def test_index_session_without_llm_writes_nulls(tmp_path: Path) -> None:
    store = KnowledgeStore(":memory:")
    sess  = _make_session(tmp_path)

    counts = index_session(store=store, session=sess,
                           session_id="sess_001", summary="x", llm=None)
    assert counts["packages"] == 1
    row = store.conn.execute(
        "SELECT embedding FROM packages LIMIT 1"
    ).fetchone()
    assert row["embedding"] is None
    store.close()
