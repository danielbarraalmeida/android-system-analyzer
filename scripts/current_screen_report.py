#!/usr/bin/env python3
"""Capture Android current screen and generate JSON, Markdown, and HTML reports.

Layers
------
1. ADB transport  – command execution and raw artifact retrieval only.
2. XML parser     – UIAutomator XML → canonical in-memory element list.
3. Model builder  – assemble ScreenSnapshotModel from parsed data.
4. Renderers      – JSON, Markdown, HTML generated solely from the in-memory model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html as html_module
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
OUTPUT_ROOT = ROOT / "output" / "captures"
SCHEMA_PATH = TEMPLATES_DIR / "screen-snapshot.schema.json"

# Minimum bounding-box area (px²) for swipe candidacy.
# NOTE: The v1 contract specifies "width * height > threshold" but does not define
# the threshold value. This constant is the implementation default; update when
# the contract is revised to specify an explicit value.
SWIPE_AREA_THRESHOLD = 10_000

# All UIAutomator XML attribute keys whose presence is normalised explicitly.
# The contract lists 21 keys (§2.4) despite saying "20"; all 21 are included.
_KNOWN_XML_KEYS: tuple[str, ...] = (
    "index", "text", "resource-id", "class", "package", "content-desc",
    "checkable", "checked", "clickable", "enabled", "focusable", "focused",
    "scrollable", "long-clickable", "password", "selected", "bounds",
    "hint", "input-type", "pane-title", "drawing-order",
)
_KNOWN_XML_KEYS_SET: frozenset[str] = frozenset(_KNOWN_XML_KEYS)

# Keys whose absent value normalises to "" rather than null (§3 rule 2).
_EMPTY_STRING_KEYS: frozenset[str] = frozenset({"text", "content-desc"})


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 – ADB Transport
# ─────────────────────────────────────────────────────────────────────────────

def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _run_adb(
    args: list[str],
    *,
    serial: str | None,
    command_log: list[dict[str, Any]],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    started = _now_utc()
    try:
        completed = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        raise RuntimeError(
            "adb executable not found. Install Android SDK Platform Tools and "
            "ensure 'adb' is on your PATH."
        )
    finished = _now_utc()
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    completed.returncode,
        "stdout":       completed.stdout.strip(),
        "stderr":       completed.stderr.strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"ADB command failed (exit {completed.returncode}): {' '.join(cmd)}\n"
            f"stderr: {completed.stderr.strip()}"
        )
    return completed


def _resolve_serial(
    command_log: list[dict[str, Any]],
    explicit_serial: str | None,
) -> str:
    if explicit_serial:
        return explicit_serial
    completed = _run_adb(["devices"], serial=None, command_log=command_log)
    devices: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    if not devices:
        raise RuntimeError(
            "No connected Android devices found. Connect a device or start an emulator, "
            "ensure USB debugging is enabled, and run 'adb devices' to confirm."
        )
    if len(devices) > 1:
        raise RuntimeError(
            f"Multiple devices connected ({', '.join(devices)}). "
            "Provide --serial <device_serial> to select one."
        )
    return devices[0]


def _ensure_adb_root(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
    adb_root_mode: str,
) -> None:
    """Try to run adbd as root based on policy.

    Modes:
    - auto: attempt adb root and continue if unavailable.
    - required: fail fast if root cannot be enabled.
    - never: skip root attempt.
    """
    if adb_root_mode == "never":
        return

    root_cmd = _run_adb(["root"], serial=serial, command_log=command_log, check=False)
    root_out = "\n".join([root_cmd.stdout, root_cmd.stderr]).lower()

    if root_cmd.returncode != 0:
        msg = (
            "adb root command failed; continuing without root. "
            f"exit={root_cmd.returncode}"
        )
        if adb_root_mode == "required":
            raise RuntimeError(msg)
        warnings.append(msg)
        return

    # If adbd restarts, wait before subsequent shell commands.
    _run_adb(["wait-for-device"], serial=serial, command_log=command_log, check=False)
    shell_id = _run_adb(["shell", "id"], serial=serial, command_log=command_log, check=False)
    is_root = "uid=0" in (shell_id.stdout or "")

    if is_root:
        return

    production_locked = "cannot run as root in production builds" in root_out
    if production_locked:
        msg = "adb root unavailable on production-locked build; continuing without root."
    else:
        msg = "adb root was requested but shell is still non-root; continuing without root."

    if adb_root_mode == "required":
        raise RuntimeError(msg)

    warnings.append(msg)


def _capture_ui_dump(
    serial: str,
    capture_dir: Path,
    command_log: list[dict[str, Any]],
) -> Path:
    remote_path = "/sdcard/window_dump.xml"
    local_path = capture_dir / "window_dump.xml"
    # --windows captures every visible window layer (system UI, overlays, app) so
    # elements like the clock, nav bar icons, climate tiles and app-grid are included.
    _run_adb(["shell", "uiautomator", "dump", "--windows", remote_path], serial=serial, command_log=command_log)
    _run_adb(["pull", remote_path, str(local_path)], serial=serial, command_log=command_log)
    return local_path


def _capture_screenshot(
    serial: str,
    capture_dir: Path,
    command_log: list[dict[str, Any]],
) -> Path:
    local_path = capture_dir / "screen.png"
    cmd = ["adb", "-s", serial, "exec-out", "screencap", "-p"]
    started = _now_utc()
    with local_path.open("wb") as fh:
        completed = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    finished = _now_utc()
    command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    completed.returncode,
        "stdout":       "",
        "stderr":       completed.stderr.decode("utf-8", errors="replace").strip(),
        "started_utc":  started.isoformat(),
        "finished_utc": finished.isoformat(),
    })
    if completed.returncode != 0:
        raise RuntimeError(
            f"Screenshot capture failed (exit {completed.returncode}): {' '.join(cmd)}"
        )
    return local_path


def _get_package_activity(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[str, str]:
    completed = _run_adb(
        ["shell", "dumpsys", "window", "windows"],
        serial=serial, command_log=command_log, check=False,
    )
    package_name = "unknown.package"
    activity_name = "unknown.activity"
    for line in completed.stdout.splitlines():
        if "mCurrentFocus" not in line and "mFocusedApp" not in line:
            continue
        match = re.search(r"([A-Za-z0-9_$.]+)/([A-Za-z0-9_$.]+)", line)
        if match:
            package_name = match.group(1)
            activity_name = match.group(2)
            break

    # Fallback for OEM builds where dumpsys window omits mCurrentFocus/mFocusedApp.
    if package_name == "unknown.package":
        completed_top = _run_adb(
            ["shell", "dumpsys", "activity", "top"],
            serial=serial,
            command_log=command_log,
            check=False,
        )
        for line in completed_top.stdout.splitlines():
            if " ACTIVITY " not in line:
                continue
            match = re.search(r"\b([A-Za-z0-9_$.]+)/([A-Za-z0-9_$.]+)\b", line)
            if match:
                package_name = match.group(1)
                activity_name = match.group(2)
                break

    if package_name == "unknown.package":
        warnings.append("Unable to determine focused package/activity from dumpsys output.")
    return package_name, activity_name


def _get_screen_size(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> tuple[int, int]:
    completed = _run_adb(
        ["shell", "wm", "size"], serial=serial, command_log=command_log, check=False,
    )
    match = re.search(r"(\d+)x(\d+)", completed.stdout)
    if match:
        return int(match.group(1)), int(match.group(2))
    warnings.append(
        "Could not determine screen size from 'wm size'; will derive from element bounds."
    )
    return 0, 0


def _get_screen_density(
    serial: str,
    command_log: list[dict[str, Any]],
    warnings: list[str],
) -> int | None:
    completed = _run_adb(
        ["shell", "wm", "density"], serial=serial, command_log=command_log, check=False,
    )
    match = re.search(r"\d+", completed.stdout)
    if match:
        return int(match.group())
    warnings.append("Could not determine screen density from 'wm density'.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 – XML Parser / Normalizer
# ─────────────────────────────────────────────────────────────────────────────

def _as_bool(value: str | None) -> bool:
    return (value or "").lower() == "true"


def _safe_text(value: str | None) -> str:
    """Empty string when value is None or empty; never null (§3 rule 2)."""
    return "" if not value else value


def _null_or_str(value: str | None) -> str | None:
    """None when value is None or empty; preserves non-empty strings (§3 rule 3)."""
    return None if not value else value


def _parse_bounds(bounds_raw: str | None) -> dict[str, int]:
    match = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", bounds_raw or "")
    if not match:
        return {"left": 0, "top": 0, "right": 0, "bottom": 0}
    left, top, right, bottom = (int(g) for g in match.groups())
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _compute_element_id(
    normalized_path: str,
    class_name: str | None,
    resource_id: str | None,
    package: str | None,
    bounds: dict[str, int],
    sibling_index: int,
) -> str:
    """Deterministic SHA-1 id from structural fields only; text is excluded (§4.2)."""
    basis = "|".join([
        normalized_path,
        class_name   if class_name   is not None else "NULL",
        resource_id  if resource_id  is not None else "NULL",
        package      if package      is not None else "NULL",
        str(bounds["left"]),
        str(bounds["top"]),
        str(bounds["right"]),
        str(bounds["bottom"]),
        str(sibling_index),
    ])
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()
    return f"el_v1_{digest[:16]}"


def _compute_view_type_hint(class_name: str | None) -> str | None:
    if not class_name:
        return None
    tail = class_name.rsplit(".", 1)[-1]
    return tail or None


def _compute_interaction_candidacy(
    clickable: bool,
    long_clickable: bool,
    scrollable: bool,
    focusable: bool,
    enabled: bool,
    width: int,
    height: int,
) -> tuple[list[str], list[str]]:
    """Returns (action_types, candidacy_reasons) per §7."""
    action_types: list[str] = []
    reasons: list[str] = []
    if clickable and enabled:
        action_types.append("tap")
        reasons.append("clickable=true, enabled=true \u2192 tap")
    if long_clickable and enabled:
        action_types.append("long_tap")
        reasons.append("long_clickable=true, enabled=true \u2192 long_tap")
    if scrollable and enabled:
        action_types.append("scroll")
        reasons.append("scrollable=true, enabled=true \u2192 scroll")
        if width * height > SWIPE_AREA_THRESHOLD:
            action_types.append("swipe")
            reasons.append(
                f"scrollable=true, enabled=true, area={width * height}>{SWIPE_AREA_THRESHOLD}"
                " \u2192 swipe"
            )
    if focusable and enabled:
        action_types.append("input")
        reasons.append("focusable=true, enabled=true \u2192 input")
    return action_types, reasons


def _build_source_attributes(
    attrs: dict[str, str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Split attributes into known (normalised) and extra (verbatim) (§2.4)."""
    known: dict[str, Any] = {}
    for key in _KNOWN_XML_KEYS:
        raw = attrs.get(key)
        if key in _EMPTY_STRING_KEYS:
            known[key] = "" if raw is None else raw
        else:
            known[key] = raw  # None when absent
    extra: dict[str, str] = {k: v for k, v in attrs.items() if k not in _KNOWN_XML_KEYS_SET}
    return known, extra


def _extract_elements(ui_dump_path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse UIAutomator XML; return (elements_in_preorder, xml_node_count) (§8)."""
    try:
        tree = ET.parse(str(ui_dump_path))
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse UI dump XML: {exc}") from exc

    root_el = tree.getroot()
    elements: list[dict[str, Any]] = []
    xml_node_count = 0
    # Mutable counter incremented once per <window> element so each window
    # gets a unique /w[N] prefix — prevents normalized_path and element_id
    # collisions when uiautomator dump --windows emits multiple windows.
    window_idx: list[int] = [0]

    def walk(
        element: ET.Element,
        depth: int,
        path: str,
        parent_path: str | None,
        sibling_index: int,
    ) -> None:
        nonlocal xml_node_count

        if element.tag != "node":
            # Non-node wrapper (e.g. <hierarchy>, <window>, <display>, <displays>).
            # With --windows the structure is:
            #   <displays> → <display> → <window> → <hierarchy> → <node>…
            if element.tag == "window":
                # Each <window> is an independent root.  Prefix all paths from
                # this window with /w[N] so they never collide with other windows.
                win_prefix = f"/w[{window_idx[0]}]"
                window_idx[0] += 1

                def _iter_top_nodes(el: ET.Element):
                    """Yield the immediate <node> descendants, skipping wrappers."""
                    if el.tag == "node":
                        yield el
                    else:
                        for ch in el:
                            yield from _iter_top_nodes(ch)

                top_nodes = list(_iter_top_nodes(element))
                for idx, top_node in enumerate(top_nodes):
                    walk(top_node, 0, f"{win_prefix}/n[{idx}]", None, idx)
            else:
                # <displays>, <display>, <hierarchy> — transparent pass-through
                for child in element:
                    walk(child, depth, path, parent_path, sibling_index)
            return

        xml_node_count += 1
        preorder_index = xml_node_count - 1  # 0-based (§5)

        attrs = element.attrib
        bounds_raw_val: str = attrs.get("bounds", "")
        bounds = _parse_bounds(bounds_raw_val or None)
        width = max(0, bounds["right"] - bounds["left"])
        height = max(0, bounds["bottom"] - bounds["top"])
        center_x = bounds["left"] + width // 2
        center_y = bounds["top"] + height // 2

        class_name   = _null_or_str(attrs.get("class"))
        resource_id  = _null_or_str(attrs.get("resource-id"))
        package_val  = _null_or_str(attrs.get("package"))
        if package_val is None and resource_id and ":" in resource_id:
            package_val = resource_id.split(":")[0] or None

        clickable      = _as_bool(attrs.get("clickable"))
        long_clickable = _as_bool(attrs.get("long-clickable"))
        scrollable     = _as_bool(attrs.get("scrollable"))
        focusable      = _as_bool(attrs.get("focusable"))
        focused        = _as_bool(attrs.get("focused"))
        enabled        = _as_bool(attrs.get("enabled"))
        selected       = _as_bool(attrs.get("selected"))
        checked        = _as_bool(attrs.get("checked"))
        checkable      = _as_bool(attrs.get("checkable"))
        password       = _as_bool(attrs.get("password"))

        element_id = _compute_element_id(
            path, class_name, resource_id, package_val, bounds, sibling_index,
        )
        action_types, candidacy_reasons = _compute_interaction_candidacy(
            clickable, long_clickable, scrollable, focusable, enabled, width, height,
        )
        source_attrs, source_attrs_extra = _build_source_attributes(attrs)

        elements.append({
            # Identity (§2.4)
            "element_id":         element_id,
            "identity_version":   "v1",
            "normalized_path":    path,
            "parent_path":        parent_path,
            "depth":              depth,
            "sibling_index":      sibling_index,
            "xml_index_preorder": preorder_index,
            # Classification
            "class_name":         class_name,
            "resource_id":        resource_id,
            "package":            package_val,
            "view_type_hint":     _compute_view_type_hint(class_name),
            # Content
            "text":               _safe_text(attrs.get("text")),
            "content_desc":       _safe_text(attrs.get("content-desc")),
            "hint":               _null_or_str(attrs.get("hint")),
            "value":              None,
            "input_type":         _null_or_str(attrs.get("input-type")),
            # Geometry
            "bounds_raw":         bounds_raw_val,
            "bounds":             bounds,
            "width":              width,
            "height":             height,
            "center_x":           center_x,
            "center_y":           center_y,
            # State flags
            "clickable":          clickable,
            "long_clickable":     long_clickable,
            "focusable":          focusable,
            "focused":            focused,
            "scrollable":         scrollable,
            "selected":           selected,
            "enabled":            enabled,
            "checked":            checked,
            "checkable":          checkable,
            "password":           password,
            # Interaction candidacy
            "is_interaction_candidate": len(action_types) > 0,
            "action_types":             action_types,
            "candidacy_reasons":        candidacy_reasons,
            # Raw attribute preservation
            "source_attributes":        source_attrs,
            "source_attributes_extra":  source_attrs_extra,
        })

        node_kids = [c for c in element if c.tag == "node"]
        for idx, child in enumerate(node_kids):
            walk(child, depth + 1, f"{path}/n[{idx}]", path, idx)

    walk(root_el, 0, "", None, 0)
    return elements, xml_node_count


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 – Model Builder
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_serial(serial: str) -> str:
    return re.sub(r"[^A-Za-z0-9\-_]", "_", serial)


def _make_capture_id(timestamp: dt.datetime, serial: str) -> str:
    ms = str(timestamp.microsecond // 1000).zfill(3)
    ts = timestamp.strftime(f"%Y%m%dT%H%M%S{ms}") + "Z"
    return f"cap_{ts}_{_sanitize_serial(serial)}"


def _build_summary(elements: list[dict[str, Any]], xml_node_count: int) -> dict[str, Any]:
    ec = len(elements)
    return {
        "xml_node_count":              xml_node_count,
        "element_count":               ec,
        "clickable_count":             sum(1 for e in elements if e["clickable"]),
        "long_clickable_count":        sum(1 for e in elements if e["long_clickable"]),
        "focusable_count":             sum(1 for e in elements if e["focusable"]),
        "focused_count":               sum(1 for e in elements if e["focused"]),
        "enabled_count":               sum(1 for e in elements if e["enabled"]),
        "scrollable_count":            sum(1 for e in elements if e["scrollable"]),
        "selected_count":              sum(1 for e in elements if e["selected"]),
        "checked_count":               sum(1 for e in elements if e["checked"]),
        "checkable_count":             sum(1 for e in elements if e["checkable"]),
        "password_count":              sum(1 for e in elements if e["password"]),
        "interaction_candidate_count": sum(1 for e in elements if e["is_interaction_candidate"]),
        "parity_assertions": {
            "xml_equals_elements": xml_node_count == ec,
        },
    }


def _validate_schema(data: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        return False, "jsonschema package not installed; skipping schema validation."
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=data, schema=schema)
        return True, None
    except jsonschema.ValidationError as exc:
        return False, str(exc.message)


class CaptureFatalError(RuntimeError):
    """Fatal capture error; carries the capture_dir for partial-output reporting."""
    def __init__(self, message: str, capture_dir: Path | None = None) -> None:
        super().__init__(message)
        self.capture_dir: Path | None = capture_dir


# ─────────────────────────────────────────────────────────────────────────────
# Layer 4 – Renderers
# ─────────────────────────────────────────────────────────────────────────────

def _cell(v: Any) -> str:
    """Escape a value for use inside a Markdown table cell."""
    if v is None:
        return ""
    return str(v).replace("|", "\\|").replace("\n", " ")


def _render_markdown(data: dict[str, Any]) -> str:
    cap  = data["capture"]
    ctx  = data["context"]
    s    = data["summary"]
    diag = data["diagnostics"]
    orig = cap["origin"]

    lines: list[str] = [
        "# Android Screen Snapshot Report",
        "",
        "## Capture Metadata",
        "",
        f"- Capture ID: `{cap['capture_id']}`",
        f"- Timestamp (UTC): {cap['timestamp_utc']}",
        f"- Device Serial: {cap['device_serial']}",
        f"- Package: {ctx['package_name']}",
        f"- Activity: {ctx['activity_name']}",
        f"- Screen Width: {ctx.get('screen_width', 'n/a')}",
        f"- Screen Height: {ctx.get('screen_height', 'n/a')}",
        f"- Screen Density: {ctx.get('screen_density', 'n/a')}",
        f"- Screenshot: {cap['source']['screenshot_path']}",
        f"- UI Dump: {cap['source']['ui_dump_path']}",
        "",
        "## Provenance",
        "",
        f"- Parent Capture ID: {orig['parent_capture_id']}",
        f"- Interacted Element ID: {orig['interacted_element_id']}",
        f"- Action Type: {orig['action_type']}",
        "",
        "## Summary",
        "",
        f"- XML Node Count: {s['xml_node_count']}",
        f"- Element Count: {s['element_count']}",
        f"- Parity (xml_equals_elements): {s['parity_assertions']['xml_equals_elements']}",
        f"- Interaction Candidates: {s['interaction_candidate_count']}",
        f"- Clickable: {s['clickable_count']}",
        f"- Long Clickable: {s['long_clickable_count']}",
        f"- Focusable: {s['focusable_count']}",
        f"- Focused: {s['focused_count']}",
        f"- Enabled: {s['enabled_count']}",
        f"- Scrollable: {s['scrollable_count']}",
        f"- Selected: {s['selected_count']}",
        f"- Checked: {s['checked_count']}",
        f"- Checkable: {s['checkable_count']}",
        f"- Password: {s['password_count']}",
        "",
        "## Interaction Candidates",
        "",
        "| element_id | normalized_path | Actions | Reasons |",
        "|------------|-----------------|---------|---------|",
    ]
    for e in data["elements"]:
        if e["is_interaction_candidate"]:
            lines.append(
                f"| {e['element_id']} "
                f"| `{_cell(e['normalized_path'])}` "
                f"| {_cell(', '.join(e['action_types']))} "
                f"| {_cell('; '.join(e['candidacy_reasons']))} |"
            )

    lines += [
        "",
        "## Element Catalog",
        "",
        "| IDX | element_id | Ver | normalized_path | parent_path | D | Si"
        " | class_name | resource_id | package | view_type_hint"
        " | text | content_desc | hint | value | input_type"
        " | bounds_raw | L | T | R | B | W | H | CX | CY"
        " | C | LC | F | Fo | En | Sc | Se | Ch | Ck | Pw"
        " | Candidate | Actions | Reasons |",
        "|-----|------------|-----|-----------------|-------------|---|---"
        "|----|-------------|---------|---------------"
        "|------|--------------|------|-------|----------"
        "|------------|---|---|---|---|---|---|----|----|"
        "---|----|----|----|----|----|----|----|----|----"
        "|-----------|---------|---------|",
    ]
    for e in data["elements"]:
        b = e["bounds"]
        lines.append(
            f"| {e['xml_index_preorder']}"
            f" | {e['element_id']}"
            f" | {e['identity_version']}"
            f" | `{_cell(e['normalized_path'])}`"
            f" | {_cell(e['parent_path'])}"
            f" | {e['depth']}"
            f" | {e['sibling_index']}"
            f" | {_cell(e['class_name'])}"
            f" | {_cell(e['resource_id'])}"
            f" | {_cell(e['package'])}"
            f" | {_cell(e['view_type_hint'])}"
            f" | {_cell(e['text'])}"
            f" | {_cell(e['content_desc'])}"
            f" | {_cell(e['hint'])}"
            f" | {_cell(e['value'])}"
            f" | {_cell(e['input_type'])}"
            f" | {_cell(e['bounds_raw'])}"
            f" | {b['left']}"
            f" | {b['top']}"
            f" | {b['right']}"
            f" | {b['bottom']}"
            f" | {e['width']}"
            f" | {e['height']}"
            f" | {e['center_x']}"
            f" | {e['center_y']}"
            f" | {int(e['clickable'])}"
            f" | {int(e['long_clickable'])}"
            f" | {int(e['focusable'])}"
            f" | {int(e['focused'])}"
            f" | {int(e['enabled'])}"
            f" | {int(e['scrollable'])}"
            f" | {int(e['selected'])}"
            f" | {int(e['checked'])}"
            f" | {int(e['checkable'])}"
            f" | {int(e['password'])}"
            f" | {int(e['is_interaction_candidate'])}"
            f" | {_cell(', '.join(e['action_types']))}"
            f" | {_cell('; '.join(e['candidacy_reasons']))} |"
        )

    lines += [
        "",
        "## Diagnostics",
        "",
        "### ADB Command Log",
        "",
        "| Command | Exit | Started (UTC) | Finished (UTC) | Stdout | Stderr |",
        "|---------|------|---------------|----------------|--------|--------|",
    ]
    for log in diag["adb_command_log"]:
        lines.append(
            f"| {_cell(log['command'])}"
            f" | {log['exit_code']}"
            f" | {log['started_utc']}"
            f" | {log['finished_utc']}"
            f" | {_cell(log.get('stdout', ''))}"
            f" | {_cell(log.get('stderr', ''))} |"
        )

    lines += ["", "### Errors", ""]
    for err in diag.get("errors") or ["None"]:
        lines.append(f"- {err}")

    lines += ["", "### Warnings", ""]
    for w in diag["warnings"] or ["None"]:
        lines.append(f"- {w}")

    lines += ["", "### Limitations", ""]
    for lim in diag["limitations"]:
        lines.append(f"- {lim}")

    v = diag["validation"]
    lines += [
        "",
        "### Schema Validation",
        "",
        f"- Performed: {v['schema_validation_performed']}",
        f"- Passed: {v['schema_validation_passed']}",
        f"- Error: {v.get('schema_validation_error') or 'None'}",
    ]

    return "\n".join(lines) + "\n"


def _render_html(data: dict[str, Any], template_text: str) -> str:
    cap  = data["capture"]
    ctx  = data["context"]
    s    = data["summary"]
    diag = data["diagnostics"]
    orig = cap["origin"]

    def _e(v: Any) -> str:
        return html_module.escape(str(v) if v is not None else "")

    screen_width  = max(1, int(ctx.get("screen_width")  or 0))
    screen_height = max(1, int(ctx.get("screen_height") or 0))

    rows: list[str] = []
    overlay_boxes: list[str] = []

    for e in data["elements"]:
        b = e["bounds"]
        indent = f"padding-left:{e['depth'] * 14}px"
        rows.append(
            "<tr>"
            f"<td>{_e(e['xml_index_preorder'])}</td>"
            f"<td><code>{_e(e['element_id'])}</code></td>"
            f"<td>{_e(e['identity_version'])}</td>"
            f"<td style='{indent}'><code>{_e(e['normalized_path'])}</code></td>"
            f"<td><code>{_e(e['parent_path'] or '')}</code></td>"
            f"<td>{_e(e['depth'])}</td>"
            f"<td>{_e(e['sibling_index'])}</td>"
            f"<td>{_e(e['class_name'] or '')}</td>"
            f"<td>{_e(e['resource_id'] or '')}</td>"
            f"<td>{_e(e['package'] or '')}</td>"
            f"<td>{_e(e['view_type_hint'] or '')}</td>"
            f"<td>{_e(e['text'])}</td>"
            f"<td>{_e(e['content_desc'])}</td>"
            f"<td>{_e(e['hint'] or '')}</td>"
            f"<td>{_e(e['value'] or '')}</td>"
            f"<td>{_e(e['input_type'] or '')}</td>"
            f"<td>[{b['left']},{b['top']}][{b['right']},{b['bottom']}]</td>"
            f"<td>{_e(e['width'])}</td>"
            f"<td>{_e(e['height'])}</td>"
            f"<td>{_e(e['center_x'])}</td>"
            f"<td>{_e(e['center_y'])}</td>"
            f"<td>{'&#10003;' if e['clickable']      else ''}</td>"
            f"<td>{'&#10003;' if e['long_clickable']  else ''}</td>"
            f"<td>{'&#10003;' if e['focusable']       else ''}</td>"
            f"<td>{'&#10003;' if e['focused']         else ''}</td>"
            f"<td>{'&#10003;' if e['enabled']         else ''}</td>"
            f"<td>{'&#10003;' if e['scrollable']      else ''}</td>"
            f"<td>{'&#10003;' if e['selected']        else ''}</td>"
            f"<td>{'&#10003;' if e['checked']         else ''}</td>"
            f"<td>{'&#10003;' if e['checkable']       else ''}</td>"
            f"<td>{'&#10003;' if e['password']        else ''}</td>"
            f"<td>{'<span class=\"tag-action\">&#10003;</span>' if e['is_interaction_candidate'] else ''}</td>"
            f"<td>{_e(', '.join(e['action_types']))}</td>"
            f"<td title='{_e(chr(10).join(e['candidacy_reasons']))}'>"
            f"{'&hellip;' if e['candidacy_reasons'] else ''}</td>"
            f"<td><details><summary>attrs</summary>"
            f"<pre>{_e(json.dumps(e['source_attributes'], indent=2))}</pre></details></td>"
            f"<td><details><summary>extra</summary>"
            f"<pre>{_e(json.dumps(e['source_attributes_extra'], indent=2))}</pre></details></td>"
            "</tr>"
        )

        if screen_width > 0 and screen_height > 0 and e["is_interaction_candidate"]:
            left_pct = max(0.0, min(100.0, b["left"]    / screen_width  * 100))
            top_pct  = max(0.0, min(100.0, b["top"]     / screen_height * 100))
            w_pct    = max(0.0, min(100.0, e["width"]   / screen_width  * 100))
            h_pct    = max(0.0, min(100.0, e["height"]  / screen_height * 100))
            label    = _e(e.get("text") or e.get("content_desc") or e.get("resource_id") or e["element_id"])
            overlay_boxes.append(
                f'<div class="overlay-box" '
                f'title="{label} | {_e(e["class_name"] or "")}" '
                f'style="left:{left_pct:.3f}%;top:{top_pct:.3f}%;'
                f'width:{w_pct:.3f}%;height:{h_pct:.3f}%;"></div>'
            )

    adb_rows: list[str] = []
    for log in diag["adb_command_log"]:
        adb_rows.append(
            "<tr>"
            f"<td><code>{_e(log['command'])}</code></td>"
            f"<td>{_e(log['exit_code'])}</td>"
            f"<td>{_e(log['started_utc'])}</td>"
            f"<td>{_e(log['finished_utc'])}</td>"
            f"<td>{_e(log.get('stdout', ''))}</td>"
            f"<td>{_e(log.get('stderr', ''))}</td>"
            "</tr>"
        )

    v = diag["validation"]
    screenshot_file = Path(cap["source"]["screenshot_path"]).name

    replacements: dict[str, str] = {
        "{{capture_id}}":                _e(cap["capture_id"]),
        "{{timestamp_utc}}":             _e(cap["timestamp_utc"]),
        "{{device_serial}}":             _e(cap["device_serial"]),
        "{{package_name}}":              _e(ctx["package_name"]),
        "{{activity_name}}":             _e(ctx["activity_name"]),
        "{{screen_width}}":              _e(ctx.get("screen_width", "")),
        "{{screen_height}}":             _e(ctx.get("screen_height", "")),
        "{{screen_density}}":            _e(ctx.get("screen_density", "")),
        "{{parent_capture_id}}":         _e(orig["parent_capture_id"] or "null"),
        "{{interacted_element_id}}":     _e(orig["interacted_element_id"] or "null"),
        "{{action_type}}":               _e(orig["action_type"] or "null"),
        "{{xml_node_count}}":            _e(s["xml_node_count"]),
        "{{element_count}}":             _e(s["element_count"]),
        "{{parity_ok}}":                 "&#10003;" if s["parity_assertions"]["xml_equals_elements"] else "&#10007;",
        "{{interaction_candidate_count}}": _e(s["interaction_candidate_count"]),
        "{{clickable_count}}":           _e(s["clickable_count"]),
        "{{long_clickable_count}}":      _e(s["long_clickable_count"]),
        "{{focusable_count}}":           _e(s["focusable_count"]),
        "{{focused_count}}":             _e(s["focused_count"]),
        "{{enabled_count}}":             _e(s["enabled_count"]),
        "{{scrollable_count}}":          _e(s["scrollable_count"]),
        "{{selected_count}}":            _e(s["selected_count"]),
        "{{checked_count}}":             _e(s["checked_count"]),
        "{{checkable_count}}":           _e(s["checkable_count"]),
        "{{password_count}}":            _e(s["password_count"]),
        "{{element_rows}}":              "\n".join(rows),
        "{{screenshot_file}}":           _e(screenshot_file),
        "{{overlay_boxes}}":             "\n".join(overlay_boxes),
        "{{adb_log_rows}}":              "\n".join(adb_rows),
        "{{errors}}":                    _e("; ".join(diag.get("errors") or []) or "None"),
        "{{warnings}}":                  _e("; ".join(diag["warnings"]) or "None"),
        "{{limitations}}":               _e("; ".join(diag["limitations"]) or "None"),
        "{{validation_performed}}":      _e(v["schema_validation_performed"]),
        "{{validation_passed}}":         _e(v["schema_validation_passed"]),
        "{{validation_error}}":          _e(v.get("schema_validation_error") or "None"),
    }

    rendered = template_text
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def generate_report(
    serial: str | None = None,
    output_dir: Path | None = None,
    adb_root_mode: str = "auto",
    *,
    parent_capture_id: str | None = None,
    interacted_element_id: str | None = None,
    action_type: str | None = None,
) -> Path:
    """Run a full current-screen capture and write JSON, Markdown, and HTML artifacts.

    The three keyword-only origin params are set by v2 exploration to record provenance.
    All default to None (v1 root-capture behaviour is unchanged).

    Raises CaptureFatalError (with capture_dir attached) when the capture
    completes but has accumulated errors (parity failure, schema validation failure,
    or file write failure).  Raises RuntimeError immediately for early transport
    failures (adb not found, no device, multiple devices).
    """
    command_log: list[dict[str, Any]] = []
    warnings:    list[str] = []
    errors:      list[str] = []

    resolved_serial = _resolve_serial(command_log, serial)  # raises immediately on failure
    _ensure_adb_root(resolved_serial, command_log, warnings, adb_root_mode)

    timestamp  = _now_utc()
    capture_id = _make_capture_id(timestamp, resolved_serial)
    target_root = output_dir or OUTPUT_ROOT
    capture_dir = target_root / capture_id

    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(f"Failed to create capture directory: {exc}") from exc

    ui_dump_path    = _capture_ui_dump(resolved_serial, capture_dir, command_log)
    screenshot_path = _capture_screenshot(resolved_serial, capture_dir, command_log)

    package_name, activity_name = _get_package_activity(resolved_serial, command_log, warnings)
    screen_width, screen_height  = _get_screen_size(resolved_serial, command_log, warnings)
    screen_density               = _get_screen_density(resolved_serial, command_log, warnings)

    elements, xml_node_count = _extract_elements(ui_dump_path)  # raises on XML parse failure

    if xml_node_count == 0:
        warnings.append("UIAutomator dump produced zero node elements.")

    if (screen_width == 0 or screen_height == 0) and elements:
        screen_width  = max(e["bounds"]["right"]  for e in elements)
        screen_height = max(e["bounds"]["bottom"] for e in elements)

    summary = _build_summary(elements, xml_node_count)

    if not summary["parity_assertions"]["xml_equals_elements"]:
        errors.append(
            f"Parity failure: xml_node_count={xml_node_count} != element_count={len(elements)}"
        )

    model: dict[str, Any] = {
        "capture": {
            "capture_id":    capture_id,
            "timestamp_utc": timestamp.isoformat(),
            "device_serial": resolved_serial,
            "source": {
                "ui_dump_path":    str(ui_dump_path.relative_to(ROOT)),
                "screenshot_path": str(screenshot_path.relative_to(ROOT)),
            },
            "origin": {
                "parent_capture_id":     parent_capture_id,
                "interacted_element_id": interacted_element_id,
                "action_type":           action_type,
            },
        },
        "context": {
            "package_name":   package_name,
            "activity_name":  activity_name,
            "screen_width":   screen_width,
            "screen_height":  screen_height,
            "screen_density": screen_density,
        },
        "summary":  summary,
        "elements": elements,
        "diagnostics": {
            "adb_command_log": command_log,
            "warnings":    warnings,
            "limitations": [
                "v1 captures only the current visible screen.",
                "UIAutomator dump may omit privileged or secure overlays.",
                "No recursive navigation is performed in this version.",
                f"Swipe candidacy threshold is {SWIPE_AREA_THRESHOLD} px\u00b2 "
                "(not specified in v1 contract; see SWIPE_AREA_THRESHOLD constant).",
            ],
            "errors": errors,
            "validation": {
                "schema_validation_performed": False,
                "schema_validation_passed":    False,
                "schema_validation_error":     None,
            },
        },
    }

    # Schema validation (after model is fully assembled)
    schema_ok, schema_err = _validate_schema(model)
    model["diagnostics"]["validation"]["schema_validation_performed"] = True
    model["diagnostics"]["validation"]["schema_validation_passed"]    = schema_ok
    model["diagnostics"]["validation"]["schema_validation_error"]     = schema_err
    if not schema_ok and schema_err and "not installed" not in schema_err:
        errors.append(f"Schema validation failed: {schema_err}")
        model["diagnostics"]["errors"] = errors

    json_path     = capture_dir / "screen-snapshot.json"
    markdown_path = capture_dir / "report.md"
    html_path     = capture_dir / "report.html"

    try:
        json_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
        markdown_path.write_text(_render_markdown(model), encoding="utf-8")
        html_template = (TEMPLATES_DIR / "report-template.html").read_text(encoding="utf-8")
        html_path.write_text(_render_html(model, html_template), encoding="utf-8")
    except OSError as exc:
        errors.append(f"File write failure: {exc}")
        raise CaptureFatalError(f"Failed to write output files: {exc}", capture_dir) from exc

    if errors:
        raise CaptureFatalError(
            f"Capture completed with errors: {'; '.join(errors)}", capture_dir
        )

    return capture_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Android current screen and build JSON/Markdown/HTML reports."
    )
    parser.add_argument("--serial", help="Target Android device serial (required if multiple devices).")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_ROOT,
        help="Output root directory (default: output/captures).",
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        capture_dir = generate_report(
            serial=args.serial,
            output_dir=args.output_dir,
            adb_root_mode=args.adb_root,
        )
        print(f"Capture generated: {capture_dir}")
        print(f"  JSON:     {capture_dir / 'screen-snapshot.json'}")
        print(f"  Markdown: {capture_dir / 'report.md'}")
        print(f"  HTML:     {capture_dir / 'report.html'}")
        return 0
    except CaptureFatalError as exc:
        if exc.capture_dir:
            print(f"Partial artifacts at: {exc.capture_dir}", file=sys.stderr)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
