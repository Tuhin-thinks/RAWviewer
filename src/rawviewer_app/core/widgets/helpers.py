"""Module-level helpers for the shared widget layer."""

import os


def _norm_dir_key(path: str) -> str:
    return os.path.normpath(os.path.abspath(path or "")).lower().rstrip("\\/")


def _filmstrip_ui_env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default
