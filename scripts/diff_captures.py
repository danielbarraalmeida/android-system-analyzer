#!/usr/bin/env python3
"""Diff two Android screen snapshot JSON captures and output JSON or Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_capture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _index_by_path(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("path", "")): item for item in elements}


def _value_changed(a: Any, b: Any) -> bool:
    return a != b


def _element_change(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    changes: dict[str, Any] = {}

    keys = [
        "class_name",
        "resource_id",
        "text",
        "content_desc",
        "bounds",
        "center",
        "flags",
    ]

    for key in keys:
        if _value_changed(a.get(key), b.get(key)):
            changes[key] = {"before": a.get(key), "after": b.get(key)}

    if not changes:
        return None

    return {
        "path": a.get("path") or b.get("path"),
        "id_before": a.get("id"),
        "id_after": b.get("id"),
        "changes": changes,
    }


def build_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_idx = _index_by_path(before.get("elements", []))
    after_idx = _index_by_path(after.get("elements", []))

    before_paths = set(before_idx)
    after_paths = set(after_idx)

    removed = sorted(before_paths - after_paths)
    added = sorted(after_paths - before_paths)
    common = sorted(before_paths & after_paths)

    modified: list[dict[str, Any]] = []
    for path in common:
        change = _element_change(before_idx[path], after_idx[path])
        if change:
            modified.append(change)

    diff = {
        "meta": {
            "before_capture_id": before.get("capture", {}).get("capture_id"),
            "after_capture_id": after.get("capture", {}).get("capture_id"),
            "before_timestamp_utc": before.get("capture", {}).get("timestamp_utc"),
            "after_timestamp_utc": after.get("capture", {}).get("timestamp_utc"),
            "before_package": before.get("context", {}).get("package_name"),
            "after_package": after.get("context", {}).get("package_name"),
            "before_activity": before.get("context", {}).get("activity_name"),
            "after_activity": after.get("context", {}).get("activity_name"),
        },
        "summary": {
            "before_elements": len(before.get("elements", [])),
            "after_elements": len(after.get("elements", [])),
            "added_count": len(added),
            "removed_count": len(removed),
            "modified_count": len(modified),
        },
        "added_paths": added,
        "removed_paths": removed,
        "modified": modified,
    }
    return diff


def to_markdown(diff: dict[str, Any]) -> str:
    meta = diff["meta"]
    summary = diff["summary"]

    lines = [
        "# Capture Diff Report",
        "",
        "## Meta",
        "",
        f"- Before Capture ID: {meta['before_capture_id']}",
        f"- After Capture ID: {meta['after_capture_id']}",
        f"- Before Timestamp: {meta['before_timestamp_utc']}",
        f"- After Timestamp: {meta['after_timestamp_utc']}",
        f"- Before Package/Activity: {meta['before_package']} / {meta['before_activity']}",
        f"- After Package/Activity: {meta['after_package']} / {meta['after_activity']}",
        "",
        "## Summary",
        "",
        f"- Before elements: {summary['before_elements']}",
        f"- After elements: {summary['after_elements']}",
        f"- Added: {summary['added_count']}",
        f"- Removed: {summary['removed_count']}",
        f"- Modified: {summary['modified_count']}",
        "",
        "## Added Paths",
        "",
    ]

    for path in diff["added_paths"] or ["None"]:
        lines.append(f"- {path}")

    lines.extend(["", "## Removed Paths", ""])
    for path in diff["removed_paths"] or ["None"]:
        lines.append(f"- {path}")

    lines.extend(["", "## Modified Elements", ""])
    if not diff["modified"]:
        lines.append("- None")
    else:
        for item in diff["modified"]:
            lines.append(f"- Path: {item['path']}")
            for field, change in item["changes"].items():
                lines.append(
                    "  - {field}: {before} -> {after}".format(
                        field=field,
                        before=json.dumps(change["before"], ensure_ascii=True),
                        after=json.dumps(change["after"], ensure_ascii=True),
                    )
                )

    lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff two Android screen snapshot JSON files.")
    parser.add_argument("before", type=Path, help="Path to older screen-snapshot.json")
    parser.add_argument("after", type=Path, help="Path to newer screen-snapshot.json")
    parser.add_argument(
        "--format",
        choices=["json", "md"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file path. Prints to stdout when omitted.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    before = _load_capture(args.before)
    after = _load_capture(args.after)
    diff = build_diff(before, after)

    if args.format == "json":
        content = json.dumps(diff, indent=2)
    else:
        content = to_markdown(diff)

    if args.output:
        args.output.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
    else:
        print(content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
