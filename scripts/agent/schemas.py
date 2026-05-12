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
