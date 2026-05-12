"""Unit tests for the KnowledgeStore SQLite layer."""

from __future__ import annotations

import math

import pytest

from agent.knowledge.store import KnowledgeStore, _cosine


@pytest.fixture()
def store() -> KnowledgeStore:
    s = KnowledgeStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

def test_all_tables_created(store: KnowledgeStore) -> None:
    """All nine required tables must exist after construction."""
    for table in ("device", "properties", "packages", "services", "settings",
                  "dumpsys_excerpts", "facts", "findings", "screen_snapshots"):
        assert store.count_table(table) == 0


# ---------------------------------------------------------------------------
# Device + properties
# ---------------------------------------------------------------------------

def test_device_upsert_then_enrich(store: KnowledgeStore) -> None:
    """A second upsert must enrich missing fields without nulling existing ones."""
    store.upsert_device(serial="S1", manufacturer="BMW", model="IDC23")
    store.upsert_device(serial="S1", android_version="13", sdk_int=33)
    dev = store.get_device("S1")
    assert dev is not None
    assert dev["manufacturer"]    == "BMW"
    assert dev["model"]           == "IDC23"
    assert dev["android_version"] == "13"
    assert dev["sdk_int"]         == 33


def test_properties_bulk_upsert(store: KnowledgeStore) -> None:
    store.upsert_properties("S1", {"ro.product.model": "X", "ro.build.fingerprint": "fp"})
    assert store.count_table("properties") == 2
    # Re-upsert with a changed value must update, not duplicate.
    store.upsert_properties("S1", {"ro.product.model": "Y"})
    assert store.count_table("properties") == 2


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------

def test_package_upsert_with_embedding(store: KnowledgeStore) -> None:
    """Embedding survives a JSON round-trip via _dump_vec/_load_vec."""
    store.upsert_package(
        serial="S1", package="com.bmw.navi",
        apk_path="/system_ext/priv-app/Navi.apk",
        is_system=True, version_name="2.4.1", version_code=2401,
        permissions=["android.permission.ACCESS_FINE_LOCATION"],
        activity_count=12,
        text_repr="Navigation app",
        embedding=[0.1, 0.2, 0.3],
    )
    store.commit()
    row = store.conn.execute(
        "SELECT * FROM packages WHERE package = ?", ("com.bmw.navi",),
    ).fetchone()
    assert row is not None
    assert row["is_system"]    == 1
    assert row["version_code"] == 2401
    assert '"android.permission.ACCESS_FINE_LOCATION"' in row["permissions"]
    assert row["embedding"]    == "[0.1, 0.2, 0.3]"


def test_package_is_system_sticky(store: KnowledgeStore) -> None:
    """If a later upsert says is_system=False, the existing True is preserved."""
    store.upsert_package(serial="S1", package="x", is_system=True)
    store.upsert_package(serial="S1", package="x", is_system=False)
    store.commit()
    row = store.conn.execute(
        "SELECT is_system FROM packages WHERE package = 'x'"
    ).fetchone()
    assert row["is_system"] == 1


# ---------------------------------------------------------------------------
# Cosine search
# ---------------------------------------------------------------------------

def test_cosine_helper_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_helper_parallel() -> None:
    assert math.isclose(_cosine([1.0, 1.0], [2.0, 2.0]), 1.0, rel_tol=1e-9)


def test_cosine_search_ranks_by_similarity(store: KnowledgeStore) -> None:
    """Insert three facts; query should rank the most-similar one first."""
    now = "2026-01-01T00:00:00+00:00"
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="dsp", value="Harman", recorded_utc=now,
                      text_repr="audio DSP is Harman",
                      embedding=[1.0, 0.0, 0.0])
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="other", value="x", recorded_utc=now,
                      text_repr="unrelated", embedding=[0.0, 1.0, 0.0])
    store.insert_fact(serial="S2", session_id="sess1", category="audio",
                      key="dsp", value="Harman", recorded_utc=now,
                      text_repr="other device", embedding=[1.0, 0.0, 0.0])
    store.commit()
    hits = store.cosine_search(
        table="facts", query_embedding=[1.0, 0.0, 0.0],
        serial="S1", top_k=2,
    )
    assert len(hits) == 2
    assert hits[0].payload["key"] == "dsp"
    assert hits[0].score > hits[1].score


def test_cosine_search_skips_null_embeddings(store: KnowledgeStore) -> None:
    """Rows without an embedding must not appear in cosine_search output."""
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="no_emb", value="x", recorded_utc="t",
                      text_repr="t", embedding=None)
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="with_emb", value="x", recorded_utc="t",
                      text_repr="t", embedding=[0.5, 0.5])
    store.commit()
    hits = store.cosine_search(table="facts",
                               query_embedding=[1.0, 0.0],
                               serial="S1", top_k=10)
    assert len(hits) == 1
    assert hits[0].payload["key"] == "with_emb"


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def test_get_known_facts_returns_recent_first(store: KnowledgeStore) -> None:
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="a", value="1", recorded_utc="2026-01-01T00:00:00Z",
                      text_repr="a")
    store.insert_fact(serial="S1", session_id="sess1", category="audio",
                      key="b", value="2", recorded_utc="2026-02-01T00:00:00Z",
                      text_repr="b")
    store.commit()
    facts = store.get_known_facts("S1")
    assert [f["key"] for f in facts] == ["b", "a"]
