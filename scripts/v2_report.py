#!/usr/bin/env python3
"""v2 session report renderer.

Generates a single-page HTML report (+ Markdown summary) from a completed
session manifest, the flat list of per-state HTML reports (already written by
current_screen_report.generate_report), and the system-context JSON.

Public API
----------
generate_session_report(session_dir, manifest) -> None
    Write session-report.html and session-report.md into session_dir.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any


def _element_path_label(element_path: str) -> str:
    """Return a readable short label from a resource_id or normalized_path string."""
    if not element_path:
        return ""
    # "com.example:id/my_button" → "my button"
    tail = element_path.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail.replace("_", " ").strip() or element_path

ROOT          = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def _render_markdown(manifest: dict[str, Any]) -> str:
    s = manifest["summary"]
    sc = manifest["stop_conditions"]
    lines = [
        f"# v2 Session Report — {manifest['session_id']}",
        "",
        f"**Device**: `{manifest['device_serial']}`",
        f"**Started**: {manifest['started_utc']}",
        f"**Finished**: {manifest.get('finished_utc') or 'N/A'}",
        f"**Stop reason**: `{manifest['stop_reason']}`",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| States discovered | {s['total_states']} |",
        f"| Transitions recorded | {s['total_transitions']} |",
        f"| Interaction attempts | {s['total_attempts']} |",
        f"| Failed taps | {s['total_failures']} |",
        "",
        "## Stop Conditions",
        "",
        f"| Limit | Value |",
        f"|-------|-------|",
        f"| Max states | {sc['max_states']} |",
        f"| Max transitions | {sc['max_transitions']} |",
        f"| Max depth | {sc['max_depth']} |",
        f"| Timeout (s) | {sc['timeout_seconds']} |",
        f"| Settle delay (ms) | {sc['settle_ms']} |",
        "",
        "## States",
        "",
    ]
    for st in manifest["states"]:
        depth_indicator = "  " * st["depth"]
        home = " *(home root)*" if st["is_home_root"] else ""
        display = st.get("display_name") or st["state_id"]
        lines.append(
            f"{depth_indicator}- **{display}**{home}"
            f"  `{st.get('package_name') or 'unknown'}`"
            f" / `{st.get('activity_name') or 'unknown'}`"
            f"  ({st['element_count']} elements, {st['candidate_count']} candidates)"
            f"  *(id: {st['state_id']})*"
        )
    lines += [
        "",
        "## Transitions",
        "",
    ]
    for tr in manifest["transitions"]:
        lines.append(
            f"- `{tr['source_state_id']}` → `{tr.get('destination_state_id') or '(no change)'}` "
            f"via `{tr['source_element_id']}` [{tr['outcome']}]"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_HTML_OUTCOME_CLASS = {
    "success":   "outcome-success",
    "no_change": "outcome-nochange",
    "failed":    "outcome-failed",
    "blocked":   "outcome-blocked",
}


def _escape(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_html(manifest: dict[str, Any], template: str) -> str:
    s = manifest["summary"]
    sc = manifest["stop_conditions"]
    session_id = manifest["session_id"]

    # ---- summary cards ----
    cards_html = "".join([
        f'<div class="card"><div class="card-num">{s["total_states"]}</div>'
        f'<div class="card-label">States</div></div>',
        f'<div class="card"><div class="card-num">{s["total_transitions"]}</div>'
        f'<div class="card-label">Transitions</div></div>',
        f'<div class="card"><div class="card-num">{s["total_attempts"]}</div>'
        f'<div class="card-label">Attempts</div></div>',
        f'<div class="card"><div class="card-num">{s["total_failures"]}</div>'
        f'<div class="card-label">Failures</div></div>',
        f'<div class="card"><div class="card-num">{_escape(manifest["stop_reason"])}</div>'
        f'<div class="card-label">Stop Reason</div></div>',
    ])

    # ---- states table ----
    state_rows = ""
    for st in manifest["states"]:
        home_badge = '<span class="badge-home">HOME</span>' if st["is_home_root"] else ""
        display = _escape(st.get("display_name") or st["state_id"])
        state_rows += (
            f"<tr>"
            f"<td><a href='states/{_escape(st['state_id'])}/report.html' "
            f"title='{_escape(st['state_id'])}'>"
            f"{display}</a>{home_badge}</td>"
            f"<td>{st['depth']}</td>"
            f"<td><code>{_escape(st.get('package_name') or '')}</code></td>"
            f"<td><code>{_escape(st.get('activity_name') or '')}</code></td>"
            f"<td>{st['element_count']}</td>"
            f"<td>{st['candidate_count']}</td>"
            f"<td>{_escape(st['visited_at_utc'])}</td>"
            f"</tr>\n"
        )

    # Build a lookup: state_id → display_name for destination labels
    _dn: dict[str, str] = {
        st["state_id"]: st.get("display_name") or st["state_id"]
        for st in manifest["states"]
    }

    # ---- transitions table ----
    tr_rows = ""
    for tr in manifest["transitions"]:
        cls = _HTML_OUTCOME_CLASS.get(tr["outcome"], "")
        dest_id = tr.get("destination_state_id") or ""
        dest_display = _dn.get(dest_id, dest_id) if dest_id else "—"
        elem_path = (tr.get("action_payload") or {}).get("element_path") or tr.get("source_element_id", "")
        elem_label = _element_path_label(elem_path)
        tr_rows += (
            f"<tr class='{cls}'>"
            f"<td title='{_escape(tr['source_state_id'])}'>{_escape(_dn.get(tr['source_state_id'], tr['source_state_id']))}</td>"
            f"<td title='{_escape(elem_path)}'><code>{_escape(elem_label or elem_path)}</code></td>"
            f"<td><code>{_escape(tr['action_type'])}</code></td>"
            f"<td title='{_escape(dest_id)}'>{_escape(dest_display)}</td>"
            f"<td>{_escape(tr['outcome'])}</td>"
            f"<td>{_escape(tr.get('error') or '')}</td>"
            f"</tr>\n"
        )

    # ---- stop conditions ----
    sc_rows = "".join(
        f"<tr><td>{_escape(k)}</td><td>{_escape(v)}</td></tr>"
        for k, v in sc.items()
    )

    replacements = {
        "{{SESSION_ID}}":         _escape(session_id),
        "{{DEVICE_SERIAL}}":      _escape(manifest["device_serial"]),
        "{{STARTED_UTC}}":        _escape(manifest["started_utc"]),
        "{{FINISHED_UTC}}":       _escape(manifest.get("finished_utc") or ""),
        "{{STOP_REASON}}":        _escape(manifest["stop_reason"]),
        "{{CARDS_HTML}}":         cards_html,
        "{{STATE_ROWS}}":         state_rows,
        "{{TRANSITION_ROWS}}":    tr_rows,
        "{{STOP_CONDITION_ROWS}}": sc_rows,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_session_report(session_dir: Path, manifest: dict[str, Any]) -> None:
    """Write session-report.md and session-report.html into session_dir."""
    md = _render_markdown(manifest)
    (session_dir / "session-report.md").write_text(md, encoding="utf-8")

    template_path = TEMPLATES_DIR / "session-report-template.html"
    if not template_path.exists():
        # Fallback: bare-bones HTML if template is missing
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Session Report</title></head><body>"
            "<pre>" + _escape(json.dumps(manifest, indent=2)) + "</pre>"
            "</body></html>"
        )
    else:
        html = _render_html(manifest, template_path.read_text(encoding="utf-8"))

    (session_dir / "session-report.html").write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI — standalone report regeneration
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Regenerate session-report.html and session-report.md from an existing session directory.",
    )
    ap.add_argument(
        "session_dir",
        type=Path,
        help="Path to the session directory containing session-manifest.json",
    )
    args = ap.parse_args()
    sdir = args.session_dir.resolve()
    mpath = sdir / "session-manifest.json"
    if not mpath.exists():
        print(f"ERROR: {mpath} not found", file=sys.stderr)
        sys.exit(1)
    mf = json.loads(mpath.read_text(encoding="utf-8"))
    generate_session_report(sdir, mf)
    print(f"Report regenerated:")
    print(f"  HTML: {sdir / 'session-report.html'}")
    print(f"  MD:   {sdir / 'session-report.md'}")
