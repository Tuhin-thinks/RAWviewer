"""Semantic search package."""

from search.types import IndexAborted, MetadataRow, ProgressCallback, SearchHit
from search.indexer import (
    _get_shared_index_executor,
    format_index_progress,
    semantic_embeddings_enabled,
    face_scan_enabled,
    metadata_auto_index_enabled,
    resolve_mobileclip_backend,
)
from search.queries import (
    get_semantic_capture_times_for_paths,
    get_semantic_capture_times_for_folder,
    CustomReverseGeocoder,
)
from search.backends import (
    MobileCLIPCoreMLBackend,
    MobileCLIPONNXBackend,
    _ClipBPETokenizer,
)

__all__ = [
    "IndexAborted",
    "MetadataRow",
    "ProgressCallback",
    "SearchHit",
    "_get_shared_index_executor",
    "format_index_progress",
    "semantic_embeddings_enabled",
    "face_scan_enabled",
    "metadata_auto_index_enabled",
    "resolve_mobileclip_backend",
    "get_semantic_capture_times_for_paths",
    "get_semantic_capture_times_for_folder",
    "CustomReverseGeocoder",
    "MobileCLIPCoreMLBackend",
    "MobileCLIPONNXBackend",
    "_ClipBPETokenizer",
]
