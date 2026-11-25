"""Cache key, path, hash and environment helper functions for RAWviewer."""

import json
import os
from typing import Any

from cache_limits import (
    _darwin_memory_stats_from_vm_stat,  # noqa: F401  # re-export
    _darwin_os_proc_available_memory,  # noqa: F401  # re-export
    _darwin_page_size_bytes,  # noqa: F401  # re-export
    _darwin_total_ram_cached,  # noqa: F401  # re-export
    _env_float,  # noqa: F401  # re-export
    _env_int,  # noqa: F401  # re-export
    _is_libraw_native_name,  # noqa: F401  # re-export
    _jpeg_bytes_max_edge,  # noqa: F401  # re-export
    _libraw_native_fingerprint,  # noqa: F401  # re-export
    _libraw_native_search_dirs,  # noqa: F401  # re-export
    _parse_vm_stat_page_count,  # noqa: F401  # re-export
    _preview_cache_adaptive_enabled,  # noqa: F401  # re-export
    _psutil_vm_fallback_logged,  # noqa: F401  # re-export
    _safe_virtual_memory,  # noqa: F401  # re-export
    _sysctl_hw_memsize_bytes,  # noqa: F401  # re-export
    disk_preview_max_edge,  # noqa: F401  # re-export
    libraw_runtime_stamp,  # noqa: F401  # re-export
    memory_preview_max_edge,  # noqa: F401  # re-export
    system_memory_total_bytes,  # noqa: F401  # re-export
)


def _encode_exif_blob(data: dict[str, Any] | None) -> bytes:
    """Serialize EXIF blob as UTF-8 JSON (safe; no pickle on write)."""
    payload = data if isinstance(data, dict) else {}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode_exif_blob(blob: Any) -> dict[str, Any]:
    """Deserialize EXIF blob as JSON.

    Legacy pickle rows are intentionally NOT decoded: unpickling data from an
    on-disk cache file would be arbitrary code execution for anyone with
    write access to the cache directory. Non-JSON rows are treated as a cache
    miss (empty dict); the caller re-extracts and rewrites the row as JSON.
    """
    if not blob:
        return {}
    try:
        if isinstance(blob, str):
            out = json.loads(blob)
            return out if isinstance(out, dict) else {}
        raw = bytes(blob)
        if raw[:1] in (b"{", b"["):
            out = json.loads(raw.decode("utf-8"))
            return out if isinstance(out, dict) else {}
    except Exception:
        pass
    return {}


def _safe_path_under_root(path: str | None, root: str | None) -> str | None:
    """Return realpath of ``path`` only if it resolves under ``root``."""
    if not path or not root:
        return None
    try:
        root_real = os.path.realpath(root)
        candidate = os.path.realpath(path)
    except OSError:
        return None
    if candidate == root_real or candidate.startswith(root_real + os.sep):
        return candidate
    return None


def _exif_cache_path_key(file_path: str) -> str:
    """Normalized path for exif_cache row keys and lookups (case- and slash-tolerant on Windows)."""
    if not file_path:
        return ""
    try:
        p = file_path if os.path.isabs(file_path) else os.path.abspath(file_path)
        key = os.path.normcase(os.path.normpath(p))
    except OSError:
        key = os.path.normcase(os.path.normpath(file_path))
    if os.sep == "\\":
        key = key.replace("/", "\\")
    return key


def _cache_path_key(file_path: str) -> str:
    """Normalized path key for thumbnail/grid/preview caches (matches semantic canonical paths on Windows)."""
    return _exif_cache_path_key(file_path)


def _exif_sql_path_expr(column: str = "file_path") -> str:
    """SQL expression that normalizes slashes for path comparisons."""
    if os.sep == "\\":
        return f"lower(replace({column}, '/', '\\\\'))"
    return f"lower(replace({column}, '\\\\', '/'))"


def _exif_sql_folder_like_pattern(folder_path: str) -> str:
    """LIKE prefix for all files under folder_path (matches stored canonical keys)."""
    root = _exif_cache_path_key(os.path.abspath(folder_path)).rstrip("\\/")
    if os.sep == "\\":
        return root + "\\%"
    return root.replace("\\", "/") + "/%"


def _exif_sql_folder_like_patterns(folder_path: str) -> list[str]:
    """Candidate LIKE prefixes (drive letter vs UNC realpath may differ in the DB)."""
    patterns: list[str] = []
    try:
        primary = _exif_sql_folder_like_pattern(folder_path)
        if primary:
            patterns.append(primary)
        try:
            real_root = os.path.realpath(os.path.abspath(folder_path))
        except OSError:
            real_root = None
        if real_root:
            alt = _exif_sql_folder_like_pattern(real_root)
            if alt and alt not in patterns:
                patterns.append(alt)
    except OSError:
        pass
    return patterns


def _mtime_matches(cached_mtime: float, current_mtime: float) -> bool:
    """Allow sub-second drift (network shares / FAT timestamp granularity)."""
    try:
        return abs(float(cached_mtime) - float(current_mtime)) < 1.0
    except (TypeError, ValueError):
        return cached_mtime == current_mtime


def _capture_time_from_exif_row(row: Any, exif_data: dict[str, Any] | None = None) -> str | None:
    ct = row[7] if len(row) > 7 else None
    if ct and str(ct).strip():
        return str(ct)
    if exif_data and exif_data.get("capture_time"):
        return str(exif_data["capture_time"])
    return None


def _file_stats_row(file_stats: dict[str, tuple[int, float]] | None, *paths: str) -> tuple[int, float] | None:
    if not file_stats:
        return None
    for path in paths:
        if not path:
            continue
        if path in file_stats:
            return file_stats[path]
        nk = _exif_cache_path_key(path)
        for key, row in file_stats.items():
            if _exif_cache_path_key(key) == nk:
                return row
    return None
