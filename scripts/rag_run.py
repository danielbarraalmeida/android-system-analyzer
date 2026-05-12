"""CLI entry point for the RAG-powered Android System Analyzer.

Usage examples
--------------
    python scripts/rag_run.py
    python scripts/rag_run.py --serial 10.56.19.39:5555 --max-turns 30
    python scripts/rag_run.py --no-rag --goal "Just enumerate packages"
    python scripts/rag_run.py --allow-arbitrary-shell --base-url http://...

Flags
-----
    --serial                  ADB serial (auto-resolves if single device).
    --output-root             Where to write session_dir (default output/rag-sessions).
    --db-path                 SQLite knowledge DB (default output/knowledge.db).
    --no-rag                  Skip the knowledge store entirely.
    --require-root            adb root mode: required|preferred|skipped (default required).
    --allow-arbitrary-shell   Let `run_shell` execute non-allowlisted commands.
    --goal                    Goal text (otherwise default_goal.md is used).
    --goal-file               Path to a markdown file containing the goal.
    --max-turns               Max LLM turns (default 25).
    --timeout-seconds         Hard wall-clock cap (default 600).
    --settle-ms               UI settle delay if capture_home_screen is used.
    --base-url / --model / --api-key / --embedding-model
                              Override the LLM endpoint or models.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make ``scripts/`` importable so ``agent`` resolves.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agent.llm_client import (
    DEFAULT_API_KEY, DEFAULT_BASE_URL, DEFAULT_EMBEDDING_MODEL, DEFAULT_MODEL,
    LLMClient,
)
from agent.runner import Budget, run_agent
from agent.tools import open_session


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="rag_run",
        description="RAG-powered Android System Analyzer (root-privileged).",
    )
    p.add_argument("--serial", default=None,
                   help="ADB serial. Auto-resolved if exactly one device is connected.")
    p.add_argument("--output-root", default="output/rag-sessions",
                   help="Directory under which to create the session_dir.")
    p.add_argument("--db-path", default="output/knowledge.db",
                   help="SQLite path for the cumulative knowledge store.")
    p.add_argument("--no-rag", action="store_true",
                   help="Disable the knowledge store entirely (no read, no write).")
    p.add_argument("--require-root",
                   choices=["required", "preferred", "skipped"],
                   default="required",
                   help="adb root behaviour. `required` aborts if root fails.")
    p.add_argument("--allow-arbitrary-shell", action="store_true",
                   help="Allow run_shell to bypass the command allowlist.")
    p.add_argument("--goal", default=None,
                   help="Inline goal text. If omitted, default_goal.md is used.")
    p.add_argument("--goal-file", default=None,
                   help="Path to a markdown file containing the goal.")
    p.add_argument("--max-turns", type=int, default=25)
    p.add_argument("--timeout-seconds", type=float, default=600.0)
    p.add_argument("--settle-ms", type=int, default=1500)
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--api-key", default=DEFAULT_API_KEY)
    p.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    p.add_argument("--quiet", action="store_true",
                   help="Suppress live progress logging.")
    return p.parse_args(argv)


def _load_goal(args: argparse.Namespace) -> str:
    if args.goal_file:
        return Path(args.goal_file).read_text(encoding="utf-8")
    if args.goal:
        return args.goal
    return ""  # runner falls back to default_goal.md


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # ---- Open session (resolves serial, attempts adb root) ----------------
    try:
        session = open_session(
            serial=args.serial,
            output_root=Path(args.output_root),
            adb_root_mode=args.require_root,
            allow_arbitrary_shell=args.allow_arbitrary_shell,
            settle_ms=args.settle_ms,
        )
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"• session_dir: {session.session_dir}")
    print(f"• serial:      {session.serial}")
    print(f"• root mode:   {args.require_root}")
    print(f"• arbitrary shell: {args.allow_arbitrary_shell}")

    # ---- LLM client -------------------------------------------------------
    llm = LLMClient(
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        embedding_model=args.embedding_model,
    )

    # ---- Knowledge store --------------------------------------------------
    store = None
    if not args.no_rag:
        from agent.knowledge import KnowledgeStore
        store = KnowledgeStore(args.db_path)
        print(f"• knowledge:   {args.db_path}")
    else:
        print("• knowledge:   DISABLED (--no-rag)")

    # ---- Run --------------------------------------------------------------
    budget = Budget(max_turns=args.max_turns,
                    timeout_seconds=args.timeout_seconds)
    log = None
    if args.quiet:
        log = lambda _m: None  # noqa: E731

    result = run_agent(
        session=session,
        goal=_load_goal(args),
        llm=llm,
        budget=budget,
        knowledge_store=store,
        log=log,
    )

    # ---- Persist a JSON manifest of the run -------------------------------
    manifest = {
        "session_dir":     str(result.session_dir),
        "serial":          session.serial,
        "stop_reason":     result.stop.reason,
        "stop_detail":     result.stop.detail,
        "turns":           result.turns,
        "elapsed_seconds": round(result.elapsed_seconds, 2),
        "indexed_counts":  result.indexed_counts,
        "warnings":        session.warnings,
    }
    (session.session_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8",
    )

    print("\n— complete —")
    print(json.dumps(manifest, indent=2))

    if store is not None:
        store.close()

    # Non-zero exit only for hard failures (unreachable LLM, etc.).
    return 0 if result.stop.reason in ("finish", "model_summary",
                                       "budget_exhausted", "timeout") else 1


if __name__ == "__main__":
    raise SystemExit(main())
