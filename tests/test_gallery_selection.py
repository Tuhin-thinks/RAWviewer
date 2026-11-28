"""Explorer-style selection rules for the gallery (Shift / Ctrl / Shift+Ctrl clicks and arrows).

Pure-logic tests: no Qt, no files. Expected behaviour mirrors Windows Explorer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rawviewer_app.window.gallery_selection import (  # noqa: E402
    SelectionState,
    apply_click,
    apply_move,
    range_keys,
    toggle,
)

ORDER = ["a", "b", "c", "d", "e", "f"]


def _state(selected: set[str], anchor: str | None = None, focus: str | None = None) -> SelectionState:
    return SelectionState(selected=frozenset(selected), anchor=anchor, focus=focus)


def test_plain_click_replaces_selection_and_sets_anchor() -> None:
    s = apply_click(_state({"a", "b"}, "a", "a"), ORDER, "d", ctrl=False, shift=False)
    assert s.selected == frozenset({"d"})
    assert s.anchor == "d"
    assert s.focus == "d"


def test_ctrl_click_toggles_on_and_off() -> None:
    s = apply_click(_state({"a"}, "a", "a"), ORDER, "c", ctrl=True, shift=False)
    assert s.selected == frozenset({"a", "c"})
    assert s.anchor == "c"
    s = apply_click(s, ORDER, "a", ctrl=True, shift=False)
    assert s.selected == frozenset({"c"})


def test_shift_click_replaces_selection_with_range() -> None:
    s = _state({"f"}, "b", "f")
    s = apply_click(s, ORDER, "d", ctrl=False, shift=True)
    assert s.selected == frozenset({"b", "c", "d"})
    assert s.anchor == "b"
    assert s.focus == "d"


def test_shift_click_keeps_anchor_fixed_across_repeats() -> None:
    s = apply_click(_state(set()), ORDER, "a", ctrl=False, shift=False)
    s = apply_click(s, ORDER, "e", ctrl=False, shift=True)
    assert s.selected == frozenset({"a", "b", "c", "d", "e"})
    s = apply_click(s, ORDER, "c", ctrl=False, shift=True)
    assert s.selected == frozenset({"a", "b", "c"})
    assert s.anchor == "a"


def test_ctrl_shift_click_adds_range_to_existing_selection() -> None:
    s = _state({"f"}, "b", "b")
    s = apply_click(s, ORDER, "d", ctrl=True, shift=True)
    assert s.selected == frozenset({"f", "b", "c", "d"})
    assert s.anchor == "b"


def test_reverse_range_selects_same_items() -> None:
    assert range_keys(ORDER, "d", "b") == {"b", "c", "d"}
    assert range_keys(ORDER, "b", "d") == {"b", "c", "d"}


def test_range_with_unknown_endpoint_is_empty() -> None:
    assert range_keys(ORDER, "zzz", "b") == set()


def test_shift_click_without_anchor_uses_focus() -> None:
    s = _state(set(), None, "c")
    s = apply_click(s, ORDER, "e", ctrl=False, shift=True)
    assert s.selected == frozenset({"c", "d", "e"})


def test_shift_click_without_anchor_or_focus_starts_at_clicked_item() -> None:
    s = apply_click(_state(set()), ORDER, "b", ctrl=False, shift=True)
    assert s.selected == frozenset({"b"})
    assert s.anchor == "b"


def test_keyboard_arrow_moves_selection_to_single_item() -> None:
    s = apply_move(_state({"a", "b"}, "a", "b"), ORDER, "c", ctrl=False, shift=False)
    assert s.selected == frozenset({"c"})
    assert s.anchor == "c"
    assert s.focus == "c"


def test_shift_arrow_extends_range_from_anchor() -> None:
    s = _state({"b"}, "b", "b")
    s = apply_move(s, ORDER, "d", ctrl=False, shift=True)
    assert s.selected == frozenset({"b", "c", "d"})
    assert s.anchor == "b"
    assert s.focus == "d"


def test_ctrl_arrow_moves_focus_only() -> None:
    s = _state({"a", "b"}, "a", "a")
    s = apply_move(s, ORDER, "d", ctrl=True, shift=False)
    assert s.selected == frozenset({"a", "b"})
    assert s.anchor == "a"
    assert s.focus == "d"


def test_ctrl_shift_arrow_adds_range() -> None:
    s = _state({"f"}, "b", "b")
    s = apply_move(s, ORDER, "c", ctrl=True, shift=True)
    assert s.selected == frozenset({"f", "b", "c"})
    assert s.anchor == "b"


def test_toggle_moves_anchor_and_keeps_focus() -> None:
    s = _state({"a"}, "a", "a")
    s = toggle(s, "e")
    assert s.selected == frozenset({"a", "e"})
    assert s.anchor == "e"
    assert s.focus == "a"
