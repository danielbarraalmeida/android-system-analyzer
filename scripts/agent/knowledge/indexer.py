"""Persist an AgentSession's findings into the KnowledgeStore.

The indexer reads the session's structured buffers (properties, packages,
services, settings, dumpsys excerpts, free-text facts) plus the LLM's
final summary, builds compact natural-language ``text_repr`` strings,
embeds them in batches, and writes everything to SQLite.

Embeddings are optional — if ``llm.embed`` returns None, rows are stored
without vectors and ``cosine_search`` simply won't match them later.
That keeps the indexer working when the embedding endpoint is offline.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

from .store import KnowledgeStore


# ---------------------------------------------------------------------------
# text_repr builders — short, retrieval-friendly natural-language strings
# ---------------------------------------------------------------------------

def _package_text(serial: str, pkg: str, info: dict[str, Any]) -> str:
    parts = [f"Package {pkg} on {serial}"]
    if info.get("version_name"):
        parts.append(f"version {info['version_name']}")
    if info.get("is_system"):
        parts.append("system app")
    if info.get("activity_count") is not None:
        parts.append(f"{info['activity_count']} activities")
    perms = info.get("permissions") or []
    if perms:
        parts.append("permissions: " + ", ".join(list(perms)[:8]))
    return ". ".join(parts)


def _dumpsys_text(section: str, first_lines: str) -> str:
    head = (first_lines or "").strip().splitlines()[:8]
    return f"dumpsys {section}:\n" + "\n".join(head)


def _fact_text(fact: dict[str, Any]) -> str:
    return f"[{fact['category']}] {fact['key']}: {fact['value']}"


def _chunk_summary(summary: str, max_chars: int = 800) -> list[str]:
    """Split summary into ~800-char chunks at paragraph boundaries."""
    if not summary:
        return []
    paras = [p.strip() for p in summary.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if not buf:
            buf = para
        elif len(buf) + 2 + len(para) <= max_chars:
            buf += "\n\n" + para
        else:
            chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------------------
# Batch-embed helper. Tolerates None / partial returns.
# ---------------------------------------------------------------------------

def _embed_all(llm: Any, texts: list[str]) -> list[list[float] | None]:
    if not texts:
        return []
    if llm is None:
        return [None] * len(texts)
    # Chunk to keep per-call payloads reasonable.
    BATCH = 32
    out: list[list[float] | None] = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i : i + BATCH]
        vecs = llm.embed(chunk)
        if vecs is None or len(vecs) != len(chunk):
            out.extend([None] * len(chunk))
        else:
            out.extend(vecs)
    return out


# ---------------------------------------------------------------------------
# Identity extraction from a property dict
# ---------------------------------------------------------------------------

def _extract_identity(props: dict[str, str]) -> dict[str, Any]:
    sdk_raw = props.get("ro.build.version.sdk")
    try:
        sdk_int = int(sdk_raw) if sdk_raw else None
    except ValueError:
        sdk_int = None
    return {
        "manufacturer":      props.get("ro.product.manufacturer"),
        "model":             props.get("ro.product.model"),
        "android_version":   props.get("ro.build.version.release"),
        "sdk_int":           sdk_int,
        "build_fingerprint": props.get("ro.build.fingerprint"),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def index_session(
    *,
    store: KnowledgeStore,
    session: Any,
    session_id: str,
    summary: str,
    llm: Any = None,
) -> dict[str, int]:
    """Write everything from ``session`` (an AgentSession) into ``store``.

    Returns a dict of counts for the runner to log.
    """
    serial = session.serial

    # ---- device + properties ---------------------------------------------
    identity = _extract_identity(session.properties)
    store.upsert_device(serial=serial, **identity)
    if session.properties:
        store.upsert_properties(serial, session.properties)

    # ---- services / settings ---------------------------------------------
    for svc, iface in session.services.items():
        store.upsert_service(serial, svc, iface)
    for ns, kvs in session.settings_buckets.items():
        for k, v in kvs.items():
            store.upsert_setting(serial, ns, k, v)

    # ---- screens ---------------------------------------------------------
    for snap in session.screen_snapshots:
        store.upsert_screen_snapshot(
            serial=serial,
            signature=snap["signature"],
            session_id=session_id,
            package=snap.get("package"),
            activity=snap.get("activity"),
            screenshot_path=snap.get("screenshot"),
            captured_utc=snap.get("captured_utc") or dt.datetime.now(dt.timezone.utc).isoformat(),
        )

    # ---- packages (with embeddings) --------------------------------------
    pkg_items = list(session.packages.items())
    pkg_texts = [_package_text(serial, p, info) for p, info in pkg_items]
    pkg_vecs  = _embed_all(llm, pkg_texts)
    for (pkg, info), text, vec in zip(pkg_items, pkg_texts, pkg_vecs):
        perms = info.get("permissions")
        store.upsert_package(
            serial=serial, package=pkg,
            apk_path=info.get("apk_path"),
            is_system=bool(info.get("is_system")),
            version_name=info.get("version_name"),
            version_code=info.get("version_code"),
            permissions=perms,
            activity_count=info.get("activity_count"),
            text_repr=text,
            embedding=vec,
        )

    # ---- dumpsys excerpts ------------------------------------------------
    ds_texts = [_dumpsys_text(d["section"], d.get("first_lines", ""))
                for d in session.dumpsys_excerpts]
    ds_vecs  = _embed_all(llm, ds_texts)
    for d, text, vec in zip(session.dumpsys_excerpts, ds_texts, ds_vecs):
        store.insert_dumpsys_excerpt(
            serial=serial, session_id=session_id,
            section=d["section"], raw_file=d.get("raw_file"),
            captured_utc=d.get("captured_utc")
            or dt.datetime.now(dt.timezone.utc).isoformat(),
            text_repr=text, embedding=vec,
        )

    # ---- facts -----------------------------------------------------------
    fact_texts = [_fact_text(f) for f in session.facts]
    fact_vecs  = _embed_all(llm, fact_texts)
    for f, text, vec in zip(session.facts, fact_texts, fact_vecs):
        store.insert_fact(
            serial=serial, session_id=session_id,
            category=f["category"], key=f["key"], value=f["value"],
            recorded_utc=f["recorded_utc"],
            text_repr=text, embedding=vec,
        )

    # ---- findings (chunked summary) --------------------------------------
    chunks = _chunk_summary(summary)
    chunk_vecs = _embed_all(llm, chunks)
    for idx, (chunk, vec) in enumerate(zip(chunks, chunk_vecs)):
        store.insert_finding(
            serial=serial, session_id=session_id,
            chunk=idx, text_repr=chunk, embedding=vec,
        )

    store.commit()

    return {
        "properties":       len(session.properties),
        "packages":         len(pkg_items),
        "services":         len(session.services),
        "settings":         sum(len(v) for v in session.settings_buckets.values()),
        "dumpsys_excerpts": len(session.dumpsys_excerpts),
        "facts":            len(session.facts),
        "findings":         len(chunks),
        "screens":          len(session.screen_snapshots),
    }
