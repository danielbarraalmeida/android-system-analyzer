#!/usr/bin/env python3
"""v2 session persistence helpers.

All writes are atomic: JSON is serialised to a .tmp file beside the target,
then renamed. This prevents corrupt state on keyboard-interrupt or crash.

Public API
----------
save_registry(session_dir, states, transitions, attempts)
save_session_manifest(session_dir, manifest)
save_system_context(session_dir, ctx)
load_registry(session_dir) -> dict | None
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, obj: Any) -> None:
    """Serialise obj as pretty JSON and rename into place atomically."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    # os.replace is atomic on POSIX; on Windows it is as atomic as the OS allows.
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Registry: three parallel lists written together
# ---------------------------------------------------------------------------

_REGISTRY_FILENAME = "registries"


def _registry_dir(session_dir: Path) -> Path:
    d = session_dir / _REGISTRY_FILENAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_registry(
    session_dir: Path,
    states: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> None:
    """Persist the three registry lists as separate JSON files."""
    rdir = _registry_dir(session_dir)
    _atomic_write(rdir / "states.json", states)
    _atomic_write(rdir / "transitions.json", transitions)
    _atomic_write(rdir / "attempts.json", attempts)


def load_registry(session_dir: Path) -> dict[str, list] | None:
    """Load previously saved registry. Returns None if not found."""
    rdir = session_dir / _REGISTRY_FILENAME
    if not rdir.exists():
        return None
    try:
        states      = json.loads((rdir / "states.json").read_text(encoding="utf-8"))
        transitions = json.loads((rdir / "transitions.json").read_text(encoding="utf-8"))
        attempts    = json.loads((rdir / "attempts.json").read_text(encoding="utf-8"))
        return {"states": states, "transitions": transitions, "attempts": attempts}
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Session manifest
# ---------------------------------------------------------------------------

def save_session_manifest(session_dir: Path, manifest: dict[str, Any]) -> None:
    _atomic_write(session_dir / "session-manifest.json", manifest)


# ---------------------------------------------------------------------------
# System context
# ---------------------------------------------------------------------------

def save_system_context(session_dir: Path, ctx: dict[str, Any]) -> None:
    _atomic_write(session_dir / "system-context.json", ctx)
