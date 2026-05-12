"""Local web interface for the RAG-powered Android System Analyzer.

Exposes two flows:

- **Ask** — retrieval-only Q&A against the SQLite knowledge store.
- **Inspect** — kick off a new ``runner.run_agent`` session in a
  background thread and stream its log to the browser via SSE.

The package is import-light: ``app`` is the FastAPI factory; ``ask``
and ``sessions`` are the helpers it composes.
"""

from .ask     import AskResult, answer_question
from .app     import create_app
from .sessions import SessionRegistry

__all__ = ["AskResult", "answer_question", "create_app", "SessionRegistry"]
