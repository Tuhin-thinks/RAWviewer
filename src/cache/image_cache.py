"""Main image cache composing memory + persistent tiers (singleton accessor)."""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import threading
import time
from collections import OrderedDict
from typing import Any

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from cache.disk import PersistentGridCache, PersistentPreviewCache, PersistentThumbnailCache
from cache.keys import (
    _cache_path_key,
    _env_int,
    _jpeg_bytes_max_edge,
    disk_preview_max_edge,
    libraw_runtime_stamp,
)
from cache.memory import LRUCache, MemoryMonitor, MemoryOnlyPersistentCache
from cache.metadata import PersistentEXIFCache

logger = logging.getLogger(__name__)


class ImageCache(QObject):
    """Main image caching system with thumbnails, full images, and metadata."""

    # Signals for cache events
    cache_hit = pyqtSignal(str, str)  # file_path, cache_type
    cache_miss = pyqtSignal(str, str)  # file_path, cache_type
    memory_warning = pyqtSignal(float)  # memory_usage_percent

    def __init__(self, cache_dir: str = None, persistent_cache_enabled: bool = False):
        super().__init__()

        # Initialize cache directory
        self.persistent_cache_enabled = persistent_cache_enabled
        if self.persistent_cache_enabled:
            if cache_dir is None:
                cache_dir = os.path.expanduser("~/.rawviewer_cache")
            os.makedirs(cache_dir, exist_ok=True)
            self.cache_dir = cache_dir
        else:
            self.cache_dir = None

        # Initialize memory monitor
        self.memory_monitor = MemoryMonitor()
        cache_sizes = self.memory_monitor.get_recommended_cache_sizes()

        # Initialize caches
        # Initialize caches
        self.thumbnail_cache = LRUCache(max_size=cache_sizes['thumbnails'])
        self.grid_cache = LRUCache(max_size=cache_sizes['thumbnails'] // 2)
        def _note_preview_evicted(key):
            self.libraw_preview_paths.discard(key)

        self.preview_cache = LRUCache(
            max_size=cache_sizes['preview_images'],
            on_evict=_note_preview_evicted,
        )  # High-res preview working set (RAM-adaptive)
        # Why a full image is no longer cached. A repeated full decode of the
        # same file is expensive (a 33MP embedded JPEG, seconds of work) and
        # the plain hit/miss counters cannot tell an eviction from an
        # invalidation. Bounded; diagnostics only.
        self._full_cache_reasons: OrderedDict = OrderedDict()

        def _note_full_evicted(key):
            self._note_full_cache_reason(key, "evicted (capacity 10)")

        # Phase 4: Simplified to fixed LRU(10) for full images
        self.full_image_cache = LRUCache(
            max_size=10, on_evict=_note_full_evicted
        )
        self.pixmap_cache = LRUCache(max_size=cache_sizes['full_images'])
        self.libraw_preview_paths: set = set()
        if self.cache_dir:
            self._libraw_preview_paths_file = os.path.join(self.cache_dir, "libraw_preview_paths.json")
            self._load_libraw_preview_paths()
        else:
            self._libraw_preview_paths_file = None
        # Session-scoped provenance: full_image_cache keys currently holding an
        # embedded-JPEG stand-in rather than the app's own RAW-decoded pixels
        # (see mark_full_image_source). Many camera bodies (Canon CR3, Sony
        # ARW, ...) embed a JPEG preview whose pixel dimensions reach/exceed
        # FULL_EMBEDDED_SENSOR_COVERAGE of the true sensor resolution -- by
        # dimensions alone that stand-in is indistinguishable from a true
        # decode, which previously caused every "does this already cover
        # sensor resolution" check (the fit-view display, the cached-full
        # check, and process_full_image's own cache short-circuit) to
        # conclude no further work was needed, permanently blocking the real
        # RAW pipeline from ever running for that file during normal browsing.
        self.full_image_embedded_jpeg_paths: set = set()
        if self.persistent_cache_enabled:
            self.exif_cache = PersistentEXIFCache(cache_dir)
            self.disk_thumbnail_cache = PersistentThumbnailCache(cache_dir)
            self.disk_grid_cache = PersistentGridCache(cache_dir)
            self.disk_preview_cache = PersistentPreviewCache(cache_dir)
            self._purge_stale_oriented_pixel_caches(cache_dir)
            self._purge_stale_edit_baked_pixel_caches(cache_dir)
            self._purge_stale_libraw_pixel_caches(cache_dir)
        else:
            # Trial mode: keep all acceleration in RAM and never create/write local cache files.
            self.exif_cache = MemoryOnlyPersistentCache()
            self.disk_thumbnail_cache = MemoryOnlyPersistentCache()
            self.disk_grid_cache = MemoryOnlyPersistentCache()
            self.disk_preview_cache = MemoryOnlyPersistentCache()

        # In-memory EXIF cache (used even if persistent cache is enabled for speed)
        self.exif_memory_cache = LRUCache(max_size=cache_sizes['thumbnails'])

        # Cache statistics
        self.stats = {
            'thumbnail_requests': 0,
            'grid_requests': 0,
            'preview_requests': 0,
            'full_image_requests': 0,
            'pixmap_requests': 0,
            'exif_requests': 0
        }

        # Memory management
        self.max_memory_mb = cache_sizes['cache_budget_mb']
        self._tier_max_memory_mb = int(self.max_memory_mb)
        self.memory_check_interval = 5  # seconds
        self.last_memory_check = 0
        self.last_aggressive_purge = 0.0
        self.last_reduce_cache = 0.0

        # Background memory-pressure polling: macOS prefers ctypes
        # os_proc_available_memory (jetsam headroom). A lazily-started daemon
        # thread refreshes a cached snapshot; _check_memory_pressure only
        # ever reads it — never inline on the gallery scroll path.
        self._mem_poll_lock = threading.Lock()
        self._mem_poll_info: dict[str, float] | None = None
        self._mem_poll_pressure = 'ok'
        self._mem_poll_thread: threading.Thread | None = None

        # Background RAW-orientation verification (see get_exif): one short
        # queue drained by a single lazily-started daemon thread, one attempt
        # per path per session.
        self._orient_verify_lock = threading.Lock()
        self._orient_verify_attempted: set = set()
        # Dedup-only set (never holds a value, just "already tried"), so
        # capping it is cheap to reason about: a path evicted here that
        # comes up again just gets re-queued for one more probe, and a
        # record that already probed clean short-circuits before ever
        # reaching this set again (get_exif/get_multiple_exif only call
        # _schedule_orientation_verify when verified_orientation is falsy).
        # Uncapped, a very large library browsed in one long session grows
        # this set forever; same clear-when-full convention as
        # enhanced_raw_processor._embedded_scan_miss_cache.
        self._orient_verify_attempted_max = 4096
        self._orient_verify_queue: list = []
        self._orient_verify_queue_max = 256
        self._orient_verify_thread: threading.Thread | None = None
        self._closing = False

    def _purge_stale_oriented_pixel_caches(self, cache_dir: str) -> None:
        """One-time purge of persisted PIXEL caches when the orientation logic version bumps.

        RAW_EXIF_SENSOR_META_VER invalidates cached EXIF ROWS, but the disk
        thumbnail/grid/preview JPEGs persist the decoded PIXELS. A version with buggy
        rotation can write content-rotated pixels at plausible dimensions — invisible to
        every dimension-based self-heal (they compare width/height, not content). Stamp
        the orientation version in the cache dir and drop all persisted pixel tiers when
        it changes, so upgraded users never see stale sideways thumbnails.
        """
        try:
            from enhanced_raw_processor import RAW_EXIF_SENSOR_META_VER

            marker = os.path.join(cache_dir, "orient_pixel_ver.txt")
            stored = None
            try:
                with open(marker, encoding="utf-8") as fh:
                    stored = int(fh.read().strip() or 0)
            except Exception:
                stored = None
            if stored == int(RAW_EXIF_SENSOR_META_VER):
                return
            print(
                "[CACHE] Orientation version changed "
                f"({stored} -> {RAW_EXIF_SENSOR_META_VER}); purging persisted pixel caches"
            )
            for cache in (
                self.disk_thumbnail_cache,
                self.disk_grid_cache,
                self.disk_preview_cache,
            ):
                try:
                    cache.clear()
                except Exception:
                    pass
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(str(int(RAW_EXIF_SENSOR_META_VER)))
        except Exception:
            pass

    # Bump when edited-look thumbnails may have been persisted without a way
    # to invalidate them (disk tiers key on RAW size+mtime only; an XMP edit
    # or Reset never touches the RAW file). v1: editor bake used to re-run
    # after Reset / miss invalidation, leaving permanently stale edited thumbs.
    EDIT_BAKE_PIXEL_VER = 1

    def _purge_stale_edit_baked_pixel_caches(self, cache_dir: str) -> None:
        """One-time purge of persisted pixel tiers when the edit-bake hygiene version bumps.

        _persist_editor_aligned_browse_caches writes edits-baked-in pixels into
        the same disk tiers as plain browse thumbs. Those tiers validate by RAW
        (size, mtime), which an XMP sidecar edit/reset never changes -- so any
        stale edited bake (older Reset bug, crash between bake and invalidate)
        survives forever. Same pattern as _purge_stale_oriented_pixel_caches.
        """
        try:
            marker = os.path.join(cache_dir, "edit_bake_ver.txt")
            stored = None
            try:
                with open(marker, encoding="utf-8") as fh:
                    stored = int(fh.read().strip() or 0)
            except Exception:
                stored = None
            if stored == int(self.EDIT_BAKE_PIXEL_VER):
                return
            print(
                "[CACHE] Edit-bake hygiene version changed "
                f"({stored} -> {self.EDIT_BAKE_PIXEL_VER}); purging persisted pixel caches"
            )
            for cache in (
                self.disk_thumbnail_cache,
                self.disk_grid_cache,
                self.disk_preview_cache,
            ):
                try:
                    cache.clear()
                except Exception:
                    pass
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(str(int(self.EDIT_BAKE_PIXEL_VER)))
        except Exception:
            pass

    def _purge_stale_libraw_pixel_caches(self, cache_dir: str) -> None:
        """Purge demosaic/preview pixel tiers when the LibRaw binary changes.

        Disk thumb/grid/preview for RAW often hold LibRaw-rendered pixels.
        Stock rawpy and the packaging-swapped LibRaw (ARW6 master pin) can
        report the same libraw_version tuple while producing different pixels,
        so the stamp fingerprints the native library file beside rawpy as well.
        """
        try:
            stamp = libraw_runtime_stamp()
            if not stamp:
                return
            marker = os.path.join(cache_dir, "libraw_pixel_ver.txt")
            stored = None
            try:
                with open(marker, encoding="utf-8") as fh:
                    stored = (fh.read() or "").strip() or None
            except Exception:
                stored = None
            if stored == stamp:
                return
            print(
                "[CACHE] LibRaw runtime changed "
                f"({stored!r} -> {stamp!r}); purging persisted pixel caches"
            )
            for cache in (
                self.disk_thumbnail_cache,
                self.disk_grid_cache,
                self.disk_preview_cache,
            ):
                try:
                    cache.clear()
                except Exception:
                    pass
            try:
                from processing.unified import (
                    prune_libraw_unsupported_paths,
                    release_edit_base_cache,
                )
            except Exception:
                prune_libraw_unsupported_paths = None
                release_edit_base_cache = None
            if release_edit_base_cache is not None:
                try:
                    release_edit_base_cache()
                except Exception:
                    pass
            if prune_libraw_unsupported_paths is not None:
                try:
                    prune_libraw_unsupported_paths(clear_all=True)
                except Exception:
                    pass
            try:
                self.libraw_preview_paths.clear()
                self._save_libraw_preview_paths()
            except Exception:
                pass
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write(stamp)
        except Exception:
            pass

    @staticmethod
    def _path_key(file_path: str) -> str:
        return _cache_path_key(file_path)

    def _ensure_mem_poll_thread(self) -> None:
        """Start the background memory-pressure poller if it isn't already running."""
        if getattr(self, "_closing", False):
            return
        with self._mem_poll_lock:
            if self._mem_poll_thread is not None and self._mem_poll_thread.is_alive():
                return
            self._mem_poll_thread = threading.Thread(
                target=self._mem_poll_worker,
                name="mem-pressure-poll",
                daemon=True,
            )
            self._mem_poll_thread.start()

    def _mem_poll_worker(self) -> None:
        """Refresh the cached memory-info/pressure snapshot every
        memory_check_interval seconds, off the main/calling thread.
        """
        while not getattr(self, "_closing", False):
            try:
                info = self.memory_monitor.get_memory_info()
                level = self.memory_monitor.system_memory_pressure_level(info)
            except Exception:
                info, level = None, None
            if info is not None:
                with self._mem_poll_lock:
                    self._mem_poll_info = info
                    self._mem_poll_pressure = level
            time.sleep(self.memory_check_interval)

    def _check_memory_pressure(self) -> bool:
        """Check if we're under memory pressure and need to reduce cache sizes.

        Reads the snapshot the background poller last collected rather than
        collecting it inline. Cache-read paths (get_grid/get_thumbnail) run
        on whatever thread touches the cache, including the GUI thread.
        """
        current_time = time.time()
        if current_time - self.last_memory_check < self.memory_check_interval:
            return False
        self.last_memory_check = current_time
        self._ensure_mem_poll_thread()

        with self._mem_poll_lock:
            memory_info = self._mem_poll_info
            pressure_level = self._mem_poll_pressure

        if memory_info is None:
            # No snapshot yet (first check(s) after startup) -- the poller
            # just started; nothing to act on until it reports back.
            return False

        percent_used = memory_info['system_percent_used']
        available_gb = memory_info['system_available_gb']
        rss_mb = memory_info['process_rss_mb']

        if pressure_level == 'critical':
            if current_time - getattr(self, 'last_aggressive_purge', 0.0) < 60.0:
                # Under cooldown to prevent app freeze / infinite loop of purging
                return False
            self.last_aggressive_purge = current_time
            logger.warning(
                "CRITICAL system memory pressure (system: %.1f%% used, %.1f GB free, process RSS: %.1f MB). Aggressively purging caches.",
                percent_used,
                available_gb,
                rss_mb,
            )
            self.memory_warning.emit(percent_used)
            self._aggressive_purge()
            return True

        if pressure_level == 'elevated':
            if current_time - getattr(self, 'last_reduce_cache', 0.0) < 20.0:
                # Under cooldown
                return False
            self.last_reduce_cache = current_time
            logger.warning(
                "Elevated system memory pressure (system: %.1f%% used, %.1f GB free, process RSS: %.1f MB). Reducing cache sizes.",
                percent_used,
                available_gb,
                rss_mb,
            )
            self.memory_warning.emit(percent_used)
            self._reduce_cache_sizes()
            return True

        return False

    def _aggressive_purge(self) -> None:
        """Aggressively clear heavy caches and drop their capacities to a minimum under critical pressure."""
        logger.info("Performing aggressive cache purge...")

        # Clear heavy caches entirely
        for _k in list(self.full_image_cache.cache.keys()):
            self._note_full_cache_reason(_k, "dropped by memory-pressure purge")
        self.full_image_cache.clear()
        self.pixmap_cache.clear()
        # The edit-base LRU lives in processing.unified and is the one
        # heavy cache this purge could not previously reach, so a
        # critical-pressure purge freed everything except a couple of
        # hundred MB of float32 bases.
        try:
            from processing.unified import (
                release_adjusted_full_cache,
                release_edit_base_cache,
                release_unpacked_raw_cache,
            )

            # All three live in processing.unified at module scope so they
            # survive the one-shot workers that build them. That is what makes
            # them useful, and also what puts them outside every cache this
            # class owns -- a critical-pressure purge used to clear everything
            # it could see and leave these whole.
            freed = (
                release_edit_base_cache()
                + release_adjusted_full_cache()
                + release_unpacked_raw_cache()
            )
            if freed:
                import logging

                logging.getLogger(__name__).info(
                    "[MEMORY] released %.0f MB of decode/edit buffers", freed / 1e6
                )
        except Exception:
            pass

        # Aggressively drop capacities to 3 thread-safely
        self.full_image_cache.shrink_to_size(3)
        self.pixmap_cache.shrink_to_size(3)

        # Reduce and clear other caches aggressively thread-safely
        new_thumbnail_size = max(10, int(self.thumbnail_cache.max_size * 0.5))
        new_grid_size = max(5, int(self.grid_cache.max_size * 0.5))
        new_preview_size = max(4, int(self.preview_cache.max_size * 0.5))

        self.thumbnail_cache.shrink_to_size(new_thumbnail_size)
        self.grid_cache.shrink_to_size(new_grid_size)
        self.preview_cache.shrink_to_size(new_preview_size)

        self._release_gpu_memory()

        import gc
        gc.collect()

    def _reduce_cache_sizes(self) -> None:
        """Reduce cache sizes when under memory pressure."""
        # Reduce by 25%
        new_full_size = max(5, int(self.full_image_cache.max_size * 0.75))
        new_pixmap_size = max(5, int(self.pixmap_cache.max_size * 0.75))
        new_thumbnail_size = max(20, int(self.thumbnail_cache.max_size * 0.75))
        new_grid_size = max(10, int(self.grid_cache.max_size * 0.75))
        min_preview = _env_int(
            "RAWVIEWER_PREVIEW_CACHE_ITEMS_MIN", 6, minimum=4, maximum=32
        )
        new_preview_size = max(
            min_preview, int(self.preview_cache.max_size * 0.75)
        )

        # Clear excess items thread-safely and update max sizes
        self.full_image_cache.shrink_to_size(new_full_size)
        self.pixmap_cache.shrink_to_size(new_pixmap_size)
        self.thumbnail_cache.shrink_to_size(new_thumbnail_size)
        self.grid_cache.shrink_to_size(new_grid_size)
        self.preview_cache.shrink_to_size(new_preview_size)

        self._release_gpu_memory()

    def _release_gpu_memory(self) -> None:
        """Return the GPU/unified-memory caching allocator's pool to the OS.

        On Apple Silicon, PyTorch's MPS allocator caches freed device buffers
        in UNIFIED memory (shared with system RAM) rather than releasing them
        -- that pool is invisible to gc/tracemalloc but shows up directly as
        process RSS, and nothing in this codebase ever called
        torch.mps.empty_cache() before this. v2.5 had no GPU decode path at
        all and doesn't have this cost; this build's GPU-accelerated decode
        (gpu_raw_processor.py) does. Piggyback on the existing cache-pressure
        cadence rather than calling it every decode (that would defeat the
        allocator's whole point -- reuse without re-requesting from the OS).
        """
        try:
            from gpu_raw_processor import release_cached_gpu_memory

            release_cached_gpu_memory()
        except Exception:
            logger.debug("GPU memory release failed", exc_info=True)

    def _heal_raw_orientation(self, file_path: str, arr: np.ndarray | None) -> np.ndarray | None:
        """Self-heal: return arr already in EXIF display orientation, re-orienting if not.

        Every cache tier (thumbnail, grid, preview) must return display-oriented pixels so
        the gallery/single-view never render a stale, wrongly-rotated buffer. Uses the same
        make-independent authority as extraction (finalize_index_thumbnail_array), which is
        idempotent, so calling this on already-correct pixels is a no-op.
        """
        if arr is None:
            return None
        try:
            from common_image_loader import (
                finalize_index_thumbnail_array,
                index_thumbnail_needs_orient_fix,
                is_raw_file,
            )

            if not is_raw_file(file_path):
                return arr
            if not index_thumbnail_needs_orient_fix(file_path, arr, cache=self):
                return arr
            repaired = finalize_index_thumbnail_array(file_path, arr, cache=self)
            return repaired if repaired is not None else arr
        except Exception:
            return arr

    def get_thumbnail(self, file_path: str) -> np.ndarray | None:
        """Get cached thumbnail or return None if not cached."""
        self.stats['thumbnail_requests'] += 1
        self._check_memory_pressure()

        key = self._path_key(file_path)

        def _validate_raw_thumbnail(arr: np.ndarray | None) -> np.ndarray | None:
            return self._heal_raw_orientation(file_path, arr)

        # Check in-memory cache first
        thumbnail = self.thumbnail_cache.get(key)
        if thumbnail is not None:
            validated = _validate_raw_thumbnail(thumbnail)
            if validated is not thumbnail:
                self.thumbnail_cache.put(key, validated.copy())
                self.disk_thumbnail_cache.remove(file_path)
            self.cache_hit.emit(file_path, 'thumbnail')
            return validated

        # Check disk cache for JPEG thumbnails
        jpeg_data = self.disk_thumbnail_cache.get(file_path)
        if jpeg_data is not None:
            try:
                from common_image_loader import decode_embedded_jpeg_bytes

                thumbnail = decode_embedded_jpeg_bytes(jpeg_data, max_size=0)
                if thumbnail is None:
                    raise ValueError("decode_embedded_jpeg_bytes failed")
                thumbnail = _validate_raw_thumbnail(thumbnail)
                if thumbnail is None:
                    raise ValueError("thumbnail orientation validation failed")
                # Also cache in memory for faster subsequent access
                self.thumbnail_cache.put(key, thumbnail.copy())
                self.cache_hit.emit(file_path, 'thumbnail')
                return thumbnail
            except Exception:
                # If loading from disk cache fails, remove it
                self.disk_thumbnail_cache.remove(file_path)

        # --- Dynamic Mipmap Fallback ---
        # 1. Downsample from Grid cache (512px) if available
        grid = self.grid_cache.get(key)
        if grid is None:
            grid_jpeg = self.disk_grid_cache.get(file_path)
            if grid_jpeg is not None:
                try:
                    import io

                    from PIL import Image, ImageOps
                    pil_image = Image.open(io.BytesIO(grid_jpeg))
                    pil_image = ImageOps.exif_transpose(pil_image)
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')
                    grid = np.array(pil_image)
                except Exception:
                    pass

        if grid is not None:
            try:
                import io

                from PIL import Image
                if grid.dtype != np.uint8:
                    grid = grid.astype(np.uint8)
                pil_img = Image.fromarray(grid)
                w, h = pil_img.size
                scale = min(256 / w, 256 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                thumb_pil = pil_img.resize((new_w, new_h), Image.Resampling.HAMMING)
                thumbnail = np.array(thumb_pil)

                # Compress and store in disk/memory caches
                from common_image_loader import encode_tile_bytes

                t_jpeg = encode_tile_bytes(thumb_pil)
                self.put_thumbnail(file_path, thumbnail, t_jpeg)
                return thumbnail
            except Exception:
                pass

        # 2. Downsample from preview cache (memory or disk, up to ~512px) if available
        preview = self.preview_cache.get(key)
        if preview is None:
            preview_jpeg = self.disk_preview_cache.get(file_path)
            if preview_jpeg is not None:
                try:
                    import io

                    from PIL import Image, ImageOps
                    pil_image = Image.open(io.BytesIO(preview_jpeg))
                    pil_image = ImageOps.exif_transpose(pil_image)
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')
                    preview = np.array(pil_image)
                except Exception:
                    pass

        if preview is not None:
            try:
                import io

                from PIL import Image
                if preview.dtype != np.uint8:
                    preview = preview.astype(np.uint8)
                pil_img = Image.fromarray(preview)
                w, h = pil_img.size
                scale = min(256 / w, 256 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                thumb_pil = pil_img.resize((new_w, new_h), Image.Resampling.HAMMING)
                thumbnail = np.array(thumb_pil)

                # Compress and store in disk/memory caches
                from common_image_loader import encode_tile_bytes

                t_jpeg = encode_tile_bytes(thumb_pil)
                self.put_thumbnail(file_path, thumbnail, t_jpeg)
                return thumbnail
            except Exception:
                pass

        self.cache_miss.emit(file_path, 'thumbnail')
        return None

    def put_thumbnail(self, file_path: str, thumbnail: np.ndarray, jpeg_data: bytes = None) -> None:
        """Cache a thumbnail image."""
        if thumbnail is not None:
            # Ensure thumbnail is reasonable size (max 1024x1024)
            if thumbnail.shape[0] > 1024 or thumbnail.shape[1] > 1024:
                # This should be handled by the caller, but safety check
                return
            key = self._path_key(file_path)
            # Cache in memory
            self.thumbnail_cache.put(key, thumbnail.copy())

            # If JPEG data is provided, also cache to disk
            if jpeg_data is not None:
                self.disk_thumbnail_cache.put(file_path, jpeg_data)

    def get_preview_memory_only(self, file_path: str) -> np.ndarray | None:
        """In-memory preview only -- never decodes disk tier on the calling thread."""
        key = self._path_key(file_path)
        return self.preview_cache.peek(key)

    def get_grid_memory_only(self, file_path: str) -> np.ndarray | None:
        """In-memory grid only -- never decodes disk tier on the calling thread."""
        key = self._path_key(file_path)
        return self.grid_cache.peek(key)

    def get_thumbnail_memory_only(self, file_path: str) -> np.ndarray | None:
        """In-memory thumbnail only -- never decodes disk tier on the calling thread."""
        key = self._path_key(file_path)
        return self.thumbnail_cache.peek(key)

    def has_warm_thumbnail_in_memory(self, file_path: str) -> bool:
        """True if a grid/thumbnail tier is already in the fast in-memory
        cache -- no disk read, no decode, unlike get_grid()/get_thumbnail().

        Those two fall through to a disk-cache read + synchronous PIL decode
        + EXIF transpose on an in-memory miss. Callers that only want to know
        "is this warm, should I skip prefetching it" (filmstrip prefetch scan
        over dozens of neighbor files) were using get_grid()/get_thumbnail()
        for that check and paying the decode cost as a side effect -- on a
        long session, once the small in-memory tier evicts entries the disk
        tier still has, every such "check" becomes a real synchronous decode.
        Measured via faulthandler stack dumps during a 250+ navigation
        stress test: multi-second main-thread stalls with the current frame
        inside PIL's WebP/JPEG decode, called from exactly this "just
        checking" path.
        """
        key = self._path_key(file_path)
        # peek(): a warmth CHECK must not promote never-displayed entries to MRU.
        return (
            self.grid_cache.peek(key) is not None
            or self.thumbnail_cache.peek(key) is not None
        )

    def get_grid(self, file_path: str) -> np.ndarray | None:
        """Get cached grid image (max 512px) or return None if not cached."""
        if 'grid_requests' not in self.stats:
            self.stats['grid_requests'] = 0
        self.stats['grid_requests'] += 1
        self._check_memory_pressure()

        key = self._path_key(file_path)
        # 1. Check in-memory grid cache
        grid = self.grid_cache.get(key)
        if grid is not None:
            healed = self._heal_raw_orientation(file_path, grid)
            if healed is not grid:
                self.grid_cache.put(key, healed.copy())
                self.disk_grid_cache.remove(file_path)
            self.cache_hit.emit(file_path, 'grid')
            return healed

        # 2. Check disk grid cache
        jpeg_data = self.disk_grid_cache.get(file_path)
        if jpeg_data is not None:
            try:
                import io

                from PIL import Image, ImageOps
                pil_image = Image.open(io.BytesIO(jpeg_data))
                pil_image = ImageOps.exif_transpose(pil_image)
                if pil_image.mode != 'RGB':
                    pil_image = pil_image.convert('RGB')
                grid = np.array(pil_image)
                grid = self._heal_raw_orientation(file_path, grid)
                self.grid_cache.put(key, grid.copy())
                self.cache_hit.emit(file_path, 'grid')
                return grid
            except Exception:
                self.disk_grid_cache.remove(file_path)

        # 3. Dynamic Mipmap Fallback 1: Downsample from preview tier
        preview = self.preview_cache.get(key)
        if preview is None:
            preview_jpeg = self.disk_preview_cache.get(file_path)
            if preview_jpeg is not None:
                try:
                    import io

                    from PIL import Image, ImageOps
                    pil_image = Image.open(io.BytesIO(preview_jpeg))
                    pil_image = ImageOps.exif_transpose(pil_image)
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')
                    preview = np.array(pil_image)
                except Exception:
                    pass

        if preview is not None:
            try:
                import io

                from PIL import Image
                if preview.dtype != np.uint8:
                    preview = preview.astype(np.uint8)
                pil_img = Image.fromarray(preview)
                w, h = pil_img.size
                scale = min(512 / w, 512 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                grid_pil = pil_img.resize((new_w, new_h), Image.Resampling.HAMMING)
                grid = np.array(grid_pil)

                # Compress and store in disk/memory caches
                from common_image_loader import encode_tile_bytes

                g_jpeg = encode_tile_bytes(grid_pil)
                self.put_grid(file_path, grid, g_jpeg)
                return grid
            except Exception:
                pass

        # 4. Dynamic Mipmap Fallback 2: Upsample from Thumbnail (256px)
        thumb = self.thumbnail_cache.get(key)
        if thumb is None:
            thumb_jpeg = self.disk_thumbnail_cache.get(file_path)
            if thumb_jpeg is not None:
                try:
                    import io

                    from PIL import Image, ImageOps
                    pil_image = Image.open(io.BytesIO(thumb_jpeg))
                    pil_image = ImageOps.exif_transpose(pil_image)
                    if pil_image.mode != 'RGB':
                        pil_image = pil_image.convert('RGB')
                    thumb = np.array(pil_image)
                except Exception:
                    pass

        if thumb is not None:
            try:
                from PIL import Image
                if thumb.dtype != np.uint8:
                    thumb = thumb.astype(np.uint8)
                pil_img = Image.fromarray(thumb)
                w, h = pil_img.size
                scale = min(512 / w, 512 / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                grid_pil = pil_img.resize((new_w, new_h), Image.Resampling.BICUBIC)
                grid = np.array(grid_pil)
                self.grid_cache.put(key, grid.copy())
                return grid
            except Exception:
                pass

        self.cache_miss.emit(file_path, 'grid')
        return None

    def put_grid(self, file_path: str, grid: np.ndarray, jpeg_data: bytes = None) -> None:
        """Cache a grid image (max 512px)."""
        if grid is not None:
            key = self._path_key(file_path)
            # Ensure grid image is reasonable size (max 512x512)
            if grid.shape[0] > 512 or grid.shape[1] > 512:
                return
            # Cache in memory
            self.grid_cache.put(key, grid.copy())

            # Cache on disk
            if self.persistent_cache_enabled:
                if jpeg_data is not None:
                    self.disk_grid_cache.put(file_path, jpeg_data)
                else:
                    try:
                        from PIL import Image

                        from common_image_loader import encode_tile_bytes

                        if grid.dtype != np.uint8:
                            grid = grid.astype(np.uint8)
                        pil_image = Image.fromarray(grid)
                        self.disk_grid_cache.put(file_path, encode_tile_bytes(pil_image))
                    except Exception:
                        pass

    def get_preview(self, file_path: str) -> np.ndarray | None:
        """Get cached preview (screen size) or return None."""
        self.stats['preview_requests'] += 1
        self._check_memory_pressure()

        key = self._path_key(file_path)
        # Check in-memory cache first
        preview = self.preview_cache.get(key)
        if preview is not None:
            self.cache_hit.emit(file_path, 'preview')
            return preview

        # Check disk cache for previews
        jpeg_data = self.disk_preview_cache.get(file_path)
        if jpeg_data is None:
            jpeg_data = self.disk_grid_cache.get(file_path)
        if jpeg_data is not None:
             try:
                # cv2.imdecode over PIL: several times faster on the
                # 1280-1536px entries here, and this path runs on the
                # CALLING thread -- navigation paints call get_preview()
                # from the UI thread, so every ms is a paint stall. EXIF
                # transpose is intentionally absent: disk preview entries
                # are re-encoded from already-oriented pixels with EXIF
                # stripped (_jpeg_bytes_max_edge), and grid tiles are
                # encoded from oriented arrays.
                import cv2

                arr = cv2.imdecode(
                    np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR
                )
                if arr is None:
                    raise ValueError("imdecode failed")
                preview = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
                # Also cache in memory
                self.preview_cache.put(key, preview)
                self.cache_hit.emit(file_path, 'preview')
                return preview
             except Exception:
                self.disk_preview_cache.remove(file_path)

        self.cache_miss.emit(file_path, 'preview')
        return None

    def put_preview(
        self,
        file_path: str,
        preview: np.ndarray,
        jpeg_data: bytes = None,
        *,
        libraw_rendered: bool = False,
        copy: bool = True,
    ) -> None:
        """Cache a preview image (memory); optional disk JPEG clamped to disk_preview_max_edge().

        ``libraw_rendered`` marks the preview as LibRaw-pipeline pixels (vs
        embedded-JPEG-derived) so the RAW workflow can refuse to paint
        camera-JPEG interim tiers (session-scoped provenance).

        ``copy=False`` transfers ownership of a freshly allocated buffer the
        caller will not mutate, matching ``put_full_image``.
        """
        if preview is not None:
            key = self._path_key(file_path)
            stored = (
                np.ascontiguousarray(preview) if not copy else preview.copy()
            )
            self.preview_cache.put(key, stored)
            if libraw_rendered:
                self.libraw_preview_paths.add(key)
                self._prune_libraw_preview_paths()
                self._save_libraw_preview_paths()
            else:
                if key in self.libraw_preview_paths:
                    self.libraw_preview_paths.discard(key)
                    self._save_libraw_preview_paths()
            if jpeg_data is not None:
                cap = disk_preview_max_edge()
                jpeg_data = _jpeg_bytes_max_edge(jpeg_data, cap)
                self.disk_preview_cache.put(file_path, jpeg_data)
            self._enforce_numpy_cache_byte_budget(
                self.preview_cache,
                fraction=0.25,
                min_keep=2,
                label="preview",
            )

    def is_libraw_preview(self, file_path: str) -> bool:
        """True when the cached preview holds LibRaw-rendered pixels (this session)."""
        return self._path_key(file_path) in self.libraw_preview_paths

    def _prune_libraw_preview_paths(self, max_paths: int = 2048) -> None:
        """Bound session provenance set (paths only)."""
        n = len(self.libraw_preview_paths)
        if n <= max_paths:
            return
        # Discard arbitrary surplus (set has no order); prefer keeping keys still in preview.
        excess = n - max_paths
        drop = []
        for k in self.libraw_preview_paths:
            if self.preview_cache.peek(k) is None:
                drop.append(k)
            if len(drop) >= excess:
                break
        for k in drop:
            self.libraw_preview_paths.discard(k)
        while len(self.libraw_preview_paths) > max_paths:
            self.libraw_preview_paths.pop()

    def _note_full_cache_reason(self, file_path: str, reason: str) -> None:
        """Record why a full image left the cache (diagnostics only)."""
        try:
            self._full_cache_reasons[file_path] = reason
            self._full_cache_reasons.move_to_end(file_path)
            while len(self._full_cache_reasons) > 256:
                self._full_cache_reasons.popitem(last=False)
        except Exception:
            pass

    def get_full_image(self, file_path: str) -> np.ndarray | None:
        """Get cached full image or return None if not cached."""
        self.stats['full_image_requests'] += 1
        self._check_memory_pressure()

        image = self.full_image_cache.get(file_path)
        if image is not None:
            self.cache_hit.emit(file_path, 'full_image')
            return image
        else:
            self.cache_miss.emit(file_path, 'full_image')
            if os.environ.get("RAWVIEWER_DEBUG_FULLCACHE"):
                import logging

                logging.getLogger(__name__).info(
                    "[FULLCACHE] miss %s -- %s (cache holds %d/%d)",
                    os.path.basename(file_path or ""),
                    self._full_cache_reasons.get(file_path, "never cached this session"),
                    len(self.full_image_cache.cache),
                    self.full_image_cache.max_size,
                )
            return None

    def put_full_image(
        self, file_path: str, image: np.ndarray, *, copy: bool = True
    ) -> None:
        """Cache a full processed image.

        ``copy=False`` transfers ownership of a freshly allocated buffer the
        caller will not mutate (avoids a second multi-hundred-MB memcpy on
        40–60MP decodes). Contiguous arrays are stored as-is; non-contiguous
        inputs are still compacted with ``ascontiguousarray``.
        """
        if image is not None:
            stored = (
                np.ascontiguousarray(image) if not copy else image.copy()
            )
            self.full_image_cache.put(file_path, stored)

    def mark_full_image_source(self, file_path: str, *, is_embedded_jpeg: bool) -> None:
        """Track whether the cached full_image entry is an embedded-JPEG
        stand-in rather than the app's own RAW-decoded pixels -- see
        full_image_embedded_jpeg_paths. Call alongside every put_full_image()
        so the flag never goes stale: mark True from the embedded-preview
        shortcut, False once a true decode overwrites that same key.
        """
        if not file_path:
            return
        key = self._path_key(file_path)
        if is_embedded_jpeg:
            self.full_image_embedded_jpeg_paths.add(key)
            self._prune_full_image_embedded_jpeg_paths()
        else:
            self.full_image_embedded_jpeg_paths.discard(key)

    def full_image_is_embedded_jpeg(self, file_path: str) -> bool:
        """True when the currently cached full_image for this path is an
        embedded-JPEG stand-in (see mark_full_image_source)."""
        if not file_path:
            return False
        return self._path_key(file_path) in self.full_image_embedded_jpeg_paths

    def _prune_full_image_embedded_jpeg_paths(self, max_paths: int = 2048) -> None:
        """Bound session provenance set (paths only); same shape as
        _prune_libraw_preview_paths."""
        n = len(self.full_image_embedded_jpeg_paths)
        if n <= max_paths:
            return
        excess = n - max_paths
        drop = []
        for k in self.full_image_embedded_jpeg_paths:
            if self.full_image_cache.peek(k) is None:
                drop.append(k)
            if len(drop) >= excess:
                break
        for k in drop:
            self.full_image_embedded_jpeg_paths.discard(k)
        while len(self.full_image_embedded_jpeg_paths) > max_paths:
            self.full_image_embedded_jpeg_paths.pop()

    def _estimate_entry_bytes(self, value: Any) -> int:
        try:
            if hasattr(value, "nbytes"):
                return int(value.nbytes)
            # QPixmap / QImage
            if hasattr(value, "width") and hasattr(value, "height") and callable(value.width):
                w, h = int(value.width()), int(value.height())
                # Assume up to 4 bytes/pixel (RGBA) for budget purposes.
                return max(0, w * h * 4)
        except Exception:
            pass
        return 0

    def _enforce_numpy_cache_byte_budget(
        self,
        lru: LRUCache,
        *,
        fraction: float,
        min_keep: int = 2,
        label: str = "cache",
    ) -> None:
        """Evict LRU entries until sum(nbytes) fits fraction of max_memory_mb."""
        try:
            budget_bytes = float(self.max_memory_mb) * 1024 * 1024 * float(fraction)
            cache = lru.cache
            with lru.lock:
                total = sum(self._estimate_entry_bytes(v) for v in cache.values())
                if total <= budget_bytes:
                    return
                evicted = 0
                while total > budget_bytes and len(cache) > max(1, min_keep):
                    evicted_key, oldest = cache.popitem(last=False)
                    total -= self._estimate_entry_bytes(oldest)
                    evicted += 1
                    if lru.on_evict is not None:
                        try:
                            lru.on_evict(evicted_key)
                        except Exception:
                            pass
            if evicted:
                logger.info(
                    "%s cache: evicted %d oldest (budget ~%.0fMB, now ~%.0fMB, %d left)",
                    label,
                    evicted,
                    budget_bytes / 1e6,
                    total / 1e6,
                    len(cache),
                )
        except Exception:
            logger.warning("%s byte-budget enforcement failed", label, exc_info=True)

    def apply_pressure_budget_scale(self, scale: float, *, min_mb: int = 128) -> int:
        """Shrink the full-image byte budget for the rest of the session.

        Never raises the budget (session-sticky). Installed-RAM tier is unchanged.
        """
        original = int(getattr(self, "_tier_max_memory_mb", self.max_memory_mb) or self.max_memory_mb)
        self._tier_max_memory_mb = original
        new_mb = max(int(min_mb), int(original * float(scale)))
        self.max_memory_mb = min(int(self.max_memory_mb), new_mb)
        try:
            new_full = max(2, int(self.full_image_cache.max_size * float(scale)))
            self.full_image_cache.shrink_to_size(min(self.full_image_cache.max_size, new_full))
        except Exception:
            pass
        return int(self.max_memory_mb)

    def get_pixmap(self, file_path: str) -> QPixmap | None:
        """Get cached QPixmap or return None if not cached."""
        self.stats['pixmap_requests'] += 1

        pixmap = self.pixmap_cache.get(file_path)
        if pixmap is not None:
            self.cache_hit.emit(file_path, 'pixmap')
            return pixmap
        else:
            self.cache_miss.emit(file_path, 'pixmap')
            return None

    def put_pixmap(self, file_path: str, pixmap: QPixmap) -> None:
        """Cache a QPixmap."""
        if pixmap is not None and not pixmap.isNull():
            self.pixmap_cache.put(file_path, pixmap)
            self._enforce_numpy_cache_byte_budget(
                self.pixmap_cache,
                fraction=0.20,
                min_keep=2,
                label="pixmap",
            )

    def clear_heavy_memory_tiers(self, keep_path: str | None = None) -> None:
        """Drop full/preview/pixmap RAM tiers (keep grid/thumb for gallery scroll).

        Call on folder change. Optional keep_path retains that file's heavy entries
        when still navigating within open flow.
        """
        keep_key = self._path_key(keep_path) if keep_path else None
        keep_full = None
        keep_preview = None
        keep_pixmap = None
        keep_full_is_embedded_jpeg = False
        if keep_path:
            keep_full = self.full_image_cache.get(keep_path)
            keep_preview = self.preview_cache.get(keep_key)
            keep_pixmap = self.pixmap_cache.get(keep_path)
            keep_full_is_embedded_jpeg = self.full_image_is_embedded_jpeg(keep_path)

        self.full_image_cache.clear()
        self.preview_cache.clear()
        self.pixmap_cache.clear()
        self.libraw_preview_paths.clear()
        self.full_image_embedded_jpeg_paths.clear()

        if keep_path and keep_full is not None:
            self.full_image_cache.put(keep_path, keep_full)
            # Preserve provenance across the clear -- otherwise a kept
            # embedded-JPEG stand-in would look like a true decode afterward
            # and never get upgraded to the real RAW pixels.
            self.mark_full_image_source(keep_path, is_embedded_jpeg=keep_full_is_embedded_jpeg)
        if keep_path and keep_preview is not None and keep_key:
            self.preview_cache.put(keep_key, keep_preview)
        if keep_path and keep_pixmap is not None:
            self.pixmap_cache.put(keep_path, keep_pixmap)

    def on_folder_changed(self, keep_path: str | None = None) -> None:
        """Folder scope changed: free heavy RAM; leave disk thumbs for warm reopen."""
        self.clear_heavy_memory_tiers(keep_path=keep_path)
        try:
            from processing.unified import prune_libraw_unsupported_paths

            prune_libraw_unsupported_paths(clear_all=False, max_keep=512)
        except Exception:
            pass

    def get_exif_memory_only(self, file_path: str) -> dict[str, Any] | None:
        """In-memory EXIF only (no SQLite) — for cache-hit emit during preview-first."""
        return self.exif_memory_cache.get(file_path)

    def get_exif_for_ui(self, file_path: str) -> dict[str, Any] | None:
        """UI hot path: never blocks (see get_exif's verify=False default)."""
        return self.get_exif(file_path)

    def get_exif(
        self, file_path: str, *, verify: bool = False
    ) -> dict[str, Any] | None:
        """Get cached EXIF data or return None if not cached.

        RAW orientation trustworthiness: records lacking ``verified_orientation``
        used to be re-checked synchronously on EVERY call via
        cached_raw_exif_orientation_trustworthy() -> rawpy.imread() -- a real
        file open that stalled whatever thread called this (including the Qt
        main thread: status bar, HUD, prefetch decisions), and whose
        successful result was never memoized, so the same file re-paid the
        probe on every subsequent call too.

        Now: ``verify=False`` (default) returns the record optimistically and
        schedules the probe on a background thread; every pixel-rotation path
        re-derives orientation at decode time, so a briefly-unverified record
        can't rotate pixels wrongly, and the background result either stamps
        verified_orientation=True or evicts the bad record so the next fetch
        re-extracts fresh. ``verify=True`` (decode paths that gate orientation
        decisions on this record, e.g. image_load_manager's deferred-EXIF
        emits) keeps the synchronous probe but now memoizes success.
        """
        self.stats['exif_requests'] += 1

        # 1. Check in-memory cache first
        exif_data = self.exif_memory_cache.get(file_path)
        if exif_data is None:
            # 2. Check persistent cache
            exif_data = self.exif_cache.get(file_path)
            if exif_data is not None:
                self.exif_memory_cache.put(file_path, exif_data)

        if exif_data is None:
            self.cache_miss.emit(file_path, 'exif')
            return None

        try:
            from enhanced_raw_processor import (
                RAW_EXIF_SENSOR_META_VER,
                cached_raw_exif_orientation_trustworthy,
            )
            cached_ver = exif_data.get('raw_exif_sensor_meta_ver', 0)
            if cached_ver < RAW_EXIF_SENSOR_META_VER:
                self.cache_miss.emit(file_path, 'exif')
                return None
            if not exif_data.get("verified_orientation"):
                if verify:
                    if not cached_raw_exif_orientation_trustworthy(
                        file_path, exif_data
                    ):
                        self.cache_miss.emit(file_path, 'exif')
                        return None
                    # Memoize the successful probe so no caller ever re-pays it.
                    exif_data = dict(exif_data)
                    exif_data["verified_orientation"] = True
                    self.put_exif(file_path, exif_data)
                else:
                    self._schedule_orientation_verify(file_path, exif_data)
        except Exception:
            pass

        self.cache_hit.emit(file_path, 'exif')
        return exif_data

    def _schedule_orientation_verify(
        self, file_path: str, exif_data: dict[str, Any]
    ) -> None:
        """Queue a background trustworthiness probe for an unverified RAW record.

        One attempt per path per session; a single lazily-started daemon
        thread drains the queue (probes serialize on the rawpy global lock
        anyway, so more workers would only pile up blocked threads).
        """
        try:
            import common_image_loader

            if not common_image_loader.is_raw_file(file_path):
                return
        except Exception:
            return
        with self._orient_verify_lock:
            if getattr(self, "_closing", False):
                return
            if file_path in self._orient_verify_attempted:
                return
            if len(self._orient_verify_queue) >= self._orient_verify_queue_max:
                return
            if len(self._orient_verify_attempted) >= self._orient_verify_attempted_max:
                self._orient_verify_attempted.clear()
            self._orient_verify_attempted.add(file_path)
            self._orient_verify_queue.append(file_path)
            if (
                self._orient_verify_thread is None
                or not self._orient_verify_thread.is_alive()
            ):
                self._orient_verify_thread = threading.Thread(
                    target=self._orientation_verify_worker,
                    name="exif-orient-verify",
                    daemon=True,
                )
                self._orient_verify_thread.start()

    def _orientation_verify_worker(self) -> None:
        while True:
            with self._orient_verify_lock:
                if not self._orient_verify_queue or getattr(self, "_closing", False):
                    self._orient_verify_thread = None
                    return
                path = self._orient_verify_queue.pop(0)
            try:
                from enhanced_raw_processor import (
                    cached_raw_exif_orientation_trustworthy,
                )

                cached = self.exif_memory_cache.peek(path)
                if not cached:
                    getter = getattr(self.exif_cache, "get", None)
                    cached = getter(path) if callable(getter) else None
                if not cached:
                    continue
                cached = dict(cached)
                if cached_raw_exif_orientation_trustworthy(path, cached):
                    cached["verified_orientation"] = True
                    if not getattr(self, "_closing", False):
                        self.put_exif(path, cached)
                else:
                    # Cached orientation is wrong: drop the record so the next
                    # fetch re-extracts fresh (extraction writes a verified one).
                    if not getattr(self, "_closing", False):
                        self.exif_memory_cache.remove(path)
                    try:
                        self.exif_cache.remove(path)
                    except Exception:
                        pass
                    self.cache_miss.emit(path, 'exif')
            except Exception:
                pass

    def put_exif(
        self,
        file_path: str,
        exif_data: dict[str, Any],
        *,
        file_size: int | None = None,
        file_mtime: float | None = None,
        persist_disk: bool = True,
    ) -> None:
        """Cache EXIF data (memory always; disk optional for preview-first minimal stubs)."""
        if exif_data:
            self.exif_memory_cache.put(file_path, exif_data)
            if persist_disk:
                self.exif_cache.put(
                    file_path,
                    exif_data,
                    file_size=file_size,
                    file_mtime=file_mtime,
                )

    def flush_exif_disk_cache(self) -> None:
        """Commit batched EXIF disk writes (call after indexing batches)."""
        if hasattr(self.exif_cache, "flush"):
            self.exif_cache.flush()

    def get_capture_times_for_sort(self, file_paths: list) -> dict[str, str]:
        """capture_time only, for folder sort (ignores stale mtime on network folders)."""
        return self.exif_cache.get_capture_times_bulk(file_paths)

    def record_probed_capture_times(self, timestamps: dict[str, float]) -> None:
        """Persist sort-probe capture times so the next folder open skips re-probing.

        timestamps values are epoch floats (from probe_capture_timestamp_from_file);
        stored as ISO strings to match the capture_time column's existing format.
        """
        if not timestamps or not hasattr(self.exif_cache, "update_capture_time_bulk"):
            return
        import datetime

        iso_times: dict[str, str] = {}
        for path, ts in timestamps.items():
            if not ts or ts <= 0:
                continue
            try:
                iso_times[path] = datetime.datetime.fromtimestamp(ts).strftime(
                    "%Y:%m:%d %H:%M:%S"
                )
            except Exception:
                continue
        self.exif_cache.update_capture_time_bulk(iso_times)

    def get_capture_times_for_folder_sort(self, folder_path: str) -> dict[str, str]:
        """All capture_time rows under folder_path (normalized path keys)."""
        return self.exif_cache.get_capture_times_for_folder(folder_path)

    def get_multiple_exif(self, file_paths: list, file_stats: dict[str, tuple[int, float]] | None = None, fast_mode: bool = True) -> dict[str, dict[str, Any]]:
        """Get cached EXIF data for multiple files at once.

        Orientation trust for unverified RAW records is optimistic + deferred
        to a background probe, same as get_exif's verify=False default --
        NOT the old synchronous cached_raw_exif_orientation_trustworthy() ->
        rawpy.imread() call per file. That call, run for every file in a
        folder on the caller's thread (e.g. the main-thread instant-sort
        fast path in main.py's _on_quick_folder_index_ready), turned a large
        folder's sort into a serial rawpy-open storm that froze the UI for
        the whole scan -- get_exif itself was already fixed for this; this
        bulk sibling had the same synchronous check duplicated inline and
        was missed.
        """
        self.stats['exif_requests'] += len(file_paths)

        results = {}
        missing_paths = []

        try:
            from enhanced_raw_processor import RAW_EXIF_SENSOR_META_VER
            has_ver = True
        except Exception:
            has_ver = False
            RAW_EXIF_SENSOR_META_VER = 0

        # Check memory cache first
        for path in file_paths:
            exif = self.exif_memory_cache.get(path)
            if exif:
                if has_ver:
                    cached_ver = exif.get('raw_exif_sensor_meta_ver', 0)
                    if cached_ver < RAW_EXIF_SENSOR_META_VER:
                        missing_paths.append(path)
                        continue
                    if not exif.get('verified_orientation'):
                        self._schedule_orientation_verify(path, exif)
                results[path] = exif
            else:
                missing_paths.append(path)

        if missing_paths:
            # Fetch missing from persistent cache
            db_results = self.exif_cache.get_multiple(missing_paths, file_stats, fast_mode)
            for path, exif in db_results.items():
                if has_ver:
                    cached_ver = exif.get('raw_exif_sensor_meta_ver', 0)
                    if cached_ver < RAW_EXIF_SENSOR_META_VER:
                        continue
                    if not exif.get('verified_orientation'):
                        self._schedule_orientation_verify(path, exif)
                self.exif_memory_cache.put(path, exif)
                results[path] = exif

        return results

    def release_full_image_memory(self, file_path: str | None) -> None:
        """Drop in-memory full-res / pixmap tiers; keep preview/grid/thumbnail on disk."""
        if not file_path:
            return
        if self.full_image_cache.remove(file_path):
            self._note_full_cache_reason(file_path, "invalidated (invalidate_file)")
        self.pixmap_cache.remove(file_path)

    def invalidate_file(self, file_path: str) -> None:
        """Remove all cached data for a specific file."""
        key = self._path_key(file_path)
        self.thumbnail_cache.remove(key)
        self.grid_cache.remove(key)
        self.preview_cache.remove(key)
        if self.full_image_cache.remove(file_path):
            self._note_full_cache_reason(file_path, "invalidated (invalidate_file)")
        self.pixmap_cache.remove(file_path)
        self.disk_thumbnail_cache.remove(file_path)
        self.disk_grid_cache.remove(file_path)
        self.disk_preview_cache.remove(file_path)
        self.exif_memory_cache.remove(file_path)
        self.exif_cache.remove(file_path)
        self.full_image_embedded_jpeg_paths.discard(key)

    def clear_all(self) -> None:
        """Clear all caches."""
        self.thumbnail_cache.clear()
        self.grid_cache.clear()
        self.preview_cache.clear()
        for _k in list(self.full_image_cache.cache.keys()):
            self._note_full_cache_reason(_k, "dropped by memory-pressure purge")
        self.full_image_cache.clear()
        self.pixmap_cache.clear()
        # The edit-base LRU lives in processing.unified and is the one
        # heavy cache this purge could not previously reach, so a
        # critical-pressure purge freed everything except a couple of
        # hundred MB of float32 bases.
        try:
            from processing.unified import (
                release_adjusted_full_cache,
                release_edit_base_cache,
                release_unpacked_raw_cache,
            )

            # All three live in processing.unified at module scope so they
            # survive the one-shot workers that build them. That is what makes
            # them useful, and also what puts them outside every cache this
            # class owns -- a critical-pressure purge used to clear everything
            # it could see and leave these whole.
            freed = (
                release_edit_base_cache()
                + release_adjusted_full_cache()
                + release_unpacked_raw_cache()
            )
            if freed:
                import logging

                logging.getLogger(__name__).info(
                    "[MEMORY] released %.0f MB of decode/edit buffers", freed / 1e6
                )
        except Exception:
            pass
        self.libraw_preview_paths.clear()
        if self._libraw_preview_paths_file and os.path.isfile(self._libraw_preview_paths_file):
            try:
                os.remove(self._libraw_preview_paths_file)
            except Exception:
                pass
        self.full_image_embedded_jpeg_paths.clear()
        self.exif_cache.clear()
        self.exif_memory_cache.clear()
        self.disk_thumbnail_cache.clear()
        self.disk_grid_cache.clear()
        self.disk_preview_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get comprehensive cache statistics."""
        memory_info = self.memory_monitor.get_memory_info()

        return {
            'thumbnail_cache': self.thumbnail_cache.get_stats(),
            'grid_cache': self.grid_cache.get_stats(),
            'full_image_cache': self.full_image_cache.get_stats(),
            'pixmap_cache': self.pixmap_cache.get_stats(),
            'memory_info': memory_info,
            'request_stats': self.stats.copy(),
            'cache_budget_mb': self.max_memory_mb,
            'persistent_cache_enabled': self.persistent_cache_enabled
        }

    def cleanup_old_cache(self) -> None:
        """Clean up old cache entries."""
        self.exif_cache.cleanup_old_entries()
        self.disk_thumbnail_cache.cleanup_old_entries()
        self.disk_grid_cache.cleanup_old_entries()
        self.disk_preview_cache.cleanup_old_entries()

    def enforce_disk_cache_budget(self, max_mb: int | None = None) -> dict[str, float]:
        """Evict oldest-cached thumbnail/grid/preview disk entries when total usage
        exceeds a budget (RAWVIEWER_DISK_CACHE_MAX_MB, default 4096 MB).

        Age-based cleanup_old_cache() only removes entries older than 30 days --
        nothing previously capped total disk usage, so a long-lived install could
        grow unbounded across every folder ever browsed. This is stat-based (no
        size column is tracked in the DB) and evicts down to 90% of budget
        (hysteresis) so it doesn't re-trigger on every startup right at the
        boundary -- call from a background/startup pass only, never a hot path.
        """
        import logging

        logger = logging.getLogger(__name__)
        if max_mb is None:
            max_mb = _env_int(
                "RAWVIEWER_DISK_CACHE_MAX_MB", 4096, minimum=256, maximum=1024 * 1024
            )
        max_bytes = max_mb * 1024 * 1024
        target_bytes = int(max_bytes * 0.9)

        tiers = {
            "thumbnail": self.disk_thumbnail_cache,
            "grid": self.disk_grid_cache,
            "preview": self.disk_preview_cache,
        }
        usage: dict[str, int] = {}
        for name, cache in tiers.items():
            try:
                usage[name] = cache.get_disk_usage_bytes()
            except Exception:
                usage[name] = 0

        total = sum(usage.values())
        usage_mb = {f"{k}_mb": v / (1024 * 1024) for k, v in usage.items()}
        if total <= max_bytes:
            return usage_mb

        to_free = total - target_bytes
        freed_total = 0
        for name, cache in tiers.items():
            tier_usage = usage.get(name, 0)
            if tier_usage <= 0 or total <= 0:
                continue
            # Evict proportionally to this tier's share of total usage.
            share = int(to_free * (tier_usage / total))
            try:
                freed_total += cache.evict_lru(share)
            except Exception:
                pass

        logger.info(
            "[CACHE] Disk cache budget exceeded (%.1f MB > %d MB); evicted %.1f MB "
            "(thumbnail=%.1fMB grid=%.1fMB preview=%.1fMB before eviction)",
            total / (1024 * 1024),
            max_mb,
            freed_total / (1024 * 1024),
            usage_mb.get("thumbnail_mb", 0.0),
            usage_mb.get("grid_mb", 0.0),
            usage_mb.get("preview_mb", 0.0),
        )
        return usage_mb

    def _load_libraw_preview_paths(self) -> None:
        try:
            if self._libraw_preview_paths_file and os.path.isfile(self._libraw_preview_paths_file):
                import json
                with open(self._libraw_preview_paths_file, encoding="utf-8") as f:
                    loaded = json.load(f)
                self.libraw_preview_paths = set(loaded) if isinstance(loaded, list) else set()
                self._prune_libraw_preview_paths()
        except Exception:
            self.libraw_preview_paths = set()

    def _save_libraw_preview_paths(self) -> None:
        try:
            if self._libraw_preview_paths_file:
                import json
                with open(self._libraw_preview_paths_file, "w", encoding="utf-8") as f:
                    json.dump(list(self.libraw_preview_paths), f)
        except Exception:
            pass

    def close(self):
        """Close all persistent cache connections."""
        self._closing = True
        self._save_libraw_preview_paths()

        with self._orient_verify_lock:
            self._orient_verify_queue.clear()
        if self._orient_verify_thread is not None and self._orient_verify_thread.is_alive():
            self._orient_verify_thread.join(timeout=2.0)

        if hasattr(self, 'exif_cache'):
            self.exif_cache.close()
        if hasattr(self, 'disk_thumbnail_cache'):
            self.disk_thumbnail_cache.close()
        if hasattr(self, 'disk_grid_cache'):
            self.disk_grid_cache.close()
        if hasattr(self, 'disk_preview_cache'):
            self.disk_preview_cache.close()


# Global cache instance
_global_cache: ImageCache | None = None
_legacy_cache_cleanup_done = False


def _cleanup_legacy_disk_cache_once() -> None:
    """Remove legacy image caches while preserving semantic search assets."""
    global _legacy_cache_cleanup_done
    if _legacy_cache_cleanup_done:
        return
    _legacy_cache_cleanup_done = True

    legacy_dir = os.path.expanduser("~/.rawviewer_cache")
    if not os.path.isdir(legacy_dir):
        return

    # Keep semantic_index.db, WAL/SHM files, MobileCLIP assets, and any future
    # non-image assets in this cache root. Memory-only mode should only clear
    # obsolete image/EXIF cache stores from older builds.
    obsolete_entries = (
        "exif_cache.db",
        "exif_cache.db-shm",
        "exif_cache.db-wal",
        "thumbnail_cache.db",
        "thumbnail_cache.db-shm",
        "thumbnail_cache.db-wal",
        "grid_cache.db",
        "grid_cache.db-shm",
        "grid_cache.db-wal",
        "preview_cache.db",
        "preview_cache.db-shm",
        "preview_cache.db-wal",
        "thumbnails",
        "grid",
        "previews",
        "images",
    )
    for name in obsolete_entries:
        path = os.path.join(legacy_dir, name)
        if not os.path.exists(path):
            continue
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            # Best effort cleanup; app should continue even if deletion fails.
            pass


_exif_cache_atexit_registered = False


def _register_exif_cache_atexit() -> None:
    global _exif_cache_atexit_registered
    if _exif_cache_atexit_registered:
        return
    _exif_cache_atexit_registered = True

    def _flush_exif_on_exit() -> None:
        try:
            global _global_cache
            if _global_cache is not None and hasattr(_global_cache, "flush_exif_disk_cache"):
                _global_cache.flush_exif_disk_cache()
        except Exception:
            pass

    atexit.register(_flush_exif_on_exit)


def get_image_cache() -> ImageCache:
    """Get the global image cache instance."""
    global _global_cache
    if _global_cache is None:
        persistent = os.environ.get("RAWVIEWER_PERSISTENT_CACHE", "1").lower() in {"1", "true", "yes", "on"}
        if not persistent:
            _cleanup_legacy_disk_cache_once()
        _global_cache = ImageCache(persistent_cache_enabled=persistent)
        _register_exif_cache_atexit()
    return _global_cache


def initialize_cache(cache_dir: str = None, persistent_cache_enabled: bool = False) -> ImageCache:
    """Initialize the global image cache with custom settings."""
    global _global_cache
    _global_cache = ImageCache(cache_dir, persistent_cache_enabled=persistent_cache_enabled)
    return _global_cache
