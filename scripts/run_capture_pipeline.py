#!/usr/bin/env python3
"""v1 current-screen capture orchestrator.

Primary behaviour: capture the current Android screen and write JSON, Markdown,
and HTML artifacts.

Auxiliary behaviour (opt-in): diff the new capture against a previous one.
Diff is NOT part of the v1 standard output contract; pass --diff to enable it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from current_screen_report import OUTPUT_ROOT, CaptureFatalError, generate_report
from diff_captures import build_diff, to_markdown


def _list_capture_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        [p for p in root.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
    )


def _find_previous_capture(root: Path, current_capture: Path) -> Path | None:
    captures = _list_capture_dirs(root)
    previous = [p for p in captures if p != current_capture]
    return previous[-1] if previous else None


def _load_snapshot(capture_dir: Path) -> dict[str, Any]:
    snapshot_path = capture_dir / "screen-snapshot.json"
    return json.loads(snapshot_path.read_text(encoding="utf-8"))


def _write_text(path: Path, content: str) -> None:
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture current Android screen (v1). Diff is auxiliary and opt-in."
    )
    parser.add_argument(
        "--serial",
        help="Target Android device serial (required if multiple devices).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_ROOT,
        help="Capture output root directory (default: output/captures).",
    )
    parser.add_argument(
        "--adb-root",
        choices=["auto", "required", "never"],
        default="auto",
        help=(
            "ADB root policy: auto=attempt and continue if unavailable (default), "
            "required=fail if root unavailable, never=skip adb root."
        ),
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="(Auxiliary) Generate a diff report against the previous capture.",
    )
    parser.add_argument(
        "--diff-with",
        type=Path,
        help="(Auxiliary) Specific previous capture directory to diff against.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    capture_dir: Path | None = None
    try:
        capture_dir = generate_report(
            serial=args.serial,
            output_dir=args.output_dir,
            adb_root_mode=args.adb_root,
        )
        print(f"Capture:  {capture_dir}")
        print(f"  JSON:     {capture_dir / 'screen-snapshot.json'}")
        print(f"  Markdown: {capture_dir / 'report.md'}")
        print(f"  HTML:     {capture_dir / 'report.html'}")
    except CaptureFatalError as exc:
        if exc.capture_dir:
            capture_dir = exc.capture_dir
            print(f"Partial artifacts at: {capture_dir}")
        print(f"Error: {exc}")
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1

    if not (args.diff or args.diff_with):
        return 0

    # ── Auxiliary diff (opt-in) ──────────────────────────────────────────────
    previous_dir = (
        args.diff_with
        if args.diff_with
        else _find_previous_capture(args.output_dir, capture_dir)
    )
    if previous_dir is None:
        print("No previous capture available. Diff was not generated.")
        return 0

    before = _load_snapshot(previous_dir)
    after  = _load_snapshot(capture_dir)
    diff   = build_diff(before, after)

    diff_json = capture_dir / "diff-from-previous.json"
    diff_md   = capture_dir / "diff-from-previous.md"

    _write_text(diff_json, json.dumps(diff, indent=2))
    _write_text(diff_md,   to_markdown(diff))

    print(f"Diff source: {previous_dir}")
    print(f"Diff JSON:   {diff_json}")
    print(f"Diff MD:     {diff_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

