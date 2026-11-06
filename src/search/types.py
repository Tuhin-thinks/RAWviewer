"""Shared types for semantic search."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from core.errors import IndexAborted  # re-export

type ProgressCallback = Callable[[int, int, str], None] | None

type MetadataRow = sqlite3.Row | dict[str, object]


@dataclass
class SearchHit:
    file_path: str
    score: float
    file_name: str = ""
    capture_time: str = ""
    camera_model: str = ""
    lens_model: str = ""
    iso: int = 0
    gps_lat: float | None = None
    gps_lon: float | None = None
    city: str = ""
    landmark: str = ""
    admin1: str = ""
    country: str = ""
    country_code: str = ""
    face_count: int = 0

# Re-export for convenience
__all__ = [
    "IndexAborted",
    "MetadataRow",
    "ProgressCallback",
    "SearchHit",
]
