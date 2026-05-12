"""RAG knowledge store package.

Layout
------
- ``store``     — SQLite schema + low-level upserts + cosine search.
- ``indexer``   — Reads an AgentSession's buffers + the LLM summary,
                  builds text representations, calls ``LLMClient.embed``,
                  writes rows into the store.
- ``retriever`` — Builds a compact "what we already know" markdown
                  context block to be injected into the system prompt.
"""

from .store     import KnowledgeStore
from .indexer   import index_session
from .retriever import get_context

__all__ = ["KnowledgeStore", "index_session", "get_context"]
