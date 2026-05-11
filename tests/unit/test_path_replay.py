"""Unit tests for _build_path_to_state (v2 path replay helper)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from v2_explore import _build_path_to_state  # noqa: E402

pytestmark = pytest.mark.unit

HOME = "state_home"


def _transition(src: str, dst: str, x: int, y: int) -> dict:
    return {
        "transition_id":        f"t_{src}_{dst}",
        "source_state_id":      src,
        "source_element_id":    f"elem_{src}",
        "action_type":          "tap",
        "action_payload":       {"x": x, "y": y, "element_path": f"id/{src}"},
        "destination_state_id": dst,
        "outcome":              "success",
        "error":                None,
    }


# ─────────────────────────── happy paths ─────────────────────────────────────

def test_home_returns_empty_list() -> None:
    """Requesting the path to Home itself must return an empty list — no taps needed."""
    assert _build_path_to_state(HOME, HOME, []) == []


def test_single_hop() -> None:
    """State A reachable in one tap from Home: path must be one step with the correct coordinates."""
    transitions = [_transition(HOME, "state_a", x=100, y=200)]
    result = _build_path_to_state("state_a", HOME, transitions)
    assert result == [{"x": 100, "y": 200, "state_id": "state_a"}]


def test_two_hop_chain() -> None:
    """State B reachable via Home → A → B: path must list both steps in order."""
    transitions = [
        _transition(HOME, "state_a", x=100, y=200),
        _transition("state_a", "state_b", x=300, y=400),
    ]
    result = _build_path_to_state("state_b", HOME, transitions)
    assert result == [
        {"x": 100, "y": 200, "state_id": "state_a"},
        {"x": 300, "y": 400, "state_id": "state_b"},
    ]


def test_three_hop_chain() -> None:
    """State C reachable via Home → A → B → C: path must list all three steps."""
    transitions = [
        _transition(HOME, "state_a", x=10, y=20),
        _transition("state_a", "state_b", x=30, y=40),
        _transition("state_b", "state_c", x=50, y=60),
    ]
    result = _build_path_to_state("state_c", HOME, transitions)
    assert len(result) == 3
    assert result[0] == {"x": 10, "y": 20, "state_id": "state_a"}
    assert result[1] == {"x": 30, "y": 40, "state_id": "state_b"}
    assert result[2] == {"x": 50, "y": 60, "state_id": "state_c"}


# ─────────────────────────── failure paths ───────────────────────────────────

def test_orphan_state_returns_none() -> None:
    """A state with no incoming transition must return None — cannot replay path."""
    result = _build_path_to_state("state_orphan", HOME, [])
    assert result is None


def test_non_success_transition_is_ignored() -> None:
    """A failed or no-change transition must not count as a usable path step."""
    transitions = [
        {
            "transition_id":        "t_fail",
            "source_state_id":      HOME,
            "source_element_id":    "elem_x",
            "action_type":          "tap",
            "action_payload":       {"x": 1, "y": 1},
            "destination_state_id": "state_a",
            "outcome":              "failed",
            "error":                "adb error",
        }
    ]
    assert _build_path_to_state("state_a", HOME, transitions) is None


def test_no_change_transition_is_ignored() -> None:
    """A no_change transition (destination == source) must not be used as a path step."""
    transitions = [
        {
            "transition_id":        "t_noop",
            "source_state_id":      HOME,
            "source_element_id":    "elem_x",
            "action_type":          "tap",
            "action_payload":       {"x": 1, "y": 1},
            "destination_state_id": HOME,
            "outcome":              "no_change",
            "error":                None,
        }
    ]
    # state_a has no incoming success transition — orphan
    assert _build_path_to_state("state_a", HOME, transitions) is None


def test_mixed_transitions_uses_only_success() -> None:
    """When there is both a failed and a successful transition to the same state,
    the successful one must be used."""
    transitions = [
        {
            "transition_id":        "t_fail",
            "source_state_id":      HOME,
            "source_element_id":    "elem_fail",
            "action_type":          "tap",
            "action_payload":       {"x": 99, "y": 99},
            "destination_state_id": "state_a",
            "outcome":              "failed",
            "error":                "adb error",
        },
        _transition(HOME, "state_a", x=100, y=200),
    ]
    result = _build_path_to_state("state_a", HOME, transitions)
    assert result == [{"x": 100, "y": 200, "state_id": "state_a"}]
