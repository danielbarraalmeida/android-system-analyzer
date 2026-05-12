"""Thin OpenAI-compatible chat client for local LLM servers (LM Studio, Ollama).

We use the official ``openai`` SDK pointed at the local ``base_url`` so the
exact same code targets:

* LM Studio  (default)  http://127.0.0.1:1234/v1
* Ollama                 http://127.0.0.1:11434/v1
* Any OpenAI-compatible gateway

The client deliberately does not retry on protocol errors — the runner
handles malformed tool_calls with corrective reprompts.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI                              # type: ignore
    from openai import APIConnectionError                  # type: ignore
except ImportError as exc:                                 # pragma: no cover
    raise ImportError(
        "The 'openai' package is required for agentic mode. "
        "Install it via 'pip install -r requirements.txt'."
    ) from exc

try:
    from PIL import Image                                  # type: ignore
except ImportError:                                        # pragma: no cover
    Image = None  # type: ignore[assignment]


DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_MODEL    = "google/gemma-4-e4b"
DEFAULT_EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
# Local servers reject empty API keys; any non-empty placeholder works.
DEFAULT_API_KEY  = "local"

# Screenshot resize target — smaller = fewer tokens. 768px wide keeps
# automotive HMIs legible while staying inside Gemma 3 4B's context.
SCREENSHOT_MAX_WIDTH = 768


@dataclass
class LLMClient:
    """Wraps an OpenAI-compatible chat completion call."""

    model:    str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key:  str = DEFAULT_API_KEY
    temperature: float = 0.0
    timeout: float = 120.0
    embedding_model: str = DEFAULT_EMBEDDING_MODEL

    def __post_init__(self) -> None:
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # Health check — used by the runner to fail fast with a clear message.
    # ------------------------------------------------------------------
    def ping(self) -> tuple[bool, str]:
        try:
            self._client.models.list()
            return True, "ok"
        except APIConnectionError as exc:
            return False, f"cannot reach LLM at {self.base_url}: {exc}"
        except Exception as exc:                               # noqa: BLE001
            return False, f"LLM endpoint error at {self.base_url}: {exc}"

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        """Single chat-completion turn. Returns the raw ``choices[0].message``."""
        normalised = _coalesce_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": normalised,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message

    # ------------------------------------------------------------------
    # Embeddings (for the knowledge store / RAG layer)
    # ------------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return one embedding vector per input text.

        Returns ``None`` if the endpoint does not expose embeddings or
        any error occurs. Callers MUST handle the None case so the
        indexer can degrade gracefully (store rows without vectors).
        """
        if not texts:
            return []
        try:
            resp = self._client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )
        except Exception:                                      # noqa: BLE001
            return None
        try:
            return [list(item.embedding) for item in resp.data]
        except Exception:                                      # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# Message normalisation
# ---------------------------------------------------------------------------

# Strict chat templates (Mistral / Ministral, some Llama variants) require
# user and assistant roles to strictly alternate. The agent loop naturally
# produces back-to-back ``user`` messages (initial goal + observation, plus
# a screenshot follow-up after some tool results), which those templates
# reject with a 400. ``_coalesce_messages`` merges adjacent same-role
# messages so the wire format stays valid across providers.

def _coalesce_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if (
            out
            and role in ("user", "assistant", "system")
            and out[-1].get("role") == role
            and not msg.get("tool_calls")
            and not out[-1].get("tool_calls")
        ):
            out[-1] = _merge_same_role(out[-1], msg)
            continue
        out.append(dict(msg))
    return out


def _merge_same_role(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Return a single message whose content is the concatenation of a and b.

    Content may be a string or a list of parts (multimodal). The result
    keeps the richer representation when either side is multimodal.
    """
    merged = dict(a)
    ac = a.get("content")
    bc = b.get("content")

    if isinstance(ac, list) or isinstance(bc, list):
        parts: list[Any] = []
        for c in (ac, bc):
            if isinstance(c, list):
                parts.extend(c)
            elif isinstance(c, str) and c:
                parts.append({"type": "text", "text": c})
        merged["content"] = parts
    else:
        merged["content"] = (
            (ac or "") + ("\n\n" if ac and bc else "") + (bc or "")
        )
    return merged


# ---------------------------------------------------------------------------
# Multimodal helpers
# ---------------------------------------------------------------------------

def encode_screenshot_data_url(path: Path, max_width: int = SCREENSHOT_MAX_WIDTH) -> str | None:
    """Resize and base64-encode a PNG screenshot for chat content.

    Returns ``None`` if Pillow is unavailable or the file cannot be read.
    """
    if Image is None or not path.exists():
        return None
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            data = buf.getvalue()
    except Exception:                                         # noqa: BLE001
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def make_user_observation_message(
    observation: dict[str, Any],
    *,
    screenshot_data_url: str | None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build a multimodal user message wrapping a tool-result observation.

    When the screenshot is missing we fall back to a plain-text message.
    """
    import json as _json
    text = (note + "\n\n" if note else "") + (
        "Current screen observation:\n```json\n"
        + _json.dumps(observation, indent=2)
        + "\n```"
    )
    if screenshot_data_url is None:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": screenshot_data_url}},
        ],
    }
