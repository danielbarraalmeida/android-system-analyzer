"""OpenAI tool/function schemas matching ``tools.TOOL_REGISTRY``.

These declarations are what the LLM actually sees. Keep descriptions
focused — they steer behaviour as much as the system prompt does.
"""

from __future__ import annotations

from typing import Any

from .tools import DUMPSYS_SECTIONS

_PACKAGE_FILTERS = ["third_party", "system", "all", "disabled", "enabled"]
_SETTINGS_NAMESPACES = ["system", "secure", "global"]
_NOTE_CATEGORIES = [
    "hardware", "software", "build", "audio", "display", "network",
    "wifi", "bluetooth", "sensors", "battery", "storage", "memory",
    "cpu", "processes", "services", "packages", "permissions",
    "settings", "users", "security", "automotive", "other",
]


def _fn(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name":        name,
            "description": description,
            "parameters":  params,
        },
    }


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type":       "object",
        "properties": props,
        "required":   required or [],
        "additionalProperties": False,
    }


def schemas_by_name(schemas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["function"]["name"]: s for s in schemas}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _fn(
        "get_device_properties",
        "Read every Android system property via `getprop`. Use this once "
        "early to identify the device (manufacturer, model, build, SDK).",
        _obj({}),
    ),
    _fn(
        "list_packages",
        "List installed packages. Use `third_party` first to see user-installed "
        "apps, then `system` to enumerate the OS. Returns up to 60 packages plus "
        "a count; the full list is persisted on disk.",
        _obj({
            "filter": {
                "type": "string",
                "enum": _PACKAGE_FILTERS,
                "description": "Which packages to enumerate (default `third_party`).",
            },
        }),
    ),
    _fn(
        "inspect_package",
        "Dump detailed info for a single package (version, permissions, activities). "
        "Use this for OEM / vehicle-specific packages you want to characterise.",
        _obj({
            "package": {"type": "string",
                        "description": "Fully-qualified package name."},
            "compact": {"type": "boolean",
                        "description": "If false, include full permission and activity lists in response (default true)."},
        }, required=["package"]),
    ),
    _fn(
        "list_services",
        "Enumerate registered system services via `service list`.",
        _obj({}),
    ),
    _fn(
        "dumpsys",
        "Run `dumpsys <section>` for a curated allowlist of sections.",
        _obj({
            "section": {
                "type": "string",
                "enum": list(DUMPSYS_SECTIONS),
                "description": "Which dumpsys section to read.",
            },
        }, required=["section"]),
    ),
    _fn(
        "read_settings",
        "Read all keys in `settings <namespace>` (system / secure / global).",
        _obj({
            "namespace": {
                "type": "string",
                "enum": _SETTINGS_NAMESPACES,
            },
        }, required=["namespace"]),
    ),
    _fn(
        "list_processes",
        "List running processes via `ps -A`.",
        _obj({}),
    ),
    _fn(
        "read_file",
        "Read a file from the device via `cat`. Use for /proc, build.prop, etc.",
        _obj({
            "path":      {"type": "string",
                          "description": "Absolute device path (must start with /)."},
            "max_bytes": {"type": "integer",
                          "description": "Max bytes to return in the preview (default 65536)."},
        }, required=["path"]),
    ),
    _fn(
        "list_dir",
        "List the contents of a directory via `ls -la`.",
        _obj({
            "path": {"type": "string",
                     "description": "Absolute device path."},
        }, required=["path"]),
    ),
    _fn(
        "run_shell",
        "Execute an arbitrary `adb shell` command. By default ONLY commands "
        "matching a built-in allowlist are accepted (getprop, pm, dumpsys, "
        "service list, settings, ps, ls, cat /proc..., cmd, etc.).",
        _obj({
            "command": {"type": "string",
                        "description": "Shell command to execute on the device."},
        }, required=["command"]),
    ),
    _fn(
        "capture_home_screen",
        "Press HOME, wait for the UI to settle, capture a single UI snapshot "
        "and screenshot. Use AT MOST ONCE per session — it's only for visual "
        "confirmation of the launcher, not exploration.",
        _obj({}),
    ),
    _fn(
        "find_property",
        "Grep `getprop` for keys or values matching a regex. Auto-fetches "
        "getprop on first call. Case-insensitive. Prefer this over running "
        "`getprop` again when you only want a subset (e.g. pattern='audio' "
        "or 'ro\\.boot\\.').",
        _obj({
            "pattern":       {"type": "string",
                              "description": "Python regex (matches key OR value)."},
            "value_pattern": {"type": "string",
                              "description": "Optional extra regex matched against the value only."},
            "max_matches":   {"type": "integer",
                              "description": "Cap on returned matches (default 50)."},
        }, required=["pattern"]),
    ),
    _fn(
        "find_package",
        "Grep installed package names (and APK paths) for a regex. "
        "Auto-fetches the package list on first call. Use this to find "
        "OEM / vendor / automotive packages without dumping the whole list.",
        _obj({
            "pattern":     {"type": "string",
                            "description": "Python regex (matches package name or APK path)."},
            "filter":      {"type": "string", "enum": _PACKAGE_FILTERS,
                            "description": "Scope: third_party / system / all (default all)."},
            "max_matches": {"type": "integer"},
        }, required=["pattern"]),
    ),
    _fn(
        "find_service",
        "Grep registered binder services (and interface descriptors) for a "
        "regex. Auto-fetches the service list on first call.",
        _obj({
            "pattern":     {"type": "string"},
            "max_matches": {"type": "integer"},
        }, required=["pattern"]),
    ),
    _fn(
        "find_setting",
        "Grep settings keys/values across system / secure / global "
        "namespaces. Auto-fetches each namespace on first access.",
        _obj({
            "pattern":     {"type": "string"},
            "namespaces":  {"type": "array",
                            "items": {"type": "string", "enum": _SETTINGS_NAMESPACES},
                            "description": "Subset of namespaces to search; default all three."},
            "max_matches": {"type": "integer"},
        }, required=["pattern"]),
    ),
    _fn(
        "grep_dumpsys",
        "Run (or reuse cached) `dumpsys <section>` and return only lines "
        "matching a regex, with surrounding context. Strongly preferred "
        "over `dumpsys` when you have a specific sub-topic to investigate.",
        _obj({
            "section":     {"type": "string", "enum": list(DUMPSYS_SECTIONS)},
            "pattern":     {"type": "string"},
            "context":     {"type": "integer",
                            "description": "Lines of context around each match (default 2)."},
            "max_matches": {"type": "integer"},
        }, required=["section", "pattern"]),
    ),
    _fn(
        "grep_logcat",
        "Dump recent logcat (`adb shell logcat -d`) and return matching "
        "lines. Use `since` like '15m' or '2h' to bound time, otherwise "
        "the last `max_lines` lines are scanned.",
        _obj({
            "pattern":     {"type": "string"},
            "since":       {"type": "string",
                            "description": "logcat -t time spec (e.g. '15m', '2h') or timestamp."},
            "max_lines":   {"type": "integer",
                            "description": "Last-N lines when `since` is omitted (default 5000)."},
            "context":     {"type": "integer"},
            "max_matches": {"type": "integer"},
        }, required=["pattern"]),
    ),
    _fn(
        "grep_file",
        "Read a device file via `cat` and return regex-matching lines. "
        "Cheaper than `read_file` when you only need a subset.",
        _obj({
            "path":        {"type": "string",
                            "description": "Absolute device path (must start with /)."},
            "pattern":     {"type": "string"},
            "context":     {"type": "integer"},
            "max_matches": {"type": "integer"},
        }, required=["path", "pattern"]),
    ),
    _fn(
        "search_facts",
        "Search the facts you have already recorded in THIS session via "
        "`note`. Use this before adding a new note to avoid duplicates.",
        _obj({
            "pattern":     {"type": "string"},
            "max_matches": {"type": "integer"},
        }, required=["pattern"]),
    ),
    _fn(
        "note",
        "Record a structured fact for the knowledge store. Use this to "
        "summarise non-obvious observations (e.g. 'this device runs Android "
        "Automotive 13 on Qualcomm SA8155').",
        _obj({
            "category": {"type": "string", "enum": _NOTE_CATEGORIES},
            "key":      {"type": "string"},
            "value":    {"type": "string"},
        }, required=["category", "key", "value"]),
    ),
    _fn(
        "finish",
        "Signal that the session is complete and provide a 1-3 paragraph summary.",
        _obj({
            "summary": {"type": "string",
                        "description": "Markdown summary of what was discovered."},
        }, required=["summary"]),
    ),
]


SCHEMAS_BY_NAME: dict[str, dict] = schemas_by_name(TOOL_SCHEMAS)
