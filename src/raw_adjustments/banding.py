"""banding: helpers moved out of apply.py (P2b cycle break)."""

import math
import os


def banded_worker_count(height: int) -> int:
    """Row-band thread count for the banded pixel pipelines (shared by
    raw_adjustments and raw_edit_pipeline -- one tuning point, not several).
    """
    # Thread-pool overhead isn't worth it below a few hundred rows.
    if height < 512:
        return 1
    return min(8, os.cpu_count() or 1)


def band_ranges(h: int, n_workers: int, pad_px: int = 0) -> list:
    """Split ``h`` rows into up to ``n_workers`` contiguous bands.

    Returns [(y0, y1, pad_top, pad_bot), ...] where pads are clamped to the
    image edges. Single source of truth for the band-boundary math used by
    every banded pipeline (a boundary off-by-one fixed here fixes all of
    them; previously three near-identical copies existed).
    """

    band_h = math.ceil(h / max(1, n_workers))
    bands = []
    for i in range(n_workers):
        y0 = i * band_h
        y1 = min(h, y0 + band_h)
        if y0 >= y1:
            break
        bands.append((y0, y1, min(pad_px, y0), min(pad_px, h - y1)))
    return bands


_BANDED_EXECUTOR = None


_BANDED_EXECUTOR_LOCK = None


def banded_executor():
    """Shared lazily-created pool for banded pixel stages.

    The banded functions used to build (spawn + join up to 8 OS threads) a
    fresh ThreadPoolExecutor per call -- on the Adjust-panel live-preview
    path that's pool churn on every slider tick. One idle pool costs nothing
    between calls; concurrent callers share it safely (Executor.map is
    thread-safe).
    """
    global _BANDED_EXECUTOR, _BANDED_EXECUTOR_LOCK
    import threading

    if _BANDED_EXECUTOR_LOCK is None:
        _BANDED_EXECUTOR_LOCK = threading.Lock()
    with _BANDED_EXECUTOR_LOCK:
        if _BANDED_EXECUTOR is None:
            from concurrent.futures import ThreadPoolExecutor

            _BANDED_EXECUTOR = ThreadPoolExecutor(
                max_workers=min(8, os.cpu_count() or 1),
                thread_name_prefix="banded-pixels",
            )
        return _BANDED_EXECUTOR


_BAND_PAD_PX = 48
