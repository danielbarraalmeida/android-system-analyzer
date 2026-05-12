"""Pure retrieval+answer helper.

``answer_question`` is the single function the web API and the CLI
``ask`` subcommand both call. It deliberately knows nothing about
FastAPI, threads, or argparse so it stays unit-testable in isolation.

Pipeline:

1. Embed the question via ``llm.embed([question])``.
2. Cosine-search the ``facts`` table filtered by serial.
3. Build a grounded chat prompt with the top-k citations.
4. Single ``llm.chat(...)`` call, no tools.
5. Return the answer plus citation metadata.

The LLM client and the store are passed in by the caller — keep them
shared across requests so the SQLite connection and HTTP keep-alive
are reused.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from ..knowledge import KnowledgeStore
from ..llm_client import LLMClient


@dataclass
class Citation:
    fact_id:      int
    category:     str
    key:          str
    value:        str
    score:        float
    recorded_utc: str


@dataclass
class AskResult:
    serial:    str
    question:  str
    answer:    str
    citations: list[Citation] = field(default_factory=list)
    warnings:  list[str]      = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "serial":    self.serial,
            "question":  self.question,
            "answer":    self.answer,
            "citations": [asdict(c) for c in self.citations],
            "warnings":  self.warnings,
        }


_SYSTEM_PROMPT = (
    "You are the Android System Analyzer's RAG question-answering "
    "assistant. Use ONLY the cited facts below to answer the user's "
    "question about the device. If the facts are insufficient, say "
    "so explicitly — do not invent details. Quote the relevant "
    "category/key when you reference a fact."
)


def answer_question(
    *,
    store:    KnowledgeStore,
    llm:      LLMClient,
    serial:   str,
    question: str,
    top_k:    int = 5,
) -> AskResult:
    """Run one retrieve-then-answer cycle. Never raises on LLM glitches."""
    question = (question or "").strip()
    if not question:
        return AskResult(
            serial=serial, question="",
            answer="(empty question — nothing to ask)",
        )

    warnings: list[str] = []

    # ---- 1. Embed the question -------------------------------------------
    embeddings = llm.embed([question])
    if not embeddings:
        return AskResult(
            serial=serial, question=question,
            answer=(
                "Embeddings unavailable on the configured LLM endpoint, "
                "so I can't search the knowledge store. Configure an "
                "embedding model or use a different endpoint."
            ),
            warnings=["embed_failed"],
        )

    # ---- 2. Cosine search over facts for this device ---------------------
    hits = store.cosine_search(
        table="facts",
        query_embedding=embeddings[0],
        serial=serial,
        top_k=top_k,
    )

    citations: list[Citation] = []
    for hit in hits:
        p = hit.payload
        citations.append(Citation(
            fact_id=int(hit.row_id),
            category=p.get("category", ""),
            key=p.get("key", ""),
            value=p.get("value", ""),
            score=round(float(hit.score), 4),
            recorded_utc=p.get("recorded_utc", ""),
        ))

    if not citations:
        return AskResult(
            serial=serial, question=question,
            answer=(
                f"No facts recorded yet for device `{serial}`. Run an "
                "inspection session first."
            ),
            warnings=["no_citations"],
        )

    # ---- 3. Build grounded prompt ----------------------------------------
    fact_lines = []
    for i, c in enumerate(citations, 1):
        fact_lines.append(
            f"[{i}] ({c.category}) {c.key}: {c.value} "
            f"(recorded {c.recorded_utc}, score={c.score})"
        )
    facts_block = "\n".join(fact_lines)

    user_msg = (
        f"Device serial: `{serial}`\n\n"
        f"Question:\n{question}\n\n"
        f"Cited facts:\n{facts_block}\n\n"
        "Answer the question using only those facts. If you need to "
        "reference one, use the bracketed citation number."
    )

    # ---- 4. Single chat call --------------------------------------------
    try:
        message = llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            tools=[],
        )
        answer_text = getattr(message, "content", None) or "(empty answer)"
    except Exception as exc:                                       # noqa: BLE001
        warnings.append(f"chat_failed: {exc}")
        answer_text = (
            "Retrieved the facts but the LLM call failed. See "
            "warnings for the underlying error."
        )

    return AskResult(
        serial=serial,
        question=question,
        answer=answer_text.strip(),
        citations=citations,
        warnings=warnings,
    )
