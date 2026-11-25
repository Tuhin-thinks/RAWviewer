"""Opt-in runtime dump of instance attribute types, for typing diagnostics.

Active only when RAWVIEWER_TYPING_DUMP=1. ``install()`` registers an atexit hook that
writes ``{class: {attr: sorted({type(value).__qualname__})}}`` for every instance whose
class is defined under src/, to ``scratch/attr_types.json`` at the repository root.
Nothing is registered or written when the flag is unset.
"""

import atexit
import gc
import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Final

ENV_FLAG: Final[str] = "RAWVIEWER_TYPING_DUMP"
ROOT: Final[Path] = Path(__file__).resolve().parents[2]
APP_SRC: Final[Path] = ROOT / "src"
OUT_PATH: Final[Path] = ROOT / "scratch" / "attr_types.json"


def _is_app_class(cls: type) -> bool:
    module = sys.modules.get(cls.__module__)
    file = getattr(module, "__file__", None)
    return isinstance(file, str) and Path(file).resolve().is_relative_to(APP_SRC)


def collect(objects: Iterable[object]) -> dict[str, dict[str, list[str]]]:
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    app_cache: dict[type, bool] = {}
    for obj in objects:
        cls = type(obj)
        if cls not in app_cache:
            app_cache[cls] = _is_app_class(cls)
        if not app_cache[cls]:
            continue
        attrs = getattr(obj, "__dict__", None)
        if not isinstance(attrs, dict):
            continue
        bucket = seen[cls.__qualname__]
        for attr, value in attrs.items():
            if isinstance(attr, str):
                bucket[attr].add(type(value).__qualname__)
    return {
        class_name: {attr: sorted(names) for attr, names in sorted(attrs.items())}
        for class_name, attrs in sorted(seen.items())
    }


def dump() -> None:
    data = collect(gc.get_objects())
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def install() -> bool:
    """Register the atexit dump if RAWVIEWER_TYPING_DUMP=1. Return whether it was registered."""
    if os.environ.get(ENV_FLAG) != "1":
        return False
    atexit.register(dump)
    return True
