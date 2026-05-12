"""Build a "what we already know" context block for the system prompt.

Given a serial and (optionally) an LLM client capable of embedding the
user goal, the retriever returns a compact markdown block summarising
prior sessions' findings about this device. The runner prepends this
block to the system prompt so the agent doesn't repeat work.
"""

from __future__ import annotations

from typing import Any

from .store import KnowledgeStore


def _identity_block(store: KnowledgeStore, serial: str) -> list[str]:
    dev = store.get_device(serial)
    if not dev:
        return [f"This is the FIRST session for device `{serial}`. No prior data."]
    out = [f"Device `{serial}` — known since {dev['first_seen_utc']}."]
    if dev.get("manufacturer") or dev.get("model"):
        out.append(
            f"- Identity: {dev.get('manufacturer') or '?'} {dev.get('model') or '?'}"
        )
    if dev.get("android_version"):
        out.append(
            f"- Android {dev['android_version']}"
            + (f" (SDK {dev['sdk_int']})" if dev.get("sdk_int") else "")
        )
    if dev.get("build_fingerprint"):
        out.append(f"- Build: `{dev['build_fingerprint']}`")
    return out


def _recent_facts_block(store: KnowledgeStore, serial: str, limit: int) -> list[str]:
    facts = store.get_known_facts(serial, limit=limit)
    if not facts:
        return []
    lines = ["", f"### Recent recorded facts ({len(facts)})"]
    # Group by category for readability.
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        by_cat.setdefault(f["category"], []).append(f)
    for cat in sorted(by_cat):
        lines.append(f"- **{cat}**:")
        for f in by_cat[cat][:6]:
            val = (f["value"] or "").replace("\n", " ")
            if len(val) > 140:
                val = val[:137] + "…"
            lines.append(f"    - `{f['key']}` = {val}")
    return lines


def _semantic_block(
    store: KnowledgeStore,
    serial: str,
    llm: Any,
    goal: str,
    top_k: int,
) -> list[str]:
    if not llm or not goal.strip():
        return []
    vecs = llm.embed([goal])
    if not vecs:
        return []
    query = vecs[0]
    hits: list[tuple[str, Any]] = []
    for table in ("findings", "facts", "dumpsys_excerpts"):
        try:
            results = store.cosine_search(
                table=table, query_embedding=query,
                serial=serial, top_k=top_k,
            )
        except ValueError:
            continue
        for h in results:
            if h.score <= 0.0:
                continue
            hits.append((h.table, h))
    if not hits:
        return []
    hits.sort(key=lambda kv: kv[1].score, reverse=True)
    lines = ["", "### Most relevant prior findings for this goal"]
    for _table, hit in hits[: top_k * 2]:
        text = (hit.payload.get("text_repr") or "").strip().replace("\n", " ")
        if len(text) > 200:
            text = text[:197] + "…"
        lines.append(f"- ({hit.table}, score={hit.score:.2f}) {text}")
    return lines


def get_context(
    *,
    store: KnowledgeStore | None,
    serial: str,
    goal: str,
    llm: Any = None,
    top_k: int = 5,
    fact_limit: int = 50,
) -> str:
    """Return a markdown block (possibly empty) to inject into the system prompt."""
    if store is None:
        return ""
    parts: list[str] = ["## Prior knowledge about this device", ""]
    parts.extend(_identity_block(store, serial))
    parts.extend(_recent_facts_block(store, serial, fact_limit))
    parts.extend(_semantic_block(store, serial, llm, goal, top_k))
    block = "\n".join(parts).rstrip()
    return block + "\n" if block else ""
