"""Shared types for semantic search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, TypeAlias

import sqlite3

from core.errors import IndexAborted  # re-export


ProgressCallback: TypeAlias = Optional[Callable[[int, int, str], None]]

MetadataRow: TypeAlias = sqlite3.Row | Dict[str, object]


@dataclass
class SearchHit:
    file_path: str
    score: float
    file_name: str = ""
    capture_time: str = ""
    camera_model: str = ""
    lens_model: str = ""
    iso: int = 0
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
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
