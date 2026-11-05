"""Backwards-compatible facade re-exporting the cache tiers.

The implementation now lives under the :mod:`cache` package
(``cache.memory``, ``cache.disk``, ``cache.metadata``, ``cache.keys``,
``cache.image_cache``). This module re-exports the historical
``image_cache`` public surface so existing imports keep working.
"""

from cache.disk import PersistentGridCache, PersistentPreviewCache, PersistentThumbnailCache
from cache.image_cache import (
    ImageCache,
    _cleanup_legacy_disk_cache_once,
    _exif_cache_atexit_registered,
    _global_cache,
    _legacy_cache_cleanup_done,
    _register_exif_cache_atexit,
    get_image_cache,
    initialize_cache,
)
from cache.keys import (
    _cache_path_key,
    _capture_time_from_exif_row,
    _darwin_memory_stats_from_vm_stat,
    _darwin_os_proc_available_memory,
    _darwin_page_size_bytes,
    _darwin_total_ram_cached,
    _decode_exif_blob,
    _encode_exif_blob,
    _env_float,
    _env_int,
    _exif_cache_path_key,
    _exif_sql_folder_like_pattern,
    _exif_sql_folder_like_patterns,
    _exif_sql_path_expr,
    _file_stats_row,
    _is_libraw_native_name,
    _jpeg_bytes_max_edge,
    _libraw_native_fingerprint,
    _libraw_native_search_dirs,
    _mtime_matches,
    _parse_vm_stat_page_count,
    _preview_cache_adaptive_enabled,
    _psutil_vm_fallback_logged,
    _safe_path_under_root,
    _safe_virtual_memory,
    _sysctl_hw_memsize_bytes,
    disk_preview_max_edge,
    libraw_runtime_stamp,
    memory_preview_max_edge,
    recommended_preview_cache_items,
    system_memory_total_bytes,
)
from cache.memory import FullImageCache, LRUCache, MemoryMonitor, MemoryOnlyPersistentCache
from cache.metadata import PersistentEXIFCache

__all__ = [
    # Tiers
    "FullImageCache",
    "ImageCache",
    "LRUCache",
    "MemoryMonitor",
    "MemoryOnlyPersistentCache",
    "PersistentEXIFCache",
    "PersistentGridCache",
    "PersistentPreviewCache",
    "PersistentThumbnailCache",
    # Main cache entry points
    "get_image_cache",
    "initialize_cache",
    # Cache helpers
    "_cache_path_key",
    "_capture_time_from_exif_row",
    "_darwin_memory_stats_from_vm_stat",
    "_darwin_os_proc_available_memory",
    "_darwin_page_size_bytes",
    "_decode_exif_blob",
    "_encode_exif_blob",
    "_env_float",
    "_env_int",
    "_exif_cache_path_key",
    "_exif_sql_folder_like_pattern",
    "_exif_sql_folder_like_patterns",
    "_exif_sql_path_expr",
    "_file_stats_row",
    "_is_libraw_native_name",
    "_jpeg_bytes_max_edge",
    "_libraw_native_fingerprint",
    "_libraw_native_search_dirs",
    "_mtime_matches",
    "_parse_vm_stat_page_count",
    "_preview_cache_adaptive_enabled",
    "_psutil_vm_fallback_logged",
    "_safe_path_under_root",
    "_safe_virtual_memory",
    "_sysctl_hw_memsize_bytes",
    "_darwin_total_ram_cached",
    "disk_preview_max_edge",
    "libraw_runtime_stamp",
    "memory_preview_max_edge",
    "recommended_preview_cache_items",
    "system_memory_total_bytes",
    # Module-level cache state / lifecycle hooks
    "_cleanup_legacy_disk_cache_once",
    "_exif_cache_atexit_registered",
    "_global_cache",
    "_legacy_cache_cleanup_done",
    "_register_exif_cache_atexit",
]
