"""Explorer-style selection rules for the gallery, free of Qt so they can be unit-tested.

Keys are normalised file paths in on-screen order. Behaviour mirrors Windows Explorer:

* click            -> select only this item; anchor = item
* Ctrl+click       -> toggle this item; anchor = item
* Shift+click      -> replace selection with anchor..item; anchor unchanged
* Ctrl+Shift+click -> add anchor..item to the selection; anchor unchanged

Keyboard moves follow the same rules, with the arrow target as the item.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SelectionState:
    selected: frozenset[str] = frozenset()
    anchor: str | None = None
    focus: str | None = None


def range_keys(order: Sequence[str], a: str, b: str) -> set[str]:
    """Inclusive keys between ``a`` and ``b`` in display order; empty if either is unknown."""
    try:
        i = order.index(a)
        j = order.index(b)
    except ValueError:
        return set()
    lo, hi = (i, j) if i <= j else (j, i)
    return set(order[lo : hi + 1])


def _resolve_anchor(state: SelectionState, order: Sequence[str], key: str) -> str:
    """Anchor for a range: the stored anchor, else the focus, else the target itself."""
    for candidate in (state.anchor, state.focus):
        if candidate is not None and candidate in order:
            return candidate
    return key


def toggle(state: SelectionState, key: str) -> SelectionState:
    """Ctrl+Space / Ctrl+click: flip membership and make ``key`` the anchor."""
    selected = set(state.selected)
    if key in selected:
        selected.discard(key)
    else:
        selected.add(key)
    return SelectionState(frozenset(selected), anchor=key, focus=state.focus)


def apply_click(
    state: SelectionState,
    order: Sequence[str],
    key: str,
    *,
    ctrl: bool,
    shift: bool,
) -> SelectionState:
    """Mouse click on ``key`` with the given modifiers."""
    if shift:
        anchor = _resolve_anchor(state, order, key)
        span = range_keys(order, anchor, key)
        selected = frozenset(state.selected | span) if ctrl else frozenset(span)
        return SelectionState(selected, anchor=anchor, focus=key)
    if ctrl:
        toggled = toggle(state, key)
        return SelectionState(toggled.selected, anchor=key, focus=key)
    return SelectionState(frozenset({key}), anchor=key, focus=key)


def apply_move(
    state: SelectionState,
    order: Sequence[str],
    target: str,
    *,
    ctrl: bool,
    shift: bool,
) -> SelectionState:
    """Arrow / Home / End move to ``target``. Ctrl alone only moves focus."""
    if shift:
        anchor = _resolve_anchor(state, order, target)
        span = range_keys(order, anchor, target)
        selected = frozenset(state.selected | span) if ctrl else frozenset(span)
        return SelectionState(selected, anchor=anchor, focus=target)
    if ctrl:
        return SelectionState(state.selected, anchor=state.anchor, focus=target)
    return SelectionState(frozenset({target}), anchor=target, focus=target)
