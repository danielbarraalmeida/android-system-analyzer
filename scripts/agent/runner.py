"""Agent loop for the RAG-powered Android System Analyzer.

Architecture
------------
Pre-session:
  1. Caller (CLI) opens an ``AgentSession`` (already root-elevated).
  2. Caller passes an optional ``KnowledgeStore``. If present, the runner
     uses it to embed the goal and inject a "prior knowledge" block into
     the system prompt — this is the RAG read path.
  3. The agent's first observation is auto-populated by an unconditional
     ``get_device_properties`` call so the model always sees the device
     identity without burning a turn.

Loop:
  For each turn the model returns either:
    - a tool call → execute, append observation, continue.
    - a final assistant message → if it looks like a summary, stop;
      otherwise nudge for a tool call and continue.
  Stops on the ``finish`` tool, exhausted turn budget, or hard timeout.

Post-session:
  The runner extracts a final markdown summary (from ``finish``, the
  last assistant message, or a synthesised fallback) and — if a store
  is configured — calls ``index_session`` to write everything into the
  knowledge DB. This is the RAG write path.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import tools as agent_tools
from .llm_client import LLMClient
from .schemas import SCHEMAS_BY_NAME, TOOL_SCHEMAS
from .tools import AgentSession, TOOL_REGISTRY

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

LogFn = Callable[[str], None]


# ---------------------------------------------------------------------------
# Logging helpers (Windows-safe)
# ---------------------------------------------------------------------------

def _default_logger(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
        print(msg.encode(encoding, errors="replace").decode(encoding), flush=True)


def _noop_logger(_msg: str) -> None:
    pass


def _truncate(text: str, n: int = 160) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class Budget:
    max_turns:       int   = 25
    timeout_seconds: float = 600.0


@dataclass
class StopCondition:
    reason: str
    detail: str = ""


@dataclass
class AgentResult:
    session_dir:     Path
    transcript:      list[dict[str, Any]]
    final_summary:   str | None
    stop:            StopCondition
    turns:           int
    elapsed_seconds: float
    indexed_counts:  dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt loading + RAG injection
# ---------------------------------------------------------------------------

def _read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_system_prompt(
    *,
    serial: str,
    rag_context: str,
    allow_arbitrary_shell: bool,
) -> str:
    base = _read_prompt("system.md")
    try:
        dumpsys = _read_prompt("dumpsys_sections.md")
    except FileNotFoundError:
        dumpsys = ""
    parts = [
        f"Target device serial: `{serial}`.",
        ("Shell mode: ARBITRARY (run_shell accepts any command)."
         if allow_arbitrary_shell else
         "Shell mode: ALLOWLIST (run_shell only accepts allowlisted commands)."),
        "",
        base,
    ]
    if dumpsys.strip():
        parts.extend(["", dumpsys])
    if rag_context.strip():
        parts.extend(["", "---", "", rag_context])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool call parsing & validation
# ---------------------------------------------------------------------------

def _serialise_message(msg: Any) -> dict[str, Any]:
    """Convert OpenAI SDK message → plain dict for the transcript."""
    out: dict[str, Any] = {"role": "assistant"}
    content = getattr(msg, "content", None)
    if content:
        out["content"] = content
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        out["tool_calls"] = [
            {
                "id":   tc.id,
                "type": "function",
                "function": {
                    "name":      tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out


def _parse_args(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"arguments are not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "arguments must be a JSON object"
    return parsed, None


def _validate(name: str, args: dict[str, Any]) -> str | None:
    schema = SCHEMAS_BY_NAME.get(name)
    if schema is None:
        return f"unknown tool {name!r}; valid: {sorted(SCHEMAS_BY_NAME)}"
    params = schema["function"]["parameters"]
    required = params.get("required", [])
    missing = [k for k in required if k not in args]
    if missing:
        return f"missing required argument(s): {missing}"
    allowed = set(params.get("properties", {}).keys())
    extra = sorted(set(args) - allowed)
    if extra and params.get("additionalProperties") is False:
        return f"unexpected argument(s): {extra}"
    return None


def _dispatch(name: str, args: dict[str, Any], session: AgentSession) -> dict[str, Any]:
    fn = TOOL_REGISTRY[name]
    try:
        return fn(session, **args)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": f"tool {name!r} raised: {exc.__class__.__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Auto-bootstrap: run get_device_properties once and inject as user message
# ---------------------------------------------------------------------------

def _bootstrap_observation(session: AgentSession, log: LogFn) -> str:
    log("  • bootstrap: get_device_properties")
    result = TOOL_REGISTRY["get_device_properties"](session)
    return json.dumps({
        "auto_bootstrap": "get_device_properties",
        "result":         result,
    }, indent=2)


# ---------------------------------------------------------------------------
# Summary extraction
# ---------------------------------------------------------------------------

def _looks_like_summary(text: str) -> bool:
    if not text:
        return False
    if len(text.strip()) < 80:
        return False
    return True


def _fallback_summary(session: AgentSession) -> str:
    ident = session.properties
    lines = ["# Android System Analysis (fallback summary)", ""]
    headline_keys = [
        ("ro.product.manufacturer", "Manufacturer"),
        ("ro.product.model",        "Model"),
        ("ro.build.version.release","Android version"),
        ("ro.build.version.sdk",    "SDK level"),
        ("ro.build.fingerprint",    "Build fingerprint"),
        ("ro.hardware",             "Hardware"),
        ("ro.board.platform",       "Board platform"),
    ]
    for key, label in headline_keys:
        if key in ident:
            lines.append(f"- **{label}**: {ident[key]}")
    lines.append("")
    lines.append(f"- Packages enumerated: {len(session.packages)}")
    lines.append(f"- Services seen: {len(session.services)}")
    lines.append(f"- Dumpsys sections collected: {len(session.dumpsys_excerpts)}")
    lines.append(f"- Settings buckets read: {len(session.settings_buckets)}")
    lines.append(f"- Free-text facts recorded: {len(session.facts)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_agent(
    *,
    session: AgentSession,
    goal: str,
    llm: LLMClient,
    budget: Budget | None = None,
    knowledge_store: Any = None,
    log: LogFn | None = None,
    extra_user_context: str = "",
) -> AgentResult:
    """Drive the agent loop. Returns once finished, budget-exhausted, or timeout."""
    budget = budget or Budget()
    log    = log if log is not None else _default_logger
    session.log = log

    # ---- RAG read: build prior-knowledge context block --------------------
    rag_context = ""
    if knowledge_store is not None:
        from .knowledge import get_context  # local import to avoid cycles
        try:
            rag_context = get_context(
                store=knowledge_store, serial=session.serial,
                goal=goal, llm=llm, top_k=5,
            )
        except Exception as exc:                               # noqa: BLE001
            session.warnings.append(f"RAG retrieval failed: {exc}")

    system_prompt = _build_system_prompt(
        serial=session.serial,
        rag_context=rag_context,
        allow_arbitrary_shell=session.allow_arbitrary_shell,
    )

    # ---- Health-check the LLM --------------------------------------------
    ok, detail = llm.ping()
    if not ok:
        return AgentResult(
            session_dir=session.session_dir,
            transcript=[], final_summary=None,
            stop=StopCondition("llm_unreachable", detail),
            turns=0, elapsed_seconds=0.0,
        )

    # ---- Compose initial message stack -----------------------------------
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _build_user_prompt(goal, extra_user_context)},
    ]

    # Auto-run get_device_properties so the model never has to ask "who is this".
    bootstrap_obs = _bootstrap_observation(session, log)
    messages.append({
        "role":    "user",
        "content": f"Pre-fetched device identity:\n{bootstrap_obs}",
    })

    transcript: list[dict[str, Any]] = [
        {"role": "system", "content": _truncate(system_prompt, 4000)},
        {"role": "user",   "content": goal},
    ]

    started = time.monotonic()
    stop = StopCondition("budget_exhausted")
    final_summary: str | None = None

    # ---- Main loop --------------------------------------------------------
    for turn in range(1, budget.max_turns + 1):
        if time.monotonic() - started > budget.timeout_seconds:
            stop = StopCondition("timeout",
                                 f"{budget.timeout_seconds:.0f}s elapsed")
            break

        log(f"\n— Turn {turn}/{budget.max_turns} —")
        try:
            msg = llm.chat(messages, TOOL_SCHEMAS)
        except Exception as exc:                               # noqa: BLE001
            stop = StopCondition("llm_error", str(exc))
            break

        msg_dict = _serialise_message(msg)
        messages.append(msg_dict)
        transcript.append(msg_dict)

        tool_calls = msg_dict.get("tool_calls") or []
        text       = msg_dict.get("content") or ""

        if not tool_calls:
            # Model produced freeform text. If it looks like a summary, treat
            # as implicit finish; otherwise nudge for a tool call.
            if _looks_like_summary(text):
                final_summary = text
                stop = StopCondition("model_summary")
                break
            log(f"  ← model spoke without tools: {_truncate(text)}")
            messages.append({
                "role": "user",
                "content": (
                    "You must respond with a tool call. If you have finished, "
                    "call `finish` with a markdown summary; otherwise pick "
                    "the next inspection tool."
                ),
            })
            continue

        # Process each tool call sequentially.
        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]
            args, parse_err = _parse_args(raw_args)
            if parse_err is not None:
                observation = {"error": parse_err}
            else:
                vali_err = _validate(name, args or {})
                if vali_err is not None:
                    observation = {"error": vali_err}
                else:
                    log(f"  → {name}({_truncate(json.dumps(args), 80)})")
                    observation = _dispatch(name, args or {}, session)
                    if name == "finish":
                        final_summary = (args or {}).get("summary") or ""
                        stop = StopCondition("finish")

            tool_msg = {
                "role":         "tool",
                "tool_call_id": tc["id"],
                "name":         name,
                "content":      json.dumps(observation),
            }
            messages.append(tool_msg)
            transcript.append({
                "role":      "tool",
                "name":      name,
                "arguments": args,
                "result_preview": _truncate(json.dumps(observation), 240),
            })

            if name == "finish":
                break

        if stop.reason == "finish":
            break

    elapsed = time.monotonic() - started

    # ---- Make sure we always have *some* summary --------------------------
    if not final_summary:
        final_summary = _fallback_summary(session)

    # ---- Write transcript + summary to disk ------------------------------
    session.ensure_dirs()
    transcript_path = session.session_dir / "transcript.json"
    transcript_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    summary_path = session.session_dir / "summary.md"
    summary_path.write_text(final_summary, encoding="utf-8")
    (session.session_dir / "command_log.json").write_text(
        json.dumps(session.command_log, indent=2), encoding="utf-8",
    )
    if session.warnings:
        (session.session_dir / "warnings.txt").write_text(
            "\n".join(session.warnings), encoding="utf-8",
        )

    # ---- RAG write: index everything -------------------------------------
    indexed_counts: dict[str, int] = {}
    if knowledge_store is not None:
        from .knowledge import index_session  # local import
        session_id = session.session_dir.name
        try:
            indexed_counts = index_session(
                store=knowledge_store, session=session,
                session_id=session_id, summary=final_summary, llm=llm,
            )
            log(f"\n• indexed into knowledge DB: {indexed_counts}")
        except Exception as exc:                               # noqa: BLE001
            session.warnings.append(f"indexing failed: {exc}")
            log(f"  ! indexing failed: {exc}")

    return AgentResult(
        session_dir=session.session_dir,
        transcript=transcript, final_summary=final_summary,
        stop=stop, turns=turn if 'turn' in locals() else 0,
        elapsed_seconds=elapsed, indexed_counts=indexed_counts,
    )


def _build_user_prompt(goal: str, extra: str) -> str:
    try:
        default = _read_prompt("default_goal.md").strip()
    except FileNotFoundError:
        default = ""
    body = goal.strip() or default
    if extra.strip():
        body += "\n\n## Additional context\n" + extra.strip()
    return body
