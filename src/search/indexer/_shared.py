"""Semantic indexer: build pipeline, shared executor, configuration."""

from __future__ import annotations

import os
import platform
import re
import shutil
import sqlite3
import sys
import time
import hashlib
import json
import gzip
import threading
import concurrent.futures
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, cast

import numpy as np
from PIL import Image, ImageOps
from PIL.Image import Image as PilImage

from core.errors import IndexAborted

from search.types import MetadataRow, ProgressCallback

_SHARED_INDEX_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = None
_SHARED_EXECUTOR_LOCK = threading.Lock()


def _get_shared_index_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _SHARED_INDEX_EXECUTOR
    if _SHARED_INDEX_EXECUTOR is None:
        with _SHARED_EXECUTOR_LOCK:
            if _SHARED_INDEX_EXECUTOR is None:
                max_w = os.cpu_count() or 4
                # Limit max workers to avoid overwhelming the system
                max_w = min(16, max_w)
                _SHARED_INDEX_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_w, thread_name_prefix="shared-index"
                )
    return _SHARED_INDEX_EXECUTOR


def _bundled_resource_path(relative_path: str) -> str:
    """Absolute path to bundled data (dev tree or PyInstaller _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    return os.path.join(base, relative_path)

import metadata_backend

