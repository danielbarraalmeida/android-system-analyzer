"""Agent tool layer — system inspection (root-privileged ADB).

The agent's primary mission is to map the Android *system*: hardware,
properties, packages, services, settings, processes, etc. Each tool
wraps a small set of ``adb shell`` commands, returns a JSON-serialisable
dict (the LLM "observation"), and persists the raw output to disk.

Design notes
------------
- All tools assume root has been (or is being) attempted by the runner.
- Long outputs are persisted to ``session_dir/raw/`` and only a compact
  summary is returned to the model — keeps the context window small.
- Every ADB call is recorded in ``session.command_log`` for provenance.
- A small ``run_shell`` tool can execute arbitrary commands but is
  ALLOWLIST-gated by default (security boundary).
- One ``capture_home_screen`` tool produces a single UI snapshot for
  visual context. No tap navigation.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ._adb import (
    _capture_screenshot,
    _capture_ui_dump,
    _ensure_adb_root,
    _get_package_activity,
    _get_screen_density,
    _get_screen_size,
    _resolve_serial,
    extract_ui_signature,
)
from ._navigation import navigate_to_home


# ---------------------------------------------------------------------------
# Session container
# ---------------------------------------------------------------------------

@dataclass
class AgentSession:
    """Mutable per-run state shared by every tool invocation."""

    serial:       str
    session_dir:  Path
    settle_ms:    int = 1500

    command_log:  list[dict[str, Any]] = field(default_factory=list)
    warnings:     list[str]            = field(default_factory=list)

    # Set by the CLI/runner. If False, ``run_shell`` only accepts allowlisted
    # commands. The model never sees this flag — it just sees rejections.
    allow_arbitrary_shell: bool = False

    # Optional live-progress logger. Tools call this to emit "  • running…"
    log: Callable[[str], None] | None = None

    # Indexer-facing buffers populated by tools as they run.
    properties:        dict[str, str]            = field(default_factory=dict)
    packages:          dict[str, dict[str, Any]] = field(default_factory=dict)
    services:          dict[str, str]            = field(default_factory=dict)
    settings_buckets:  dict[str, dict[str, str]] = field(default_factory=dict)
    dumpsys_excerpts:  list[dict[str, Any]]      = field(default_factory=list)
    facts:             list[dict[str, Any]]      = field(default_factory=list)
    screen_snapshots:  list[dict[str, Any]]      = field(default_factory=list)

    # Used by runner to record device identity once at session start.
    device_identity:   dict[str, Any]            = field(default_factory=dict)

    # Raw text caches for the grep_/find_ family of tools. Populated
    # lazily on first access so the LLM can re-query without re-shelling.
    cache_dumpsys:  dict[str, str] = field(default_factory=dict)
    cache_logcat:   str            = ""

    # ------------------------------------------------------------------
    # Filesystem layout
    # ------------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.session_dir / "raw"

    @property
    def screens_dir(self) -> Path:
        return self.session_dir / "screens"

    @property
    def screenshots_dir(self) -> Path:
        return self.session_dir / "screenshots"

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.screens_dir.mkdir(parents=True, exist_ok=True)
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Allowlist for run_shell
# ---------------------------------------------------------------------------

# Each entry is a regex anchored at start. Commands MUST match one to be
# allowed when ``allow_arbitrary_shell`` is False.
_SHELL_ALLOWLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"^getprop( |$)",
        r"^pm list ",
        r"^pm dump ",
        r"^pm path ",
        r"^dumpsys [a-zA-Z0-9_.\-]+( |$)",
        r"^service list$",
        r"^settings (get|list) (system|secure|global)( |$)",
        r"^ps( |$)",
        r"^ip addr( show)?$",
        r"^cat /proc/[a-zA-Z0-9_./\-]+$",
        r"^cat /system/build\.prop$",
        r"^ls( -[laRh]+)?(  *[a-zA-Z0-9_./\-]+)*$",
        r"^uname",
        r"^id$",
        r"^uptime$",
        r"^whoami$",
        r"^df( |$)",
        r"^mount$",
        r"^date( |$)",
        r"^am stack list$",
        r"^cmd [a-zA-Z0-9_]+ ",
        r"^logcat -d( |$)",
    )
)


def _shell_command_allowed(cmd: str) -> bool:
    cmd = cmd.strip()
    return any(p.match(cmd) for p in _SHELL_ALLOWLIST)


# ---------------------------------------------------------------------------
# Internal ADB shell helpers
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _run(
    session: AgentSession,
    args: list[str],
    *,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run an adb command with full logging. Never raises on non-zero exit."""
    cmd = ["adb", "-s", session.serial, *args]
    started = _now_utc()
    try:
        proc = subprocess.run(
            cmd, text=True, capture_output=True,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        proc = subprocess.CompletedProcess(
            args=cmd, returncode=124,
            stdout=exc.stdout or "", stderr=f"timeout after {timeout}s",
        )
    except FileNotFoundError:
        proc = subprocess.CompletedProcess(
            args=cmd, returncode=127, stdout="", stderr="adb not found",
        )
    finished = _now_utc()
    session.command_log.append({
        "command":      " ".join(cmd),
        "exit_code":    proc.returncode,
        "stdout":       (proc.stdout or "")[:4000],
        "stderr":       (proc.stderr or "")[:1000],
        "started_utc":  started,
        "finished_utc": finished,
    })
    return proc


def _shell(
    session: AgentSession,
    shell_cmd: str,
    *,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    """Run ``adb shell <shell_cmd>``. Returns (exit_code, stdout, stderr)."""
    proc = _run(session, ["shell", shell_cmd], timeout=timeout)
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _persist_raw(
    session: AgentSession,
    tool_name: str,
    key: str,
    content: str,
) -> str:
    """Write raw tool output to ``raw/<tool>__<key>.txt`` and return rel path."""
    session.ensure_dirs()
    safe_key = re.sub(r"[^a-zA-Z0-9_.\-]+", "_", key)[:80] or "out"
    fn = f"{tool_name}__{safe_key}.txt"
    path = session.raw_dir / fn
    path.write_text(content, encoding="utf-8", errors="replace")
    return path.relative_to(session.session_dir).as_posix()


def _truncate(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…(truncated)"


# ---------------------------------------------------------------------------
# Host-side grep helper — used by all find_/grep_ tools.
# ---------------------------------------------------------------------------

def _compile_regex(pattern: str, case_sensitive: bool) -> tuple[Any, str | None]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern, flags), None
    except re.error as exc:
        return None, f"invalid regex {pattern!r}: {exc}"


def _grep_text(
    text: str,
    pattern: str,
    *,
    context: int = 2,
    max_matches: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Return regex matches with line numbers + surrounding context."""
    rx, err = _compile_regex(pattern, case_sensitive)
    if err:
        return {"error": err}
    lines = text.splitlines()
    hits = [i for i, ln in enumerate(lines) if rx.search(ln)]
    matches: list[dict[str, Any]] = []
    for i in hits[:max_matches]:
        start = max(0, i - context)
        end   = min(len(lines), i + context + 1)
        matches.append({
            "line_no": i + 1,
            "line":    lines[i].rstrip(),
            "context": [lines[j].rstrip() for j in range(start, end)],
        })
    return {
        "pattern":       pattern,
        "total_matches": len(hits),
        "returned":      len(matches),
        "matches":       matches,
    }


def _grep_mapping(
    items: dict[str, str],
    pattern: str,
    *,
    value_pattern: str | None = None,
    max_matches: int = 50,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Grep a key→value mapping; match against key OR value by default."""
    rx_k, err = _compile_regex(pattern, case_sensitive)
    if err:
        return {"error": err}
    rx_v = None
    if value_pattern:
        rx_v, err = _compile_regex(value_pattern, case_sensitive)
        if err:
            return {"error": err}
    hits: list[dict[str, str]] = []
    for k, v in items.items():
        if not rx_k.search(k) and not rx_k.search(str(v)):
            continue
        if rx_v is not None and not rx_v.search(str(v)):
            continue
        hits.append({"key": k, "value": str(v)})
    total = len(hits)
    return {
        "pattern":       pattern,
        "value_pattern": value_pattern,
        "total_matches": total,
        "returned":      min(total, max_matches),
        "matches":       hits[:max_matches],
    }


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_GETPROP_LINE = re.compile(r"^\[([^\]]+)\]:\s*\[(.*)\]\s*$")


def _parse_getprop(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _GETPROP_LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


_PM_LIST_PKG = re.compile(r"^package:(?:(.+?\.apk)=)?([a-zA-Z0-9_.]+)\s*$")


def _parse_pm_list(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _PM_LIST_PKG.match(line)
        if not m:
            continue
        apk, pkg = m.group(1), m.group(2)
        rows.append({"package": pkg, "apk_path": apk})
    return rows


def _parse_service_list(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    # Format: "  N\t<service>: [<iface>]"
    pat = re.compile(r"^\s*\d+\t([^:]+):\s*\[([^\]]*)\]\s*$")
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            rows.append({"service": m.group(1).strip(),
                         "interface": m.group(2).strip()})
    return rows


def _parse_settings(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _parse_ps(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    if not lines:
        return rows
    # Header e.g. "USER  PID PPID VSIZE RSS WCHAN PC NAME"
    header = lines[0].split()
    for line in lines[1:]:
        parts = line.split(None, len(header) - 1)
        if len(parts) == len(header):
            rows.append(dict(zip(header, parts)))
    return rows


# ---------------------------------------------------------------------------
# Public tool functions
#
# Each tool takes (session, **kwargs) and returns a JSON-serialisable dict.
# Tools also mutate session buffers (properties, packages, ...) so the
# indexer can persist everything at end of session.
# ---------------------------------------------------------------------------

def tool_get_device_properties(session: AgentSession) -> dict[str, Any]:
    """Read every key from ``getprop`` and return a flat dict."""
    _log = session.log or (lambda _m: None)
    _log("    • getprop…")
    code, out, err = _shell(session, "getprop")
    if code != 0:
        return {"error": f"getprop failed (exit {code}): {err}".strip()}
    props = _parse_getprop(out)
    session.properties.update(props)
    raw_path = _persist_raw(session, "getprop", "all", out)
    # Surface a curated subset to the LLM (full set is on disk).
    HEADLINE_KEYS = (
        "ro.product.manufacturer", "ro.product.model", "ro.product.brand",
        "ro.product.device", "ro.product.name",
        "ro.build.version.release", "ro.build.version.sdk",
        "ro.build.version.incremental", "ro.build.fingerprint",
        "ro.build.type", "ro.build.tags",
        "ro.hardware", "ro.board.platform", "ro.boot.bootloader",
        "ro.serialno", "ro.secure", "ro.debuggable",
    )
    headline = {k: props[k] for k in HEADLINE_KEYS if k in props}
    return {
        "property_count": len(props),
        "headline":       headline,
        "raw_file":       raw_path,
    }


def tool_list_packages(
    session: AgentSession,
    *,
    filter: str = "third_party",
) -> dict[str, Any]:
    """List installed packages.

    ``filter`` ∈ {"third_party","system","all","disabled","enabled"}.
    Returns counts plus the first 60 packages (full list on disk).
    """
    flag_map = {
        "all":         "",
        "third_party": "-3",
        "system":      "-s",
        "disabled":    "-d",
        "enabled":     "-e",
    }
    flag = flag_map.get(filter, "-3")
    _log = session.log or (lambda _m: None)
    _log(f"    • pm list packages {flag} -f…")
    cmd = "pm list packages -f"
    if flag:
        cmd += f" {flag}"
    code, out, err = _shell(session, cmd)
    if code != 0:
        return {"error": f"pm list failed (exit {code}): {err}".strip()}
    rows = _parse_pm_list(out)
    is_system_flag = (filter == "system")
    for r in rows:
        existing = session.packages.get(r["package"], {})
        existing.update({
            "apk_path":  r.get("apk_path"),
            "is_system": is_system_flag or existing.get("is_system", False),
            "last_seen_utc": _now_utc(),
        })
        session.packages[r["package"]] = existing
    raw_path = _persist_raw(session, "pm_list", filter, out)
    preview = [r["package"] for r in rows[:60]]
    return {
        "filter":        filter,
        "package_count": len(rows),
        "preview":       preview,
        "raw_file":      raw_path,
    }


_PKG_VERSION = re.compile(r"versionName=(\S+)")
_PKG_VERSION_CODE = re.compile(r"versionCode=(\d+)")
_PKG_REQUESTED_PERM = re.compile(r"^\s+([a-zA-Z0-9_.]+\.permission\.[A-Z_0-9]+)\s*$")
_PKG_ACTIVITY = re.compile(r"^\s+[0-9a-f]+\s+([a-zA-Z0-9_./$]+)\s+filter")


def tool_inspect_package(
    session: AgentSession,
    *,
    package: str,
    compact: bool = True,
) -> dict[str, Any]:
    """Run ``dumpsys package <pkg>`` and extract version + permissions + activities."""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_.]+$", package):
        return {"error": f"invalid package name: {package!r}"}
    _log = session.log or (lambda _m: None)
    _log(f"    • dumpsys package {package}…")
    code, out, err = _shell(session, f"dumpsys package {package}")
    if code != 0:
        return {"error": f"dumpsys package failed (exit {code}): {err}".strip()}
    if "Unable to find package" in out:
        return {"error": f"package {package!r} not installed"}

    raw_path = _persist_raw(session, "dumpsys_package", package, out)

    version_name = None
    version_code = None
    perms_requested: list[str] = []
    activities: list[str] = []

    m = _PKG_VERSION.search(out)
    if m:
        version_name = m.group(1)
    m = _PKG_VERSION_CODE.search(out)
    if m:
        version_code = int(m.group(1))

    in_perms = False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("requested permissions:"):
            in_perms = True
            continue
        if in_perms:
            if not line.startswith("    "):
                in_perms = False
            else:
                m = _PKG_REQUESTED_PERM.match(line)
                if m:
                    perms_requested.append(m.group(1))
        m = _PKG_ACTIVITY.match(line)
        if m:
            activities.append(m.group(1))

    activities = sorted(set(activities))
    perms_requested = sorted(set(perms_requested))

    # Update session buffer.
    entry = session.packages.setdefault(package, {})
    entry.update({
        "version_name":     version_name,
        "version_code":     version_code,
        "permissions":      perms_requested,
        "activity_count":   len(activities),
        "last_seen_utc":    _now_utc(),
    })

    out_dict: dict[str, Any] = {
        "package":          package,
        "version_name":     version_name,
        "version_code":     version_code,
        "permission_count": len(perms_requested),
        "activity_count":   len(activities),
        "raw_file":         raw_path,
    }
    if not compact:
        out_dict["permissions"] = perms_requested
        out_dict["activities"] = activities[:50]
    else:
        out_dict["permissions_preview"] = perms_requested[:15]
        out_dict["activities_preview"]  = activities[:10]
    return out_dict


def tool_list_services(session: AgentSession) -> dict[str, Any]:
    """Enumerate registered system services via ``service list``."""
    _log = session.log or (lambda _m: None)
    _log("    • service list…")
    code, out, err = _shell(session, "service list")
    if code != 0:
        return {"error": f"service list failed (exit {code}): {err}".strip()}
    rows = _parse_service_list(out)
    for r in rows:
        session.services[r["service"]] = r["interface"]
    raw_path = _persist_raw(session, "service_list", "all", out)
    return {
        "service_count": len(rows),
        "preview":       [r["service"] for r in rows[:80]],
        "raw_file":      raw_path,
    }


# Curated dumpsys sections — small enough for the model to choose from.
DUMPSYS_SECTIONS: tuple[str, ...] = (
    "activity", "activity_top", "alarm", "audio", "battery", "battery_stats",
    "bluetooth_manager", "connectivity", "cpuinfo", "deviceidle", "display",
    "dropbox", "input", "input_method", "jobscheduler", "location", "media.audio_flinger",
    "media.audio_policy", "meminfo", "netstats", "notification", "package",
    "permissionmgr", "power", "procstats", "sensorservice", "settings",
    "statusbar", "telephony.registry", "usagestats", "user", "wifi", "window",
)


def tool_dumpsys(
    session: AgentSession,
    *,
    section: str,
) -> dict[str, Any]:
    """Run ``dumpsys <section>``. Returns a compact summary."""
    if section not in DUMPSYS_SECTIONS:
        return {
            "error":            f"section {section!r} not in allowlist",
            "allowed_sections": list(DUMPSYS_SECTIONS),
        }
    _log = session.log or (lambda _m: None)
    _log(f"    • dumpsys {section}…")
    # activity_top is a convenience alias.
    cmd = "dumpsys activity activities" if section == "activity_top" else f"dumpsys {section}"
    code, out, err = _shell(session, cmd, timeout=90.0)
    if code != 0:
        return {"error": f"dumpsys failed (exit {code}): {err}".strip()}
    raw_path = _persist_raw(session, "dumpsys", section, out)
    session.cache_dumpsys[section] = out
    session.dumpsys_excerpts.append({
        "section":       section,
        "raw_file":      raw_path,
        "captured_utc":  _now_utc(),
        "first_lines":   "\n".join(out.splitlines()[:30]),
    })
    return {
        "section":  section,
        "lines":    len(out.splitlines()),
        "preview":  _truncate(out, 1500),
        "raw_file": raw_path,
    }


def tool_read_settings(
    session: AgentSession,
    *,
    namespace: str,
) -> dict[str, Any]:
    """Read all keys in a settings namespace (system/secure/global)."""
    if namespace not in ("system", "secure", "global"):
        return {"error": f"invalid namespace {namespace!r}"}
    _log = session.log or (lambda _m: None)
    _log(f"    • settings list {namespace}…")
    code, out, err = _shell(session, f"settings list {namespace}")
    if code != 0:
        return {"error": f"settings list failed (exit {code}): {err}".strip()}
    settings = _parse_settings(out)
    session.settings_buckets[namespace] = settings
    raw_path = _persist_raw(session, "settings", namespace, out)
    return {
        "namespace":  namespace,
        "key_count":  len(settings),
        "preview":    dict(list(settings.items())[:30]),
        "raw_file":   raw_path,
    }


def tool_list_processes(session: AgentSession) -> dict[str, Any]:
    """List running processes."""
    _log = session.log or (lambda _m: None)
    _log("    • ps -A…")
    code, out, err = _shell(session, "ps -A")
    if code != 0:
        # Some old shells need just `ps`
        code, out, err = _shell(session, "ps")
        if code != 0:
            return {"error": f"ps failed (exit {code}): {err}".strip()}
    rows = _parse_ps(out)
    raw_path = _persist_raw(session, "ps", "all", out)
    return {
        "process_count": len(rows),
        "preview":       rows[:30],
        "raw_file":      raw_path,
    }


def tool_read_file(
    session: AgentSession,
    *,
    path: str,
    max_bytes: int = 65536,
) -> dict[str, Any]:
    """``cat`` a file via shell. Allowed under root for diagnostics."""
    if not re.match(r"^/[a-zA-Z0-9_./\-]+$", path):
        return {"error": f"invalid path: {path!r}"}
    _log = session.log or (lambda _m: None)
    _log(f"    • cat {path}…")
    code, out, err = _shell(session, f"cat {shlex.quote(path)}", timeout=30.0)
    if code != 0:
        return {"error": f"cat failed (exit {code}): {err}".strip(), "path": path}
    truncated = out[:max_bytes]
    raw_path = _persist_raw(session, "cat", path.replace("/", "_"), out)
    return {
        "path":       path,
        "size_bytes": len(out),
        "truncated":  len(out) > max_bytes,
        "content":    _truncate(truncated, 2000),
        "raw_file":   raw_path,
    }


def tool_list_dir(
    session: AgentSession,
    *,
    path: str,
) -> dict[str, Any]:
    """List directory entries via ``ls -la``."""
    if not re.match(r"^/[a-zA-Z0-9_./\-]*$", path):
        return {"error": f"invalid path: {path!r}"}
    _log = session.log or (lambda _m: None)
    _log(f"    • ls -la {path}…")
    code, out, err = _shell(session, f"ls -la {shlex.quote(path)}", timeout=20.0)
    if code != 0:
        return {"error": f"ls failed (exit {code}): {err}".strip(), "path": path}
    entries = [ln for ln in out.splitlines() if ln.strip()]
    raw_path = _persist_raw(session, "ls", path.replace("/", "_") or "root", out)
    return {
        "path":        path,
        "entry_count": len(entries),
        "preview":     entries[:60],
        "raw_file":    raw_path,
    }


def tool_run_shell(
    session: AgentSession,
    *,
    command: str,
) -> dict[str, Any]:
    """Run an arbitrary ``adb shell`` command (allowlisted by default).

    When ``session.allow_arbitrary_shell`` is False, the command must match
    one of the ``_SHELL_ALLOWLIST`` patterns.
    """
    if not session.allow_arbitrary_shell and not _shell_command_allowed(command):
        return {
            "error": (
                f"command not on allowlist; pass --allow-arbitrary-shell "
                f"to enable arbitrary shell. Rejected: {command!r}"
            ),
        }
    _log = session.log or (lambda _m: None)
    _log(f"    • shell: {command[:60]}…")
    code, out, err = _shell(session, command, timeout=45.0)
    raw_path = _persist_raw(session, "shell", command[:40], out)
    return {
        "command":     command,
        "exit_code":   code,
        "stdout_preview": _truncate(out, 1500),
        "stderr_preview": _truncate(err, 400),
        "raw_file":    raw_path,
    }


def tool_capture_home_screen(session: AgentSession) -> dict[str, Any]:
    """Press HOME, settle, capture one UI snapshot + screenshot."""
    _log = session.log or (lambda _m: None)
    _log("    • pressing HOME + observing…")
    navigate_to_home(session.serial, session.command_log, session.warnings, session.settle_ms)

    session.ensure_dirs()
    scratch = session.session_dir / "_scratch" / uuid.uuid4().hex[:8]
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        ui_dump = _capture_ui_dump(session.serial, scratch, session.command_log)
        signature, element_count = extract_ui_signature(ui_dump)
        pkg, activity = _get_package_activity(session.serial, session.command_log, session.warnings)
        width, height = _get_screen_size(session.serial, session.command_log, session.warnings)
        density = _get_screen_density(session.serial, session.command_log, session.warnings)

        screenshot_rel: str | None = None
        try:
            raw = _capture_screenshot(session.serial, scratch, session.command_log)
            dest = session.screenshots_dir / "home.png"
            raw.replace(dest)
            screenshot_rel = dest.relative_to(session.session_dir).as_posix()
        except RuntimeError as exc:
            session.warnings.append(f"screenshot failed: {exc}")

        snap_obj = {
            "signature":     signature,
            "package":       pkg,
            "activity":      activity,
            "screen":        {"width": width, "height": height, "density": density},
            "element_count": element_count,
            "screenshot":    screenshot_rel,
            "captured_utc":  _now_utc(),
        }
        snap_path = session.screens_dir / f"home_{signature[:12]}.json"
        snap_path.write_text(json.dumps(snap_obj, indent=2), encoding="utf-8")
        session.screen_snapshots.append(snap_obj)

        return {
            "package":        pkg,
            "activity":       activity,
            "element_count":  element_count,
            "screenshot":     screenshot_rel,
            "snapshot_file":  snap_path.relative_to(session.session_dir).as_posix(),
        }
    finally:
        try:
            for p in sorted(scratch.rglob("*"), reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
            scratch.rmdir()
        except OSError:
            pass


_NOTE_CATEGORIES = (
    "hardware", "software", "build", "audio", "display", "network",
    "wifi", "bluetooth", "sensors", "battery", "storage", "memory",
    "cpu", "processes", "services", "packages", "permissions",
    "settings", "users", "security", "automotive", "other",
)


# ---------------------------------------------------------------------------
# Abstract-query tools: search cached data without re-running heavy shells.
# ---------------------------------------------------------------------------

def tool_find_property(
    session: AgentSession,
    *,
    pattern: str,
    value_pattern: str | None = None,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Grep ``getprop`` for keys or values matching a regex.

    Auto-fetches getprop if it has not been collected yet. Match is
    case-insensitive by default and runs against both key and value.
    """
    if not session.properties:
        bootstrap = tool_get_device_properties(session)
        if "error" in bootstrap:
            return bootstrap
    return _grep_mapping(
        session.properties, pattern,
        value_pattern=value_pattern, max_matches=max_matches,
    )


def tool_find_package(
    session: AgentSession,
    *,
    pattern: str,
    filter: str = "all",
    max_matches: int = 60,
) -> dict[str, Any]:
    """Grep installed package names.

    Auto-fetches the requested ``filter`` set if not yet enumerated.
    """
    # Bootstrap if we have nothing OR if a specific filter was requested
    # but we haven't run that filter yet.
    if not session.packages or filter == "third_party":
        if not any(True for _ in session.packages):
            tool_list_packages(session, filter=filter)
    if not session.packages:
        return {"error": "package list unavailable"}
    items = {pkg: meta.get("apk_path") or "" for pkg, meta in session.packages.items()}
    if filter == "system":
        items = {k: v for k, v in items.items()
                 if session.packages[k].get("is_system")}
    elif filter == "third_party":
        items = {k: v for k, v in items.items()
                 if not session.packages[k].get("is_system")}
    return _grep_mapping(items, pattern, max_matches=max_matches)


def tool_find_service(
    session: AgentSession,
    *,
    pattern: str,
    max_matches: int = 60,
) -> dict[str, Any]:
    """Grep registered binder services for a regex match."""
    if not session.services:
        tool_list_services(session)
    if not session.services:
        return {"error": "service list unavailable"}
    return _grep_mapping(session.services, pattern, max_matches=max_matches)


def tool_find_setting(
    session: AgentSession,
    *,
    pattern: str,
    namespaces: list[str] | None = None,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Grep settings keys/values across one or more namespaces."""
    wanted = namespaces or ["system", "secure", "global"]
    bad = [n for n in wanted if n not in ("system", "secure", "global")]
    if bad:
        return {"error": f"invalid namespace(s): {bad}"}
    for ns in wanted:
        if ns not in session.settings_buckets:
            tool_read_settings(session, namespace=ns)
    rx, err = _compile_regex(pattern, case_sensitive=False)
    if err:
        return {"error": err}
    hits: list[dict[str, str]] = []
    for ns in wanted:
        for k, v in session.settings_buckets.get(ns, {}).items():
            if rx.search(k) or rx.search(str(v)):
                hits.append({"namespace": ns, "key": k, "value": str(v)})
    total = len(hits)
    return {
        "pattern":       pattern,
        "namespaces":    wanted,
        "total_matches": total,
        "returned":      min(total, max_matches),
        "matches":       hits[:max_matches],
    }


def tool_grep_dumpsys(
    session: AgentSession,
    *,
    section: str,
    pattern: str,
    context: int = 2,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Run ``dumpsys <section>`` (cached) and return regex-matching lines.

    Use this instead of ``dumpsys`` when you only care about a sub-topic
    — e.g. ``grep_dumpsys("activity", "ResumedActivity")`` rather than
    pulling the entire dumpsys activity output.
    """
    if section not in DUMPSYS_SECTIONS:
        return {
            "error":            f"section {section!r} not in allowlist",
            "allowed_sections": list(DUMPSYS_SECTIONS),
        }
    if section not in session.cache_dumpsys:
        fetched = tool_dumpsys(session, section=section)
        if "error" in fetched:
            return fetched
    text = session.cache_dumpsys.get(section, "")
    result = _grep_text(text, pattern,
                        context=context, max_matches=max_matches)
    result["section"] = section
    return result


def tool_grep_logcat(
    session: AgentSession,
    *,
    pattern: str,
    since: str | None = None,
    max_lines: int = 5000,
    context: int = 0,
    max_matches: int = 50,
) -> dict[str, Any]:
    """Dump recent logcat and return matching lines.

    ``since`` accepts logcat's ``-t`` time spec, e.g. ``"15m"``, ``"2h"``,
    or an explicit timestamp. If omitted, the last ``max_lines`` lines
    are returned (via ``-T <n>``).
    """
    _log = session.log or (lambda _m: None)
    if since and not re.match(r"^[a-zA-Z0-9: .\-]+$", since):
        return {"error": f"invalid since value: {since!r}"}
    cmd_parts = ["logcat", "-d"]
    if since:
        cmd_parts.extend(["-t", since])
    else:
        cmd_parts.extend(["-T", str(int(max_lines))])
    cmd = " ".join(cmd_parts)
    _log(f"    • {cmd}…")
    code, out, err = _shell(session, cmd, timeout=30.0)
    if code != 0:
        return {"error": f"logcat failed (exit {code}): {err}".strip()}
    session.cache_logcat = out
    raw_path = _persist_raw(session, "logcat", since or f"T{max_lines}", out)
    result = _grep_text(out, pattern, context=context, max_matches=max_matches)
    result["raw_file"]   = raw_path
    result["line_count"] = len(out.splitlines())
    return result


def tool_grep_file(
    session: AgentSession,
    *,
    path: str,
    pattern: str,
    context: int = 0,
    max_matches: int = 50,
) -> dict[str, Any]:
    """``cat`` a device file and return regex-matching lines."""
    if not re.match(r"^/[a-zA-Z0-9_./\-]+$", path):
        return {"error": f"invalid path: {path!r}"}
    _log = session.log or (lambda _m: None)
    _log(f"    • grep {pattern!r} {path}…")
    code, out, err = _shell(session, f"cat {shlex.quote(path)}", timeout=30.0)
    if code != 0:
        return {"error": f"cat failed (exit {code}): {err}".strip(), "path": path}
    raw_path = _persist_raw(session, "grep_file", path.replace("/", "_"), out)
    result = _grep_text(out, pattern, context=context, max_matches=max_matches)
    result["path"]     = path
    result["raw_file"] = raw_path
    return result


def tool_search_facts(
    session: AgentSession,
    *,
    pattern: str,
    max_matches: int = 20,
) -> dict[str, Any]:
    """Grep facts already recorded in *this* session (via ``note``).

    Useful so the LLM can recall what it has already established and
    avoid duplicate ``note`` calls.
    """
    rx, err = _compile_regex(pattern, case_sensitive=False)
    if err:
        return {"error": err}
    hits = []
    for f in session.facts:
        blob = f"{f['category']} {f['key']} {f['value']}"
        if rx.search(blob):
            hits.append(f)
    return {
        "pattern":       pattern,
        "total_matches": len(hits),
        "returned":      min(len(hits), max_matches),
        "matches":       hits[:max_matches],
    }


def tool_note(
    session: AgentSession,
    *,
    category: str,
    key: str,
    value: str,
) -> dict[str, Any]:
    """Record a structured fact for the knowledge store."""
    if category not in _NOTE_CATEGORIES:
        return {
            "error":           f"unknown category {category!r}",
            "allowed":         list(_NOTE_CATEGORIES),
        }
    if not key or not value:
        return {"error": "key and value must be non-empty"}
    fact = {
        "category":     category,
        "key":          key[:120],
        "value":        value[:2000],
        "recorded_utc": _now_utc(),
    }
    session.facts.append(fact)
    return {"accepted": True, **fact}


def tool_finish(session: AgentSession, *, summary: str) -> dict[str, Any]:
    """Mark the session as complete. Runner stops on this."""
    return {"action": "finish", "summary_length": len(summary or "")}


# ---------------------------------------------------------------------------
# Registry — name → function for the runner's dispatcher.
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "get_device_properties": tool_get_device_properties,
    "list_packages":         tool_list_packages,
    "inspect_package":       tool_inspect_package,
    "list_services":         tool_list_services,
    "dumpsys":               tool_dumpsys,
    "read_settings":         tool_read_settings,
    "list_processes":        tool_list_processes,
    "read_file":             tool_read_file,
    "list_dir":              tool_list_dir,
    "run_shell":             tool_run_shell,
    "capture_home_screen":   tool_capture_home_screen,
    "find_property":         tool_find_property,
    "find_package":          tool_find_package,
    "find_service":          tool_find_service,
    "find_setting":          tool_find_setting,
    "grep_dumpsys":          tool_grep_dumpsys,
    "grep_logcat":           tool_grep_logcat,
    "grep_file":             tool_grep_file,
    "search_facts":          tool_search_facts,
    "note":                  tool_note,
    "finish":                tool_finish,
}


# ---------------------------------------------------------------------------
# Session bootstrap (used by the CLI)
# ---------------------------------------------------------------------------

def open_session(
    *,
    serial: str | None,
    output_root: Path,
    adb_root_mode: str = "required",
    allow_arbitrary_shell: bool = False,
    settle_ms: int = 1500,
) -> AgentSession:
    """Resolve serial, enable adb root, create session_dir, return session."""
    command_log: list[dict[str, Any]] = []
    warnings:    list[str]            = []
    resolved = _resolve_serial(command_log, serial)
    _ensure_adb_root(resolved, command_log, warnings, adb_root_mode)

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z"
    safe = resolved.replace(":", "_").replace(".", "_")
    session_dir = output_root / f"rag_{ts}_{safe}"
    session_dir.mkdir(parents=True, exist_ok=True)

    sess = AgentSession(
        serial=resolved,
        session_dir=session_dir,
        settle_ms=settle_ms,
        command_log=command_log,
        warnings=warnings,
        allow_arbitrary_shell=allow_arbitrary_shell,
    )
    sess.ensure_dirs()
    return sess
