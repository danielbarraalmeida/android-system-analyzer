"""Shared pytest fixtures for the Android System Analyzer test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Make `scripts/` importable as flat modules (current_screen_report, diff_captures).
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def sample_xml_path() -> Path:
    return FIXTURES / "sample_window_dump.xml"


@pytest.fixture(scope="session")
def schema_path() -> Path:
    return ROOT / "templates" / "screen-snapshot.schema.json"
